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
#include <bluetooth/hci_vs_sdc.h>
#include <zephyr/device.h>
#include <zephyr/debug/thread_analyzer.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/net_buf.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/sys/iterable_sections.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>
#include <zephyr/usb/usb_device.h>

#include <SEGGER_RTT.h>

#include "hci_core.h"
#include "biospur_fusion_ble.h"
#include "host_binary_protocol.h"
#include "led_panel.h"
#include "timer_epoch.h"

#define TARGET_NAME_PREFIX "BSF"
#define TARGET_NAME_LEN 7
#define MAX_FUSION_PEERS 10
#define CDC_TEXT_LINE_MAX 512
#define CDC_COMMAND_MAX BSF_CONTROL_LINE_MAX
#define FUSION_LOG_QUEUE_DEPTH 128u
#define CDC_TX_RING_CAPACITY 16384u
#define BSF_BLE_PROTOCOL_PREVIOUS 6u
#define BSF_BLE_TELEMETRY_V4_SIZE 235u
#define SPACING_OFF_US 7500u
#define SPACING_ON_US 5000u
#define QOS_WINDOW_MS 1000u
#define LED_RENDER_PERIOD_MS 500u
#define LED_HEARTBEAT_TIMEOUT_MS 1500u
#define LED_UWB_TOGGLE_RECORDS 50u
#define LED_IMU_TOGGLE_RECORDS 200u
#define LED_EXPECTED_DEFAULT 5u
#define LED_EXPECTED_MIN 1u
#define LED_EXPECTED_MAX 10u
/*
 * Zephyr enforces the Core-spec ATT transaction timeout, BT_ATT_TIMEOUT =
 * K_SECONDS(30) (att_internal.h), and on expiry att_timeout() calls
 * bt_att_disconnected() -- after which the spec forbids any further request,
 * command, indication or notification on that bearer until reconnection.
 *
 * The application timeout is therefore set below 30 s so our own bookkeeping
 * always resolves first and reports a clean terminal reason instead of being
 * overtaken by the stack.
 *
 * It does NOT prevent the bearer teardown. bt_gatt_cancel() -> bt_att_req_cancel()
 * -> bt_att_chan_req_cancel() only sets chan->req = &cancel and frees the
 * request; it never touches chan->timeout_work, which is cancelled solely in
 * att_handle_rsp() when a real response arrives. A stalled peer never responds,
 * so the 30 s timer still fires and still kills the bearer. The only action
 * that pre-empts it is disconnecting the peer first -- see RECONNECT below.
 */
#define STALL_READ_TIMEOUT_MS 25000u
#define ATT_TRANSACTION_TIMEOUT_MS 30000u

/* Bounded wait for a forced-reconnect probe to complete, disconnect to bridge. */
#define RECONNECT_PROBE_TIMEOUT_MS 120000u

#if defined(CONFIG_BSF_CCC_FILTERED_REPRO)
#define FUSION_MASTER_MARKER "dk-fusion-ccc-repro-v1"
#else
#define FUSION_MASTER_MARKER "dk-fusion-imu-relay-v35"
#endif

#define CDC_ACM_NODE DT_NODELABEL(cdc_acm_uart0)
#define LED1_NODE DT_ALIAS(led0)
#define LED2_NODE DT_ALIAS(led1)
#define LED3_NODE DT_ALIAS(led2)
#define LED4_NODE DT_ALIAS(led3)

static const struct device *const cdc_acm = DEVICE_DT_GET(CDC_ACM_NODE);
static const struct gpio_dt_spec fleet_led =
	GPIO_DT_SPEC_GET(LED1_NODE, gpios);
static const struct gpio_dt_spec uwb_led =
	GPIO_DT_SPEC_GET(LED2_NODE, gpios);
static const struct gpio_dt_spec imu_led =
	GPIO_DT_SPEC_GET(LED3_NODE, gpios);
static const struct gpio_dt_spec fault_led =
	GPIO_DT_SPEC_GET(LED4_NODE, gpios);
RING_BUF_DECLARE(cdc_tx_ring, 16384);
RING_BUF_DECLARE(cdc_rx_ring, 1024);
K_SEM_DEFINE(cdc_rx_sem, 0, 1);
K_MUTEX_DEFINE(command_dispatch_lock);
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
static struct bt_uuid_128 fusion_stall_uuid =
	BT_UUID_INIT_128(BT_UUID_128_ENCODE(
		BSF_BLE_UUID_STALL_W32, BSF_BLE_UUID_W16_1,
		BSF_BLE_UUID_W16_2, BSF_BLE_UUID_W16_3, BSF_BLE_UUID_W48));

static bt_addr_le_t candidate_addr;
static bool candidate_valid;
static bool candidate_has_service;
static bool candidate_has_name;
static bool connecting;
static int8_t candidate_rssi;
static char candidate_name[TARGET_NAME_LEN + 1];
static uint32_t received_packets;
static uint32_t malformed_packets;
static uint32_t reconnections;
static uint32_t logger_dropped;
static uint32_t cdc_dropped_bytes;
static uint32_t cdc_dropped_records;
static atomic_t fusion_log_queue_high_water;
static atomic_t cdc_tx_ring_high_water;
static atomic_t host_frame_sequence;
static atomic_t host_binary_output = ATOMIC_INIT(1);
static atomic_t qos_unknown_handle_events;
static atomic_t led_uwb_records;
static atomic_t led_imu_records;
static atomic_t led_last_uwb_ms;
static atomic_t led_last_imu_ms;
static atomic_t led_uwb_seen;
static atomic_t led_imu_seen;
static atomic_t led_expected_count = ATOMIC_INIT(LED_EXPECTED_DEFAULT);
static struct fusion_led_fault_latch led_fault_latch;
static struct k_spinlock led_fault_lock;

enum spacing_mode {
	SPACING_MODE_OFF = 0,
	SPACING_MODE_ON = 1,
};

struct qos_aggregate {
	uint32_t window_start_ms;
	uint32_t report_count;
	uint32_t event_counter_gap_count;
	uint32_t crc_ok_count;
	uint32_t crc_error_count;
	uint32_t nak_count;
	uint32_t rx_timeout_count;
	uint16_t first_event_counter;
	uint16_t last_event_counter;
	uint16_t channel_event_count[37];
};

static struct k_spinlock qos_lock;
static enum spacing_mode spacing_active_mode = SPACING_MODE_OFF;
static enum spacing_mode spacing_target_mode = SPACING_MODE_OFF;
static uint32_t spacing_active_us = SPACING_OFF_US;
static uint32_t spacing_generation;
static bool spacing_transition;
static bool spacing_failed;

enum discovery_stage {
	DISCOVERY_SERVICE,
	DISCOVERY_DATA_CHARACTERISTIC,
	DISCOVERY_DATA_CCC,
	DISCOVERY_TELEMETRY_CHARACTERISTIC,
	DISCOVERY_TELEMETRY_CCC,
	DISCOVERY_STALL_CHARACTERISTIC,
	DISCOVERY_CONTROL_CHARACTERISTIC,
};

struct fusion_peer {
	struct bt_conn *conn;
	bt_addr_le_t addr;
	char name[TARGET_NAME_LEN + 1];
	int8_t rssi;
	bool allocated;
	bool bridge_ready;
	enum discovery_stage discovery_stage;
	struct bt_gatt_exchange_params exchange_params;
	struct bt_gatt_discover_params discover_params;
	struct bt_gatt_subscribe_params data_subscribe_params;
	struct bt_gatt_subscribe_params telemetry_subscribe_params;
	struct bt_gatt_read_params stall_read_params;
	struct k_work_delayable stall_read_timeout_work;
	uint32_t stall_read_generation;
	uint32_t stall_read_started_ms;
	bool stall_read_active;
	uint16_t service_end_handle;
	uint16_t data_value_handle;
	uint16_t telemetry_value_handle;
	uint16_t stall_value_handle;
	uint16_t control_value_handle;
	uint16_t interval;
	uint16_t latency;
	uint16_t timeout;
	uint8_t tx_phy;
	uint8_t rx_phy;
	bool phy_readback_valid;
	uint16_t dle_tx_len;
	uint16_t dle_tx_time;
	uint16_t dle_rx_len;
	uint16_t dle_rx_time;
	bool dle_readback_valid;
	uint16_t hci_handle;
	bool hci_handle_valid;
	struct qos_aggregate qos;
	uint16_t qos_last_event_counter;
	bool qos_have_event_counter;
	uint32_t imu_epoch_defer_drop;
	uint32_t delivered_imu;
	uint32_t delivered_uwb;
	uint32_t delivered_ctl;
	uint64_t imu_last_extended_base_us;
	uint64_t node_time_reference_us;
	uint32_t node_timer_wrap_count;
	bool imu_have_extended_base;
	bool node_have_time_reference;
	bool node_have_timer_wrap_count;
	bool imu_wait_epoch_reported;
	uint32_t received_packets;
	uint32_t malformed_packets;
	uint32_t logger_dropped;
	uint32_t reconnections;
	bool led_have_uwb_sequence;
	uint32_t led_last_uwb_sequence;
	uint32_t led_last_uwb_uptime_ms;
	bool led_have_telemetry;
	uint32_t led_telemetry_uptime_ms;
	uint32_t led_crc_errors;
	uint32_t led_header_errors;
	uint32_t led_ring_dropped_bytes;
	uint32_t led_drop_err;
	uint32_t led_uart_restarts;
	uint32_t led_relay_timeout;
	int32_t led_last_uart_error;
	bool led_have_queue;
	uint32_t led_queue_uptime_ms;
	uint32_t led_q_drop_imu;
	uint32_t led_q_drop_uwb;
	uint32_t led_q_drop_ctl;
	uint32_t led_abort_imu;
	uint32_t led_abort_uwb;
	uint32_t led_abort_ctl;
};

static struct fusion_peer peers[MAX_FUSION_PEERS];
static struct fusion_peer *connecting_peer;

static struct fusion_peer *peer_by_conn(const struct bt_conn *conn)
{
	for (size_t i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].allocated && peers[i].conn == conn) {
			return &peers[i];
		}
	}
	return NULL;
}

static struct fusion_peer *peer_by_name(const char *name)
{
	for (size_t i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].allocated &&
		    strncmp(peers[i].name, name, TARGET_NAME_LEN) == 0) {
			return &peers[i];
		}
	}
	return NULL;
}

static struct fusion_peer *peer_by_addr(const bt_addr_le_t *addr)
{
	for (size_t i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].allocated &&
		    bt_addr_le_cmp(&peers[i].addr, addr) == 0) {
			return &peers[i];
		}
	}
	return NULL;
}

static struct fusion_peer *peer_by_hci_handle(uint16_t conn_handle)
{
	for (size_t i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].allocated && peers[i].hci_handle_valid &&
		    peers[i].hci_handle == conn_handle) {
			return &peers[i];
		}
	}
	return NULL;
}

static size_t peer_count_allocated(void)
{
	size_t count = 0u;

	for (size_t i = 0; i < ARRAY_SIZE(peers); ++i) {
		count += peers[i].allocated ? 1u : 0u;
	}
	return count;
}

static size_t peer_count_ready(void)
{
	size_t count = 0u;

	for (size_t i = 0; i < ARRAY_SIZE(peers); ++i) {
		count += peers[i].allocated && peers[i].bridge_ready ? 1u : 0u;
	}
	return count;
}

static const char *peer_link_contract(const struct fusion_peer *peer)
{
	if (!peer->phy_readback_valid || !peer->dle_readback_valid) {
		return "PENDING";
	}
	return peer->tx_phy == BT_GAP_LE_PHY_2M &&
		peer->rx_phy == BT_GAP_LE_PHY_2M && peer->dle_tx_len == 251u &&
		peer->dle_rx_len == 251u ? "PASS" : "FAIL";
}

static void led_note_fault(enum fusion_led_fault_class fault,
			   uint32_t amount)
{
	k_spinlock_key_t key = k_spin_lock(&led_fault_lock);

	fusion_led_fault_note(&led_fault_latch, fault, amount);
	k_spin_unlock(&led_fault_lock, key);
}

static struct fusion_led_fault_latch led_fault_snapshot(void)
{
	struct fusion_led_fault_latch snapshot;
	k_spinlock_key_t key = k_spin_lock(&led_fault_lock);

	snapshot = led_fault_latch;
	k_spin_unlock(&led_fault_lock, key);
	return snapshot;
}

static void led_clear_faults(void)
{
	k_spinlock_key_t key = k_spin_lock(&led_fault_lock);

	fusion_led_fault_clear(&led_fault_latch);
	k_spin_unlock(&led_fault_lock, key);
}

static void led_note_uwb_record(void)
{
	atomic_inc(&led_uwb_records);
	atomic_set(&led_last_uwb_ms,
		   (atomic_val_t)(uint32_t)k_uptime_get());
	atomic_set(&led_uwb_seen, 1);
}

static void led_note_imu_record(void)
{
	atomic_inc(&led_imu_records);
	atomic_set(&led_last_imu_ms,
		   (atomic_val_t)(uint32_t)k_uptime_get());
	atomic_set(&led_imu_seen, 1);
}

static void led_check_uwb_sequence(struct fusion_peer *peer,
				   const bsf_ble_uwb_packet_t *packet)
{
	uint32_t delta;

	if (!peer->led_have_uwb_sequence ||
	    packet->node_uptime_ms < peer->led_last_uwb_uptime_ms) {
		peer->led_have_uwb_sequence = true;
		peer->led_last_uwb_sequence = packet->node_sequence;
		peer->led_last_uwb_uptime_ms = packet->node_uptime_ms;
		return;
	}

	delta = packet->node_sequence - peer->led_last_uwb_sequence;
	if (delta != 1u) {
		/*
		 * A node reboot is recognized by uptime moving backwards and
		 * rebases the expected sequence above.  Everything else is an
		 * unexpected duplicate, reorder, or delivery gap.
		 */
		led_note_fault(FUSION_LED_FAULT_SEQUENCE, 1u);
	}
	peer->led_last_uwb_sequence = packet->node_sequence;
	peer->led_last_uwb_uptime_ms = packet->node_uptime_ms;
}

static uint32_t led_counter_delta(uint32_t current, uint32_t previous)
{
	return current - previous;
}

