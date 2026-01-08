#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/logging/log.h>
#include <string.h>

LOG_MODULE_REGISTER(lora_cfg, LOG_LEVEL_INF);

/* 设备名 & 引脚 */
#define UART0_NAME      "uart0"
#define GPIO1_NAME      "gpio1"
#define M0_PIN           7   /* P1.07 */
#define M1_PIN           8   /* P1.08 */
#define AUX_PIN         10   /* P1.10 */

/* 配置命令 & 长度 */
#define CMD_WRITE_CFG   0xC0  /* WRITE_CFG_PWR_DWN_SAVE */
#define CMD_READ_CFG    0xC1  /* READ_CONFIGURATION */
#define ADR_CFG         0x00
#define LEN_CFG         0x08
#define CFG_BYTES      (3 + LEN_CFG)  /* Header(3) + Config(8) */

/* AIR 数据率枚举：111→625bps */
#define AIR_DATA_RATE_111_625 0x07

/* 全局设备句柄 */
static const struct device *uart_dev;
static const struct device *gpio_dev;

/* 进入 PROGRAM 模式：M0=1, M1=1，延时稳定 */
static void enter_prog(void)
{
    gpio_pin_set(gpio_dev, M0_PIN, 1);
    gpio_pin_set(gpio_dev, M1_PIN, 1);
    k_msleep(60);
}

/* 恢复 NORMAL 模式：M0=M1=0，延时稳定 */
static void enter_normal(void)
{
    gpio_pin_set(gpio_dev, M0_PIN, 0);
    gpio_pin_set(gpio_dev, M1_PIN, 0);
    k_msleep(50);
}

/* 等待 AUX 拉高（超时 1000ms），返回 true=成功 */
static bool wait_aux_high(void)
{
    int64_t start = k_uptime_get();
    while (gpio_pin_get(gpio_dev, AUX_PIN) == 0) {
        if (k_uptime_get() - start > 1000) {
            LOG_ERR("等待 AUX 超时");
            return false;
        }
        k_msleep(10);
    }
    k_msleep(20);
    return true;
}

