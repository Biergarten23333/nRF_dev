/*
 * BioSpur Fusion Master diagnostic bridge for nRF52840 DK 683234364.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <errno.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/net_buf.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>
#include <zephyr/usb/usb_device.h>

#include "biospur_fusion_ble.h"

#define TARGET_NAME_PREFIX "BSF"
#define TARGET_NAME_LEN 7
#define CDC_LINE_MAX 1024
#define CDC_COMMAND_MAX BSF_CONTROL_LINE_MAX

#define CDC_ACM_NODE DT_NODELABEL(cdc_acm_uart0)

static const struct device *const cdc_acm = DEVICE_DT_GET(CDC_ACM_NODE);
RING_BUF_DECLARE(cdc_tx_ring, 16384);
RING_BUF_DECLARE(cdc_rx_ring, 1024);
K_SEM_DEFINE(cdc_rx_sem, 0, 1);
static bool cdc_ready;

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

static struct bt_conn *target_conn;
static struct bt_gatt_exchange_params exchange_params;
static struct bt_gatt_discover_params discover_params;
static struct bt_gatt_subscribe_params data_subscribe_params;
static struct bt_gatt_subscribe_params telemetry_subscribe_params;

static bt_addr_le_t candidate_addr;
static bool candidate_valid;
static bool candidate_has_service;
static bool candidate_has_name;
static bool connecting;
static bool bridge_ready;
static int8_t candidate_rssi;
static char candidate_name[TARGET_NAME_LEN + 1];
static uint16_t service_end_handle;
static uint16_t data_value_handle;
static uint16_t telemetry_value_handle;
static uint16_t control_value_handle;
static uint32_t received_packets;
static uint32_t malformed_packets;
static uint32_t reconnections;
static uint32_t logger_dropped;
static uint32_t cdc_dropped_bytes;

enum discovery_stage {
	DISCOVERY_SERVICE,
	DISCOVERY_DATA_CHARACTERISTIC,
	DISCOVERY_DATA_CCC,
	DISCOVERY_TELEMETRY_CHARACTERISTIC,
	DISCOVERY_TELEMETRY_CCC,
	DISCOVERY_CONTROL_CHARACTERISTIC,
};

static enum discovery_stage discovery_stage;

struct advertising_fields {
	bool has_service;
	bool has_name;
	char name[TARGET_NAME_LEN + 1];
};

enum fusion_log_kind {
	FUSION_LOG_UWB = 1,
	FUSION_LOG_TELEMETRY = 2,
	FUSION_LOG_IMU = 3,
	FUSION_LOG_REPLY = 4,
};

struct fusion_imu_log {
	bsf_ble_imu_prefix_t prefix;
	bsf_ble_imu_sample_t samples[BSF_IMU_BATCH_MAX];
	int16_t temperature;
	uint8_t sample_count;
};

struct fusion_reply_log {
	bsf_ble_control_reply_prefix_t prefix;
	char text[BSF_CONTROL_REPLY_TEXT_MAX + 1u];
};

struct fusion_log_record {
	uint64_t master_arrival_ms;
	uint8_t kind;
	union {
		bsf_ble_uwb_packet_t uwb;
		bsf_ble_telemetry_t telemetry;
		struct fusion_imu_log imu;
		struct fusion_reply_log reply;
	} payload;
};

K_MSGQ_DEFINE(fusion_log_queue, sizeof(struct fusion_log_record), 32, 4);

static void start_scan(void);
static void start_fusion_discovery(struct bt_conn *conn);

static void cdc_uart_callback(const struct device *dev, void *user_data)
{
	uint8_t buffer[64];

	ARG_UNUSED(user_data);
	if (uart_irq_update(dev) == 0) {
		return;
	}

	if (uart_irq_rx_ready(dev) != 0) {
		int count = uart_fifo_read(dev, buffer, sizeof(buffer));

		if (count > 0) {
			unsigned int key = irq_lock();

			(void)ring_buf_put(&cdc_rx_ring, buffer, (uint32_t)count);
			irq_unlock(key);
			k_sem_give(&cdc_rx_sem);
		}
	}

	if (uart_irq_tx_ready(dev) != 0) {
		uint8_t *data;
		unsigned int key = irq_lock();
		uint32_t claimed =
			ring_buf_get_claim(&cdc_tx_ring, &data, sizeof(buffer));
		int sent = claimed == 0u ? 0 :
			uart_fifo_fill(dev, data, claimed);

		(void)ring_buf_get_finish(&cdc_tx_ring,
					  sent > 0 ? (uint32_t)sent : 0u);
		if (ring_buf_is_empty(&cdc_tx_ring)) {
			uart_irq_tx_disable(dev);
		}
		irq_unlock(key);
	}
}

static int cdc_start(void)
{
	int err;

	if (!device_is_ready(cdc_acm)) {
		return -ENODEV;
	}
	err = usb_enable(NULL);
	if (err != 0 && err != -EALREADY) {
		return err;
	}
	err = uart_irq_callback_user_data_set(cdc_acm,
					      cdc_uart_callback, NULL);
	if (err != 0) {
		return err;
	}
	uart_irq_rx_enable(cdc_acm);
	cdc_ready = true;
	return 0;
}

static void fusion_printf(const char *format, ...)
{
	char line[CDC_LINE_MAX];
	va_list args;
	int written;

	va_start(args, format);
	written = vsnprintf(line, sizeof(line), format, args);
	va_end(args);
	if (written < 0) {
		return;
	}
	line[sizeof(line) - 1u] = '\0';
	printk("%s", line);

	if (cdc_ready) {
		size_t length = strnlen(line, sizeof(line));
		unsigned int key = irq_lock();
		uint32_t accepted;

		accepted = ring_buf_put(&cdc_tx_ring,
				       (const uint8_t *)line,
				       (uint32_t)length);
		cdc_dropped_bytes += (uint32_t)length - accepted;
		uart_irq_tx_enable(cdc_acm);
		irq_unlock(key);
	}
}

/* Every existing instrument line now goes to native USB CDC and RTT. */
#define printk(...) fusion_printf(__VA_ARGS__)