static void led_check_telemetry(struct fusion_peer *peer,
				const bsf_ble_telemetry_t *telemetry)
{
	if (!peer->led_have_telemetry ||
	    telemetry->node_uptime_ms < peer->led_telemetry_uptime_ms) {
		peer->led_have_telemetry = true;
		peer->led_telemetry_uptime_ms = telemetry->node_uptime_ms;
		peer->led_crc_errors = telemetry->crc_errors;
		peer->led_header_errors = telemetry->header_errors;
		peer->led_ring_dropped_bytes = telemetry->ring_dropped_bytes;
		peer->led_drop_err = telemetry->drop_err;
		peer->led_uart_restarts = telemetry->uart_restarts;
		peer->led_relay_timeout = telemetry->relay_timeout;
		peer->led_last_uart_error = telemetry->last_uart_error;
		return;
	}

	led_note_fault(
		FUSION_LED_FAULT_CRC_HEADER,
		led_counter_delta(telemetry->crc_errors,
				  peer->led_crc_errors));
	led_note_fault(
		FUSION_LED_FAULT_CRC_HEADER,
		led_counter_delta(telemetry->header_errors,
				  peer->led_header_errors));
	led_note_fault(
		FUSION_LED_FAULT_QUEUE,
		led_counter_delta(telemetry->ring_dropped_bytes,
				  peer->led_ring_dropped_bytes));
	led_note_fault(
		FUSION_LED_FAULT_NOTIFY_UART,
		led_counter_delta(telemetry->drop_err,
				  peer->led_drop_err));
	led_note_fault(
		FUSION_LED_FAULT_NOTIFY_UART,
		led_counter_delta(telemetry->uart_restarts,
				  peer->led_uart_restarts));
	led_note_fault(
		FUSION_LED_FAULT_NOTIFY_UART,
		led_counter_delta(telemetry->relay_timeout,
				  peer->led_relay_timeout));
	if (telemetry->last_uart_error != 0 &&
	    telemetry->last_uart_error != peer->led_last_uart_error) {
		led_note_fault(FUSION_LED_FAULT_NOTIFY_UART, 1u);
	}

	peer->led_telemetry_uptime_ms = telemetry->node_uptime_ms;
	peer->led_crc_errors = telemetry->crc_errors;
	peer->led_header_errors = telemetry->header_errors;
	peer->led_ring_dropped_bytes = telemetry->ring_dropped_bytes;
	peer->led_drop_err = telemetry->drop_err;
	peer->led_uart_restarts = telemetry->uart_restarts;
	peer->led_relay_timeout = telemetry->relay_timeout;
	peer->led_last_uart_error = telemetry->last_uart_error;
}

static void led_check_queue(struct fusion_peer *peer,
			    const bsf_ble_queue_counters_t *queue)
{
	if (!peer->led_have_queue ||
	    queue->node_uptime_ms < peer->led_queue_uptime_ms) {
		peer->led_have_queue = true;
		peer->led_queue_uptime_ms = queue->node_uptime_ms;
		peer->led_q_drop_imu = queue->q_drop_imu;
		peer->led_q_drop_uwb = queue->q_drop_uwb;
		peer->led_q_drop_ctl = queue->q_drop_ctl;
		peer->led_abort_imu = queue->abort_imu;
		peer->led_abort_uwb = queue->abort_uwb;
		peer->led_abort_ctl = queue->abort_ctl;
		return;
	}

	led_note_fault(FUSION_LED_FAULT_QUEUE,
		       led_counter_delta(queue->q_drop_imu,
					 peer->led_q_drop_imu));
	led_note_fault(FUSION_LED_FAULT_QUEUE,
		       led_counter_delta(queue->q_drop_uwb,
					 peer->led_q_drop_uwb));
	led_note_fault(FUSION_LED_FAULT_QUEUE,
		       led_counter_delta(queue->q_drop_ctl,
					 peer->led_q_drop_ctl));
	led_note_fault(FUSION_LED_FAULT_QUEUE,
		       led_counter_delta(queue->abort_imu,
					 peer->led_abort_imu));
	led_note_fault(FUSION_LED_FAULT_QUEUE,
		       led_counter_delta(queue->abort_uwb,
					 peer->led_abort_uwb));
	led_note_fault(FUSION_LED_FAULT_QUEUE,
		       led_counter_delta(queue->abort_ctl,
					 peer->led_abort_ctl));

	peer->led_queue_uptime_ms = queue->node_uptime_ms;
	peer->led_q_drop_imu = queue->q_drop_imu;
	peer->led_q_drop_uwb = queue->q_drop_uwb;
	peer->led_q_drop_ctl = queue->q_drop_ctl;
	peer->led_abort_imu = queue->abort_imu;
	peer->led_abort_uwb = queue->abort_uwb;
	peer->led_abort_ctl = queue->abort_ctl;
}

static int led_gpio_init(void)
{
	const struct gpio_dt_spec *leds[] = {
		&fleet_led, &uwb_led, &imu_led, &fault_led,
	};

	for (size_t i = 0u; i < ARRAY_SIZE(leds); ++i) {
		int err;

		if (!gpio_is_ready_dt(leds[i])) {
			return -ENODEV;
		}
		err = gpio_pin_configure_dt(leds[i], GPIO_OUTPUT_INACTIVE);
		if (err != 0) {
			return err;
		}
	}
	return 0;
}

static void led_work_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(led_work, led_work_handler);

static void led_work_handler(struct k_work *work)
{
	uint32_t now_ms = (uint32_t)k_uptime_get();
	uint32_t uwb_count = (uint32_t)atomic_get(&led_uwb_records);
	uint32_t imu_count = (uint32_t)atomic_get(&led_imu_records);
	uint32_t last_uwb_ms = (uint32_t)atomic_get(&led_last_uwb_ms);
	uint32_t last_imu_ms = (uint32_t)atomic_get(&led_last_imu_ms);
	uint32_t expected =
		(uint32_t)atomic_get(&led_expected_count);
	size_t ready = peer_count_ready();
	struct fusion_led_fault_latch faults = led_fault_snapshot();
	bool uwb_recent = atomic_get(&led_uwb_seen) != 0 &&
		(uint32_t)(now_ms - last_uwb_ms) <=
			LED_HEARTBEAT_TIMEOUT_MS;
	bool imu_recent = atomic_get(&led_imu_seen) != 0 &&
		(uint32_t)(now_ms - last_imu_ms) <=
			LED_HEARTBEAT_TIMEOUT_MS;
	bool fleet_on = ready == 0u ? false :
		ready == expected ? true :
		((now_ms / LED_RENDER_PERIOD_MS) & 1u) != 0u;
	bool uwb_on = uwb_recent &&
		((uwb_count / LED_UWB_TOGGLE_RECORDS) & 1u) != 0u;
	bool imu_on = imu_recent &&
		((imu_count / LED_IMU_TOGGLE_RECORDS) & 1u) != 0u;

	(void)gpio_pin_set_dt(&fleet_led, fleet_on);
	(void)gpio_pin_set_dt(&uwb_led, uwb_on);
	(void)gpio_pin_set_dt(&imu_led, imu_on);
	(void)gpio_pin_set_dt(&fault_led, faults.mask != 0u);
	k_work_reschedule(&led_work, K_MSEC(LED_RENDER_PERIOD_MS));
}

static void reset_node_time_extension(struct fusion_peer *peer)
{
	peer->imu_last_extended_base_us = 0u;
	peer->node_time_reference_us = 0u;
	peer->node_timer_wrap_count = 0u;
	peer->imu_have_extended_base = false;
	peer->node_have_time_reference = false;
	peer->node_have_timer_wrap_count = false;
	peer->imu_wait_epoch_reported = false;
}

static void stall_read_abort(struct fusion_peer *peer, const char *reason);
static void stall_read_timeout_handler(struct k_work *work);
static void reconnect_probe_note_disconnect(const char *name);
static void reconnect_probe_note_connected(const char *name);
static void reconnect_probe_note_bridge_ready(struct fusion_peer *peer);
static void reconnect_probe_timeout_handler(struct k_work *work);

static void release_peer(struct fusion_peer *peer)
{
	stall_read_abort(peer, "disconnect");
	k_spinlock_key_t key = k_spin_lock(&qos_lock);

	memset(&peer->qos, 0, sizeof(peer->qos));
	peer->qos_have_event_counter = false;
	peer->hci_handle_valid = false;
	k_spin_unlock(&qos_lock, key);
	if (peer->conn != NULL) {
		bt_conn_unref(peer->conn);
	}
	memset(peer, 0, sizeof(*peer));
}

static struct fusion_peer *allocate_peer(const bt_addr_le_t *addr,
					 const char *name, int8_t rssi)
{
	for (size_t i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (!peers[i].allocated) {
			struct fusion_peer *peer = &peers[i];

			memset(peer, 0, sizeof(*peer));
			peer->allocated = true;
			bt_addr_le_copy(&peer->addr, addr);
			memcpy(peer->name, name, TARGET_NAME_LEN);
			peer->name[TARGET_NAME_LEN] = '\0';
			peer->rssi = rssi;
			k_work_init_delayable(&peer->stall_read_timeout_work,
					      stall_read_timeout_handler);
			return peer;
		}
	}
	return NULL;
}

static bool extend_imu_base(struct fusion_peer *peer, uint32_t low,
			    uint64_t *extended_out)
{
	uint64_t reference;
	uint64_t extended;

	if (peer->imu_have_extended_base) {
		reference = peer->imu_last_extended_base_us;
	} else if (peer->node_have_time_reference) {
		reference = peer->node_time_reference_us;
	} else if (peer->node_have_timer_wrap_count) {
		reference = (uint64_t)peer->node_timer_wrap_count << 32;
	} else {
		return false;
	}
	extended = bsf_extend_low32_near(low, reference);
	peer->imu_last_extended_base_us = extended;
	peer->imu_have_extended_base = true;
	*extended_out = extended;
	return true;
}

static void note_full_node_time(struct fusion_peer *peer,
				uint64_t timestamp_us)
{
	peer->node_time_reference_us = timestamp_us;
	peer->node_have_time_reference = true;
}

static void mark_malformed(struct fusion_peer *peer)
{
	++malformed_packets;
	if (peer != NULL) {
		++peer->malformed_packets;
	}
	led_note_fault(FUSION_LED_FAULT_CRC_HEADER, 1u);
}

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
	FUSION_LOG_QUEUE_COUNTERS = 5,
	FUSION_LOG_POOL_USAGE = 6,
};

struct fusion_imu_log {
	uint64_t base_timer2_ts_us;
	bsf_ble_imu_sample_t samples[BSF_IMU_BATCH_MAX];
	int16_t temperature;
	uint16_t sequence;
	uint8_t sample_count;
	uint8_t version;
};

/*
 * Transition-only reader for b306-imu-relay-v25. That deployed image widened
 * kind-3 in violation of the frozen record layout. v26 restores the protected
 * 10-byte prefix; accepting v25 here keeps the bridge alive during OTA.
 */
struct __attribute__((packed)) bsf_ble_imu_prefix_v25 {
	uint8_t version;
	uint8_t kind;
	uint16_t len;
	uint16_t seq;
	uint64_t base_timer2_ts_us;
};

#define BSF_IMU_V25_RECORD_LEN(n) \
	(sizeof(struct bsf_ble_imu_prefix_v25) + \
	 (size_t)(n) * sizeof(bsf_ble_imu_sample_t) + sizeof(int16_t))

struct fusion_reply_log {
	bsf_ble_control_reply_prefix_t prefix;
	char text[BSF_CONTROL_REPLY_TEXT_MAX + 1u];
};

struct fusion_log_record {
	uint64_t master_arrival_ms;
	char node_name[TARGET_NAME_LEN + 1];
	uint32_t peer_received_packets;
	uint32_t peer_malformed_packets;
	uint32_t peer_logger_dropped;
	uint32_t peer_imu_epoch_defer_drop;
	uint32_t peer_delivered_imu;
	uint32_t peer_delivered_uwb;
	uint32_t peer_delivered_ctl;
	uint8_t kind;
	union {
		bsf_ble_uwb_packet_t uwb;
		bsf_ble_telemetry_t telemetry;
		struct fusion_imu_log imu;
		struct fusion_reply_log reply;
		bsf_ble_queue_counters_t queue_counters;
		bsf_ble_pool_usage_t pool_usage;
	} payload;
};

K_MSGQ_DEFINE(fusion_log_queue, sizeof(struct fusion_log_record),
	      FUSION_LOG_QUEUE_DEPTH, 4);

static void start_scan(void);
static void start_fusion_discovery(struct fusion_peer *peer);
static void fusion_printf(const char *format, ...);

static uint8_t hex_nibble(char value)
{
	if (value >= '0' && value <= '9') {
		return (uint8_t)(value - '0');
	}
	if (value >= 'A' && value <= 'F') {
		return (uint8_t)(value - 'A' + 10);
	}
	if (value >= 'a' && value <= 'f') {
		return (uint8_t)(value - 'a' + 10);
	}
	return 0u;
}

static uint16_t host_node_id(const char *node_name)
{
	uint16_t identity = 0u;

	if (node_name == NULL ||
	    strncmp(node_name, TARGET_NAME_PREFIX,
		    strlen(TARGET_NAME_PREFIX)) != 0) {
		return 0u;
	}
	for (size_t i = 3u; i < TARGET_NAME_LEN; ++i) {
		identity = (uint16_t)((identity << 4) |
				     hex_nibble(node_name[i]));
	}
	return identity;
}

static void update_high_water(atomic_t *high_water, atomic_val_t sample)
{
	atomic_val_t previous = atomic_get(high_water);

	while (sample > previous &&
	       !atomic_cas(high_water, previous, sample)) {
		previous = atomic_get(high_water);
	}
}

static void log_thread_stack(struct thread_analyzer_info *info)
{
	fusion_printf(
		"FUSION_STACK name=%s used=%u available=%u total=%u percent=%u\n",
		info->name,
		(unsigned int)info->stack_used,
		(unsigned int)(info->stack_size - info->stack_used),
		(unsigned int)info->stack_size,
		info->stack_size == 0u ? 0u :
			(unsigned int)(100u * info->stack_used /
				       info->stack_size));
}

static void log_net_buf_pools(const char *source)
{
	STRUCT_SECTION_FOREACH(net_buf_pool, pool) {
		uint32_t available = (uint32_t)atomic_get(
			&pool->avail_count);
		const char *scope = "other";

		if (strcmp(pool->name, "hci_rx_pool") == 0) {
			scope = "acl_rx+events";
		} else if (strcmp(pool->name, "att_pool") == 0) {
			scope = "att_tx";
		} else if (strcmp(pool->name, "acl_tx_pool") == 0) {
			scope = "l2cap_tx";
		}
		fusion_printf(
			"FUSION_RESOURCE_POOL source=%s pool=%s scope=%s used=%u available=%u total=%u bytes=%u\n",
			source, pool->name, scope,
			pool->buf_count - available,
			available,
			pool->buf_count,
			pool->pool_size);
	}
}

static void log_resource_snapshot(const char *source)
{
	uint32_t queue_used = k_msgq_num_used_get(&fusion_log_queue);
	uint32_t cdc_used;
	uint32_t acl_tx_available =
		(uint32_t)k_sem_count_get(&bt_dev.le.acl_pkts);
	uint32_t acl_tx_total = (uint32_t)bt_dev.le.acl_pkts.limit;
	unsigned int key = irq_lock();

	cdc_used = ring_buf_size_get(&cdc_tx_ring);
	irq_unlock(key);

	fusion_printf(
		"FUSION_RESOURCE_SUMMARY source=%s connections=%u ready=%u logq_used=%u logq_available=%u logq_total=%u logq_high_water=%u cdc_used=%u cdc_available=%u cdc_total=%u cdc_high_water=%u cdc_drop_bytes=%u cdc_drop_records=%u acl_tx_used=%u acl_tx_available=%u acl_tx_total=%u acl_tx_configured=%u\n",
		source,
		(unsigned int)peer_count_allocated(),
		(unsigned int)peer_count_ready(),
		queue_used,
		FUSION_LOG_QUEUE_DEPTH - queue_used,
		FUSION_LOG_QUEUE_DEPTH,
		(unsigned int)atomic_get(&fusion_log_queue_high_water),
		cdc_used,
		CDC_TX_RING_CAPACITY - cdc_used,
		CDC_TX_RING_CAPACITY,
		(unsigned int)atomic_get(&cdc_tx_ring_high_water),
		cdc_dropped_bytes,
		cdc_dropped_records,
		acl_tx_total - acl_tx_available,
		acl_tx_available,
		acl_tx_total,
		CONFIG_BT_BUF_ACL_TX_COUNT);

	log_net_buf_pools(source);
	thread_analyzer_run(log_thread_stack, 0);
}

