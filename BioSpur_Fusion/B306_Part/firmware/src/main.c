#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <hal/nrf_ficr.h>
#include <helpers/nrfx_reset_reason.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/device.h>
#include <zephyr/dfu/mcuboot.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/drivers/watchdog.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/sys/util.h>

#include "biospur_fusion_ble.h"
#include "biospur_link.h"
#include "imu.h"
#include "strobe_capture.h"

LOG_MODULE_REGISTER(biospur_fusion, LOG_LEVEL_INF);

#define LED0_NODE DT_ALIAS(led0)
#define UWB_UART_NODE DT_ALIAS(uwb_uart)

#define FW_MARKER "b306-imu-relay-v11"

#define UART_DMA_BUFFER_SIZE 256u
#define UART_RING_SIZE 2048u
#define UART_RX_TIMEOUT_US 2000
#define PARSER_STACK_SIZE 2048
#define PARSER_PRIORITY 5
#define CONTROL_STACK_SIZE 3072
#define CONTROL_PRIORITY 5
#define CONTROL_QUEUE_DEPTH 4
#define RELAY_ACK_TIMEOUT_MS 2000
#define RELAY_PENDING_MAX CONTROL_QUEUE_DEPTH
#define WATCHDOG_TIMEOUT_MS 30000u

BUILD_ASSERT(DT_NODE_HAS_STATUS(UWB_UART_NODE, okay),
	     "B306 UWB UART must be enabled by the application overlay");
BUILD_ASSERT(DT_PROP(UWB_UART_NODE, current_speed) == BSL_BAUDRATE,
	     "B306 UWB UART baudrate must match biospur_link.h");

#if !DT_NODE_HAS_STATUS(LED0_NODE, okay)
#error "B306 firmware requires the board's led0 alias"
#endif

static const struct gpio_dt_spec status_led =
	GPIO_DT_SPEC_GET(LED0_NODE, gpios);
static const struct device *const uwb_uart = DEVICE_DT_GET(UWB_UART_NODE);
static const struct device *const watchdog = DEVICE_DT_GET(DT_NODELABEL(wdt0));
static int watchdog_channel = -1;
static atomic_t watchdog_feed_count;
static uint32_t boot_reset_reason;

static int watchdog_start(void)
{
	const struct wdt_timeout_cfg config = {
		.window = {
			.min = 0u,
			.max = WATCHDOG_TIMEOUT_MS,
		},
		.callback = NULL,
		.flags = WDT_FLAG_RESET_SOC,
	};
	int ret;

	if (!device_is_ready(watchdog)) {
		return -ENODEV;
	}

	watchdog_channel = wdt_install_timeout(watchdog, &config);
	if (watchdog_channel < 0) {
		return watchdog_channel;
	}

	ret = wdt_setup(watchdog, WDT_OPT_PAUSE_HALTED_BY_DBG);
	if (ret != 0) {
		watchdog_channel = -1;
		return ret;
	}

	ret = wdt_feed(watchdog, watchdog_channel);
	if (ret == 0) {
		atomic_inc(&watchdog_feed_count);
	}
	return ret;
}

static int watchdog_feed_once(void)
{
	int ret;

	if (watchdog_channel < 0) {
		return -ENODEV;
	}
	ret = wdt_feed(watchdog, watchdog_channel);
	if (ret == 0) {
		atomic_inc(&watchdog_feed_count);
	}
	return ret;
}

static struct bt_uuid_128 fusion_service_uuid =
	BT_UUID_INIT_128(BT_UUID_128_ENCODE(
		BSF_BLE_UUID_SERVICE_W32, BSF_BLE_UUID_W16_1,
		BSF_BLE_UUID_W16_2, BSF_BLE_UUID_W16_3, BSF_BLE_UUID_W48));
static struct bt_uuid_128 fusion_data_uuid =
	BT_UUID_INIT_128(BT_UUID_128_ENCODE(
		BSF_BLE_UUID_DATA_W32, BSF_BLE_UUID_W16_1,
		BSF_BLE_UUID_W16_2, BSF_BLE_UUID_W16_3, BSF_BLE_UUID_W48));
static struct bt_uuid_128 fusion_telemetry_uuid =
	BT_UUID_INIT_128(BT_UUID_128_ENCODE(
		BSF_BLE_UUID_TELEMETRY_W32, BSF_BLE_UUID_W16_1,
		BSF_BLE_UUID_W16_2, BSF_BLE_UUID_W16_3, BSF_BLE_UUID_W48));
static struct bt_uuid_128 fusion_control_uuid =
	BT_UUID_INIT_128(BT_UUID_128_ENCODE(
		BSF_BLE_UUID_CONTROL_W32, BSF_BLE_UUID_W16_1,
		BSF_BLE_UUID_W16_2, BSF_BLE_UUID_W16_3, BSF_BLE_UUID_W48));

static atomic_t data_subscribed;
static atomic_t telemetry_subscribed;

static ssize_t control_write(struct bt_conn *conn,
			     const struct bt_gatt_attr *attr,
			     const void *buf, uint16_t len,
			     uint16_t offset, uint8_t flags);

static void data_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	ARG_UNUSED(attr);
	atomic_set(&data_subscribed, value == BT_GATT_CCC_NOTIFY);
}

static void telemetry_ccc_changed(const struct bt_gatt_attr *attr,
				  uint16_t value)
{
	ARG_UNUSED(attr);
	atomic_set(&telemetry_subscribed, value == BT_GATT_CCC_NOTIFY);
}

