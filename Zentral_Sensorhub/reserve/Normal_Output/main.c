#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/sys_io.h>
#include <zephyr/settings/settings.h>
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/gatt_dm.h>
#include <bluetooth/services/nus.h>
#include <dk_buttons_and_leds.h>
#include <zephyr/irq.h>
#include <nrf.h>

/* ===================== 配置 ===================== */
#ifndef IRQ_PRIO_LOWEST
#define IRQ_PRIO_LOWEST 7
#endif

#define BMD_BAUD               57600
#define BMD_SEND_START_CMD     0

#define BLE_PRIO   3
#define IMU_PRIO   4
#define UWB_PRIO   3
#define ECG_PRIO   2

#define BLE_STACK_SIZE 1024
#define BLE_BUF_SIZE   256
#define BLE_FIFO_MAX   32

/* UWB INT 脚：P1.03 */
#define UWB_INT_PORT_DEV   DT_NODELABEL(gpio1)
#define UWB_INT_PIN        3

/* ===================== 工具/时间戳 ===================== */
static inline void timestamp_now(char *buf, size_t len)
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

/* ===================== 设备名 ===================== */
static char bt_name[8];
static void init_dev_name(void)
{
    uint32_t id0 = sys_read32(0x10000060);
    uint8_t b0 = (id0 >> 0) & 0xFF;
    uint8_t b1 = (id0 >> 8) & 0xFF;
    snprintk(bt_name, sizeof(bt_name), "eF%02X%02X", b0, b1);
}

/* ===================== BLE 发送（内存池） ===================== */
struct ble_item_t { void *fifo_reserved; char data[BLE_BUF_SIZE]; };
static struct bt_conn *current_conn;
static struct bt_gatt_exchange_params exch_mtu_params;
static K_FIFO_DEFINE(fifo_ble);
static atomic_t fifo_cnt = ATOMIC_INIT(0);
static K_SEM_DEFINE(ble_ready, 0, 1);

/* 固定内存池，避免频繁堆分配 */
K_MEM_SLAB_DEFINE(ble_slab, sizeof(struct ble_item_t), BLE_FIFO_MAX, 4);

/* 统一输出：不再向 RTT 再打印，避免“message dropped” */
#define SEND(tag, fmt, ...)                                                        \
    do {                                                                           \
        struct ble_item_t *it = NULL;                                              \
        if (k_mem_slab_alloc(&ble_slab, (void **)&it, K_MSEC(5)) != 0) {           \
            break;                                                                 \
        }                                                                          \
        char _ts[20]; timestamp_now(_ts, sizeof(_ts));                             \
        snprintk(it->data, BLE_BUF_SIZE, "[%s][%s][%s]" fmt "\r\n",                \
                 bt_name, _ts, tag, ##__VA_ARGS__);                                \
        k_fifo_put(&fifo_ble, it);                                                 \
        atomic_inc(&fifo_cnt);                                                     \
    } while (0)

/* ===================== BLE 基础 ===================== */
static void exchange_mtu_cb(struct bt_conn *conn, uint8_t err,
                            struct bt_gatt_exchange_params *params)
{
    if (!err) printk("MTU exchanged, new MTU: %d\n", bt_gatt_get_mtu(conn));
    else      printk("MTU exchange failed (%u)\n", err);
}
static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    if (err) { printk("Connection failed (0x%02x)\n", err); return; }
    current_conn = bt_conn_ref(conn);
    dk_set_led_on(DK_LED2);
    exch_mtu_params.func = exchange_mtu_cb;
    bt_gatt_exchange_mtu(current_conn, &exch_mtu_params);
    SEND("BLE", "[connected]");
}
static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    ARG_UNUSED(reason);
    if (current_conn) { bt_conn_unref(current_conn); current_conn = NULL; }
    dk_set_led_off(DK_LED2);
    SEND("BLE", "[disconnected 0x%02X]", reason);
}
BT_CONN_CB_DEFINE(conn_cb) = { .connected=connected_cb, .disconnected=disconnected_cb };

