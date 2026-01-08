/* src/imu_task.c — WT901 / JY61P / WT61P IMU over I2C
 *
 * 目标：
 *  - IMU 内部 200 Hz 输出（RRATE=0x0B，可在 imu_config_200hz 里改）
 *  - 采样线程：固定 IMU_ODR_HZ（默认 120Hz，可设 200Hz）轮询 I2C，只采样不发 BLE
 *  - 发送线程：从 FIFO 聚合 1~4 个 sample，按配置 fps（默认 200Hz，
 *    多 TX 情况下一般设置为 50Hz）发送 'I' 帧
 *  - R 命令：设置 “发送 fps (target_fps)” 和 “相位偏移 phase_ms”（多 TX 分时隙）
 *  - 帧格式 'I'：
 *        0  : 0xAA
 *        1  : 'I'
 *        2-3: seq (u16, LE)
 *        4-5: dev_id (u16, LE)
 *        6-9: t_ms (u32, LE, ble_link_now_host_ms)
 *        10-11: fps (u16, LE)  —— 实际当前发送 fps
 *        12: sample_count (u8)
 *        后面: sample_count × 20B，每个 sample =
 *              10×int16(ax,ay,az,gx,gy,gz,q0,q1,q2,q3)
 */

#include "app_config.h"
#include "device_id.h"
#include "ble_link.h"
#include "imu_task.h"

#include <string.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>

#include <zephyr/device.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>

/* ========================= IMU 硬件配置 ========================= */

/* WT61P / WT901 I2C 地址与寄存器 */
#define IMU_I2C_ADDR            0x50        /* WT61P / JY901 / JY61P 7-bit I2C 地址 */

/* 原始传感器数据寄存器：AX ~ AZ + GX ~ GZ (+ Hx~Hz)，从 0x34 开始 */
#define IMU_REG_START           0x34        /* AX 寄存器起始地址 */
#define IMU_REG_LEN             18          /* AX~AZ + GX~GZ + HX~HZ 共 18 字节 */
#define IMU_RAW_USED_LEN        12          /* 只用前 12 字节：AX~AZ + GX~GZ */

/* 四元数寄存器：基于 WitMotion 标准协议
 * WT 系列通常为：
 *   0x51/0x52: q0 (L/H)
 *   0x53/0x54: q1 (L/H)
 *   0x55/0x56: q2 (L/H)
 *   0x57/0x58: q3 (L/H)
 * 如你的固件不同，直接改下面两个宏即可。
 */
#define IMU_REG_QUAT_START      0x51
#define IMU_REG_QUAT_LEN        8           /* q0~q3 共 8 字节 */

/* IMU 内部输出速率（ODR）：这里用 120Hz，你可以改成 200 试试
 * 同时记得把 RRATE 对应改为 0x0B（200Hz）或合适值。
 */
#define IMU_ODR_HZ              100         /* Kann bis zu 200Hz */
#define IMU_SAMPLE_PERIOD_MS    (1000 / IMU_ODR_HZ)

/* 每帧最多聚合的 sample 数量：1~4（200Hz / 50Hz = 4） */
#define IMU_SAMPLES_PER_FRAME_MAX   4

/* 手动控制每帧聚合的 sample 数量：
 *  1 = 每包 1 个 sample（原来的行为）
 *  2 = 每包 2 个 sample
 *  3/4 = 每包 3/4 个 sample（上限不超过 IMU_SAMPLES_PER_FRAME_MAX）
 *
 * 后面你可以直接改这个宏的值做实验。
 */
#ifndef IMU_DATA_PER_PACK
#define IMU_DATA_PER_PACK  2
#endif

/* R 命令配置的“发送 fps”，默认 200（单 TX 场景） */
#define IMU_FPS_DEFAULT         200

/* FIFO 大小：纯原始 sample 数量（越大越抗抖，但占 RAM） */
#define IMU_FIFO_SIZE           256

