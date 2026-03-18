#include "uwb_imu.h"

#include <stdbool.h>
#include <stdint.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#if !DT_NODE_HAS_STATUS(DT_ALIAS(accel0), okay)
#error "DWM1001 board is expected to expose accel0 alias"
#endif

#define UWB_IMU_SAMPLE_ODR_HZ 50U
#define UWB_IMU_GRAVITY_MILLI_MPS2 9807L

static const struct device *const uwb_imu_dev = DEVICE_DT_GET(DT_ALIAS(accel0));

static struct {
    bool initialized;
    bool have_prev;
    int32_t prev_ax_milli_mps2;
    int32_t prev_ay_milli_mps2;
    int32_t prev_az_milli_mps2;
} uwb_imu_state;

static int32_t uwb_imu_sensor_value_to_milli(const struct sensor_value *value)
{
    int64_t total_micro = (int64_t)value->val1 * 1000000LL + value->val2;
    return (int32_t)(total_micro / 1000LL);
}

static uint32_t uwb_imu_isqrt_u64(uint64_t value)
{
    uint64_t op = value;
    uint64_t res = 0;
    uint64_t one = 1ULL << 62;

    while (one > op) {
        one >>= 2;
    }

    while (one != 0U) {
        if (op >= res + one) {
            op -= res + one;
            res = (res >> 1) + one;
        } else {
            res >>= 1;
        }

        one >>= 2;
    }

    return (uint32_t)res;
}

static int uwb_imu_configure_odr(void)
{
    struct sensor_value odr = {
        .val1 = UWB_IMU_SAMPLE_ODR_HZ,
        .val2 = 0,
    };
    int ret;

    ret = sensor_attr_set(uwb_imu_dev, SENSOR_CHAN_ACCEL_XYZ,
                          SENSOR_ATTR_SAMPLING_FREQUENCY, &odr);
    if (ret != 0) {
        printk("Tag accel ODR set skipped ret=%d\n", ret);
        return 0;
    }

    printk("Tag accel ODR=%u Hz\n", (unsigned int)UWB_IMU_SAMPLE_ODR_HZ);
    return 0;
}

int uwb_imu_init(void)
{
    if (!device_is_ready(uwb_imu_dev)) {
        printk("Tag accel sensor not ready: %s\n", uwb_imu_dev->name);
        return -1;
    }

    uwb_imu_configure_odr();

    uwb_imu_state.initialized = true;
    uwb_imu_state.have_prev = false;
    uwb_imu_state.prev_ax_milli_mps2 = 0;
    uwb_imu_state.prev_ay_milli_mps2 = 0;
    uwb_imu_state.prev_az_milli_mps2 = 0;

    printk("Tag accel sensor ready: %s alias=accel0\n", uwb_imu_dev->name);
    return 0;
}

bool uwb_imu_read(struct uwb_imu_sample *sample)
{
    struct sensor_value accel[3];
    int ret;
    int32_t ax_milli_mps2;
    int32_t ay_milli_mps2;
    int32_t az_milli_mps2;
    uint64_t norm_sq;
    uint64_t delta_sq = 0U;
    uint32_t norm_milli_mps2;
    uint32_t delta_magnitude_milli_mps2;

    if (sample == NULL || !uwb_imu_state.initialized) {
        return false;
    }

    ret = sensor_sample_fetch(uwb_imu_dev);
    if (ret < 0) {
        printk("Tag accel sensor fetch failed: %d\n", ret);
        return false;
    }

    ret = sensor_channel_get(uwb_imu_dev, SENSOR_CHAN_ACCEL_X, &accel[0]);
    if (ret < 0) {
        printk("Tag accel X read failed: %d\n", ret);
        return false;
    }

    ret = sensor_channel_get(uwb_imu_dev, SENSOR_CHAN_ACCEL_Y, &accel[1]);
    if (ret < 0) {
        printk("Tag accel Y read failed: %d\n", ret);
        return false;
    }

    ret = sensor_channel_get(uwb_imu_dev, SENSOR_CHAN_ACCEL_Z, &accel[2]);
    if (ret < 0) {
        printk("Tag accel Z read failed: %d\n", ret);
        return false;
    }

    ax_milli_mps2 = uwb_imu_sensor_value_to_milli(&accel[0]);
    ay_milli_mps2 = uwb_imu_sensor_value_to_milli(&accel[1]);
    az_milli_mps2 = uwb_imu_sensor_value_to_milli(&accel[2]);

    norm_sq = (uint64_t)((int64_t)ax_milli_mps2 * (int64_t)ax_milli_mps2) +
              (uint64_t)((int64_t)ay_milli_mps2 * (int64_t)ay_milli_mps2) +
              (uint64_t)((int64_t)az_milli_mps2 * (int64_t)az_milli_mps2);
    norm_milli_mps2 = uwb_imu_isqrt_u64(norm_sq);

    if (uwb_imu_state.have_prev) {
        int32_t dx = ax_milli_mps2 - uwb_imu_state.prev_ax_milli_mps2;
        int32_t dy = ay_milli_mps2 - uwb_imu_state.prev_ay_milli_mps2;
        int32_t dz = az_milli_mps2 - uwb_imu_state.prev_az_milli_mps2;

        delta_sq = (uint64_t)((int64_t)dx * (int64_t)dx) +
                   (uint64_t)((int64_t)dy * (int64_t)dy) +
                   (uint64_t)((int64_t)dz * (int64_t)dz);
    }

    delta_magnitude_milli_mps2 = uwb_imu_isqrt_u64(delta_sq);

    sample->valid = true;
    sample->ax_milli_mps2 = ax_milli_mps2;
    sample->ay_milli_mps2 = ay_milli_mps2;
    sample->az_milli_mps2 = az_milli_mps2;
    sample->norm_milli_mps2 = (int32_t)norm_milli_mps2;
    sample->gravity_error_milli_mps2 =
        (int32_t)norm_milli_mps2 - UWB_IMU_GRAVITY_MILLI_MPS2;
    sample->delta_magnitude_milli_mps2 = delta_magnitude_milli_mps2;
    sample->timestamp_ms = (uint32_t)k_uptime_get();

    uwb_imu_state.have_prev = true;
    uwb_imu_state.prev_ax_milli_mps2 = ax_milli_mps2;
    uwb_imu_state.prev_ay_milli_mps2 = ay_milli_mps2;
    uwb_imu_state.prev_az_milli_mps2 = az_milli_mps2;

    return true;
}