static const char *capture_verdict_name(uint8_t verdict)
{
	switch (verdict) {
	case BSF_CAPTURE_HEALTHY:
		return "healthy";
	case BSF_CAPTURE_B306_MISSED_EDGE:
		return "b306_missed_edge";
	case BSF_CAPTURE_TAG_NO_POLL_TX:
		return "tag_no_poll";
	case BSF_CAPTURE_CONTRADICTION:
		return "contradiction";
	default:
		return "invalid";
	}
}

static const char *capture_edge_name(uint8_t shape)
{
	switch (shape) {
	case BSF_CAPTURE_EDGE_NONE:
		return "none";
	case BSF_CAPTURE_EDGE_ACTIVE_HIGH:
		return "active_high";
	case BSF_CAPTURE_EDGE_ACTIVE_LOW:
		return "active_low";
	case BSF_CAPTURE_EDGE_RISING_ONLY:
		return "rising_only";
	case BSF_CAPTURE_EDGE_FALLING_ONLY:
		return "falling_only";
	default:
		return "invalid";
	}
}

static void format_capture_timestamp(char *buffer, size_t size,
				     uint64_t timestamp)
{
	if (timestamp == BSF_CAPTURE_TS_ABSENT) {
		(void)snprintf(buffer, size, "-");
	} else {
		(void)snprintf(buffer, size, "%llu",
			       (unsigned long long)timestamp);
	}
}

static void format_capture_delta(char *buffer, size_t size, uint32_t delta)
{
	if (delta == BSF_CAPTURE_DELTA_ABSENT) {
		(void)snprintf(buffer, size, "-");
	} else {
		(void)snprintf(buffer, size, "%u", delta);
	}
}

static void log_uwb_record(const struct fusion_log_record *record)
{
	const bsf_ble_uwb_packet_t *packet = &record->payload.uwb;
	char ranges[160];
	char strobe_timestamp[24];
	char rising_timestamp[24];
	char falling_timestamp[24];
	char orphan_timestamp[24];
	char pair_delta[16];
	size_t used = 0u;
	uint64_t poll_tx_timestamp;

	ranges[0] = '\0';
	for (unsigned int i = 0; i < BSL_MAX_ANCHORS; ++i) {
		int written;

		if (packet->uwb.anchor_id[i] == BSL_ANCHOR_NONE) {
			continue;
		}
		written = snprintf(&ranges[used], sizeof(ranges) - used,
				   "%s%u:%u",
				   used == 0u ? "" : ",",
				   packet->uwb.anchor_id[i],
				   packet->uwb.range_mm[i]);
		if (written < 0 || (size_t)written >= sizeof(ranges) - used) {
			break;
		}
		used += (size_t)written;
	}

	poll_tx_timestamp = bsl_ts40_get(packet->uwb.poll_tx_ts);
	format_capture_timestamp(strobe_timestamp, sizeof(strobe_timestamp),
				 packet->capture.strobe_ts_us);
	format_capture_timestamp(rising_timestamp, sizeof(rising_timestamp),
				 packet->capture.rising_ts_us);
	format_capture_timestamp(falling_timestamp, sizeof(falling_timestamp),
				 packet->capture.falling_ts_us);
	format_capture_timestamp(orphan_timestamp, sizeof(orphan_timestamp),
				 packet->capture.last_orphan_strobe_ts_us);
	format_capture_delta(pair_delta, sizeof(pair_delta),
			     packet->capture.frame_to_strobe_us);

	printk("FUSION_UWB proto=%u master_ms=%llu node_ms=%u pkt=%u sweep=%u identity=%04X logical=%u poll_tx=%010llX frame_us=%llu strobe_us=%s rise_us=%s fall_us=%s pair_dt_us=%s verdict=%s edge=%s candidates=%u window_us=%u valid=0x%02x flags=0x%02x strobe_sent=%u rise_n=%u fall_n=%u boot_discard=%u edge_qdrop=%u orphan_strobe=%u orphan_edge=%u orphan_frame=%u near_window=%u last_orphan_us=%s capture_flags=0x%02x ranges=%s\n",
	       packet->version,
	       (unsigned long long)record->master_arrival_ms,
	       packet->node_uptime_ms,
	       packet->node_sequence,
	       packet->uwb.sweep,
	       packet->uwb.identity_code,
	       packet->uwb.logical_tag_id,
	       (unsigned long long)poll_tx_timestamp,
	       (unsigned long long)packet->capture.frame_rx_ts_us,
	       strobe_timestamp,
	       rising_timestamp,
	       falling_timestamp,
	       pair_delta,
	       capture_verdict_name(packet->capture.verdict),
	       capture_edge_name(packet->capture.edge_shape),
	       packet->capture.pair_candidates,
	       packet->capture.pairing_window_us,
	       packet->uwb.valid_mask,
	       packet->uwb.flags,
	       (packet->uwb.flags & BSL_FLAG_STROBE_SENT) != 0u,
	       packet->capture.rising_edge_count,
	       packet->capture.falling_edge_count,
	       packet->capture.boot_discarded_edge_count,
	       packet->capture.edge_queue_drop_count,
	       packet->capture.orphan_strobe_count,
	       packet->capture.orphan_edge_count,
	       packet->capture.orphan_frame_count,
	       packet->capture.near_window_edge_count,
	       orphan_timestamp,
	       packet->capture.capture_flags,
	       ranges[0] != '\0' ? ranges : "-");
}

