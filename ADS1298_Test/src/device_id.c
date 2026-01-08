/* src/device_id.c */

#include "app_config.h"
#include "device_id.h"

/* Device name / ID state (was static in main.c) */
static char     bt_name[8];
static uint16_t dev_id16 = 0;

void device_id_init(void)
{
    uint32_t id0 = sys_read32(0x10000060);
    uint8_t b0 = (id0 >> 0) & 0xFF;
    uint8_t b1 = (id0 >> 8) & 0xFF;
    dev_id16 = ((uint16_t)b1 << 8) | b0;
    snprintk(bt_name, sizeof(bt_name), "ES%02X%02X", b0, b1);
}

uint16_t device_id_get16(void)
{
    return dev_id16;
}

const char *device_bt_name_get(void)
{
    return bt_name;
}
