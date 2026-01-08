/*
 * final_main.c — eFxx BLE TX (last consolidated version + LED3/LED4 BPM indicator)
 * nRF Connect SDK 2.8.0 / Zephyr 3.7.x
 *
 * Changes in this revision (anti-drop + non-blocking LEDs):
 *  - Dual-slab TX queue: high-priority lane for BPM('H') / SigQ('Q') to avoid starvation
 *  - enqueue_frame_ex(..., high_prio) with short wait for high-prio frames
 *  - ble_item_t carries 'src' to free slab correctly
 *  - send_frame_bpm() no longer sleeps; LED3 uses k_work_delayable for 50 ms flash
 *  - bt_nus_send() retry on -ENOMEM/-EAGAIN, optional debug logs
 *  - Added per-lane drop counters and periodic stats (behind DEBUG_LOG_ENABLE)
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
#include <zephyr/sys/sys_io.h>
#include <zephyr/settings/settings.h>
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/services/nus.h>
#include <dk_buttons_and_leds.h>
#include <math.h>

/* ========================= Global toggles ========================= */
#define DEBUG_LOG_ENABLE   0  /* 0=silent, 1=printk */
#if DEBUG_LOG_ENABLE
  #define LOGF(...)  printk(__VA_ARGS__)
#else
  #define LOGF(...)  do {} while (0)
#endif

#define EN_BLE             1
#define EN_IMU             1
#define EN_UWB             1
#define EN_ECG_RAW         1
#define EN_ECG_BPM         1
#define EN_ECG_SIGQ        1
#define BMD_OPEN_RAW_ON_BOOT 1
#define FORCE_Q_TEST   0

/* ========================= RT settings ========================= */
#define BLE_PRIO   3
#define IMU_PRIO   4
#define UWB_PRIO   3
#define ECG_PRIO   2

#define BLE_STACK_SIZE  1280
#define IMU_STACK_SIZE  1536
#define UWB_STACK_SIZE  1536
#define ECG_STACK_SIZE  2304

/* BLE send buffer & FIFO depth (tune to RAM budget) */
#define BLE_BUF_SIZE     128
#ifndef BLE_FIFO_MAX
#define BLE_FIFO_MAX     160
#endif

/* Helpers */
static inline uint32_t ts_ms(void){ return (uint32_t)k_uptime_get_32(); }

/* ========================= NEW: LED GPIO (hardcoded, no DTS changes) ========================= */
#define LED3_PIN 15  /* P0.15 */
#define LED4_PIN 16  /* P0.16 */
static const struct device *const gpio0 = DEVICE_DT_GET(DT_NODELABEL(gpio0));
static atomic_t bpm_latest = ATOMIC_INIT(0);

/* ========================= Device name / ID ========================= */
static char bt_name[8];
static uint16_t dev_id16 = 0;
static void init_dev_name_and_id(void)
{
    uint32_t id0 = sys_read32(0x10000060);
    uint8_t b0 = (id0 >> 0) & 0xFF;
    uint8_t b1 = (id0 >> 8) & 0xFF;
    dev_id16 = ((uint16_t)b1 << 8) | b0;  /* eF%02X%02X mapping */
    snprintk(bt_name, sizeof(bt_name), "eF%02X%02X", b0, b1);
}

/* ========================= BLE TX path =========================
 * Dual-slab: high-priority for control/status (H/Q), normal for data (I/U/R).
 * A single FIFO carries mixed items; each item remembers its source slab.
 */
enum ble_src_lane { SRC_NORM = 0, SRC_HI = 1 };

struct ble_item_t {
    void *fifo_reserved;
    uint16_t len;
    uint8_t  src;                 /* 0=norm, 1=hi */
    uint8_t  data[BLE_BUF_SIZE];
};

static struct bt_conn *current_conn;
static struct bt_gatt_exchange_params exch_mtu_params;
static K_FIFO_DEFINE(fifo_ble);
static K_SEM_DEFINE(ble_ready, 0, 1);