static int ble_init(void)
{
    int err = bt_enable(NULL);
    if (err) { printk("bt_enable failed (%d)\n", err); return err; }
    settings_load();
    bt_nus_init(NULL);
    const struct bt_data ad[] = {
        BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
        BT_DATA(BT_DATA_NAME_COMPLETE, bt_name, strlen(bt_name)),
    };
    const struct bt_data sd[] = { BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_NUS_VAL), };
    err = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
    if (err) { printk("adv start failed (%d)\n", err); return err; }
    dk_leds_init(); dk_set_led_on(DK_LED1);
    k_sem_give(&ble_ready);
    return 0;
}
static void ble_tx_thread(void)
{
    k_sem_take(&ble_ready, K_FOREVER);
    while (1) {
        struct ble_item_t *it = k_fifo_get(&fifo_ble, K_FOREVER);
        atomic_dec(&fifo_cnt);
        if (current_conn) (void)bt_nus_send(current_conn, (uint8_t *)it->data, strlen(it->data));
        k_mem_slab_free(&ble_slab, (void *)it);
    }
}
K_THREAD_DEFINE(ble_tx_tid, BLE_STACK_SIZE, ble_tx_thread, NULL,NULL,NULL, BLE_PRIO, 0, 0);

/* ===================== IMU（I2C0@0x50）→ BLE ===================== */
#define I2C_NODE   DT_NODELABEL(i2c0)
#define IMU_ADDR   0x50
#define REG_START  0x34
#define REG_LEN    18

/* 提前声明，避免隐式声明错误 */
static inline int16_t le16(const uint8_t *p){ return (int16_t)((p[1] << 8) | p[0]); }

/* I2C 设备提前放在全局，确保所有函数可见 */
static const struct device *const i2c_dev = DEVICE_DT_GET(I2C_NODE);

/* IMU 开机自检放行信号量：自检结束再启动 IMU 线程，避免 I2C 冲突 */
K_SEM_DEFINE(imu_start_sem, 0, 1);

/* ===================== UWB（UART1）→ BLE ===================== */
#define WAKE_BYTE              0x00
static const uint8_t CMD_LOC_GET[2] = {0x0C, 0x00};
static const uint8_t TLV_INT_EN[4]  = {0x34, 0x02, 0x01, 0x00};

#define UBUF_MAX               128
#define UWB_POLL_MS            200
#define UWB_READ_WINDOW_MS     10
#define UWB_WAKE_BYTES         8

static const struct device *uart1_dev = DEVICE_DT_GET(DT_NODELABEL(uart1));
static const struct device *gpio1_dev = DEVICE_DT_GET(UWB_INT_PORT_DEV);
static struct gpio_callback uwb_cb; static struct k_sem sem_uwb;

static inline void uwb_tx_bytes(const uint8_t *d, size_t n)
{ for (size_t i = 0; i < n; i++) uart_poll_out(uart1_dev, d[i]); }

static inline void uwb_drain_rx(void)
{ uint8_t ch; while (uart_poll_in(uart1_dev, &ch) == 0) {} }

static void gpio_int(const struct device *d, struct gpio_callback *cb, uint32_t pins)
{ ARG_UNUSED(d); ARG_UNUSED(cb); ARG_UNUSED(pins); k_sem_give(&sem_uwb); }

/* ===================== BMD101（UART0）→ BLE（BPM 1Hz） ===================== */
static const struct device *const ecg_uart = DEVICE_DT_GET(DT_NODELABEL(uart0));
K_MSGQ_DEFINE(ecg_rx_q, 1, 1024, 4);
static atomic_t ecg_rx_isr_cnt = ATOMIC_INIT(0);

