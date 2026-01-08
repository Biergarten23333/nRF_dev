#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/byteorder.h>   // ← 为了 sys_put_le16 / sys_put_le32 / sys_get_le32

#include "app_config.h"
#include "device_id.h"
#include "ble_link.h"
#include "uwb_task.h"

/* ========================= UWB (UART1) ========================= */
#define UWB_INT_PORT_DEV   DT_NODELABEL(gpio1)
#define UWB_INT_PIN        3
#define UBUF_MAX           128
#define UWB_POLL_MS        200
#define UWB_READ_WINDOW_MS 10
#define UWB_WAKE_BYTES     8
#define WAKE_BYTE          0x00

static const uint8_t CMD_LOC_GET[2] = {0x0C, 0x00};
static const uint8_t TLV_INT_EN[4]  = {0x34, 0x02, 0x01, 0x00};

static const struct device *uart1_dev = DEVICE_DT_GET(DT_NODELABEL(uart1));
static const struct device *gpio1_dev = DEVICE_DT_GET(UWB_INT_PORT_DEV);
static struct gpio_callback uwb_cb;
static struct k_sem sem_uwb;

static inline void uwb_tx_bytes(const uint8_t *d, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        uart_poll_out(uart1_dev, d[i]);
    }
}

static inline void uwb_drain_rx(void)
{
    uint8_t ch;
    while (uart_poll_in(uart1_dev, &ch) == 0) {}
}

static void gpio_int(const struct device *d, struct gpio_callback *cb,
                     uint32_t pins)
{
    ARG_UNUSED(d);
    ARG_UNUSED(cb);
    ARG_UNUSED(pins);
    k_sem_give(&sem_uwb);
}

/* UWB frame seq */
static uint16_t seq_uwb = 0;
static inline uint16_t next_seq_uwb(void)
{
    uint16_t v = seq_uwb;
    seq_uwb = (uint16_t)(v + 1);
    return v;
}

/* ========================= UWB frame 'U' — x,y,z(i32), q(u8) ========================= */
static void send_frame_uwb(int32_t x, int32_t y, int32_t z, uint8_t q)
{
    if (!EN_UWB || !EN_BLE) return;

    uint8_t f[BLE_BUF_SIZE];
    uint16_t p = 0;
    f[p++] = 0xAA;
    f[p++] = 'U';
    sys_put_le16(next_seq_uwb(), &f[p]); p += 2;
    sys_put_le16(dev_id16, &f[p]);       p += 2;
    sys_put_le32(ts_ms(), &f[p]);        p += 4;
    sys_put_le32(x, &f[p]); p += 4;
    sys_put_le32(y, &f[p]); p += 4;
    sys_put_le32(z, &f[p]); p += 4;
    f[p++] = q;

    ble_enqueue_frame_norm(f, p);
}

static void uwb_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
    if (!EN_UWB) return;

    if (!device_is_ready(uart1_dev) || !device_is_ready(gpio1_dev)) {
        LOGF("UWB hw not ready\n");
        return;
    }

    gpio_pin_configure(gpio1_dev, UWB_INT_PIN,
                       GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_interrupt_configure(gpio1_dev, UWB_INT_PIN,
                                 GPIO_INT_EDGE_RISING);
    gpio_init_callback(&uwb_cb, gpio_int, BIT(UWB_INT_PIN));
    gpio_add_callback(gpio1_dev, &uwb_cb);
    k_sem_init(&sem_uwb, 0, 1);

    for (int i = 0; i < UWB_WAKE_BYTES; i++) {
        uart_poll_out(uart1_dev, WAKE_BYTE);
    }
    uwb_tx_bytes(TLV_INT_EN, sizeof(TLV_INT_EN));
    k_msleep(10);
    uwb_drain_rx();
    LOGF("UWB ready\n");

    uint8_t buf[UBUF_MAX];
    int64_t last_poll = 0;

    while (1) {
        bool do_query = false;
        if (k_sem_take(&sem_uwb, K_MSEC(5)) == 0) {
            do_query = true;
        }
        int64_t now = k_uptime_get();
        if (now - last_poll >= UWB_POLL_MS) {
            do_query = true;
        }
        if (!do_query) {
            continue;
        }
        last_poll = now;

        uwb_tx_bytes(CMD_LOC_GET, sizeof(CMD_LOC_GET));
        size_t n = 0;
        int64_t deadline = k_uptime_get() + UWB_READ_WINDOW_MS;
        while (k_uptime_get() < deadline && n < UBUF_MAX) {
            uint8_t ch;
            if (uart_poll_in(uart1_dev, &ch) == 0) {
                buf[n++] = ch;
            }
        }
        if (n == 0) continue;

        for (size_t i = 0; i + 14 <= n; i++) {
            if (buf[i] == 0x41 && buf[i+1] == 0x0D) {
                const uint8_t *p = &buf[i+2];
                int32_t x = sys_get_le32(p);
                int32_t y = sys_get_le32(p+4);
                int32_t z = sys_get_le32(p+8);
                uint8_t q = p[12];
                send_frame_uwb(x, y, z, q);
                break;
            }
        }
    }
}

K_THREAD_DEFINE(uwb_tid, UWB_STACK_SIZE,
                uwb_thread, NULL, NULL, NULL,
                UWB_PRIO, 0, 0);