/* Two slabs: most frames use norm; BPM/SigQ use hi. Tune counts per RAM. */
K_MEM_SLAB_DEFINE(ble_slab_norm, sizeof(struct ble_item_t), 144, 4);
K_MEM_SLAB_DEFINE(ble_slab_hi,   sizeof(struct ble_item_t),  16,  4);

static atomic_t ble_drops_norm = ATOMIC_INIT(0);
static atomic_t ble_drops_hi   = ATOMIC_INIT(0);

/* Forward decls */
static inline bool enqueue_frame_ex(const uint8_t *buf, uint16_t len, bool high_prio);

/* Frame header helpers */
static uint16_t seq_imu=0, seq_uwb=0, seq_bpm=0, seq_sigq=0, seq_raw=0;
static inline uint16_t next_seq(uint16_t *s){ uint16_t v=*s; *s=(uint16_t)(v+1); return v; }

/* ===== Enqueue helpers ===== */
static inline bool enqueue_frame_ex(const uint8_t *buf, uint16_t len, bool high_prio)
{
    if (!EN_BLE || len == 0 || len > BLE_BUF_SIZE) return false;

    struct ble_item_t *it = NULL;
    int ret;
    if (high_prio) {
        /* High priority lane allows a short wait to avoid drop */
        ret = k_mem_slab_alloc(&ble_slab_hi, (void **)&it, K_MSEC(5));
        if (ret != 0) {
            atomic_inc(&ble_drops_hi);
            return false;
        }
        it->src = SRC_HI;
    } else {
        /* Normal lane: best-effort, no wait to keep latency bounded */
        ret = k_mem_slab_alloc(&ble_slab_norm, (void **)&it, K_NO_WAIT);
        if (ret != 0) {
            atomic_inc(&ble_drops_norm);
            return false;
        }
        it->src = SRC_NORM;
    }

    it->len = len;
    memcpy(it->data, buf, len);
    k_fifo_put(&fifo_ble, it);
    return true;
}

static inline void enqueue_frame_norm(const uint8_t *buf, uint16_t len)
{ (void)enqueue_frame_ex(buf, len, false); }

static inline void enqueue_frame_hi(const uint8_t *buf, uint16_t len)
{ (void)enqueue_frame_ex(buf, len, true); }

/* ========================= IMU frame 'I' — ax,ay,az / gx,gy,gz ========================= */
static void send_frame_imu(float ax_ms2,float ay_ms2,float az_ms2,
                           float gx_dps,float gy_dps,float gz_dps)
{
    if (!EN_IMU || !EN_BLE) return;
    int16_t ax = (int16_t)lrintf((ax_ms2 / 9.80665f) * 1000.0f);
    int16_t ay = (int16_t)lrintf((ay_ms2 / 9.80665f) * 1000.0f);
    int16_t az = (int16_t)lrintf((az_ms2 / 9.80665f) * 1000.0f);
    int16_t gx = (int16_t)lrintf(gx_dps * 10.0f);
    int16_t gy = (int16_t)lrintf(gy_dps * 10.0f);
    int16_t gz = (int16_t)lrintf(gz_dps * 10.0f);

    uint8_t f[BLE_BUF_SIZE]; uint16_t p=0;
    f[p++]=0xAA; f[p++]='I';
    sys_put_le16(next_seq(&seq_imu), &f[p]); p+=2;
    sys_put_le16(dev_id16, &f[p]); p+=2;
    sys_put_le32(ts_ms(), &f[p]); p+=4;
    sys_put_le16(ax,&f[p]); p+=2; sys_put_le16(ay,&f[p]); p+=2; sys_put_le16(az,&f[p]); p+=2;
    sys_put_le16(gx,&f[p]); p+=2; sys_put_le16(gy,&f[p]); p+=2; sys_put_le16(gz,&f[p]); p+=2;
    enqueue_frame_norm(f, p);
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
    enqueue_frame_norm(f, p);
}