/* ===================== IMU 开机自检（只打印一次） ===================== */
static int imu_post_once(void)
{
    if (!device_is_ready(i2c_dev)) {
        printk("[IMU][POST] I2C0 not ready\n");
        return -ENODEV;
    }

    /* 与线程一致的初始化 */
    uint8_t cmd1[3] = {0x69, 0x88, 0xB5};
    uint8_t cmd2[3] = {0x03, 0x08, 0x00};
    (void)i2c_write(i2c_dev, cmd1, sizeof(cmd1), IMU_ADDR);
    (void)i2c_write(i2c_dev, cmd2, sizeof(cmd2), IMU_ADDR);
    k_msleep(5);

    /* 读一次寄存器窗口 */
    uint8_t reg = REG_START, buf[REG_LEN] = {0};
    int ret = i2c_write_read(i2c_dev, IMU_ADDR, &reg, 1, buf, REG_LEN);
    if (ret) {
        printk("[IMU][POST] read fail, err=%d\n", ret);
        return ret;
    }

    bool all0 = true, allff = true;
    for (int i = 0; i < REG_LEN; ++i) { if (buf[i] != 0x00) all0 = false; if (buf[i] != 0xFF) allff = false; }
    if (all0 || allff) {
        printk("[IMU][POST] invalid frame (all %s)\n", all0 ? "0x00" : "0xFF");
        return -EIO;
    }

    float ax = le16(&buf[0])  / 32768.f * 16.f * 9.8f;
    float ay = le16(&buf[2])  / 32768.f * 16.f * 9.8f;
    float az = le16(&buf[4])  / 32768.f * 16.f * 9.8f;
    float gx = le16(&buf[6])  / 32768.f * 2000.f;
    float gy = le16(&buf[8])  / 32768.f * 2000.f;
    float gz = le16(&buf[10]) / 32768.f * 2000.f;

    printk("[IMU][POST] ax=%.3f ay=%.3f az=%.3f gx=%.2f gy=%.2f gz=%.2f\n",
           (double)ax, (double)ay, (double)az, (double)gx, (double)gy, (double)gz);
    return 0;
}

/* ===================== IMU 线程 ===================== */
static void imu_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

    /* 等待开机自检完成，避免与自检抢 I2C */
    k_sem_take(&imu_start_sem, K_FOREVER);

    if (!device_is_ready(i2c_dev)) { printk("I2C not ready\n"); return; }
    uint8_t cmd1[3] = {0x69, 0x88, 0xB5}; (void)i2c_write(i2c_dev, cmd1, 3, IMU_ADDR);
    uint8_t cmd2[3] = {0x03, 0x08, 0};    (void)i2c_write(i2c_dev, cmd2, 3, IMU_ADDR);

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

            /* === 100 Hz 输出到 BLE（SEND），不往 RTT 刷屏 === */
            int64_t now = k_uptime_get();
            if (now - last_out >= 10) {
                last_out = now;
                SEND("IMU", "[%.3f, %.3f, %.3f, %.2f, %.2f, %.2f]",
                     (double)ax,(double)ay,(double)az,
                     (double)gx,(double)gy,(double)gz);
            }
        }
        k_sleep(K_MSEC(2));
    }
}
K_THREAD_DEFINE(imu_tid, 1536, imu_thread, NULL,NULL,NULL, IMU_PRIO, 0, 0);

