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
#include "boot_confirm_policy.h"
#include "imu.h"
#include "imu_pull_diag_math.h"
#include "led_fault_window.h"
#include "publisher_priority.h"
#include "strobe_capture.h"

LOG_MODULE_REGISTER(biospur_fusion, LOG_LEVEL_INF);

#define LED0_NODE DT_ALIAS(led0)
#define LED1_NODE DT_ALIAS(led1)
#define UWB_UART_NODE DT_ALIAS(uwb_uart)

#ifndef BSF_FW_MARKER
#define BSF_FW_MARKER "b306-imu-relay-v32"
#endif
#ifndef BSF_BOOT_CONFIRM_ENABLED
#define BSF_BOOT_CONFIRM_ENABLED 1
#endif
#define FW_MARKER BSF_FW_MARKER

#define UART_DMA_BUFFER_SIZE 256u
#define UART_RING_SIZE 2048u
#define UART_RING_ITEM_RX 1u
#define UART_RING_TIMESTAMP_WORDS 2u
#define UART_RING_DATA_WORDS DIV_ROUND_UP(UART_DMA_BUFFER_SIZE, sizeof(uint32_t))
#define UART_RING_ITEM_WORDS (UART_RING_TIMESTAMP_WORDS + UART_RING_DATA_WORDS)
#define UART_RX_TIMEOUT_US 2000
#define PARSER_STACK_SIZE 2048
/*
 * IMU priority 4 intentionally remains above parser priority 5. Frame arrival
 * time is now captured in the UART callback, so parser latency cannot corrupt
 * pairing correctness and the proven IMU cadence is not perturbed.
 */
#define PARSER_PRIORITY 5
#define CONTROL_STACK_SIZE 3072
#define CONTROL_PRIORITY 5
#define CONTROL_QUEUE_DEPTH 4
#define PUBLISHER_STACK_SIZE 2048
#define PUBLISHER_PRIORITY 10
#define PUBLISH_CTL_DEPTH 4
#define PUBLISH_UWB_DEPTH 16
#define PUBLISH_IMU_DEPTH 64
#define PUBLISH_HIST_BINS_PER_PAGE 7u
#define ENQUEUE_HIST_BINS 11u
#define RELAY_ACK_TIMEOUT_MS 2000
#define RELAY_PENDING_MAX CONTROL_QUEUE_DEPTH
#define WATCHDOG_TIMEOUT_MS 30000u
#define BOOT_CONFIRM_TIMEOUT_MS 180000u
#define BOOT_CONFIRM_GUARD_MS 5000u
#define LED_RENDER_PERIOD_MS 50u
#define LED_UART_ALIVE_MS 1000u
#define LED_EVENT_PULSE_MS 20u

BUILD_ASSERT(DT_NODE_HAS_STATUS(UWB_UART_NODE, okay),
	     "B306 UWB UART must be enabled by the application overlay");
BUILD_ASSERT(DT_PROP(UWB_UART_NODE, current_speed) == BSL_BAUDRATE,
	     "B306 UWB UART baudrate must match biospur_link.h");

#if !DT_NODE_HAS_STATUS(LED0_NODE, okay)
#error "B306 firmware requires the board's led0 alias"
#endif
#if !DT_NODE_HAS_STATUS(LED1_NODE, okay)
#error "B306 firmware requires the board's led1 alias"
#endif

static const struct gpio_dt_spec data_led =
	GPIO_DT_SPEC_GET(LED0_NODE, gpios);
static const struct gpio_dt_spec link_led =
	GPIO_DT_SPEC_GET(LED1_NODE, gpios);
static const struct device *const uwb_uart = DEVICE_DT_GET(UWB_UART_NODE);
static const struct device *const watchdog = DEVICE_DT_GET(DT_NODELABEL(wdt0));
static int watchdog_channel = -1;
static atomic_t watchdog_feed_count;
static uint32_t boot_reset_reason;
static struct bsf_boot_confirm_policy boot_confirm_policy;
K_MUTEX_DEFINE(boot_confirm_lock);

/*
 * LED event inputs are counters/state already used by the software gates.
 * Timing-sensitive callbacks never touch GPIO. The existing priority-5 UART
 * parser worker renders these flags every 50 ms and is the sole GPIO writer
 * after initialization. This stays below the priority-4 IMU worker without
 * allocating another thread stack.
 */
static atomic_t ble_connected;
static atomic_t led_uart_seen;
static atomic_t led_last_uart_ms;
static atomic_t led_paired_events;
static atomic_t led_fault_clear_requested;
static struct bsf_led_fault_window led_uwb_fault_window;
static uint32_t led_rendered_paired_events;
static uint32_t led_data_pulse_until_ms;
static int8_t led_data_level = -1;
static int8_t led_link_level = -1;

static void led_note_uart_activity(void)
{
	atomic_set(&led_uart_seen, 1);
	atomic_set(&led_last_uart_ms, (atomic_val_t)(uint32_t)k_uptime_get());
}

static void led_note_uwb_fault(void)
{
	bsf_led_fault_window_observe(
		&led_uwb_fault_window, (uint32_t)k_uptime_get(), true);
}

static void led_note_paired_frame(void)
{
	bsf_led_fault_window_observe(
		&led_uwb_fault_window, (uint32_t)k_uptime_get(), false);
	atomic_inc(&led_paired_events);
}

static void led_set_if_changed(const struct gpio_dt_spec *led, bool on,
			       int8_t *rendered)
{
	int8_t requested = on ? 1 : 0;

	if (*rendered == requested) {
		return;
	}
	if (gpio_pin_set_dt(led, requested) == 0) {
		*rendered = requested;
	}
}

