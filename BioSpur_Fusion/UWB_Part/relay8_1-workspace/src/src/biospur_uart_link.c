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
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/sys/util.h>

#define BSL_UART_NODE DT_NODELABEL(uart0)
#define BSL_STROBE_PIN NRF_GPIO_PIN_MAP(0, 26)
#define BSL_STROBE_WIDTH_US 10U
#define BSL_TX_ABORT_TIMEOUT_MS 20U
#define BSL_RX_DMA_BUFFER_SIZE 128U
#define BSL_RX_RING_SIZE 256U
#define BSL_RX_TIMEOUT_US 2000
#define BSL_RELAY_QUEUE_DEPTH 2U
#define BSL_RELAY_ACK_TIMEOUT_MS 30U
#define BSL_RELAY_DATA_WINDOW_MS 20U
#define BSL_RELAY_FALLBACK_MS 250U
#define BSL_RELAY_WAIT_MAX_MS 1000U
#define BSL_RELAY_WORKER_STACK 1536
#define BSL_RELAY_WORKER_PRIORITY 4

enum bsl_tx_kind {
	BSL_TX_KIND_NONE = 0,
	BSL_TX_KIND_DATA = 1,
	BSL_TX_KIND_ACK = 2,
};

struct bsl_relay_ack_item {
	uint16_t correlation;
	uint16_t len;
	char text[BSL_RELAY_PAYLOAD_MAX];
};

BUILD_ASSERT(DT_NODE_HAS_STATUS(BSL_UART_NODE, okay),
	     "BioSpur UART requires enabled uart0");
BUILD_ASSERT(DT_PROP(BSL_UART_NODE, current_speed) == BSL_BAUDRATE,
	     "BioSpur UART baudrate must match biospur_link.h");

static const struct device *const bsl_uart = DEVICE_DT_GET(BSL_UART_NODE);
static bsl_frame_t bsl_tx_frame;
static struct k_spinlock bsl_tx_lock;
static K_SEM_DEFINE(bsl_tx_idle, 0, 1);
static K_SEM_DEFINE(bsl_ack_tx_done, 0, 1);
static K_SEM_DEFINE(bsl_rx_data, 0, 1);
RING_BUF_DECLARE(bsl_rx_ring, BSL_RX_RING_SIZE);
K_MSGQ_DEFINE(bsl_ack_queue, sizeof(struct bsl_relay_ack_item),
	      BSL_RELAY_QUEUE_DEPTH, 4);

static atomic_t bsl_ready;
static atomic_t bsl_active;
static atomic_t bsl_tx_busy;
static atomic_t bsl_tx_kind;
static atomic_t bsl_rx_enabled;
static atomic_t bsl_next_rx_buffer;
static atomic_t bsl_frames_generated;
static atomic_t bsl_tx_started;
static atomic_t bsl_tx_completed;
static atomic_t bsl_tx_dropped;
static atomic_t bsl_tx_failed;
static atomic_t bsl_tx_aborted;
static atomic_t bsl_strobe_count;
static atomic_t bsl_last_tx_error;
static atomic_t bsl_last_data_complete_ms;
static atomic_t bsl_relay_rx_frames;
static atomic_t bsl_relay_rx_crc_errors;
static atomic_t bsl_relay_rx_dropped;
static atomic_t bsl_relay_ack_sent;
static atomic_t bsl_relay_ack_failed;

static uint8_t bsl_rx_dma_buffers[2][BSL_RX_DMA_BUFFER_SIZE];
static uint8_t bsl_relay_rx_frame[BSL_RELAY_FRAME_MAX];
static size_t bsl_relay_rx_position;
static size_t bsl_relay_rx_expected;
static biospur_uart_command_handler_t bsl_command_handler;

static int bsl_relay_transmit_ack(const struct bsl_relay_ack_item *item);

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

static int bsl_uart_enable_rx(void)
{
	int err;

	atomic_set(&bsl_next_rx_buffer, 1);
	err = uart_rx_enable(bsl_uart, bsl_rx_dma_buffers[0],
			     sizeof(bsl_rx_dma_buffers[0]), BSL_RX_TIMEOUT_US);
	if (err == 0) {
		atomic_set(&bsl_rx_enabled, 1);
	}
	return err;
}