/* ===================== UWB 线程 ===================== */
static void uwb_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!device_is_ready(uart1_dev) || !device_is_ready(gpio1_dev)) {
        printk("UWB hw not ready\n"); return;
    }

    gpio_pin_configure(gpio1_dev, UWB_INT_PIN, GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_interrupt_configure(gpio1_dev, UWB_INT_PIN, GPIO_INT_EDGE_RISING);
    gpio_init_callback(&uwb_cb, gpio_int, BIT(UWB_INT_PIN)); gpio_add_callback(gpio1_dev, &uwb_cb);
    k_sem_init(&sem_uwb, 0, 1);

    /* 唤醒 + 允许上报，并清空残留 */
    for (int i = 0; i < UWB_WAKE_BYTES; i++) uart_poll_out(uart1_dev, WAKE_BYTE);
    uwb_tx_bytes(TLV_INT_EN, sizeof(TLV_INT_EN));
    k_msleep(10);
    uwb_drain_rx();
    printk("UWB ready\n");

    uint8_t buf[UBUF_MAX];
    int64_t last_poll = 0;

    while (1) {
        bool do_query = false;
        if (k_sem_take(&sem_uwb, K_MSEC(5)) == 0) do_query = true;
        int64_t now = k_uptime_get();
        if (now - last_poll >= UWB_POLL_MS) do_query = true;
        if (!do_query) continue;
        last_poll = now;

        uwb_tx_bytes(CMD_LOC_GET, sizeof(CMD_LOC_GET));

        size_t n = 0;
        int64_t deadline = k_uptime_get() + UWB_READ_WINDOW_MS;
        while (k_uptime_get() < deadline && n < UBUF_MAX) {
            uint8_t ch;
            if (uart_poll_in(uart1_dev, &ch) == 0) buf[n++] = ch;
        }

        if (n == 0) continue;

        bool parsed = false;
        for (size_t i = 0; i + 14 <= n; i++) {
            if (buf[i] == 0x41 && buf[i+1] == 0x0D) {
                const uint8_t *p = &buf[i+2];
                int32_t x = sys_get_le32(p);
                int32_t y = sys_get_le32(p+4);
                int32_t z = sys_get_le32(p+8);
                uint8_t q  = p[12];
                SEND("UWB", "[%d,%d,%d,%u]", x, y, z, q);
                parsed = true;
                break;
            }
        }
        if (!parsed) {
            /* 需要时可打开： */
            /* SEND("UWB", "[unparsed %u bytes]", (unsigned)n); */
        }
    }
}
K_THREAD_DEFINE(uwb_tid, 1536, uwb_thread, NULL,NULL,NULL, UWB_PRIO, 0, 0);

/* ===================== BMD101 UART0 ISR 与解析 ===================== */
static void ecg_uart_isr(const struct device *dev, void *user_data)
{
    ARG_UNUSED(user_data);
    if (!uart_irq_update(dev)) return;
    while (uart_irq_rx_ready(dev)) {
        uint8_t buf[32];
        int rd = uart_fifo_read(dev, buf, sizeof(buf));
        for (int i=0; i<rd; ++i) (void)k_msgq_put(&ecg_rx_q, &buf[i], K_NO_WAIT);
        if (rd>0) atomic_inc(&ecg_rx_isr_cnt);
    }
}
static int ecg_uart_init_hw(void)
{
    if (!device_is_ready(ecg_uart)) { printk("[BMD][INIT] uart0 not ready\n"); return -ENODEV; }
    struct uart_config want = {
        .baudrate  = BMD_BAUD,
        .parity    = UART_CFG_PARITY_NONE,
        .stop_bits = UART_CFG_STOP_BITS_1,
        .data_bits = UART_CFG_DATA_BITS_8,
        .flow_ctrl = UART_CFG_FLOW_CTRL_NONE,
    };
    int err = uart_configure(ecg_uart, &want);
    if (err) printk("[BMD][INIT] uart_configure err=%d (using DTS)\n", err);
    uart_irq_callback_user_data_set(ecg_uart, ecg_uart_isr, NULL);
    uart_irq_rx_enable(ecg_uart);
#ifdef NRF_UARTE0
    printk("[BMD][INIT] UARTE0 PSEL RXD=0x%08x TXD=0x%08x\n",
           (unsigned)NRF_UARTE0->PSEL.RXD, (unsigned)NRF_UARTE0->PSEL.TXD);
#endif
    printk("[BMD][INIT] UART0 ready: %d bps, RX=P1.08 TX=P0.06\n", (int)BMD_BAUD);
    return 0;
}

/* ThinkGear 解析（仅 BPM） */
static inline uint8_t tgpp_checksum(const uint8_t *p, uint8_t len)
{ uint32_t s=0; for (uint8_t i=0;i<len;i++) s+=p[i]; return (uint8_t)(0xFF - (s & 0xFF)); }
static atomic_t last_bpm_atomic = ATOMIC_INIT(-1);
static void tgpp_parse_bpm_only(const uint8_t *payload, uint8_t plen)
{
    int bpm = -1;
    for (uint8_t i=0;i<plen;){
        while (i<plen && payload[i]==0x55) i++;
        if (i>=plen) break;
        uint8_t code = payload[i++];
        if (code < 0x80){ if (i>=plen) break; uint8_t val = payload[i++]; if (code==0x03) bpm = val; }
        else { if (i>=plen) break; uint8_t vlen = payload[i++]; if (i+vlen>plen) break; i += vlen; }
    }
    if (bpm>=0) atomic_set(&last_bpm_atomic, bpm);
}

