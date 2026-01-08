/*
 * final_main_noECG.c — eFxx BLE TX (IMU + UWB only, no ECG, no sleep)
 * nRF Connect SDK 2.8.0 / Zephyr 3.7.x
 *
 * Changes vs soft-sleep version:
 *  - Removed motion/still detection & low-rate mode
 *  - Always IMU ~200 Hz (2 ms loop)
 *  - UWB poll fixed at 200 ms
 *  - No 'S' sleep heartbeat frame
 */

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
#include <zephyr/settings/settings.h>
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/services/nus.h>
#include <dk_buttons_and_leds.h>
#include <math.h>

/* ========================= Global toggles ========================= */
#define DEBUG_LOG_ENABLE   1
#if DEBUG_LOG_ENABLE
  #define LOGF(...)  printk(__VA_ARGS__)
#else
  #define LOGF(...)  do {} while (0)
#endif

#define EN_BLE 1
#define EN_IMU 1
#define EN_UWB 1

/* ========================= RT settings ========================= */
#define BLE_PRIO   3
#define IMU_PRIO   4
#define UWB_PRIO   3

#define BLE_STACK_SIZE  1280
#define IMU_STACK_SIZE  1536
#define UWB_STACK_SIZE  1536

#define BLE_BUF_SIZE     128

static inline uint32_t ts_ms(void){ return (uint32_t)k_uptime_get_32(); }

/* ========================= LED GPIO ========================= */
#define LED3_PIN 15  /* P0.15 */
#define LED4_PIN 16  /* P0.16 */
static const struct device *const gpio0 = DEVICE_DT_GET(DT_NODELABEL(gpio0));

/* ========================= Device name / ID ========================= */
static char bt_name[8];
static uint16_t dev_id16 = 0;
static void init_dev_name_and_id(void)
{
    uint32_t id0 = sys_read32(0x10000060);
    uint8_t b0 = (id0 >> 0) & 0xFF;
    uint8_t b1 = (id0 >> 8) & 0xFF;
    dev_id16 = ((uint16_t)b1 << 8) | b0;
    snprintk(bt_name, sizeof(bt_name), "eF%02X%02X", b0, b1);
}

/* ========================= BLE TX path ========================= */
struct ble_item_t {
    void *fifo_reserved;
    uint16_t len;
    uint8_t  data[BLE_BUF_SIZE];
};

static struct bt_conn *current_conn;
static struct bt_gatt_exchange_params exch_mtu_params;
static K_FIFO_DEFINE(fifo_ble);
static K_SEM_DEFINE(ble_ready, 0, 1);

K_MEM_SLAB_DEFINE(ble_slab_norm, sizeof(struct ble_item_t), 160, 4);
static atomic_t ble_drops_norm = ATOMIC_INIT(0);

static inline bool enqueue_frame(const uint8_t *buf, uint16_t len)
{
    if (!EN_BLE || len == 0 || len > BLE_BUF_SIZE) return false;
    struct ble_item_t *it = NULL;
    if (k_mem_slab_alloc(&ble_slab_norm, (void **)&it, K_NO_WAIT) != 0) {
        atomic_inc(&ble_drops_norm);
        return false;
    }
    it->len = len;
    memcpy(it->data, buf, len);
    k_fifo_put(&fifo_ble, it);
    return true;
}

/* Frame header helpers */
static uint16_t seq_imu=0, seq_uwb=0;
static inline uint16_t next_seq(uint16_t *s){ uint16_t v=*s; *s=(uint16_t)(v+1); return v; }

/* ===== IMU batch frame 'I' with sr_hz + cnt ===== */
static void send_frame_imu_batch(const int16_t *samples_6axis, uint8_t n, uint16_t sr_hz)
{
    if (!EN_IMU || !EN_BLE || n==0) return;
    /* Header: 0xAA 'I' + seq2 + id2 + ts4 + sr2 + cnt1 = 13B */
    const uint16_t hdr = 13;
    const uint16_t one = 12; /* 6 axes * int16 */
    uint8_t nn = n;
    if ((hdr + nn*one) > BLE_BUF_SIZE) {
        nn = (uint8_t)((BLE_BUF_SIZE - hdr) / one);
        if (nn==0) return;
    }

    uint8_t f[BLE_BUF_SIZE]; uint16_t p=0;
    f[p++]=0xAA; f[p++]='I';
    sys_put_le16(next_seq(&seq_imu), &f[p]); p+=2;
    sys_put_le16(dev_id16, &f[p]); p+=2;
    sys_put_le32(ts_ms(), &f[p]); p+=4;
    sys_put_le16(sr_hz, &f[p]); p+=2;
    f[p++]=nn;

    const int16_t *s = samples_6axis;
    for (uint8_t i=0;i<nn;i++) {
        for (int k=0;k<6;k++) { sys_put_le16((uint16_t)s[k], &f[p]); p+=2; }
        s += 6;
    }
    enqueue_frame(f, p);
}