/* ========================= LED3 non-blocking flash ========================= */
static struct k_work_delayable led3_off_work;

static void led3_off_work_handler(struct k_work *work)
{
    ARG_UNUSED(work);
    if (device_is_ready(gpio0)) {
        gpio_pin_set(gpio0, LED3_PIN, 0);
    }
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
    /* schedule off after 50 ms */
    k_work_schedule(&led3_off_work, K_MSEC(50));
}

/* ========================= BPM 'H' (high priority) ========================= */
static void send_frame_bpm(uint8_t bpm)
{
    if (!EN_ECG_BPM || !EN_BLE) return;

    /* LED3 flash non-blocking; LED4 blink thread uses bpm_latest */
    led3_flash_50ms_now();
    atomic_set(&bpm_latest, bpm);

    uint8_t f[BLE_BUF_SIZE]; uint16_t p=0;
    f[p++]=0xAA; f[p++]='H';
    sys_put_le16(next_seq(&seq_bpm), &f[p]); p+=2;
    sys_put_le16(dev_id16, &f[p]); p+=2;
    sys_put_le32(ts_ms(), &f[p]); p+=4;
    f[p++]=bpm;
    enqueue_frame_hi(f, p);
}

/* ========================= SigQ 'Q' (high priority) ========================= */
static void send_frame_sigq(uint8_t q)
{
    if (!EN_ECG_SIGQ || !EN_BLE) return;
    uint8_t f[BLE_BUF_SIZE]; uint16_t p=0;
    f[p++]=0xAA; f[p++]='Q';
    sys_put_le16(next_seq(&seq_sigq), &f[p]); p+=2;
    sys_put_le16(dev_id16, &f[p]); p+=2;
    sys_put_le32(ts_ms(), &f[p]); p+=4;
    f[p++]=q;
    enqueue_frame_hi(f, p);
}

/* ========================= RAW batch 'R' ========================= */
static void send_frame_raw_batch(const int16_t *samples, uint8_t n, uint16_t sr_hz)
{
    if (!EN_ECG_RAW || !EN_BLE || n==0) return;
    const uint16_t hdr = 13; /* 0xAA + 'R' + seq2 + id2 + t4 + sr2 + cnt1 */
    const uint16_t max_payload = BLE_BUF_SIZE - hdr;
    const uint8_t max_n = (uint8_t)(max_payload / 2U);

    if (n > max_n) {
        /* Prevent overflow: split recursively into chunks */
        uint8_t remain=n; const int16_t *pS=samples;
        while (remain) {
            uint8_t take = (remain>max_n)?max_n:remain;
            send_frame_raw_batch(pS, take, sr_hz);
            pS += take; remain -= take;
        }
        return;
    }

    uint8_t f[BLE_BUF_SIZE]; uint16_t p=0;
    f[p++]=0xAA; f[p++]='R';
    sys_put_le16(next_seq(&seq_raw), &f[p]); p+=2;
    sys_put_le16(dev_id16, &f[p]); p+=2;
    sys_put_le32(ts_ms(), &f[p]); p+=4;
    sys_put_le16(sr_hz, &f[p]); p+=2;
    f[p++]=n;
    for (uint8_t i=0;i<n;i++){ sys_put_le16((uint16_t)samples[i], &f[p]); p+=2; }
    enqueue_frame_norm(f, p);
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

    const struct bt_data ad[] = {
        BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
        BT_DATA(BT_DATA_NAME_COMPLETE, bt_name, strlen(bt_name)),
    };
    const struct bt_data sd[] = {
        BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_NUS_VAL),
    };

    err = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
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
            int tries = 0;
            int err;
            do {
                err = bt_nus_send(current_conn, it->data, it->len);
                if (err == 0) break;

#if DEBUG_LOG_ENABLE
                LOGF("[BLE TX] nus_send err=%d (len=%u, src=%u)\n", err, it->len, it->src);
#endif
                if (err == -ENOMEM || err == -EAGAIN) {
                    /* ATT TX buffer full; small backoff then retry */
                    k_sleep(K_MSEC(2));
                } else {
                    /* Permanent error, drop */
                    break;
                }
            } while (++tries < 3);
        }

        /* Return to the correct slab based on src */
        if (it->src == SRC_HI) {
            k_mem_slab_free(&ble_slab_hi, (void *)it);
        } else {
            k_mem_slab_free(&ble_slab_norm, (void *)it);
        }
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

    bool all0=true, allff=true; for (int i=0;i<REG_LEN;i++){ all0&=(buf[i]==0); allff&=(buf[i]==0xFF); }
    if (all0||allff){ LOGF("[IMU] invalid frame\n"); return -EIO; }

    float ax=le16(&buf[0])/32768.f*16.f*9.8f;
    float ay=le16(&buf[2])/32768.f*16.f*9.8f;
    float az=le16(&buf[4])/32768.f*16.f*9.8f;
    float gx=le16(&buf[6])/32768.f*2000.f;
    float gy=le16(&buf[8])/32768.f*2000.f;
    float gz=le16(&buf[10])/32768.f*2000.f;
    LOGF("[IMU] ax=%.2f ay=%.2f az=%.2f gx=%.1f gy=%.1f gz=%.1f\n",
         (double)ax,(double)ay,(double)az,(double)gx,(double)gy,(double)gz);
    return 0;
}