static void bmd101_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (ecg_uart_init_hw() != 0) return;

#if BMD_SEND_START_CMD
    static const uint8_t start_cmd[] = { /* 0xAA, ... */ };
    for (size_t i=0;i<ARRAY_SIZE(start_cmd);++i) uart_poll_out(ecg_uart, start_cmd[i]);
#endif

    enum { S_SYNC1, S_SYNC2, S_PLEN, S_PAYLOAD, S_CHK } st = S_SYNC1;
    uint8_t plen=0, got=0, payload[180];

    int64_t last_tick = k_uptime_get();
    while (1){
        uint8_t byte;
        if (k_msgq_get(&ecg_rx_q, &byte, K_MSEC(100)) == 0){
            switch (st){
                case S_SYNC1: st = (byte==0xAA)?S_SYNC2:S_SYNC1; break;
                case S_SYNC2: st = (byte==0xAA)?S_PLEN:S_SYNC1; break;
                case S_PLEN:
                    plen=byte; if (plen==0 || plen>sizeof(payload)) st=S_SYNC1; else { got=0; st=S_PAYLOAD; } break;
                case S_PAYLOAD:
                    payload[got++]=byte; if (got>=plen) st=S_CHK; break;
                case S_CHK: {
                    uint8_t expect=tgpp_checksum(payload, plen);
                    if (byte==expect) tgpp_parse_bpm_only(payload, plen);
                    st=S_SYNC1; break;
                }
            }
        }

        int64_t now = k_uptime_get();
        if (now - last_tick >= 1000){
            last_tick = now;
            int bpm = atomic_get(&last_bpm_atomic);
            if (bpm < 0) bpm = 0;
            SEND("BMD", "[%d]", bpm);
        }
    }
}
K_THREAD_DEFINE(bmd_tid, 2048, bmd101_thread, NULL,NULL,NULL, ECG_PRIO, 0, 0);

/* ===================== ECG RESET（P0.17 低有效） ===================== */
#if DT_NODE_HAS_STATUS(DT_NODELABEL(ecg_rst), okay)
#define ECG_RST_NODE DT_NODELABEL(ecg_rst)
static const struct gpio_dt_spec ecg_rst = GPIO_DT_SPEC_GET(ECG_RST_NODE, gpios);
#endif
static void bmd101_hw_reset(void)
{
#if DT_NODE_HAS_STATUS(ECG_RST_NODE, okay)
    if (!device_is_ready(ecg_rst.port)) { printk("[BMD][INIT] RESET gpio not ready\n"); return; }
    gpio_pin_configure_dt(&ecg_rst, GPIO_OUTPUT_ACTIVE);   /* active_low → 0 */
    k_msleep(20);
    gpio_pin_set_dt(&ecg_rst, 1);
    gpio_pin_configure_dt(&ecg_rst, GPIO_INPUT);
    printk("[BMD][INIT] RESET pulse done on %s p%u\n", ecg_rst.port->name, ecg_rst.pin);
#else
    printk("[BMD][INIT] RESET pin not defined in DT\n");
#endif
}

/* ===================== 主函数 ===================== */
int main(void)
{
    init_dev_name();
    printk("\n=== %s TX boot ===\n", bt_name);
    SEND("BOOT", "[app started]");

    bmd101_hw_reset();

    /* 开机 IMU 自检：只打印一次，不持续输出 */
    (void)imu_post_once();

    /* 放行 IMU 线程，避免与自检抢 I2C */
    k_sem_give(&imu_start_sem);

    if (ble_init()) return 0;

    while (1){
        dk_set_led_on(DK_LED1); k_sleep(K_MSEC(500));
        dk_set_led_off(DK_LED1); k_sleep(K_MSEC(500));
    }
}
