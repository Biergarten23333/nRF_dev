#include "uwb_imu.h"

#include <stdbool.h>
#include <stdint.h>

#include <zephyr/devicetree.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/printk.h>

#if !DT_NODE_HAS_STATUS(DT_NODELABEL(lis2dh12), okay)
#error "DWM1001 board is expected to expose lis2dh12"
#endif

#define UWB_IMU_SAMPLE_ODR_HZ 100U
#define UWB_IMU_GRAVITY_MILLI_G 1000L

#define LIS2DH_REG_WAI 0x0F
#define LIS2DH_CHIP_ID 0x33

#define LIS2DH_REG_CTRL1 0x20
#define LIS2DH_ACCEL_EN_BITS 0x07
#define LIS2DH_ODR_100HZ_BITS 0x50

#define LIS2DH_REG_CTRL4 0x23
#define LIS2DH_CTRL4_BDU_BIT BIT(7)
#define LIS2DH_CTRL4_HR_BIT BIT(3)

#define LIS2DH_REG_STATUS 0x27
#define LIS2DH_STATUS_DRDY_MASK BIT_MASK(4)

#define LIS2DH_REG_ACCEL_X_LSB 0x28
#define LIS2DH_AUTOINCREMENT_ADDR BIT(7)

static struct i2c_dt_spec uwb_imu_i2c = {
    .bus = DEVICE_DT_GET(DT_NODELABEL(i2c0)),
    .addr = 0x19,
};

static struct {
    bool initialized;
    bool have_prev;
    int32_t prev_ax_mg;
    int32_t prev_ay_mg;
    int32_t prev_az_mg;
} uwb_imu_state;

static int32_t uwb_imu_isqrt_u64(uint64_t value)
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

    return (int32_t)res;
}

static int uwb_imu_write_reg(uint8_t reg, uint8_t value)
{
    return i2c_reg_write_byte_dt(&uwb_imu_i2c, reg, value);
}

static int uwb_imu_read_reg(uint8_t reg, uint8_t *value)
{
    return i2c_reg_read_byte_dt(&uwb_imu_i2c, reg, value);
}

static bool uwb_imu_probe_addr(uint16_t addr, uint8_t *whoami)
{
    struct i2c_dt_spec spec = {
        .bus = uwb_imu_i2c.bus,
        .addr = addr,
    };
    int ret;

    ret = i2c_reg_read_byte_dt(&spec, LIS2DH_REG_WAI, whoami);
    if (ret < 0) {
        return false;
    }

    if (*whoami != LIS2DH_CHIP_ID) {
        return false;
    }

    uwb_imu_i2c.addr = addr;
    return true;
}

static void uwb_imu_scan_bus(void)
{
    uint8_t found = 0U;

    printk("Tag accel I2C scan on %s: ", uwb_imu_i2c.bus->name);
    for (uint16_t addr = 0x08U; addr <= 0x77U; ++addr) {
        uint8_t dummy;
        struct i2c_msg msg = {
            .buf = &dummy,
            .len = 0U,
            .flags = I2C_MSG_WRITE | I2C_MSG_STOP,
        };

        if (i2c_transfer(uwb_imu_i2c.bus, &msg, 1, addr) == 0) {
            printk("0x%02x ", (unsigned int)addr);
            ++found;
        }
    }

    if (found == 0U) {
        printk("(none)");
    }

    printk("\n");
}

int uwb_imu_init(void)
{
    uint8_t whoami = 0U;
    int ret;

    if (!device_is_ready(uwb_imu_i2c.bus)) {
        printk("Tag accel I2C bus not ready: %s\n", uwb_imu_i2c.bus->name);
        return -1;
    }

    uwb_imu_scan_bus();

    if (!uwb_imu_probe_addr(0x19U, &whoami) &&
        !uwb_imu_probe_addr(0x18U, &whoami)) {
        printk("Tag accel WHO_AM_I probe failed at 0x18/0x19\n");
        return -1;
    }

    ret = uwb_imu_read_reg(LIS2DH_REG_WAI, &whoami);
    if (ret < 0) {
        printk("Tag accel WHO_AM_I read failed: %d\n", ret);
        return ret;
    }

    ret = uwb_imu_write_reg(LIS2DH_REG_CTRL1,
                            LIS2DH_ACCEL_EN_BITS | LIS2DH_ODR_100HZ_BITS);
    if (ret < 0) {
        printk("Tag accel CTRL1 init failed: %d\n", ret);
        return ret;
    }

    ret = uwb_imu_write_reg(LIS2DH_REG_CTRL4,
                            LIS2DH_CTRL4_BDU_BIT | LIS2DH_CTRL4_HR_BIT);
    if (ret < 0) {
        printk("Tag accel CTRL4 init failed: %d\n", ret);
        return ret;
    }

    uwb_imu_state.initialized = true;
    uwb_imu_state.have_prev = false;
    uwb_imu_state.prev_ax_mg = 0;
    uwb_imu_state.prev_ay_mg = 0;
    uwb_imu_state.prev_az_mg = 0;

    printk("Tag accel sensor ready: lis2dh12 addr=0x%02x whoami=0x%02x odr=%uHz\n",
           (unsigned int)uwb_imu_i2c.addr, whoami, (unsigned int)UWB_IMU_SAMPLE_ODR_HZ);
    return 0;
}