static void imu_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!EN_IMU) return;

    k_sem_take(&imu_start_sem, K_FOREVER);
    if (!device_is_ready(i2c_dev)) { LOGF("[IMU] I2C not ready\n"); return; }

    uint8_t cmd1[3]={0x69,0x88,0xB5}; (void)i2c_write(i2c_dev, cmd1, 3, IMU_ADDR);
    uint8_t cmd2[3]={0x03,0x08,0x00}; (void)i2c_write(i2c_dev, cmd2, 3, IMU_ADDR);

    uint8_t reg=REG_START, buf[REG_LEN];
    int64_t last_out=0;

    while (1) {
        if (i2c_write_read(i2c_dev, IMU_ADDR, &reg, 1, buf, REG_LEN) == 0) {
            float ax=le16(&buf[0])/32768.f*16.f*9.8f;
            float ay=le16(&buf[2])/32768.f*16.f*9.8f;
            float az=le16(&buf[4])/32768.f*16.f*9.8f;
            float gx=le16(&buf[6])/32768.f*2000.f;
            float gy=le16(&buf[8])/32768.f*2000.f;
            float gz=le16(&buf[10])/32768.f*2000.f;
            int64_t now=k_uptime_get();
            if (now - last_out >= 5) { last_out = now; send_frame_imu(ax,ay,az,gx,gy,gz); }
        }
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

    if (!device_is_ready(uart1_dev) || !device_is_ready(gpio1_dev)) { LOGF("UWB hw not ready\n"); return; }

    gpio_pin_configure(gpio1_dev, UWB_INT_PIN, GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_interrupt_configure(gpio1_dev, UWB_INT_PIN, GPIO_INT_EDGE_RISING);
    gpio_init_callback(&uwb_cb, gpio_int, BIT(UWB_INT_PIN));
    gpio_add_callback(gpio1_dev, &uwb_cb);
    k_sem_init(&sem_uwb, 0, 1);

    for (int i=0;i<UWB_WAKE_BYTES;i++){ uart_poll_out(uart1_dev, WAKE_BYTE); }
    uwb_tx_bytes(TLV_INT_EN, sizeof(TLV_INT_EN));
    k_msleep(10); uwb_drain_rx(); LOGF("UWB ready\n");

    uint8_t buf[UBUF_MAX]; int64_t last_poll=0;

    while (1){
        bool do_query=false;
        if (k_sem_take(&sem_uwb, K_MSEC(5))==0) do_query=true;
        int64_t now=k_uptime_get(); if (now - last_poll >= UWB_POLL_MS) do_query=true;
        if (!do_query) continue; last_poll=now;

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
                break;
            }
        }
    }
}
K_THREAD_DEFINE(uwb_tid, UWB_STACK_SIZE, uwb_thread, NULL, NULL, NULL, UWB_PRIO, 0, 0);

