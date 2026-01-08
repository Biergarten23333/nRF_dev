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

LOG_MODULE_REGISTER(lora_tx, LOG_LEVEL_INF);

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


/* 发送原始字节流并等待 AUX 回高 */
static int sendStruct(const uint8_t *data, size_t len) {
    if (len > MAX_TX_PACKET_BYTES) {
        return ERR_PACKET_TOO_BIG;
    }
    for (size_t i = 0; i < len; i++) {
        uart_poll_out(uart_dev, data[i]);
    }
    if (!wait_aux_high()) {
        return ERR_AUX_TIMEOUT;
    }
    return STATUS_OK;
}

/* 新的 sendMessage：在 msg 前拼 3 字节头，然后走 sendStruct */
static int sendMessage(const char *msg) {
    size_t payload_len = strlen(msg);
    /* 整包长度 = 头 3 字节 + payload_len */
    size_t packet_len = 3 + payload_len;
    uint8_t packet[3 + MAX_TX_PACKET_BYTES];

    /* 填头 */
    packet[0] = 0x00; //ADDH
    packet[1] = 0x03; //ADDL
    packet[2] = 0x00; //CHANNEL // 这里可以根据需要修改 ADDH/ADDL/CHANNEL 的值
    /* 填数据 */
    memcpy(&packet[3], msg, payload_len);

    /* 发整包 */
    return sendStruct(packet, packet_len);
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
    { uint8_t cmd[3] = { CMD_READ_CFG, ADR_CFG, LEN_CFG };//0xC1, 0x00, 0x08
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
    k_msleep(1000);    
    /*—— 5. 透明透传 循环 ——*/
    uint32_t counter = 0;
    while (1) {
        uint64_t ms = k_uptime_get();
        uint32_t h = (ms / 3600000) % 24;
        uint32_t m = (ms / 60000) % 60;
        uint32_t s = (ms / 1000) % 60;
        char payload[64];
        int len = snprintf(payload, sizeof(payload),
                           "[%02u:%02u:%02u][#%u]\r\n",
                           h, m, s, counter++);
        int ret = sendMessage(payload);
        if (ret != STATUS_OK) {
            LOG_ERR("LoRa 发送失败: %d", ret);
        } else {
            LOG_INF("发送: %s", payload);
        }
        k_msleep(1000);
    }
}
