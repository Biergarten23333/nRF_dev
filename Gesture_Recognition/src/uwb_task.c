/* src/uwb_task.c
 *
 * DWM1001C polling 版 UWB 驱动（流式修包版，不再靠每轮 drain）
 * - 每 100ms 发送 0x0C 0x00（LOC_GET）
 * - 80ms 内收字节，累积到 rx_buf（可跨轮）
 * - 在 rx_buf 里查找 TLV：0x41 0x0D + 13 字节 payload
 * - 只在启动时 drain 一次 UART，后面通过“修包”避免半包错位
 * - 过滤异常坐标 + 过滤全 0 坐标 (x=y=z=q=0 当作无效)
 */

#include "app_config.h"
#include "device_id.h"
#include "ble_link.h"
#include "uwb_task.h"

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/printk.h>
#include <string.h>

/* ------------------------------------------------------------------------- */
/*                      CONFIG & CONSTANTS                                   */
/* ------------------------------------------------------------------------- */

#define WAKE_BYTE              0x00
static const uint8_t CMD_LOC_GET[2] = { 0x0C, 0x00 };

#define UBUF_MAX               256
#define UWB_POLL_INTERVAL_MS   100    /* 10Hz */
#define UWB_RX_WINDOW_MS        80    /* 单次接收窗口 80ms */

static const struct device *uart1_dev = DEVICE_DT_GET(DT_NODELABEL(uart1));

static uint16_t seq_uwb;

/* ------------------------------------------------------------------------- */
/*                      辅助函数                                             */
/* ------------------------------------------------------------------------- */

static inline uint16_t next_seq_uwb(void)
{
    uint16_t v = seq_uwb;
    seq_uwb = (uint16_t)(v + 1);
    return v;
}

static inline void uart_tx_bytes(const uint8_t *d, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        uart_poll_out(uart1_dev, d[i]);
    }
}

/* 启动阶段用一次：把历史残留垃圾清空 */
static inline void uart_drain_rx(void)
{
    uint8_t ch;
    while (uart_poll_in(uart1_dev, &ch) == 0) {
        /* drain old garbage */
    }
}

/* ------------------------------------------------------------------------- */
/*                    发送 UWB 'U' 帧                                        */
/* ------------------------------------------------------------------------- */

static void send_frame_uwb(int32_t x, int32_t y, int32_t z, uint8_t q)
{
    if (!EN_UWB || !EN_BLE) {
        return;
    }

    uint8_t f[BLE_BUF_SIZE];
    uint16_t p = 0;

    f[p++] = 0xAA;
    f[p++] = 'U';

    sys_put_le16(next_seq_uwb(),      &f[p]); p += 2;
    sys_put_le16(device_id_get16(),   &f[p]); p += 2;

    uint32_t t_ms = ble_link_now_host_ms();
    sys_put_le32(t_ms,                &f[p]); p += 4;

    sys_put_le32(x,                   &f[p]); p += 4;
    sys_put_le32(y,                   &f[p]); p += 4;
    sys_put_le32(z,                   &f[p]); p += 4;

    f[p++] = q;   /* quality */
    f[p++] = 0;   /* reserved */

    ble_link_send(f, p);
}

/* ------------------------------------------------------------------------- */
/*              累积缓冲区里的 TLV 修包 + 解析                                */
/* ------------------------------------------------------------------------- */
/*
 * DWM1001 LOC TLV：
 *   type = 0x41
 *   len  = 0x0D
 *   payload(13 bytes) = x(4) + y(4) + z(4) + q(1)
 *
 * 总长度 = 2 + 0x0D = 15 字节
 *
 * rx_buf: 当前缓冲区
 * *rx_len: 当前有效字节数（函数内会更新）
 */
