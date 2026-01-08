#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/byteorder.h>
#include <string.h>

#include "app_config.h"
#include "device_id.h"
#include "ble_link.h"
#include "ecg_task.h"

/* ========================= ECG (BMD101, UART0) ========================= */
#define RAW_BATCH_N            30   /* BLE frame packing count */
#define RAW_SAMPLE_RATE_HZ     512
#define ECG_UART_BAUD          57600

static const struct device *const ecg_uart =
    DEVICE_DT_GET(DT_NODELABEL(uart0));

/* Tiny byte queue from ISR → thread; keep it byte-granular to match parser */
K_MSGQ_DEFINE(ecg_rx_q, 1, 2048, 4);   /* depth 2k bytes */

/* 导出给 main 用于统计 */
atomic_t ecg_rx_overflows = ATOMIC_INIT(0);
atomic_t ecg_rx_isr_cnt   = ATOMIC_INIT(0);

/* Optional: enable RAW on boot (ThinkGear 'r'): 0x02 'r' 0x01 */
static const uint8_t cmd_enable_raw[] = { 0x02, 0x72, 0x01 };

static void ecg_uart_isr(const struct device *dev, void *user_data)
{
    ARG_UNUSED(user_data);
    if (!uart_irq_update(dev)) return;
    while (uart_irq_rx_ready(dev)) {
        uint8_t buf[32];
        int rd = uart_fifo_read(dev, buf, sizeof(buf));
        for (int i = 0; i < rd; i++) {
            if (k_msgq_put(&ecg_rx_q, &buf[i], K_NO_WAIT) != 0) {
                /* drop and count overflow */
                atomic_inc(&ecg_rx_overflows);
            }
        }
        if (rd > 0) {
            atomic_inc(&ecg_rx_isr_cnt);
        }
    }
}

static int ecg_uart_init_hw(void)
{
    if (!device_is_ready(ecg_uart)) {
        LOGF("[BMD] uart0 not ready\n");
        return -ENODEV;
    }
    struct uart_config want = {
        .baudrate  = ECG_UART_BAUD,
        .parity    = UART_CFG_PARITY_NONE,
        .stop_bits = UART_CFG_STOP_BITS_1,
        .data_bits = UART_CFG_DATA_BITS_8,
        .flow_ctrl = UART_CFG_FLOW_CTRL_NONE,
    };
    int err = uart_configure(ecg_uart, &want);
    if (err) {
        LOGF("[BMD] uart_configure err=%d (using DTS)\n", err);
    }
    uart_irq_callback_user_data_set(ecg_uart, ecg_uart_isr, NULL);
    uart_irq_rx_enable(ecg_uart);
    LOGF("[BMD] UART0 ready: %d bps\n", (int)ECG_UART_BAUD);
    return 0;
}

/* ThinkGear helpers */
static inline uint8_t tgpp_checksum(const uint8_t *p, uint8_t len)
{
    uint32_t s = 0;
    for (uint8_t i = 0; i < len; i++) s += p[i];
    return (uint8_t)(0xFF - (s & 0xFF));
}

/* Last seen BPM & SigQ (atomic) */
static atomic_t last_bpm_atomic  = ATOMIC_INIT(-1);
static atomic_t last_sigq_atomic = ATOMIC_INIT(200);

/* ========================= RAW batch 'R' ========================= */
static uint16_t seq_raw = 0;
static inline uint16_t next_seq_raw(void)
{
    uint16_t v = seq_raw;
    seq_raw = (uint16_t)(v + 1);
    return v;
}

static void send_frame_raw_batch(const int16_t *samples, uint8_t n,
                                 uint16_t sr_hz)
{
    if (!EN_ECG_RAW || !EN_BLE || n == 0) return;

    const uint16_t hdr = 13; /* 0xAA + 'R' + seq2 + id2 + t4 + sr2 + cnt1 */
    const uint16_t max_payload = BLE_BUF_SIZE - hdr;
    const uint8_t max_n = (uint8_t)(max_payload / 2U);

    if (n > max_n) {
        /* Prevent overflow: split recursively into chunks */
        uint8_t remain = n;
        const int16_t *pS = samples;
        while (remain) {
            uint8_t take = (remain > max_n) ? max_n : remain;
            send_frame_raw_batch(pS, take, sr_hz);
            pS += take;
            remain -= take;
        }
        return;
    }

    uint8_t f[BLE_BUF_SIZE];
    uint16_t p = 0;
    f[p++] = 0xAA;
    f[p++] = 'R';
    sys_put_le16(next_seq_raw(), &f[p]); p += 2;
    sys_put_le16(dev_id16, &f[p]);       p += 2;
    sys_put_le32(ts_ms(), &f[p]);        p += 4;
    sys_put_le16(sr_hz, &f[p]);          p += 2;
    f[p++] = n;
    for (uint8_t i = 0; i < n; i++) {
        sys_put_le16((uint16_t)samples[i], &f[p]); p += 2;
    }
    ble_enqueue_frame_norm(f, p);
}