static void led_render(void)
{
	struct bsf_imu_stats imu_stats;
	uint32_t now_ms = (uint32_t)k_uptime_get();
	uint32_t paired_events = (uint32_t)atomic_get(&led_paired_events);
	uint32_t last_uart_ms = (uint32_t)atomic_get(&led_last_uart_ms);
	bool uart_recent = atomic_get(&led_uart_seen) != 0 &&
		(uint32_t)(now_ms - last_uart_ms) <= LED_UART_ALIVE_MS;
	bool recent_uwb_fault =
		bsf_led_fault_window_active(&led_uwb_fault_window, now_ms);
	bool data_on = false;
	bool link_on = false;

	if (paired_events != led_rendered_paired_events) {
		led_rendered_paired_events = paired_events;
		led_data_pulse_until_ms = now_ms + LED_EVENT_PULSE_MS;
	}

	if (uart_recent) {
		if (recent_uwb_fault) {
			/* Two flashes plus a long pause: unlike 10 Hz event flicker. */
			data_on = bsf_led_grouped_fault_on(now_ms);
		} else {
			data_on = (int32_t)(led_data_pulse_until_ms - now_ms) > 0;
		}
	}

	bsf_imu_get_stats(&imu_stats);
	if (atomic_get(&ble_connected) != 0) {
		if (imu_stats.health_latched != 0u) {
			/*
			 * Deliberately latched until COUNTERS CLEAR or reboot. A
			 * recovered sensor fault loses data and must be acknowledged;
			 * unlike startup pairing artifacts it is not a benign window
			 * baseline.
			 */
			link_on = bsf_led_paired_grouped_fault_on(now_ms);
		} else if (imu_stats.active != 0u) {
			link_on = true;
		} else {
			/* SLOW: 1 Hz, 500 ms on / 500 ms off. */
			link_on = ((now_ms / 500u) & 1u) == 0u;
		}
	}

	led_set_if_changed(&data_led, data_on, &led_data_level);
	led_set_if_changed(&link_led, link_on, &led_link_level);
}

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

enum publish_attribute {
	PUBLISH_ATTRIBUTE_DATA,
	PUBLISH_ATTRIBUTE_TELEMETRY,
};

struct publish_ctl_item {
	uint16_t len;
	uint8_t attribute;
	uint8_t payload[sizeof(bsf_ble_telemetry_t)];
};

struct publish_uwb_item {
	uint16_t len;
	uint8_t payload[sizeof(bsf_ble_uwb_packet_t)];
};

struct publish_imu_item {
	uint16_t len;
	uint8_t payload[BSF_IMU_RECORD_LEN(BSF_IMU_BATCH_MAX)];
};

BUILD_ASSERT(sizeof(bsf_ble_control_reply_prefix_t) +
		     BSF_CONTROL_REPLY_TEXT_MAX <=
	     sizeof(((struct publish_ctl_item *)0)->payload),
	     "control reply does not fit q_ctl");
BUILD_ASSERT(sizeof(bsf_ble_queue_counters_t) <=
	     sizeof(((struct publish_ctl_item *)0)->payload),
	     "queue counters do not fit q_ctl");

K_MSGQ_DEFINE(q_ctl, sizeof(struct publish_ctl_item),
	      PUBLISH_CTL_DEPTH, 4);
K_MSGQ_DEFINE(q_uwb, sizeof(struct publish_uwb_item),
	      PUBLISH_UWB_DEPTH, 4);
K_MSGQ_DEFINE(q_imu, sizeof(struct publish_imu_item),
	      PUBLISH_IMU_DEPTH, 4);
K_SEM_DEFINE(publisher_sem, 0, 1);

RING_BUF_ITEM_DECLARE(uart_ring, UART_RING_SIZE / sizeof(uint32_t));
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
static atomic_t q_drop_ctl;
static atomic_t q_drop_uwb;
static atomic_t q_drop_imu;
static atomic_t q_hwm_ctl;
static atomic_t q_hwm_uwb;
static atomic_t q_hwm_imu;
static atomic_t enqueue_ctl_count;
static atomic_t enqueue_uwb_count;
static atomic_t enqueue_imu_count;
static atomic_t abort_ctl_count;
static atomic_t abort_uwb_count;
static atomic_t abort_imu_count;
static atomic_t enqueue_ctl_max_us;
static atomic_t enqueue_uwb_max_us;
static atomic_t enqueue_imu_max_us;
static atomic_t enqueue_ctl_hist[ENQUEUE_HIST_BINS];
static atomic_t enqueue_uwb_hist[ENQUEUE_HIST_BINS];
static atomic_t enqueue_imu_hist[ENQUEUE_HIST_BINS];
static atomic_t publisher_count;
static atomic_t publisher_max_us;
static atomic_t publisher_hist[BSF_IMU_PULL_HIST_BINS];

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

static void atomic_update_max(atomic_t *maximum, uint32_t value)
{
	atomic_val_t previous = atomic_get(maximum);

	while (value > (uint32_t)previous &&
	       !atomic_cas(maximum, previous, (atomic_val_t)value)) {
		previous = atomic_get(maximum);
	}
}

static uint8_t enqueue_hist_bin(uint32_t duration_us)
{
	return duration_us < 100u ?
		(uint8_t)(duration_us / 10u) : ENQUEUE_HIST_BINS - 1u;
}

static void record_enqueue_duration(uint64_t start_us, atomic_t *count,
				    atomic_t *maximum, atomic_t *hist)
{
	uint64_t end_us = bsf_time_now_us();
	uint32_t duration_us = end_us >= start_us ?
		(uint32_t)MIN(end_us - start_us, (uint64_t)UINT32_MAX) : 0u;

	atomic_inc(count);
	atomic_update_max(maximum, duration_us);
	atomic_inc(&hist[enqueue_hist_bin(duration_us)]);
}

static void update_queue_high_water(struct k_msgq *queue, atomic_t *high_water)
{
	atomic_update_max(high_water, k_msgq_num_used_get(queue));
}

static void put_drop_oldest(struct k_msgq *queue, const void *item,
			    void *discard, atomic_t *drops)
{
	while (k_msgq_put(queue, item, K_NO_WAIT) != 0) {
		/*
		 * The publisher may win the race after the failed put. Count a
		 * drop only when this producer actually removes the oldest item.
		 */
		if (k_msgq_get(queue, discard, K_NO_WAIT) == 0) {
			atomic_inc(drops);
		}
	}
	k_sem_give(&publisher_sem);
}

static int enqueue_ctl_record(enum publish_attribute attribute,
			      const void *record, size_t len)
{
	struct publish_ctl_item item = {
		.len = (uint16_t)len,
		.attribute = (uint8_t)attribute,
	};
	struct publish_ctl_item discard;
	uint64_t start_us;

	if (len > sizeof(item.payload)) {
		atomic_inc(&abort_ctl_count);
		return -EMSGSIZE;
	}
	start_us = bsf_time_now_us();
	memcpy(item.payload, record, len);
	put_drop_oldest(&q_ctl, &item, &discard, &q_drop_ctl);
	update_queue_high_water(&q_ctl, &q_hwm_ctl);
	record_enqueue_duration(start_us, &enqueue_ctl_count,
				&enqueue_ctl_max_us, enqueue_ctl_hist);
	return 0;
}

