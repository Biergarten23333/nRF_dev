#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/i2c.h>
#include <math.h>
#include <zephyr/sys/byteorder.h>   // ← 为了 sys_put_le16 / sys_put_le32

#include "app_config.h"
#include "device_id.h"
#include "ble_link.h"
#include "imu_task.h"

/* ========================= IMU (I2C) ========================= */
#define I2C_NODE   DT_NODELABEL(i2c0)
#define IMU_ADDR   0x50
#define REG_START  0x34
#define REG_LEN    18

static inline int16_t le16(const uint8_t *p)
{
    return (int16_t)((p[1] << 8) | p[0]);
}

static const struct device *const i2c_dev = DEVICE_DT_GET(I2C_NODE);

K_SEM_DEFINE(imu_start_sem, 0, 1);

/* 序列号本地维护 */
static uint16_t seq_imu = 0;
static inline uint16_t next_seq_imu(void)
{
    uint16_t v = seq_imu;
    seq_imu = (uint16_t)(v + 1);
    return v;
}

/* IMU frame 'I' — ax,ay,az / gx,gy,gz */
static void send_frame_imu(float ax_ms2, float ay_ms2, float az_ms2,
                           float gx_dps, float gy_dps, float gz_dps)
{
    if (!EN_IMU || !EN_BLE) return;

    int16_t ax = (int16_t)lrintf((ax_ms2 / 9.80665f) * 1000.0f);
    int16_t ay = (int16_t)lrintf((ay_ms2 / 9.80665f) * 1000.0f);
    int16_t az = (int16_t)lrintf((az_ms2 / 9.80665f) * 1000.0f);
    int16_t gx = (int16_t)lrintf(gx_dps * 10.0f);
    int16_t gy = (int16_t)lrintf(gy_dps * 10.0f);
    int16_t gz = (int16_t)lrintf(gz_dps * 10.0f);

    uint8_t f[BLE_BUF_SIZE];
    uint16_t p = 0;
    f[p++] = 0xAA;
    f[p++] = 'I';
    sys_put_le16(next_seq_imu(), &f[p]); p += 2;
    sys_put_le16(dev_id16, &f[p]);       p += 2;
    sys_put_le32(ts_ms(), &f[p]);        p += 4;
    sys_put_le16(ax, &f[p]); p += 2;
    sys_put_le16(ay, &f[p]); p += 2;
    sys_put_le16(az, &f[p]); p += 2;
    sys_put_le16(gx, &f[p]); p += 2;
    sys_put_le16(gy, &f[p]); p += 2;
    sys_put_le16(gz, &f[p]); p += 2;

    ble_enqueue_frame_norm(f, p);
}

int imu_post_once(void)
{
    if (!EN_IMU) return 0;
    if (!device_is_ready(i2c_dev)) {
        LOGF("[IMU] I2C0 not ready\n");
        return -ENODEV;
    }
    uint8_t cmd1[3] = {0x69, 0x88, 0xB5};
    uint8_t cmd2[3] = {0x03, 0x08, 0x00};
    (void)i2c_write(i2c_dev, cmd1, 3, IMU_ADDR);
    (void)i2c_write(i2c_dev, cmd2, 3, IMU_ADDR);
    k_msleep(5);

    uint8_t reg = REG_START, buf[REG_LEN] = {0};
    int ret = i2c_write_read(i2c_dev, IMU_ADDR, &reg, 1, buf, REG_LEN);
    if (ret) {
        LOGF("[IMU] POST read err=%d\n", ret);
        return ret;
    }

    bool all0 = true, allff = true;
    for (int i = 0; i < REG_LEN; i++) {
        all0  &= (buf[i] == 0);
        allff &= (buf[i] == 0xFF);
    }
    if (all0 || allff) {
        LOGF("[IMU] invalid frame\n");
        return -EIO;
    }

    float ax = le16(&buf[0])  / 32768.f * 16.f * 9.8f;
    float ay = le16(&buf[2])  / 32768.f * 16.f * 9.8f;
    float az = le16(&buf[4])  / 32768.f * 16.f * 9.8f;
    float gx = le16(&buf[6])  / 32768.f * 2000.f;
    float gy = le16(&buf[8])  / 32768.f * 2000.f;
    float gz = le16(&buf[10]) / 32768.f * 2000.f;

    LOGF("[IMU] ax=%.2f ay=%.2f az=%.2f gx=%.1f gy=%.1f gz=%.1f\n",
         (double)ax, (double)ay, (double)az,
         (double)gx, (double)gy, (double)gz);
    return 0;
}

void imu_start_loop(void)
{
    k_sem_give(&imu_start_sem);
}

static void imu_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!EN_IMU) return;

    k_sem_take(&imu_start_sem, K_FOREVER);
    if (!device_is_ready(i2c_dev)) {
        LOGF("[IMU] I2C not ready\n");
        return;
    }

    uint8_t cmd1[3] = {0x69, 0x88, 0xB5};
    (void)i2c_write(i2c_dev, cmd1, 3, IMU_ADDR);
    uint8_t cmd2[3] = {0x03, 0x08, 0x00};
    (void)i2c_write(i2c_dev, cmd2, 3, IMU_ADDR);

    uint8_t reg = REG_START, buf[REG_LEN];
    int64_t last_out = 0;

    while (1) {
        if (i2c_write_read(i2c_dev, IMU_ADDR, &reg, 1, buf, REG_LEN) == 0) {
            float ax = le16(&buf[0])  / 32768.f * 16.f * 9.8f;
            float ay = le16(&buf[2])  / 32768.f * 16.f * 9.8f;
            float az = le16(&buf[4])  / 32768.f * 16.f * 9.8f;
            float gx = le16(&buf[6])  / 32768.f * 2000.f;
            float gy = le16(&buf[8])  / 32768.f * 2000.f;
            float gz = le16(&buf[10]) / 32768.f * 2000.f;

            int64_t now = k_uptime_get();
            if (now - last_out >= 5) {
                last_out = now;
                send_frame_imu(ax, ay, az, gx, gy, gz);
            }
        }
        k_sleep(K_MSEC(2));
    }
}

K_THREAD_DEFINE(imu_tid, IMU_STACK_SIZE,
                imu_thread, NULL, NULL, NULL,
                IMU_PRIO, 0, 0);