static void log_telemetry_record(const struct fusion_log_record *record)
{
	const bsf_ble_telemetry_t *telemetry = &record->payload.telemetry;

	printk("FUSION_TELEMETRY proto=%u node_ms=%u bytes=%u frames=%u crc=%u header=%u ring_drop=%u sweep_drop=%u duplicate=%u reorder=%u notify_ok=%u drop_unsub=%u drop_err=%u notify_errno=%d uart_restarts=%u uart_err=%d last_sweep=%u have=%u subscribed=%u rise_n=%u fall_n=%u boot_discard=%u edge_qdrop=%u orphan_strobe=%u orphan_edge=%u orphan_frame=%u near_window=%u capture_flags=0x%02x timer=%u window_us=%u timer_wraps=%u watchdog_feeds=%u reset_reason=0x%08x imu_pulls=%u imu_dup=%u imu_i2c_err=%u imu_records=%u imu_rate=%u imu_batch=%u imu_active=%u ctrl_rx=%u ctrl_bad_bsf=%u relay_tx=%u relay_ack=%u relay_timeout=%u master_rx=%u malformed=%u logger_drop=%u\n",
	       telemetry->version,
	       telemetry->node_uptime_ms,
	       telemetry->uart_bytes,
	       telemetry->valid_frames,
	       telemetry->crc_errors,
	       telemetry->header_errors,
	       telemetry->ring_dropped_bytes,
	       telemetry->dropped_sweeps,
	       telemetry->duplicate_sweeps,
	       telemetry->out_of_order_sweeps,
	       telemetry->notify_ok,
	       telemetry->drop_unsub,
	       telemetry->drop_err,
	       telemetry->last_notify_error,
	       telemetry->uart_restarts,
	       telemetry->last_uart_error,
	       telemetry->last_sweep,
	       telemetry->have_last_sweep,
	       telemetry->data_subscribed,
	       telemetry->rising_edge_count,
	       telemetry->falling_edge_count,
	       telemetry->boot_discarded_edge_count,
	       telemetry->edge_queue_drop_count,
	       telemetry->orphan_strobe_count,
	       telemetry->orphan_edge_count,
	       telemetry->orphan_frame_count,
	       telemetry->near_window_edge_count,
	       telemetry->capture_flags,
	       telemetry->timer_instance,
	       telemetry->pairing_window_us,
	       telemetry->timer_wrap_count,
	       telemetry->watchdog_feed_count,
	       telemetry->reset_reason,
	       telemetry->imu_pulls,
	       telemetry->imu_dup,
	       telemetry->imu_i2c_err,
	       telemetry->imu_records,
	       telemetry->imu_rate_hz,
	       telemetry->imu_batch,
	       telemetry->imu_active,
	       telemetry->ctrl_rx,
	       telemetry->ctrl_bad_bsf,
	       telemetry->relay_tx,
	       telemetry->relay_ack,
	       telemetry->relay_timeout,
	       received_packets,
	       malformed_packets,
	       logger_dropped);
}

static void log_imu_record(const struct fusion_log_record *record)
{
	const struct fusion_imu_log *imu = &record->payload.imu;
	char samples[480];
	size_t used = 0u;

	samples[0] = '\0';
	for (uint8_t i = 0; i < imu->sample_count; ++i) {
		const bsf_ble_imu_sample_t *sample = &imu->samples[i];
		int written = snprintf(
			&samples[used], sizeof(samples) - used,
			"%s%u,%d,%d,%d,%d,%d,%d",
			i == 0u ? "" : ";",
			sample->delta_us,
			sample->acc[0], sample->acc[1], sample->acc[2],
			sample->gyro[0], sample->gyro[1], sample->gyro[2]);

		if (written < 0 || (size_t)written >= sizeof(samples) - used) {
			break;
		}
		used += (size_t)written;
	}
	printk("FUSION_IMU proto=%u master_ms=%llu seq=%u base_us=%u n=%u temp_raw=%d samples=%s\n",
	       imu->prefix.version,
	       (unsigned long long)record->master_arrival_ms,
	       imu->prefix.seq,
	       imu->prefix.base_timer2_ts_us,
	       imu->sample_count,
	       imu->temperature,
	       samples);
}

