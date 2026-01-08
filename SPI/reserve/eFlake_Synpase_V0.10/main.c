/*
 * Sensor-Hub TX  ——  IMU(JY901B) + ECG + UWB → BLE NUS
 * nRF Connect SDK v2.8  / Zephyr 3.7.99
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/settings/settings.h>
#include <math.h>
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/gatt_dm.h>
#include <bluetooth/services/nus.h>
#include <dk_buttons_and_leds.h>

/* ───────── 线程优先级 ───────── */
#define BLE_PRIO   3
#define IMU_PRIO   4
#define UWB_PRIO   5
#define ECG_PRIO   6

/* ───────── BLE FIFO 设置 ───────── */
#define BLE_STACK_SIZE 1024
#define BLE_BUF_SIZE   256
#define BLE_FIFO_MAX   32

struct ble_item_t {
    void *fifo_reserved;
    char  data[BLE_BUF_SIZE];
};

static struct bt_conn         *current_conn;
static struct bt_gatt_exchange_params exch_mtu_params;
static K_FIFO_DEFINE(fifo_ble);
static atomic_t                fifo_cnt = ATOMIC_INIT(0);
static K_SEM_DEFINE(ble_ready, 0, 1);

/* ───────── 时间戳工具 ───────── */
static void timestamp_now(char *buf, size_t len)
{
    uint64_t us = k_ticks_to_us_floor64(k_uptime_ticks());
    uint32_t us_part = us % 1000000ULL;
    uint32_t sec_tot = us / 1000000ULL;
    snprintk(buf, len, "%02u:%02u:%02u.%03u%03u",
             (sec_tot / 3600U) % 24U,
             (sec_tot / 60U)   % 60U,
              sec_tot          % 60U,
              us_part / 1000U,
              us_part % 1000U);
}

/* ───────── SEND 宏：将数据推入 FIFO ───────── */
#define SEND(tag, fmt, ...)                                                 \
    do {                                                                    \
        while (atomic_get(&fifo_cnt) >= BLE_FIFO_MAX)                       \
            k_sleep(K_MSEC(5));                                            \
        struct ble_item_t *it = k_malloc(sizeof(*it));                      \
        if (!it) break;                                                     \
        char _ts[20]; timestamp_now(_ts, sizeof(_ts));                     \
        snprintk(it->data, BLE_BUF_SIZE, "[%s][%s] " fmt "\r\n",            \
                 _ts, tag, ##__VA_ARGS__);                                 \
        k_fifo_put(&fifo_ble, it);                                          \
        atomic_inc(&fifo_cnt);                                              \
    } while (0)

/* ───────── BLE 回调 ───────── */
static void exchange_mtu_cb(struct bt_conn *conn,
                            uint8_t err,
                            struct bt_gatt_exchange_params *params)
{
    if (!err) {
        printk("MTU exchanged, new MTU: %d\n", bt_gatt_get_mtu(conn));
    } else {
        printk("MTU exchange failed (%u)\n", err);
    }
}

static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    if (err) {
        printk("Connection failed (0x%02x)\n", err);
        return;
    }
    current_conn = bt_conn_ref(conn);
    dk_set_led_on(DK_LED2);

    /* 发起 MTU 交换 */
    exch_mtu_params.func = exchange_mtu_cb;
    bt_gatt_exchange_mtu(current_conn, &exch_mtu_params);
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    ARG_UNUSED(reason);
    if (current_conn) {
        bt_conn_unref(current_conn);
        current_conn = NULL;
    }
    dk_set_led_off(DK_LED2);
}
BT_CONN_CB_DEFINE(conn_cb) = {
    .connected    = connected_cb,
    .disconnected = disconnected_cb,
};

/* ───────── BLE 初始化 & 发送线程 ───────── */
static int ble_init(void)
{
    int err = bt_enable(NULL);
    if (err) {
        printk("bt_enable failed (%d)\n", err);
        return err;
    }

    settings_load();
    bt_nus_init(NULL);

    /* 启动可连接广播 */
    const struct bt_data ad[] = {
        BT_DATA_BYTES(BT_DATA_FLAGS,
                      (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
        BT_DATA(BT_DATA_NAME_COMPLETE,
                "Sensor-Hub", sizeof("Sensor-Hub") - 1),
    };
    const struct bt_data sd[] = {
        BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_NUS_VAL),
    };
    err = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad),
                          sd, ARRAY_SIZE(sd));
    if (err) {
        printk("adv start failed (%d)\n", err);
        return err;
    }

    dk_leds_init();
    dk_set_led_on(DK_LED1);
    k_sem_give(&ble_ready);
    return 0;
}

