#include "uwb_bringup.h"

#include <errno.h>
#include <stdint.h>

#include <deca_device_api.h>
#include <zephyr/sys/printk.h>

#include "uwb_port.h"

int uwb_hw_bringup_and_init(void)
{
    uint32_t dev_id = 0;
    int ret;

    printk("DWM1001C minimal check start\n");

    ret = uwb_port_init();
    if (ret) {
        printk("uwb_port_init failed: %d\n", ret);
        return ret;
    }

    ret = uwb_port_hw_reset();
    if (ret) {
        printk("uwb_port_hw_reset failed: %d\n", ret);
        return ret;
    }

    ret = uwb_port_read_dev_id(&dev_id);
    if (ret) {
        printk("uwb_port_read_dev_id failed: %d\n", ret);
        return ret;
    }

    printk("DW1000 DEV_ID: 0x%08x\n", dev_id);

    if (dev_id != UWB_DW1000_DEVICE_ID) {
        printk("Unexpected DEV_ID, wiring/SPI/reset likely wrong\n");
        return -EIO;
    }

    printk("DWM1001C SPI check OK\n");

    ret = uwb_port_set_spi_slow();
    if (ret) {
        printk("uwb_port_set_spi_slow failed: %d\n", ret);
        return ret;
    }

    ret = dwt_initialise(DWT_LOADUCODE);
    if (ret == DWT_ERROR) {
        printk("dwt_initialise failed\n");
        return -EIO;
    }

    ret = uwb_port_set_spi_fast();
    if (ret) {
        printk("uwb_port_set_spi_fast failed: %d\n", ret);
        return ret;
    }

    printk("dwt_initialise OK\n");
    return 0;
}
