/* cc1101_driver.h */
#ifndef CC1101_DRIVER_H
#define CC1101_DRIVER_H

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/drivers/gpio.h>


#define CC1101_SRES       0x30
#define CC1101_PARTNUM    0x30
#define CC1101_VERSION    0x31

struct cc1101_config {
    const struct device *spi;
    struct spi_cs_control cs_ctrl;
    const struct gpio_dt_spec gdo0;
    const struct gpio_dt_spec gdo2;
};

int cc1101_init(const struct cc1101_config *config);
int cc1101_test_status(const struct cc1101_config *config, uint8_t *status);

#endif
