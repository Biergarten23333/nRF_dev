#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/printk.h>

#define SLEEP_TIME_MS 500

static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);

int main(void)
{
    int ret;
    bool on = false;

    if (!gpio_is_ready_dt(&led)) {
        printk("LED device not ready\n");
        return 0;
    }

    ret = gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);
    if (ret < 0) {
        printk("LED config failed: %d\n", ret);
        return 0;
    }

    printk("Blinky start\n");

    while (1) {
        on = !on;
        gpio_pin_set_dt(&led, on);
        k_msleep(SLEEP_TIME_MS);
    }
}