/* I2C 设备：默认 i2c0，如果 overlay 里 IMU 接在别的 bus，请改 node label */
static const struct device *const imu_i2c = DEVICE_DT_GET(DT_NODELABEL(i2c0));

/* ========================= 基本数据结构 ========================= */

struct imu_sample_raw {
    int16_t ax;
    int16_t ay;
    int16_t az;
    int16_t gx;
    int16_t gy;
    int16_t gz;
    int16_t q0;
    int16_t q1;
    int16_t q2;
    int16_t q3;
};

static inline int16_t le16(const uint8_t *p)
{
    /* 文档约定：DATA = (H << 8) | L，小端 */
    return (int16_t)((((int16_t)p[1]) << 8) | p[0]);
}

/* ========================= FIFO / 状态变量 ========================= */

/* 采样启动信号：main.c 调 imu_start() 之后采样线程才开始跑 */
static struct k_sem   imu_start_sem;

/* FIFO：采样线程 -> 发送线程 */
static struct imu_sample_raw imu_fifo[IMU_FIFO_SIZE];
static uint16_t fifo_head;   /* 写指针 */
static uint16_t fifo_tail;   /* 读指针 */
static struct k_mutex fifo_lock;

/* R 命令配置的“发送 fps” + “相位偏移” */
static volatile uint16_t g_target_fps  = IMU_FPS_DEFAULT; /* 实际发包 fps 目标（如 50/100/200） */
static volatile uint16_t g_phase_ms    = 0;               /* 多 TX 分时隙用 */

/* IMU 'I' 帧的 seq 计数器 */
static uint16_t seq_imu;

/* ========================= FIFO 辅助函数 ========================= */

static inline bool fifo_empty(void)
{
    return fifo_head == fifo_tail;
}

static inline bool fifo_full(void)
{
    return (uint16_t)((fifo_head + 1U) % IMU_FIFO_SIZE) == fifo_tail;
}

/* 向 FIFO 推入一个 sample：满则覆盖最旧的（tail 往前挪） */
static void fifo_push(const struct imu_sample_raw *s)
{
    k_mutex_lock(&fifo_lock, K_FOREVER);

    if (fifo_full()) {
        /* 覆盖最旧的：tail 前进一格 */
        fifo_tail = (uint16_t)((fifo_tail + 1U) % IMU_FIFO_SIZE);
    }

    imu_fifo[fifo_head] = *s;
    fifo_head = (uint16_t)((fifo_head + 1U) % IMU_FIFO_SIZE);

    k_mutex_unlock(&fifo_lock);
}

/* 从 FIFO 弹出一个 sample：返回 true 表示成功 */
static bool fifo_pop(struct imu_sample_raw *out)
{
    bool ok = false;

    k_mutex_lock(&fifo_lock, K_FOREVER);

    if (!fifo_empty()) {
        *out = imu_fifo[fifo_tail];
        fifo_tail = (uint16_t)((fifo_tail + 1U) % IMU_FIFO_SIZE);
        ok = true;
    }

    k_mutex_unlock(&fifo_lock);
    return ok;
}

/* ========================= I2C 配置 & 读写 ========================= */

/* 配置 WT901 / JY61P / WT61P 输出速率为 200 Hz（RRATE=0x0B） */
static int imu_config_200hz(void)
{
    /* 这两条命令与你之前的保持一致，只是第二条改为 0x0B（200Hz） */
    uint8_t cmd1[3] = {0x69, 0x88, 0xB5};  /* 解锁/进入配置 */
    uint8_t cmd2[3] = {0x03, 0x09, 0x00};  /* RRATE = 0x0B -> 200 Hz RRATE = 0x09 -> 100 Hz*/

    int ret;

    ret = i2c_write(imu_i2c, cmd1, sizeof(cmd1), IMU_I2C_ADDR);
    if (ret) {
        LOGF("IMU cfg cmd1 failed: %d\n", ret);
        return ret;
    }
    k_msleep(2);

    ret = i2c_write(imu_i2c, cmd2, sizeof(cmd2), IMU_I2C_ADDR);
    if (ret) {
        LOGF("IMU cfg cmd2 failed: %d\n", ret);
        return ret;
    }
    k_msleep(5);

    LOGF("IMU configured to 200Hz (RRATE=0x0B)\n");
    return 0;
}

