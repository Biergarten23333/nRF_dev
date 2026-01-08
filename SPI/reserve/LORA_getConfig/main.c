#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(lora_cfg, LOG_LEVEL_INF);

/* 设备名 & 引脚 */
#define UART0_NAME    "uart0"
#define GPIO1_NAME    "gpio1"
#define M0_PIN         7   /* P1.07 */
#define M1_PIN         8   /* P1.08 */
#define AUX_PIN       10   /* P1.10 */

/* 配置命令 */
#define CMD_READ_CFG   0xC1
#define ADR_CFG        0x00
#define LEN_CFG        0x08
#define CFG_BYTES      11  // Header(3) + Config(8)

static void print_separator(void) {
    LOG_INF("----------------------------------------");
}

void main(void) {
    const struct device *uart_dev = DEVICE_DT_GET(DT_NODELABEL(uart0));
    const struct device *gpio_dev = DEVICE_DT_GET(DT_NODELABEL(gpio1));
    uint8_t cfg[CFG_BYTES] = {0};
    int64_t start_time;
    size_t got = 0;

    if (!device_is_ready(uart_dev)) {
        LOG_ERR("UART device not ready");
        return;
    }
    if (!device_is_ready(gpio_dev)) {
        LOG_ERR("GPIO device not ready");
        return;
    }

    /* 配置引脚 */
    gpio_pin_configure(gpio_dev, M0_PIN, GPIO_OUTPUT);
    gpio_pin_configure(gpio_dev, M1_PIN, GPIO_OUTPUT);
    gpio_pin_configure(gpio_dev, AUX_PIN, GPIO_INPUT | GPIO_PULL_UP);

    /* 进入 PROGRAM 模式 (M0=1, M1=1) */
    gpio_pin_set(gpio_dev, M0_PIN, 1);
    gpio_pin_set(gpio_dev, M1_PIN, 1);
    k_msleep(60); // 等待模块稳定

    /* 发送读配置命令: C1 00 08 */
    uint8_t cmd[] = {CMD_READ_CFG, ADR_CFG, LEN_CFG};
    for (int i = 0; i < sizeof(cmd); i++) {
        uart_poll_out(uart_dev, cmd[i]);
    }

    /* 等待 AUX 变高（最大 1s）*/
    start_time = k_uptime_get();
    while (gpio_pin_get(gpio_dev, AUX_PIN) == 0) {
        if (k_uptime_get() - start_time > 1000) {
            LOG_ERR("等待 AUX 超时");
            return;
        }
        k_msleep(10);
    }
    k_msleep(20); // 确保数据就绪

    /* 读取响应数据（11字节）*/
    start_time = k_uptime_get();
    while (got < CFG_BYTES && (k_uptime_get() - start_time < 500)) {
        uint8_t byte;
        if (uart_poll_in(uart_dev, &byte) == 0) {
            cfg[got++] = byte;
        }
    }

    /* 打印原始数据 */
    print_separator();
    LOG_INF("HEAD : 0x%02X 0x%02X 0x%02X", cfg[0], cfg[1], cfg[2]);
    LOG_INF("AddH : 0x%02X", cfg[3]);
    LOG_INF("AddL : 0x%02X", cfg[4]);
    LOG_INF("Chan : 0x%02X -> %dMHz", cfg[7], 850 + cfg[7]);

    /* 解析 SPED 字段 (cfg[5]) */
    uint8_t sped = cfg[5];
    static const char *parity_str[] = {"8N1 (default)", "8O1", "8E1", "8N1"};
    static const char *baud_str[] = {
        "1200bps", "2400bps", "4800bps", "9600bps (default)",
        "19200bps", "38400bps", "57600bps", "115200bps"
    };
    static const char *air_rate_str[] = {
        "2.4kbps", "2.4kbps", "2.4kbps (default)", "4.8kbps",
        "9.6kbps", "19.2kbps", "38.4kbps", "62.5kbps"
    };

    uint8_t parity_val = (sped >> 3) & 0x03;
    uint8_t baud_val = (sped >> 5) & 0x07;
    uint8_t air_rate_val = sped & 0x07;

    LOG_INF("SPED      : 0x%02X", sped);
    LOG_INF("  UART Parity: 0x%01X -> %s", 
           parity_val, 
           (parity_val <= 3) ? parity_str[parity_val] : "Unknown");
    LOG_INF("  UART Baud  : 0x%01X -> %s", 
           baud_val, 
           (baud_val <= 7) ? baud_str[baud_val] : "Unknown");
    LOG_INF("  Air Rate   : 0x%01X -> %s", 
           air_rate_val, 
           (air_rate_val <= 7) ? air_rate_str[air_rate_val] : "Unknown");

    /* 解析 OPTION 字段 (cfg[6]) */
    uint8_t opt = cfg[6];
    static const char *pwr_str[] = {
        "22dBm (default)", "17dBm", "13dBm", "10dBm"
    };
    uint8_t pwr_val = opt & 0x03;
    uint8_t rssi_noise_val = (opt >> 5) & 0x01;

    LOG_INF("OPTION    : 0x%02X", opt);
    LOG_INF("  Tx Power   : 0x%01X -> %s", 
           pwr_val, 
           (pwr_val <= 3) ? pwr_str[pwr_val] : "Unknown");
    LOG_INF("  RSSI Noise : 0x%01X -> %s", 
           rssi_noise_val,
           rssi_noise_val ? "Enabled" : "Disabled (default)");

    /* 解析 TRANSMISSION_MODE 字段 (cfg[8]) */
    uint8_t tm = cfg[8];
    uint8_t wor_val = tm & 0x07;
    uint8_t fixed_trans_val = (tm >> 6) & 0x01;
    uint8_t rssi_enable_val = (tm >> 7) & 0x01;

    LOG_INF("TRANS_MODE: 0x%02X", tm);
    LOG_INF("  WOR Period : 0x%01X -> %dms%s", 
           wor_val, 
           (wor_val + 1) * 500,
           (wor_val == 3) ? " (default)" : "");
    LOG_INF("  Fixed Trans: 0x%01X -> %s", 
           fixed_trans_val,
           fixed_trans_val ? "Enabled" : "Disabled (default)");
    LOG_INF("  RSSI Enable: 0x%01X -> %s", 
           rssi_enable_val,
           rssi_enable_val ? "Enabled" : "Disabled (default)");

    print_separator();

    /* 恢复 NORMAL 模式 */
    gpio_pin_set(gpio_dev, M0_PIN, 0);
    gpio_pin_set(gpio_dev, M1_PIN, 0);
    k_msleep(50);

    while (1) {
        k_sleep(K_SECONDS(10));
    }
}