static void ble_tx_thread(void)
{
    k_sem_take(&ble_ready, K_FOREVER);

    while (1) {
        struct ble_item_t *it = k_fifo_get(&fifo_ble, K_FOREVER);
        atomic_dec(&fifo_cnt);

        if (!current_conn) {
            k_free(it);
            continue;
        }

        int ret = bt_nus_send(current_conn,
                              (uint8_t *)it->data,
                              strlen(it->data));
        k_free(it);
        (void)ret;
    }
}
K_THREAD_DEFINE(ble_tx_tid, BLE_STACK_SIZE,
                ble_tx_thread, NULL, NULL, NULL,
                BLE_PRIO, 0, 0);


/* =================================================
 *                IMU —— JY901B (I²C)
 * =================================================*/
#define I2C_NODE   DT_NODELABEL(i2c0)
#define IMU_ADDR   0x50
#define REG_START  0x34
#define REG_LEN    18

static const struct device *const i2c_dev = DEVICE_DT_GET(I2C_NODE);

static inline int16_t le16(const uint8_t *p)
{
    return (int16_t)((p[1] << 8) | p[0]);
}

static void imu_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!device_is_ready(i2c_dev)) {
        printk("I2C not ready\n"); return;
    }
    /* 配置 200Hz */
    uint8_t cmd1[3] = {0x69, 0x88, 0xB5};
    i2c_write(i2c_dev, cmd1, 3, IMU_ADDR);
    uint8_t cmd2[3] = {0x03, 0x08, 0};
    i2c_write(i2c_dev, cmd2, 3, IMU_ADDR);

    uint8_t reg = REG_START, buf[REG_LEN];
    uint8_t cnt = 0;

    while (1) {
        if (i2c_write_read(i2c_dev, IMU_ADDR, &reg, 1, buf, REG_LEN) == 0) {
            float ax = le16(&buf[0])  / 32768.f * 16.f * 9.8f;
            float ay = le16(&buf[2])  / 32768.f * 16.f * 9.8f;
            float az = le16(&buf[4])  / 32768.f * 16.f * 9.8f;
            float gx = le16(&buf[6])  / 32768.f * 2000.f;
            float gy = le16(&buf[8])  / 32768.f * 2000.f;
            float gz = le16(&buf[10]) / 32768.f * 2000.f;
            float mx = le16(&buf[12]) / 32768.f * 1000.f;
            float my = le16(&buf[14]) / 32768.f * 1000.f;
            float mz = le16(&buf[16]) / 32768.f * 1000.f;

            if (++cnt >= 4) {  /* 200Hz→20Hz */
                cnt = 0;
                SEND("IMU",
                    "[%+6.2f,%+6.2f,%+6.2f,"
                    "%+6.2f,%+6.2f,%+6.2f,"
                    "%+6.1f,%+6.1f,%+6.1f]",
                    (double)ax,(double)ay,(double)az, 
                    (double)gx,(double)gy,(double)gz, 
                    (double)mx,(double)my,(double)mz);
            }
        }
        k_sleep(K_MSEC(5));
    }
}
K_THREAD_DEFINE(imu_tid, 1024,
                imu_thread, NULL, NULL, NULL,
                IMU_PRIO, 0, 0);


/* =================================================
 *                UWB 线程（DWM1001C 模块）
 * =================================================*/
#define WAKE_BYTE     0x00
static const uint8_t CMD_LOC_GET[2] = {0x0C, 0x00};
static const uint8_t TLV_INT_EN[4]  = {0x36, 0x02, 0x01, 0x00};
#define UBUF_MAX      128

static const struct device *uart1_dev = DEVICE_DT_GET(DT_NODELABEL(uart1));
static const struct device *gpio1_dev = DEVICE_DT_GET(DT_NODELABEL(gpio1));
static struct gpio_callback   uwb_cb;
static struct k_sem           sem_uwb;

static void gpio_int(const struct device *d,
                     struct gpio_callback *cb, uint32_t pins)
{
    ARG_UNUSED(d); ARG_UNUSED(cb); ARG_UNUSED(pins);
    k_sem_give(&sem_uwb);
    
}