/* 连续读取 IMU 寄存器块 */
static int imu_i2c_read_block(uint8_t reg_start, uint8_t *buf, size_t len)
{
    int ret = i2c_write_read(imu_i2c,
                             IMU_I2C_ADDR,
                             &reg_start, 1,
                             buf, len);
    if (ret) {
        LOGF("IMU i2c_write_read failed: reg=0x%02X len=%u err=%d\n",
             reg_start, (unsigned int)len, ret);
    }
    return ret;
}

/* 读一帧原始 6 轴 + 四元数数据（不做去重）
 *
 * ACC/GYRO: 从 0x34 连续读 18 字节，取前 12 字节
 * QUAT    : 从 0x51 连续读 8 字节
 */
static int imu_read_one_sample(struct imu_sample_raw *out)
{
    uint8_t buf[IMU_REG_LEN];
    int ret = imu_i2c_read_block(IMU_REG_START, buf, sizeof(buf));
    if (ret) {
        return ret;
    }

    /* AX/AY/AZ = 0x34~0x36, GX/GY/GZ = 0x37~0x39，小端 */
    out->ax = le16(&buf[0]);
    out->ay = le16(&buf[2]);
    out->az = le16(&buf[4]);
    out->gx = le16(&buf[6]);
    out->gy = le16(&buf[8]);
    out->gz = le16(&buf[10]);

    /* 读取四元数 q0~q3 */
    uint8_t qbuf[IMU_REG_QUAT_LEN];
    ret = imu_i2c_read_block(IMU_REG_QUAT_START, qbuf, sizeof(qbuf));
    if (ret) {
        return ret;
    }

    out->q0 = le16(&qbuf[0]);
    out->q1 = le16(&qbuf[2]);
    out->q2 = le16(&qbuf[4]);
    out->q3 = le16(&qbuf[6]);

    return 0;
}

/* ========================= 'I' 帧打包 ========================= */

static inline uint16_t next_seq_imu(void)
{
    uint16_t v = seq_imu;
    seq_imu = (uint16_t)(v + 1);
    return v;
}

/* 发送一帧 'I' 包：samples[0..num_samples-1] */
static void imu_send_frame(uint32_t t_global_ms,
                           uint16_t fps,
                           const struct imu_sample_raw *samples,
                           uint8_t num_samples)
{
    if (!EN_IMU || !EN_BLE) return;
    if (num_samples == 0 || num_samples > IMU_SAMPLES_PER_FRAME_MAX) return;

    uint8_t  f[BLE_BUF_SIZE];
    uint16_t p = 0;

    /* 头部：AA 'I' */
    f[p++] = 0xAA;
    f[p++] = 'I';

    /* seq (u16, LE) */
    sys_put_le16(next_seq_imu(),      &f[p]); p += 2;

    /* device_id (u16, LE) */
    sys_put_le16(device_id_get16(),   &f[p]); p += 2;

    /* 时间戳：全局 host_ms */
    sys_put_le32(t_global_ms,         &f[p]); p += 4;

    /* 当前 fps（u16, LE）：实际本帧使用的 fps */
    sys_put_le16(fps,                 &f[p]); p += 2;

    /* sample_count (u8) */
    f[p++] = num_samples;

    /* samples: 每个 sample = 10×int16 小端 (ACC+GYRO+QUAT) */
    for (uint8_t i = 0; i < num_samples; i++) {
        const struct imu_sample_raw *s = &samples[i];

        sys_put_le16((uint16_t)s->ax,  &f[p]); p += 2;
        sys_put_le16((uint16_t)s->ay,  &f[p]); p += 2;
        sys_put_le16((uint16_t)s->az,  &f[p]); p += 2;
        sys_put_le16((uint16_t)s->gx,  &f[p]); p += 2;
        sys_put_le16((uint16_t)s->gy,  &f[p]); p += 2;
        sys_put_le16((uint16_t)s->gz,  &f[p]); p += 2;

        sys_put_le16((uint16_t)s->q0,  &f[p]); p += 2;
        sys_put_le16((uint16_t)s->q1,  &f[p]); p += 2;
        sys_put_le16((uint16_t)s->q2,  &f[p]); p += 2;
        sys_put_le16((uint16_t)s->q3,  &f[p]); p += 2;
    }

    (void)ble_link_send(f, p);
}