static void log_reply_record(const struct fusion_log_record *record)
{
	const struct fusion_reply_log *reply = &record->payload.reply;

	printk("FUSION_REPLY proto=%u master_ms=%llu source=%s correlation=%u text=%s\n",
	       reply->prefix.version,
	       (unsigned long long)record->master_arrival_ms,
	       reply->prefix.source == BSF_CONTROL_SOURCE_TAG ? "TAG" : "B306",
	       reply->prefix.correlation,
	       reply->text);
}

static void fusion_log_thread(void *first, void *second, void *third)
{
	struct fusion_log_record record;

	ARG_UNUSED(first);
	ARG_UNUSED(second);
	ARG_UNUSED(third);
	while (true) {
		(void)k_msgq_get(&fusion_log_queue, &record, K_FOREVER);
		if (record.kind == FUSION_LOG_UWB) {
			log_uwb_record(&record);
		} else if (record.kind == FUSION_LOG_TELEMETRY) {
			log_telemetry_record(&record);
		} else if (record.kind == FUSION_LOG_IMU) {
			log_imu_record(&record);
		} else if (record.kind == FUSION_LOG_REPLY) {
			log_reply_record(&record);
		}
	}
}

K_THREAD_DEFINE(fusion_logger, 4096, fusion_log_thread,
		NULL, NULL, NULL, 8, 0, 0);

static bool advertising_field(struct bt_data *data, void *user_data)
{
	struct advertising_fields *fields = user_data;
	size_t copy_len;

	switch (data->type) {
	case BT_DATA_NAME_SHORTENED:
	case BT_DATA_NAME_COMPLETE:
		copy_len = MIN(data->data_len, TARGET_NAME_LEN);
		memcpy(fields->name, data->data, copy_len);
		fields->name[copy_len] = '\0';
		fields->has_name =
			(data->data_len == TARGET_NAME_LEN) &&
			(strncmp(fields->name, TARGET_NAME_PREFIX,
				 strlen(TARGET_NAME_PREFIX)) == 0);
		break;

	case BT_DATA_UUID128_SOME:
	case BT_DATA_UUID128_ALL:
		for (size_t offset = 0;
		     offset + sizeof(fusion_service_uuid.val) <= data->data_len;
		     offset += sizeof(fusion_service_uuid.val)) {
			struct bt_uuid_128 advertised_uuid;

			advertised_uuid.uuid.type = BT_UUID_TYPE_128;
			memcpy(advertised_uuid.val, &data->data[offset],
			       sizeof(advertised_uuid.val));
			if (bt_uuid_cmp(&advertised_uuid.uuid,
					&fusion_service_uuid.uuid) == 0) {
				fields->has_service = true;
				break;
			}
		}
		break;

	default:
		break;
	}

	return true;
}

static void connect_candidate(void)
{
	static const struct bt_le_conn_param conn_params = {
		.interval_min = 12, /* 15 ms */
		.interval_max = 24, /* 30 ms */
		.latency = 0,
		.timeout = 400, /* 4 s */
	};
	char addr[BT_ADDR_LE_STR_LEN];
	int err;

	if (connecting || target_conn || !candidate_has_service ||
	    !candidate_has_name) {
		return;
	}

	connecting = true;
	bt_addr_le_to_str(&candidate_addr, addr, sizeof(addr));
	printk("FUSION_TARGET name=%s addr=%s rssi=%d\n",
	       candidate_name, addr, candidate_rssi);

	err = bt_le_scan_stop();
	if (err != 0 && err != -EALREADY) {
		printk("FUSION_FAIL step=scan_stop err=%d\n", err);
		connecting = false;
		return;
	}

	err = bt_conn_le_create(&candidate_addr, BT_CONN_LE_CREATE_CONN,
				&conn_params, &target_conn);
	if (err != 0) {
		printk("FUSION_FAIL step=connect_start err=%d\n", err);
		connecting = false;
		target_conn = NULL;
		start_scan();
	}
}

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	struct advertising_fields fields = { 0 };

	ARG_UNUSED(type);

	if (connecting || target_conn) {
		return;
	}

	bt_data_parse(ad, advertising_field, &fields);
	if (!fields.has_service && !fields.has_name) {
		return;
	}

	if (!candidate_valid || bt_addr_le_cmp(addr, &candidate_addr) != 0) {
		bt_addr_le_copy(&candidate_addr, addr);
		candidate_valid = true;
		candidate_has_service = false;
		candidate_has_name = false;
		memset(candidate_name, 0, sizeof(candidate_name));
	}

	candidate_rssi = rssi;
	if (fields.has_service) {
		candidate_has_service = true;
	}
	if (fields.has_name) {
		candidate_has_name = true;
		memcpy(candidate_name, fields.name, sizeof(candidate_name));
	}

	connect_candidate();
}

static void start_scan(void)
{
	static const struct bt_le_scan_param scan_params = {
		.type = BT_LE_SCAN_TYPE_ACTIVE,
		.options = BT_LE_SCAN_OPT_NONE,
		.interval = BT_GAP_SCAN_FAST_INTERVAL,
		.window = BT_GAP_SCAN_FAST_WINDOW,
	};
	int err;

	candidate_valid = false;
	candidate_has_service = false;
	candidate_has_name = false;
	memset(candidate_name, 0, sizeof(candidate_name));

	err = bt_le_scan_start(&scan_params, device_found);
	if (err != 0 && err != -EALREADY) {
		printk("FUSION_FAIL step=scan_start err=%d\n", err);
		return;
	}

	printk("FUSION_SCAN_STARTED target=BSFxxxx service=7b120001\n");
}

