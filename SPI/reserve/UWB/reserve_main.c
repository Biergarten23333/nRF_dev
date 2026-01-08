/* src/main.c */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/byteorder.h>
#include <string.h>

#define WAKE_BYTE      0x00
static const uint8_t CMD_LOC_GET[2] = {0x0C, 0x00};
static const uint8_t TLV_INT_EN[4]  = {0x34, 0x02, 0x01, 0x00};

#define BUF_MAX       128
#define UPTIME_FREQ   60    /* 打印 uptime 的循环次数，可以按需调整 */

static const struct device *uart1_dev;
static const struct device *gpio1_dev;
static const uint32_t    ready_pin_num = 3;  /* P1.03 */

static struct gpio_callback ready_cb;
static struct k_sem sem_ready;

/* UART 写缓冲区输出，阻塞式 */
static inline void uart_write_block(const struct device *dev,
                                    const uint8_t *buf, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        uart_poll_out(dev, buf[i]);
    }
}

/* UART 读单字节，非阻塞 */
static inline int uart_read_byte(const struct device *dev, uint8_t *ch)
{
    return uart_poll_in(dev, ch);
}

/* 解析返回的 LOC_TLV 数据 */
static bool try_parse_loc(const uint8_t *buf, size_t len)
{
    for (size_t i = 0; i + 14 <= len; i++) {
        if (buf[i] == 0x41 && buf[i+1] == 0x0D) {
            const uint8_t *p = buf + i + 2;
            int32_t x = (int32_t)sys_get_le32(p);
            int32_t y = (int32_t)sys_get_le32(p + 4);
            int32_t z = (int32_t)sys_get_le32(p + 8);
            uint8_t qf = p[12];
            printk("POS = [%d %d %d] mm  QF=%u%%\n", x, y, z, qf);
            return true;
        }
    }
    return false;
}

/* READY 引脚中断回调，释放信号量 */
static void ready_isr(const struct device *dev,
                      struct gpio_callback *cb, uint32_t pins)
{
    ARG_UNUSED(dev);
    ARG_UNUSED(cb);
    ARG_UNUSED(pins);
    k_sem_give(&sem_ready);
}

/* 上电唤醒模块：发三个 WAKE_BYTE */
static void wake_module(const struct device *u)
{
    for (int i = 0; i < 3; i++) {
        uart_poll_out(u, WAKE_BYTE);
    }
    k_sleep(K_MSEC(5));
}

int main(void)
{
    /* 1. 获取并检查设备 */
    uart1_dev = DEVICE_DT_GET(DT_NODELABEL(uart1));
    if (!device_is_ready(uart1_dev)) {
        printk("ERR: UART1 not ready!\n");
        return 0;
    }

    gpio1_dev = DEVICE_DT_GET(DT_NODELABEL(gpio1));
    if (!device_is_ready(gpio1_dev)) {
        printk("ERR: GPIO1 not ready!\n");
        return 0;
    }

    /* 2. 配置 READY 引脚为上升沿中断输入 */
    gpio_pin_configure(gpio1_dev, ready_pin_num,
                       GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_interrupt_configure(gpio1_dev, ready_pin_num,
                                 GPIO_INT_EDGE_RISING);
    gpio_init_callback(&ready_cb, ready_isr, BIT(ready_pin_num));
    gpio_add_callback(gpio1_dev, &ready_cb);

    /* 3. 初始化信号量 */
    k_sem_init(&sem_ready, 0, 1);

    printk("READY-triggered dwm_loc_get 启动\n");

    /* 4. 唤醒并使能 Generic-TLV 中断 */
    wake_module(uart1_dev);
    uart_write_block(uart1_dev, TLV_INT_EN, sizeof(TLV_INT_EN));
    k_sleep(K_MSEC(10));

    /* 5. 主循环：每次 sem_take 唤醒即可，速率由 READY 引脚决定 */
    uint32_t loop = 0;
    while (1) {
        /* 阻塞直到 READY 中断 */
        k_sem_take(&sem_ready, K_FOREVER);

        /* 发送定位请求 */
        uart_write_block(uart1_dev, CMD_LOC_GET, sizeof(CMD_LOC_GET));

        /* 接收并解析数据 */
        uint8_t buf[BUF_MAX];
        size_t idx = 0;
        uint8_t ch;
        int64_t deadline = k_uptime_get() + 50;  /* 最多等 50ms 数据 */
        while (k_uptime_get() < deadline && idx < BUF_MAX) {
            if (uart_read_byte(uart1_dev, &ch) == 0) {
                buf[idx++] = ch;
            } else {
                /* 没字节可读就短延时 */
                k_sleep(K_USEC(50));
            }
        }
        if (!idx || !try_parse_loc(buf, idx)) {
            printk("(No POS TLV)\n");
        }

        /* 可选：每 UPTIME_FREQ 次打印一次运行时间 */
        if (++loop >= UPTIME_FREQ) {
            uint32_t s = k_uptime_get() / 1000U;
            printk("Uptime %02u:%02u:%02u\n",
                   s / 3600U, (s / 60U) % 60U, s % 60U);
            loop = 0;
        }
    }

    return 0;
}