/* ========================= ECG (BMD101, UART0) ========================= */
#define RAW_BATCH_N            30   /* BLE frame packing count */
#define RAW_SAMPLE_RATE_HZ     512
#define ECG_UART_BAUD          57600

static const struct device *const ecg_uart = DEVICE_DT_GET(DT_NODELABEL(uart0));
/* Tiny byte queue from ISR → thread; keep it byte-granular to match parser */
K_MSGQ_DEFINE(ecg_rx_q, 1, 2048, 4);   /* depth 2k bytes */
static atomic_t ecg_rx_overflows = ATOMIC_INIT(0);
static atomic_t ecg_rx_isr_cnt   = ATOMIC_INIT(0);

/* Optional: enable RAW on boot (ThinkGear 'r'): 0x02 'r' 0x01 */
static const uint8_t cmd_enable_raw[] = { 0x02, 0x72, 0x01 };

static void ecg_uart_isr(const struct device *dev, void *user_data)
{
    ARG_UNUSED(user_data);
    if (!uart_irq_update(dev)) return;
    while (uart_irq_rx_ready(dev)) {
        uint8_t buf[32];
        int rd = uart_fifo_read(dev, buf, sizeof(buf));
        for (int i=0;i<rd;i++) {
            if (k_msgq_put(&ecg_rx_q, &buf[i], K_NO_WAIT) != 0) {
                /* drop and count overflow */
                atomic_inc(&ecg_rx_overflows);
            }
        }
        if (rd>0) { atomic_inc(&ecg_rx_isr_cnt); }
    }
}

static int ecg_uart_init_hw(void)
{
    if (!device_is_ready(ecg_uart)) { LOGF("[BMD] uart0 not ready\n"); return -ENODEV; }
    struct uart_config want = {
        .baudrate  = ECG_UART_BAUD,
        .parity    = UART_CFG_PARITY_NONE,
        .stop_bits = UART_CFG_STOP_BITS_1,
        .data_bits = UART_CFG_DATA_BITS_8,
        .flow_ctrl = UART_CFG_FLOW_CTRL_NONE,
    };
    int err = uart_configure(ecg_uart, &want);
    if (err) { LOGF("[BMD] uart_configure err=%d (using DTS)\n", err); }
    uart_irq_callback_user_data_set(ecg_uart, ecg_uart_isr, NULL);
    uart_irq_rx_enable(ecg_uart);
    LOGF("[BMD] UART0 ready: %d bps\n", (int)ECG_UART_BAUD);
    return 0;
}

/* ThinkGear helpers */
static inline uint8_t tgpp_checksum(const uint8_t *p, uint8_t len)
{ uint32_t s=0; for (uint8_t i=0;i<len;i++) s+=p[i]; return (uint8_t)(0xFF - (s & 0xFF)); }

/* Last seen BPM & SigQ (atomic) */
static atomic_t last_bpm_atomic  = ATOMIC_INIT(-1);
static atomic_t last_sigq_atomic = ATOMIC_INIT(200);

