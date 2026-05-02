#include "uwb_bringup.h"

#include <errno.h>
#include <stdint.h>

#include <deca_device_api.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>

#include "uwb_port.h"

#ifndef APP_TAG_USB_DIAG_TRACE
#define APP_TAG_USB_DIAG_TRACE 0U
#endif

/* Direct UART tracing is only useful for bringup diagnosis.
 * Leave it disabled by default to keep serial output parseable.
 */
#if APP_TAG_USB_DIAG_TRACE
static void direct_console_write(const char *msg)
{
    const struct device *console = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

    if (!device_is_ready(console) || msg == NULL) {
        return;
    }

    while (*msg != '\0') {
        uart_poll_out(console, *msg++);
    }
}
#endif

int uwb_hw_bringup_and_init(void)
{
    uint32_t dev_id = 0;
    int ret;

#if APP_TAG_USB_DIAG_TRACE
    direct_console_write("UWB_BRINGUP: enter\n");
#endif
    printk("DWM1001C minimal check start\n");

    ret = uwb_port_init();
    if (ret) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_BRINGUP: port_init failed\n");
#endif
        printk("uwb_port_init failed: %d\n", ret);
        return ret;
    }

    ret = uwb_port_hw_reset();
    if (ret) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_BRINGUP: hw_reset failed\n");
#endif
        printk("uwb_port_hw_reset failed: %d\n", ret);
        return ret;
    }

    ret = uwb_port_read_dev_id(&dev_id);
    if (ret) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_BRINGUP: read_dev_id failed\n");
#endif
        printk("uwb_port_read_dev_id failed: %d\n", ret);
        return ret;
    }

#if APP_TAG_USB_DIAG_TRACE
    direct_console_write("UWB_BRINGUP: dev_id read\n");
#endif
    printk("DW1000 DEV_ID: 0x%08x\n", dev_id);

    if (dev_id != UWB_DW1000_DEVICE_ID) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_BRINGUP: unexpected dev_id\n");
#endif
        printk("Unexpected DEV_ID, wiring/SPI/reset likely wrong\n");
        return -EIO;
    }

#if APP_TAG_USB_DIAG_TRACE
    direct_console_write("UWB_BRINGUP: dev_id ok\n");
#endif
    printk("DWM1001C SPI check OK\n");

    ret = uwb_port_set_spi_slow();
    if (ret) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_BRINGUP: set_spi_slow failed\n");
#endif
        printk("uwb_port_set_spi_slow failed: %d\n", ret);
        return ret;
    }

    ret = dwt_initialise(DWT_LOADUCODE);
    if (ret == DWT_ERROR) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_BRINGUP: dwt_initialise failed\n");
#endif
        printk("dwt_initialise failed\n");
        return -EIO;
    }

    ret = uwb_port_set_spi_fast();
    if (ret) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_BRINGUP: set_spi_fast failed\n");
#endif
        printk("uwb_port_set_spi_fast failed: %d\n", ret);
        return ret;
    }

#if APP_TAG_USB_DIAG_TRACE
    direct_console_write("UWB_BRINGUP: done\n");
#endif
    printk("dwt_initialise OK\n");
    return 0;
}
