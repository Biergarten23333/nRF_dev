/*
 * 完整示例：LoRa E220 读写配置 + 每 250 ms 透明透传
 * 新增：打印 SPED/OPTION/CHANNEL/TRANSMISSION_MODE 四组配置的二进制
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/logging/log.h>
#include <zephyr/devicetree.h>
#include <stdio.h>
#include <string.h>

LOG_MODULE_REGISTER(lora_rx, LOG_LEVEL_INF);

/*—— 硬件 & 串口 定义 ——*/
#define UART_LABEL      "uart0"
#define GPIO_LABEL      "gpio1"
#define M0_PIN          7   /* P1.07 */
#define M1_PIN          8   /* P1.08 */
#define AUX_PIN        10   /* P1.10 */

/*—— LoRa 配置 命令 & 长度 ——*/
#define CMD_READ_CFG    0xC1
#define CMD_WRITE_CFG   0xC0
#define ADR_CFG         0x00
#define LEN_CFG         0x08//0x08
#define CFG_BYTES     (3 + LEN_CFG)

/*—— 透明透传 最大载荷 & 错误码 ——*/
#define MAX_TX_PACKET_BYTES 58
#define ERR_PACKET_TOO_BIG  -1
#define ERR_AUX_TIMEOUT     -2
#define STATUS_OK           0

/*—— 全局设备句柄 ——*/
static const struct device *uart_dev;
static const struct device *gpio_dev;


/*—— 其余辅助函数（保持不变） ——*/
static void enter_prog(void) {
    gpio_pin_set(gpio_dev, M0_PIN, 1);
    gpio_pin_set(gpio_dev, M1_PIN, 1);
    k_msleep(60);
}
static void enter_normal(void) {
    gpio_pin_set(gpio_dev, M0_PIN, 0);
    gpio_pin_set(gpio_dev, M1_PIN, 0);
    k_msleep(50);
}
static bool wait_aux_high(void) {
    int64_t start = k_uptime_get();
    while (gpio_pin_get(gpio_dev, AUX_PIN) == 0) {
        if (k_uptime_get() - start > 1000) {
            LOG_ERR("AUX 等待超时");
            return false;
        }
        k_msleep(10);
    }
    k_msleep(20);
    return true;
}
static size_t uart_read_bytes(uint8_t *buf, size_t len, size_t timeout_ms) {
    size_t cnt = 0;
    int64_t start = k_uptime_get();
    while (cnt < len && (k_uptime_get() - start) < timeout_ms) {
        uint8_t b;
        if (uart_poll_in(uart_dev, &b) == 0) {
            buf[cnt++] = b;
        }
    }
    return cnt;
}
static void print_separator(void) {
    LOG_INF("========================================");
}

void main(void)
{
    uint8_t cfg[CFG_BYTES];
    size_t got;

    /*—— 1. 绑定 & 配置 UART/GPIO ——*/
    uart_dev = device_get_binding(UART_LABEL);
    gpio_dev = device_get_binding(GPIO_LABEL);
    if (!device_is_ready(uart_dev) || !device_is_ready(gpio_dev)) {
        LOG_ERR("UART/GPIO 未就绪");
        return;
    }
    gpio_pin_configure(gpio_dev, M0_PIN, GPIO_OUTPUT);
    gpio_pin_configure(gpio_dev, M1_PIN, GPIO_OUTPUT);
    gpio_pin_configure(gpio_dev, AUX_PIN, GPIO_INPUT | GPIO_PULL_UP);

    /*—— 2. 读取 & 打印 原始配置 ——*/
    enter_prog();
    { uint8_t cmd[3] = { CMD_READ_CFG, ADR_CFG, LEN_CFG };//0xC1, 0x00, 0x09
      for (int i = 0; i < 3; i++) uart_poll_out(uart_dev, cmd[i]);
    }
    if (!wait_aux_high()) {
        enter_normal();
        return;
    }
    got = uart_read_bytes(cfg, CFG_BYTES, 500);
    LOG_INF("【初次读出 %u 字节】", (unsigned)got);
    LOG_INF("cfg_raw = %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x ",
            cfg[0], cfg[1], cfg[2],
            cfg[3], cfg[4], cfg[5],
            cfg[6], cfg[7], cfg[8],
            cfg[9], cfg[10],cfg[11]);

    enter_normal();
    print_separator ();
    
    /*—— 3. 修改 & 写回 ——*/
    cfg[3] = 0x00;
    cfg[4] = 0x03;
    cfg[5] = 0x00;
    cfg[6] = 0x62;
    cfg[7] = 0x00;
    cfg[8] = 0x12;
    cfg[9] = 0x00;
    cfg[10] = 0x00; // 这里可以修改 cfg[3] ~ cfg[10] 的值    
    cfg[11] = 0x00; // 保持最后一位为 0x00    
    enter_prog();
    {
        uint8_t wr[CFG_BYTES];
        wr[0] = CMD_WRITE_CFG; wr[1] = ADR_CFG; wr[2] = LEN_CFG;
        memcpy(&wr[3], &cfg[3], LEN_CFG);
        printk("Sending: ");
        LOG_INF("cfg_raw_check_before_send =");
        for (int i = 0; i < CFG_BYTES; i++) {
            uart_poll_out(uart_dev, wr[i]);
            LOG_INF("%02x ", wr[i]);
        }
    }
    if (!wait_aux_high()) {
        enter_normal();
        return;
    }
    enter_normal();

    /*—— 4. 再读 & 打印 验证 ——*/
    k_msleep(100);
    enter_prog();
    {
        uint8_t rd2[3] = { CMD_READ_CFG, ADR_CFG, LEN_CFG };
        for (int i = 0; i < 3; i++) uart_poll_out(uart_dev, rd2[i]);
    }
    if (!wait_aux_high()) {
        enter_normal();
        return;
    }
    got = uart_read_bytes(cfg, CFG_BYTES, 500);
    LOG_INF("【写入后读回 %u 字节】", (unsigned)got);
    LOG_INF("cfg_raw = %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x ",
            cfg[0], cfg[1], cfg[2],
            cfg[3], cfg[4], cfg[5],
            cfg[6], cfg[7], cfg[8],
            cfg[9], cfg[10],cfg[11]);
    enter_normal();

    /*—— 5. 透明透传 循环 ——*/
    uint8_t rx_buf[64];
    size_t idx = 0;

    while (1) {
        uint8_t c;
        /* 非阻塞读一个字节 */
        if (uart_poll_in(uart_dev, &c) == 0) {
            /* 累积到缓冲区，保留一位给 '\0' */
            if (idx < sizeof(rx_buf) - 1) {
                rx_buf[idx++] = c;
            }
            /* 遇到换行或缓冲满，就打印一行并重置 */
            if (c == '\n' || idx == sizeof(rx_buf) - 1) {
                rx_buf[idx] = '\0';
                LOG_INF("接收: %s", rx_buf);
                idx = 0;
            }
        } else {
            /* 没有数据，稍等一下 */
            k_msleep(1);
        }
    } 
}
