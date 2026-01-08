#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/logging/log.h>
#include <zephyr/devicetree.h>
#include "cc1101.h"

LOG_MODULE_REGISTER(main);

static struct cc1101_config module_a_config = {
    .spi = DEVICE_DT_GET(DT_NODELABEL(spi2)),
    .cs_ctrl = {
        .gpio_dev = DEVICE_DT_GET(DT_NODELABEL(gpio2)),
        .gpio_pin = 5, // CSN for Module A
        .delay = 0,
    },
    .gdo0 = GPIO_DT_SPEC_GET(DT_NODELABEL(gdo0_a), gpios),
    .gdo2 = GPIO_DT_SPEC_GET(DT_NODELABEL(gdo2_a), gpios),
};

static struct cc1101_config module_b_config = {
    .spi = DEVICE_DT_GET(DT_NODELABEL(spi2)),
    .cs_ctrl = {
        .gpio_dev = DEVICE_DT_GET(DT_NODELABEL(gpio2)),
        .gpio_pin = 10, // CSN for Module B
        .delay = 0,
    },
    .gdo0 = GPIO_DT_SPEC_GET(DT_NODELABEL(gdo0_b), gpios),
    .gdo2 = GPIO_DT_SPEC_GET(DT_NODELABEL(gdo2_b), gpios),
};

static const struct gpio_dt_spec led0 = GPIO_DT_SPEC_GET(DT_NODELABEL(led0), gpios); // P2.09
static const struct gpio_dt_spec led1 = GPIO_DT_SPEC_GET(DT_NODELABEL(led1), gpios); // P1.10

void main(void) {
    uint8_t status_a, status_b;

    // 初始化 LED
    gpio_pin_configure(led0.port, led0.pin, GPIO_OUTPUT_ACTIVE);
    gpio_pin_configure(led1.port, led1.pin, GPIO_OUTPUT_ACTIVE);

    gpio_pin_set(led0.port, led0.pin, 0);
    gpio_pin_set(led1.port, led1.pin, 0);

    // 初始化 CC1101 模块
    if (cc1101_init(&module_a_config) != 0) {
        LOG_ERR("模块 A 初始化失败");
        return;
    }
    if (cc1101_init(&module_b_config) != 0) {
        LOG_ERR("模块 B 初始化失败");
        return;
    }

    // 测试模块 A
    if (cc1101_test_status(&module_a_config, &status_a) == 0) {
        LOG_INF("模块 A 状态: 0x%02X", status_a);
        gpio_pin_set(led0.port, led0.pin, 1); // 如果模块 A 工作，点亮 LED0
    } else {
        LOG_ERR("无法获取模块 A 的状态");
    }

    // 测试模块 B
    if (cc1101_test_status(&module_b_config, &status_b) == 0) {
        LOG_INF("模块 B 状态: 0x%02X", status_b);
        gpio_pin_set(led1.port, led1.pin, 1); // 如果模块 B 工作，点亮 LED1
    } else {
        LOG_ERR("无法获取模块 B 的状态");
    }
}
