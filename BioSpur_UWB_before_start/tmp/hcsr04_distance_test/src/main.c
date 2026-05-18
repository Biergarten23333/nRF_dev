#include <errno.h>
#include <stdint.h>

#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

/*
 * DWM1001-DEV J10 header wiring:
 *   TRIG -> J10 pin 19 / SPI1_MOSI / nRF P0.06
 *   ECHO -> J10 pin 21 / SPI1_MISO / nRF P0.07, through a 10k/20k divider
 */
#define HCSR04_TRIG_PIN 6U
#define HCSR04_ECHO_PIN 7U

#define ECHO_TIMEOUT_US 30000U
#define SAMPLE_PERIOD_MS 100U

static uint32_t cycles_to_us(uint32_t cycles)
{
	return (uint32_t)k_cyc_to_us_floor64(cycles);
}

static int wait_for_echo_level(const struct device *gpio0, int level, uint32_t timeout_us)
{
	const uint32_t start = k_cycle_get_32();
	const uint32_t timeout_cycles = k_us_to_cyc_floor32(timeout_us);

	while ((uint32_t)(k_cycle_get_32() - start) < timeout_cycles) {
		int value = gpio_pin_get(gpio0, HCSR04_ECHO_PIN);
		if (value < 0) {
			return value;
		}
		if (value == level) {
			return 0;
		}
	}

	return -ETIMEDOUT;
}

static int measure_echo_us(const struct device *gpio0, uint32_t *echo_us)
{
	int ret;

	gpio_pin_set(gpio0, HCSR04_TRIG_PIN, 0);
	k_busy_wait(3);
	gpio_pin_set(gpio0, HCSR04_TRIG_PIN, 1);
	k_busy_wait(10);
	gpio_pin_set(gpio0, HCSR04_TRIG_PIN, 0);

	ret = wait_for_echo_level(gpio0, 1, ECHO_TIMEOUT_US);
	if (ret) {
		return ret;
	}

	const uint32_t rise = k_cycle_get_32();

	ret = wait_for_echo_level(gpio0, 0, ECHO_TIMEOUT_US);
	if (ret) {
		return ret;
	}

	const uint32_t fall = k_cycle_get_32();
	*echo_us = cycles_to_us(fall - rise);
	return 0;
}

int main(void)
{
	const struct device *gpio0 = DEVICE_DT_GET(DT_NODELABEL(gpio0));
	uint32_t seq = 0;

	if (!device_is_ready(gpio0)) {
		printk("HCSR04;ERR;gpio0_not_ready\n");
		return 0;
	}

	int ret = gpio_pin_configure(gpio0, HCSR04_TRIG_PIN, GPIO_OUTPUT_INACTIVE);
	if (ret) {
		printk("HCSR04;ERR;trig_config;%d\n", ret);
		return 0;
	}

	ret = gpio_pin_configure(gpio0, HCSR04_ECHO_PIN, GPIO_INPUT);
	if (ret) {
		printk("HCSR04;ERR;echo_config;%d\n", ret);
		return 0;
	}

	printk("HCSR04;BOOT;trig=P0.%02u;echo=P0.%02u;period_ms=%u\n",
	       HCSR04_TRIG_PIN, HCSR04_ECHO_PIN, SAMPLE_PERIOD_MS);

	while (1) {
		uint32_t echo_us = 0;
		ret = measure_echo_us(gpio0, &echo_us);
		if (ret == 0) {
			/* Speed of sound approx. 343 m/s:
			 * distance_mm = echo_us * 343 / 2000 for round trip.
			 */
			uint32_t distance_mm = (echo_us * 343U + 1000U) / 2000U;
			printk("DIST;%u;echo_us=%u;distance_mm=%u\n", seq, echo_us, distance_mm);
		} else {
			printk("DIST;%u;ERR;timeout_or_gpio;%d\n", seq, ret);
		}

		seq++;
		k_msleep(SAMPLE_PERIOD_MS);
	}
}
