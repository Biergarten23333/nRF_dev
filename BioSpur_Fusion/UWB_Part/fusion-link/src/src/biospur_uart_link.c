#include "biospur_uart_link.h"

#include <errno.h>
#include <stddef.h>
#include <string.h>

#include <hal/nrf_gpio.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/util.h>

#define BSL_UART_NODE DT_NODELABEL(uart0)
#define BSL_STROBE_PIN NRF_GPIO_PIN_MAP(0, 26)
#define BSL_STROBE_WIDTH_US 10U
#define BSL_TX_ABORT_TIMEOUT_MS 20U

BUILD_ASSERT(DT_NODE_HAS_STATUS(BSL_UART_NODE, okay),
	     "BioSpur UART requires enabled uart0");
BUILD_ASSERT(DT_PROP(BSL_UART_NODE, current_speed) == BSL_BAUDRATE,
	     "BioSpur UART baudrate must match biospur_link.h");

static const struct device *const bsl_uart = DEVICE_DT_GET(BSL_UART_NODE);
static bsl_frame_t bsl_tx_frame;
static struct k_spinlock bsl_tx_lock;
static K_SEM_DEFINE(bsl_tx_idle, 0, 1);

static atomic_t bsl_ready;
static atomic_t bsl_active;
static atomic_t bsl_tx_busy;
static atomic_t bsl_frames_generated;
static atomic_t bsl_tx_started;
static atomic_t bsl_tx_completed;
static atomic_t bsl_tx_dropped;
static atomic_t bsl_tx_failed;
static atomic_t bsl_tx_aborted;
static atomic_t bsl_strobe_count;
static atomic_t bsl_last_tx_error;

static uint16_t bsl_crc16_ccitt_false(const uint8_t *data, size_t len)
{
	uint16_t crc = 0xffffU;

	for (size_t i = 0U; i < len; ++i) {
		crc ^= (uint16_t)data[i] << 8;
		for (uint8_t bit = 0U; bit < 8U; ++bit) {
			crc = (crc & 0x8000U) != 0U ?
				      (uint16_t)((crc << 1) ^ 0x1021U) :
				      (uint16_t)(crc << 1);
		}
	}

	return crc;
}

static void bsl_uart_callback(const struct device *dev,
			      struct uart_event *evt,
			      void *user_data)
{
	ARG_UNUSED(dev);
	ARG_UNUSED(user_data);

	switch (evt->type) {
	case UART_TX_DONE:
		atomic_inc(&bsl_tx_completed);
		atomic_clear(&bsl_tx_busy);
		k_sem_give(&bsl_tx_idle);
		break;
	case UART_TX_ABORTED:
		atomic_inc(&bsl_tx_aborted);
		atomic_clear(&bsl_tx_busy);
		k_sem_give(&bsl_tx_idle);
		break;
	default:
		break;
	}
}

int biospur_uart_link_init(void)
{
	int err;

	nrf_gpio_pin_clear(BSL_STROBE_PIN);
	nrf_gpio_cfg_output(BSL_STROBE_PIN);
	nrf_gpio_pin_clear(BSL_STROBE_PIN);

	if (!device_is_ready(bsl_uart)) {
		return -ENODEV;
	}

	err = uart_callback_set(bsl_uart, bsl_uart_callback, NULL);
	if (err != 0) {
		return err;
	}

	atomic_set(&bsl_active, 1);
	atomic_set(&bsl_ready, 1);
	return 0;
}