/* ========================= UWB frame 'U' — x,y,z(i32), q(u8) ========================= */
static void send_frame_uwb(int32_t x,int32_t y,int32_t z,uint8_t q)
{
    if (!EN_UWB || !EN_BLE) return;
    uint8_t f[BLE_BUF_SIZE]; uint16_t p=0;
    f[p++]=0xAA; f[p++]='U';
    sys_put_le16(next_seq(&seq_uwb), &f[p]); p+=2;
    sys_put_le16(dev_id16, &f[p]); p+=2;
    sys_put_le32(ts_ms(), &f[p]); p+=4;
    sys_put_le32(x,&f[p]); p+=4; sys_put_le32(y,&f[p]); p+=4; sys_put_le32(z,&f[p]); p+=4;
    f[p++]=q;
    enqueue_frame(f, p);
}

/* ========================= LED3 non-blocking flash ========================= */
static struct k_work_delayable led3_off_work;
static void led3_off_work_handler(struct k_work *work)
{
    ARG_UNUSED(work);
    if (device_is_ready(gpio0)) gpio_pin_set(gpio0, LED3_PIN, 0);
}
static inline void led3_flash_50ms_now(void)
{
    if (!device_is_ready(gpio0)) return;
    static bool inited=false;
    if (!inited) {
        gpio_pin_configure(gpio0, LED3_PIN, GPIO_OUTPUT_INACTIVE);
        k_work_init_delayable(&led3_off_work, led3_off_work_handler);
        inited = true;
    }
    gpio_pin_set(gpio0, LED3_PIN, 1);
    k_work_schedule(&led3_off_work, K_MSEC(50));
}

/* ========================= BLE base ========================= */
static void exchange_mtu_cb(struct bt_conn *conn, uint8_t err, struct bt_gatt_exchange_params *params)
{ if (!err) { LOGF("MTU=%d\n", bt_gatt_get_mtu(conn)); } }

static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    if (err) { LOGF("Conn fail 0x%02x\n", err); return; }
    current_conn = bt_conn_ref(conn);
    dk_set_led_on(DK_LED2);
    exch_mtu_params.func = exchange_mtu_cb;
    (void)bt_gatt_exchange_mtu(current_conn, &exch_mtu_params);

    /* 连接参数还是保持 50ms / latency 19 / timeout 10s */
    const struct bt_le_conn_param *param = BT_LE_CONN_PARAM(40, 40, 19, 1000);
    int perr = bt_conn_le_param_update(conn, param);
    if (perr) LOGF("conn param upd err=%d\n", perr);
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    ARG_UNUSED(reason);
    if (current_conn) { bt_conn_unref(current_conn); current_conn=NULL; }
    dk_set_led_off(DK_LED2);
}
BT_CONN_CB_DEFINE(conn_cb) = { .connected=connected_cb, .disconnected=disconnected_cb };

static int ble_init(void)
{
    if (!EN_BLE) { k_sem_give(&ble_ready); return 0; }
    int err = bt_enable(NULL);
    if (err) { LOGF("bt_enable %d\n", err); return err; }
    settings_load();
    bt_nus_init(NULL);
    bt_set_name(bt_name);  
    
    const struct bt_data ad[] = {
        BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
        BT_DATA(BT_DATA_NAME_COMPLETE, bt_name, strlen(bt_name)),
    };
    const struct bt_data sd[] = {
        BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_NUS_VAL),
    };

    err = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
    LOGF("BLE adv start ret=%d, name=%s\n", err, bt_name);
    if (err) { LOGF("adv start %d\n", err); return err; }

    dk_leds_init();
    dk_set_led_on(DK_LED1);
    k_sem_give(&ble_ready);
    return 0;
}

/* ========================= TX thread (retry on transient errors) ========================= */
static void ble_tx_thread(void)
{
    k_sem_take(&ble_ready, K_FOREVER);
    while (1) {
        struct ble_item_t *it = k_fifo_get(&fifo_ble, K_FOREVER);
        if (EN_BLE && current_conn) {
            int tries=0, err;
            do {
                err = bt_nus_send(current_conn, it->data, it->len);
                if (err==0) break;
                if (err==-ENOMEM || err==-EAGAIN) k_sleep(K_MSEC(2));
                else break;
            } while (++tries<3);
        }
        k_mem_slab_free(&ble_slab_norm, (void *)it);
    }
}
K_THREAD_DEFINE(ble_tx_tid, BLE_STACK_SIZE, ble_tx_thread, NULL, NULL, NULL, BLE_PRIO, 0, 0);