BT_GATT_SERVICE_DEFINE(
	fusion_service,
	BT_GATT_PRIMARY_SERVICE(&fusion_service_uuid.uuid),
	BT_GATT_CHARACTERISTIC(&fusion_data_uuid.uuid,
			       BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_NONE,
			       NULL, NULL, NULL),
	BT_GATT_CCC(data_ccc_changed,
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
	BT_GATT_CHARACTERISTIC(&fusion_telemetry_uuid.uuid,
			       BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_NONE,
			       NULL, NULL, NULL),
	BT_GATT_CCC(telemetry_ccc_changed,
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
	BT_GATT_CHARACTERISTIC(&fusion_control_uuid.uuid,
			       BT_GATT_CHRC_WRITE |
			       BT_GATT_CHRC_WRITE_WITHOUT_RESP,
			       BT_GATT_PERM_WRITE,
			       NULL, control_write, NULL));

#define FUSION_DATA_ATTR (&fusion_service.attrs[2])
#define FUSION_TELEMETRY_ATTR (&fusion_service.attrs[5])

RING_BUF_DECLARE(uart_ring, UART_RING_SIZE);
K_SEM_DEFINE(uart_data_sem, 0, 1);
K_SEM_DEFINE(uart_tx_done, 0, 1);
K_MUTEX_DEFINE(uart_tx_lock);

static uint8_t uart_dma_buffers[2][UART_DMA_BUFFER_SIZE];
static atomic_t next_uart_buffer;

static atomic_t uart_bytes;
static atomic_t valid_frames;
static atomic_t crc_errors;
static atomic_t header_errors;
static atomic_t ring_dropped_bytes;
static atomic_t dropped_sweeps;
static atomic_t duplicate_sweeps;
static atomic_t out_of_order_sweeps;
static atomic_t notify_ok;
static atomic_t drop_unsub;
static atomic_t drop_err;
static atomic_t last_notify_error;
static atomic_t uart_restarts;
static atomic_t last_uart_error;
static atomic_t last_sweep;
static atomic_t have_last_sweep;
static atomic_t node_sequence;
static atomic_t ctrl_rx;
static atomic_t ctrl_bad_bsf;
static atomic_t relay_tx;
static atomic_t relay_ack;
static atomic_t relay_timeout;
static atomic_t next_correlation;

static char device_name[8];
static uint16_t node_identity;
static uint8_t parser_frame[BSL_RELAY_FRAME_MAX];
static size_t parser_position;
static size_t parser_expected;
static uint8_t parser_type;
static const uint8_t firmware_advertising_marker[] = {
	0xff, 0xff, 'B', '3', '0', '6', 'D', '3',
};
static const struct bt_data advertising_data[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS,
		      BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
	BT_DATA_BYTES(BT_DATA_UUID128_ALL,
		      BT_UUID_128_ENCODE(
			      BSF_BLE_UUID_SERVICE_W32,
			      BSF_BLE_UUID_W16_1,
			      BSF_BLE_UUID_W16_2,
			      BSF_BLE_UUID_W16_3,
			      BSF_BLE_UUID_W48)),
};

static uint16_t crc16_ccitt_false(const uint8_t *data, size_t len)
{
	uint16_t crc = 0xffffu;

	for (size_t i = 0; i < len; ++i) {
		crc ^= (uint16_t)data[i] << 8;
		for (uint8_t bit = 0; bit < 8u; ++bit) {
			crc = (crc & 0x8000u) != 0u ?
				      (uint16_t)((crc << 1) ^ 0x1021u) :
				      (uint16_t)(crc << 1);
		}
	}

	return crc;
}

static int publish_data_record(const void *record, size_t len)
{
	int err;

	if (atomic_get(&data_subscribed) == 0) {
		atomic_inc(&drop_unsub);
		return -ENOTCONN;
	}

	err = bt_gatt_notify(NULL, FUSION_DATA_ATTR, record, len);
	if (err == 0) {
		atomic_inc(&notify_ok);
	} else {
		atomic_inc(&drop_err);
		atomic_set(&last_notify_error, err);
	}
	return err;
}

static int publish_control_reply(uint8_t source, uint16_t correlation,
				 const char *text)
{
	uint8_t record[sizeof(bsf_ble_control_reply_prefix_t) +
		       BSF_CONTROL_REPLY_TEXT_MAX];
	bsf_ble_control_reply_prefix_t prefix;
	size_t text_len = strnlen(text, BSF_CONTROL_REPLY_TEXT_MAX);

	prefix.version = BSF_BLE_PROTOCOL_VERSION;
	prefix.kind = BSF_BLE_KIND_CONTROL_REPLY;
	prefix.len = (uint16_t)(sizeof(prefix) + text_len);
	prefix.source = source;
	prefix.correlation = correlation;
	memcpy(record, &prefix, sizeof(prefix));
	memcpy(&record[sizeof(prefix)], text, text_len);
	return publish_data_record(record, sizeof(prefix) + text_len);
}

static void parser_reset(void)
{
	parser_position = 0u;
	parser_expected = 0u;
	parser_type = 0u;
}

static void account_sweep(uint32_t sweep)
{
	if (atomic_get(&have_last_sweep) == 0) {
		atomic_set(&last_sweep, (atomic_val_t)sweep);
		atomic_set(&have_last_sweep, 1);
		return;
	}

	uint32_t previous = (uint32_t)atomic_get(&last_sweep);
	uint32_t delta = sweep - previous;

	if (delta == 0u) {
		atomic_inc(&duplicate_sweeps);
		return;
	}

	if (delta < 0x80000000u) {
		if (delta > 1u) {
			atomic_add(&dropped_sweeps, (atomic_val_t)(delta - 1u));
		}
		atomic_set(&last_sweep, (atomic_val_t)sweep);
		return;
	}

	atomic_inc(&out_of_order_sweeps);
}

static void publish_uwb(const bsl_frame_t *frame)
{
	bsf_ble_uwb_packet_t packet = {
		.version = BSF_BLE_PROTOCOL_VERSION,
		.kind = BSF_BLE_KIND_UWB,
		.len = sizeof(packet),
		.node_sequence = (uint32_t)atomic_inc(&node_sequence) + 1u,
		.node_uptime_ms = (uint32_t)k_uptime_get(),
		.uwb = frame->body,
	};
	bsf_strobe_capture_pair(frame->body.flags, &packet.capture);
	(void)publish_data_record(&packet, sizeof(packet));
}

static void parser_accept_uwb_frame(void)
{
	const bsl_frame_t *frame = (const bsl_frame_t *)parser_frame;
	uint16_t received_crc;
	uint16_t calculated_crc;

	if (frame->hdr.version != BSL_VERSION ||
	    frame->hdr.len != sizeof(bsl_uwb_t)) {
		atomic_inc(&header_errors);
		parser_reset();
		return;
	}

	memcpy(&received_crc, &frame->crc, sizeof(received_crc));
	calculated_crc = crc16_ccitt_false(
		parser_frame, offsetof(bsl_frame_t, crc));
	if (received_crc != calculated_crc) {
		atomic_inc(&crc_errors);
		parser_reset();
		return;
	}

	atomic_inc(&valid_frames);
	account_sweep(frame->body.sweep);
	publish_uwb(frame);
	parser_reset();
}

static void relay_pending_acknowledge(uint16_t correlation);

static void parser_accept_relay_frame(void)
{
	const bsl_relay_hdr_t *header =
		(const bsl_relay_hdr_t *)parser_frame;
	uint16_t received_crc;
	uint16_t calculated_crc;
	char reply[BSL_RELAY_PAYLOAD_MAX + 1u];

	memcpy(&received_crc, &parser_frame[parser_expected - sizeof(uint16_t)],
	       sizeof(received_crc));
	calculated_crc = crc16_ccitt_false(
		parser_frame, parser_expected - sizeof(uint16_t));
	if (received_crc != calculated_crc) {
		atomic_inc(&crc_errors);
		parser_reset();
		return;
	}
	if (header->version != BSL_RELAY_VERSION ||
	    header->type != BSL_RELAY_TYPE_ACK ||
	    header->len > BSL_RELAY_PAYLOAD_MAX) {
		atomic_inc(&header_errors);
		parser_reset();
		return;
	}

	memcpy(reply, &parser_frame[sizeof(*header)], header->len);
	reply[header->len] = '\0';
	atomic_inc(&relay_ack);
	relay_pending_acknowledge(header->correlation);
	(void)publish_control_reply(BSF_CONTROL_SOURCE_TAG,
				    header->correlation, reply);
	parser_reset();
}

static void parser_consume_byte(uint8_t byte)
{
	if (parser_position == 0u) {
		if (byte == BSL_MAGIC0) {
			parser_type = BSF_BLE_KIND_UWB;
			parser_expected = BSL_FRAME_LEN_EXPECTED;
			parser_frame[parser_position++] = byte;
		} else if (byte == BSL_RELAY_MAGIC0) {
			parser_type = BSL_RELAY_TYPE_ACK;
			parser_frame[parser_position++] = byte;
		}
		return;
	}

	if (parser_position == 1u) {
		uint8_t expected_magic1 =
			parser_type == BSF_BLE_KIND_UWB ?
				BSL_MAGIC1 : BSL_RELAY_MAGIC1;

		if (byte == expected_magic1) {
			parser_frame[parser_position++] = byte;
		} else {
			parser_reset();
			parser_consume_byte(byte);
		}
		return;
	}

	if (parser_position >= sizeof(parser_frame)) {
		atomic_inc(&header_errors);
		parser_reset();
		return;
	}
	parser_frame[parser_position++] = byte;

	if (parser_type == BSL_RELAY_TYPE_ACK &&
	    parser_position == sizeof(bsl_relay_hdr_t)) {
		const bsl_relay_hdr_t *header =
			(const bsl_relay_hdr_t *)parser_frame;

		if (header->magic0 != BSL_RELAY_MAGIC0 ||
		    header->magic1 != BSL_RELAY_MAGIC1 ||
		    header->len > BSL_RELAY_PAYLOAD_MAX) {
			atomic_inc(&header_errors);
			parser_reset();
			return;
		}
		parser_expected = sizeof(*header) + header->len +
			sizeof(uint16_t);
	}

	if (parser_expected != 0u && parser_position == parser_expected) {
		if (parser_type == BSF_BLE_KIND_UWB) {
			parser_accept_uwb_frame();
		} else {
			parser_accept_relay_frame();
		}
	}
}

static void uart_parser_thread(void *unused1, void *unused2, void *unused3)
{
	uint8_t chunk[128];

	ARG_UNUSED(unused1);
	ARG_UNUSED(unused2);
	ARG_UNUSED(unused3);

	while (true) {
		k_sem_take(&uart_data_sem, K_FOREVER);

		while (true) {
			uint32_t count = ring_buf_get(&uart_ring, chunk, sizeof(chunk));

			if (count == 0u) {
				break;
			}
			for (uint32_t i = 0; i < count; ++i) {
				parser_consume_byte(chunk[i]);
			}
		}
	}
}

K_THREAD_DEFINE(uart_parser_thread_id, PARSER_STACK_SIZE,
		uart_parser_thread, NULL, NULL, NULL,
		PARSER_PRIORITY, 0, 0);

static int uart_enable_rx(const struct device *dev)
{
	atomic_set(&next_uart_buffer, 1);
	return uart_rx_enable(dev, uart_dma_buffers[0],
			      sizeof(uart_dma_buffers[0]), UART_RX_TIMEOUT_US);
}

static void uart_callback(const struct device *dev,
			  struct uart_event *event,
			  void *user_data)
{
	ARG_UNUSED(user_data);

	switch (event->type) {
	case UART_RX_RDY: {
		const uint8_t *data =
			&event->data.rx.buf[event->data.rx.offset];
		uint32_t accepted =
			ring_buf_put(&uart_ring, data, event->data.rx.len);

		atomic_add(&uart_bytes, (atomic_val_t)event->data.rx.len);
		if (accepted != event->data.rx.len) {
			atomic_add(&ring_dropped_bytes,
				   (atomic_val_t)(event->data.rx.len - accepted));
		}
		k_sem_give(&uart_data_sem);
		break;
	}

	case UART_RX_BUF_REQUEST: {
		int index = (int)atomic_get(&next_uart_buffer);
		int err = uart_rx_buf_rsp(dev, uart_dma_buffers[index],
					  sizeof(uart_dma_buffers[index]));

		if (err == 0) {
			atomic_set(&next_uart_buffer, index ^ 1);
		} else {
			atomic_set(&last_uart_error, err);
		}
		break;
	}

	case UART_RX_STOPPED:
		atomic_set(&last_uart_error, event->data.rx_stop.reason);
		break;

	case UART_RX_DISABLED: {
		int err;

		atomic_inc(&uart_restarts);
		err = uart_enable_rx(dev);
		if (err != 0) {
			atomic_set(&last_uart_error, err);
		}
		break;
	}

	case UART_TX_DONE:
		atomic_set(&last_uart_error, 0);
		k_sem_give(&uart_tx_done);
		break;

	case UART_TX_ABORTED:
		atomic_set(&last_uart_error, -EIO);
		k_sem_give(&uart_tx_done);
		break;

	default:
		break;
	}
}

static int uart_start(void)
{
	int err;

	if (!device_is_ready(uwb_uart)) {
		return -ENODEV;
	}

	err = uart_callback_set(uwb_uart, uart_callback, NULL);
	if (err != 0) {
		return err;
	}

	return uart_enable_rx(uwb_uart);
}

static int uart_send_relay(uint16_t correlation, const char *line)
{
	uint8_t frame[BSL_RELAY_FRAME_MAX];
	bsl_relay_hdr_t header = {
		.magic0 = BSL_RELAY_MAGIC0,
		.magic1 = BSL_RELAY_MAGIC1,
		.version = BSL_RELAY_VERSION,
		.type = BSL_RELAY_TYPE_COMMAND,
		.correlation = correlation,
	};
	size_t line_len = strnlen(line, BSL_RELAY_PAYLOAD_MAX + 1u);
	size_t frame_len;
	uint16_t crc;
	int ret;

	if (line_len == 0u || line_len > BSL_RELAY_PAYLOAD_MAX) {
		return -EMSGSIZE;
	}
	header.len = (uint16_t)line_len;
	frame_len = sizeof(header) + line_len + sizeof(crc);
	memcpy(frame, &header, sizeof(header));
	memcpy(&frame[sizeof(header)], line, line_len);
	crc = crc16_ccitt_false(frame, sizeof(header) + line_len);
	memcpy(&frame[sizeof(header) + line_len], &crc, sizeof(crc));

	k_mutex_lock(&uart_tx_lock, K_FOREVER);
	k_sem_reset(&uart_tx_done);
	ret = uart_tx(uwb_uart, frame, frame_len, 500000);
	if (ret == 0) {
		ret = k_sem_take(&uart_tx_done, K_MSEC(600));
		if (ret != 0) {
			(void)uart_tx_abort(uwb_uart);
			ret = -ETIMEDOUT;
		} else {
			ret = (int)atomic_get(&last_uart_error);
		}
	}
	k_mutex_unlock(&uart_tx_lock);

	if (ret == 0) {
		atomic_inc(&relay_tx);
	}
	return ret;
}

struct relay_pending_entry {
	int64_t deadline_ms;
	uint16_t correlation;
	bool used;
};

struct control_request {
	uint16_t len;
	char line[BSF_CONTROL_LINE_MAX + 1u];
};

static struct relay_pending_entry relay_pending[RELAY_PENDING_MAX];
K_MUTEX_DEFINE(relay_pending_lock);
K_MSGQ_DEFINE(control_queue, sizeof(struct control_request),
	      CONTROL_QUEUE_DEPTH, 4);

static void relay_timeout_work_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(relay_timeout_work, relay_timeout_work_handler);

static void reboot_work_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(reboot_work, reboot_work_handler);

static int relay_pending_reserve(uint16_t correlation)
{
	int ret = -ENOSPC;

	k_mutex_lock(&relay_pending_lock, K_FOREVER);
	for (size_t i = 0; i < ARRAY_SIZE(relay_pending); ++i) {
		if (!relay_pending[i].used) {
			relay_pending[i].used = true;
			relay_pending[i].correlation = correlation;
			relay_pending[i].deadline_ms =
				k_uptime_get() + RELAY_ACK_TIMEOUT_MS;
			ret = 0;
			break;
		}
	}
	k_mutex_unlock(&relay_pending_lock);
	if (ret == 0) {
		k_work_reschedule(&relay_timeout_work, K_MSEC(250));
	}
	return ret;
}

static bool relay_pending_remove(uint16_t correlation)
{
	bool found = false;

	k_mutex_lock(&relay_pending_lock, K_FOREVER);
	for (size_t i = 0; i < ARRAY_SIZE(relay_pending); ++i) {
		if (relay_pending[i].used &&
		    relay_pending[i].correlation == correlation) {
			relay_pending[i].used = false;
			found = true;
			break;
		}
	}
	k_mutex_unlock(&relay_pending_lock);
	return found;
}

static void relay_pending_acknowledge(uint16_t correlation)
{
	(void)relay_pending_remove(correlation);
}

static void relay_timeout_work_handler(struct k_work *work)
{
	uint16_t expired[RELAY_PENDING_MAX];
	size_t expired_count = 0u;
	bool remaining = false;
	int64_t now = k_uptime_get();

	ARG_UNUSED(work);
	k_mutex_lock(&relay_pending_lock, K_FOREVER);
	for (size_t i = 0; i < ARRAY_SIZE(relay_pending); ++i) {
		if (!relay_pending[i].used) {
			continue;
		}
		if (now >= relay_pending[i].deadline_ms) {
			expired[expired_count++] =
				relay_pending[i].correlation;
			relay_pending[i].used = false;
		} else {
			remaining = true;
		}
	}
	k_mutex_unlock(&relay_pending_lock);

	for (size_t i = 0; i < expired_count; ++i) {
		atomic_inc(&relay_timeout);
		(void)publish_control_reply(BSF_CONTROL_SOURCE_TAG,
					    expired[i], "TIMEOUT");
	}
	if (remaining) {
		k_work_reschedule(&relay_timeout_work, K_MSEC(250));
	}
}

static void reboot_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	sys_reboot(SYS_REBOOT_COLD);
}

static void clear_session_counters(void)
{
	atomic_set(&uart_bytes, 0);
	atomic_set(&valid_frames, 0);
	atomic_set(&crc_errors, 0);
	atomic_set(&header_errors, 0);
	atomic_set(&ring_dropped_bytes, 0);
	atomic_set(&dropped_sweeps, 0);
	atomic_set(&duplicate_sweeps, 0);
	atomic_set(&out_of_order_sweeps, 0);
	atomic_set(&notify_ok, 0);
	atomic_set(&drop_unsub, 0);
	atomic_set(&drop_err, 0);
	atomic_set(&last_notify_error, 0);
	atomic_set(&uart_restarts, 0);
	atomic_set(&last_uart_error, 0);
	atomic_set(&last_sweep, 0);
	atomic_set(&have_last_sweep, 0);
	atomic_set(&ctrl_rx, 0);
	atomic_set(&ctrl_bad_bsf, 0);
	atomic_set(&relay_tx, 0);
	atomic_set(&relay_ack, 0);
	atomic_set(&relay_timeout, 0);
	bsf_imu_clear_counters();
	bsf_strobe_capture_counters_clear();
}

static int parse_u32_token(const char *token, const char *prefix,
			   uint32_t *value)
{
	char *end;
	unsigned long parsed;
	size_t prefix_len = strlen(prefix);

	if (strncmp(token, prefix, prefix_len) != 0) {
		return -ENOENT;
	}
	errno = 0;
	parsed = strtoul(token + prefix_len, &end, 10);
	if (errno != 0 || *end != '\0' || end == token + prefix_len ||
	    parsed > UINT32_MAX) {
		return -EINVAL;
	}
	*value = (uint32_t)parsed;
	return 0;
}

static int parse_exact_u32_command(const char *command, const char *prefix,
				   uint32_t *value)
{
	return parse_u32_token(command, prefix, value);
}

static int make_tag_command(const char *command, char *tag_line,
			    size_t tag_line_size, const char **mapping)
{
	char arguments[BSF_CONTROL_LINE_MAX + 1u];
	char *save;
	char *token;
	uint32_t id = 0u;
	uint32_t slot = 0u;
	uint32_t count = 0u;
	uint32_t period = 10u;
	uint32_t active = 9u;
	uint32_t epoch = 5000u;
	bool have_id = false;
	bool have_slot = false;
	bool have_count = false;

	*mapping = NULL;
	if (strcmp(command, "TAG PING") == 0) {
		snprintf(tag_line, tag_line_size, "PING");
		return 0;
	}
	if (strcmp(command, "TAG REBOOT") == 0) {
		snprintf(tag_line, tag_line_size, "REBOOT");
		return 0;
	}
	if (strcmp(command, "TAG STATUS") == 0) {
		snprintf(tag_line, tag_line_size, "STATUS");
		return 0;
	}
	if (strcmp(command, "TAG TDMA_STATUS") == 0) {
		snprintf(tag_line, tag_line_size, "TDMA_STATUS");
		return 0;
	}
	if (strcmp(command, "TAG TDMA CLEAR") == 0) {
		/* The audited tag command surface has no direct free-run command.
		 * Reboot restores its documented boot/free-run behavior. */
		snprintf(tag_line, tag_line_size, "REBOOT");
		*mapping = "TDMA_CLEAR->REBOOT";
		return 0;
	}
	if (strncmp(command, "TAG RAW ", 8u) == 0) {
		size_t raw_len = strnlen(command + 8u,
					 BSL_RELAY_PAYLOAD_MAX + 1u);

		if (raw_len == 0u || raw_len > BSL_RELAY_PAYLOAD_MAX) {
			return -EMSGSIZE;
		}
		memcpy(tag_line, command + 8u, raw_len + 1u);
		return 0;
	}
	if (strncmp(command, "TAG CFG ", 8u) != 0) {
		return -ENOTSUP;
	}

	snprintf(arguments, sizeof(arguments), "%s", command + 8u);
	token = strtok_r(arguments, " ", &save);
	while (token != NULL) {
		uint32_t parsed;
		int ret;

		if ((ret = parse_u32_token(token, "id=", &parsed)) == 0) {
			id = parsed;
			have_id = true;
		} else if ((ret = parse_u32_token(token, "slot=", &parsed)) == 0) {
			slot = parsed;
			have_slot = true;
		} else if ((ret = parse_u32_token(token, "count=", &parsed)) == 0) {
			count = parsed;
			have_count = true;
		} else if ((ret = parse_u32_token(token, "period=", &parsed)) == 0) {
			period = parsed;
		} else if ((ret = parse_u32_token(token, "active=", &parsed)) == 0) {
			active = parsed;
		} else if ((ret = parse_u32_token(token, "epoch=", &parsed)) == 0) {
			epoch = parsed;
		} else {
			return ret == -EINVAL ? ret : -EINVAL;
		}
		token = strtok_r(NULL, " ", &save);
	}
	if (!have_id || !have_slot || !have_count ||
	    id > UINT8_MAX || slot > UINT16_MAX || count == 0u ||
	    count > UINT16_MAX || period == 0u || active == 0u) {
		return -EINVAL;
	}

	if (snprintf(tag_line, tag_line_size,
		     "CFG TAG=%u SLOT=%u COUNT=%u PERIOD=%u ACTIVE=%u EPOCH=%u",
		     id, slot, count, period, active, epoch) >= tag_line_size) {
		return -EMSGSIZE;
	}
	return 0;
}

static void process_control(const char *command, uint16_t correlation)
{
	char reply[BSF_CONTROL_REPLY_TEXT_MAX + 1u];
	char tag_line[BSL_RELAY_PAYLOAD_MAX + 1u];
	const char *mapping;
	struct bsf_imu_stats imu_stats;
	uint32_t value;
	int ret;

	if (strcmp(command, "PING") == 0) {
		snprintf(reply, sizeof(reply), "PONG name=%s fw=%s proto=%u",
			 device_name, FW_MARKER, BSF_BLE_PROTOCOL_VERSION);
	} else if (strcmp(command, "STATUS") == 0) {
		bsf_ble_telemetry_t capture_status = { 0 };

		bsf_imu_get_stats(&imu_stats);
		bsf_strobe_capture_telemetry(&capture_status);
		snprintf(reply, sizeof(reply),
			 "STATUS fw=%s id=%04X up_ms=%u frames=%u strobe_rise=%u imu=%u/%uHz/N%u verify=%s",
			 FW_MARKER, node_identity, (uint32_t)k_uptime_get(),
			 (uint32_t)atomic_get(&valid_frames),
			 capture_status.rising_edge_count,
			 imu_stats.active, imu_stats.rate_hz,
			 imu_stats.batch_size,
			 imu_stats.verify_pass ? "PASS" : "WARN");
	} else if (strcmp(command, "REBOOT") == 0) {
		snprintf(reply, sizeof(reply), "REBOOT QUEUED delay_ms=150");
		(void)publish_control_reply(BSF_CONTROL_SOURCE_B306,
					    correlation, reply);
		k_work_reschedule(&reboot_work, K_MSEC(150));
		return;
	} else if (strcmp(command, "COUNTERS CLEAR") == 0) {
		clear_session_counters();
		snprintf(reply, sizeof(reply), "COUNTERS CLEARED");
	} else if (strcmp(command, "COUNTERS") == 0) {
		bsf_ble_telemetry_t capture_status = { 0 };

		bsf_imu_get_stats(&imu_stats);
		bsf_strobe_capture_telemetry(&capture_status);
		snprintf(reply, sizeof(reply),
			 "CTR1 bytes=%u f=%u crc=%u hdr=%u ring=%u lost=%u dup=%u ooo=%u rise=%u fall=%u orphan=%u/%u/%u",
			 (uint32_t)atomic_get(&uart_bytes),
			 (uint32_t)atomic_get(&valid_frames),
			 (uint32_t)atomic_get(&crc_errors),
			 (uint32_t)atomic_get(&header_errors),
			 (uint32_t)atomic_get(&ring_dropped_bytes),
			 (uint32_t)atomic_get(&dropped_sweeps),
			 (uint32_t)atomic_get(&duplicate_sweeps),
			 (uint32_t)atomic_get(&out_of_order_sweeps),
			 capture_status.rising_edge_count,
			 capture_status.falling_edge_count,
			 capture_status.orphan_strobe_count,
			 capture_status.orphan_edge_count,
			 capture_status.orphan_frame_count);
		(void)publish_control_reply(BSF_CONTROL_SOURCE_B306,
					    correlation, reply);
		snprintf(reply, sizeof(reply),
			 "CTR2 nok=%u unsub=%u nerr=%u last=%d ip=%u idup=%u ie=%u ir=%u ctrl=%u bad=%u relay=%u/%u/%u",
			 (uint32_t)atomic_get(&notify_ok),
			 (uint32_t)atomic_get(&drop_unsub),
			 (uint32_t)atomic_get(&drop_err),
			 (int32_t)atomic_get(&last_notify_error),
			 imu_stats.pulls, imu_stats.duplicate_samples,
			 imu_stats.i2c_errors, imu_stats.records,
			 (uint32_t)atomic_get(&ctrl_rx),
			 (uint32_t)atomic_get(&ctrl_bad_bsf),
			 (uint32_t)atomic_get(&relay_tx),
			 (uint32_t)atomic_get(&relay_ack),
			 (uint32_t)atomic_get(&relay_timeout));
	} else if (strcmp(command, "IMU START") == 0) {
		ret = bsf_imu_start();
		snprintf(reply, sizeof(reply), "IMU START %s err=%d",
			 ret == 0 ? "OK" : "FAIL", ret);
	} else if (strcmp(command, "IMU STOP") == 0) {
		ret = bsf_imu_stop();
		snprintf(reply, sizeof(reply), "IMU STOP %s err=%d",
			 ret == 0 ? "OK" : "FAIL", ret);
	} else if (parse_exact_u32_command(command, "IMU RATE=", &value) == 0) {
		ret = value <= UINT16_MAX ?
			bsf_imu_set_rate((uint16_t)value) : -EINVAL;
		snprintf(reply, sizeof(reply), "IMU RATE %s hz=%u err=%d",
				 ret == 0 ? "OK" : "FAIL", value, ret);
	} else if (parse_exact_u32_command(command, "IMU BATCH=", &value) == 0) {
		ret = value <= UINT8_MAX ?
			bsf_imu_set_batch((uint8_t)value) : -EINVAL;
		snprintf(reply, sizeof(reply), "IMU BATCH %s n=%u err=%d",
			 ret == 0 ? "OK" : "FAIL", value, ret);
	} else if (strcmp(command, "IMU STATUS") == 0) {
		bsf_imu_format_status(reply, sizeof(reply));
	} else if (strcmp(command, "IMU PROVISION") == 0) {
		ret = bsf_imu_provision(reply, sizeof(reply));
		ARG_UNUSED(ret);
	} else if (strcmp(command, "IMU SELFTEST") == 0) {
		ret = bsf_imu_selftest(reply, sizeof(reply));
		ARG_UNUSED(ret);
	} else if (strcmp(command, "IMU CAL_ACC") == 0) {
		ret = bsf_imu_cal_acc(reply, sizeof(reply));
		ARG_UNUSED(ret);
	} else if (strncmp(command, "TAG ", 4u) == 0) {
		ret = make_tag_command(command, tag_line, sizeof(tag_line),
				       &mapping);
			if (ret != 0) {
				snprintf(reply, sizeof(reply),
					 "RELAY REJECT err=%d", ret);
			} else {
				ret = relay_pending_reserve(correlation);
				if (ret != 0) {
					snprintf(reply, sizeof(reply),
						 "RELAY BUSY err=%d", ret);
				} else {
					ret = uart_send_relay(correlation, tag_line);
					if (ret == 0) {
						snprintf(reply, sizeof(reply),
							 "RELAY_QUEUED%s%s bytes=%u",
							 mapping != NULL ? " mapping=" : "",
							 mapping != NULL ? mapping : "",
							 (unsigned int)strlen(tag_line));
					} else {
						(void)relay_pending_remove(correlation);
						snprintf(reply, sizeof(reply),
							 "RELAY FAIL err=%d", ret);
					}
				}
			}
	} else {
		snprintf(reply, sizeof(reply), "ERR UNKNOWN_COMMAND");
	}

	(void)publish_control_reply(BSF_CONTROL_SOURCE_B306,
				    correlation, reply);
}

static void control_thread(void *unused1, void *unused2, void *unused3)
{
	struct control_request request;

	ARG_UNUSED(unused1);
	ARG_UNUSED(unused2);
	ARG_UNUSED(unused3);

	while (true) {
		uint16_t correlation;
		const char *command;

		k_msgq_get(&control_queue, &request, K_FOREVER);
		if (request.len < 9u ||
		    strncmp(request.line, device_name,
			    sizeof(device_name) - 1u) != 0 ||
		    request.line[sizeof(device_name) - 1u] != ' ') {
			atomic_inc(&ctrl_bad_bsf);
			continue;
		}
		command = &request.line[sizeof(device_name)];
		correlation =
			(uint16_t)((uint32_t)atomic_inc(&next_correlation) + 1u);
		process_control(command, correlation);
	}
}

K_THREAD_DEFINE(control_thread_id, CONTROL_STACK_SIZE,
		control_thread, NULL, NULL, NULL,
		CONTROL_PRIORITY, 0, 0);

static ssize_t control_write(struct bt_conn *conn,
			     const struct bt_gatt_attr *attr,
			     const void *buf, uint16_t len,
			     uint16_t offset, uint8_t flags)
{
	struct control_request request;
	uint16_t written_len = len;

	ARG_UNUSED(conn);
	ARG_UNUSED(attr);
	ARG_UNUSED(flags);
	if (offset != 0u || len == 0u || len > BSF_CONTROL_LINE_MAX) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	if (memchr(buf, '\0', len) != NULL) {
		return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
	}

	memcpy(request.line, buf, len);
	while (len != 0u &&
	       (request.line[len - 1u] == '\r' ||
		request.line[len - 1u] == '\n')) {
		--len;
	}
	request.line[len] = '\0';
	request.len = len;
	if (k_msgq_put(&control_queue, &request, K_NO_WAIT) != 0) {
		return BT_GATT_ERR(BT_ATT_ERR_INSUFFICIENT_RESOURCES);
	}
	atomic_inc(&ctrl_rx);
	return written_len;
}

static void telemetry_work_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(telemetry_work, telemetry_work_handler);

static void telemetry_work_handler(struct k_work *work)
{
	int watchdog_ret = watchdog_feed_once();
	struct bsf_imu_stats imu_stats;

	bsf_imu_get_stats(&imu_stats);
	bsf_ble_telemetry_t telemetry = {
		.version = BSF_BLE_PROTOCOL_VERSION,
		.kind = BSF_BLE_KIND_TELEMETRY,
		.len = sizeof(telemetry),
		.node_uptime_ms = (uint32_t)k_uptime_get(),
		.uart_bytes = (uint32_t)atomic_get(&uart_bytes),
		.valid_frames = (uint32_t)atomic_get(&valid_frames),
		.crc_errors = (uint32_t)atomic_get(&crc_errors),
		.header_errors = (uint32_t)atomic_get(&header_errors),
		.ring_dropped_bytes =
			(uint32_t)atomic_get(&ring_dropped_bytes),
		.dropped_sweeps = (uint32_t)atomic_get(&dropped_sweeps),
		.duplicate_sweeps =
			(uint32_t)atomic_get(&duplicate_sweeps),
		.out_of_order_sweeps =
			(uint32_t)atomic_get(&out_of_order_sweeps),
		.notify_ok = (uint32_t)atomic_get(&notify_ok),
		.drop_unsub = (uint32_t)atomic_get(&drop_unsub),
		.drop_err = (uint32_t)atomic_get(&drop_err),
		.last_notify_error =
			(int32_t)atomic_get(&last_notify_error),
		.uart_restarts = (uint32_t)atomic_get(&uart_restarts),
		.last_uart_error = (int32_t)atomic_get(&last_uart_error),
		.last_sweep = (uint32_t)atomic_get(&last_sweep),
		.have_last_sweep = (uint8_t)(atomic_get(&have_last_sweep) != 0),
		.data_subscribed = (uint8_t)(atomic_get(&data_subscribed) != 0),
		.watchdog_feed_count =
			(uint32_t)atomic_get(&watchdog_feed_count),
		.reset_reason = boot_reset_reason,
		.imu_pulls = imu_stats.pulls,
		.imu_dup = imu_stats.duplicate_samples,
		.imu_i2c_err = imu_stats.i2c_errors,
		.imu_records = imu_stats.records,
		.ctrl_rx = (uint32_t)atomic_get(&ctrl_rx),
		.ctrl_bad_bsf = (uint32_t)atomic_get(&ctrl_bad_bsf),
		.relay_tx = (uint32_t)atomic_get(&relay_tx),
		.relay_ack = (uint32_t)atomic_get(&relay_ack),
		.relay_timeout = (uint32_t)atomic_get(&relay_timeout),
		.imu_rate_hz = imu_stats.rate_hz,
		.imu_batch = imu_stats.batch_size,
		.imu_active = imu_stats.active,
	};

	ARG_UNUSED(work);
	if (watchdog_ret != 0) {
		LOG_ERR("watchdog feed failed: %d", watchdog_ret);
	}
	bsf_strobe_capture_telemetry(&telemetry);

	if (atomic_get(&telemetry_subscribed) != 0) {
		(void)bt_gatt_notify(NULL, FUSION_TELEMETRY_ATTR,
				     &telemetry, sizeof(telemetry));
	}

	k_work_reschedule(&telemetry_work, K_SECONDS(1));
}

static int start_advertising(void)
{
	const struct bt_data scan_response[] = {
		BT_DATA(BT_DATA_NAME_COMPLETE, device_name,
			sizeof(device_name) - 1),
		BT_DATA(BT_DATA_MANUFACTURER_DATA,
			firmware_advertising_marker,
			sizeof(firmware_advertising_marker)),
	};

	return bt_le_adv_start(BT_LE_ADV_CONN,
			       advertising_data, ARRAY_SIZE(advertising_data),
			       scan_response, ARRAY_SIZE(scan_response));
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	ARG_UNUSED(conn);
	LOG_INF("BLE disconnected reason=0x%02x", reason);
	atomic_clear(&data_subscribed);
	atomic_clear(&telemetry_subscribed);
	(void)start_advertising();
}

static void connected(struct bt_conn *conn, uint8_t err)
{
	char address[BT_ADDR_LE_STR_LEN];

	if (err != 0u) {
		LOG_WRN("BLE connection failed err=0x%02x", err);
		return;
	}
	bt_addr_le_to_str(bt_conn_get_dst(conn), address, sizeof(address));
	LOG_INF("BLE connected peer=%s", address);
}

static void le_param_updated(struct bt_conn *conn, uint16_t interval,
			     uint16_t latency, uint16_t timeout)
{
	ARG_UNUSED(conn);
	LOG_INF("BLE CI negotiated interval_units=%u interval_us=%u latency=%u timeout_units=%u",
		interval, (uint32_t)interval * 1250u, latency, timeout);
}

BT_CONN_CB_DEFINE(connection_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
	.le_param_updated = le_param_updated,
};

int main(void)
{
	uint32_t deviceid0 = NRF_FICR->DEVICEID[0];
	uint32_t deviceid1 = NRF_FICR->DEVICEID[1];
	int ret;

	node_identity = bsl_identity_from_ficr(deviceid0, deviceid1);

	boot_reset_reason = nrfx_reset_reason_get();
	nrfx_reset_reason_clear(boot_reset_reason);

	ret = watchdog_start();
	if (ret != 0) {
		LOG_ERR("watchdog initialization failed: %d", ret);
		return 0;
	}

	if (!gpio_is_ready_dt(&status_led)) {
		LOG_ERR("status LED GPIO is not ready");
		return 0;
	}

	ret = gpio_pin_configure_dt(&status_led, GPIO_OUTPUT_INACTIVE);
	if (ret != 0) {
		LOG_ERR("status LED configuration failed: %d", ret);
		return 0;
	}

	snprintf(device_name, sizeof(device_name), "BSF%04X", node_identity);
	ret = bt_set_name(device_name);
	if (ret != 0) {
		LOG_ERR("BLE name setup failed: %d", ret);
		return 0;
	}

	LOG_INF("firmware=%s identity=0x%04X name=%s reset_reason=0x%08x watchdog_ms=%u",
		FW_MARKER, node_identity, device_name, boot_reset_reason,
		WATCHDOG_TIMEOUT_MS);

	ret = bsf_strobe_capture_init();
	if (ret != 0) {
		LOG_ERR("UWB strobe capture initialization failed: %d", ret);
		return 0;
	}

	ret = bsf_imu_init(publish_data_record);
	if (ret != 0) {
		LOG_ERR("JY61P initialization failed: %d", ret);
		return 0;
	}

	ret = bt_enable(NULL);
	if (ret != 0) {
		LOG_ERR("Bluetooth initialization failed: %d", ret);
		return 0;
	}

	ret = start_advertising();
	if (ret != 0) {
		LOG_ERR("BLE advertising failed: %d", ret);
		return 0;
	}

	ret = uart_start();
	if (ret != 0) {
		LOG_ERR("UWB UART start failed: %d", ret);
		return 0;
	}

	k_work_schedule(&telemetry_work, K_SECONDS(1));
	LOG_INF("UART/strobe/IMU bridge ready: rx=P1.01 tx=P1.02 ready=P1.03 i2c=P0.26/P0.27@400k baud=%u frame=%u",
		BSL_BAUDRATE, BSL_FRAME_LEN_EXPECTED);

	if (!boot_is_img_confirmed()) {
		ret = boot_write_img_confirmed();
		if (ret != 0) {
			LOG_ERR("MCUboot image confirmation failed: %d", ret);
		} else {
			LOG_INF("MCUboot image confirmed after BLE/UART health check");
		}
	}

	while (true) {
		ret = gpio_pin_toggle_dt(&status_led);
		if (ret != 0) {
			LOG_ERR("status LED toggle failed: %d", ret);
			return 0;
		}
		k_sleep(K_MSEC(500));
	}

	return 0;
}