int biospur_uart_link_submit(const bsl_uwb_t *body)
{
	k_spinlock_key_t key;
	int err;

	if (body == NULL) {
		return -EINVAL;
	}

	atomic_inc(&bsl_frames_generated);
	if (atomic_get(&bsl_ready) == 0 || atomic_get(&bsl_active) == 0) {
		atomic_inc(&bsl_tx_dropped);
		atomic_set(&bsl_last_tx_error, -ESHUTDOWN);
		return -ESHUTDOWN;
	}

	if (!atomic_cas(&bsl_tx_busy, 0, 1)) {
		atomic_inc(&bsl_tx_dropped);
		atomic_set(&bsl_last_tx_error, -EBUSY);
		return -EBUSY;
	}

	key = k_spin_lock(&bsl_tx_lock);
	bsl_tx_frame.hdr.magic0 = BSL_MAGIC0;
	bsl_tx_frame.hdr.magic1 = BSL_MAGIC1;
	bsl_tx_frame.hdr.version = BSL_VERSION;
	bsl_tx_frame.hdr.len = (uint8_t)sizeof(bsl_uwb_t);
	bsl_tx_frame.body = *body;
	bsl_tx_frame.crc = bsl_crc16_ccitt_false(
		(const uint8_t *)&bsl_tx_frame,
		offsetof(bsl_frame_t, crc));
	k_spin_unlock(&bsl_tx_lock, key);

	k_sem_reset(&bsl_tx_idle);
	err = uart_tx(bsl_uart, (const uint8_t *)&bsl_tx_frame,
		      BSL_FRAME_LEN, SYS_FOREVER_US);
	if (err != 0) {
		atomic_clear(&bsl_tx_busy);
		atomic_inc(&bsl_tx_failed);
		atomic_set(&bsl_last_tx_error, err);
		k_sem_give(&bsl_tx_idle);
		return err;
	}

	atomic_inc(&bsl_tx_started);
	return 0;
}

bool biospur_uart_link_strobe_pulse(void)
{
	if (atomic_get(&bsl_ready) == 0 || atomic_get(&bsl_active) == 0) {
		nrf_gpio_pin_clear(BSL_STROBE_PIN);
		return false;
	}

	nrf_gpio_pin_set(BSL_STROBE_PIN);
	k_busy_wait(BSL_STROBE_WIDTH_US);
	nrf_gpio_pin_clear(BSL_STROBE_PIN);
	atomic_inc(&bsl_strobe_count);
	return true;
}

int biospur_uart_link_suspend(void)
{
	int err;

	atomic_clear(&bsl_active);
	nrf_gpio_pin_clear(BSL_STROBE_PIN);

	if (atomic_get(&bsl_ready) == 0 || atomic_get(&bsl_tx_busy) == 0) {
		return 0;
	}

	err = uart_tx_abort(bsl_uart);
	if (err != 0 && err != -EFAULT) {
		atomic_set(&bsl_last_tx_error, err);
		return err;
	}

	if (atomic_get(&bsl_tx_busy) != 0 &&
	    k_sem_take(&bsl_tx_idle, K_MSEC(BSL_TX_ABORT_TIMEOUT_MS)) != 0) {
		atomic_set(&bsl_last_tx_error, -ETIMEDOUT);
		return -ETIMEDOUT;
	}

	return 0;
}

void biospur_uart_link_resume(void)
{
	nrf_gpio_pin_clear(BSL_STROBE_PIN);
	if (atomic_get(&bsl_ready) != 0) {
		atomic_set(&bsl_active, 1);
	}
}

bool biospur_uart_link_is_active(void)
{
	return atomic_get(&bsl_ready) != 0 && atomic_get(&bsl_active) != 0;
}

void biospur_uart_link_get_stats(struct biospur_uart_link_stats *stats)
{
	if (stats == NULL) {
		return;
	}

	stats->frames_generated = (uint32_t)atomic_get(&bsl_frames_generated);
	stats->tx_started = (uint32_t)atomic_get(&bsl_tx_started);
	stats->tx_completed = (uint32_t)atomic_get(&bsl_tx_completed);
	stats->tx_dropped = (uint32_t)atomic_get(&bsl_tx_dropped);
	stats->tx_failed = (uint32_t)atomic_get(&bsl_tx_failed);
	stats->tx_aborted = (uint32_t)atomic_get(&bsl_tx_aborted);
	stats->strobe_count = (uint32_t)atomic_get(&bsl_strobe_count);
	stats->last_tx_error = (int32_t)atomic_get(&bsl_last_tx_error);
}
