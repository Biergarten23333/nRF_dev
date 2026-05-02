#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

void start_smp_bluetooth_adverts(void);

int main(void)
{
	printk("Tag OTA SMP server starting\n");

	start_smp_bluetooth_adverts();
	printk("Tag OTA ready: BLE SMP transport + MCUboot\n");

	while (1) {
		k_sleep(K_SECONDS(1));
	}

	return 0;
}