bool uwb_imu_read(struct uwb_imu_sample *sample)
{
    uint8_t raw[7];
    uint8_t status;
    uint16_t raw_x;
    uint16_t raw_y;
    uint16_t raw_z;
    int32_t ax_mg;
    int32_t ay_mg;
    int32_t az_mg;
    uint64_t norm_sq;
    uint64_t delta_sq = 0U;
    int32_t norm_mg;
    int32_t delta_magnitude_mg;
    uint32_t read_start_cycle;
    uint32_t read_end_cycle;
    int ret;

    if (sample == NULL || !uwb_imu_state.initialized) {
        return false;
    }

    read_start_cycle = k_cycle_get_32();
    ret = i2c_burst_read_dt(&uwb_imu_i2c,
                            LIS2DH_REG_STATUS | LIS2DH_AUTOINCREMENT_ADDR,
                            raw, sizeof(raw));
    read_end_cycle = k_cycle_get_32();
    if (ret < 0) {
        printk("Tag accel burst read failed: %d\n", ret);
        return false;
    }

    status = raw[0];
    if ((status & LIS2DH_STATUS_DRDY_MASK) == 0U) {
        return false;
    }

    raw_x = sys_get_le16(&raw[1]);
    raw_y = sys_get_le16(&raw[3]);
    raw_z = sys_get_le16(&raw[5]);

    ax_mg = ((int16_t)raw_x) >> 4;
    ay_mg = ((int16_t)raw_y) >> 4;
    az_mg = ((int16_t)raw_z) >> 4;

    norm_sq = (uint64_t)((int64_t)ax_mg * (int64_t)ax_mg) +
              (uint64_t)((int64_t)ay_mg * (int64_t)ay_mg) +
              (uint64_t)((int64_t)az_mg * (int64_t)az_mg);
    norm_mg = uwb_imu_isqrt_u64(norm_sq);

    if (uwb_imu_state.have_prev) {
        int32_t dx = ax_mg - uwb_imu_state.prev_ax_mg;
        int32_t dy = ay_mg - uwb_imu_state.prev_ay_mg;
        int32_t dz = az_mg - uwb_imu_state.prev_az_mg;

        delta_sq = (uint64_t)((int64_t)dx * (int64_t)dx) +
                   (uint64_t)((int64_t)dy * (int64_t)dy) +
                   (uint64_t)((int64_t)dz * (int64_t)dz);
    }

    delta_magnitude_mg = uwb_imu_isqrt_u64(delta_sq);

    sample->valid = true;
    sample->ax_mg = ax_mg;
    sample->ay_mg = ay_mg;
    sample->az_mg = az_mg;
    sample->norm_mg = norm_mg;
    sample->gravity_error_mg = norm_mg - UWB_IMU_GRAVITY_MILLI_G;
    sample->delta_magnitude_mg = (uint32_t)delta_magnitude_mg;
    sample->timestamp_ms = (uint32_t)k_uptime_get();
    sample->read_start_cycle = read_start_cycle;
    sample->read_end_cycle = read_end_cycle;
    sample->timestamp_cycle =
        read_start_cycle + (uint32_t)((read_end_cycle - read_start_cycle) / 2U);

    uwb_imu_state.have_prev = true;
    uwb_imu_state.prev_ax_mg = ax_mg;
    uwb_imu_state.prev_ay_mg = ay_mg;
    uwb_imu_state.prev_az_mg = az_mg;

    return true;
}