static void uwb_process_rx_buf(uint8_t *rx_buf, size_t *rx_len)
{
    size_t len = *rx_len;

    /* 至少要有一帧 TLV 才有解析意义 */
    while (len >= 15U) {
        bool found = false;
        size_t i = 0;

        /* 1) 在缓冲区中寻找 0x41 0x0D */
        for (i = 0; i + 2U <= len; i++) {
            if (rx_buf[i] == 0x41 && rx_buf[i + 1] == 0x0D) {
                found = true;
                break;
            }
        }

        if (!found) {
            /* 整个缓冲里都没有 TLV 起始：最多保留最后 1 字节防止“半个头” */
            if (len > 1U) {
                rx_buf[0] = rx_buf[len - 1U];
                len = 1U;
            }
            break;
        }

        if (i > 0U) {
            /* 前面 i 个字节都是垃圾，整体左移丢掉 */
            memmove(rx_buf, &rx_buf[i], len - i);
            len -= i;
            continue;   /* 再从开头开始重新找 0x41 0x0D */
        }

        /* 到这里：rx_buf[0] == 0x41, rx_buf[1] == 0x0D */
        const size_t TLV_TOTAL_LEN = 2U + 0x0DU;  /* 15 */

        if (len < TLV_TOTAL_LEN) {
            /* 半包：还不够 15 字节，等下一轮再补 */
            break;
        }

        /* 2) 解析完整 TLV */
        const uint8_t *p = &rx_buf[2];  /* 跳过 type & len */
        int32_t x = sys_get_le32(p);
        int32_t y = sys_get_le32(p + 4);
        int32_t z = sys_get_le32(p + 8);
        uint8_t q = p[12];

        /* 3) Sanity check：
         *   - 丢掉全 0 (x=y=z=q=0) → 视为“没有定位”
         *   - 丢掉明显越界 / 错位帧
         */
        if (!(x == 0 && y == 0 && z == 0 && q == 0) &&
            q <= 100 &&
            x > -500000 && x < 500000 &&
            y > -500000 && y < 500000 &&
            z > -500000 && z < 500000) {

            send_frame_uwb(x, y, z, q);
        }

        /* 4) 把已经解析掉的这一帧从缓冲区“剪掉”，保留后面的内容继续修包 */
        size_t remain = len - TLV_TOTAL_LEN;
        if (remain > 0U) {
            memmove(rx_buf, &rx_buf[TLV_TOTAL_LEN], remain);
        }
        len = remain;
    }

    *rx_len = len;
}

/* ------------------------------------------------------------------------- */
/*                      核心 UWB polling 线程                                */
/* ------------------------------------------------------------------------- */

static void uwb_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a);
    ARG_UNUSED(b);
    ARG_UNUSED(c);

    if (!EN_UWB) {
        printk("UWB disabled\n");
        return;
    }
    if (!device_is_ready(uart1_dev)) {
        printk("uart1 not ready\n");
        return;
    }

    /* 唤醒模块 + 启动阶段清空一次历史残留 */
    for (int i = 0; i < 20; i++) {
        uart_poll_out(uart1_dev, WAKE_BYTE);
    }
    k_msleep(10);
    uart_drain_rx();

    printk("UWB ready (polling=%d Hz)\n", 1000 / UWB_POLL_INTERVAL_MS);

    /* 持久 RX 缓冲区：跨循环累计，避免半包 / 丢包 */
    uint8_t rx_buf[UBUF_MAX];
    size_t  rx_len = 0;

    int64_t next_t = k_uptime_get();

    while (1) {

        /* A) 等到下一个 poll 时间（目标 10Hz） */
        int64_t now = k_uptime_get();
        if (now < next_t) {
            k_msleep((uint32_t)(next_t - now));
        }
        now = k_uptime_get();
        next_t = now + UWB_POLL_INTERVAL_MS;

        /* B) 发送 LOC_GET 命令（不再每轮清空 UART） */
        uart_tx_bytes(CMD_LOC_GET, sizeof(CMD_LOC_GET));

        /* C) 在一个固定窗口内收集数据，累积到 rx_buf 中 */
        uint8_t ch;
        int64_t deadline = k_uptime_get() + UWB_RX_WINDOW_MS;

        while (k_uptime_get() < deadline) {
            if (uart_poll_in(uart1_dev, &ch) == 0) {
                if (rx_len < UBUF_MAX) {
                    rx_buf[rx_len++] = ch;
                } else {
                    /* 缓冲满了：保留最后 1 字节作为可能的头，丢掉前面 */
                    rx_buf[0] = rx_buf[rx_len - 1U];
                    rx_len = 1U;
                }
            } else {
                /* 没有新数据时稍微让一下 CPU，避免 busy loop */
                k_usleep(200);
            }
        }

        /* D) 对当前累计的缓冲区做修包 + 解析 TLV（可能解析 0~N 帧） */
        if (rx_len >= 2U) {
            uwb_process_rx_buf(rx_buf, &rx_len);
        }
    }
}

/* ------------------------------------------------------------------------- */
/*                      线程创建                                             */
/* ------------------------------------------------------------------------- */

K_THREAD_STACK_DEFINE(uwb_stack, UWB_STACK_SIZE);
static struct k_thread uwb_thread_data;

int uwb_init_and_start(void)
{
    if (!EN_UWB) {
        return 0;
    }

    k_thread_create(&uwb_thread_data,
                    uwb_stack,
                    K_THREAD_STACK_SIZEOF(uwb_stack),
                    uwb_thread,
                    NULL, NULL, NULL,
                    UWB_PRIO, 0, K_NO_WAIT);

    printk("UWB thread started\n");
    return 0;
}
