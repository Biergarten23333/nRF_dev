/* src/main.c — 带内置时戳的 IMU+UWB+ECG */

#include <stdio.h>
#include <math.h>
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>
/* ---------- 日志时戳宏 ---------- */
/* 在 config.h 或者 main.c 顶部添加：*/
#define ENABLE_LOG_TS 1    /* 设为 1 开日志、设为 0 关日志 */

#if ENABLE_LOG_TS
    #define LOG_TS(tag, fmt, ...)                                                  \
        do {                                                                          \
            uint32_t cycles = k_cycle_get_32();                                       \
            /* 周期数 ➔ 微秒 */                                                        \
            uint64_t us_total = (uint64_t)cycles * 1000000ULL                          \
                                / sys_clock_hw_cycles_per_sec();                      \
            /* 计算时分秒毫秒和微秒残余 */                                             \
            uint32_t h      = us_total / 3600000000ULL;                               \
            uint32_t m      = (us_total /   60000000ULL) % 60U;                        \
            uint32_t s      = (us_total /    1000000ULL) % 60U;                        \
            uint32_t ms     = (us_total /       1000ULL) % 1000U;                      \
            uint32_t us_rem =  us_total                 % 1000U;                      \
            printk("[%02u:%02u:%02u.%03u%03u][%s] " fmt,                               \
                h, m, s, ms, us_rem, tag, ##__VA_ARGS__);                           \
        } while (0)
#else
  /* 直接什么都不做 */
  #define LOG_TS(tag, fmt, ...)    do {} while (0)
#endif

/* ---------- MPU6050 IMU ---------- */
#define MPU6050_NODE   DT_INST(0, invensense_mpu6050)
static const struct device *mpu = DEVICE_DT_GET(MPU6050_NODE);
static struct sensor_trigger mpu_trig;

static inline void format_val(double val, char *buf, size_t len)
{
    int32_t total = (int32_t)lround(val * 100);
    int32_t iv    = total / 100;
    int32_t fv    = abs(total % 100);
    snprintf(buf, len, "%d.%02d", iv, fv);
}

static void mpu_data_ready(const struct device *dev,
                           const struct sensor_trigger *trig)
{
    struct sensor_value accel[3], gyro[3];
    sensor_sample_fetch_chan(dev, SENSOR_CHAN_ACCEL_XYZ);
    sensor_channel_get(dev, SENSOR_CHAN_ACCEL_XYZ, accel);
    sensor_sample_fetch_chan(dev, SENSOR_CHAN_GYRO_XYZ);
    sensor_channel_get(dev, SENSOR_CHAN_GYRO_XYZ, gyro);

    char ax_s[16], ay_s[16], az_s[16];
    char gx_s[16], gy_s[16], gz_s[16];
    format_val(sensor_value_to_double(&accel[0]), ax_s, sizeof(ax_s));
    format_val(sensor_value_to_double(&accel[1]), ay_s, sizeof(ay_s));
    format_val(sensor_value_to_double(&accel[2]), az_s, sizeof(az_s));
    format_val(sensor_value_to_double(&gyro[0]), gx_s, sizeof(gx_s));
    format_val(sensor_value_to_double(&gyro[1]), gy_s, sizeof(gy_s));
    format_val(sensor_value_to_double(&gyro[2]), gz_s, sizeof(gz_s));

    static int cnt;
    if (++cnt >= 1) {  /* 200Hz/20 = 10Hz */
        cnt = 0;
        LOG_TS("IMU","A[%s,%s,%s]G[%s,%s,%s]\n",
               ax_s, ay_s, az_s, gx_s, gy_s, gz_s);
    }
}

static void imu_init(void)
{
    if (!device_is_ready(mpu)) {
        LOG_TS("IMU", "ERROR: MPU6050 not ready\n");
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
    LOG_TS("IMU", "initialized\n");
}


/* ---------- UWB 定位 ---------- */
#define WAKE_BYTE      0x00
static const uint8_t CMD_LOC_GET[2] = {0x0C,0x00};
static const uint8_t TLV_INT_EN[4]  = {0x34,0x02,0x01,0x00};
#define BUF_MAX 128

static const struct device *uart1_dev;
static const struct device *gpio1_dev;
static struct gpio_callback ready_cb;
static struct k_sem sem_ready;

static inline void uart_write_block(const struct device *dev,
                                    const uint8_t *buf, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        uart_poll_out(dev, buf[i]);
    }
}

static inline int uart_read_byte(const struct device *dev, uint8_t *ch)
{
    return uart_poll_in(dev, ch);
}

static void ready_isr(const struct device *dev,
                      struct gpio_callback *cb, uint32_t pins)
{
    ARG_UNUSED(dev); ARG_UNUSED(cb); ARG_UNUSED(pins);
    k_sem_give(&sem_ready);
}

static bool try_parse_loc(const uint8_t *buf, size_t len)
{
    for (size_t i = 0; i + 14 <= len; i++) {
        if (buf[i] == 0x41 && buf[i+1] == 0x0D) {
            const uint8_t *p = buf + i + 2;
            int32_t x = sys_get_le32(p);
            int32_t y = sys_get_le32(p + 4);
            int32_t z = sys_get_le32(p + 8);
            uint8_t qf = p[12];
            LOG_TS("UWB", "POS[%d,%d,%d]QF[%u%%]\n",
                   x, y, z, qf);
            return true;
        }
    }
    return false;
}

static void uwb_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

    uart1_dev = DEVICE_DT_GET(DT_NODELABEL(uart1));
    gpio1_dev = DEVICE_DT_GET(DT_NODELABEL(gpio1));
    if (!device_is_ready(uart1_dev) ||
        !device_is_ready(gpio1_dev)) {
        LOG_TS("UWB", "ERROR: UART1/GPIO1 not ready\n");
        return;
    }
    gpio_pin_configure(gpio1_dev, 3, GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_interrupt_configure(gpio1_dev, 3,
                                 GPIO_INT_EDGE_RISING);
    gpio_init_callback(&ready_cb, ready_isr, BIT(3));
    gpio_add_callback(gpio1_dev, &ready_cb);
    k_sem_init(&sem_ready, 0, 1);

    /* 唤醒 & 使能中断 */
    for (int i = 0; i < 3; i++) uart_poll_out(uart1_dev, WAKE_BYTE);
    uart_write_block(uart1_dev, TLV_INT_EN, sizeof(TLV_INT_EN));
    k_sleep(K_MSEC(10));
    LOG_TS("UWB", "thread ready\n");

    while (1) {
        k_sem_take(&sem_ready, K_FOREVER);
        uart_write_block(uart1_dev, CMD_LOC_GET,
                         sizeof(CMD_LOC_GET));

        uint8_t buf[BUF_MAX]; size_t idx = 0; uint8_t ch;
        int64_t dl = k_uptime_get() + 50;
        while (k_uptime_get() < dl && idx < BUF_MAX) {
            if (uart_read_byte(uart1_dev, &ch) == 0) {
                buf[idx++] = ch;
            } else {
                k_sleep(K_USEC(50));
            }
        }
        if (!idx || !try_parse_loc(buf, idx)) {
            LOG_TS("UWB", "(No POS TLV)\n");
            
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

#define SAMPLING_RATE_HZ     250
#define SAMPLING_INTERVAL_US (1000000U / SAMPLING_RATE_HZ)

static void ecg_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

    if (!device_is_ready(gpio1_ecg)) {
        LOG_TS("ECG", "ERROR: gpio1 not ready\n");
        return;
    }
    gpio_pin_configure(gpio1_ecg, 4,
                       GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_configure(gpio1_ecg, 5,
                       GPIO_INPUT | GPIO_PULL_UP);

    if (!adc_is_ready_dt(&adc)) {
        LOG_TS("ECG", "ERROR: ADC %s not ready\n", adc.dev->name);
        return;
    }
    if (adc_channel_setup_dt(&adc) < 0) {
        LOG_TS("ECG", "ERROR: ADC setup failed\n");
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

    static struct k_poll_signal sig;
    k_poll_signal_init(&sig);
    struct k_poll_event evt =
        K_POLL_EVENT_INITIALIZER(
            K_POLL_TYPE_SIGNAL,
            K_POLL_MODE_NOTIFY_ONLY,
            &sig);

    int print_cnt = 0;
    while (1) {
        bool pole_off =
            gpio_pin_get(gpio1_ecg,4) &&
            gpio_pin_get(gpio1_ecg,5);
        if (pole_off) {
            LOG_TS("ECG", "POLE OFF\n");
            k_busy_wait(SAMPLING_INTERVAL_US);
            continue;
        }

        int err = adc_read_async(adc.dev, &seq, &sig);
        if (err < 0) {
            LOG_TS("ECG", "ERROR: ADC async start failed (%d)\n", err);
            return;
        }
        k_poll(&evt, 1, K_FOREVER);
        unsigned int ev; int r;
        k_poll_signal_check(&sig, &ev, &r);

        if (++print_cnt >= 1) {  /* 250Hz/25=10Hz */
            print_cnt = 0;
            LOG_TS("ECG","[%d]\n", sample);
        }
        k_sleep(K_USEC(SAMPLING_INTERVAL_US));
    }
}
K_THREAD_DEFINE(ecg_tid, 1024, ecg_thread,
                NULL, NULL, NULL, 7, 0, 0);


/* ---------- 主函数 ---------- */
int main(void)
{
    /* 延迟 500ms 等底层驱动 probe 完毕 */
    k_sleep(K_MSEC(500));
    LOG_TS("SYS", "init complete, starting IMU/UWB/ECG\n");
    imu_init();

    while (1) {
        k_sleep(K_SECONDS(1));
    }
    return 0;
}
