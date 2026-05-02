#include <errno.h>
#include <stddef.h>
#include <stdint.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/entropy.h>

static const struct device *const entropy_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_entropy));

int bt_rand(void *buf, size_t len)
{
	if (!device_is_ready(entropy_dev)) {
		return -ENODEV;
	}

	return entropy_get_entropy(entropy_dev, (uint8_t *)buf, len);
}