/* ========================= LED3/LED4 GPIO ========================= */
#define LED3_PIN 15  /* P0.15 */
#define LED4_PIN 16  /* P0.16 */

static const struct device *const gpio0 = DEVICE_DT_GET(DT_NODELABEL(gpio0));
static atomic_t bpm_latest = ATOMIC_INIT(0);

/* LED3 non-blocking flash */
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
    static bool inited = false;
    if (!inited) {
        gpio_pin_configure(gpio0, LED3_PIN, GPIO_OUTPUT_INACTIVE);
        k_work_init_delayable(&led3_off_work, led3_off_work_handler);
        inited = true;
    }
    gpio_pin_set(gpio0, LED3_PIN, 1);
    /* schedule off after 50 ms */
    k_work_schedule(&led3_off_work, K_MSEC(50));
}

/* ========================= BPM 'H' / SigQ 'Q' ========================= */
static uint16_t seq_bpm  = 0;
static uint16_t seq_sigq = 0;

static inline uint16_t next_seq_bpm(void)
{
    uint16_t v = seq_bpm;
    seq_bpm = (uint16_t)(v + 1);
    return v;
}
static inline uint16_t next_seq_sigq(void)
{
    uint16_t v = seq_sigq;
    seq_sigq = (uint16_t)(v + 1);
    return v;
}

static void send_frame_bpm(uint8_t bpm)
{
    if (!EN_ECG_BPM || !EN_BLE) return;

    /* LED3 flash non-blocking; LED4 blink thread uses bpm_latest */
    led3_flash_50ms_now();
    atomic_set(&bpm_latest, bpm);

    uint8_t f[BLE_BUF_SIZE];
    uint16_t p = 0;
    f[p++] = 0xAA;
    f[p++] = 'H';
    sys_put_le16(next_seq_bpm(), &f[p]); p += 2;
    sys_put_le16(dev_id16, &f[p]);       p += 2;
    sys_put_le32(ts_ms(), &f[p]);        p += 4;
    f[p++] = bpm;

    ble_enqueue_frame_hi(f, p);
}

static void send_frame_sigq(uint8_t q)
{
    if (!EN_ECG_SIGQ || !EN_BLE) return;
    uint8_t f[BLE_BUF_SIZE];
    uint16_t p = 0;
    f[p++] = 0xAA;
    f[p++] = 'Q';
    sys_put_le16(next_seq_sigq(), &f[p]); p += 2;
    sys_put_le16(dev_id16, &f[p]);       p += 2;
    sys_put_le32(ts_ms(), &f[p]);        p += 4;
    f[p++] = q;
    ble_enqueue_frame_hi(f, p);
}

/* Parser: BPM(0x03) / RAW(0x80,len=2,BE,i16) / SigQ(0x02) */
static void tgpp_parse_bpm_raw_sigq(const uint8_t *payload, uint8_t plen)
{
    static uint32_t raw_cnt = 0;
    static int16_t raw_batch[RAW_BATCH_N];
    static uint8_t raw_batch_n = 0;
    static uint32_t last_raw_flush_ms = 0;

    int bpm = -1, sigq = -1;
    uint8_t i = 0;
    while (i < plen) {
        while (i < plen && payload[i] == 0x55) { i++; }
        if (i >= plen) break;

        uint8_t code = payload[i++];
        if (code < 0x80) {
            if (i >= plen) break;
            uint8_t val = payload[i++];
            if (code == 0x03)      bpm  = val;
            else if (code == 0x02) sigq = val;
        } else {
            if (i >= plen) break;
            uint8_t vlen = payload[i++];
            if (i + vlen > plen) break;
            if (code == 0x80 && vlen == 2) {
                int16_t s = (int16_t)((payload[i] << 8) | payload[i+1]);
                raw_cnt++;
                if (EN_ECG_RAW) {
                    raw_batch[raw_batch_n++] = s;
                    if (raw_batch_n >= ARRAY_SIZE(raw_batch)) {
                        send_frame_raw_batch(raw_batch, raw_batch_n,
                                             RAW_SAMPLE_RATE_HZ);
                        raw_batch_n = 0;
                        last_raw_flush_ms = ts_ms();
                    }
                }
            }
            i += vlen;
        }
    }

    if (EN_ECG_BPM && bpm >= 0)  atomic_set(&last_bpm_atomic,  bpm);
    if (EN_ECG_SIGQ && sigq >= 0) atomic_set(&last_sigq_atomic, sigq);

    if (EN_ECG_RAW) {
        uint32_t now = ts_ms();
        if (raw_batch_n > 0 && (now - last_raw_flush_ms) >= 20U) {
            send_frame_raw_batch(raw_batch, raw_batch_n, RAW_SAMPLE_RATE_HZ);
            raw_batch_n = 0;
            last_raw_flush_ms = now;
        }
    }
}