static int enqueue_uwb_record(const void *record, size_t len)
{
	struct publish_uwb_item item = { .len = (uint16_t)len };
	struct publish_uwb_item discard;
	uint64_t start_us;

	if (len > sizeof(item.payload)) {
		atomic_inc(&abort_uwb_count);
		return -EMSGSIZE;
	}
	start_us = bsf_time_now_us();
	memcpy(item.payload, record, len);
	put_drop_oldest(&q_uwb, &item, &discard, &q_drop_uwb);
	update_queue_high_water(&q_uwb, &q_hwm_uwb);
	record_enqueue_duration(start_us, &enqueue_uwb_count,
				&enqueue_uwb_max_us, enqueue_uwb_hist);
	return 0;
}

static int enqueue_imu_record(const void *record, size_t len)
{
	struct publish_imu_item item = { .len = (uint16_t)len };
	struct publish_imu_item discard;
	uint64_t start_us;

	if (len > sizeof(item.payload)) {
		atomic_inc(&abort_imu_count);
		return -EMSGSIZE;
	}
	start_us = bsf_time_now_us();
	memcpy(item.payload, record, len);
	put_drop_oldest(&q_imu, &item, &discard, &q_drop_imu);
	update_queue_high_water(&q_imu, &q_hwm_imu);
	record_enqueue_duration(start_us, &enqueue_imu_count,
				&enqueue_imu_max_us, enqueue_imu_hist);
	return 0;
}

static int publish_data_record(const void *record, size_t len)
{
	const uint8_t *bytes = record;

	if (len < 2u) {
		return -EINVAL;
	}
	if (bytes[1] == BSF_BLE_KIND_UWB) {
		return enqueue_uwb_record(record, len);
	}
	if (bytes[1] == BSF_BLE_KIND_IMU) {
		return enqueue_imu_record(record, len);
	}
	return enqueue_ctl_record(PUBLISH_ATTRIBUTE_DATA, record, len);
}

static void publisher_notify(enum publish_attribute attribute,
			     const void *record, size_t len)
{
	const struct bt_gatt_attr *attr;
	atomic_t *subscribed;
	uint64_t start_us;
	uint64_t end_us;
	uint32_t duration_us;
	int err;

	if (attribute == PUBLISH_ATTRIBUTE_TELEMETRY) {
		attr = FUSION_TELEMETRY_ATTR;
		subscribed = &telemetry_subscribed;
	} else {
		attr = FUSION_DATA_ATTR;
		subscribed = &data_subscribed;
	}
	if (atomic_get(subscribed) == 0) {
		atomic_inc(&drop_unsub);
		return;
	}

	start_us = bsf_time_now_us();
	err = bt_gatt_notify(NULL, attr, record, len);
	end_us = bsf_time_now_us();
	duration_us = end_us >= start_us ?
		(uint32_t)MIN(end_us - start_us, (uint64_t)UINT32_MAX) : 0u;
	atomic_inc(&publisher_count);
	atomic_update_max(&publisher_max_us, duration_us);
	atomic_inc(&publisher_hist[bsf_imu_pull_hist_bin(duration_us)]);
	if (err == 0) {
		atomic_inc(&notify_ok);
	} else {
		atomic_inc(&drop_err);
		atomic_set(&last_notify_error, err);
	}
}

static void publisher_thread(void *unused1, void *unused2, void *unused3)
{
	struct publish_ctl_item ctl;
	struct publish_uwb_item uwb;
	struct publish_imu_item imu;

	ARG_UNUSED(unused1);
	ARG_UNUSED(unused2);
	ARG_UNUSED(unused3);
	while (true) {
		k_sem_take(&publisher_sem, K_FOREVER);
		do {
			/*
			 * Strict per-record priority. Re-evaluate from control after
			 * every potentially blocking notify so sustained IMU refill
			 * can never starve control or UWB.
			 */
			if (k_msgq_get(&q_ctl, &ctl, K_NO_WAIT) == 0) {
				publisher_notify(
					(enum publish_attribute)ctl.attribute,
					ctl.payload, ctl.len);
			} else if (k_msgq_get(&q_uwb, &uwb, K_NO_WAIT) == 0) {
				publisher_notify(PUBLISH_ATTRIBUTE_DATA,
						 uwb.payload, uwb.len);
			} else if (k_msgq_get(&q_imu, &imu, K_NO_WAIT) == 0) {
				publisher_notify(PUBLISH_ATTRIBUTE_DATA,
						 imu.payload, imu.len);
			} else {
				break;
			}
		} while (true);
	}
}

K_THREAD_DEFINE(publisher_thread_id, PUBLISHER_STACK_SIZE,
		publisher_thread, NULL, NULL, NULL,
		PUBLISHER_PRIORITY, 0, 0);

static void format_queue_summary(char *reply, size_t reply_size)
{
	snprintf(reply, reply_size,
		 "QUEUE di=%u du=%u dc=%u hi=%u hu=%u hc=%u ei=%u eu=%u ec=%u ai=%u au=%u ac=%u emi=%u emu=%u emc=%u pn=%u pm=%u",
		 (uint32_t)atomic_get(&q_drop_imu),
		 (uint32_t)atomic_get(&q_drop_uwb),
		 (uint32_t)atomic_get(&q_drop_ctl),
		 (uint32_t)atomic_get(&q_hwm_imu),
		 (uint32_t)atomic_get(&q_hwm_uwb),
		 (uint32_t)atomic_get(&q_hwm_ctl),
		 (uint32_t)atomic_get(&enqueue_imu_count),
		 (uint32_t)atomic_get(&enqueue_uwb_count),
		 (uint32_t)atomic_get(&enqueue_ctl_count),
		 (uint32_t)atomic_get(&abort_imu_count),
		 (uint32_t)atomic_get(&abort_uwb_count),
		 (uint32_t)atomic_get(&abort_ctl_count),
		 (uint32_t)atomic_get(&enqueue_imu_max_us),
		 (uint32_t)atomic_get(&enqueue_uwb_max_us),
		 (uint32_t)atomic_get(&enqueue_ctl_max_us),
		 (uint32_t)atomic_get(&publisher_count),
		 (uint32_t)atomic_get(&publisher_max_us));
}

