#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/dfu/mcuboot.h>

void start_smp_bluetooth_adverts(void);

int main(void)
{
	int confirmed;
	int rc;

	printk("Tag OTA SMP server starting\n");
	confirmed = boot_is_img_confirmed();
	printk("Tag OTA boot confirmed: %d\n", confirmed);
	if (!confirmed) {
		rc = boot_write_img_confirmed();
		if (rc) {
			printk("Tag OTA confirm failed: %d\n", rc);
		} else {
			printk("Tag OTA image confirmed\n");
		}
	}

	start_smp_bluetooth_adverts();
	printk("Tag OTA ready: BLE SMP transport + MCUboot\n");

	while (1) {
		k_sleep(K_SECONDS(1));
	}

	return 0;
}
