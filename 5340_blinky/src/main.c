#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

int main(void)
{
    uint32_t counter = 0;
    printk("boot\n");

    while (1) {
        counter++;
        printk("%u\n", counter);
        k_sleep(K_SECONDS(1));
    }
}