static int format_enqueue_hist(char queue_name, char *reply, size_t reply_size)
{
	atomic_t *hist;
	atomic_t *count;
	atomic_t *maximum;
	size_t used;

	switch (queue_name) {
	case 'I':
		hist = enqueue_imu_hist;
		count = &enqueue_imu_count;
		maximum = &enqueue_imu_max_us;
		break;
	case 'U':
		hist = enqueue_uwb_hist;
		count = &enqueue_uwb_count;
		maximum = &enqueue_uwb_max_us;
		break;
	case 'C':
		hist = enqueue_ctl_hist;
		count = &enqueue_ctl_count;
		maximum = &enqueue_ctl_max_us;
		break;
	default:
		snprintf(reply, reply_size,
			 "QUEUE ENQ FAIL err=%d reason=queue", -EINVAL);
		return -EINVAL;
	}
	used = (size_t)snprintf(
		reply, reply_size, "QUEUE ENQ q=%c n=%u max=%u bin_us=10 h=",
		queue_name, (uint32_t)atomic_get(count),
		(uint32_t)atomic_get(maximum));
	for (uint32_t i = 0u; i < ENQUEUE_HIST_BINS && used < reply_size; ++i) {
		int written = snprintf(
			&reply[used], reply_size - used, "%s%u",
			i == 0u ? "" : ",", (uint32_t)atomic_get(&hist[i]));

		if (written < 0 || (size_t)written >= reply_size - used) {
			break;
		}
		used += (size_t)written;
	}
	return 0;
}

static int format_publisher_hist(uint8_t page, char *reply, size_t reply_size)
{
	uint32_t first = (uint32_t)page * PUBLISH_HIST_BINS_PER_PAGE;
	uint32_t count;
	size_t used;

	if (first >= BSF_IMU_PULL_HIST_BINS) {
		snprintf(reply, reply_size,
			 "QUEUE PUB HIST FAIL err=%d reason=page", -EINVAL);
		return -EINVAL;
	}
	count = MIN(PUBLISH_HIST_BINS_PER_PAGE,
		    BSF_IMU_PULL_HIST_BINS - first);
	used = (size_t)snprintf(reply, reply_size,
			       "QUEUE PUB HIST p=%u first=%u n=%u h=",
			       page, first, count);
	for (uint32_t i = 0u; i < count && used < reply_size; ++i) {
		int written = snprintf(
			&reply[used], reply_size - used, "%s%u",
			i == 0u ? "" : ",",
			(uint32_t)atomic_get(&publisher_hist[first + i]));

		if (written < 0 || (size_t)written >= reply_size - used) {
			break;
		}
		used += (size_t)written;
	}
	return 0;
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

static void publish_uwb(const bsl_frame_t *frame, uint64_t frame_timestamp_us)
{
	bsf_ble_uwb_packet_t packet = {
		.version = BSF_BLE_PROTOCOL_VERSION,
		.kind = BSF_BLE_KIND_UWB,
		.len = sizeof(packet),
		.node_sequence = (uint32_t)atomic_inc(&node_sequence) + 1u,
		.node_uptime_ms = (uint32_t)k_uptime_get(),
		.uwb = frame->body,
	};
	bsf_strobe_capture_pair(frame->body.flags, frame_timestamp_us,
				&packet.capture);
	if (packet.capture.verdict == BSF_CAPTURE_HEALTHY) {
		led_note_paired_frame();
	} else {
		led_note_uwb_fault();
	}
	(void)publish_data_record(&packet, sizeof(packet));
}

static void parser_accept_uwb_frame(uint64_t frame_timestamp_us)
{
	const bsl_frame_t *frame = (const bsl_frame_t *)parser_frame;
	uint16_t received_crc;
	uint16_t calculated_crc;

	if (frame->hdr.version != BSL_VERSION ||
	    frame->hdr.len != sizeof(bsl_uwb_t)) {
		atomic_inc(&header_errors);
		led_note_uwb_fault();
		parser_reset();
		return;
	}

	memcpy(&received_crc, &frame->crc, sizeof(received_crc));
	calculated_crc = crc16_ccitt_false(
		parser_frame, offsetof(bsl_frame_t, crc));
	if (received_crc != calculated_crc) {
		atomic_inc(&crc_errors);
		led_note_uwb_fault();
		parser_reset();
		return;
	}

	atomic_inc(&valid_frames);
	account_sweep(frame->body.sweep);
	publish_uwb(frame, frame_timestamp_us);
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
		led_note_uwb_fault();
		parser_reset();
		return;
	}
	if (header->version != BSL_RELAY_VERSION ||
	    header->type != BSL_RELAY_TYPE_ACK ||
	    header->len > BSL_RELAY_PAYLOAD_MAX) {
		atomic_inc(&header_errors);
		led_note_uwb_fault();
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

static void parser_consume_byte(uint8_t byte, uint64_t arrival_timestamp_us)
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
			parser_consume_byte(byte, arrival_timestamp_us);
		}
		return;
	}

	if (parser_position >= sizeof(parser_frame)) {
		atomic_inc(&header_errors);
		led_note_uwb_fault();
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
				led_note_uwb_fault();
				parser_reset();
				return;
			}
		parser_expected = sizeof(*header) + header->len +
			sizeof(uint16_t);
	}

	if (parser_expected != 0u && parser_position == parser_expected) {
		if (parser_type == BSF_BLE_KIND_UWB) {
			parser_accept_uwb_frame(arrival_timestamp_us);
		} else {
			parser_accept_relay_frame();
		}
	}
}