static void bsl_uart_callback(const struct device *dev,
				      struct uart_event *evt,
				      void *user_data)
{
	ARG_UNUSED(user_data);

	switch (evt->type) {
	case UART_TX_DONE: {
		enum bsl_tx_kind kind =
			(enum bsl_tx_kind)atomic_get(&bsl_tx_kind);

		if (kind == BSL_TX_KIND_DATA) {
			atomic_inc(&bsl_tx_completed);
			atomic_set(&bsl_last_data_complete_ms,
				   (atomic_val_t)k_uptime_get_32());
		} else if (kind == BSL_TX_KIND_ACK) {
			atomic_inc(&bsl_relay_ack_sent);
			k_sem_give(&bsl_ack_tx_done);
		}
		atomic_set(&bsl_tx_kind, BSL_TX_KIND_NONE);
		atomic_clear(&bsl_tx_busy);
		k_sem_give(&bsl_tx_idle);
		break;
	}
	case UART_TX_ABORTED: {
		enum bsl_tx_kind kind =
			(enum bsl_tx_kind)atomic_get(&bsl_tx_kind);

		if (kind == BSL_TX_KIND_DATA) {
			atomic_inc(&bsl_tx_aborted);
		} else if (kind == BSL_TX_KIND_ACK) {
			atomic_inc(&bsl_relay_ack_failed);
			k_sem_give(&bsl_ack_tx_done);
		}
		atomic_set(&bsl_tx_kind, BSL_TX_KIND_NONE);
		atomic_clear(&bsl_tx_busy);
		k_sem_give(&bsl_tx_idle);
		break;
	}
	case UART_RX_RDY: {
		const uint8_t *data =
			&evt->data.rx.buf[evt->data.rx.offset];
		uint32_t accepted =
			ring_buf_put(&bsl_rx_ring, data, evt->data.rx.len);

		if (accepted != evt->data.rx.len) {
			atomic_add(&bsl_relay_rx_dropped,
				   (atomic_val_t)(evt->data.rx.len - accepted));
		}
		k_sem_give(&bsl_rx_data);
		break;
	}
	case UART_RX_BUF_REQUEST: {
		int index = (int)atomic_get(&bsl_next_rx_buffer);

		if (uart_rx_buf_rsp(dev, bsl_rx_dma_buffers[index],
				    sizeof(bsl_rx_dma_buffers[index])) == 0) {
			atomic_set(&bsl_next_rx_buffer, index ^ 1);
		}
		break;
	}
	case UART_RX_STOPPED:
		atomic_clear(&bsl_rx_enabled);
		break;
	case UART_RX_DISABLED:
		atomic_clear(&bsl_rx_enabled);
		if (atomic_get(&bsl_active) != 0) {
			(void)bsl_uart_enable_rx();
		}
		break;
	default:
		break;
	}
}

static void bsl_relay_parser_reset(void)
{
	bsl_relay_rx_position = 0U;
	bsl_relay_rx_expected = 0U;
}

static void bsl_relay_parser_accept(void)
{
	const bsl_relay_hdr_t *header =
		(const bsl_relay_hdr_t *)bsl_relay_rx_frame;
	uint16_t received_crc;
	uint16_t calculated_crc;
	char line[BSL_RELAY_PAYLOAD_MAX + 1U];

	memcpy(&received_crc,
	       &bsl_relay_rx_frame[bsl_relay_rx_expected - sizeof(received_crc)],
	       sizeof(received_crc));
	calculated_crc = bsl_crc16_ccitt_false(
		bsl_relay_rx_frame,
		bsl_relay_rx_expected - sizeof(received_crc));
	if (received_crc != calculated_crc) {
		atomic_inc(&bsl_relay_rx_crc_errors);
		bsl_relay_parser_reset();
		return;
	}

	memcpy(line, &bsl_relay_rx_frame[sizeof(*header)], header->len);
	line[header->len] = '\0';
	atomic_inc(&bsl_relay_rx_frames);
	if (bsl_command_handler != NULL) {
		bsl_command_handler(line, header->correlation);
	} else {
		(void)biospur_uart_link_send_ack(
			header->correlation, "ERR:COMMAND_HANDLER_UNAVAILABLE");
	}
	bsl_relay_parser_reset();
}

