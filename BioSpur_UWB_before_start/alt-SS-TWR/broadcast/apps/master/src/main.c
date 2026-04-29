#include <zephyr/kernel.h>
#if defined(CONFIG_USB_DEVICE_STACK)
#include <zephyr/usb/usb_device.h>
#include <zephyr/sys/printk.h>
#endif

int master_app_run(void);

int main(void)
{
#if defined(CONFIG_USB_DEVICE_STACK)
	int rc = usb_enable(NULL);
	if (rc != 0) {
		printk("USB CDC enable failed: %d\n", rc);
	}
#endif
    return master_app_run();
}