/* 从 UART 读取定长字节，超时后返回已读长度 */
static size_t uart_read_bytes(uint8_t *buf, size_t len, size_t timeout_ms)
{
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

/* 打印分隔线 */
static void print_separator(void)
{
    LOG_INF("----------------------------------------");
}
/* 在 print_cfg 之前加入： */
static void byte_to_binary(uint8_t byte, char *out)
{
    for (int i = 0; i < 8; i++) {
        out[i] = (byte & (1 << (7 - i))) ? '1' : '0';
    }
    out[8] = '\0';
}
/* 打印 11 字节配置的关键信息 */
static void print_cfg(const uint8_t *cfg)
{
    LOG_INF("HEAD : 0x%02X 0x%02X 0x%02X", cfg[0], cfg[1], cfg[2]);
    LOG_INF("AddH : 0x%02X  AddL : 0x%02X", cfg[3], cfg[4]);

    uint8_t sped = cfg[5];
    uint8_t opt  = cfg[6];
    uint8_t chan = cfg[7];
    uint8_t tm   = cfg[8];

    LOG_INF("SPED       : 0x%02X (AirRate=0x%X)", sped, sped & 0x07);
    LOG_INF("OPTION     : 0x%02X", opt);
    LOG_INF("CHANNEL    : 0x%02X -> %d MHz", chan, 850 + chan);
    LOG_INF("TRANS_MODE : 0x%02X", tm);
    LOG_INF("已设置：ADDH=0x%02X, ADDL=0x%02X, SPED=0x%02X, OPTION=0x%02X, CHAN=0x%02X, TRANS_MODE=0x%02X",
            cfg[3],   cfg[4],
            cfg[5],   cfg[6],
            cfg[7],   cfg[8]);

    LOG_INF("========================================");

    /* 新增：按字节打印二进制 */
    {
        char bin[9];
        for (int i = 0; i < 9; i++) {
            byte_to_binary(cfg[i], bin);
            LOG_INF("  cfg[%d] = %s", i, bin);
        }
    }

    LOG_INF("配置已打印完毕");
}

void main(void)
{
    uint8_t cfg[CFG_BYTES];
    uint8_t resp[CFG_BYTES];
    size_t got;

    /* 绑定设备 */
    uart_dev = device_get_binding(UART0_NAME);
    gpio_dev = device_get_binding(GPIO1_NAME);
    if (!device_is_ready(uart_dev) || !device_is_ready(gpio_dev)) {
        LOG_ERR("UART 或 GPIO 设备未就绪");
        return;
    }

    /* 配置引脚 */
    gpio_pin_configure(gpio_dev, M0_PIN, GPIO_OUTPUT);
    gpio_pin_configure(gpio_dev, M1_PIN, GPIO_OUTPUT);
    gpio_pin_configure(gpio_dev, AUX_PIN, GPIO_INPUT | GPIO_PULL_UP);

    /***** 1. 读取初始配置 *****/
    enter_prog();
    /* 送出读命令头 */
    {
        uint8_t cmd_rd[3] = { CMD_READ_CFG, ADR_CFG, LEN_CFG };
        for (int i = 0; i < 3; i++) {
            uart_poll_out(uart_dev, cmd_rd[i]);
        }
    }
    if (!wait_aux_high()) {
        enter_normal();
        return;
    }
    got = uart_read_bytes(cfg, CFG_BYTES, 500);
    print_separator();
    LOG_INF("【初次读出 %u 字节】", (unsigned)got);
    print_cfg(cfg);
    print_separator();
    enter_normal();

    /***** 2. 修改 SPED 字节：保留高位，仅改 AirRate = 111 *****/
    {
        /* 2.2 修改地址和频道 */
        cfg[3] = 0x00;  /* ADDH */
        cfg[4] = 0x03;  /* ADDL */
        cfg[5] = 0xE3;  /* SPED: 9600bps, 8N1, AirRate=62.5kbps==>0b11100017 */
        cfg[6] = 0x00;
        cfg[7] = 23;    /* CHAN=23 */
        cfg[8] = 0x70; /*0b01110000*/
        LOG_INF("已设置 ADDH=0x%02X, ADDL=0x%02X, CHAN=0x%02X",
                cfg[3], cfg[4], cfg[7]);
    }

    /***** 3. 写回新配置 *****/
    enter_prog();
    /* 构造写命令帧 */
    {
        uint8_t cmd_wr[CFG_BYTES];
        cmd_wr[0] = CMD_WRITE_CFG;
        cmd_wr[1] = ADR_CFG;
        cmd_wr[2] = LEN_CFG;
        memcpy(&cmd_wr[3], &cfg[3], LEN_CFG);
        for (int i = 0; i < CFG_BYTES; i++) {
            uart_poll_out(uart_dev, cmd_wr[i]);
        }
    }
    if (!wait_aux_high()) {
        enter_normal();
        return;
    }
    got = uart_read_bytes(resp, CFG_BYTES, 500);
    print_separator();
    LOG_INF("【写入后返回 %u 字节】", (unsigned)got);
    print_cfg(resp);
    print_separator();
    enter_normal();

    /***** 4. 再次读取，确认生效 *****/
    enter_prog();
    {
        uint8_t cmd_rd[3] = { CMD_READ_CFG, ADR_CFG, LEN_CFG };
        for (int i = 0; i < 3; i++) {
            uart_poll_out(uart_dev, cmd_rd[i]);
        }
    }
    if (!wait_aux_high()) {
        enter_normal();
        return;
    }
    got = uart_read_bytes(cfg, CFG_BYTES, 500);
    print_separator();
    LOG_INF("【二次读出 %u 字节】", (unsigned)got);
    print_cfg(cfg);
    print_separator();
    enter_normal();

    /* 主循环保持运行 */
    while (1) {
        k_sleep(K_SECONDS(10));
    }
}