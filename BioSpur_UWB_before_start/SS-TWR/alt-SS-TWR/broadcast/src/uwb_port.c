#include "uwb_port.h"

#include <deca_device_api.h>

#include <errno.h>
#include <stddef.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/irq.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/printk.h>

#ifndef APP_TAG_USB_DIAG_TRACE
#define APP_TAG_USB_DIAG_TRACE 0U
#endif

#define DW1000_NODE DT_NODELABEL(ieee802154)
#define UWB_SPI_BUF_SIZE 1024U

#if !DT_NODE_EXISTS(DW1000_NODE)
#error "Missing DW1000 node. Use board decawave_dwm1001_dev or add a compatible dw1000 node."
#endif

#if !DT_NODE_HAS_STATUS(DW1000_NODE, okay)
#error "DW1000 node is disabled."
#endif

#if !DT_NODE_HAS_PROP(DW1000_NODE, reset_gpios)
#error "Missing reset-gpios on DW1000 node."
#endif

#if !DT_NODE_HAS_PROP(DW1000_NODE, int_gpios)
#error "Missing int-gpios on DW1000 node."
#endif

static const struct gpio_dt_spec uwb_reset =
    GPIO_DT_SPEC_GET(DW1000_NODE, reset_gpios);
static const struct gpio_dt_spec uwb_irq =
    GPIO_DT_SPEC_GET(DW1000_NODE, int_gpios);

#define UWB_SPI_OPERATION \
    (SPI_OP_MODE_MASTER | SPI_WORD_SET(8) | SPI_TRANSFER_MSB)

static const struct device *uwb_spi_bus = DEVICE_DT_GET(DT_BUS(DW1000_NODE));
static struct spi_config uwb_spi_slow_cfg =
    SPI_CONFIG_DT(DW1000_NODE, UWB_SPI_OPERATION, 0U);
static struct spi_config uwb_spi_fast_cfg =
    SPI_CONFIG_DT(DW1000_NODE, UWB_SPI_OPERATION, 0U);
static const struct spi_config *uwb_spi_cfg = &uwb_spi_slow_cfg;

static uint8_t uwb_spi_tx_buf[UWB_SPI_BUF_SIZE];
static uint8_t uwb_spi_rx_buf[UWB_SPI_BUF_SIZE];

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

static int uwb_port_set_spi_frequency(uint32_t frequency)
{
    if (frequency <= 2000000U) {
        uwb_spi_cfg = &uwb_spi_slow_cfg;
    } else {
        uwb_spi_cfg = &uwb_spi_fast_cfg;
    }

    return 0;
}

static int uwb_spi_transfer(const uint8_t *tx_data, uint8_t *rx_data, size_t len)
{
    struct spi_buf tx_buf = {
        .buf = (void *)tx_data,
        .len = len,
    };
    struct spi_buf_set tx_set = {
        .buffers = &tx_buf,
        .count = 1U,
    };
    struct spi_buf rx_buf = {
        .buf = rx_data,
        .len = len,
    };
    struct spi_buf_set rx_set = {
        .buffers = &rx_buf,
        .count = 1U,
    };

    if (len > UWB_SPI_BUF_SIZE) {
        return -ENOMEM;
    }

    int ret = spi_transceive(uwb_spi_bus, uwb_spi_cfg, &tx_set, &rx_set);
    if (ret) {
        printk("uwb_spi_transfer failed: %d len=%u\n", ret, (unsigned int)len);
    }

    return ret;
}

static int uwb_spi_read_reg(uint8_t reg, uint8_t *buf, size_t len)
{
    if (len > 4U || buf == NULL) {
        return -EINVAL;
    }

    memset(uwb_spi_tx_buf, 0, len + 1U);
    memset(uwb_spi_rx_buf, 0, len + 1U);
    uwb_spi_tx_buf[0] = reg;

    int ret = uwb_spi_transfer(uwb_spi_tx_buf, uwb_spi_rx_buf, len + 1U);
    if (ret) {
        return ret;
    }

    memcpy(buf, &uwb_spi_rx_buf[1], len);
    return 0;
}

int uwb_port_init(void)
{
#if APP_TAG_USB_DIAG_TRACE
    direct_console_write("UWB_PORT: enter\n");
#endif
    printk("uwb_port_init: start\n");

    if (!device_is_ready(uwb_spi_bus)) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_PORT: spi bus not ready\n");
#endif
        printk("uwb_port_init: spi bus not ready\n");
        return -ENODEV;
    }

    if (!gpio_is_ready_dt(&uwb_reset) || !gpio_is_ready_dt(&uwb_irq)) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_PORT: gpio not ready\n");
#endif
        printk("uwb_port_init: gpio not ready\n");
        return -ENODEV;
    }

    uwb_spi_slow_cfg.frequency = 2000000U;
    uwb_spi_fast_cfg.frequency = 8000000U;
    uwb_spi_cfg = &uwb_spi_slow_cfg;

    int ret = gpio_pin_configure_dt(&uwb_irq, GPIO_INPUT);
    if (ret) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_PORT: irq configure failed\n");
#endif
        printk("uwb_port_init: irq configure failed %d\n", ret);
        return ret;
    }