static void note_fusion_log_queue_high_water(void)
{
	update_high_water(&fusion_log_queue_high_water,
			  (atomic_val_t)k_msgq_num_used_get(
				  &fusion_log_queue));
}

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

static void cdc_queue_record(const uint8_t *data, size_t length)
{
	unsigned int key;
	uint32_t available;

	if (!cdc_ready || data == NULL || length == 0u) {
		return;
	}
	key = irq_lock();
	available = ring_buf_space_get(&cdc_tx_ring);
	if (available >= length) {
		uint32_t accepted = ring_buf_put(&cdc_tx_ring, data,
						 (uint32_t)length);

		__ASSERT_NO_MSG(accepted == length);
		ARG_UNUSED(accepted);
	} else {
		cdc_dropped_bytes += (uint32_t)length;
		++cdc_dropped_records;
		led_note_fault(FUSION_LED_FAULT_QUEUE, 1u);
	}
	update_high_water(&cdc_tx_ring_high_water,
			  (atomic_val_t)ring_buf_size_get(&cdc_tx_ring));
	uart_irq_tx_enable(cdc_acm);
	irq_unlock(key);
}

static void host_binary_emit(uint8_t kind, const char *node_name,
			     uint64_t master_arrival_ms,
			     const void *payload, size_t payload_length)
{
	uint8_t raw[BSF_HOST_FRAME_MAX_RAW];
	uint8_t encoded[BSF_HOST_FRAME_MAX_ENCODED];
	bsf_host_frame_header_t header = {
		.magic = BSF_HOST_FRAME_MAGIC,
		.version = BSF_HOST_FRAME_VERSION,
		.kind = kind,
		.node_id = host_node_id(node_name),
		.payload_len = (uint16_t)payload_length,
		.sequence = (uint32_t)atomic_inc(&host_frame_sequence) + 1u,
		.master_arrival_ms = master_arrival_ms,
	};
	size_t raw_length;
	size_t encoded_length;
	uint16_t crc;

	if (payload_length > BSF_HOST_FRAME_MAX_PAYLOAD) {
		++cdc_dropped_records;
		cdc_dropped_bytes += (uint32_t)payload_length;
		led_note_fault(FUSION_LED_FAULT_QUEUE, 1u);
		return;
	}
	memcpy(raw, &header, sizeof(header));
	if (payload_length != 0u) {
		memcpy(&raw[sizeof(header)], payload, payload_length);
	}
	raw_length = sizeof(header) + payload_length;
	crc = bsf_host_crc16(raw, raw_length);
	memcpy(&raw[raw_length], &crc, sizeof(crc));
	raw_length += sizeof(crc);
	encoded_length = bsf_host_cobs_encode(raw, raw_length, encoded,
					      sizeof(encoded));
	if (encoded_length == 0u) {
		++cdc_dropped_records;
		cdc_dropped_bytes += (uint32_t)raw_length;
		led_note_fault(FUSION_LED_FAULT_QUEUE, 1u);
		return;
	}
	cdc_queue_record(encoded, encoded_length);
}

static void fusion_printf(const char *format, ...)
{
	char line[CDC_TEXT_LINE_MAX];
	va_list args;
	int written;
	size_t length;

	va_start(args, format);
	written = vsnprintf(line, sizeof(line), format, args);
	va_end(args);
	if (written < 0) {
		return;
	}
	length = strnlen(line, sizeof(line) - 1u);
	if (length == 0u) {
		return;
	}
	if (line[length - 1u] != '\n' && length + 1u < sizeof(line)) {
		line[length++] = '\n';
		line[length] = '\0';
	}
	printk("%s", line);
	if (atomic_get(&host_binary_output) != 0) {
		host_binary_emit(BSF_HOST_RECORD_TEXT, NULL,
				 (uint64_t)k_uptime_get(), line, length);
	} else {
		cdc_queue_record((const uint8_t *)line, length);
	}
}

/* Every existing instrument line now goes to native USB CDC and RTT. */
#define printk(...) fusion_printf(__VA_ARGS__)

static const char *spacing_mode_name(enum spacing_mode mode)
{
	return mode == SPACING_MODE_ON ? "ON" : "OFF";
}

static int spacing_apply(enum spacing_mode mode)
{
	sdc_hci_cmd_vs_central_acl_event_spacing_set_t params = {
		.central_acl_event_spacing_us =
			mode == SPACING_MODE_ON ? SPACING_ON_US :
						 SPACING_OFF_US,
	};
	int err = hci_vs_sdc_central_acl_event_spacing_set(&params);

	if (err == 0) {
		spacing_active_mode = mode;
		spacing_active_us = params.central_acl_event_spacing_us;
		++spacing_generation;
	}
	return err;
}

static void spacing_status_print(const char *state)
{
	printk("FUSION_SPACING state=%s mode=%s applied_us=%u generation=%u transition=%u failed=%u expected_anchors_us=0,%u,%u,%u,%u,%u,%u,%u,%u,%u qos=enabled\n",
		       state, spacing_mode_name(spacing_active_mode), spacing_active_us,
		       spacing_generation, spacing_transition, spacing_failed,
		       spacing_active_us, 2u * spacing_active_us,
		       3u * spacing_active_us, 4u * spacing_active_us,
		       5u * spacing_active_us, 6u * spacing_active_us,
		       7u * spacing_active_us, 8u * spacing_active_us,
		       9u * spacing_active_us);
}

static bool qos_vendor_event(struct net_buf_simple *buf)
{
	sdc_hci_subevent_vs_qos_conn_event_report_t report;
	struct fusion_peer *peer;
	k_spinlock_key_t key;
	uint16_t conn_handle;
	uint16_t event_counter;
	uint16_t event_delta;
	uint8_t subevent;

	if (buf->len < 1u) {
		return false;
	}
	subevent = net_buf_simple_pull_u8(buf);
	if (subevent != SDC_HCI_SUBEVENT_VS_QOS_CONN_EVENT_REPORT) {
		return false;
	}
	if (buf->len != sizeof(report)) {
		atomic_inc(&qos_unknown_handle_events);
		return true;
	}
	memcpy(&report, net_buf_simple_pull_mem(buf, sizeof(report)),
	       sizeof(report));
	conn_handle = sys_le16_to_cpu(report.conn_handle);
	event_counter = sys_le16_to_cpu(report.event_counter);

	key = k_spin_lock(&qos_lock);
	peer = peer_by_hci_handle(conn_handle);
	if (peer == NULL) {
		k_spin_unlock(&qos_lock, key);
		atomic_inc(&qos_unknown_handle_events);
		return true;
	}
	if (peer->qos.window_start_ms == 0u) {
		peer->qos.window_start_ms = (uint32_t)k_uptime_get();
	}
	if (peer->qos_have_event_counter) {
		event_delta =
			(uint16_t)(event_counter -
				   peer->qos_last_event_counter);
		if (event_delta > 1u) {
			peer->qos.event_counter_gap_count +=
				(uint32_t)event_delta - 1u;
		}
	} else {
		peer->qos_have_event_counter = true;
	}
	if (peer->qos.report_count == 0u) {
		peer->qos.first_event_counter = event_counter;
	}
	peer->qos_last_event_counter = event_counter;
	peer->qos.last_event_counter = event_counter;
	++peer->qos.report_count;
	peer->qos.crc_ok_count += sys_le16_to_cpu(report.crc_ok_count);
	peer->qos.crc_error_count +=
		sys_le16_to_cpu(report.crc_error_count);
	peer->qos.nak_count += sys_le16_to_cpu(report.nak_count);
	peer->qos.rx_timeout_count += report.rx_timeout != 0u ? 1u : 0u;
	if (report.channel_index < ARRAY_SIZE(peer->qos.channel_event_count)) {
		++peer->qos.channel_event_count[report.channel_index];
	}
	k_spin_unlock(&qos_lock, key);
	return true;
}

static void qos_work_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(qos_work, qos_work_handler);

static uint16_t master_pool_low_water[BSF_NET_BUF_POOL_MAX];
static bool master_pool_low_water_ready;

static uint32_t pool_name_hash(const char *name)
{
	uint32_t hash = 2166136261u;

	for (; name != NULL && *name != '\0'; ++name) {
		hash = (hash ^ (uint8_t)*name) * 16777619u;
	}
	return hash;
}

static void report_master_pool_usage(uint32_t now_ms)
{
	uint8_t index = 0u;

	printk("FUSION_MASTER_POOL master_ms=%u", now_ms);
	STRUCT_SECTION_FOREACH(net_buf_pool, pool) {
		uint16_t available;

		if (index >= ARRAY_SIZE(master_pool_low_water)) {
			break;
		}
		available = (uint16_t)atomic_get(&pool->avail_count);
		if (!master_pool_low_water_ready ||
		    available < master_pool_low_water[index]) {
			master_pool_low_water[index] = available;
		}
		printk(" pool%u=%08x:%u/%u", index,
		       pool_name_hash(pool->name), available,
		       master_pool_low_water[index]);
		++index;
	}
	master_pool_low_water_ready = true;
	printk(" count=%u\n", index);
}

static void qos_work_handler(struct k_work *work)
{
	uint32_t now_ms = (uint32_t)k_uptime_get();

	ARG_UNUSED(work);
	for (size_t i = 0u; i < ARRAY_SIZE(peers); ++i) {
		bsf_host_qos_t record = {
			.version = 1u,
			.spacing_mode = (uint8_t)spacing_active_mode,
			.spacing_us = spacing_active_us,
			.spacing_generation = spacing_generation,
		};
		char node_name[TARGET_NAME_LEN + 1];
		k_spinlock_key_t key = k_spin_lock(&qos_lock);
		struct fusion_peer *peer = &peers[i];

		if (!peer->allocated || !peer->hci_handle_valid) {
			k_spin_unlock(&qos_lock, key);
			continue;
		}
		memcpy(node_name, peer->name, sizeof(node_name));
		record.conn_handle = peer->hci_handle;
		record.window_start_ms = peer->qos.window_start_ms != 0u ?
			peer->qos.window_start_ms :
			now_ms - MIN(now_ms, QOS_WINDOW_MS);
		record.window_duration_ms = now_ms - record.window_start_ms;
		record.report_count = peer->qos.report_count;
		record.event_counter_gap_count =
			peer->qos.event_counter_gap_count;
		record.crc_ok_count = peer->qos.crc_ok_count;
		record.crc_error_count = peer->qos.crc_error_count;
		record.nak_count = peer->qos.nak_count;
		record.rx_timeout_count = peer->qos.rx_timeout_count;
		record.imu_epoch_defer_drop = peer->imu_epoch_defer_drop;
		record.delivered_imu = peer->delivered_imu;
		record.delivered_uwb = peer->delivered_uwb;
		record.delivered_ctl = peer->delivered_ctl;
		record.first_event_counter = peer->qos.first_event_counter;
		record.last_event_counter = peer->qos.last_event_counter;
		memcpy(record.channel_event_count,
		       peer->qos.channel_event_count,
		       sizeof(record.channel_event_count));
		memset(&peer->qos, 0, sizeof(peer->qos));
		peer->qos.window_start_ms = now_ms;
		k_spin_unlock(&qos_lock, key);

		if (atomic_get(&host_binary_output) != 0) {
			host_binary_emit(BSF_HOST_RECORD_QOS, node_name, now_ms,
					 &record, sizeof(record));
		} else {
			printk("FUSION_QOS name=%s master_ms=%u spacing=%s spacing_us=%u generation=%u handle=%u window_ms=%u reports=%u event_gaps=%u crc_ok=%u crc_error=%u nak=%u rx_timeout=%u first_event=%u last_event=%u imu_epoch_defer_drop=%u delivered_imu=%u delivered_uwb=%u delivered_ctl=%u\n",
			       node_name, now_ms,
			       spacing_mode_name(spacing_active_mode),
			       spacing_active_us, spacing_generation,
			       record.conn_handle, record.window_duration_ms,
			       record.report_count,
			       record.event_counter_gap_count,
			       record.crc_ok_count, record.crc_error_count,
			       record.nak_count, record.rx_timeout_count,
			       record.first_event_counter,
			       record.last_event_counter,
			       record.imu_epoch_defer_drop,
			       record.delivered_imu, record.delivered_uwb,
			       record.delivered_ctl);
		}
	}
	report_master_pool_usage(now_ms);
	k_work_reschedule(&qos_work, K_MSEC(QOS_WINDOW_MS));
}

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

	printk("FUSION_UWB proto=%u name=%s master_ms=%llu node_ms=%u pkt=%u sweep=%u identity=%04X logical=%u poll_tx=%010llX frame_us=%llu strobe_us=%s rise_us=%s fall_us=%s pair_dt_us=%s verdict=%s edge=%s candidates=%u window_us=%u valid=0x%02x flags=0x%02x strobe_sent=%u rise_n=%u fall_n=%u boot_discard=%u edge_qdrop=%u orphan_strobe=%u orphan_edge=%u orphan_frame=%u near_window=%u last_orphan_us=%s capture_flags=0x%02x ranges=%s\n",
	       packet->version,
	       record->node_name,
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

	/*
	 * Text mode is intentionally concise and interactive-only. The complete
	 * lossless telemetry record is the binary default and is rendered by the
	 * PC decoder; no split-line/truncation workaround remains on the DK.
	 */
	printk("FUSION_TELEMETRY proto=%u name=%s node_ms=%u frames=%u crc=%u header=%u ring_drop=%u sweep_drop=%u duplicate=%u reorder=%u drop_unsub=%u drop_err=%u rise_n=%u fall_n=%u edge_qdrop=%u orphan_strobe=%u orphan_edge=%u orphan_frame=%u timer_wraps=%u imu_pulls=%u imu_records=%u imu_missed_deadlines=%u imu_i2c_err=%u imu_rate=%u imu_batch=%u imu_active=%u imu_health=%u/%u/%u relay_timeout=%u imu_pull_late_max_us=%u imu_pull_dur_max_us=%u\n",
	       telemetry->version,
	       record->node_name,
	       telemetry->node_uptime_ms,
	       telemetry->valid_frames, telemetry->crc_errors,
	       telemetry->header_errors, telemetry->ring_dropped_bytes,
	       telemetry->dropped_sweeps, telemetry->duplicate_sweeps,
	       telemetry->out_of_order_sweeps, telemetry->drop_unsub,
	       telemetry->drop_err, telemetry->rising_edge_count,
	       telemetry->falling_edge_count, telemetry->edge_queue_drop_count,
	       telemetry->orphan_strobe_count, telemetry->orphan_edge_count,
	       telemetry->orphan_frame_count, telemetry->timer_wrap_count,
	       telemetry->imu_pulls, telemetry->imu_records,
	       telemetry->imu_missed_deadlines, telemetry->imu_i2c_err,
	       telemetry->imu_rate_hz, telemetry->imu_batch,
	       telemetry->imu_active, telemetry->imu_health_class,
	       telemetry->imu_health_active, telemetry->imu_health_latched,
	       telemetry->relay_timeout,
	       telemetry->imu_pull_lateness_max_us,
	       telemetry->imu_pull_duration_max_us);
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
	printk("FUSION_IMU proto=%u name=%s master_ms=%llu seq=%u base_us=%llu n=%u temp_raw=%d samples=%s\n",
		       imu->version,
		       record->node_name,
		       (unsigned long long)record->master_arrival_ms,
		       imu->sequence,
		       (unsigned long long)imu->base_timer2_ts_us,
	       imu->sample_count,
	       imu->temperature,
	       samples);
}

