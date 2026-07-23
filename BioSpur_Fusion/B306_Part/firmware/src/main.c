#include <errno.h>
#include <stddef.h>
#include <stdio.h>
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
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/sys/util.h>

#include "biospur_fusion_ble.h"
#include "biospur_link.h"
#include "strobe_capture.h"

LOG_MODULE_REGISTER(biospur_fusion, LOG_LEVEL_INF);

#define LED0_NODE DT_ALIAS(led0)
#define UWB_UART_NODE DT_ALIAS(uwb_uart)

#define FW_MARKER "b306-remote-ready-v10"

#define UART_DMA_BUFFER_SIZE 256u
#define UART_RING_SIZE 2048u
#define UART_RX_TIMEOUT_US 2000
#define PARSER_STACK_SIZE 2048
#define PARSER_PRIORITY 5
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

static atomic_t data_subscribed;
static atomic_t telemetry_subscribed;

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
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE));

#define FUSION_DATA_ATTR (&fusion_service.attrs[2])
#define FUSION_TELEMETRY_ATTR (&fusion_service.attrs[5])

RING_BUF_DECLARE(uart_ring, UART_RING_SIZE);
K_SEM_DEFINE(uart_data_sem, 0, 1);

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
static atomic_t notify_dropped;
static atomic_t uart_restarts;
static atomic_t last_uart_error;
static atomic_t last_sweep;
static atomic_t have_last_sweep;
static atomic_t node_sequence;

static char device_name[8];
static uint8_t parser_frame[BSL_FRAME_LEN_EXPECTED];
static size_t parser_position;
static const uint8_t firmware_advertising_marker[] = {
	0xff, 0xff, 'B', '3', '0', '6', 'D', '2',
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

static void parser_resynchronise(void)
{
	for (size_t i = 1; i + 1 < parser_position; ++i) {
		if (parser_frame[i] == BSL_MAGIC0 &&
		    parser_frame[i + 1] == BSL_MAGIC1) {
			memmove(parser_frame, &parser_frame[i], parser_position - i);
			parser_position -= i;
			return;
		}
	}

	if (parser_position != 0u &&
	    parser_frame[parser_position - 1] == BSL_MAGIC0) {
		parser_frame[0] = BSL_MAGIC0;
		parser_position = 1u;
	} else {
		parser_position = 0u;
	}
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
	int err;

	bsf_strobe_capture_pair(frame->body.flags, &packet.capture);

	if (atomic_get(&data_subscribed) == 0) {
		atomic_inc(&notify_dropped);
		return;
	}

	err = bt_gatt_notify(NULL, FUSION_DATA_ATTR, &packet, sizeof(packet));
	if (err == 0) {
		atomic_inc(&notify_ok);
	} else {
		atomic_inc(&notify_dropped);
	}
}

static void parser_accept_frame(void)
{
	const bsl_frame_t *frame = (const bsl_frame_t *)parser_frame;
	uint16_t received_crc;
	uint16_t calculated_crc;

	if (frame->hdr.version != BSL_VERSION ||
	    frame->hdr.len != sizeof(bsl_uwb_t)) {
		atomic_inc(&header_errors);
		parser_resynchronise();
		return;
	}

	memcpy(&received_crc, &frame->crc, sizeof(received_crc));
	calculated_crc = crc16_ccitt_false(
		parser_frame, offsetof(bsl_frame_t, crc));
	if (received_crc != calculated_crc) {
		atomic_inc(&crc_errors);
		parser_resynchronise();
		return;
	}

	atomic_inc(&valid_frames);
	account_sweep(frame->body.sweep);
	publish_uwb(frame);
	parser_position = 0u;
}

static void parser_consume_byte(uint8_t byte)
{
	if (parser_position == 0u) {
		if (byte == BSL_MAGIC0) {
			parser_frame[parser_position++] = byte;
		}
		return;
	}

	if (parser_position == 1u) {
		if (byte == BSL_MAGIC1) {
			parser_frame[parser_position++] = byte;
		} else if (byte != BSL_MAGIC0) {
			parser_position = 0u;
		}
		return;
	}

	parser_frame[parser_position++] = byte;
	if (parser_position == sizeof(parser_frame)) {
		parser_accept_frame();
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

static void telemetry_work_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(telemetry_work, telemetry_work_handler);

static void telemetry_work_handler(struct k_work *work)
{
	int watchdog_ret = watchdog_feed_once();
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
		.notify_dropped = (uint32_t)atomic_get(&notify_dropped),
		.uart_restarts = (uint32_t)atomic_get(&uart_restarts),
		.last_uart_error = (int32_t)atomic_get(&last_uart_error),
		.last_sweep = (uint32_t)atomic_get(&last_sweep),
		.have_last_sweep = (uint8_t)(atomic_get(&have_last_sweep) != 0),
		.data_subscribed = (uint8_t)(atomic_get(&data_subscribed) != 0),
		.watchdog_feed_count =
			(uint32_t)atomic_get(&watchdog_feed_count),
		.reset_reason = boot_reset_reason,
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

BT_CONN_CB_DEFINE(connection_callbacks) = {
	.disconnected = disconnected,
};

int main(void)
{
	uint32_t deviceid0 = NRF_FICR->DEVICEID[0];
	uint32_t deviceid1 = NRF_FICR->DEVICEID[1];
	uint16_t identity = bsl_identity_from_ficr(deviceid0, deviceid1);
	int ret;

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

	snprintf(device_name, sizeof(device_name), "BSF%04X", identity);
	ret = bt_set_name(device_name);
	if (ret != 0) {
		LOG_ERR("BLE name setup failed: %d", ret);
		return 0;
	}

	LOG_INF("firmware=%s identity=0x%04X name=%s reset_reason=0x%08x watchdog_ms=%u",
		FW_MARKER, identity, device_name, boot_reset_reason,
		WATCHDOG_TIMEOUT_MS);

	ret = bsf_strobe_capture_init();
	if (ret != 0) {
		LOG_ERR("UWB strobe capture initialization failed: %d", ret);
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
	LOG_INF("UART/strobe bridge ready: rx=P1.01 ready=P1.03 baud=%u frame=%u",
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