/* ========================= 采样线程：固定 IMU_ODR_HZ ========================= */

static void imu_sampling_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a);
    ARG_UNUSED(b);
    ARG_UNUSED(c);

    if (!EN_IMU) {
        return;
    }

    if (!device_is_ready(imu_i2c)) {
        LOGF("IMU I2C device not ready, sampling thread exit\n");
        return;
    }

    /* 等 main.c 中 imu_start() 放行 */
    k_sem_take(&imu_start_sem, K_FOREVER);
    LOGF("IMU sampling thread started\n");

    struct imu_sample_raw s;

    while (1) {
        uint32_t t0 = ts_ms();

        if (imu_read_one_sample(&s) == 0) {
            /* 推入 FIFO（满则覆盖最旧） */
            fifo_push(&s);
        }

        uint32_t t1    = ts_ms();
        uint32_t spent = t1 - t0;

        if (spent < IMU_SAMPLE_PERIOD_MS) {
            k_sleep(K_MSEC(IMU_SAMPLE_PERIOD_MS - spent));
        } else {
            k_yield();
        }
    }
}

static void imu_sending_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a);
    ARG_UNUSED(b);
    ARG_UNUSED(c);

    if (!EN_IMU) {
        return;
    }

    if (!device_is_ready(imu_i2c)) {
        LOGF("IMU I2C device not ready, sending thread exit\n");
        return;
    }

    LOGF("IMU sending thread started\n");

    uint32_t last_local  = k_uptime_get_32();
    uint32_t last_log_ms = ble_link_now_host_ms();
    uint32_t phase_acc   = 0;

    /* 我们现在用一个小数组，最多装 IMU_SAMPLES_PER_FRAME_MAX 个 sample */
    struct imu_sample_raw samples[IMU_SAMPLES_PER_FRAME_MAX];

    while (1) {
        if (!EN_BLE) {
            k_sleep(K_MSEC(10));
            last_local   = k_uptime_get_32();
            phase_acc    = 0;
            last_log_ms  = ble_link_now_host_ms();
            continue;
        }

        /* 当前目标 fps：由 R 命令配置；范围 clamp 到 1..IMU_ODR_HZ */
        uint16_t fps = g_target_fps;
        if (fps == 0) {
            fps = IMU_FPS_DEFAULT;
        }
        if (fps > IMU_ODR_HZ) {
            fps = IMU_ODR_HZ;
        }
        if (fps < 1) {
            fps = 1;
        }

        /* 1) 用本地时间算 dt_ms */
        uint32_t now_local = k_uptime_get_32();
        int32_t  diff      = (int32_t)(now_local - last_local);
        last_local         = now_local;

        uint32_t dt_ms = 0;

        if (diff <= 0 || diff > 1000) {
            phase_acc = 0;
            dt_ms     = 0;
        } else {
            dt_ms = (uint32_t)diff;
        }

        if (dt_ms > 0) {
            phase_acc += (uint32_t)fps * dt_ms;
        }

        /* 2) host 对齐后的时间戳（帧头用这个） */
        uint32_t now_host = ble_link_now_host_ms();

        /* 可能一次跨过多“包边界”，用 while 补齐 */
        while (phase_acc >= 1000U) {
            phase_acc -= 1000U;

            /* 本次希望聚合的 sample 数量（由宏控制），并夹在 1..MAX 之间 */
            uint8_t want = IMU_DATA_PER_PACK;
            if (want < 1) {
                want = 1;
            } else if (want > IMU_SAMPLES_PER_FRAME_MAX) {
                want = IMU_SAMPLES_PER_FRAME_MAX;
            }

            uint8_t got = 0;
            while (got < want) {
                if (!fifo_pop(&samples[got])) {
                    break;
                }
                got++;
            }

            if (got > 0) {
                /* sample_count = got */
                imu_send_frame(now_host, fps, samples, got);
            } else {
                /* 没数据就退出这轮，避免空转 */
                break;
            }
        }

        /* 每秒打印一次调试信息（原来那段可以保留/删除） */
        if ((now_host - last_log_ms) >= 1000U) {
            last_log_ms = now_host;
            LOGF("IMU send: fps=%u, phase_acc=%u\n",
                 (unsigned int)fps, (unsigned int)phase_acc);
        }

        /* 发包线程不需要太细，1ms 轮询足够 */
        k_sleep(K_MSEC(1));
    }
}

