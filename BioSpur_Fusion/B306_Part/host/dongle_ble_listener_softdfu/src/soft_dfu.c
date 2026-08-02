#include <stdint.h>

#include <hal/nrf_gpio.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart/cdc_acm.h>
#include <zephyr/drivers/usb/usb_dc.h>
#include <zephyr/init.h>

#define DFU_TOUCH_BAUD 1200U
#define PCA10059_SELF_RESET_PIN NRF_GPIO_PIN_MAP(0, 19)

static const struct device *const cdc_console =
	DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

static void dte_rate_changed(const struct device *dev, uint32_t rate)
{
	ARG_UNUSED(dev);

	if (rate != DFU_TOUCH_BAUD) {
		return;
	}

	/*
	 * Handle the USB class event itself instead of sampling the current baud
	 * every 100 ms. A short-lived 1200-baud touch can otherwise be missed.
	 *
	 * The stock PCA10059 Open USB bootloader enters DFU on pin reset. P0.19
	 * is physically wired back to RESET on this board, so driving it low is
	 * the software-controlled equivalent of pressing the reset button. A
	 * plain NVIC reset (even with GPREGRET=0xB1) does not hold this stock
	 * bootloader in DFU.
	 */
	(void)usb_dc_detach();
	nrf_gpio_cfg_output(PCA10059_SELF_RESET_PIN);
	nrf_gpio_pin_clear(PCA10059_SELF_RESET_PIN);
}

static int soft_dfu_init(void)
{
	if (!device_is_ready(cdc_console)) {
		return -ENODEV;
	}

	return cdc_acm_dte_rate_callback_set(cdc_console, dte_rate_changed);
}

SYS_INIT(soft_dfu_init, APPLICATION, 0);
