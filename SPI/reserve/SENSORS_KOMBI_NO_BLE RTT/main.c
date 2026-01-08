/* src/main.c — 带内置时戳的 IMU+UWB+ECG */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(main, LOG_LEVEL_INF);

/* ---------- 日志时戳宏 ---------- */
#define ENABLE_LOG_TS 1

#if ENABLE_LOG_TS
    #define LOG_TS_INF(tag, fmt, ...) \
        do { \
            uint32_t cycles = k_cycle_get_32(); \
            uint64_t us = (uint64_t)cycles * 1000000ULL \
                          / sys_clock_hw_cycles_per_sec(); \
            uint32_t h  = us / 3600000000ULL; \
            uint32_t m  = (us /   60000000ULL) % 60U; \
            uint32_t s  = (us /    1000000ULL) % 60U; \
            uint32_t ms = (us /       1000ULL) % 1000U; \
            uint32_t usr=  us                % 1000U; \
            LOG_INF("[%02u:%02u:%02u.%03u%03u][%s] " fmt, \
                    h, m, s, ms, usr, tag, ##__VA_ARGS__); \
        } while (0)

    #define LOG_TS_ERR(tag, fmt, ...) \
        do { /* same calc... */ \
            uint32_t cycles = k_cycle_get_32(); \
            uint64_t us = (uint64_t)cycles * 1000000ULL \
                          / sys_clock_hw_cycles_per_sec(); \
            uint32_t h  = us / 3600000000ULL; \
            uint32_t m  = (us /   60000000ULL) % 60U; \
            uint32_t s  = (us /    1000000ULL) % 60U; \
            uint32_t ms = (us /       1000ULL) % 1000U; \
            uint32_t usr=  us                % 1000U; \
            LOG_ERR("[%02u:%02u:%02u.%03u%03u][%s] " fmt, \
                    h, m, s, ms, usr, tag, ##__VA_ARGS__); \
        } while (0)

    #define LOG_TS_DBG(tag, fmt, ...) \
        do { /* same calc... */ \
            uint32_t cycles = k_cycle_get_32(); \
            uint64_t us = (uint64_t)cycles * 1000000ULL \
                          / sys_clock_hw_cycles_per_sec(); \
            uint32_t h  = us / 3600000000ULL; \
            uint32_t m  = (us /   60000000ULL) % 60U; \
            uint32_t s  = (us /    1000000ULL) % 60U; \
            uint32_t ms = (us /       1000ULL) % 1000U; \
            uint32_t usr=  us                % 1000U; \
            LOG_DBG("[%02u:%02u:%02u.%03u%03u][%s] " fmt, \
                    h, m, s, ms, usr, tag, ##__VA_ARGS__); \
        } while (0)

#else
    #define LOG_TS_INF(tag, fmt, ...)  LOG_INF(fmt, ##__VA_ARGS__)
    #define LOG_TS_ERR(tag, fmt, ...)  LOG_ERR(fmt, ##__VA_ARGS__)
    #define LOG_TS_DBG(tag, fmt, ...)  LOG_DBG(fmt, ##__VA_ARGS__)
#endif

/* ---------- MPU6050 IMU ---------- */
#define MPU6050_NODE   DT_INST(0, invensense_mpu6050)
static const struct device *mpu = DEVICE_DT_GET(MPU6050_NODE);
static struct sensor_trigger mpu_trig;

static inline void format_val(double val, char *buf, size_t len)
{
    int32_t tot = (int32_t)lround(val * 100);
    snprintf(buf, len, "%d.%02d",
             tot/100, abs(tot%100));
}

static void mpu_data_ready(const struct device *dev,
                           const struct sensor_trigger *trig)
{
    struct sensor_value accel[3], gyro[3];
    sensor_sample_fetch_chan(dev, SENSOR_CHAN_ACCEL_XYZ);
    sensor_channel_get(dev, SENSOR_CHAN_ACCEL_XYZ, accel);
    sensor_sample_fetch_chan(dev, SENSOR_CHAN_GYRO_XYZ);
    sensor_channel_get(dev, SENSOR_CHAN_GYRO_XYZ, gyro);

    char ax[16], ay[16], az[16];
    char gx[16], gy[16], gz[16];
    format_val(sensor_value_to_double(&accel[0]), ax, sizeof(ax));
    format_val(sensor_value_to_double(&accel[1]), ay, sizeof(ay));
    format_val(sensor_value_to_double(&accel[2]), az, sizeof(az));
    format_val(sensor_value_to_double(&gyro[0]), gx, sizeof(gx));
    format_val(sensor_value_to_double(&gyro[1]), gy, sizeof(gy));
    format_val(sensor_value_to_double(&gyro[2]), gz, sizeof(gz));

    static int cnt;
    if (++cnt >= 20) {  /* 200Hz/20 = 10Hz */
        cnt = 0;
        LOG_TS_INF("IMU",
            "A[%s,%s,%s] G[%s,%s,%s]",
            ax, ay, az, gx, gy, gz);
    }
}

static void imu_init(void)
{
    if (!device_is_ready(mpu)) {
        LOG_TS_ERR("IMU", "MPU6050 not ready");
        return;
    }
    struct sensor_value freq = { .val1 = 200, .val2 = 0 };
    sensor_attr_set(mpu, SENSOR_CHAN_ACCEL_XYZ,
                    SENSOR_ATTR_SAMPLING_FREQUENCY, &freq);
    sensor_attr_set(mpu, SENSOR_CHAN_GYRO_XYZ,
                    SENSOR_ATTR_SAMPLING_FREQUENCY, &freq);
    mpu_trig.type = SENSOR_TRIG_DATA_READY;
    mpu_trig.chan = SENSOR_CHAN_ALL;
    sensor_trigger_set(mpu, &mpu_trig, mpu_data_ready);
    LOG_TS_INF("IMU", "initialized");
}

/* ---------- UWB 定位 ---------- */
static const uint8_t CMD_LOC_GET[] = {0x0C,0x00};
static const uint8_t TLV_INT_EN[]  = {0x34,0x02,0x01,0x00};
#define BUF_MAX 128

static const struct device *uart1_dev, *gpio1_dev;
static struct gpio_callback ready_cb;
static struct k_sem sem_ready;

static void ready_isr(const struct device *dev,
                      struct gpio_callback *cb, uint32_t pins)
{
    ARG_UNUSED(dev); ARG_UNUSED(cb); ARG_UNUSED(pins);
    k_sem_give(&sem_ready);
}

static bool try_parse_loc(const uint8_t *buf, size_t len)
{
    for (size_t i = 0; i + 14 <= len; i++) {
        if (buf[i]==0x41 && buf[i+1]==0x0D) {
            int32_t x = sys_get_le32(buf+i+2);
            int32_t y = sys_get_le32(buf+i+6);
            int32_t z = sys_get_le32(buf+i+10);
            uint8_t q = buf[i+14];
            LOG_TS_INF("UWB", "POS[%d,%d,%d] QF[%u%%]", x,y,z,q);
            return true;
        }
    }
    return false;
}

static void uwb_thread(void *, void *, void *)
{
    uart1_dev = DEVICE_DT_GET(DT_NODELABEL(uart1));
    gpio1_dev = DEVICE_DT_GET(DT_NODELABEL(gpio1));
    if (!device_is_ready(uart1_dev) ||
        !device_is_ready(gpio1_dev)) {
        LOG_TS_ERR("UWB", "UART1/GPIO1 not ready");
        return;
    }
    gpio_pin_configure(gpio1_dev, 3,
                       GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_interrupt_configure(gpio1_dev, 3,
                                 GPIO_INT_EDGE_RISING);
    gpio_init_callback(&ready_cb, ready_isr, BIT(3));
    gpio_add_callback(gpio1_dev, &ready_cb);
    k_sem_init(&sem_ready, 0, 1);

    /* 唤醒 & 使能中断 */
    for (int i = 0; i < 3; i++) {
        uart_poll_out(uart1_dev, 0x00);
    }
    uart_poll_out(uart1_dev, TLV_INT_EN[0]);
    uart_poll_out(uart1_dev, TLV_INT_EN[1]);
    uart_poll_out(uart1_dev, TLV_INT_EN[2]);
    uart_poll_out(uart1_dev, TLV_INT_EN[3]);
    k_sleep(K_MSEC(10));
    LOG_TS_INF("UWB", "thread ready");

    while (1) {
        k_sem_take(&sem_ready, K_FOREVER);
        uart_poll_out(uart1_dev, CMD_LOC_GET[0]);
        uart_poll_out(uart1_dev, CMD_LOC_GET[1]);

        uint8_t buf[BUF_MAX];
        size_t idx = 0;
        uint64_t deadline = k_uptime_get() + 50;
        while (k_uptime_get() < deadline && idx < BUF_MAX) {
            uint8_t ch;
            if (uart_poll_in(uart1_dev, &ch) == 0) {
                buf[idx++] = ch;
            } else {
                k_busy_wait(50);
            }
        }
        if (!try_parse_loc(buf, idx)) {
            LOG_TS_INF("UWB", "(No POS TLV)");
        }
    }
}
K_THREAD_DEFINE(uwb_tid, 1024, uwb_thread,
                NULL, NULL, NULL, 5, 0, 0);

/* ---------- ECG ADC with pole-off 检测 ---------- */
static const struct adc_dt_spec adc =
    ADC_DT_SPEC_GET(DT_PATH(zephyr_user));
static const struct device *gpio1_ecg =
    DEVICE_DT_GET(DT_NODELABEL(gpio1));

#define SAMPLING_RATE_HZ     250U
#define SAMPLING_INTERVAL_US (1000000U/SAMPLING_RATE_HZ)

static void ecg_thread(void *, void *, void *)
{
    if (!device_is_ready(gpio1_ecg)) {
        LOG_TS_ERR("ECG", "gpio1 not ready");
        return;
    }
    gpio_pin_configure(gpio1_ecg, 4,
                       GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_configure(gpio1_ecg, 5,
                       GPIO_INPUT | GPIO_PULL_UP);

    if (!adc_is_ready_dt(&adc)) {
        LOG_TS_ERR("ECG", "ADC not ready");
        return;
    }
    if (adc_channel_setup_dt(&adc) < 0) {
        LOG_TS_ERR("ECG", "ADC setup failed");
        return;
    }

    struct adc_sequence_options opts = {
        .interval_us     = SAMPLING_INTERVAL_US,
        .extra_samplings = 0,
    };
    int16_t sample;
    struct adc_sequence seq = {
        .options     = &opts,
        .buffer      = &sample,
        .buffer_size = sizeof(sample),
    };
    adc_sequence_init_dt(&adc, &seq);

    struct k_poll_signal sig;
    k_poll_signal_init(&sig);
    struct k_poll_event  evt =
        K_POLL_EVENT_INITIALIZER(
            K_POLL_TYPE_SIGNAL,
            K_POLL_MODE_NOTIFY_ONLY,
            &sig);

    int cnt = 0;
    while (1) {
        bool pole_off =
            gpio_pin_get(gpio1_ecg, 4) &&
            gpio_pin_get(gpio1_ecg, 5);
        if (pole_off) {
            //LOG_TS_INF("ECG", "POLE OFF");
            k_busy_wait(SAMPLING_INTERVAL_US);
            continue;
        }

        if (adc_read_async(adc.dev, &seq, &sig) < 0) {
            LOG_TS_ERR("ECG", "ADC async start failed");
            return;
        }
        k_poll(&evt, 1, K_FOREVER);

        if (++cnt >= 25) {  /* 250Hz/25 = 10Hz */
            cnt = 0;
            LOG_TS_INF("ECG", "[%d]", sample);
        }
        k_busy_wait(SAMPLING_INTERVAL_US);
    }
}
K_THREAD_DEFINE(ecg_tid, 1024, ecg_thread,
                NULL, NULL, NULL, 7, 0, 0);

/* ---------- 主函数 ---------- */
int main(void)
{
    k_sleep(K_MSEC(500));
    LOG_TS_INF("SYS", "init complete, starting IMU/UWB/ECG");
    imu_init();

    while (1) {
        k_sleep(K_SECONDS(1));
    }
    return 0;
}