/* ========================= IMU (I2C) ========================= */
#define I2C_NODE   DT_NODELABEL(i2c0)
#define IMU_ADDR   0x50
#define REG_START  0x34
#define REG_LEN    18
static inline int16_t le16(const uint8_t *p){ return (int16_t)((p[1]<<8)|p[0]); }
static const struct device *const i2c_dev = DEVICE_DT_GET(I2C_NODE);
K_SEM_DEFINE(imu_start_sem, 0, 1);

static int imu_post_once(void)
{
    if (!EN_IMU) return 0;
    if (!device_is_ready(i2c_dev)) { LOGF("[IMU] I2C0 not ready\n"); return -ENODEV; }
    uint8_t cmd1[3]={0x69,0x88,0xB5}; uint8_t cmd2[3]={0x03,0x08,0x00};
    (void)i2c_write(i2c_dev, cmd1, 3, IMU_ADDR);
    (void)i2c_write(i2c_dev, cmd2, 3, IMU_ADDR);
    k_msleep(5);
    uint8_t reg=REG_START, buf[REG_LEN]={0};
    int ret=i2c_write_read(i2c_dev, IMU_ADDR, &reg, 1, buf, REG_LEN);
    if (ret) { LOGF("[IMU] POST read err=%d\n", ret); return ret; }
    LOGF("[IMU] POST OK\n");
    return 0;
}

static void imu_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!EN_IMU) return;
    k_sem_take(&imu_start_sem, K_FOREVER);
    if (!device_is_ready(i2c_dev)) { LOGF("[IMU] I2C not ready"); return; }

    uint8_t cmd1[3]={0x69,0x88,0xB5}; (void)i2c_write(i2c_dev, cmd1, 3, IMU_ADDR);
    uint8_t cmd2[3]={0x03,0x08,0x00}; (void)i2c_write(i2c_dev, cmd2, 3, IMU_ADDR);

    uint8_t reg=REG_START, buf[REG_LEN];

    /* Batch: 4 samples per packet (200 Hz → 50 pkt/s) */
    enum { IMU_SAMPLES_PER_PKT = 4 };
    int16_t batch[IMU_SAMPLES_PER_PKT * 6];
    uint8_t n = 0;
    uint32_t last_flush = ts_ms();

    while (1) {
        if (i2c_write_read(i2c_dev, IMU_ADDR, &reg, 1, buf, REG_LEN) == 0) {
            float ax=le16(&buf[0])/32768.f*16.f*9.80665f;
            float ay=le16(&buf[2])/32768.f*16.f*9.80665f;
            float az=le16(&buf[4])/32768.f*16.f*9.80665f;
            float gx=le16(&buf[6])/32768.f*2000.f;
            float gy=le16(&buf[8])/32768.f*2000.f;
            float gz=le16(&buf[10])/32768.f*2000.f;

            int16_t qax = (int16_t)lrintf((ax / 9.80665f) * 1000.0f);
            int16_t qay = (int16_t)lrintf((ay / 9.80665f) * 1000.0f);
            int16_t qaz = (int16_t)lrintf((az / 9.80665f) * 1000.0f);
            int16_t qgx = (int16_t)lrintf(gx * 10.0f);
            int16_t qgy = (int16_t)lrintf(gy * 10.0f);
            int16_t qgz = (int16_t)lrintf(gz * 10.0f);

            /* 打包发送，采样率固定 200 Hz */
            int16_t *slot = &batch[n*6];
            slot[0]=qax; slot[1]=qay; slot[2]=qaz;
            slot[3]=qgx; slot[4]=qgy; slot[5]=qgz;
            n++;

            uint32_t now2 = ts_ms();
            if (n >= IMU_SAMPLES_PER_PKT || (now2 - last_flush) >= 20U) {
                send_frame_imu_batch(batch, n, 200);
                n = 0;
                last_flush = now2;
            }
        }

        /* 固定 2 ms 周期 ≈ 200 Hz */
        k_sleep(K_MSEC(2));
    }
}
K_THREAD_DEFINE(imu_tid, IMU_STACK_SIZE, imu_thread, NULL, NULL, NULL, IMU_PRIO, 0, 0);

/* ========================= UWB (UART1) ========================= */
#define UWB_INT_PORT_DEV   DT_NODELABEL(gpio1)
#define UWB_INT_PIN        3
#define UBUF_MAX               128
#define UWB_POLL_MS            200
#define UWB_READ_WINDOW_MS     10
#define UWB_WAKE_BYTES         8
#define WAKE_BYTE              0x00