/* Parser: BPM(0x03) / RAW(0x80,len=2,BE,i16) / SigQ(0x02) */
static void tgpp_parse_bpm_raw_sigq(const uint8_t *payload, uint8_t plen)
{
    static uint32_t raw_cnt = 0;
    static int16_t raw_batch[RAW_BATCH_N];
    static uint8_t raw_batch_n = 0;
    static uint32_t last_raw_flush_ms = 0;

    int bpm = -1, sigq = -1;
    uint8_t i=0;
    while (i < plen) {
        while (i < plen && payload[i]==0x55) { i++; }
        if (i >= plen) break;
        uint8_t code = payload[i++];
        if (code < 0x80) {
            if (i >= plen) break; uint8_t val = payload[i++];
            if (code==0x03) bpm = val; else if (code==0x02) sigq = val;
        } else {
            if (i >= plen) break; uint8_t vlen = payload[i++];
            if (i + vlen > plen) break;
            if (code==0x80 && vlen==2) {
                int16_t s = (int16_t)((payload[i]<<8) | payload[i+1]);
                raw_cnt++;
                if (EN_ECG_RAW) {
                    raw_batch[raw_batch_n++] = s;
                    if (raw_batch_n >= ARRAY_SIZE(raw_batch)) {
                        send_frame_raw_batch(raw_batch, raw_batch_n, RAW_SAMPLE_RATE_HZ);
                        raw_batch_n = 0; last_raw_flush_ms = ts_ms();
                    }
                }
            }
            i += vlen;
        }
    }
    if (EN_ECG_BPM && bpm >= 0)  atomic_set(&last_bpm_atomic, bpm);
    if (EN_ECG_SIGQ && sigq >= 0) atomic_set(&last_sigq_atomic, sigq);

    if (EN_ECG_RAW) {
        uint32_t now = ts_ms();
        if (raw_batch_n > 0 && (now - last_raw_flush_ms) >= 20U) {
            send_frame_raw_batch(raw_batch, raw_batch_n, RAW_SAMPLE_RATE_HZ);
            raw_batch_n = 0; last_raw_flush_ms = now;
        }
    }
}

static void bmd101_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!(EN_ECG_RAW || EN_ECG_BPM || EN_ECG_SIGQ)) return;
    if (ecg_uart_init_hw()!=0) return;

#if BMD_OPEN_RAW_ON_BOOT
    if (EN_ECG_RAW) {
        for (size_t i=0;i<sizeof(cmd_enable_raw);i++) uart_poll_out(ecg_uart, cmd_enable_raw[i]);
        LOGF("[BMD] Sent RAW enable\n");
    }
#endif

    enum { S_SYNC1, S_SYNC2, S_PLEN, S_PAYLOAD, S_CHK } st = S_SYNC1;
    uint8_t plen=0, got=0, payload[180];

    int64_t last_tick = k_uptime_get();

    while (1) {
        uint8_t byte;
        if (k_msgq_get(&ecg_rx_q, &byte, K_MSEC(50)) == 0) {
            switch (st) {
                case S_SYNC1: st=(byte==0xAA)?S_SYNC2:S_SYNC1; break;
                case S_SYNC2: st=(byte==0xAA)?S_PLEN:S_SYNC1; break;
                case S_PLEN:
                    plen=byte;
                    if (plen==0 || plen>sizeof(payload)) { st=S_SYNC1; }
                    else { got=0; st=S_PAYLOAD; }
                    break;
                case S_PAYLOAD:
                    payload[got++]=byte;
                    if (got>=plen) st=S_CHK;
                    break;
                case S_CHK: {
                    uint8_t expect = tgpp_checksum(payload, plen);
                    if (byte==expect) { tgpp_parse_bpm_raw_sigq(payload, plen); }
                    st=S_SYNC1; break;
                }
            }
        }

        int64_t now = k_uptime_get();
        if (now - last_tick >= 1000) {
            last_tick = now;
            if (EN_ECG_BPM) {
                int bpm = atomic_get(&last_bpm_atomic);
                if (bpm < 0) bpm = 0; if (bpm > 255) bpm = 255;
                send_frame_bpm((uint8_t)bpm);
            }
            if (EN_ECG_SIGQ) {
                int sigq = atomic_get(&last_sigq_atomic);
                if (sigq < 0) sigq = 200; if (sigq > 255) sigq = 255;
                send_frame_sigq((uint8_t)sigq);
            }
        }
        #if FORCE_Q_TEST
        {
            static int64_t last_q = 0;
            int64_t now2 = k_uptime_get();
            if (now2 - last_q >= 500) {   /* 每 500ms 发一次 Q=201 */
                last_q = now2;
                send_frame_sigq(201);
            }
        }
#endif
    }
}
K_THREAD_DEFINE(bmd_tid, ECG_STACK_SIZE, bmd101_thread, NULL, NULL, NULL, ECG_PRIO, 0, 0);