static void uwb_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!device_is_ready(uart1_dev) || !device_is_ready(gpio1_dev)) {
        printk("UWB hw not ready\n"); return;
    }

    gpio_pin_configure(gpio1_dev, 3, GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_interrupt_configure(gpio1_dev, 3,
                                 GPIO_INT_EDGE_RISING);
    gpio_init_callback(&uwb_cb, gpio_int, BIT(3));
    gpio_add_callback(gpio1_dev, &uwb_cb);
    k_sem_init(&sem_uwb, 0, 1);

    for (int i = 0; i < 3; i++) {
        uart_poll_out(uart1_dev, WAKE_BYTE);
    }
    for (size_t i = 0; i < ARRAY_SIZE(TLV_INT_EN); i++) {
        uart_poll_out(uart1_dev, TLV_INT_EN[i]);
    }
    k_sleep(K_MSEC(10));
    printk("UWB ready\n");

    uint8_t buf[UBUF_MAX];
    while (1) {
        k_sem_take(&sem_uwb, K_FOREVER);
        uart_poll_out(uart1_dev, CMD_LOC_GET[0]);
        uart_poll_out(uart1_dev, CMD_LOC_GET[1]);
        size_t n = 0; uint8_t ch;
        int64_t deadline = k_uptime_get() + 60;
        while (k_uptime_get() < deadline && n < UBUF_MAX) {
            if (uart_poll_in(uart1_dev, &ch) == 0) {
                buf[n++] = ch;
            }
        }
        for (size_t i = 0; i + 14 <= n; i++) {
            if (buf[i] == 0x41 && buf[i+1] == 0x0D) {
                const uint8_t *p = &buf[i+2];
                int32_t x = sys_get_le32(p);
                int32_t y = sys_get_le32(p+4);
                int32_t z = sys_get_le32(p+8);
                uint8_t q = p[12];
                SEND("UWB", "[%d,%d,%d,%u]", x, y, z, q);
                break;
            }
        }
    }
}
K_THREAD_DEFINE(uwb_tid, 1024,
                uwb_thread, NULL, NULL, NULL,
                UWB_PRIO, 0, 0);


/* =================================================
 *                ECG 线程 — SAADC
 * =================================================*/
static const struct adc_dt_spec adc_spec =
    ADC_DT_SPEC_GET(DT_PATH(zephyr_user));
static const struct device *gpio_ecg_dev =
    DEVICE_DT_GET(DT_NODELABEL(gpio1));

#define ECG_RATE_HZ 300
#define ECG_INT_US  (1000000 / ECG_RATE_HZ)
#define ECG_PKT_N   10

static void ecg_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!device_is_ready(gpio_ecg_dev)) {
        printk("ECG GPIO not ready\n"); return;
    }
    gpio_pin_configure(gpio_ecg_dev, 10, GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_configure(gpio_ecg_dev, 11, GPIO_INPUT | GPIO_PULL_UP);

    if (!adc_is_ready_dt(&adc_spec)) {
        printk("ADC not ready\n"); return;
    }
    adc_channel_setup_dt(&adc_spec);

    static int16_t buf[ECG_PKT_N];
    int idx = 0;
    int16_t sample;
    struct adc_sequence seq = {
        .buffer = &sample,
        .buffer_size = sizeof(sample),
    };
    adc_sequence_init_dt(&adc_spec, &seq);

    while (1) {
        /* 电极脱落检测 */
        if (gpio_pin_get(gpio_ecg_dev, 10) &&
            gpio_pin_get(gpio_ecg_dev, 11)) {
            SEND("ECG", "Electrode off");
            idx = 0;
            k_sleep(K_MSEC(200));
            continue;
        }
        adc_read(adc_spec.dev, &seq);
        buf[idx++] = sample;
        if (idx >= ECG_PKT_N) {
            char line[96];
            int pos = snprintk(line, sizeof(line),
                               "[%d", buf[0]);
            for (int i = 1; i < ECG_PKT_N; i++) {
                pos += snprintk(line+pos,
                                sizeof(line)-pos,
                                ",%d", buf[i]);
            }
            snprintk(line+pos, sizeof(line)-pos, "]");
            SEND("ECG", "%s", line);
            idx = 0;
        }
        k_sleep(K_USEC(ECG_INT_US));
    }
}
K_THREAD_DEFINE(ecg_tid, 1024,
                ecg_thread, NULL, NULL, NULL,
                ECG_PRIO, 0, 0);


/* ───────── 主函数 ───────── */
int main(void)
{
    printk("\n=== Sensor-Hub TX boot ===\n");

    if (ble_init()) {
        return 0;
    }

    /* 其他线程已自动启动 */
    while (1) {
        /* LED1 闪烁表示系统运行 */
        dk_set_led_on(DK_LED1);
        k_sleep(K_MSEC(500));
        dk_set_led_off(DK_LED1);
        k_sleep(K_MSEC(500));
    }

    return 0;
}