static const uint8_t CMD_LOC_GET[2] = {0x0C, 0x00};
static const uint8_t TLV_INT_EN[4]  = {0x34, 0x02, 0x01, 0x00};

static const struct device *uart1_dev = DEVICE_DT_GET(DT_NODELABEL(uart1));
static const struct device *gpio1_dev = DEVICE_DT_GET(UWB_INT_PORT_DEV);
static struct gpio_callback uwb_cb; static struct k_sem sem_uwb;

static inline void uwb_tx_bytes(const uint8_t *d, size_t n)
{ for(size_t i=0;i<n;i++) uart_poll_out(uart1_dev,d[i]); }

static inline void uwb_drain_rx(void){ uint8_t ch; while (uart_poll_in(uart1_dev,&ch)==0){} }
static void gpio_int(const struct device *d, struct gpio_callback *cb, uint32_t pins)
{ ARG_UNUSED(d); ARG_UNUSED(cb); ARG_UNUSED(pins); k_sem_give(&sem_uwb); }

static void uwb_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!EN_UWB) return;

    if (!device_is_ready(uart1_dev) || !device_is_ready(gpio1_dev)) {
        LOGF("UWB hw not ready\n");
        return;
    }

    gpio_pin_configure(gpio1_dev, UWB_INT_PIN, GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_interrupt_configure(gpio1_dev, UWB_INT_PIN, GPIO_INT_EDGE_RISING);
    gpio_init_callback(&uwb_cb, gpio_int, BIT(UWB_INT_PIN));
    gpio_add_callback(gpio1_dev, &uwb_cb);
    k_sem_init(&sem_uwb, 0, 1);

    for (int i=0;i<UWB_WAKE_BYTES;i++){ uart_poll_out(uart1_dev, WAKE_BYTE); }
    uwb_tx_bytes(TLV_INT_EN, sizeof(TLV_INT_EN));
    k_msleep(10);
    uwb_drain_rx();
    LOGF("UWB ready\n");

    uint8_t buf[UBUF_MAX];
    int64_t last_poll=0;

    while (1){
        bool do_query=false;
        if (k_sem_take(&sem_uwb, K_MSEC(5))==0) do_query=true;
        int64_t now=k_uptime_get();
        if (now - last_poll >= (int64_t)UWB_POLL_MS) do_query=true;
        if (!do_query) {
            continue;
        }
        last_poll=now;

        uwb_tx_bytes(CMD_LOC_GET, sizeof(CMD_LOC_GET));
        size_t n=0; int64_t deadline = k_uptime_get() + UWB_READ_WINDOW_MS;
        while (k_uptime_get() < deadline && n < UBUF_MAX){
            uint8_t ch; if (uart_poll_in(uart1_dev,&ch)==0) buf[n++]=ch;
        }
        if (n==0) continue;

        for (size_t i=0; i+14<=n; i++){
            if (buf[i]==0x41 && buf[i+1]==0x0D) {
                const uint8_t *p=&buf[i+2];
                int32_t x=sys_get_le32(p); int32_t y=sys_get_le32(p+4);
                int32_t z=sys_get_le32(p+8); uint8_t q=p[12];
                send_frame_uwb(x,y,z,q);
                led3_flash_50ms_now();
                break;
            }
        }
    }
}
K_THREAD_DEFINE(uwb_tid, UWB_STACK_SIZE, uwb_thread, NULL, NULL, NULL, UWB_PRIO, 0, 0);

/* ========================= Main ========================= */
int main(void)
{
    init_dev_name_and_id();
    LOGF("\n=== %s TX boot (IMU+UWB only, NO SLEEP) ===\n", bt_name);

    (void)imu_post_once();
    k_sem_give(&imu_start_sem);

    if (ble_init()) return 0;

    if (device_is_ready(gpio0)) {
        gpio_pin_configure(gpio0, LED3_PIN, GPIO_OUTPUT_INACTIVE);
        gpio_pin_configure(gpio0, LED4_PIN, GPIO_OUTPUT_INACTIVE);
    }

    while (1) {
        /* 主线程只当个“心跳灯”和统计输出 */
#if DEBUG_LOG_ENABLE
        static uint32_t last_print=0;
        if (ts_ms()-last_print>2000) {
            last_print=ts_ms();
            uint32_t drops_norm=(uint32_t)atomic_get(&ble_drops_norm);
            LOGF("[STAT] drop_norm=%u\n", drops_norm);
        }
#endif
        dk_set_led_on(DK_LED1);
        k_sleep(K_MSEC(200));
        dk_set_led_off(DK_LED1);
        k_sleep(K_MSEC(800));
    }
}
