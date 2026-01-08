/*
 * nRF52840 + Zephyr 3.7 —— JY901B I²C 读取示例（200 Hz / 9-axis）
 */
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(jy901b_i2c, LOG_LEVEL_INF);

/* -------- 常量 -------- */
#define I2C_NODE        DT_NODELABEL(i2c0)
#define IMU_ADDR        0x50          /* 7-bit 地址 */
#define REG_START       0x34          /* Acc X_L */
#define REG_LEN         18            /* Acc+Gyro+Mag 共 18 B */

static const struct device *const i2c_dev = DEVICE_DT_GET(I2C_NODE);

/* 小端转 int16 */
static inline int16_t le16(const uint8_t *p)
{
    return (int16_t)((p[1] << 8) | p[0]);
}

/* 把 RRATE 设为 200 Hz（立即生效并写 Flash） */
static int imu_set_200hz(void)
{
    uint8_t buf[3];

    /* 解锁写保护 */
    buf[0] = 0x69; buf[1] = 0x88; buf[2] = 0xB5;
    if (i2c_write(i2c_dev, buf, 3, IMU_ADDR)) return -1;

    /* RRATE = 0x08 → 200 Hz */
    buf[0] = 0x03; buf[1] = 0x08; buf[2] = 0x00;
    if (i2c_write(i2c_dev, buf, 3, IMU_ADDR)) return -2;

    /* 保存到内闪，掉电仍保留（可选） */
    buf[0] = 0x00; buf[1] = 0x00; buf[2] = 0x00;
    if (i2c_write(i2c_dev, buf, 3, IMU_ADDR)) return -3;

    LOG_INF("RRATE set to 200 Hz");
    return 0;
}

void main(void)
{
    if (!device_is_ready(i2c_dev)) {
        LOG_ERR("I2C0 not ready");
        return;
    }
    LOG_INF("I2C0 ready, addr=0x%02X", IMU_ADDR);

    /* 若已永久写过 200 Hz，可把下面这行注释掉 */
    imu_set_200hz();

    /* 提升当前线程优先级，避免 logger 反抢 */
    k_thread_priority_set(k_current_get(), 5);

    uint8_t reg = REG_START;
    uint8_t buf[REG_LEN];

    while (1) {
        /* 读取 18 B：Acc(0x34)~Mag(0x45) */
        int ret = i2c_write_read(i2c_dev, IMU_ADDR, &reg, 1,
                                 buf, sizeof(buf));
        if (ret) {
            LOG_ERR("I2C err %d", ret);
            /* 出错也保持节拍，避免 CPU 占满 */
            k_sleep(K_MSEC(5));
            continue;
        }

        /* ---- 加速度（m/s²） ---- */
        float ax = le16(&buf[0])  / 32768.0f * 16.0f * 9.8f;
        float ay = le16(&buf[2])  / 32768.0f * 16.0f * 9.8f;
        float az = le16(&buf[4])  / 32768.0f * 16.0f * 9.8f;

        /* ---- 陀螺仪（°/s） ---- */
        float gx = le16(&buf[6])  / 32768.0f * 2000.0f;
        float gy = le16(&buf[8])  / 32768.0f * 2000.0f;
        float gz = le16(&buf[10]) / 32768.0f * 2000.0f;

        /* ---- 磁场（mG） ---- */
        float mx = le16(&buf[12]) / 32768.0f * 1000.0f;
        float my = le16(&buf[14]) / 32768.0f * 1000.0f;
        float mz = le16(&buf[16]) / 32768.0f * 1000.0f;

        /* 每帧都打印（≈200 行/秒） */
        LOG_INF("Acc[m/s²] %+7.2f %+7.2f %+7.2f | "
                "Gyr[°/s] %+7.2f %+7.2f %+7.2f | "
                "Mag[mG] %+7.2f %+7.2f %+7.2f",
                (double)ax,(double)ay,(double)az,
                (double)gx,(double)gy,(double)gz,
                (double)mx,(double)my,(double)mz);

        k_sleep(K_MSEC(5));      /* 采样节拍 200 Hz */
    }
}