static void log_reply_record(const struct fusion_log_record *record)
{
	const struct fusion_reply_log *reply = &record->payload.reply;

	printk("FUSION_REPLY proto=%u name=%s master_ms=%llu source=%s correlation=%u text=%s\n",
	       reply->prefix.version,
	       record->node_name,
	       (unsigned long long)record->master_arrival_ms,
	       reply->prefix.source == BSF_CONTROL_SOURCE_TAG ? "TAG" : "B306",
	       reply->prefix.correlation,
	       reply->text);
}

static void log_queue_counters_record(const struct fusion_log_record *record)
{
	const bsf_ble_queue_counters_t *queue =
		&record->payload.queue_counters;

	printk("FUSION_QUEUE proto=%u name=%s master_ms=%llu node_ms=%u q_drop_imu=%u q_drop_uwb=%u q_drop_ctl=%u q_hwm_imu=%u q_hwm_uwb=%u q_hwm_ctl=%u publisher_count=%u publisher_max_us=%u enq_imu=%u enq_uwb=%u enq_ctl=%u abort_imu=%u abort_uwb=%u abort_ctl=%u delivered_imu=%u delivered_uwb=%u delivered_ctl=%u imu_epoch_defer_drop=%u\n",
	       queue->version, record->node_name,
	       (unsigned long long)record->master_arrival_ms,
	       queue->node_uptime_ms, queue->q_drop_imu,
	       queue->q_drop_uwb, queue->q_drop_ctl,
	       queue->q_hwm_imu, queue->q_hwm_uwb, queue->q_hwm_ctl,
	       queue->publisher_count, queue->publisher_max_us,
	       queue->enq_imu, queue->enq_uwb, queue->enq_ctl,
	       queue->abort_imu, queue->abort_uwb, queue->abort_ctl,
	       record->peer_delivered_imu, record->peer_delivered_uwb,
	       record->peer_delivered_ctl,
	       record->peer_imu_epoch_defer_drop);
}

static void log_pool_usage_record(const struct fusion_log_record *record)
{
	const bsf_ble_pool_usage_t *p = &record->payload.pool_usage;

	printk("FUSION_POOL proto=%u name=%s master_ms=%llu node_ms=%u count=%u",
	       p->version, record->node_name,
	       (unsigned long long)record->master_arrival_ms,
	       p->node_uptime_ms, p->pool_count);
	for (uint8_t i = 0u; i < p->pool_count &&
	     i < ARRAY_SIZE(p->pools); ++i) {
		printk(" pool%u=%08x:%u/%u", i, p->pools[i].name_hash,
		       p->pools[i].available, p->pools[i].low_water);
	}
	printk("\n");
}

static void log_binary_record(const struct fusion_log_record *record)
{
	if (record->kind == FUSION_LOG_UWB) {
		host_binary_emit(BSF_HOST_RECORD_UWB, record->node_name,
				 record->master_arrival_ms,
				 &record->payload.uwb,
				 sizeof(record->payload.uwb));
	} else if (record->kind == FUSION_LOG_TELEMETRY) {
		uint8_t payload[sizeof(record->payload.telemetry) +
				6u * sizeof(uint32_t)];
		uint32_t counters[6] = {
			record->peer_received_packets,
			record->peer_malformed_packets,
			record->peer_logger_dropped,
			received_packets,
			malformed_packets,
			logger_dropped,
		};
		size_t telemetry_length = record->payload.telemetry.len;

		memcpy(payload, &record->payload.telemetry,
		       telemetry_length);
		memcpy(&payload[telemetry_length], counters,
		       sizeof(counters));
		host_binary_emit(BSF_HOST_RECORD_TELEMETRY, record->node_name,
				 record->master_arrival_ms,
				 payload, telemetry_length + sizeof(counters));
	} else if (record->kind == FUSION_LOG_IMU) {
		const struct fusion_imu_log *imu = &record->payload.imu;
		uint8_t payload[sizeof(bsf_host_imu_prefix_t) +
				BSF_IMU_BATCH_MAX *
					sizeof(bsf_ble_imu_sample_t)];
		bsf_host_imu_prefix_t prefix = {
			.ble_version = imu->version,
			.sample_count = imu->sample_count,
			.sequence = imu->sequence,
			.base_timer2_ts_us = imu->base_timer2_ts_us,
			.temperature_raw = imu->temperature,
		};
		size_t payload_length = sizeof(prefix) +
			(size_t)imu->sample_count *
				sizeof(bsf_ble_imu_sample_t);

		memcpy(payload, &prefix, sizeof(prefix));
		memcpy(&payload[sizeof(prefix)], imu->samples,
		       payload_length - sizeof(prefix));
		host_binary_emit(BSF_HOST_RECORD_IMU, record->node_name,
				 record->master_arrival_ms,
				 payload, payload_length);
	} else if (record->kind == FUSION_LOG_REPLY) {
		const struct fusion_reply_log *reply = &record->payload.reply;
		uint8_t payload[sizeof(reply->prefix) +
				BSF_CONTROL_REPLY_TEXT_MAX];
		size_t text_length = strnlen(reply->text,
					    BSF_CONTROL_REPLY_TEXT_MAX);

		memcpy(payload, &reply->prefix, sizeof(reply->prefix));
		memcpy(&payload[sizeof(reply->prefix)], reply->text,
		       text_length);
		host_binary_emit(BSF_HOST_RECORD_REPLY, record->node_name,
				 record->master_arrival_ms,
				 payload, sizeof(reply->prefix) + text_length);
	} else if (record->kind == FUSION_LOG_QUEUE_COUNTERS) {
		uint8_t payload[sizeof(record->payload.queue_counters) +
				4u * sizeof(uint32_t)];
		uint32_t delivery[4] = {
			record->peer_delivered_imu,
			record->peer_delivered_uwb,
			record->peer_delivered_ctl,
			record->peer_imu_epoch_defer_drop,
		};

		memcpy(payload, &record->payload.queue_counters,
		       sizeof(record->payload.queue_counters));
		memcpy(&payload[sizeof(record->payload.queue_counters)],
		       delivery, sizeof(delivery));
		host_binary_emit(BSF_HOST_RECORD_QUEUE_COUNTERS,
				 record->node_name,
				 record->master_arrival_ms,
				 payload, sizeof(payload));
	} else if (record->kind == FUSION_LOG_POOL_USAGE) {
		host_binary_emit(BSF_HOST_RECORD_POOL_USAGE,
				 record->node_name,
				 record->master_arrival_ms,
				 &record->payload.pool_usage,
				 sizeof(record->payload.pool_usage));
	}
}

static void fusion_log_thread(void *first, void *second, void *third)
{
	struct fusion_log_record record;

	ARG_UNUSED(first);
	ARG_UNUSED(second);
	ARG_UNUSED(third);
	while (true) {
		(void)k_msgq_get(&fusion_log_queue, &record, K_FOREVER);
		if (atomic_get(&host_binary_output) != 0) {
			log_binary_record(&record);
		} else if (record.kind == FUSION_LOG_UWB) {
			log_uwb_record(&record);
		} else if (record.kind == FUSION_LOG_TELEMETRY) {
			log_telemetry_record(&record);
		} else if (record.kind == FUSION_LOG_IMU) {
			log_imu_record(&record);
		} else if (record.kind == FUSION_LOG_REPLY) {
			log_reply_record(&record);
		} else if (record.kind == FUSION_LOG_QUEUE_COUNTERS) {
			log_queue_counters_record(&record);
		} else if (record.kind == FUSION_LOG_POOL_USAGE) {
			log_pool_usage_record(&record);
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
		.interval_min = 40, /* 50 ms */
		.interval_max = 40, /* 50 ms */
		.latency = 0,
		.timeout = 400, /* 4 s */
	};
	char addr[BT_ADDR_LE_STR_LEN];
	struct fusion_peer *peer;
	int err;

	if (spacing_transition || spacing_failed || connecting ||
	    peer_count_allocated() >= MAX_FUSION_PEERS ||
	    !candidate_has_service ||
	    !candidate_has_name) {
		return;
	}
	if (peer_by_addr(&candidate_addr) != NULL ||
	    peer_by_name(candidate_name) != NULL) {
		return;
	}

	peer = allocate_peer(&candidate_addr, candidate_name, candidate_rssi);
	if (peer == NULL) {
		printk("FUSION_FAIL step=peer_allocate name=%s\n",
		       candidate_name);
		return;
	}
	connecting = true;
	connecting_peer = peer;
	bt_addr_le_to_str(&candidate_addr, addr, sizeof(addr));
	printk("FUSION_TARGET name=%s addr=%s rssi=%d\n",
	       candidate_name, addr, candidate_rssi);

	err = bt_le_scan_stop();
	if (err != 0 && err != -EALREADY) {
		printk("FUSION_FAIL step=scan_stop err=%d\n", err);
		connecting = false;
		connecting_peer = NULL;
		release_peer(peer);
		return;
	}

	err = bt_conn_le_create(&candidate_addr, BT_CONN_LE_CREATE_CONN,
				&conn_params, &peer->conn);
	if (err != 0) {
		printk("FUSION_FAIL step=connect_start name=%s err=%d\n",
		       peer->name, err);
		connecting = false;
		connecting_peer = NULL;
		release_peer(peer);
		start_scan();
	}
}

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	struct advertising_fields fields = { 0 };

	ARG_UNUSED(type);

	if (spacing_transition || spacing_failed || connecting ||
	    peer_count_allocated() >= MAX_FUSION_PEERS) {
		return;
	}

	bt_data_parse(ad, advertising_field, &fields);
	if (!fields.has_service && !fields.has_name) {
		return;
	}
	if (peer_by_addr(addr) != NULL ||
	    (fields.has_name && peer_by_name(fields.name) != NULL)) {
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

	if (spacing_transition || spacing_failed || connecting ||
	    peer_count_allocated() >= MAX_FUSION_PEERS) {
		return;
	}
	candidate_valid = false;
	candidate_has_service = false;
	candidate_has_name = false;
	memset(candidate_name, 0, sizeof(candidate_name));

	err = bt_le_scan_start(&scan_params, device_found);
	if (err != 0 && err != -EALREADY) {
		printk("FUSION_FAIL step=scan_start err=%d\n", err);
		return;
	}

	printk("FUSION_SCAN_STARTED target=BSFxxxx service=7b120001 connected=%u capacity=%u\n",
	       (unsigned int)peer_count_allocated(), MAX_FUSION_PEERS);
}

static void spacing_transition_work_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(spacing_transition_work,
			spacing_transition_work_handler);

static void spacing_transition_work_handler(struct k_work *work)
{
	int err;

	ARG_UNUSED(work);
	if (!spacing_transition) {
		return;
	}
	if (connecting || peer_count_allocated() != 0u) {
		k_work_reschedule(&spacing_transition_work, K_MSEC(50));
		return;
	}

	err = spacing_apply(spacing_target_mode);
	if (err != 0) {
		spacing_failed = true;
		spacing_transition = false;
		printk("FUSION_SPACING_FAILED target=%s err=%d action=scan_blocked\n",
		       spacing_mode_name(spacing_target_mode), err);
		spacing_status_print("FAILED");
		return;
	}
	spacing_failed = false;
	spacing_transition = false;
	spacing_status_print("APPLIED");
	start_scan();
}

static void spacing_request(enum spacing_mode target)
{
	int scan_err;

	if (spacing_transition) {
		printk("FUSION_SPACING_REJECT reason=transition_in_progress target=%s\n",
		       spacing_mode_name(target));
		return;
	}
	if (!spacing_failed && target == spacing_active_mode) {
		spacing_status_print("UNCHANGED");
		return;
	}

	spacing_target_mode = target;
	spacing_transition = true;
	spacing_failed = false;
	printk("FUSION_SPACING_QUEUED target=%s target_us=%u action=disconnect_all_then_apply_before_reconnect\n",
	       spacing_mode_name(target),
	       target == SPACING_MODE_ON ? SPACING_ON_US : SPACING_OFF_US);
	scan_err = bt_le_scan_stop();
	if (scan_err != 0 && scan_err != -EALREADY) {
		printk("FUSION_FAIL step=spacing_scan_stop err=%d\n", scan_err);
	}
	for (size_t i = 0u; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].allocated && peers[i].conn != NULL) {
			int err = bt_conn_disconnect(
				peers[i].conn,
				BT_HCI_ERR_REMOTE_USER_TERM_CONN);

			if (err != 0 && err != -ENOTCONN) {
				printk("FUSION_FAIL name=%s step=spacing_disconnect err=%d\n",
				       peers[i].name, err);
			}
		}
	}
	k_work_reschedule(&spacing_transition_work, K_NO_WAIT);
}