/* ========================= 线程创建 ========================= */

K_THREAD_DEFINE(imu_sample_tid, IMU_STACK_SIZE, imu_sampling_thread,
                NULL, NULL, NULL, IMU_PRIO, 0, 0);

K_THREAD_DEFINE(imu_send_tid, IMU_STACK_SIZE, imu_sending_thread,
                NULL, NULL, NULL, IMU_PRIO, 0, 0);

/* ========================= 对外 API ========================= */

int imu_init(void)
{
    if (!EN_IMU) {
        return 0;
    }

    k_sem_init(&imu_start_sem, 0, 1);
    k_mutex_init(&fifo_lock);

    fifo_head = 0;
    fifo_tail = 0;
    seq_imu   = 0;

    if (!device_is_ready(imu_i2c)) {
        LOGF("IMU I2C device not ready in imu_init()\n");
        return -ENODEV;
    }

    /* 上电时配置 IMU 为 200Hz 输出（RRATE=0x0B） */
    (void)imu_config_200hz();

    LOGF("IMU init OK (addr=0x%02X)\n", IMU_I2C_ADDR);
    return 0;
}

void imu_start(void)
{
    if (!EN_IMU) return;
    (void)k_sem_give(&imu_start_sem);
}

/* BLE 端通过 'R' 帧调用：设置 “发送 fps” 和 “phase_ms” 时隙
 *
 *  - target_fps: 本节点希望的发包频率（例如 200/100/50/120 等）
 *  - phase_ms  : 相位偏移（用于多 TX 错开发包）
 *
 *  具体行为：
 *    - IMU 始终以 IMU_ODR_HZ 采样（例如 200Hz）
 *    - 发送线程以 target_fps 周期从 FIFO 取样本，并聚合：
 *
 *        ideal_samples = clamp(IMU_ODR_HZ / target_fps, 1, IMU_SAMPLES_PER_FRAME_MAX)
 *
 *      然后打成一帧 'I' 包发出。
 *
 *  典型配置：
 *    - 单 TX：RSLOT slot 200 0   → 200Hz 发包，每帧 1 sample
 *    - 10×TX：RSLOT slot 50  phase → 50Hz 发包，每帧最多 4 sample（等效 200Hz）
 *    - 你想要的：RSLOT slot 120 phase → 120Hz 发包，每帧约 1~2 sample
 */
void imu_set_rate_phase(uint16_t target_fps, uint16_t phase_ms)
{
    if (target_fps == 0) {
        target_fps = IMU_FPS_DEFAULT;
    }

    if (target_fps > IMU_ODR_HZ) {
        target_fps = IMU_ODR_HZ;  /* 给个合理上限：不超过 IMU ODR */
    }

    g_target_fps = target_fps;
    g_phase_ms   = phase_ms;

    LOGF("IMU rate cfg: send_fps=%u, phase=%u ms\n",
         (unsigned int)g_target_fps, (unsigned int)g_phase_ms);
}