static void bsl_relay_parser_consume(uint8_t byte)
{
	if (bsl_relay_rx_position == 0U) {
		if (byte == BSL_RELAY_MAGIC0) {
			bsl_relay_rx_frame[bsl_relay_rx_position++] = byte;
		}
		return;
	}
	if (bsl_relay_rx_position == 1U) {
		if (byte == BSL_RELAY_MAGIC1) {
			bsl_relay_rx_frame[bsl_relay_rx_position++] = byte;
		} else {
			bsl_relay_parser_reset();
			bsl_relay_parser_consume(byte);
		}
		return;
	}
	if (bsl_relay_rx_position >= sizeof(bsl_relay_rx_frame)) {
		atomic_inc(&bsl_relay_rx_dropped);
		bsl_relay_parser_reset();
		return;
	}

	bsl_relay_rx_frame[bsl_relay_rx_position++] = byte;
	if (bsl_relay_rx_position == sizeof(bsl_relay_hdr_t)) {
		const bsl_relay_hdr_t *header =
			(const bsl_relay_hdr_t *)bsl_relay_rx_frame;

		if (header->version != BSL_RELAY_VERSION ||
		    header->type != BSL_RELAY_TYPE_COMMAND ||
		    header->len == 0U ||
		    header->len > BSL_RELAY_PAYLOAD_MAX) {
			atomic_inc(&bsl_relay_rx_dropped);
			bsl_relay_parser_reset();
			return;
		}
		bsl_relay_rx_expected = sizeof(*header) + header->len +
			sizeof(uint16_t);
	}
	if (bsl_relay_rx_expected != 0U &&
	    bsl_relay_rx_position == bsl_relay_rx_expected) {
		bsl_relay_parser_accept();
	}
}

static void bsl_relay_rx_thread(void *unused1, void *unused2, void *unused3)
{
	uint8_t chunk[64];
	struct bsl_relay_ack_item ack;

	ARG_UNUSED(unused1);
	ARG_UNUSED(unused2);
	ARG_UNUSED(unused3);
	while (true) {
		k_sem_take(&bsl_rx_data, K_FOREVER);
		while (true) {
			uint32_t count =
				ring_buf_get(&bsl_rx_ring, chunk, sizeof(chunk));

			if (count == 0U) {
				break;
			}
			for (uint32_t i = 0U; i < count; ++i) {
				bsl_relay_parser_consume(chunk[i]);
			}
		}
		while (k_msgq_get(&bsl_ack_queue, &ack, K_NO_WAIT) == 0) {
			(void)bsl_relay_transmit_ack(&ack);
		}
	}
}

K_THREAD_DEFINE(bsl_relay_rx_thread_id, BSL_RELAY_WORKER_STACK,
		bsl_relay_rx_thread, NULL, NULL, NULL,
		BSL_RELAY_WORKER_PRIORITY, 0, 0);