static void uart_parser_thread(void *unused1, void *unused2, void *unused3)
{
	uint32_t item[UART_RING_ITEM_WORDS];

	ARG_UNUSED(unused1);
	ARG_UNUSED(unused2);
	ARG_UNUSED(unused3);

	while (true) {
		(void)k_sem_take(&uart_data_sem, K_MSEC(LED_RENDER_PERIOD_MS));
		if (atomic_cas(&led_fault_clear_requested, 1, 0)) {
			bsf_led_fault_window_clear_and_arm(
				&led_uwb_fault_window);
		}

		while (true) {
			uint16_t data_len;
			uint8_t item_type;
			uint8_t item_words = ARRAY_SIZE(item);
			uint64_t arrival_timestamp_us;
			int ret = ring_buf_item_get(&uart_ring, &data_len,
						    &item_type, item,
						    &item_words);

			if (ret == -EAGAIN) {
				break;
			}
			if (ret != 0 || item_type != UART_RING_ITEM_RX ||
			    data_len == 0u || data_len > UART_DMA_BUFFER_SIZE ||
			    item_words != UART_RING_TIMESTAMP_WORDS +
				DIV_ROUND_UP(data_len, sizeof(uint32_t))) {
				atomic_inc(&header_errors);
				led_note_uwb_fault();
				continue;
			}
			arrival_timestamp_us =
				(uint64_t)item[0] | ((uint64_t)item[1] << 32);
			const uint8_t *data =
				(const uint8_t *)&item[UART_RING_TIMESTAMP_WORDS];

			led_note_uart_activity();
			for (uint16_t i = 0; i < data_len; ++i) {
				parser_consume_byte(data[i],
						    arrival_timestamp_us);
			}
		}
		led_render();
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
		uint32_t item[UART_RING_ITEM_WORDS];
		uint16_t data_len = event->data.rx.len;
		uint8_t item_words = UART_RING_TIMESTAMP_WORDS +
			DIV_ROUND_UP(data_len, sizeof(uint32_t));
		/*
		 * Timing-critical ISR addition: one TIMER2 capture and one
		 * timestamp field carried in the same ring item as these bytes.
		 */
		uint64_t arrival_timestamp_us = bsf_time_now_us();
		int ret;

		item[0] = (uint32_t)arrival_timestamp_us;
		item[1] = (uint32_t)(arrival_timestamp_us >> 32);
		item[item_words - 1u] = 0u;
		memcpy(&item[UART_RING_TIMESTAMP_WORDS], data, data_len);
		ret = ring_buf_item_put(&uart_ring, data_len,
					UART_RING_ITEM_RX, item, item_words);

		atomic_add(&uart_bytes, (atomic_val_t)event->data.rx.len);
		if (ret != 0) {
			atomic_add(&ring_dropped_bytes, (atomic_val_t)data_len);
		} else {
			k_sem_give(&uart_data_sem);
		}
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

static void boot_confirm_timeout_work_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(boot_confirm_timeout_work,
			boot_confirm_timeout_work_handler);

static void boot_confirm_commit_work_handler(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(boot_confirm_commit_work,
			boot_confirm_commit_work_handler);

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

static void boot_confirm_timeout_work_handler(struct k_work *work)
{
	bool required;

	ARG_UNUSED(work);
	k_mutex_lock(&boot_confirm_lock, K_FOREVER);
	required = boot_confirm_policy.required && !boot_is_img_confirmed();
	k_mutex_unlock(&boot_confirm_lock);
	if (required) {
		LOG_ERR("MCUboot confirmation timeout after %u ms; rebooting for test-image rollback",
			BOOT_CONFIRM_TIMEOUT_MS);
		sys_reboot(SYS_REBOOT_COLD);
	}
}

static void boot_confirm_commit_work_handler(struct k_work *work)
{
	bool may_confirm;
	int ret;

	ARG_UNUSED(work);
	k_mutex_lock(&boot_confirm_lock, K_FOREVER);
	may_confirm = bsf_boot_confirm_policy_may_confirm(
		&boot_confirm_policy, atomic_get(&ble_connected) != 0,
		atomic_get(&data_subscribed) != 0);
	if (!may_confirm) {
		k_mutex_unlock(&boot_confirm_lock);
		LOG_ERR("MCUboot confirmation guard failed connected=%u subscribed=%u; timeout rollback remains armed",
			atomic_get(&ble_connected) != 0,
			atomic_get(&data_subscribed) != 0);
		return;
	}
	ret = boot_write_img_confirmed();
	if (ret == 0) {
		boot_confirm_policy.required = false;
	}
	k_mutex_unlock(&boot_confirm_lock);
	if (ret == 0) {
		(void)k_work_cancel_delayable(&boot_confirm_timeout_work);
		LOG_INF("MCUboot image confirmed after two-command BLE round trip and %u ms guard",
			BOOT_CONFIRM_GUARD_MS);
	} else {
		LOG_ERR("MCUboot image confirmation failed err=%d; timeout rollback remains armed",
			ret);
	}
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
	atomic_set(&q_drop_ctl, 0);
	atomic_set(&q_drop_uwb, 0);
	atomic_set(&q_drop_imu, 0);
	atomic_set(&q_hwm_ctl, 0);
	atomic_set(&q_hwm_uwb, 0);
	atomic_set(&q_hwm_imu, 0);
	atomic_set(&enqueue_ctl_count, 0);
	atomic_set(&enqueue_uwb_count, 0);
	atomic_set(&enqueue_imu_count, 0);
	atomic_set(&abort_ctl_count, 0);
	atomic_set(&abort_uwb_count, 0);
	atomic_set(&abort_imu_count, 0);
	atomic_set(&enqueue_ctl_max_us, 0);
	atomic_set(&enqueue_uwb_max_us, 0);
	atomic_set(&enqueue_imu_max_us, 0);
	atomic_set(&publisher_count, 0);
	atomic_set(&publisher_max_us, 0);
	for (uint32_t i = 0u; i < ENQUEUE_HIST_BINS; ++i) {
		atomic_set(&enqueue_ctl_hist[i], 0);
		atomic_set(&enqueue_uwb_hist[i], 0);
		atomic_set(&enqueue_imu_hist[i], 0);
	}
	for (uint32_t i = 0u; i < BSF_IMU_PULL_HIST_BINS; ++i) {
		atomic_set(&publisher_hist[i], 0);
	}
	/*
	 * The parser owns the window state. COUNTERS CLEAR requests the same
	 * acceptance baseline asynchronously instead of racing that worker.
	 */
	atomic_set(&led_fault_clear_requested, 1);
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

static int parse_hex_u32(const char *text, uint32_t max_value,
			 const char **end_out, uint32_t *value)
{
	char *end;
	unsigned long parsed;

	errno = 0;
	parsed = strtoul(text, &end, 16);
	if (errno != 0 || end == text || parsed > max_value) {
		return -EINVAL;
	}
	*end_out = end;
	*value = (uint32_t)parsed;
	return 0;
}

static int parse_hex_u32_command(const char *command, const char *prefix,
				 uint32_t *value)
{
	const size_t prefix_len = strlen(prefix);
	const char *end;
	int ret;

	if (strncmp(command, prefix, prefix_len) != 0) {
		return -ENOENT;
	}
	ret = parse_hex_u32(command + prefix_len, UINT32_MAX, &end, value);
	return ret == 0 && *end == '\0' ? 0 : -EINVAL;
}

static int parse_imu_reg_command(const char *command, uint8_t *reg,
				 bool *have_value, uint16_t *value)
{
	static const char prefix[] = "IMU REG=";
	static const char value_prefix[] = " VAL=";
	const char *end;
	uint32_t parsed;
	int ret;

	if (strncmp(command, prefix, sizeof(prefix) - 1u) != 0) {
		return -ENOENT;
	}
	ret = parse_hex_u32(command + sizeof(prefix) - 1u, UINT8_MAX,
			    &end, &parsed);
	if (ret != 0) {
		return ret;
	}
	*reg = (uint8_t)parsed;
	if (*end == '\0') {
		*have_value = false;
		return 0;
	}
	if (strncmp(end, value_prefix, sizeof(value_prefix) - 1u) != 0) {
		return -EINVAL;
	}
	ret = parse_hex_u32(end + sizeof(value_prefix) - 1u, UINT16_MAX,
			    &end, &parsed);
	if (ret != 0 || *end != '\0') {
		return -EINVAL;
	}
	*have_value = true;
	*value = (uint16_t)parsed;
	return 0;
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
	uint16_t reg_value = 0u;
	uint32_t value;
	uint8_t reg = 0u;
	bool have_reg_value = false;
	int ret;

	if (strcmp(command, "BOOT CONFIRM STATUS") == 0) {
		bool required;
		bool prepared;
		bool committed;

		k_mutex_lock(&boot_confirm_lock, K_FOREVER);
		required = boot_confirm_policy.required;
		prepared = boot_confirm_policy.prepared;
		committed = boot_confirm_policy.committed;
		k_mutex_unlock(&boot_confirm_lock);
		snprintf(reply, sizeof(reply),
			 "BOOT CONFIRM STATUS confirmed=%u required=%u prepared=%u committed=%u enabled=%u timeout_ms=%u guard_ms=%u",
			 boot_is_img_confirmed() ? 1u : 0u, required ? 1u : 0u,
			 prepared ? 1u : 0u, committed ? 1u : 0u,
			 BSF_BOOT_CONFIRM_ENABLED, BOOT_CONFIRM_TIMEOUT_MS,
			 BOOT_CONFIRM_GUARD_MS);
	} else if (strcmp(command, "BOOT CONFIRM PREPARE") == 0) {
		uint32_t token = ((uint32_t)node_identity << 16) ^
			k_cycle_get_32() ^ (uint32_t)correlation ^ boot_reset_reason;
		bool prepared = false;

		if (token == 0u) {
			token = 1u;
		}
		if (BSF_BOOT_CONFIRM_ENABLED != 0) {
			k_mutex_lock(&boot_confirm_lock, K_FOREVER);
			prepared = bsf_boot_confirm_policy_prepare(
				&boot_confirm_policy,
				atomic_get(&ble_connected) != 0,
				atomic_get(&data_subscribed) != 0, token);
			k_mutex_unlock(&boot_confirm_lock);
		}
		if (boot_is_img_confirmed()) {
			snprintf(reply, sizeof(reply),
				 "BOOT CONFIRM PREPARE ALREADY confirmed=1");
		} else if (BSF_BOOT_CONFIRM_ENABLED == 0) {
			snprintf(reply, sizeof(reply),
				 "BOOT CONFIRM PREPARE DISABLED proof=1 confirmed=0");
		} else if (!prepared) {
			snprintf(reply, sizeof(reply),
				 "BOOT CONFIRM PREPARE FAIL connected=%u subscribed=%u confirmed=0",
				 atomic_get(&ble_connected) != 0,
				 atomic_get(&data_subscribed) != 0);
		} else {
			snprintf(reply, sizeof(reply),
				 "BOOT CONFIRM PREPARED token=%08X confirmed=0",
				 token);
		}
	} else if (parse_hex_u32_command(command, "BOOT CONFIRM COMMIT=",
						 &value) == 0) {
		bool committed = false;

		if (BSF_BOOT_CONFIRM_ENABLED != 0) {
			k_mutex_lock(&boot_confirm_lock, K_FOREVER);
			committed = bsf_boot_confirm_policy_commit(
				&boot_confirm_policy, value);
			k_mutex_unlock(&boot_confirm_lock);
		}
		if (committed) {
			snprintf(reply, sizeof(reply),
				 "BOOT CONFIRM COMMIT OK token=%08X guard_ms=%u",
				 value, BOOT_CONFIRM_GUARD_MS);
			(void)k_work_reschedule(&boot_confirm_commit_work,
						K_MSEC(BOOT_CONFIRM_GUARD_MS));
		} else {
			snprintf(reply, sizeof(reply),
				 "BOOT CONFIRM COMMIT FAIL token=%08X enabled=%u",
				 value, BSF_BOOT_CONFIRM_ENABLED);
		}
	} else if (strncmp(command, "BOOT CONFIRM COMMIT=", 20u) == 0) {
		snprintf(reply, sizeof(reply),
			 "BOOT CONFIRM COMMIT FAIL reason=syntax");
	} else if (strcmp(command, "PING") == 0) {
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
	} else if (bsf_led_fault_test_command_matches(command)) {
		/*
		 * F3 indicator-rendering hook only. This enters at the same
		 * recent-window input as a detected CRC/header/pairing fault; it
		 * neither alters nor claims to test those detectors.
		 */
		led_note_uwb_fault();
		snprintf(reply, sizeof(reply),
			 "TEST ONLY LED SENDING FAULT INJECTED window_ms=%u",
			 BSF_LED_FAULT_WINDOW_MS);
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
			 "CTRQ di=%u du=%u dc=%u hi=%u hu=%u hc=%u pn=%u pm=%u",
			 (uint32_t)atomic_get(&q_drop_imu),
			 (uint32_t)atomic_get(&q_drop_uwb),
			 (uint32_t)atomic_get(&q_drop_ctl),
			 (uint32_t)atomic_get(&q_hwm_imu),
			 (uint32_t)atomic_get(&q_hwm_uwb),
			 (uint32_t)atomic_get(&q_hwm_ctl),
			 (uint32_t)atomic_get(&publisher_count),
			 (uint32_t)atomic_get(&publisher_max_us));
		(void)publish_control_reply(BSF_CONTROL_SOURCE_B306,
					    correlation, reply);
		snprintf(reply, sizeof(reply),
			 "CTR2 nok=%u unsub=%u nerr=%u last=%d ip=%u idup=%u ie=%u ir=%u ctrl=%u bad=%u relay=%u/%u/%u",
			 (uint32_t)atomic_get(&notify_ok),
			 (uint32_t)atomic_get(&drop_unsub),
			 (uint32_t)atomic_get(&drop_err),
			 (int32_t)atomic_get(&last_notify_error),
			 imu_stats.pulls, imu_stats.repeated_chip_polls,
			 imu_stats.i2c_errors, imu_stats.records,
			 (uint32_t)atomic_get(&ctrl_rx),
			 (uint32_t)atomic_get(&ctrl_bad_bsf),
			 (uint32_t)atomic_get(&relay_tx),
			 (uint32_t)atomic_get(&relay_ack),
			 (uint32_t)atomic_get(&relay_timeout));
	} else if (strcmp(command, "IMU START") == 0) {
		ret = bsf_imu_start(reply, sizeof(reply));
		ARG_UNUSED(ret);
	} else if (strcmp(command, "IMU STOP") == 0) {
		ret = bsf_imu_stop();
		bsf_imu_format_stop(reply, sizeof(reply), ret);
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
	} else if (parse_exact_u32_command(command, "IMU RRATE=", &value) == 0) {
		if (value <= UINT16_MAX) {
			ret = bsf_imu_set_rrate_runtime((uint16_t)value, reply,
						       sizeof(reply));
		} else {
			snprintf(reply, sizeof(reply),
				 "IMU RRATE FAIL request=%u volatile=1 saved=0 err=%d reason=range",
				 value, -EINVAL);
		}
	} else if ((ret = parse_hex_u32_command(command, "IMU BW=", &value)) == 0) {
		if (value <= UINT16_MAX) {
			ret = bsf_imu_set_bandwidth_runtime((uint16_t)value, reply,
							   sizeof(reply));
		} else {
			snprintf(reply, sizeof(reply),
				 "IMU BW FAIL request=%lX readback=0000 volatile=1 saved=0 step=guard err=%d reason=range",
				 (unsigned long)value, -EINVAL);
		}
	} else if (strncmp(command, "IMU BW=", 7u) == 0) {
		snprintf(reply, sizeof(reply),
			 "IMU BW FAIL readback=0000 volatile=1 saved=0 step=guard err=%d reason=syntax",
			 ret);
	} else if ((ret = parse_imu_reg_command(command, &reg,
						&have_reg_value,
						&reg_value)) == 0) {
		if (have_reg_value) {
			(void)bsf_imu_reg_write(reg, reg_value, reply,
						sizeof(reply));
		} else {
			(void)bsf_imu_reg_read(reg, reply, sizeof(reply));
		}
	} else if (strncmp(command, "IMU REG=", 8u) == 0) {
		snprintf(reply, sizeof(reply),
			 "IMU REG FAIL err=%d reason=syntax", ret);
		} else if (strcmp(command, "IMU STATUS") == 0) {
			bsf_imu_format_status(reply, sizeof(reply));
			} else if (strcmp(command, "IMU LATENCY") == 0) {
				bsf_imu_format_latency(reply, sizeof(reply));
			} else if (strcmp(command, "IMU PULL") == 0) {
				bsf_imu_format_pull_summary(reply, sizeof(reply));
			} else if (parse_exact_u32_command(command, "IMU PULL LAT=",
							   &value) == 0) {
				ret = value <= UINT8_MAX ?
					bsf_imu_format_pull_hist_page(
						false, (uint8_t)value, reply,
						sizeof(reply)) : -EINVAL;
			} else if (parse_exact_u32_command(command, "IMU PULL DUR=",
							   &value) == 0) {
				ret = value <= UINT8_MAX ?
					bsf_imu_format_pull_hist_page(
						true, (uint8_t)value, reply,
						sizeof(reply)) : -EINVAL;
				} else if (strcmp(command, "QUEUE") == 0 ||
					   strcmp(command, "IMU PUB") == 0) {
					format_queue_summary(reply, sizeof(reply));
				} else if (strcmp(command, "QUEUE ENQ=I") == 0) {
					ret = format_enqueue_hist(
						'I', reply, sizeof(reply));
				} else if (strcmp(command, "QUEUE ENQ=U") == 0) {
					ret = format_enqueue_hist(
						'U', reply, sizeof(reply));
				} else if (strcmp(command, "QUEUE ENQ=C") == 0) {
					ret = format_enqueue_hist(
						'C', reply, sizeof(reply));
				} else if (parse_exact_u32_command(
						   command, "QUEUE PUB HIST=",
						   &value) == 0 ||
					   parse_exact_u32_command(
						   command, "IMU PUB HIST=",
						   &value) == 0) {
					ret = value <= UINT8_MAX ?
						format_publisher_hist(
							(uint8_t)value, reply,
							sizeof(reply)) : -EINVAL;
				} else if (parse_exact_u32_command(command, "IMU DELTA=",
								   &value) == 0) {
			ret = value <= UINT8_MAX ?
				bsf_imu_format_delta_page((uint8_t)value, reply,
							  sizeof(reply)) :
				-EINVAL;
			if (value > UINT8_MAX) {
				snprintf(reply, sizeof(reply),
					 "IMU DELTA FAIL err=%d reason=page",
					 ret);
			}
		} else if (strncmp(command, "IMU DELTA=", 10u) == 0) {
			snprintf(reply, sizeof(reply),
				 "IMU DELTA FAIL err=%d reason=syntax",
				 -EINVAL);
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
	bsf_ble_queue_counters_t queue_counters;

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
		/* Legacy wire name: now counts repeated chip-ms polls. */
		.imu_dup = imu_stats.repeated_chip_polls,
		.imu_i2c_err = imu_stats.i2c_errors,
		.imu_records = imu_stats.records,
		.imu_missed_deadlines = imu_stats.missed_chip_frames,
		.ctrl_rx = (uint32_t)atomic_get(&ctrl_rx),
		.ctrl_bad_bsf = (uint32_t)atomic_get(&ctrl_bad_bsf),
		.relay_tx = (uint32_t)atomic_get(&relay_tx),
		.relay_ack = (uint32_t)atomic_get(&relay_ack),
		.relay_timeout = (uint32_t)atomic_get(&relay_timeout),
		.imu_rate_hz = imu_stats.rate_hz,
		.imu_batch = imu_stats.batch_size,
		.imu_active = imu_stats.active,
		.imu_health_class = imu_stats.health_class,
		.imu_health_active = imu_stats.health_active,
		.imu_health_latched = imu_stats.health_latched,
		.imu_extended_burst = imu_stats.extended_burst,
		.imu_health_reset = imu_stats.health_reset,
		.imu_health_frozen = imu_stats.health_frozen,
		.imu_health_rate = imu_stats.health_rate,
		.imu_health_canary = imu_stats.health_canary,
		.imu_health_plausibility = imu_stats.health_plausibility,
		.imu_health_dead = imu_stats.health_dead,
		.imu_health_identical = imu_stats.health_identical,
		/* Legacy wire field: consecutive-I2C-failure escalations. */
		.imu_health_i2c_burst = imu_stats.health_i2c_escalation,
		.imu_health_recover_ok = imu_stats.health_recover_ok,
		.imu_health_recover_fail = imu_stats.health_recover_fail,
		.imu_legacy_pull_mean_us = imu_stats.legacy_pull_mean_us,
		.imu_extended_pull_mean_us =
			imu_stats.extended_pull_mean_us,
		.imu_last_good_ts_us = imu_stats.last_good_ts_us,
		.imu_fault_ts_us = imu_stats.fault_ts_us,
			.imu_recovered_ts_us = imu_stats.recovered_ts_us,
			.imu_pull_lateness_max_us =
				(uint16_t)MIN(imu_stats.pull_lateness_max_us,
					      (uint32_t)UINT16_MAX),
			.imu_pull_duration_max_us =
				(uint16_t)MIN(imu_stats.pull_duration_max_us,
					      (uint32_t)UINT16_MAX),
		};

	ARG_UNUSED(work);
	if (watchdog_ret != 0) {
		LOG_ERR("watchdog feed failed: %d", watchdog_ret);
	}
	bsf_strobe_capture_telemetry(&telemetry);
	(void)enqueue_ctl_record(PUBLISH_ATTRIBUTE_TELEMETRY,
				 &telemetry, sizeof(telemetry));
	queue_counters = (bsf_ble_queue_counters_t) {
		.version = BSF_BLE_PROTOCOL_VERSION,
		.kind = BSF_BLE_KIND_QUEUE_COUNTERS,
		.len = sizeof(queue_counters),
		.node_uptime_ms = (uint32_t)k_uptime_get(),
		.q_drop_imu = (uint32_t)atomic_get(&q_drop_imu),
		.q_drop_uwb = (uint32_t)atomic_get(&q_drop_uwb),
		.q_drop_ctl = (uint32_t)atomic_get(&q_drop_ctl),
		.q_hwm_imu = (uint16_t)atomic_get(&q_hwm_imu),
		.q_hwm_uwb = (uint16_t)atomic_get(&q_hwm_uwb),
		.q_hwm_ctl = (uint16_t)atomic_get(&q_hwm_ctl),
			.publisher_count = (uint32_t)atomic_get(&publisher_count),
			.publisher_max_us = (uint32_t)atomic_get(&publisher_max_us),
			.enq_imu = (uint32_t)atomic_get(&enqueue_imu_count),
			.enq_uwb = (uint32_t)atomic_get(&enqueue_uwb_count),
			.enq_ctl = (uint32_t)atomic_get(&enqueue_ctl_count),
			.abort_imu = (uint32_t)atomic_get(&abort_imu_count),
			.abort_uwb = (uint32_t)atomic_get(&abort_uwb_count),
			.abort_ctl = (uint32_t)atomic_get(&abort_ctl_count),
		};
	(void)enqueue_ctl_record(PUBLISH_ATTRIBUTE_DATA,
				 &queue_counters, sizeof(queue_counters));

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
	atomic_clear(&ble_connected);
	atomic_clear(&data_subscribed);
	atomic_clear(&telemetry_subscribed);
	k_mutex_lock(&boot_confirm_lock, K_FOREVER);
	boot_confirm_policy.prepared = false;
	boot_confirm_policy.committed = false;
	k_mutex_unlock(&boot_confirm_lock);
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
	atomic_set(&ble_connected, 1);
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
	bsf_boot_confirm_policy_init(&boot_confirm_policy,
				     boot_is_img_confirmed());

	ret = watchdog_start();
	if (ret != 0) {
		LOG_ERR("watchdog initialization failed: %d", ret);
		return 0;
	}

	if (!gpio_is_ready_dt(&data_led) || !gpio_is_ready_dt(&link_led)) {
		LOG_ERR("status LED GPIO is not ready");
		return 0;
	}

	ret = gpio_pin_configure_dt(&data_led, GPIO_OUTPUT_INACTIVE);
	if (ret != 0) {
		LOG_ERR("data LED configuration failed: %d", ret);
		return 0;
	}
	ret = gpio_pin_configure_dt(&link_led, GPIO_OUTPUT_INACTIVE);
	if (ret != 0) {
		LOG_ERR("link LED configuration failed: %d", ret);
		return 0;
	}
	led_data_level = 0;
	led_link_level = 0;

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

	if (boot_confirm_policy.required) {
		(void)k_work_reschedule(&boot_confirm_timeout_work,
					K_MSEC(BOOT_CONFIRM_TIMEOUT_MS));
		LOG_INF("MCUboot image unconfirmed: two-command BLE round trip required; enabled=%u timeout_ms=%u guard_ms=%u",
			BSF_BOOT_CONFIRM_ENABLED, BOOT_CONFIRM_TIMEOUT_MS,
			BOOT_CONFIRM_GUARD_MS);
	}

	while (true) {
		k_sleep(K_HOURS(1));
	}

	return 0;
}
