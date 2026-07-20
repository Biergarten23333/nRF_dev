#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(biospur_fusion, LOG_LEVEL_INF);

#define LED0_NODE DT_ALIAS(led0)

#if !DT_NODE_HAS_STATUS(LED0_NODE, okay)
#error "The minimal bring-up application requires a devicetree led0 alias"
#endif

static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(LED0_NODE, gpios);

int main(void)
{
	int ret;

	if (!gpio_is_ready_dt(&led)) {
		LOG_ERR("LED GPIO is not ready");
		return 0;
	}

	ret = gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);
	if (ret != 0) {
		LOG_ERR("LED configuration failed: %d", ret);
		return 0;
	}

	LOG_INF("BioSpur Fusion B306 scaffold running");

	while (true) {
		ret = gpio_pin_toggle_dt(&led);
		if (ret != 0) {
			LOG_ERR("LED toggle failed: %d", ret);
			return 0;
		}
		k_sleep(K_SECONDS(1));
	}

	return 0;
}