static int bsl_relay_transmit_ack(const struct bsl_relay_ack_item *item)
{
	uint8_t frame[BSL_RELAY_FRAME_MAX];
	bsl_relay_hdr_t header = {
		.magic0 = BSL_RELAY_MAGIC0,
		.magic1 = BSL_RELAY_MAGIC1,
		.version = BSL_RELAY_VERSION,
		.type = BSL_RELAY_TYPE_ACK,
		.len = item->len,
		.correlation = item->correlation,
	};
	uint32_t started_ms = k_uptime_get_32();
	uint16_t crc;
	size_t frame_len;
	int err;

	while ((uint32_t)(k_uptime_get_32() - started_ms) <
	       BSL_RELAY_WAIT_MAX_MS) {
		uint32_t now = k_uptime_get_32();
		uint32_t last_data =
			(uint32_t)atomic_get(&bsl_last_data_complete_ms);
		bool recent_data =
			last_data != 0U &&
			(uint32_t)(now - last_data) <= BSL_RELAY_DATA_WINDOW_MS;
		bool fallback =
			(uint32_t)(now - started_ms) >= BSL_RELAY_FALLBACK_MS;

		if ((recent_data || fallback ||
		     atomic_get(&bsl_frames_generated) == 0) &&
		    atomic_cas(&bsl_tx_busy, 0, 1)) {
			break;
		}
		k_sleep(K_MSEC(2));
	}
	if (atomic_get(&bsl_tx_busy) == 0 ||
	    atomic_get(&bsl_tx_kind) != BSL_TX_KIND_NONE) {
		return -ETIMEDOUT;
	}

	memcpy(frame, &header, sizeof(header));
	memcpy(&frame[sizeof(header)], item->text, item->len);
	crc = bsl_crc16_ccitt_false(frame, sizeof(header) + item->len);
	memcpy(&frame[sizeof(header) + item->len], &crc, sizeof(crc));
	frame_len = sizeof(header) + item->len + sizeof(crc);

	k_sem_reset(&bsl_tx_idle);
	k_sem_reset(&bsl_ack_tx_done);
	atomic_set(&bsl_tx_kind, BSL_TX_KIND_ACK);
	err = uart_tx(bsl_uart, frame, frame_len, SYS_FOREVER_US);
	if (err != 0) {
		atomic_set(&bsl_tx_kind, BSL_TX_KIND_NONE);
		atomic_clear(&bsl_tx_busy);
		atomic_inc(&bsl_relay_ack_failed);
		k_sem_give(&bsl_tx_idle);
		return err;
	}
	if (k_sem_take(&bsl_ack_tx_done,
		       K_MSEC(BSL_RELAY_ACK_TIMEOUT_MS)) != 0) {
		(void)uart_tx_abort(bsl_uart);
		return -ETIMEDOUT;
	}
	return 0;
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
	err = bsl_uart_enable_rx();
	if (err != 0) {
		atomic_clear(&bsl_ready);
		atomic_clear(&bsl_active);
		return err;
	}
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
	atomic_set(&bsl_tx_kind, BSL_TX_KIND_DATA);
	err = uart_tx(bsl_uart, (const uint8_t *)&bsl_tx_frame,
			      BSL_FRAME_LEN, SYS_FOREVER_US);
	if (err != 0) {
		atomic_set(&bsl_tx_kind, BSL_TX_KIND_NONE);
		atomic_clear(&bsl_tx_busy);
		atomic_inc(&bsl_tx_failed);
		atomic_set(&bsl_last_tx_error, err);
		k_sem_give(&bsl_tx_idle);
		return err;
	}

	atomic_inc(&bsl_tx_started);
	return 0;
}

void biospur_uart_link_set_command_handler(
	biospur_uart_command_handler_t handler)
{
	bsl_command_handler = handler;
}

int biospur_uart_link_send_ack(uint16_t correlation, const char *text)
{
	struct bsl_relay_ack_item item;
	size_t len;
	int err;

	if (text == NULL) {
		return -EINVAL;
	}
	len = strnlen(text, BSL_RELAY_PAYLOAD_MAX + 1U);
	if (len == 0U) {
		return -EINVAL;
	}
	if (len > BSL_RELAY_PAYLOAD_MAX) {
		text = "ERR:REPLY_TOO_LONG";
		len = strlen(text);
	}
	item.correlation = correlation;
	item.len = (uint16_t)len;
	memcpy(item.text, text, len);
	err = k_msgq_put(&bsl_ack_queue, &item, K_NO_WAIT);

	if (err == 0) {
		k_sem_give(&bsl_rx_data);
	}
	return err;
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
	if (atomic_get(&bsl_rx_enabled) != 0) {
		(void)uart_rx_disable(bsl_uart);
	}

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
		if (atomic_get(&bsl_rx_enabled) == 0) {
			(void)bsl_uart_enable_rx();
		}
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
