#include <zephyr/sys/printk.h>
#include "device_id.h"

/* 直接用裸指针读 FICR->DEVICEID[0]，不用 sys_read32 */
static inline uint32_t ficr_read32(uint32_t addr)
{
    return *(volatile uint32_t *)addr;
}

char bt_name[8];
uint16_t dev_id16 = 0;

void init_dev_name_and_id(void)
{
    /* nRF52840 FICR->DEVICEID[0] 地址：0x10000060 */
    uint32_t id0 = ficr_read32(0x10000060u);

    uint8_t b0 = (id0 >> 0) & 0xFF;
    uint8_t b1 = (id0 >> 8) & 0xFF;

    dev_id16 = ((uint16_t)b1 << 8) | b0;  /* eF%02X%02X mapping */

    snprintk(bt_name, sizeof(bt_name), "eF%02X%02X", b0, b1);
    /* 你如果想调试，也可以打印一下：
     * printk("DEV ID: 0x%04X, name=%s\n", dev_id16, bt_name);
     */
}