static void bmd101_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!(EN_ECG_RAW || EN_ECG_BPM || EN_ECG_SIGQ)) return;
    if (ecg_uart_init_hw() != 0) return;

#if BMD_OPEN_RAW_ON_BOOT
    if (EN_ECG_RAW) {
        for (size_t i = 0; i < sizeof(cmd_enable_raw); i++) {
            uart_poll_out(ecg_uart, cmd_enable_raw[i]);
        }
        LOGF("[BMD] Sent RAW enable\n");
    }
#endif

    enum { S_SYNC1, S_SYNC2, S_PLEN, S_PAYLOAD, S_CHK } st = S_SYNC1;
    uint8_t plen = 0, got = 0, payload[180];

    int64_t last_tick = k_uptime_get();

    while (1) {
        uint8_t byte;
        if (k_msgq_get(&ecg_rx_q, &byte, K_MSEC(50)) == 0) {
            switch (st) {
                case S_SYNC1:
                    st = (byte == 0xAA) ? S_SYNC2 : S_SYNC1;
                    break;
                case S_SYNC2:
                    st = (byte == 0xAA) ? S_PLEN : S_SYNC1;
                    break;
                case S_PLEN:
                    plen = byte;
                    if (plen == 0 || plen > sizeof(payload)) {
                        st = S_SYNC1;
                    } else {
                        got = 0;
                        st = S_PAYLOAD;
                    }
                    break;
                case S_PAYLOAD:
                    payload[got++] = byte;
                    if (got >= plen) st = S_CHK;
                    break;
                case S_CHK: {
                    uint8_t expect = tgpp_checksum(payload, plen);
                    if (byte == expect) {
                        tgpp_parse_bpm_raw_sigq(payload, plen);
                    }
                    st = S_SYNC1;
                    break;
                }
            }
        }

        int64_t now = k_uptime_get();
        if (now - last_tick >= 1000) {
            last_tick = now;
            if (EN_ECG_BPM) {
                int bpm = atomic_get(&last_bpm_atomic);
                if (bpm < 0)   bpm = 0;
                if (bpm > 255) bpm = 255;
                send_frame_bpm((uint8_t)bpm);
            }
            if (EN_ECG_SIGQ) {
                int sigq = atomic_get(&last_sigq_atomic);
                if (sigq < 0)   sigq = 200;
                if (sigq > 255) sigq = 255;
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

K_THREAD_DEFINE(bmd_tid, ECG_STACK_SIZE,
                bmd101_thread, NULL, NULL, NULL,
                ECG_PRIO, 0, 0);

/* ========================= ECG RESET (active-low) ========================= */
#if DT_NODE_HAS_STATUS(DT_NODELABEL(ecg_rst), okay)
#define ECG_RST_NODE DT_NODELABEL(ecg_rst)
static const struct gpio_dt_spec ecg_rst =
    GPIO_DT_SPEC_GET(ECG_RST_NODE, gpios);
#endif

void bmd101_hw_reset(void)
{
#if DT_NODE_HAS_STATUS(ECG_RST_NODE, okay)
    if (!device_is_ready(ecg_rst.port)) {
        LOGF("[BMD] RESET gpio not ready\n");
        return;
    }
    gpio_pin_configure_dt(&ecg_rst, GPIO_OUTPUT_ACTIVE); /* active_low -> 0 */
    k_msleep(20);
    gpio_pin_set_dt(&ecg_rst, 1);
    gpio_pin_configure_dt(&ecg_rst, GPIO_INPUT);
    LOGF("[BMD] RESET pulse done on %s p%u\n",
         ecg_rst.port->name, ecg_rst.pin);
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
        if (bpm <= 0) {
            gpio_pin_set(gpio0, LED4_PIN, 0);
            k_msleep(200);
            continue;
        }
        if (bpm > 80) bpm = 80; /* cap visible blink */
        uint32_t period_ms = 60000U / (uint32_t)bpm;
        gpio_pin_toggle(gpio0, LED4_PIN);
        k_msleep(period_ms / 2);
    }
}

K_THREAD_DEFINE(led4_tid, 512,
                led4_thread, NULL, NULL, NULL,
                5, 0, 0);