#if APP_TAG_USB_DIAG_TRACE
    direct_console_write("UWB_PORT: irq configured\n");
#endif
    printk("uwb_port_init: irq configured\n");

    ret = gpio_pin_configure_dt(&uwb_reset, GPIO_OUTPUT_INACTIVE);
    if (ret) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_PORT: reset configure failed\n");
#endif
        printk("uwb_port_init: reset configure failed %d\n", ret);
        return ret;
    }
#if APP_TAG_USB_DIAG_TRACE
    direct_console_write("UWB_PORT: reset configured\n");
#endif
    printk("uwb_port_init: reset configured\n");

#if APP_TAG_USB_DIAG_TRACE
    direct_console_write("UWB_PORT: done\n");
#endif
    printk("uwb_port_init: done\n");
    return 0;
}

int uwb_port_hw_reset(void)
{
#if APP_TAG_USB_DIAG_TRACE
    direct_console_write("UWB_PORT: hw_reset enter\n");
#endif
    printk("uwb_port_hw_reset: start\n");

    int ret = gpio_pin_configure_dt(&uwb_reset, GPIO_OUTPUT_ACTIVE);
    if (ret) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_PORT: hw_reset active failed\n");
#endif
        printk("uwb_port_hw_reset: active failed %d\n", ret);
        return ret;
    }

    k_msleep(2);

    ret = gpio_pin_configure_dt(&uwb_reset, GPIO_OUTPUT_INACTIVE);
    if (ret) {
#if APP_TAG_USB_DIAG_TRACE
        direct_console_write("UWB_PORT: hw_reset inactive failed\n");
#endif
        printk("uwb_port_hw_reset: inactive failed %d\n", ret);
        return ret;
    }

    k_msleep(5);
#if APP_TAG_USB_DIAG_TRACE
    direct_console_write("UWB_PORT: hw_reset done\n");
#endif
    printk("uwb_port_hw_reset: done\n");
    return 0;
}

int uwb_port_read_dev_id(uint32_t *dev_id)
{
    uint8_t raw[4];
    int ret;

    if (dev_id == NULL) {
        return -EINVAL;
    }

    ret = uwb_spi_read_reg(0x00, raw, sizeof(raw));
    if (ret) {
        return ret;
    }

    *dev_id = sys_get_le32(raw);
    return 0;
}

int uwb_port_set_spi_slow(void)
{
    return uwb_port_set_spi_frequency(2000000U);
}

int uwb_port_set_spi_fast(void)
{
    return uwb_port_set_spi_frequency(8000000U);
}

int openspi(void)
{
    return uwb_port_init();
}

int closespi(void)
{
    return 0;
}

void set_spi_speed_slow(void)
{
    (void)uwb_port_set_spi_slow();
}

void set_spi_speed_fast(void)
{
    (void)uwb_port_set_spi_fast();
}

int writetospi(uint16 headerLength, const uint8 *headerBuffer, uint32 bodylength,
               const uint8 *bodyBuffer)
{
    const size_t total_len = (size_t)headerLength + (size_t)bodylength;
    decaIrqStatus_t irq_state;
    int ret;

    if (headerBuffer == NULL || (bodylength != 0U && bodyBuffer == NULL)) {
        return -EINVAL;
    }

    if (total_len > UWB_SPI_BUF_SIZE) {
        return -ENOMEM;
    }

    memset(uwb_spi_tx_buf, 0, total_len);
    memset(uwb_spi_rx_buf, 0, total_len);
    memcpy(uwb_spi_tx_buf, headerBuffer, headerLength);
    if (bodylength != 0U) {
        memcpy(uwb_spi_tx_buf + headerLength, bodyBuffer, bodylength);
    }

    irq_state = decamutexon();
    ret = uwb_spi_transfer(uwb_spi_tx_buf, uwb_spi_rx_buf, total_len);
    decamutexoff(irq_state);
    return ret;
}

int readfromspi(uint16 headerLength, const uint8 *headerBuffer, uint32 readlength,
                uint8 *readBuffer)
{
    const size_t total_len = (size_t)headerLength + (size_t)readlength;
    decaIrqStatus_t irq_state;
    int ret;

    if (headerBuffer == NULL || (readlength != 0U && readBuffer == NULL)) {
        return -EINVAL;
    }

    if (total_len > UWB_SPI_BUF_SIZE) {
        return -ENOMEM;
    }

    memset(uwb_spi_tx_buf, 0, total_len);
    memset(uwb_spi_rx_buf, 0, total_len);
    memcpy(uwb_spi_tx_buf, headerBuffer, headerLength);

    irq_state = decamutexon();
    ret = uwb_spi_transfer(uwb_spi_tx_buf, uwb_spi_rx_buf, total_len);
    decamutexoff(irq_state);
    if (ret) {
        return ret;
    }

    if (readlength != 0U) {
        memcpy(readBuffer, uwb_spi_rx_buf + headerLength, readlength);
    }
    return 0;
}

decaIrqStatus_t decamutexon(void)
{
    return (decaIrqStatus_t)irq_lock();
}

void decamutexoff(decaIrqStatus_t state)
{
    irq_unlock((unsigned int)state);
}

void deca_sleep(unsigned int time_ms)
{
    k_msleep(time_ms);
}