/* ========================= ECG RESET (active-low) ========================= */
#if DT_NODE_HAS_STATUS(DT_NODELABEL(ecg_rst), okay)
#define ECG_RST_NODE DT_NODELABEL(ecg_rst)
static const struct gpio_dt_spec ecg_rst = GPIO_DT_SPEC_GET(ECG_RST_NODE, gpios);
#endif

static void bmd101_hw_reset(void)
{
#if DT_NODE_HAS_STATUS(ECG_RST_NODE, okay)
    if (!device_is_ready(ecg_rst.port)) { LOGF("[BMD] RESET gpio not ready\n"); return; }
    gpio_pin_configure_dt(&ecg_rst, GPIO_OUTPUT_ACTIVE); /* active_low -> 0 */
    k_msleep(20);
    gpio_pin_set_dt(&ecg_rst, 1);
    gpio_pin_configure_dt(&ecg_rst, GPIO_INPUT);
    LOGF("[BMD] RESET pulse done on %s p%u\n", ecg_rst.port->name, ecg_rst.pin);
#else
    LOGF("[BMD] RESET pin not defined in DT\n");
#endif
}

/* ========================= LED4 blink thread ========================= */
static void led4_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!device_is_ready(gpio0)) return;
    gpio_pin_configure(gpio0, LED4_PIN, GPIO_OUTPUT_INACTIVE);

    while (1) {
        int bpm = atomic_get(&bpm_latest);
        if (bpm <= 0) { gpio_pin_set(gpio0, LED4_PIN, 0); k_msleep(200); continue; }
        if (bpm > 80) bpm = 80; /* cap visible blink */
        uint32_t period_ms = 60000U / (uint32_t)bpm;
        gpio_pin_toggle(gpio0, LED4_PIN);
        k_msleep(period_ms / 2);
    }
}
K_THREAD_DEFINE(led4_tid, 512, led4_thread, NULL, NULL, NULL, 5, 0, 0);

/* ========================= Main ========================= */
int main(void)
{
    init_dev_name_and_id();
    LOGF("\n=== %s TX boot ===\n", bt_name);

    bmd101_hw_reset();
    (void)imu_post_once();
    k_sem_give(&imu_start_sem); /* release IMU loop after POST */

    if (ble_init()) return 0;  /* silent if BLE init fails */

    /* Ensure LED pins are configured (redundant safe init) */
    if (device_is_ready(gpio0)) {
        gpio_pin_configure(gpio0, LED3_PIN, GPIO_OUTPUT_INACTIVE);
        gpio_pin_configure(gpio0, LED4_PIN, GPIO_OUTPUT_INACTIVE);
    }

    while (1) {
        if (EN_BLE) { dk_set_led_on(DK_LED1); k_sleep(K_MSEC(500)); dk_set_led_off(DK_LED1); }
#if DEBUG_LOG_ENABLE
        /* Periodic stats: lane drops + ECG RX health */
        static uint32_t last_print=0;
        if (ts_ms()-last_print>2000) {
            last_print=ts_ms();
            uint32_t drops_norm=(uint32_t)atomic_get(&ble_drops_norm);
            uint32_t drops_hi  =(uint32_t)atomic_get(&ble_drops_hi);
            uint32_t oflow     =(uint32_t)atomic_get(&ecg_rx_overflows);
            uint32_t isrcnt    =(uint32_t)atomic_get(&ecg_rx_isr_cnt);
            LOGF("[STAT] drop_norm=%u drop_hi=%u ecg_of=%u ecg_isr_chunks=%u\n",
                 drops_norm, drops_hi, oflow, isrcnt);
        }
#endif
        k_sleep(K_MSEC(500));
    }
}