static uint8_t data_notification(
	struct bt_conn *conn,
	struct bt_gatt_subscribe_params *params,
	const void *data, uint16_t length)
{
	struct fusion_log_record record = {
		.master_arrival_ms = (uint64_t)k_uptime_get(),
	};
	const uint8_t *bytes = data;
	uint16_t declared_length;
	uint8_t kind;

	ARG_UNUSED(conn);

	if (data == NULL) {
		printk("FUSION_DATA_UNSUBSCRIBED\n");
		params->value_handle = 0;
		return BT_GATT_ITER_STOP;
	}

	if (length < 4u) {
		++malformed_packets;
		printk("FUSION_MALFORMED kind=data_short len=%u total=%u\n",
		       length, malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}
	memcpy(&declared_length, &bytes[2], sizeof(declared_length));
	kind = bytes[1];
	if (bytes[0] != BSF_BLE_PROTOCOL_VERSION ||
	    declared_length != length) {
		++malformed_packets;
		printk("FUSION_MALFORMED kind=data_header version=%u type=%u declared=%u actual=%u total=%u\n",
		       bytes[0], kind, declared_length, length,
		       malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}

	if (kind == BSF_BLE_KIND_UWB) {
		bsf_ble_uwb_packet_t *packet = &record.payload.uwb;

		if (length != sizeof(*packet)) {
			goto malformed_kind_length;
		}
		record.kind = FUSION_LOG_UWB;
		memcpy(packet, data, sizeof(*packet));
	} else if (kind == BSF_BLE_KIND_IMU) {
		struct fusion_imu_log *imu = &record.payload.imu;
		size_t sample_bytes;
		size_t sample_count;
		size_t temperature_offset;

		if (length < BSF_IMU_RECORD_LEN(BSF_IMU_BATCH_MIN)) {
			goto malformed_kind_length;
		}
		sample_bytes = length - sizeof(bsf_ble_imu_prefix_t) -
			sizeof(int16_t);
		if (sample_bytes % sizeof(bsf_ble_imu_sample_t) != 0u) {
			goto malformed_kind_length;
		}
		sample_count = sample_bytes / sizeof(bsf_ble_imu_sample_t);
		if (sample_count < BSF_IMU_BATCH_MIN ||
		    sample_count > BSF_IMU_BATCH_MAX) {
			goto malformed_kind_length;
		}
		record.kind = FUSION_LOG_IMU;
		memcpy(&imu->prefix, data, sizeof(imu->prefix));
		memcpy(imu->samples, &bytes[sizeof(imu->prefix)],
		       sample_bytes);
		temperature_offset = sizeof(imu->prefix) + sample_bytes;
		memcpy(&imu->temperature, &bytes[temperature_offset],
		       sizeof(imu->temperature));
		imu->sample_count = (uint8_t)sample_count;
	} else if (kind == BSF_BLE_KIND_CONTROL_REPLY) {
		struct fusion_reply_log *reply = &record.payload.reply;
		size_t text_length;

		if (length < sizeof(reply->prefix) ||
		    length > sizeof(reply->prefix) +
			     BSF_CONTROL_REPLY_TEXT_MAX) {
			goto malformed_kind_length;
		}
		memcpy(&reply->prefix, data, sizeof(reply->prefix));
		if (reply->prefix.source != BSF_CONTROL_SOURCE_B306 &&
		    reply->prefix.source != BSF_CONTROL_SOURCE_TAG) {
			goto malformed_kind_length;
		}
		text_length = length - sizeof(reply->prefix);
		if (memchr(&bytes[sizeof(reply->prefix)], '\0',
			   text_length) != NULL) {
			goto malformed_kind_length;
		}
		record.kind = FUSION_LOG_REPLY;
		memcpy(reply->text, &bytes[sizeof(reply->prefix)],
		       text_length);
		reply->text[text_length] = '\0';
	} else {
		++malformed_packets;
		printk("FUSION_MALFORMED kind=unknown type=%u len=%u total=%u\n",
		       kind, length, malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}

	++received_packets;
	if (k_msgq_put(&fusion_log_queue, &record, K_NO_WAIT) != 0) {
		++logger_dropped;
	}

	return BT_GATT_ITER_CONTINUE;

malformed_kind_length:
	++malformed_packets;
	printk("FUSION_MALFORMED kind=%u len=%u total=%u\n",
	       kind, length, malformed_packets);
	return BT_GATT_ITER_CONTINUE;
}

static uint8_t telemetry_notification(
	struct bt_conn *conn,
	struct bt_gatt_subscribe_params *params,
	const void *data, uint16_t length)
{
	struct fusion_log_record record = {
		.master_arrival_ms = (uint64_t)k_uptime_get(),
		.kind = FUSION_LOG_TELEMETRY,
	};
	bsf_ble_telemetry_t *telemetry = &record.payload.telemetry;

	ARG_UNUSED(conn);

	if (data == NULL) {
		printk("FUSION_TELEMETRY_UNSUBSCRIBED\n");
		params->value_handle = 0;
		return BT_GATT_ITER_STOP;
	}

	if (length != sizeof(*telemetry)) {
		++malformed_packets;
		printk("FUSION_MALFORMED kind=telemetry len=%u expected=%u total=%u\n",
		       length, (unsigned int)sizeof(*telemetry), malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}

	memcpy(telemetry, data, sizeof(*telemetry));
	if (telemetry->version != BSF_BLE_PROTOCOL_VERSION ||
	    telemetry->kind != BSF_BLE_KIND_TELEMETRY ||
	    telemetry->len != sizeof(*telemetry)) {
		++malformed_packets;
		printk("FUSION_MALFORMED kind=telemetry_header version=%u type=%u declared=%u total=%u\n",
		       telemetry->version, telemetry->kind, telemetry->len,
		       malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}

	if (k_msgq_put(&fusion_log_queue, &record, K_NO_WAIT) != 0) {
		++logger_dropped;
	}

	return BT_GATT_ITER_CONTINUE;
}

static uint8_t discover_fusion(struct bt_conn *conn,
			       const struct bt_gatt_attr *attr,
			       struct bt_gatt_discover_params *params)
{
	int err;

	if (attr == NULL) {
		printk("FUSION_FAIL step=discover_%u err=not_found\n",
		       discovery_stage);
		memset(params, 0, sizeof(*params));
		return BT_GATT_ITER_STOP;
	}

	switch (discovery_stage) {
	case DISCOVERY_SERVICE: {
		const struct bt_gatt_service_val *service = attr->user_data;

		service_end_handle = service->end_handle;
		printk("FUSION_SERVICE start=%u end=%u\n",
		       attr->handle, service_end_handle);

		discovery_stage = DISCOVERY_DATA_CHARACTERISTIC;
		discover_params.uuid = &fusion_data_uuid.uuid;
		discover_params.start_handle = attr->handle + 1;
		discover_params.end_handle = service_end_handle;
		discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;
		err = bt_gatt_discover(conn, &discover_params);
		if (err != 0) {
			printk("FUSION_FAIL step=discover_data_start err=%d\n",
			       err);
		}
		break;
	}

	case DISCOVERY_DATA_CHARACTERISTIC: {
		const struct bt_gatt_chrc *characteristic = attr->user_data;

		data_value_handle = characteristic->value_handle;
		printk("FUSION_DATA_CHARACTERISTIC value=%u props=0x%02x\n",
		       data_value_handle, characteristic->properties);
		if ((characteristic->properties & BT_GATT_CHRC_NOTIFY) == 0u) {
			printk("FUSION_FAIL step=data_not_notifiable\n");
			break;
		}

		discovery_stage = DISCOVERY_DATA_CCC;
		discover_params.uuid = BT_UUID_GATT_CCC;
		discover_params.start_handle = data_value_handle + 1;
		discover_params.end_handle = service_end_handle;
		discover_params.type = BT_GATT_DISCOVER_DESCRIPTOR;
		err = bt_gatt_discover(conn, &discover_params);
		if (err != 0) {
			printk("FUSION_FAIL step=discover_data_ccc_start err=%d\n",
			       err);
		}
		break;
	}

	case DISCOVERY_DATA_CCC:
		data_subscribe_params.notify = data_notification;
		data_subscribe_params.value = BT_GATT_CCC_NOTIFY;
		data_subscribe_params.value_handle = data_value_handle;
		data_subscribe_params.ccc_handle = attr->handle;
		err = bt_gatt_subscribe(conn, &data_subscribe_params);
		if (err != 0 && err != -EALREADY) {
			printk("FUSION_FAIL step=subscribe_data err=%d\n", err);
			break;
		}
		printk("FUSION_DATA_SUBSCRIBED value=%u ccc=%u\n",
		       data_value_handle, attr->handle);

		discovery_stage = DISCOVERY_TELEMETRY_CHARACTERISTIC;
		discover_params.uuid = &fusion_telemetry_uuid.uuid;
		discover_params.start_handle = attr->handle + 1;
		discover_params.end_handle = service_end_handle;
		discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;
		err = bt_gatt_discover(conn, &discover_params);
		if (err != 0) {
			printk("FUSION_FAIL step=discover_telemetry_start err=%d\n",
			       err);
		}
		break;

	case DISCOVERY_TELEMETRY_CHARACTERISTIC: {
		const struct bt_gatt_chrc *characteristic = attr->user_data;

		telemetry_value_handle = characteristic->value_handle;
		printk("FUSION_TELEMETRY_CHARACTERISTIC value=%u props=0x%02x\n",
		       telemetry_value_handle, characteristic->properties);
		if ((characteristic->properties & BT_GATT_CHRC_NOTIFY) == 0u) {
			printk("FUSION_FAIL step=telemetry_not_notifiable\n");
			break;
		}

		discovery_stage = DISCOVERY_TELEMETRY_CCC;
		discover_params.uuid = BT_UUID_GATT_CCC;
		discover_params.start_handle = telemetry_value_handle + 1u;
		discover_params.end_handle = service_end_handle;
		discover_params.type = BT_GATT_DISCOVER_DESCRIPTOR;
		err = bt_gatt_discover(conn, &discover_params);
		if (err != 0) {
			printk("FUSION_FAIL step=discover_telemetry_ccc_start err=%d\n",
			       err);
		}
		break;
	}

	case DISCOVERY_TELEMETRY_CCC:
		telemetry_subscribe_params.notify = telemetry_notification;
		telemetry_subscribe_params.value = BT_GATT_CCC_NOTIFY;
		telemetry_subscribe_params.value_handle = telemetry_value_handle;
		telemetry_subscribe_params.ccc_handle = attr->handle;
		err = bt_gatt_subscribe(conn, &telemetry_subscribe_params);
		if (err != 0 && err != -EALREADY) {
			printk("FUSION_FAIL step=subscribe_telemetry err=%d\n",
			       err);
			break;
		}
		printk("FUSION_TELEMETRY_SUBSCRIBED value=%u ccc=%u\n",
		       telemetry_value_handle, attr->handle);

		discovery_stage = DISCOVERY_CONTROL_CHARACTERISTIC;
		discover_params.uuid = &fusion_control_uuid.uuid;
		discover_params.start_handle = attr->handle + 1u;
		discover_params.end_handle = service_end_handle;
		discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;
		err = bt_gatt_discover(conn, &discover_params);
		if (err != 0) {
			printk("FUSION_FAIL step=discover_control_start err=%d\n",
			       err);
		}
		break;

	case DISCOVERY_CONTROL_CHARACTERISTIC: {
		const struct bt_gatt_chrc *characteristic = attr->user_data;

		control_value_handle = characteristic->value_handle;
		printk("FUSION_CONTROL_CHARACTERISTIC value=%u props=0x%02x\n",
		       control_value_handle, characteristic->properties);
		if ((characteristic->properties &
		     (BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP)) ==
		    0u) {
			printk("FUSION_FAIL step=control_not_writable\n");
			break;
		}

		bridge_ready = true;
		printk("FUSION_BRIDGE_READY name=%s rssi=%d mtu=%u data=%u telemetry=%u control=%u\n",
		       candidate_name, candidate_rssi, bt_gatt_get_mtu(conn),
		       data_value_handle, telemetry_value_handle,
		       control_value_handle);
		memset(params, 0, sizeof(*params));
		break;
	}
	}

	return BT_GATT_ITER_STOP;
}

static void start_fusion_discovery(struct bt_conn *conn)
{
	int err;

	memset(&discover_params, 0, sizeof(discover_params));
	discovery_stage = DISCOVERY_SERVICE;
	discover_params.uuid = &fusion_service_uuid.uuid;
	discover_params.func = discover_fusion;
	discover_params.start_handle = BT_ATT_FIRST_ATTRIBUTE_HANDLE;
	discover_params.end_handle = BT_ATT_LAST_ATTRIBUTE_HANDLE;
	discover_params.type = BT_GATT_DISCOVER_PRIMARY;

	err = bt_gatt_discover(conn, &discover_params);
	if (err != 0) {
		printk("FUSION_FAIL step=discover_service_start err=%d\n", err);
	}
}

static void mtu_exchanged(struct bt_conn *conn, uint8_t err,
			  struct bt_gatt_exchange_params *params)
{
	ARG_UNUSED(params);
	printk("FUSION_ATT_MTU value=%u err=%u\n", bt_gatt_get_mtu(conn), err);
	start_fusion_discovery(conn);
}

static void connected(struct bt_conn *conn, uint8_t conn_err)
{
	static const struct bt_conn_le_phy_param phy_params = {
		.options = BT_CONN_LE_PHY_OPT_NONE,
		.pref_tx_phy = BT_GAP_LE_PHY_2M,
		.pref_rx_phy = BT_GAP_LE_PHY_2M,
	};
	char addr[BT_ADDR_LE_STR_LEN];
	int err;

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	if (conn_err != 0) {
		printk("FUSION_FAIL step=connect_complete addr=%s hci=0x%02x\n",
		       addr, conn_err);
		if (target_conn != NULL) {
			bt_conn_unref(target_conn);
			target_conn = NULL;
		}
		connecting = false;
		start_scan();
		return;
	}

	if (conn != target_conn) {
		return;
	}

	connecting = false;
	++reconnections;
	printk("FUSION_CONNECTED addr=%s connection=%u\n",
	       addr, reconnections);

	err = bt_conn_le_phy_update(conn, &phy_params);
	printk("FUSION_PHY_REQUEST preferred=2M err=%d\n", err);

	err = bt_conn_le_data_len_update(conn, BT_LE_DATA_LEN_PARAM_MAX);
	printk("FUSION_DLE_REQUEST max=251 err=%d\n", err);

	exchange_params.func = mtu_exchanged;
	err = bt_gatt_exchange_mtu(conn, &exchange_params);
	if (err != 0) {
		printk("FUSION_ATT_MTU_REQUEST err=%d\n", err);
		start_fusion_discovery(conn);
	}
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];

	if (conn != target_conn) {
		return;
	}

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("FUSION_DISCONNECTED addr=%s reason=0x%02x packets=%u malformed=%u\n",
	       addr, reason, received_packets, malformed_packets);
	bt_conn_unref(target_conn);
	target_conn = NULL;
	connecting = false;
	bridge_ready = false;
	control_value_handle = 0u;
	memset(&data_subscribe_params, 0, sizeof(data_subscribe_params));
	memset(&telemetry_subscribe_params, 0,
	       sizeof(telemetry_subscribe_params));
	start_scan();
}

static void le_phy_updated(struct bt_conn *conn,
			   struct bt_conn_le_phy_info *param)
{
	ARG_UNUSED(conn);
	printk("FUSION_PHY_UPDATED tx=%u rx=%u\n",
	       param->tx_phy, param->rx_phy);
}

static void le_data_len_updated(struct bt_conn *conn,
				struct bt_conn_le_data_len_info *info)
{
	ARG_UNUSED(conn);
	printk("FUSION_DLE_UPDATED tx_len=%u tx_time=%u rx_len=%u rx_time=%u\n",
	       info->tx_max_len, info->tx_max_time,
	       info->rx_max_len, info->rx_max_time);
}

static void le_param_updated(struct bt_conn *conn, uint16_t interval,
			     uint16_t latency, uint16_t timeout)
{
	ARG_UNUSED(conn);
	printk("FUSION_CI_UPDATED interval_units=%u interval_us=%u latency=%u timeout_units=%u\n",
	       interval, (uint32_t)interval * 1250u, latency, timeout);
}

BT_CONN_CB_DEFINE(connection_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
	.le_phy_updated = le_phy_updated,
	.le_data_len_updated = le_data_len_updated,
	.le_param_updated = le_param_updated,
};

static void handle_cdc_command(char *line)
{
	size_t length = strlen(line);
	int err;

	if (strcmp(line, "LIST") == 0) {
		if (target_conn == NULL) {
			printk("FUSION_LIST count=0 scanning=%u\n", !connecting);
		} else {
			printk("FUSION_LIST count=1 name=%s rssi=%d connected=1 subscribed=%u control=%u\n",
			       candidate_name, candidate_rssi, bridge_ready,
			       control_value_handle);
		}
		return;
	}
	if (length < 9u ||
	    strncmp(line, TARGET_NAME_PREFIX,
		    strlen(TARGET_NAME_PREFIX)) != 0) {
		printk("FUSION_COMMAND_REJECT reason=syntax expected=LIST_or_BSFxxxx\n");
		return;
	}
	if (!bridge_ready || target_conn == NULL || control_value_handle == 0u) {
		printk("FUSION_COMMAND_REJECT reason=bridge_not_ready line=%s\n",
		       line);
		return;
	}
	if (strncmp(line, candidate_name, TARGET_NAME_LEN) != 0 ||
	    line[TARGET_NAME_LEN] != ' ') {
		printk("FUSION_COMMAND_REJECT reason=not_connected target=%.7s connected=%s\n",
		       line, candidate_name);
		return;
	}

	err = bt_gatt_write_without_response(target_conn,
					     control_value_handle,
					     line, (uint16_t)length,
					     false);
	printk("FUSION_COMMAND_TX target=%s len=%u err=%d line=%s\n",
	       candidate_name, (unsigned int)length, err, line);
}

static void cdc_command_thread(void *first, void *second, void *third)
{
	char line[CDC_COMMAND_MAX + 1u];
	size_t used = 0u;

	ARG_UNUSED(first);
	ARG_UNUSED(second);
	ARG_UNUSED(third);

	while (true) {
		uint8_t chunk[64];
		uint32_t count;
		unsigned int key;

		k_sem_take(&cdc_rx_sem, K_FOREVER);
		do {
			key = irq_lock();
			count = ring_buf_get(&cdc_rx_ring, chunk, sizeof(chunk));
			irq_unlock(key);
			for (uint32_t i = 0; i < count; ++i) {
				char ch = (char)chunk[i];

				if (ch == '\r' || ch == '\n') {
					if (used != 0u) {
						line[used] = '\0';
						handle_cdc_command(line);
						used = 0u;
					}
				} else if (used < CDC_COMMAND_MAX) {
					line[used++] = ch;
				} else {
					used = 0u;
					printk("FUSION_COMMAND_REJECT reason=line_too_long max=%u\n",
					       CDC_COMMAND_MAX);
				}
			}
		} while (count != 0u);
	}
}

K_THREAD_DEFINE(cdc_command_thread_id, 2048, cdc_command_thread,
		NULL, NULL, NULL, 7, 0, 0);

int main(void)
{
	int err;

	err = cdc_start();
	if (err != 0) {
		printk("FUSION_FAIL step=cdc_start err=%d\n", err);
		return 0;
	}
	printk("FUSION_MASTER marker=dk-fusion-imu-relay-v5 probe=683234364 pc=USB_CDC rtt=fallback\n");
	err = bt_enable(NULL);
	if (err != 0) {
		printk("FUSION_FAIL step=bt_enable err=%d\n", err);
		return 0;
	}

	printk("FUSION_MASTER_BLUETOOTH_READY\n");
	start_scan();

	while (true) {
		k_sleep(K_SECONDS(10));
		if (!target_conn && !connecting) {
			printk("FUSION_SCAN_WAITING\n");
		} else if (bridge_ready) {
			printk("FUSION_HEALTH packets=%u malformed=%u logger_drop=%u cdc_drop_bytes=%u connections=%u\n",
			       received_packets, malformed_packets, logger_dropped,
			       cdc_dropped_bytes, reconnections);
		}
	}

	return 0;
}
