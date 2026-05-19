#include "device_id.h"

#include <zephyr/kernel.h>
#include <zephyr/sys/sys_io.h>
#include <zephyr/sys/printk.h>

static char bt_name[8];
static uint16_t dev_id16;

void device_id_init(void)
{
	uint32_t id0 = sys_read32(0x10000060);
	uint8_t b0 = (uint8_t)(id0 & 0xffU);
	uint8_t b1 = (uint8_t)((id0 >> 8) & 0xffU);

	dev_id16 = ((uint16_t)b1 << 8) | b0;
	snprintk(bt_name, sizeof(bt_name), "GR%02X%02X", b0, b1);
}

uint16_t device_id_get16(void)
{
	return dev_id16;
}

const char *device_bt_name_get(void)
{
	return bt_name;
}