static uint8_t data_notification(
	struct bt_conn *conn,
	struct bt_gatt_subscribe_params *params,
	const void *data, uint16_t length)
{
	struct fusion_log_record record = {
		.master_arrival_ms = (uint64_t)k_uptime_get(),
	};
	struct fusion_peer *peer = peer_by_conn(conn);
	const uint8_t *bytes = data;
	uint16_t declared_length;
	uint8_t kind;

	if (peer == NULL) {
		printk("FUSION_MALFORMED name=- kind=unknown_connection len=%u\n",
		       length);
		return BT_GATT_ITER_STOP;
	}
	memcpy(record.node_name, peer->name, sizeof(record.node_name));

	if (data == NULL) {
		printk("FUSION_DATA_UNSUBSCRIBED name=%s\n", peer->name);
		params->value_handle = 0;
		return BT_GATT_ITER_STOP;
	}

	if (length < 4u) {
		mark_malformed(peer);
		printk("FUSION_MALFORMED name=%s kind=data_short len=%u node_total=%u total=%u\n",
		       peer->name, length, peer->malformed_packets,
		       malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}
	memcpy(&declared_length, &bytes[2], sizeof(declared_length));
	kind = bytes[1];
	if ((bytes[0] != BSF_BLE_PROTOCOL_VERSION &&
	     bytes[0] != BSF_BLE_PROTOCOL_PREVIOUS) ||
	    declared_length != length) {
		mark_malformed(peer);
		printk("FUSION_MALFORMED name=%s kind=data_header version=%u type=%u declared=%u actual=%u node_total=%u total=%u\n",
		       peer->name, bytes[0], kind, declared_length, length,
		       peer->malformed_packets, malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}

	if (kind == BSF_BLE_KIND_UWB) {
		bsf_ble_uwb_packet_t *packet = &record.payload.uwb;

		if (length != sizeof(*packet)) {
			goto malformed_kind_length;
		}
		record.kind = FUSION_LOG_UWB;
		memcpy(packet, data, sizeof(*packet));
		note_full_node_time(peer, packet->capture.frame_rx_ts_us);
		led_check_uwb_sequence(peer, packet);
		led_note_uwb_record();
	} else if (kind == BSF_BLE_KIND_IMU) {
		struct fusion_imu_log *imu = &record.payload.imu;
		struct bsf_ble_imu_prefix_v25 v25_prefix;
		bsf_ble_imu_prefix_t prefix;
		size_t prefix_size;
		size_t sample_bytes;
		size_t sample_count;
		size_t temperature_offset;
		bool v25_layout = false;

		if (length >= BSF_IMU_V25_RECORD_LEN(BSF_IMU_BATCH_MIN) &&
		    (length - sizeof(struct bsf_ble_imu_prefix_v25) -
		     sizeof(int16_t)) % sizeof(bsf_ble_imu_sample_t) == 0u) {
			prefix_size = sizeof(struct bsf_ble_imu_prefix_v25);
			v25_layout = true;
		} else if (length >= BSF_IMU_RECORD_LEN(BSF_IMU_BATCH_MIN) &&
			   (length - sizeof(bsf_ble_imu_prefix_t) -
			    sizeof(int16_t)) %
				   sizeof(bsf_ble_imu_sample_t) == 0u) {
			prefix_size = sizeof(bsf_ble_imu_prefix_t);
		} else {
			goto malformed_kind_length;
		}
		sample_bytes = length - prefix_size - sizeof(int16_t);
		sample_count = sample_bytes / sizeof(bsf_ble_imu_sample_t);
		if (sample_count < BSF_IMU_BATCH_MIN ||
		    sample_count > BSF_IMU_BATCH_MAX) {
			goto malformed_kind_length;
		}
		record.kind = FUSION_LOG_IMU;
		if (v25_layout) {
			memcpy(&v25_prefix, data, sizeof(v25_prefix));
			imu->version = v25_prefix.version;
			imu->sequence = v25_prefix.seq;
			imu->base_timer2_ts_us = v25_prefix.base_timer2_ts_us;
			peer->imu_last_extended_base_us =
				v25_prefix.base_timer2_ts_us;
			peer->imu_have_extended_base = true;
			} else {
				memcpy(&prefix, data, sizeof(prefix));
				imu->version = prefix.version;
				imu->sequence = prefix.seq;
				if (!extend_imu_base(peer,
						     prefix.base_timer2_ts_us,
						     &imu->base_timer2_ts_us)) {
					if (!peer->imu_wait_epoch_reported) {
						printk("FUSION_IMU_WAIT_EPOCH name=%s low_us=%u action=defer_until_uwb_or_telemetry\n",
						       peer->name,
						       prefix.base_timer2_ts_us);
						peer->imu_wait_epoch_reported =
							true;
						}
						++peer->imu_epoch_defer_drop;
						++peer->delivered_imu;
						return BT_GATT_ITER_CONTINUE;
				}
				peer->imu_wait_epoch_reported = false;
			}
		memcpy(imu->samples, &bytes[prefix_size], sample_bytes);
		temperature_offset = prefix_size + sample_bytes;
		memcpy(&imu->temperature, &bytes[temperature_offset],
		       sizeof(imu->temperature));
		imu->sample_count = (uint8_t)sample_count;
		led_note_imu_record();
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
	} else if (kind == BSF_BLE_KIND_QUEUE_COUNTERS) {
		bsf_ble_queue_counters_t *queue =
			&record.payload.queue_counters;

		if (length != sizeof(*queue)) {
			goto malformed_kind_length;
		}
		record.kind = FUSION_LOG_QUEUE_COUNTERS;
		memcpy(queue, data, sizeof(*queue));
		led_check_queue(peer, queue);
	} else if (kind == BSF_BLE_KIND_POOL_USAGE) {
		bsf_ble_pool_usage_t *pools = &record.payload.pool_usage;

		if (length != sizeof(*pools)) {
			goto malformed_kind_length;
		}
		record.kind = FUSION_LOG_POOL_USAGE;
		memcpy(pools, data, sizeof(*pools));
	} else {
		mark_malformed(peer);
		printk("FUSION_MALFORMED name=%s kind=unknown type=%u len=%u node_total=%u total=%u\n",
		       peer->name, kind, length, peer->malformed_packets,
		       malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}

	if (record.kind == FUSION_LOG_IMU) {
		++peer->delivered_imu;
	} else if (record.kind == FUSION_LOG_UWB) {
		++peer->delivered_uwb;
	} else {
		++peer->delivered_ctl;
	}
	++received_packets;
	++peer->received_packets;
	record.peer_received_packets = peer->received_packets;
	record.peer_malformed_packets = peer->malformed_packets;
	record.peer_logger_dropped = peer->logger_dropped;
	record.peer_imu_epoch_defer_drop = peer->imu_epoch_defer_drop;
	record.peer_delivered_imu = peer->delivered_imu;
	record.peer_delivered_uwb = peer->delivered_uwb;
	record.peer_delivered_ctl = peer->delivered_ctl;
	if (k_msgq_put(&fusion_log_queue, &record, K_NO_WAIT) != 0) {
		++logger_dropped;
		++peer->logger_dropped;
		led_note_fault(FUSION_LED_FAULT_QUEUE, 1u);
	} else {
		note_fusion_log_queue_high_water();
	}

	return BT_GATT_ITER_CONTINUE;

malformed_kind_length:
	mark_malformed(peer);
	printk("FUSION_MALFORMED name=%s kind=%u len=%u node_total=%u total=%u\n",
	       peer->name, kind, length, peer->malformed_packets,
	       malformed_packets);
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
	struct fusion_peer *peer = peer_by_conn(conn);
	bsf_ble_telemetry_t *telemetry = &record.payload.telemetry;

	if (peer == NULL) {
		printk("FUSION_MALFORMED name=- kind=telemetry_unknown_connection len=%u\n",
		       length);
		return BT_GATT_ITER_STOP;
	}
	memcpy(record.node_name, peer->name, sizeof(record.node_name));

	if (data == NULL) {
		printk("FUSION_TELEMETRY_UNSUBSCRIBED name=%s\n",
		       peer->name);
		params->value_handle = 0;
		return BT_GATT_ITER_STOP;
	}

	if (length != sizeof(*telemetry) &&
	    length != BSF_BLE_TELEMETRY_V4_SIZE) {
		mark_malformed(peer);
		printk("FUSION_MALFORMED name=%s kind=telemetry len=%u expected=%u node_total=%u total=%u\n",
		       peer->name, length, (unsigned int)sizeof(*telemetry),
		       peer->malformed_packets, malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}

	memset(telemetry, 0, sizeof(*telemetry));
	memcpy(telemetry, data, length);
	if ((telemetry->version != BSF_BLE_PROTOCOL_VERSION &&
	     telemetry->version != BSF_BLE_PROTOCOL_PREVIOUS) ||
	    telemetry->kind != BSF_BLE_KIND_TELEMETRY ||
	    telemetry->len != length) {
		mark_malformed(peer);
		printk("FUSION_MALFORMED name=%s kind=telemetry_header version=%u type=%u declared=%u node_total=%u total=%u\n",
		       peer->name, telemetry->version, telemetry->kind,
		       telemetry->len, peer->malformed_packets,
		       malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}
	peer->node_timer_wrap_count = telemetry->timer_wrap_count;
	peer->node_have_timer_wrap_count = true;
	led_check_telemetry(peer, telemetry);
	++received_packets;
	++peer->received_packets;
	++peer->delivered_ctl;
	record.peer_received_packets = peer->received_packets;
	record.peer_malformed_packets = peer->malformed_packets;
	record.peer_logger_dropped = peer->logger_dropped;

	if (k_msgq_put(&fusion_log_queue, &record, K_NO_WAIT) != 0) {
		++logger_dropped;
		++peer->logger_dropped;
		led_note_fault(FUSION_LED_FAULT_QUEUE, 1u);
	} else {
		note_fusion_log_queue_high_water();
	}

	return BT_GATT_ITER_CONTINUE;
}

static uint8_t discover_fusion(struct bt_conn *conn,
			       const struct bt_gatt_attr *attr,
			       struct bt_gatt_discover_params *params)
{
	struct fusion_peer *peer = peer_by_conn(conn);
	int err;

	if (peer == NULL || params != &peer->discover_params) {
		printk("FUSION_FAIL name=- step=discover_unknown_peer\n");
		return BT_GATT_ITER_STOP;
	}
	if (attr == NULL) {
		if (peer->discovery_stage == DISCOVERY_STALL_CHARACTERISTIC) {
			/*
			 * v35 and earlier have no status characteristic.  Keep the
			 * control path usable so that such nodes can be upgraded OTA;
			 * v36 discovery records the read handle normally.
			 */
			printk("FUSION_STALL_CHARACTERISTIC name=%s value=0 compatibility=pre_v36\n",
			       peer->name);
			peer->discovery_stage = DISCOVERY_CONTROL_CHARACTERISTIC;
			peer->discover_params.uuid = &fusion_control_uuid.uuid;
			peer->discover_params.start_handle =
				peer->telemetry_value_handle + 2u;
			peer->discover_params.end_handle = peer->service_end_handle;
			peer->discover_params.type =
				BT_GATT_DISCOVER_CHARACTERISTIC;
			err = bt_gatt_discover(conn, &peer->discover_params);
			if (err != 0) {
				printk("FUSION_FAIL name=%s step=discover_control_compat_start err=%d\n",
				       peer->name, err);
			}
			return BT_GATT_ITER_STOP;
		}
		printk("FUSION_FAIL name=%s step=discover_%u err=not_found start=%u end=%u mode=%s\n",
		       peer->name, peer->discovery_stage,
		       params->start_handle, params->end_handle,
		       IS_ENABLED(CONFIG_BSF_CCC_FILTERED_REPRO) ?
			       "filtered_repro" : "enumerated");
		if (IS_ENABLED(CONFIG_BSF_CCC_FILTERED_REPRO)) {
			log_net_buf_pools("ccc_not_found");
		}
		memset(params, 0, sizeof(*params));
		err = bt_conn_disconnect(peer->conn,
					 BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		printk("FUSION_DISCOVERY_ABORT name=%s disconnect_err=%d\n",
		       peer->name, err);
		return BT_GATT_ITER_STOP;
	}

	switch (peer->discovery_stage) {
	case DISCOVERY_SERVICE: {
		const struct bt_gatt_service_val *service = attr->user_data;

		peer->service_end_handle = service->end_handle;
		printk("FUSION_SERVICE name=%s start=%u end=%u\n",
		       peer->name, attr->handle, peer->service_end_handle);

		peer->discovery_stage = DISCOVERY_DATA_CHARACTERISTIC;
		peer->discover_params.uuid = &fusion_data_uuid.uuid;
		peer->discover_params.start_handle = attr->handle + 1;
		peer->discover_params.end_handle = peer->service_end_handle;
		peer->discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;
		err = bt_gatt_discover(conn, &peer->discover_params);
		if (err != 0) {
			printk("FUSION_FAIL name=%s step=discover_data_start err=%d\n",
			       peer->name, err);
		}
		break;
	}

	case DISCOVERY_DATA_CHARACTERISTIC: {
		const struct bt_gatt_chrc *characteristic = attr->user_data;

		peer->data_value_handle = characteristic->value_handle;
		printk("FUSION_DATA_CHARACTERISTIC name=%s value=%u props=0x%02x\n",
		       peer->name, peer->data_value_handle,
		       characteristic->properties);
		if ((characteristic->properties & BT_GATT_CHRC_NOTIFY) == 0u) {
			printk("FUSION_FAIL name=%s step=data_not_notifiable\n",
			       peer->name);
			break;
		}

		peer->discovery_stage = DISCOVERY_DATA_CCC;
		/*
		 * As with the telemetry CCC below, enumerate the descriptor and
		 * validate it in the callback.  UUID-filtered CCC discovery is not
		 * reliable on the deployed Zephyr peers once another connection is
		 * already subscribed.
		 */
		peer->discover_params.uuid =
			IS_ENABLED(CONFIG_BSF_CCC_FILTERED_REPRO) ?
				BT_UUID_GATT_CCC : NULL;
		peer->discover_params.start_handle =
			peer->data_value_handle + 1;
		peer->discover_params.end_handle = peer->service_end_handle;
		peer->discover_params.type = BT_GATT_DISCOVER_DESCRIPTOR;
		printk("FUSION_CCC_DISCOVERY name=%s kind=data mode=%s start=%u end=%u att_pool_total=%u l2cap_pool_total=%u acl_rx_total=%u acl_tx_configured=%u acl_tx_available=%u acl_tx_controller_total=%u\n",
		       peer->name,
		       IS_ENABLED(CONFIG_BSF_CCC_FILTERED_REPRO) ?
			       "uuid_filtered" : "enumerated",
		       peer->discover_params.start_handle,
		       peer->discover_params.end_handle,
		       CONFIG_BT_ATT_TX_COUNT,
		       CONFIG_BT_L2CAP_TX_BUF_COUNT,
		       CONFIG_BT_BUF_ACL_RX_COUNT,
		       CONFIG_BT_BUF_ACL_TX_COUNT,
		       (unsigned int)k_sem_count_get(
			       &bt_dev.le.acl_pkts),
		       (unsigned int)bt_dev.le.acl_pkts.limit);
		if (IS_ENABLED(CONFIG_BSF_CCC_FILTERED_REPRO)) {
			log_net_buf_pools("ccc_start");
		}
		err = bt_gatt_discover(conn, &peer->discover_params);
		if (err != 0) {
			printk("FUSION_FAIL name=%s step=discover_data_ccc_start err=%d\n",
			       peer->name, err);
		}
		break;
	}

	case DISCOVERY_DATA_CCC:
		if (bt_uuid_cmp(attr->uuid, BT_UUID_GATT_CCC) != 0) {
			printk("FUSION_FAIL name=%s step=data_descriptor_not_ccc handle=%u\n",
			       peer->name, attr->handle);
			memset(params, 0, sizeof(*params));
			break;
		}
		peer->data_subscribe_params.notify = data_notification;
		peer->data_subscribe_params.value = BT_GATT_CCC_NOTIFY;
		peer->data_subscribe_params.value_handle =
			peer->data_value_handle;
		peer->data_subscribe_params.ccc_handle = attr->handle;
		err = bt_gatt_subscribe(conn, &peer->data_subscribe_params);
		if (err != 0 && err != -EALREADY) {
			printk("FUSION_FAIL name=%s step=subscribe_data err=%d\n",
			       peer->name, err);
			break;
		}
		printk("FUSION_DATA_SUBSCRIBED name=%s value=%u ccc=%u\n",
		       peer->name, peer->data_value_handle, attr->handle);

		peer->discovery_stage =
			DISCOVERY_TELEMETRY_CHARACTERISTIC;
		peer->discover_params.uuid = &fusion_telemetry_uuid.uuid;
		peer->discover_params.start_handle = attr->handle + 1;
		peer->discover_params.end_handle = peer->service_end_handle;
		peer->discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;
		err = bt_gatt_discover(conn, &peer->discover_params);
		if (err != 0) {
			printk("FUSION_FAIL name=%s step=discover_telemetry_start err=%d\n",
			       peer->name, err);
		}
		break;

	case DISCOVERY_TELEMETRY_CHARACTERISTIC: {
		const struct bt_gatt_chrc *characteristic = attr->user_data;

		peer->telemetry_value_handle = characteristic->value_handle;
		printk("FUSION_TELEMETRY_CHARACTERISTIC name=%s value=%u props=0x%02x\n",
		       peer->name, peer->telemetry_value_handle,
		       characteristic->properties);
		if ((characteristic->properties & BT_GATT_CHRC_NOTIFY) == 0u) {
			printk("FUSION_FAIL name=%s step=telemetry_not_notifiable\n",
			       peer->name);
			break;
		}

		peer->discovery_stage = DISCOVERY_TELEMETRY_CCC;
		/*
		 * Enumerate the descriptor at the telemetry value boundary and
		 * validate its UUID in the callback.  A filtered CCC discovery can
		 * return not-found here on the deployed Zephyr peer even though the
		 * descriptor is present at the next handle.
		 */
		peer->discover_params.uuid = NULL;
		peer->discover_params.start_handle =
			peer->telemetry_value_handle + 1u;
		peer->discover_params.end_handle = peer->service_end_handle;
		peer->discover_params.type = BT_GATT_DISCOVER_DESCRIPTOR;
		err = bt_gatt_discover(conn, &peer->discover_params);
		if (err != 0) {
			printk("FUSION_FAIL name=%s step=discover_telemetry_ccc_start err=%d\n",
			       peer->name, err);
		}
		break;
	}

	case DISCOVERY_TELEMETRY_CCC:
		if (bt_uuid_cmp(attr->uuid, BT_UUID_GATT_CCC) != 0) {
			printk("FUSION_FAIL name=%s step=telemetry_descriptor_not_ccc handle=%u\n",
			       peer->name, attr->handle);
			memset(params, 0, sizeof(*params));
			break;
		}
		peer->telemetry_subscribe_params.notify =
			telemetry_notification;
		peer->telemetry_subscribe_params.value = BT_GATT_CCC_NOTIFY;
		peer->telemetry_subscribe_params.value_handle =
			peer->telemetry_value_handle;
		peer->telemetry_subscribe_params.ccc_handle = attr->handle;
		err = bt_gatt_subscribe(conn,
					&peer->telemetry_subscribe_params);
		if (err != 0 && err != -EALREADY) {
			printk("FUSION_FAIL name=%s step=subscribe_telemetry err=%d\n",
			       peer->name, err);
			break;
		}
		printk("FUSION_TELEMETRY_SUBSCRIBED name=%s value=%u ccc=%u\n",
		       peer->name, peer->telemetry_value_handle,
		       attr->handle);

		peer->discovery_stage = DISCOVERY_STALL_CHARACTERISTIC;
		peer->discover_params.uuid = &fusion_stall_uuid.uuid;
		peer->discover_params.start_handle = attr->handle + 1u;
		peer->discover_params.end_handle = peer->service_end_handle;
		peer->discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;
		err = bt_gatt_discover(conn, &peer->discover_params);
		if (err != 0) {
			printk("FUSION_FAIL name=%s step=discover_stall_start err=%d\n",
			       peer->name, err);
		}
		break;

	case DISCOVERY_STALL_CHARACTERISTIC: {
		const struct bt_gatt_chrc *characteristic = attr->user_data;

		peer->stall_value_handle = characteristic->value_handle;
		printk("FUSION_STALL_CHARACTERISTIC name=%s value=%u props=0x%02x\n",
		       peer->name, peer->stall_value_handle,
		       characteristic->properties);
		if ((characteristic->properties & BT_GATT_CHRC_READ) == 0u) {
			printk("FUSION_FAIL name=%s step=stall_not_readable\n",
			       peer->name);
			break;
		}
		peer->discovery_stage = DISCOVERY_CONTROL_CHARACTERISTIC;
		peer->discover_params.uuid = &fusion_control_uuid.uuid;
		peer->discover_params.start_handle =
			peer->stall_value_handle + 1u;
		peer->discover_params.end_handle = peer->service_end_handle;
		peer->discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;
		err = bt_gatt_discover(conn, &peer->discover_params);
		if (err != 0) {
			printk("FUSION_FAIL name=%s step=discover_control_start err=%d\n",
			       peer->name, err);
		}
		break;
	}

	case DISCOVERY_CONTROL_CHARACTERISTIC: {
		const struct bt_gatt_chrc *characteristic = attr->user_data;

		peer->control_value_handle = characteristic->value_handle;
		printk("FUSION_CONTROL_CHARACTERISTIC name=%s value=%u props=0x%02x\n",
		       peer->name, peer->control_value_handle,
		       characteristic->properties);
		if ((characteristic->properties &
		     (BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP)) ==
		    0u) {
			printk("FUSION_FAIL name=%s step=control_not_writable\n",
			       peer->name);
			break;
		}

		struct bt_conn_info info;
		int info_err = bt_conn_get_info(conn, &info);
		uint16_t interval = 0u;
		uint16_t latency = 0u;
		uint16_t timeout = 0u;

		if (info_err == 0 && info.type == BT_CONN_TYPE_LE) {
			interval = info.le.interval;
			latency = info.le.latency;
			timeout = info.le.timeout;
		}

		peer->interval = interval;
		peer->latency = latency;
		peer->timeout = timeout;
		peer->bridge_ready = true;
		printk("FUSION_BRIDGE_READY name=%s rssi=%d mtu=%u data=%u telemetry=%u stall=%u control=%u interval_units=%u interval_us=%u latency=%u timeout_units=%u\n",
		       peer->name, peer->rssi, bt_gatt_get_mtu(conn),
		       peer->data_value_handle, peer->telemetry_value_handle,
		       peer->stall_value_handle,
		       peer->control_value_handle, interval,
		       BT_CONN_INTERVAL_TO_US(interval), latency, timeout);

		reconnect_probe_note_bridge_ready(peer);

		if (info_err == 0 && info.type == BT_CONN_TYPE_LE) {
			printk("FUSION_CI_CURRENT name=%s interval_units=%u interval_us=%u latency=%u timeout_units=%u\n",
			       peer->name,
			       info.le.interval,
			       BT_CONN_INTERVAL_TO_US(info.le.interval),
			       info.le.latency, info.le.timeout);
		} else {
			printk("FUSION_FAIL name=%s step=conn_info err=%d type=%d\n",
			       peer->name, info_err,
			       info_err == 0 ? (int)info.type : -1);
		}
		static const struct bt_le_conn_param bench_params = {
			.interval_min = 40, /* 50 ms */
			.interval_max = 40, /* 50 ms */
			.latency = 0,
			.timeout = 400, /* 4 s */
		};
		err = bt_conn_le_param_update(conn, &bench_params);
		printk("FUSION_CI_REQUEST name=%s interval_units=40 interval_us=50000 latency=0 timeout_units=400 err=%d source=master_post_gatt\n",
		       peer->name, err);
		memset(params, 0, sizeof(*params));
		/*
		 * Serialize connect + MTU/GATT discovery.  Starting the next
		 * connection before this point caused concurrent CCC discovery
		 * procedures to return not-found on four of five real peers.
		 */
		start_scan();
		break;
	}
	}

	return BT_GATT_ITER_STOP;
}

static void start_fusion_discovery(struct fusion_peer *peer)
{
	int err;

	memset(&peer->discover_params, 0, sizeof(peer->discover_params));
	peer->discovery_stage = DISCOVERY_SERVICE;
	peer->discover_params.uuid = &fusion_service_uuid.uuid;
	peer->discover_params.func = discover_fusion;
	peer->discover_params.start_handle = BT_ATT_FIRST_ATTRIBUTE_HANDLE;
	peer->discover_params.end_handle = BT_ATT_LAST_ATTRIBUTE_HANDLE;
	peer->discover_params.type = BT_GATT_DISCOVER_PRIMARY;

	err = bt_gatt_discover(peer->conn, &peer->discover_params);
	if (err != 0) {
		printk("FUSION_FAIL name=%s step=discover_service_start err=%d\n",
		       peer->name, err);
	}
}

static void mtu_exchanged(struct bt_conn *conn, uint8_t err,
			  struct bt_gatt_exchange_params *params)
{
	struct fusion_peer *peer = peer_by_conn(conn);

	if (peer == NULL || params != &peer->exchange_params) {
		printk("FUSION_FAIL name=- step=mtu_unknown_peer\n");
		return;
	}
	printk("FUSION_ATT_MTU name=%s value=%u err=%u\n",
	       peer->name, bt_gatt_get_mtu(conn), err);
	start_fusion_discovery(peer);
}

static void connected(struct bt_conn *conn, uint8_t conn_err)
{
	static const struct bt_conn_le_phy_param phy_params = {
		.options = BT_CONN_LE_PHY_OPT_NONE,
		.pref_tx_phy = BT_GAP_LE_PHY_2M,
		.pref_rx_phy = BT_GAP_LE_PHY_2M,
	};
	struct fusion_peer *peer = peer_by_conn(conn);
	char addr[BT_ADDR_LE_STR_LEN];
	int err;

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	if (peer == NULL) {
		printk("FUSION_FAIL name=- step=connect_untracked addr=%s hci=0x%02x\n",
		       addr, conn_err);
		return;
	}
	if (conn_err != 0) {
		printk("FUSION_FAIL name=%s step=connect_complete addr=%s hci=0x%02x\n",
		       peer->name, addr, conn_err);
		connecting = false;
		if (connecting_peer == peer) {
			connecting_peer = NULL;
		}
		release_peer(peer);
		if (spacing_transition) {
			k_work_reschedule(&spacing_transition_work, K_NO_WAIT);
		} else {
			start_scan();
		}
		return;
	}

	connecting = false;
	if (connecting_peer == peer) {
		connecting_peer = NULL;
	}
	reset_node_time_extension(peer);
	++reconnections;
	++peer->reconnections;
	err = bt_hci_get_conn_handle(conn, &peer->hci_handle);
	if (err == 0) {
		k_spinlock_key_t key = k_spin_lock(&qos_lock);

		peer->hci_handle_valid = true;
		peer->qos.window_start_ms = (uint32_t)k_uptime_get();
		k_spin_unlock(&qos_lock, key);
	} else {
		printk("FUSION_FAIL name=%s step=hci_handle err=%d\n",
		       peer->name, err);
	}
	printk("FUSION_CONNECTED name=%s addr=%s node_connection=%u connections_total=%u active=%u\n",
	       peer->name, addr, peer->reconnections, reconnections,
	       (unsigned int)peer_count_allocated());
	reconnect_probe_note_connected(peer->name);

	if (spacing_transition) {
		err = bt_conn_disconnect(conn,
					BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		printk("FUSION_SPACING_LATE_CONNECT name=%s action=disconnect err=%d\n",
		       peer->name, err);
		k_work_reschedule(&spacing_transition_work, K_MSEC(50));
		return;
	}

	err = bt_conn_le_phy_update(conn, &phy_params);
	printk("FUSION_PHY_REQUEST name=%s preferred=2M err=%d\n",
	       peer->name, err);

	err = bt_conn_le_data_len_update(conn, BT_LE_DATA_LEN_PARAM_MAX);
	printk("FUSION_DLE_REQUEST name=%s max=251 err=%d\n",
	       peer->name, err);

	peer->exchange_params.func = mtu_exchanged;
	err = bt_gatt_exchange_mtu(conn, &peer->exchange_params);
	if (err != 0) {
		printk("FUSION_ATT_MTU_REQUEST name=%s err=%d\n",
		       peer->name, err);
		start_fusion_discovery(peer);
	}
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	struct fusion_peer *peer = peer_by_conn(conn);
	char addr[BT_ADDR_LE_STR_LEN];

	if (peer == NULL) {
		return;
	}

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("FUSION_DISCONNECTED name=%s addr=%s reason=0x%02x packets=%u malformed=%u\n",
	       peer->name, addr, reason, peer->received_packets,
	       peer->malformed_packets);
	led_note_fault(FUSION_LED_FAULT_DISCONNECT, 1u);
	/* Timestamp before release_peer() clears the slot we read the name from. */
	reconnect_probe_note_disconnect(peer->name);
	if (connecting_peer == peer) {
		connecting = false;
		connecting_peer = NULL;
	}
	release_peer(peer);
	if (spacing_transition) {
		k_work_reschedule(&spacing_transition_work, K_NO_WAIT);
	} else {
		start_scan();
	}
}

static void le_phy_updated(struct bt_conn *conn,
			   struct bt_conn_le_phy_info *param)
{
	struct fusion_peer *peer = peer_by_conn(conn);

	if (peer != NULL) {
		peer->tx_phy = param->tx_phy;
		peer->rx_phy = param->rx_phy;
		peer->phy_readback_valid = true;
	}
	printk("FUSION_PHY_UPDATED name=%s tx=%u rx=%u contract=%s\n",
	       peer != NULL ? peer->name : "-", param->tx_phy, param->rx_phy,
	       peer != NULL ? peer_link_contract(peer) : "UNKNOWN");
}

static void le_data_len_updated(struct bt_conn *conn,
				struct bt_conn_le_data_len_info *info)
{
	struct fusion_peer *peer = peer_by_conn(conn);

	if (peer != NULL) {
		peer->dle_tx_len = info->tx_max_len;
		peer->dle_tx_time = info->tx_max_time;
		peer->dle_rx_len = info->rx_max_len;
		peer->dle_rx_time = info->rx_max_time;
		peer->dle_readback_valid = true;
	}
	printk("FUSION_DLE_UPDATED name=%s tx_len=%u tx_time=%u rx_len=%u rx_time=%u contract=%s\n",
	       peer != NULL ? peer->name : "-",
	       info->tx_max_len, info->tx_max_time,
	       info->rx_max_len, info->rx_max_time,
	       peer != NULL ? peer_link_contract(peer) : "UNKNOWN");
}

static bool le_param_req(struct bt_conn *conn, struct bt_le_conn_param *param)
{
	struct fusion_peer *peer = peer_by_conn(conn);
	uint16_t requested_timeout = param->timeout;

	/*
	 * The deployed B306 mcumgr peripheral preference requests a 420 ms
	 * supervision timeout after the central's own update.  That is too
	 * fragile for five concurrent 50 ms links.  Keep the peer's interval
	 * request, but enforce the four-second bench supervision policy.
	 */
	if (param->timeout < 200u) {
		param->timeout = 400u;
	}
	printk("FUSION_CI_PEER_REQUEST name=%s interval_min=%u interval_max=%u latency=%u requested_timeout_units=%u accepted_timeout_units=%u\n",
	       peer != NULL ? peer->name : "-", param->interval_min,
	       param->interval_max, param->latency, requested_timeout,
	       param->timeout);

	return true;
}

static void le_param_updated(struct bt_conn *conn, uint16_t interval,
			     uint16_t latency, uint16_t timeout)
{
	struct fusion_peer *peer = peer_by_conn(conn);

	if (peer != NULL) {
		peer->interval = interval;
		peer->latency = latency;
		peer->timeout = timeout;
	}
	printk("FUSION_CI_UPDATED name=%s interval_units=%u interval_us=%u latency=%u timeout_units=%u\n",
	       peer != NULL ? peer->name : "-", interval,
	       (uint32_t)interval * 1250u, latency, timeout);
}

BT_CONN_CB_DEFINE(connection_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
	.le_phy_updated = le_phy_updated,
	.le_data_len_updated = le_data_len_updated,
	.le_param_req = le_param_req,
	.le_param_updated = le_param_updated,
};

static bool parse_led_expected(const char *line, uint8_t *value)
{
	const char prefix[] = "LEDEXPECT ";
	uint32_t parsed = 0u;
	size_t offset = sizeof(prefix) - 1u;

	if (strncmp(line, prefix, offset) != 0 || line[offset] == '\0') {
		return false;
	}
	for (; line[offset] != '\0'; ++offset) {
		if (line[offset] < '0' || line[offset] > '9') {
			return false;
		}
		parsed = parsed * 10u + (uint32_t)(line[offset] - '0');
		if (parsed > LED_EXPECTED_MAX) {
			return false;
		}
	}
	if (parsed < LED_EXPECTED_MIN || parsed > LED_EXPECTED_MAX) {
		return false;
	}
	*value = (uint8_t)parsed;
	return true;
}

static void led_status_print(void)
{
	struct fusion_led_fault_latch faults = led_fault_snapshot();

	printk("LEDSTAT expect=%u ready=%u latch=%u mask=0x%02x crc=%u seq=%u queue=%u io=%u disc=%u uwb=%u imu=%u\n",
	       (uint32_t)atomic_get(&led_expected_count),
	       (unsigned int)peer_count_ready(),
	       faults.mask != 0u, faults.mask,
	       faults.count[FUSION_LED_FAULT_CRC_HEADER],
	       faults.count[FUSION_LED_FAULT_SEQUENCE],
	       faults.count[FUSION_LED_FAULT_QUEUE],
	       faults.count[FUSION_LED_FAULT_NOTIFY_UART],
	       faults.count[FUSION_LED_FAULT_DISCONNECT],
	       (uint32_t)atomic_get(&led_uwb_records),
	       (uint32_t)atomic_get(&led_imu_records));
}

static uint8_t stall_read_cb(struct bt_conn *conn, uint8_t att_err,
			     struct bt_gatt_read_params *params,
			     const void *data, uint16_t length)
{
	struct fusion_peer *peer = CONTAINER_OF(
		params, struct fusion_peer, stall_read_params);
	uint32_t generation = peer->stall_read_generation;
	uint32_t elapsed_ms = (uint32_t)k_uptime_get() -
		peer->stall_read_started_ms;

	ARG_UNUSED(conn);
	if (!peer->stall_read_active) {
		printk("FUSION_STALL_READ_LATE name=%s generation=%u ignored=1\n",
		       peer->name, generation);
		return BT_GATT_ITER_STOP;
	}
	if (att_err != 0u) {
		printk("FUSION_STALL_READ name=%s att_err=%u len=0\n",
		       peer->name, att_err);
	} else if (data == NULL || length != sizeof(bsf_stall_status_t)) {
		printk("FUSION_STALL_READ name=%s att_err=0 len=%u expected=%u parse=fail\n",
		       peer->name, length,
		       (unsigned int)sizeof(bsf_stall_status_t));
	} else if (((const uint8_t *)data)[0] >= BSF_STALL_RING_VERSION_V41) {
		/*
		 * A v41 onset-ring page. It is deliberately the SAME 232 bytes as
		 * the status struct, so the length check above cannot separate
		 * them -- byte 0 does. Without this branch the page is decoded as
		 * a status struct and 64 of its bytes are never printed at all,
		 * because the pools loop is bounded by a pool_count that is
		 * really ring payload.
		 *
		 * The master deliberately does NOT parse the ring. It emits the
		 * raw bytes and lets tools/stall_ring_decode.py own the layout,
		 * so a later ring format needs no DK reflash. Chunked to 32 bytes
		 * per line because fusion_printf() emits one whole line per call.
		 *
		 * v35: the test is `>= BSF_STALL_RING_VERSION_V41`, not `==
		 * BSF_STALL_RING_VERSION`. The equality form defeated the very
		 * intent stated above -- it baked the CURRENT version number into
		 * the DK image, so H1's bump to v4 silently made dk-v34 route v42
		 * ring pages into the status branch, where 64 bytes are dropped
		 * and the pool loop runs off ring payload. Any page format at or
		 * past the v41 tag now reaches the hex dump, including the v43
		 * corpse pages, and no future format needs a DK reflash again.
		 */
		const uint8_t *raw = data;

		printk("FUSION_STALL_RING name=%s len=%u v=%u page=%u pages=%u entries=%u\n",
		       peer->name, length, raw[0], raw[1], raw[2], raw[3]);
		for (uint16_t off = 0u; off < length; off += 32u) {
			char hex[65];
			uint16_t n = MIN((uint16_t)32u, (uint16_t)(length - off));

			for (uint16_t i = 0u; i < n; ++i) {
				(void)snprintf(&hex[i * 2u], 3u, "%02x",
					       raw[off + i]);
			}
			hex[n * 2u] = '\0';
			printk("FUSION_STALL_RING_HEX name=%s off=%u n=%u %s\n",
			       peer->name, off, n, hex);
		}
	} else {
		const bsf_stall_status_t *s = data;

		printk("FUSION_STALL_READ name=%s att_err=0 len=%u v=%u reason=%u armed=%u sample_ms=%u e=%u x=%u entry_ms=%u exit_ms=%u age=%u stream=%u rc=%d rcc=%u/%u/%u/%u/%u q=%u/%u/%u qd=%u/%u/%u td=%u/%u/%u hb=%u alarm=%u@%u recovery=%u\n",
		       peer->name, length, s->version, s->reason, s->armed,
		       s->sample_uptime_ms, s->entry_count, s->exit_count,
		       s->entry_ms, s->exit_ms, s->in_call_age_ms,
		       s->in_call_stream, s->last_return_code,
		       s->return_ok, s->return_nomem, s->return_notconn,
		       s->return_again, s->return_other,
		       s->queue_depth_ctl, s->queue_depth_uwb,
		       s->queue_depth_imu, s->q_drop_ctl, s->q_drop_uwb,
		       s->q_drop_imu, s->timeout_drop_ctl,
		       s->timeout_drop_uwb, s->timeout_drop_imu,
		       s->producer_heartbeat, s->alarm_count,
		       s->alarm_timestamp_ms, s->recovery_count);
		printk("FUSION_STALL_POOLS name=%s count=%u usage=%u sent_cb=%u",
		       peer->name, s->pool_count, s->pool_usage_enabled,
		       s->att_sent_cb_after_tx);
		for (uint8_t i = 0u; i < s->pool_count &&
		     i < ARRAY_SIZE(s->pools); ++i) {
			printk(" pool%u=%08x:%u/%u", i,
			       s->pools[i].name_hash, s->pools[i].available,
			       s->pools[i].low_water);
		}
		printk("\n");
	}
	peer->stall_read_active = false;
	(void)k_work_cancel_delayable(&peer->stall_read_timeout_work);
	memset(params, 0, sizeof(*params));
	printk("FUSION_STALL_READ_DONE name=%s generation=%u elapsed_ms=%u terminal=callback\n",
	       peer->name, generation, elapsed_ms);
	return BT_GATT_ITER_STOP;
}

static void stall_read_abort(struct fusion_peer *peer, const char *reason)
{
	uint32_t generation;

	if (!peer->stall_read_active) {
		return;
	}
	generation = peer->stall_read_generation;
	peer->stall_read_active = false;
	(void)k_work_cancel_delayable(&peer->stall_read_timeout_work);
	if (peer->conn != NULL && peer->stall_read_params.func != NULL) {
		(void)bt_gatt_cancel(peer->conn, &peer->stall_read_params);
	}
	memset(&peer->stall_read_params, 0,
	       sizeof(peer->stall_read_params));
	printk("FUSION_STALL_READ_DONE name=%s generation=%u elapsed_ms=%u terminal=%s\n",
	       peer->name, generation,
	       (uint32_t)k_uptime_get() - peer->stall_read_started_ms,
	       reason);
}

/*
 * Forced disconnect/reconnect probe.
 *
 * State lives outside struct fusion_peer because release_peer() clears the peer
 * slot on disconnect, which is precisely the interval being measured. Exactly
 * one probe may be in flight, which is also what guarantees the operation can
 * never become fleet-wide: a second request while one is active is rejected
 * rather than queued.
 *
 * Every terminal path -- disconnect seen, bridge re-established, timeout,
 * rejection -- funnels through reconnect_probe_finish(), the single idempotent
 * cleanup, matching the STALL READ lifecycle discipline.
 */
struct reconnect_probe {
	char name[TARGET_NAME_LEN + 1];
	bool active;
	bool saw_disconnect;
	uint32_t generation;
	uint32_t requested_ms;
	uint32_t disconnected_ms;
	uint32_t connected_ms;
	uint32_t bridge_ready_ms;
	struct k_work_delayable timeout_work;
};

static struct reconnect_probe reconnect_probe;

static void reconnect_probe_finish(const char *outcome)
{
	uint32_t now;

	if (!reconnect_probe.active) {
		return;
	}
	now = (uint32_t)k_uptime_get();
	reconnect_probe.active = false;
	(void)k_work_cancel_delayable(&reconnect_probe.timeout_work);

	printk("FUSION_RECONNECT_DONE name=%s generation=%u outcome=%s "
	       "disconnect_ms=%u connect_ms=%u bridge_ms=%u "
	       "down_interval_ms=%d bridge_interval_ms=%d total_ms=%u\n",
	       reconnect_probe.name, reconnect_probe.generation, outcome,
	       reconnect_probe.disconnected_ms, reconnect_probe.connected_ms,
	       reconnect_probe.bridge_ready_ms,
	       reconnect_probe.saw_disconnect && reconnect_probe.connected_ms != 0u
		       ? (int)(reconnect_probe.connected_ms -
			       reconnect_probe.disconnected_ms)
		       : -1,
	       reconnect_probe.saw_disconnect &&
			       reconnect_probe.bridge_ready_ms != 0u
		       ? (int)(reconnect_probe.bridge_ready_ms -
			       reconnect_probe.disconnected_ms)
		       : -1,
	       now - reconnect_probe.requested_ms);
	memset(reconnect_probe.name, 0, sizeof(reconnect_probe.name));
}

static void reconnect_probe_timeout_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	reconnect_probe_finish("timeout");
}

static void reconnect_probe_note_disconnect(const char *name)
{
	if (!reconnect_probe.active || reconnect_probe.saw_disconnect) {
		return;
	}
	if (strcmp(reconnect_probe.name, name) != 0) {
		return;
	}
	reconnect_probe.saw_disconnect = true;
	reconnect_probe.disconnected_ms = (uint32_t)k_uptime_get();
	printk("FUSION_RECONNECT_DISCONNECTED name=%s generation=%u at_ms=%u\n",
	       name, reconnect_probe.generation,
	       reconnect_probe.disconnected_ms);
}

static void reconnect_probe_note_connected(const char *name)
{
	if (!reconnect_probe.active || !reconnect_probe.saw_disconnect) {
		return;
	}
	if (strcmp(reconnect_probe.name, name) != 0 ||
	    reconnect_probe.connected_ms != 0u) {
		return;
	}
	reconnect_probe.connected_ms = (uint32_t)k_uptime_get();
	printk("FUSION_RECONNECT_CONNECTED name=%s generation=%u at_ms=%u "
	       "down_interval_ms=%u\n",
	       name, reconnect_probe.generation, reconnect_probe.connected_ms,
	       reconnect_probe.connected_ms - reconnect_probe.disconnected_ms);
}

static int start_stall_read(struct fusion_peer *peer);

static void reconnect_probe_note_bridge_ready(struct fusion_peer *peer)
{
	int err;

	if (!reconnect_probe.active || !reconnect_probe.saw_disconnect) {
		return;
	}
	if (strcmp(reconnect_probe.name, peer->name) != 0) {
		return;
	}
	reconnect_probe.bridge_ready_ms = (uint32_t)k_uptime_get();
	/*
	 * Read the status characteristic on the fresh bearer. This is the
	 * measurement the probe exists for: whether a reconnect alone restores
	 * the export path, without the reboot the firmware's own recovery uses.
	 */
	err = start_stall_read(peer);
	printk("FUSION_RECONNECT_VERIFY name=%s generation=%u bridge_ms=%u "
	       "bridge_interval_ms=%u stall_read_err=%d\n",
	       peer->name, reconnect_probe.generation,
	       reconnect_probe.bridge_ready_ms,
	       reconnect_probe.bridge_ready_ms - reconnect_probe.disconnected_ms,
	       err);
	reconnect_probe_finish(err == 0 ? "reconnected" : "reconnected_read_error");
}

static void stall_read_timeout_handler(struct k_work *work)
{
	struct k_work_delayable *delayable = k_work_delayable_from_work(work);
	struct fusion_peer *peer = CONTAINER_OF(
		delayable, struct fusion_peer, stall_read_timeout_work);

	/*
	 * Fires at 25 s, i.e. 5 s before the stack's ATT transaction timeout.
	 * The abort resolves our own state cleanly, but the bearer is still on
	 * course to be torn down at 30 s -- say so explicitly rather than let a
	 * clean-looking terminal reason imply the peer is unharmed.
	 */
	stall_read_abort(peer, "timeout");
	printk("FUSION_STALL_READ_BEARER_WARNING name=%s att_timeout_in_ms=%u "
	       "note=cancel_does_not_stop_att_timer\n",
	       peer->name, ATT_TRANSACTION_TIMEOUT_MS - STALL_READ_TIMEOUT_MS);
}

static int start_stall_read(struct fusion_peer *peer)
{
	int err;

	if (peer->conn == NULL || peer->stall_value_handle == 0u) {
		return -ENOTCONN;
	}
	if (peer->stall_read_active) {
		return -EBUSY;
	}
	++peer->stall_read_generation;
	peer->stall_read_started_ms = (uint32_t)k_uptime_get();
	peer->stall_read_active = true;
	peer->stall_read_params.func = stall_read_cb;
	peer->stall_read_params.handle_count = 1u;
	peer->stall_read_params.single.handle = peer->stall_value_handle;
	peer->stall_read_params.single.offset = 0u;
	err = bt_gatt_read(peer->conn, &peer->stall_read_params);
	if (err != 0) {
		stall_read_abort(peer, "submit_error");
		return err;
	}
	k_work_reschedule(&peer->stall_read_timeout_work,
			  K_MSEC(STALL_READ_TIMEOUT_MS));
	printk("FUSION_STALL_READ_START name=%s generation=%u timeout_ms=%u\n",
	       peer->name, peer->stall_read_generation,
	       STALL_READ_TIMEOUT_MS);
	return 0;
}

static void handle_console_command(char *line)
{
	struct fusion_peer *peer;
	size_t length = strlen(line);
	int err;

	if (strncmp(line, "LEDEXPECT", strlen("LEDEXPECT")) == 0) {
		uint8_t expected;

		if (!parse_led_expected(line, &expected)) {
			printk("LEDEXPECT_REJECT reason=range_or_syntax expected=LEDEXPECT_1_to_10\n");
			return;
		}
		atomic_set(&led_expected_count, expected);
		printk("LEDEXPECT value=%u ready=%u volatile=1\n",
		       expected, (unsigned int)peer_count_ready());
		return;
	}
	if (strcmp(line, "LEDSTAT") == 0) {
		led_status_print();
		return;
	}
	if (strcmp(line, "LEDCLEAR") == 0) {
		struct fusion_led_fault_latch previous =
			led_fault_snapshot();

		led_clear_faults();
		printk("LEDCLEAR ok=1 previous_mask=0x%02x\n",
		       previous.mask);
		return;
	}
	if (strcmp(line, "LIST") == 0) {
		printk("FUSION_LIST count=%u ready=%u scanning=%u capacity=%u spacing=%s spacing_us=%u spacing_generation=%u spacing_transition=%u spacing_failed=%u qos=enabled qos_unknown=%u\n",
		       (unsigned int)peer_count_allocated(),
		       (unsigned int)peer_count_ready(),
		       !spacing_transition && !spacing_failed && !connecting &&
			       peer_count_allocated() < MAX_FUSION_PEERS,
		       MAX_FUSION_PEERS, spacing_mode_name(spacing_active_mode),
		       spacing_active_us, spacing_generation,
		       spacing_transition, spacing_failed,
		       (uint32_t)atomic_get(&qos_unknown_handle_events));
		for (size_t i = 0; i < ARRAY_SIZE(peers); ++i) {
			uint32_t qos_reports;
			uint32_t qos_gaps;
			k_spinlock_key_t key;

			peer = &peers[i];
			if (!peer->allocated) {
				continue;
			}
			key = k_spin_lock(&qos_lock);
			qos_reports = peer->qos.report_count;
			qos_gaps = peer->qos.event_counter_gap_count;
			k_spin_unlock(&qos_lock, key);
			printk("FUSION_PEER index=%u name=%s rssi=%d connected=1 subscribed=%u control=%u interval_units=%u interval_us=%u latency=%u timeout_units=%u phy_tx=%u phy_rx=%u phy_readback=%u dle_tx_len=%u dle_tx_time=%u dle_rx_len=%u dle_rx_time=%u dle_readback=%u link_contract=%s hci_handle=%u qos_reports_window=%u qos_event_gaps_window=%u imu_epoch_defer_drop=%u packets=%u malformed=%u logger_drop=%u\n",
			       (unsigned int)i, peer->name, peer->rssi,
			       peer->bridge_ready, peer->control_value_handle,
			       peer->interval,
			       BT_CONN_INTERVAL_TO_US(peer->interval),
			       peer->latency, peer->timeout, peer->tx_phy,
			       peer->rx_phy,
			       peer->phy_readback_valid ? 1u : 0u,
			       peer->dle_tx_len, peer->dle_tx_time,
			       peer->dle_rx_len, peer->dle_rx_time,
			       peer->dle_readback_valid ? 1u : 0u,
			       peer_link_contract(peer),
			       peer->hci_handle_valid ? peer->hci_handle :
						       UINT16_MAX,
			       qos_reports, qos_gaps,
			       peer->imu_epoch_defer_drop,
			       peer->received_packets,
			       peer->malformed_packets, peer->logger_dropped);
		}
		return;
	}
	if (strcmp(line, "SPACING STATUS") == 0) {
		spacing_status_print(spacing_transition ? "TRANSITION" :
				     spacing_failed ? "FAILED" : "APPLIED");
		return;
	}
	if (strcmp(line, "MASTER STATUS") == 0) {
		printk("FUSION_MASTER_STATUS marker=%s count=%u ready=%u spacing=%s spacing_us=%u spacing_generation=%u qos=enabled qos_unknown=%u malformed=%u logger_drop=%u cdc_drop_bytes=%u cdc_drop_records=%u\n",
		       FUSION_MASTER_MARKER,
		       (unsigned int)peer_count_allocated(),
		       (unsigned int)peer_count_ready(),
		       spacing_mode_name(spacing_active_mode),
		       spacing_active_us, spacing_generation,
		       (uint32_t)atomic_get(&qos_unknown_handle_events),
		       malformed_packets, logger_dropped, cdc_dropped_bytes,
		       cdc_dropped_records);
		return;
	}
	if (strcmp(line, "SPACING OFF") == 0) {
		spacing_request(SPACING_MODE_OFF);
		return;
	}
	if (strcmp(line, "SPACING ON") == 0) {
		spacing_request(SPACING_MODE_ON);
		return;
	}
	if (strcmp(line, "RESOURCES") == 0) {
		log_resource_snapshot("command");
		return;
	}
	if (strcmp(line, "OUTPUT BINARY") == 0) {
		atomic_set(&host_binary_output, 1);
		printk("FUSION_OUTPUT mode=binary version=%u framing=COBS_CRC16\n",
		       BSF_HOST_FRAME_VERSION);
		return;
	}
	if (strcmp(line, "OUTPUT TEXT") == 0) {
		atomic_set(&host_binary_output, 0);
		printk("FUSION_OUTPUT mode=text version=%u diagnostic=1\n",
		       BSF_HOST_FRAME_VERSION);
		return;
	}
	if (length < 9u ||
	    strncmp(line, TARGET_NAME_PREFIX,
		    strlen(TARGET_NAME_PREFIX)) != 0) {
		printk("FUSION_COMMAND_REJECT reason=syntax expected=LIST_RESOURCES_OUTPUT_SPACING_MASTER_LED_or_BSFxxxx\n");
		return;
	}
	if (line[TARGET_NAME_LEN] != ' ') {
		printk("FUSION_COMMAND_REJECT reason=syntax expected=BSFxxxx_space_command\n");
		return;
	}
	peer = peer_by_name(line);
	if (peer == NULL) {
		printk("FUSION_COMMAND_REJECT reason=not_connected target=%.7s\n",
		       line);
		return;
	}
	if (!peer->bridge_ready || peer->conn == NULL ||
	    peer->control_value_handle == 0u) {
		printk("FUSION_COMMAND_REJECT reason=bridge_not_ready line=%s\n",
		       line);
		return;
	}
	if (strcmp(&line[TARGET_NAME_LEN + 1u], "STALL READ") == 0) {
		err = start_stall_read(peer);
		printk("FUSION_STALL_READ_START name=%s handle=%u err=%d\n",
		       peer->name, peer->stall_value_handle, err);
		return;
	}
	if (strcmp(&line[TARGET_NAME_LEN + 1u], "STALL CANCEL") == 0) {
		stall_read_abort(peer, "cancel");
		return;
	}
	if (strcmp(&line[TARGET_NAME_LEN + 1u], "RECONNECT") == 0) {
		if (reconnect_probe.active) {
			printk("FUSION_RECONNECT_REJECT reason=probe_active "
			       "active_target=%s requested=%s\n",
			       reconnect_probe.name, peer->name);
			return;
		}
		memset(&reconnect_probe, 0, sizeof(reconnect_probe));
		k_work_init_delayable(&reconnect_probe.timeout_work,
				      reconnect_probe_timeout_handler);
		strncpy(reconnect_probe.name, peer->name,
			sizeof(reconnect_probe.name) - 1u);
		reconnect_probe.active = true;
		++reconnect_probe.generation;
		reconnect_probe.requested_ms = (uint32_t)k_uptime_get();
		/*
		 * Only this peer's connection is touched. Every other link, and
		 * the scan/spacing machinery, is left exactly as it was.
		 */
		err = bt_conn_disconnect(peer->conn,
					 BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		printk("FUSION_RECONNECT_START name=%s generation=%u at_ms=%u "
		       "timeout_ms=%u err=%d\n",
		       peer->name, reconnect_probe.generation,
		       reconnect_probe.requested_ms,
		       RECONNECT_PROBE_TIMEOUT_MS, err);
		if (err != 0) {
			reconnect_probe_finish("disconnect_error");
			return;
		}
		k_work_reschedule(&reconnect_probe.timeout_work,
				  K_MSEC(RECONNECT_PROBE_TIMEOUT_MS));
		return;
	}
	err = bt_gatt_write_without_response(peer->conn,
					     peer->control_value_handle,
					     line, (uint16_t)length,
					     false);
	printk("FUSION_COMMAND_TX target=%s len=%u err=%d line=%s\n",
	       peer->name, (unsigned int)length, err, line);
}

struct command_line_state {
	char line[CDC_COMMAND_MAX + 1u];
	size_t used;
};

static void consume_console_bytes(struct command_line_state *state,
				  const uint8_t *bytes, uint32_t count)
{
	for (uint32_t i = 0; i < count; ++i) {
		char ch = (char)bytes[i];

		if (ch == '\r' || ch == '\n') {
			if (state->used != 0u) {
				state->line[state->used] = '\0';
				k_mutex_lock(&command_dispatch_lock, K_FOREVER);
				handle_console_command(state->line);
				k_mutex_unlock(&command_dispatch_lock);
				state->used = 0u;
			}
		} else if (state->used < CDC_COMMAND_MAX) {
			state->line[state->used++] = ch;
		} else {
			state->used = 0u;
			printk("FUSION_COMMAND_REJECT reason=line_too_long max=%u\n",
			       CDC_COMMAND_MAX);
		}
	}
}

static void cdc_command_thread(void *first, void *second, void *third)
{
	struct command_line_state state = {0};

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
			consume_console_bytes(&state, chunk, count);
		} while (count != 0u);
	}
}

K_THREAD_DEFINE(cdc_command_thread_id, 3072, cdc_command_thread,
		NULL, NULL, NULL, 7, 0, 0);

static void rtt_command_thread(void *first, void *second, void *third)
{
	struct command_line_state state = {0};

	ARG_UNUSED(first);
	ARG_UNUSED(second);
	ARG_UNUSED(third);

	while (true) {
		uint8_t chunk[64];
		unsigned int count = SEGGER_RTT_Read(0, chunk, sizeof(chunk));

		if (count != 0u) {
			consume_console_bytes(&state, chunk, count);
		} else {
			k_sleep(K_MSEC(5));
		}
	}
}

K_THREAD_DEFINE(rtt_command_thread_id, 3072, rtt_command_thread,
		NULL, NULL, NULL, 7, 0, 0);

int main(void)
{
	sdc_hci_cmd_vs_qos_conn_event_report_enable_t qos_params = {
		.enable = 1u,
	};
	int err;

	err = led_gpio_init();
	if (err != 0) {
		printk("FUSION_FAIL step=led_gpio_init err=%d\n", err);
		return 0;
	}
	k_work_reschedule(&led_work, K_NO_WAIT);
	err = cdc_start();
	if (err != 0) {
		printk("FUSION_FAIL step=cdc_start err=%d\n", err);
		return 0;
	}
	printk("FUSION_MASTER marker=%s probe=683234364 pc=USB_CDC rtt=control+log max_conn=%u ccc_mode=%s\n",
	       FUSION_MASTER_MARKER, MAX_FUSION_PEERS,
	       IS_ENABLED(CONFIG_BSF_CCC_FILTERED_REPRO) ?
		       "filtered_repro" : "enumerated");
	printk("FUSION_LED_PANEL expected=%u uwb_toggle_records=%u imu_toggle_records=%u render_ms=%u heartbeat_timeout_ms=%u\n",
	       LED_EXPECTED_DEFAULT, LED_UWB_TOGGLE_RECORDS,
	       LED_IMU_TOGGLE_RECORDS, LED_RENDER_PERIOD_MS,
	       LED_HEARTBEAT_TIMEOUT_MS);
	err = bt_enable(NULL);
	if (err != 0) {
		printk("FUSION_FAIL step=bt_enable err=%d\n", err);
		return 0;
	}

	err = bt_hci_register_vnd_evt_cb(qos_vendor_event);
	if (err != 0) {
		printk("FUSION_FAIL step=qos_event_callback err=%d\n", err);
		return 0;
	}
	err = hci_vs_sdc_qos_conn_event_report_enable(&qos_params);
	if (err != 0) {
		printk("FUSION_FAIL step=qos_enable err=%d\n", err);
		return 0;
	}
	err = spacing_apply(SPACING_MODE_OFF);
	if (err != 0) {
		spacing_failed = true;
		printk("FUSION_FAIL step=spacing_boot_off err=%d action=scan_blocked\n",
		       err);
		return 0;
	}
	printk("FUSION_MASTER_BLUETOOTH_READY qos=enabled\n");
	spacing_status_print("APPLIED");
	k_work_reschedule(&qos_work, K_MSEC(QOS_WINDOW_MS));
	start_scan();

	while (true) {
		k_sleep(K_SECONDS(10));
		if (peer_count_allocated() < MAX_FUSION_PEERS &&
		    !connecting) {
			start_scan();
		}
		if (peer_count_allocated() == 0u && !connecting) {
			printk("FUSION_SCAN_WAITING connected=0 capacity=%u\n",
			       MAX_FUSION_PEERS);
		} else {
			printk("FUSION_HEALTH packets=%u malformed=%u logger_drop=%u cdc_drop_bytes=%u cdc_drop_records=%u logq_high_water=%u cdc_high_water=%u connections_total=%u active=%u ready=%u capacity=%u spacing=%s spacing_us=%u spacing_generation=%u spacing_transition=%u qos_unknown=%u\n",
			       received_packets, malformed_packets, logger_dropped,
			       cdc_dropped_bytes,
			       cdc_dropped_records,
			       (unsigned int)atomic_get(
				       &fusion_log_queue_high_water),
			       (unsigned int)atomic_get(
				       &cdc_tx_ring_high_water),
			       reconnections,
			       (unsigned int)peer_count_allocated(),
			       (unsigned int)peer_count_ready(),
			       MAX_FUSION_PEERS,
			       spacing_mode_name(spacing_active_mode),
			       spacing_active_us, spacing_generation,
			       spacing_transition,
			       (uint32_t)atomic_get(
				       &qos_unknown_handle_events));
		}
	}

	return 0;
}
