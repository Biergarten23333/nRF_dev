/* src/main.c */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/byteorder.h>

#include "app_config.h"
#include "device_id.h"
#include "ble_link.h"
#include "ads1298.h"
#include "imu_task.h"

LOG_MODULE_REGISTER(main, LOG_LEVEL_INF);

/* ============ EMG -> BLE 'E' 帧打包 ============ */

/* EMG 帧序号（u16，溢出自然回绕） */
static uint16_t g_emg_seq = 0;

static uint16_t next_emg_seq(void)
{
    uint16_t v = g_emg_seq;
    g_emg_seq = (uint16_t)(v + 1);
    return v;
}

/* 缓存 EMG 样本：最多 EMG_SAMPLES_PER_FRAME 组，每组 8 通道 */
static int32_t emg_buf[EMG_SAMPLES_PER_FRAME][8];
static uint8_t emg_buf_count = 0;

/* 把 emg_buf 里当前攒的 sample_count 组打成一帧发出去 */
static void emg_flush_frame(uint8_t sample_count)
{
    if (sample_count == 0) {
        return;
    }

#if EN_BLE
    uint8_t  f[BLE_BUF_SIZE];
    uint16_t p = 0;

    /* 帧头：AA + 'E' */
    f[p++] = 0xAA;
    f[p++] = 'E';

    /* seq (u16 LE) */
    sys_put_le16(next_emg_seq(), &f[p]); p += 2;

    /* dev_id16（与 BT 名字一一对应） */
    sys_put_le16(device_id_get16(), &f[p]); p += 2;

    /* 全局时间戳：host_ms（按第 0 组 sample 的时间） */
    sys_put_le32(ble_link_now_host_ms(), &f[p]); p += 4;

    /* 采样率：与 ads1298.c 中 EMG_SAMPLE_RATE_SPS 一致 */
    sys_put_le16(EMG_SAMPLE_RATE_SPS, &f[p]); p += 2;

    /* 本帧包含的 sample 数（1~EMG_SAMPLES_PER_FRAME） */
    f[p++] = sample_count;

    /* 写入 sample_count 组，每组 8 通道 24bit (3B) */
    for (uint8_t s = 0; s < sample_count; s++) {
        for (int ch = 0; ch < 8; ch++) {
            int32_t v = emg_buf[s][ch];
            f[p++] = (uint8_t)((v >> 16) & 0xFF);
            f[p++] = (uint8_t)((v >>  8) & 0xFF);
            f[p++] = (uint8_t)( v        & 0xFF);
        }
    }

    (void)ble_link_send(f, p);
#else
    ARG_UNUSED(sample_count);
    /* 如果 EN_BLE=0，就什么都不发，只是丢掉缓存 */
#endif
}

/* ADS1298 每来一个时间点（8 路样本）就会回调一次 */
static void emg_frame_cb(const int32_t ch_code[8], const uint8_t status[3])
{
    ARG_UNUSED(status);

    /* 把当前这组 8 通道塞进缓存 */
    if (emg_buf_count < EMG_SAMPLES_PER_FRAME) {
        for (int i = 0; i < 8; i++) {
            emg_buf[emg_buf_count][i] = ch_code[i];
        }
        emg_buf_count++;
    }

    /* 攒满 EMG_SAMPLES_PER_FRAME 组，就打一帧发出去 */
    if (emg_buf_count >= EMG_SAMPLES_PER_FRAME) {
        emg_flush_frame(emg_buf_count);
        emg_buf_count = 0;
    }
}

/* ============ 主入口 ============ */

int main(void)
{
    int ret;
    int ble_ok = 0;

    /* 1) 设备 ID & BT 名字（这里会生成 ESxxxx/eFxxxx 名字） */
    device_id_init();
    LOG_INF("=== EMG + IMU TX Boot ===");
    LOG_INF("BT name: %s, dev_id: 0x%04x",
            device_bt_name_get(), device_id_get16());

#if EN_BLE
    /* 2) 初始化 BLE + NUS + APP_KEY/TSYNC 支持 */
    ret = ble_link_init();
    if (ret) {
        LOG_ERR("ble_link_init failed: %d", ret);
    } else {
        ble_ok = 1;
    }
#endif

#if EN_IMU
    /* 3) IMU：沿用 Gesture_Recognition 的逻辑（发 'I' 帧） */
    ret = imu_init();
    if (ret) {
        LOG_ERR("imu_init failed: %d", ret);
    } else {
        imu_start();
    }
#endif

#if EN_EMG
    /* 4) ADS1298：初始化 + 注册回调 + 启动采样线程 */
    ret = ads1298_init();
    if (ret) {
        LOG_ERR("ads1298_init failed: %d", ret);
    } else {
        /* 如果 BLE 成功初始化，就注册 EMG->BLE 回调；否则只跑 USB 输出 */
        if (ble_ok) {
            ads1298_set_frame_callback(emg_frame_cb);
        } else {
            ads1298_set_frame_callback(NULL);
        }

        /* 启动采样（内部仍然通过 USB CDC 输出 31B 帧，不影响） */
        ads1298_start();
    }
#endif

    /* 5) main 线程什么都不干，直接睡死 */
    while (1) {
        k_sleep(K_FOREVER);
    }

    return 0;
}
