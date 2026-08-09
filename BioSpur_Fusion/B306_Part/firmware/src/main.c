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
#include <zephyr/net_buf.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/sys/util.h>

#include "biospur_fusion_ble.h"
#include "biospur_link.h"
#include "boot_confirm_policy.h"
#include "imu.h"
#include "imu_autostart_policy.h"
#include "imu_pull_diag_math.h"
#include "led_fault_window.h"
#include "publisher_priority.h"
#include "stall_detector_policy.h"
#include "stall_ring_policy.h"
#include <zephyr/sys/crc.h>
#include "bsf_bt_stage.h"
#include "bsf_v45.h"
#include "bsf_v45_corpse.h"
#include "bsf_v45_detector.h"
#include "bsf_v45_trace.h"
#include "strobe_capture.h"
#include <zephyr/mgmt/mcumgr/mgmt/callbacks.h>
#if defined(CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS)
#include <zephyr/mgmt/mcumgr/grp/img_mgmt/img_mgmt_callbacks.h>
#endif

LOG_MODULE_REGISTER(biospur_fusion, LOG_LEVEL_INF);

#define LED0_NODE DT_ALIAS(led0)
#define LED1_NODE DT_ALIAS(led1)
#define UWB_UART_NODE DT_ALIAS(uwb_uart)

#ifndef BSF_FW_MARKER
#define BSF_FW_MARKER "b306-imu-relay-v37"
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
#define NOTIFY_WORKER_STACK_SIZE 2048
#define NOTIFY_WORKER_PRIORITY 9
/* 1.120 s was the largest isolated healthy call; retain 80 ms margin. */
#define NOTIFY_ACCEPT_TIMEOUT_MS 1200u
#define BLE_SUPERVISION_TIMEOUT_MS 4000u
#define STALL_DETECT_MS 5000u
#define STALL_RECOVERY_RETRACT_MS 1500u
#define STALL_ARM_NOTIFY_OK 64u
#define STALL_MAX_RECOVERIES_PER_POWER 1u
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

/*
 * The v45 capture routine's one feed. See bsf_v45.h for why this exists and
 * why it is not the same thing as extending WATCHDOG_TIMEOUT_MS.
 *
 * The return value is dropped deliberately: the capture must proceed whether
 * or not the feed took. A failed feed costs the corpse its extra 30 s, which
 * is exactly the situation that existed before this function -- it is never a
 * reason to abandon a capture that is already underway.
 */
void bsf_v45_wdt_kick(void)
{
	(void)watchdog_feed_once();
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
static struct bt_uuid_128 fusion_stall_uuid =
	BT_UUID_INIT_128(BT_UUID_128_ENCODE(
		BSF_BLE_UUID_STALL_W32, BSF_BLE_UUID_W16_1,
		BSF_BLE_UUID_W16_2, BSF_BLE_UUID_W16_3, BSF_BLE_UUID_W48));

static atomic_t data_subscribed;
static atomic_t telemetry_subscribed;
static atomic_t subscribed_notify_ok;
static bsf_stall_status_t stall_status;
static struct k_spinlock stall_status_lock;

static ssize_t control_write(struct bt_conn *conn,
			     const struct bt_gatt_attr *attr,
			     const void *buf, uint16_t len,
			     uint16_t offset, uint8_t flags);
static ssize_t stall_status_read(struct bt_conn *conn,
				 const struct bt_gatt_attr *attr,
				 void *buf, uint16_t len, uint16_t offset);

/* Defined with the v45 environment bridge, below. */
static void v45_new_epoch(bool connected_now);

static void data_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	ARG_UNUSED(attr);
	atomic_set(&data_subscribed, value == BT_GATT_CCC_NOTIFY);
	if (value != BT_GATT_CCC_NOTIFY) {
		atomic_set(&subscribed_notify_ok, 0);
		/*
		 * v45: unsubscribe retires the incarnation. Section 5 lists
		 * "unsubscribed -> no trigger" as a required no-trigger path;
		 * this is where it is made true.
		 */
		v45_new_epoch(false);
	}
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
	BT_GATT_CHARACTERISTIC(&fusion_stall_uuid.uuid,
			       BT_GATT_CHRC_READ,
			       BT_GATT_PERM_READ,
			       stall_status_read, NULL, NULL),
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

enum notify_stream { NOTIFY_CTL, NOTIFY_UWB, NOTIFY_IMU, NOTIFY_STREAMS };

struct notify_job {
	const struct bt_gatt_attr *attr;
	uint16_t len;
	uint8_t stream;
	uint8_t payload[sizeof(bsf_ble_telemetry_t)];
};

struct retained_stall_diag {
	uint32_t magic;
	uint32_t test_value;
	uint32_t alarm_count;
	uint32_t alarm_reason;
	uint32_t entry_count;
	uint32_t exit_count;
	uint32_t entry_ms;
	uint32_t exit_ms;
	uint32_t in_call_stream;
	uint32_t last_return_code;
	uint32_t alarm_timestamp_ms;
	uint32_t recovery_count;
	bsf_stall_status_t first_snapshot;
};

#define RETAINED_STALL_MAGIC 0x56333852u
__attribute__((section(".noinit"))) static struct retained_stall_diag retained_stall;

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
K_SEM_DEFINE(notify_idle_sem, 1, 1);
K_SEM_DEFINE(notify_job_sem, 0, 1);
static struct notify_job notify_job;

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
static atomic_t uart_restart_framing;
static atomic_t uart_restart_overrun;
static atomic_t uart_restart_break_idle;
static atomic_t uart_restart_parser;
static atomic_t uart_restart_explicit;
static atomic_t uart_restart_other;
static atomic_t uart_restart_discarded_frames;
static atomic_t uart_last_stop_reason;
static atomic_t last_uart_error;
static atomic_t last_sweep;
static atomic_t have_last_sweep;
static atomic_t tag_reset_detected;
static atomic_t tag_reset_recovery_attempted;
/*
 * Volatile one-shot initial-condition latch. Any DK IMU command outranks the
 * beacon for the remainder of this power cycle. Beacon loss never clears it
 * and never stops an already-running IMU.
 */
static atomic_t imu_touched;
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
static atomic_t notify_in_call;
static atomic_t notify_fast_drop;
static atomic_t notify_timeout_drop[NOTIFY_STREAMS];
static atomic_t notify_rc_nomem;
static atomic_t notify_rc_notconn;
static atomic_t notify_rc_again;
static atomic_t notify_rc_other;
/*
 * R4/A3. Consecutive -ENOTCONN from bt_gatt_notify() while the application
 * still believes it is connected. Written only by notify_worker_thread, which
 * is the sole caller of bt_gatt_notify() -- single writer by construction.
 */
static atomic_t notify_notconn_streak;
static atomic_t producer_heartbeat;
static atomic_t stall_alarm_count;
static atomic_t stall_alarm_reason;
static atomic_t stall_recovery_pending;
static atomic_t pool_low_water[BSF_NET_BUF_POOL_MAX];
static struct bsf_stall_detector stall_detector;

/*
 * v45 connection incarnation.
 *
 * The detector's arming conditions and every dwell it accumulates are scoped to
 * ONE connection. Bumping this on connect and on disconnect is what makes
 * "normal disconnect within the supervision timeout" a no-trigger path by
 * construction rather than by a special case in the trigger test -- a new epoch
 * simply replaces all dwell state.
 */
static atomic_t v45_epoch;
static atomic_t v45_connected_at_ms;
static atomic_t v45_exit_base;   /* notify_exit_total at the epoch boundary */

static uint32_t pool_name_hash(const char *name)
{
	uint32_t hash = 2166136261u;

	for (; *name != '\0'; ++name) {
		hash = (hash ^ (uint8_t)*name) * 16777619u;
	}
	return hash;
}

static uint8_t sample_pool_usage(bsf_net_buf_pool_usage_t *out)
{
	uint8_t count = 0u;
	struct net_buf_pool *pool;

	STRUCT_SECTION_FOREACH(net_buf_pool, pool) {
		if (count >= BSF_NET_BUF_POOL_MAX) {
			break;
		}
		uint16_t available = (uint16_t)atomic_get(&pool->avail_count);
		atomic_val_t low = atomic_get(&pool_low_water[count]);
		uint16_t window_low;

		/* Fold this reading into the open window. */
		while (available < (uint16_t)low &&
		       !atomic_cas(&pool_low_water[count], low, available)) {
			low = atomic_get(&pool_low_water[count]);
		}
		/*
		 * Take the window minimum and re-arm at the present level, in one
		 * atomic exchange, so `low_water` means "minimum available since
		 * the previous record" rather than "minimum since boot".
		 *
		 * The since-boot form was unusable in practice: every board drives
		 * its ATT pool to zero during its own DFU, so the field latched at
		 * 0 for the entire deployment and looked like a live signal while
		 * carrying nothing. Only instantaneous `available` was meaningful.
		 * The wire layout is unchanged -- same field, same 140-byte
		 * kind-8 payload -- only the semantics are repaired.
		 */
		window_low = (uint16_t)atomic_set(&pool_low_water[count],
						  (atomic_val_t)available);
		out[count] = (bsf_net_buf_pool_usage_t) {
			.name_hash = pool_name_hash(pool->name),
			.available = available,
			.low_water = window_low,
		};
		count++;
	}
	return count;
}

static char device_name[8];
static uint16_t node_identity;
static uint8_t parser_frame[BSL_RELAY_FRAME_MAX];
static size_t parser_position;
static size_t parser_expected;
static char cached_tag_cfg[BSL_RELAY_PAYLOAD_MAX + 1u];
K_MUTEX_DEFINE(cached_tag_cfg_lock);

/*
 * The trajectory ring. Retained across the soft reset the recovery path
 * performs -- nRF52840 RAM keeps its contents through watchdog, SYSRESETREQ,
 * pin and lockup resets; only a power-on or brownout reset loses them. The
 * same mechanism already carries retained_stall.first_snapshot.
 */
__attribute__((section(".noinit"))) static struct bsf_stall_ring stall_ring;
static struct bsf_stall_ring_view stall_ring_view;
static struct k_spinlock stall_ring_lock;
static uint8_t stall_ring_boot_result;

/*
 * Instantaneous free counts only. Deliberately NOT sample_pool_usage(): that
 * one takes and re-arms the kind-8 low-water window, and a 20 Hz sampler would
 * shred the 1 Hz record's meaning.
 */
static uint8_t sample_pool_available(uint8_t *out)
{
	uint8_t count = 0u;

	/*
	 * No outer declaration: STRUCT_SECTION_FOREACH declares its own
	 * iterator, and sample_pool_usage()'s spare one is the source of the
	 * only -Wunused-variable warning left in this file.
	 */
	STRUCT_SECTION_FOREACH(net_buf_pool, pool) {
		uint32_t available = (uint32_t)atomic_get(&pool->avail_count);

		if (count < BSF_STALL_RING_POOL_SLOTS) {
			out[count] = (uint8_t)MIN(available, (uint32_t)UINT8_MAX);
		}
		/* Keep counting past the slots so the wire carries the REAL pool
		 * count and a decoder can see that it was truncated. */
		if (count < UINT8_MAX) {
			count++;
		}
	}
	return count;
}

/*
 * Sampled from the k_timer expiry, i.e. the system-clock ISR.
 *
 * The context matters more than the contents. D1's watchdog argument proves
 * the system workqueue kept running through every stall, but a workqueue is
 * still a thread: it can be blocked by any work item ahead of it, and a
 * context that can block is a context that can stop sampling. The timer
 * expiry cannot block -- blocking is illegal there by construction -- so it
 * keeps sampling through a deadlock of the publisher, the notify worker, the
 * BT RX/TX threads, or the system workqueue itself.
 *
 * It does not feed the watchdog, and must not: the watchdog's diagnostic value
 * comes entirely from being fed only by a context that has to run a full body
 * to completion.
 *
 * Everything read here is a single word or an atomic, and the only write is a
 * 40-byte structure copy under a spinlock held for that copy alone.
 */
/* v43: shared reboot budget, defined with the corpse machinery below. */
#define BSF_REBOOT_OWNER_RING_FWD 1u
static bool bsf_reboot_budget_take(uint32_t owner);

static void stall_ring_sample(struct k_timer *timer)
{
	ARG_UNUSED(timer);
	uint32_t now_ms = k_uptime_get_32();
	uint32_t entry_ms = retained_stall.entry_ms;
	uint32_t stream = retained_stall.in_call_stream;
	bool connected = atomic_get(&ble_connected) != 0;
	bool data_sub = atomic_get(&data_subscribed) != 0;
	bool telemetry_sub = atomic_get(&telemetry_subscribed) != 0;
	uint32_t age = stream != 0u ? (now_ms - entry_ms) : 0u;
	bsf_stall_ring_entry_t entry = {
		.uptime_ms = now_ms,
		.producer_heartbeat = (uint32_t)atomic_get(&producer_heartbeat),
		.entry_count = retained_stall.entry_count,
		.exit_count = retained_stall.exit_count,
		.in_call_age_ms = (uint16_t)MIN(age, (uint32_t)UINT16_MAX),
		.in_call_stream = (uint8_t)stream,
		.queue_depth_ctl =
			(uint8_t)MIN(k_msgq_num_used_get(&q_ctl), 255u),
		.queue_depth_uwb =
			(uint8_t)MIN(k_msgq_num_used_get(&q_uwb), 255u),
		.queue_depth_imu =
			(uint8_t)MIN(k_msgq_num_used_get(&q_imu), 255u),
	};

	entry.flags = (uint8_t)((connected ? BSF_RING_FLAG_CONNECTED : 0u) |
				(data_sub ? BSF_RING_FLAG_DATA_SUB : 0u) |
				(telemetry_sub ? BSF_RING_FLAG_TELEMETRY_SUB : 0u) |
				(atomic_get(&notify_in_call) != 0 ?
					 BSF_RING_FLAG_NOTIFY_IN_CALL : 0u) |
				(atomic_get(&notify_fast_drop) != 0 ?
					 BSF_RING_FLAG_FAST_DROP : 0u) |
				(atomic_get(&stall_recovery_pending) != 0 ?
					 BSF_RING_FLAG_RECOVERY_ARMED : 0u));
	entry.pool_count = sample_pool_available(entry.pool_avail);
	/*
	 * The detector's inputs, so a retrieved ring can answer why it did or did
	 * not act. `armed` needs subscribed_notify_ok >= STALL_ARM_NOTIFY_OK, the
	 * dwell lives in the detector's own frozen_ms, and the alarm block is gated
	 * on retained_stall.alarm_reason. N6 had none of these.
	 */
	entry.subscribed_notify_ok =
		(uint32_t)atomic_get(&subscribed_notify_ok);
	entry.detector_frozen_ms =
		(uint16_t)MIN(stall_detector.frozen_ms, (uint32_t)UINT16_MAX);
	entry.alarm_reason = (uint8_t)retained_stall.alarm_reason;
	entry.alarm_count = (uint8_t)MIN(retained_stall.alarm_count, 255u);

	k_spinlock_key_t key = k_spin_lock(&stall_ring_lock);
	bool reset_now;

	(void)bsf_stall_ring_push(&stall_ring, &entry,
				  connected && data_sub && telemetry_sub);
	/*
	 * Claimed inside the same critical section that may have raised it, so the
	 * freeze and the claim cannot interleave with another sample. The reset
	 * itself is issued outside the lock.
	 */
	reset_now = bsf_stall_ring_take_reset(&stall_ring);
	k_spin_unlock(&stall_ring_lock, key);

	/*
	 * v43: ONE reboot budget, shared with the BT RX monitor (brief section
	 * 3). take_reset() above still consumes the ring's own one-shot claim --
	 * that is what keeps the freeze bookkeeping honest -- but the actual
	 * reboot is granted by the shared budget, and the monitor may already
	 * have spent it. Precedence is stated at bsf_reboot_budget_take(): the
	 * monitor wins, because its corpse embeds this ring's tail, so yielding
	 * here loses nothing that the corpse does not already carry.
	 */
	if (reset_now && !bsf_reboot_budget_take(BSF_REBOOT_OWNER_RING_FWD)) {
		reset_now = false;
	}

	if (reset_now) {
		/*
		 * H1. The ring is frozen at this point -- take_reset() refuses
		 * otherwise -- so the evidence is already safe. sys_reboot() goes
		 * through NVIC_SystemReset(), which RETAINS .noinit, which is the
		 * whole reason this is worth doing: it is the one reset the board
		 * can give itself that does not destroy the trajectory. A brownout
		 * cannot, the detector did not, and RECONNECT only removed the
		 * board from the fleet.
		 *
		 * Legal from an ISR: sys_reboot() does not schedule or block.
		 */
		sys_reboot(SYS_REBOOT_COLD);
	}
}

K_TIMER_DEFINE(stall_ring_timer, stall_ring_sample, NULL);

static bool stall_ring_latch(uint8_t reason, uint32_t now_ms)
{
	k_spinlock_key_t key = k_spin_lock(&stall_ring_lock);
	bool fired = bsf_stall_ring_freeze(&stall_ring, reason, now_ms);

	k_spin_unlock(&stall_ring_lock, key);
	return fired;
}

/*
 * ===========================================================================
 * v43 BT RX wedge self-capture (batch v43_selfcapture_20260807)
 * ===========================================================================
 *
 * Storage for the stage instrumentation declared in bsf_bt_stage.h. The writer
 * is the patched Bluetooth host running on the BT RX workqueue; everything here
 * is a reader except these definitions.
 */
struct bsf_bt_trace_entry bsf_bt_trace[BSF_BT_TRACE_ENTRIES];
volatile uint32_t bsf_bt_trace_head;
volatile uint32_t bsf_bt_stage_seq;
volatile uint32_t bsf_bt_stage_cycles;
volatile uint32_t bsf_bt_stage_arg;
volatile uint16_t bsf_bt_stage_id;
volatile uint32_t bsf_bt_rx_thread;
volatile uint32_t bsf_bt_stage_max[BSF_BT_STAGE__COUNT];

#define BSF_CORPSE_MAGIC        0x34335043u   /* 'CP43' */
/*
 * SCHEMA MOVES WITH THE LAYOUT. v44 appended 7 stages, and stage_max[] is sized
 * BSF_BT_STAGE__COUNT, so sizeof(bsf_corpse_t) went 812 -> 840 and every field
 * after stage_max shifted. Two different layouts must never claim the same
 * schema -- that is the same discipline the ring follows (v3 kept decodable,
 * v4 announced), and skipping it here would have let a v44 corpse be decoded
 * with v43 offsets into plausible-looking nonsense.
 */
#define BSF_CORPSE_SCHEMA       2u
#define BSF_CORPSE_TRACE_KEEP   32u
#define BSF_CORPSE_RING_KEEP    6u
#define BSF_CORPSE_PAGE_FORM    0xC3u         /* != any ring entries count (<=5) */
#define BSF_CORPSE_PAGE_DATA    220u
#define BSF_CORPSE_VIEW_TTL_MS  30000u

#define BSF_CORPSE_TRIGGER_MONITOR    1u
#define BSF_CORPSE_TRIGGER_ARTIFICIAL 2u

/* Reboot-budget owners -- see bsf_reboot_budget_take(). */
#define BSF_REBOOT_OWNER_NONE   0u
#define BSF_REBOOT_OWNER_RING   1u
#define BSF_REBOOT_OWNER_BTRX   2u

/*
 * The corpse. Laid out for the decoder, so field order and sizes are wire
 * contract: tools/bt_corpse_decode.py mirrors this exactly.
 *
 * `valid` is deliberately the LAST member and is written last, after the CRC,
 * so a reset that lands mid-capture leaves a record that fails validation
 * rather than one that passes with half its fields uninitialised.
 */
typedef struct __packed {
	uint32_t magic;
	uint16_t schema;
	uint16_t length;          /* bytes from `crc_start` to end of ring[]    */
	uint32_t crc32;           /* over [crc_start .. end of ring[]]          */

	/* --- crc_start --- */
	uint32_t fw_marker_hash;
	uint32_t node_identity;
	uint32_t uptime_ms;
	uint32_t boot_reset_reason;
	uint32_t corpse_seq;
	uint16_t wedge_count;
	uint16_t trigger;

	uint16_t stage;
	uint16_t stage_pad;
	uint32_t stage_seq;
	uint32_t stage_age_ms;
	uint32_t stage_arg;

	uint32_t rx_thread_addr;
	uint32_t rx_thread_sp;
	uint32_t rx_stack_size;
	uint32_t rx_stack_unused;
	uint8_t  rx_thread_state;
	uint8_t  rx_thread_prio;
	uint8_t  rx_capture_ok;
	uint8_t  pad0;

	struct bsf_bt_corpse_conn conn;

	uint32_t wdt_feed_count;      /* system-workqueue heartbeat */
	uint32_t notify_ok;
	uint32_t producer_seq;
	uint32_t ring_writes;
	uint32_t stage_max[BSF_BT_STAGE__COUNT];

	uint16_t trace_entries;
	uint16_t ring_entries;
	struct bsf_bt_trace_entry trace[BSF_CORPSE_TRACE_KEEP];
	bsf_stall_ring_entry_t ring[BSF_CORPSE_RING_KEEP];
	/* --- crc_end --- */

	uint32_t valid;           /* BSF_CORPSE_MAGIC when complete; written last */
} bsf_corpse_t;

typedef struct __packed {
	uint8_t  wire_tag;        /* 3: dk >= v35 hex-dumps anything >= v41 tag */
	uint8_t  page;
	uint8_t  pages;
	uint8_t  form;            /* BSF_CORPSE_PAGE_FORM */
	uint16_t total_len;
	uint16_t offset;
	uint16_t crc16;
	uint16_t seq;
	uint8_t  data[BSF_CORPSE_PAGE_DATA];
} bsf_corpse_page_t;

/*
 * The corpse must still fit the export walk. Page count is computed, so this is
 * not a truncation today -- it is a tripwire: the next field added without
 * thinking pushes the corpse past four pages, lengthening every retrieval and
 * every 90 s sweep silently. Growth past this is a deliberate act, not a
 * side effect. Same shape as the schema rule above: close the class, not the
 * instance.
 */
#define BSF_CORPSE_EXPORT_PAGES 4u
_Static_assert(sizeof(bsf_corpse_t) <= BSF_CORPSE_EXPORT_PAGES * BSF_CORPSE_PAGE_DATA,
	       "bsf_corpse_t no longer fits the 4-page export budget: either "
	       "shrink it, or raise BSF_CORPSE_EXPORT_PAGES deliberately and "
	       "re-check the retrieval walk and the 90 s sweep cost");

_Static_assert(sizeof(bsf_corpse_page_t) == sizeof(bsf_stall_status_t),
	       "a corpse page must be the same length as every other form of "
	       "this characteristic, or the DK's length check rejects it");

/*
 * Retained across the software reset, exactly like the ring. `.noinit` survives
 * sys_reboot()/NVIC_SystemReset(), the watchdog, and pin and lockup resets. It
 * does NOT survive power-on or brownout, which is precisely why nothing here is
 * trusted without magic + CRC.
 */
__attribute__((section(".noinit"))) static bsf_corpse_t retained_corpse;

/* Shared reboot budget, also retained. See bsf_reboot_budget_take(). */
__attribute__((section(".noinit"))) static struct {
	uint32_t magic;
	uint32_t taken;
	uint32_t owner;
	uint32_t corpse_seq;
} retained_reboot;

static struct bsf_stall_ring_view corpse_view;
static bool corpse_present;        /* a validated corpse is awaiting export */
static uint8_t corpse_pages_total;

/*
 * v45 uses its own page view, alongside -- not instead of -- the v43/v44 one.
 * Two independent selections cannot collide because the read path checks them
 * in a fixed order and each has its own TTL, and keeping them separate is what
 * lets a v44 corpse and a v45 corpse coexist on the same board after a
 * mid-campaign OTA.
 */
static struct bsf_stall_ring_view v45_view;
#define BSF_V45_PAGE_FORM 0xC5u    /* != BSF_CORPSE_PAGE_FORM (0xC3) */

/*
 * ONE reboot budget, shared between the two authorities (brief section 3).
 *
 * v42's ring ISR already resets the board once per power cycle after freezing
 * the ring. v43 adds the BT RX monitor, which also wants a reset. Two
 * independent one-shot budgets would be two resets per power cycle, and the
 * second would land on top of the first one's evidence.
 *
 * PRECEDENCE: the BT RX monitor wins. It is the authority this round exists
 * for, and its corpse EMBEDS the ring tail -- so when the monitor takes the
 * budget nothing the ring would have reported is lost, whereas the reverse is
 * not true. The ring ISR therefore yields whenever the monitor has already
 * taken it, and the monitor is allowed to take it even after the ring has
 * frozen (freezing costs no budget; only rebooting does).
 */
static bool bsf_reboot_budget_take(uint32_t owner)
{
	unsigned int key = irq_lock();
	bool granted = false;

	if (retained_reboot.magic != BSF_CORPSE_MAGIC) {
		retained_reboot.magic = BSF_CORPSE_MAGIC;
		retained_reboot.taken = 0u;
		retained_reboot.owner = BSF_REBOOT_OWNER_NONE;
		retained_reboot.corpse_seq = 0u;
	}
	if (retained_reboot.taken == 0u) {
		retained_reboot.taken = 1u;
		retained_reboot.owner = owner;
		granted = true;
	}
	irq_unlock(key);
	return granted;
}

static uint32_t bsf_fnv1a(const char *s)
{
	uint32_t h = 2166136261u;

	while (*s != '\0') {
		h ^= (uint8_t)(*s++);
		h *= 16777619u;
	}
	return h;
}

/*
 * The v45 runtime's window onto application state.
 *
 * bsf_v45.c deliberately owns no application state: it is the detector and the
 * capture routine, and both must be readable in isolation on the host. Every
 * value it needs comes through here, from the one file that legitimately owns
 * them. `extern`-declared in bsf_v45.c, defined here -- the dependency points
 * the way round that lets the policy be unit-tested without a kernel.
 */
void bsf_v45_env_get(struct bsf_v45_env *out)
{
	uint32_t exits = (uint32_t)atomic_get(&bsf_v45_cnt.notify_exit_total);
	uint32_t base = (uint32_t)atomic_get(&v45_exit_base);

	*out = (struct bsf_v45_env){
		.node_identity = node_identity,
		.fw_marker_hash = bsf_fnv1a(BSF_FW_MARKER),
		.boot_reset_reason = boot_reset_reason,
		.epoch = (uint32_t)atomic_get(&v45_epoch),
		.connected_at_ms = (uint32_t)atomic_get(&v45_connected_at_ms),
		/*
		 * producer_seq is SUBMISSION-stage and is used ONLY as an
		 * is-the-board-alive gate, never as a trigger. COUNTER_SEMANTICS
		 * is explicit that submission counters gate nothing, and the two
		 * watermarks that do trigger are both exit/completion stage.
		 */
		.producer_seq = (uint32_t)atomic_get(&valid_frames),
		.publisher_count = (uint32_t)atomic_get(&publisher_count),
		.wdt_feed_count = (uint32_t)atomic_get(&watchdog_feed_count),
		.notify_timeout_drop_total =
			(uint32_t)atomic_get(&notify_timeout_drop[0]) +
			(uint32_t)atomic_get(&notify_timeout_drop[1]) +
			(uint32_t)atomic_get(&notify_timeout_drop[2]),
		.notify_exits_this_epoch = exits - base,
		.notify_ok_total = (uint32_t)atomic_get(&notify_ok),
		.notconn_streak = (uint32_t)atomic_get(&notify_notconn_streak),
		.connected = atomic_get(&ble_connected) != 0,
		.data_subscribed = atomic_get(&data_subscribed) != 0,
		.telemetry_subscribed = atomic_get(&telemetry_subscribed) != 0,
	};
}

#if defined(CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS)
/*
 * OTA in progress, from the horse's mouth.
 *
 * The detector must not fire during a DFU, and this is not a theoretical
 * concern: a 4.1 s bt_gatt_notify() was measured during one, and an image
 * upload monopolises the same ATT bearer the notifications use. Guessing at it
 * from pool occupancy would be an indirect, racy proxy; MCUmgr will simply tell
 * us.
 *
 * DFU_CHUNK refreshes a keepalive rather than latching, so an OTA that is
 * abandoned mid-transfer -- host crash, link loss -- cannot leave the detector
 * disarmed for the rest of the run. That failure mode is more likely than a
 * clean STOPPED.
 */
static enum mgmt_cb_return v45_dfu_cb(uint32_t event,
				      enum mgmt_cb_return prev_status,
				      int32_t *rc, uint16_t *group,
				      bool *abort_more, void *data,
				      size_t data_size)
{
	ARG_UNUSED(prev_status); ARG_UNUSED(rc); ARG_UNUSED(group);
	ARG_UNUSED(abort_more); ARG_UNUSED(data); ARG_UNUSED(data_size);

	switch (event) {
	case MGMT_EVT_OP_IMG_MGMT_DFU_STARTED:
	case MGMT_EVT_OP_IMG_MGMT_DFU_CHUNK:
	case MGMT_EVT_OP_IMG_MGMT_DFU_PENDING:
		bsf_v45_ota_mark(true);
		break;
	case MGMT_EVT_OP_IMG_MGMT_DFU_STOPPED:
	case MGMT_EVT_OP_IMG_MGMT_DFU_CONFIRMED:
		bsf_v45_ota_mark(false);
		break;
	default:
		break;
	}
	return MGMT_CB_OK;
}

static struct mgmt_callback v45_dfu_callback = {
	.callback = v45_dfu_cb,
	.event_id = MGMT_EVT_OP_IMG_MGMT_ALL,
};
#endif /* CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS */

/*
 * Bump the incarnation. Called from connect, disconnect and unsubscribe.
 * bsf_v45_connection_epoch_changed() clears the detector's dwell; re-basing
 * v45_exit_base here is what makes "64 completed notifications" mean 64 IN THIS
 * INCARNATION rather than 64 since boot.
 */
static void v45_new_epoch(bool connected_now)
{
	uint32_t now_ms = k_uptime_get_32();
	uint32_t epoch = (uint32_t)atomic_inc(&v45_epoch) + 1u;

	atomic_set(&v45_exit_base,
		   atomic_get(&bsf_v45_cnt.notify_exit_total));
	if (connected_now) {
		atomic_set(&v45_connected_at_ms, (atomic_val_t)now_ms);
	}
	bsf_v45_connection_epoch_changed(epoch, now_ms);
}

/*
 * Locate the BT RX workqueue thread by name. `bt_workq` is static inside
 * hci_core.c with no accessor, so the application cannot name it directly --
 * which is exactly why no round has ever measured its stack. CONFIG_THREAD_NAME
 * and CONFIG_THREAD_MONITOR are both already enabled, so a name walk works and
 * needs no second SDK patch.
 */
static void bsf_bt_rx_thread_find_cb(const struct k_thread *thread, void *user)
{
	const char *name = k_thread_name_get((k_tid_t)thread);

	ARG_UNUSED(user);
	if (name != NULL && strcmp(name, "BT RX WQ") == 0) {
		bsf_bt_rx_thread = (uint32_t)(uintptr_t)thread;
	}
}

static void bsf_capture_corpse(uint16_t trigger, uint32_t now_ms)
{
	bsf_corpse_t *c = &retained_corpse;
	k_tid_t rx = (k_tid_t)(uintptr_t)bsf_bt_rx_thread;
	size_t unused = 0u;
	uint32_t seq = retained_reboot.corpse_seq + 1u;

	memset(c, 0, sizeof(*c));
	c->magic = BSF_CORPSE_MAGIC;
	c->schema = BSF_CORPSE_SCHEMA;

	c->fw_marker_hash = bsf_fnv1a(BSF_FW_MARKER);
	c->node_identity = node_identity;
	c->uptime_ms = now_ms;
	c->boot_reset_reason = boot_reset_reason;
	c->corpse_seq = seq;
	c->wedge_count = (uint16_t)seq;
	c->trigger = trigger;

	c->stage = bsf_bt_stage_id;
	c->stage_seq = bsf_bt_stage_seq;
	c->stage_arg = bsf_bt_stage_arg;
	c->stage_age_ms = k_cyc_to_ms_near32(k_cycle_get_32() - bsf_bt_stage_cycles);
	for (size_t i = 0; i < BSF_BT_STAGE__COUNT; ++i) {
		c->stage_max[i] = bsf_bt_stage_max[i];
	}

	if (rx != NULL) {
		c->rx_thread_addr = (uint32_t)(uintptr_t)rx;
		c->rx_thread_state = rx->base.thread_state;
		c->rx_thread_prio = (uint8_t)(int8_t)rx->base.prio;
		c->rx_thread_sp = (uint32_t)rx->callee_saved.psp;
		c->rx_stack_size = (uint32_t)rx->stack_info.size;
		if (k_thread_stack_space_get(rx, &unused) == 0) {
			c->rx_stack_unused = (uint32_t)unused;
		}
		c->rx_capture_ok = 1u;
	}

	/* Private host state, read inside the patched conn.c. Captured into an
	 * aligned local first: &c->conn is a packed member, and handing an
	 * under-aligned pointer to another translation unit is undefined.
	 */
	{
		struct bsf_bt_corpse_conn cc;

		bsf_bt_capture_conn(&cc);
		memcpy(&c->conn, &cc, sizeof(cc));
	}

	c->wdt_feed_count = (uint32_t)atomic_get(&watchdog_feed_count);
	c->notify_ok = (uint32_t)atomic_get(&notify_ok);
	c->producer_seq = (uint32_t)atomic_get(&valid_frames);

	/* Flight-recorder tail, oldest-first. */
	{
		uint32_t head = bsf_bt_trace_head;
		uint32_t n = MIN(head, (uint32_t)BSF_CORPSE_TRACE_KEEP);

		for (uint32_t i = 0; i < n; ++i) {
			uint32_t idx = head - n + i;

			c->trace[i] = bsf_bt_trace[idx & (BSF_BT_TRACE_ENTRIES - 1u)];
		}
		c->trace_entries = (uint16_t)n;
	}

	/*
	 * v42 ring tail. Freeze first so the trajectory stops advancing while
	 * it is copied -- a corpse carrying the ring tail is strictly better
	 * than either record alone, which is why both authorities are kept.
	 */
	{
		k_spinlock_key_t key = k_spin_lock(&stall_ring_lock);
		uint32_t count = stall_ring.count;
		uint32_t n = MIN(count, (uint32_t)BSF_CORPSE_RING_KEEP);

		(void)bsf_stall_ring_freeze(&stall_ring, BSF_RING_FREEZE_NO_EXIT,
					    now_ms);
		c->ring_writes = stall_ring.writes_total;
		for (uint32_t i = 0; i < n; ++i) {
			uint32_t idx = stall_ring.head + BSF_STALL_RING_CAPACITY -
				       n + i;

			c->ring[i] = stall_ring.entries[idx % BSF_STALL_RING_CAPACITY];
		}
		c->ring_entries = (uint16_t)n;
		k_spin_unlock(&stall_ring_lock, key);
	}

	c->length = (uint16_t)(offsetof(bsf_corpse_t, valid) -
			       offsetof(bsf_corpse_t, fw_marker_hash));
	c->crc32 = crc32_ieee((const uint8_t *)&c->fw_marker_hash, c->length);
	retained_reboot.corpse_seq = seq;

	/* LAST. Everything above must be settled before this becomes true. */
	__DMB();
	c->valid = BSF_CORPSE_MAGIC;
	__DMB();
}

static bool bsf_corpse_validate(void)
{
	bsf_corpse_t *c = &retained_corpse;
	uint16_t want;

	if (c->magic != BSF_CORPSE_MAGIC || c->valid != BSF_CORPSE_MAGIC) {
		return false;
	}
	if (c->schema != BSF_CORPSE_SCHEMA) {
		return false;
	}
	want = (uint16_t)(offsetof(bsf_corpse_t, valid) -
			  offsetof(bsf_corpse_t, fw_marker_hash));
	if (c->length != want) {
		return false;
	}
	return c->crc32 == crc32_ieee((const uint8_t *)&c->fw_marker_hash,
				      c->length);
}

static void bsf_corpse_invalidate(void)
{
	retained_corpse.valid = 0u;
	retained_corpse.magic = 0u;
	corpse_present = false;
	bsf_stall_ring_view_clear(&corpse_view);
}

static uint8_t bsf_corpse_page_count(void)
{
	uint32_t total = (uint32_t)sizeof(bsf_corpse_t);

	return (uint8_t)((total + BSF_CORPSE_PAGE_DATA - 1u) /
			 BSF_CORPSE_PAGE_DATA);
}

static int bsf_corpse_render_page(uint8_t page, bsf_corpse_page_t *out)
{
	const uint8_t *src = (const uint8_t *)&retained_corpse;
	uint32_t total = (uint32_t)sizeof(bsf_corpse_t);
	uint32_t off = (uint32_t)page * BSF_CORPSE_PAGE_DATA;
	uint32_t n;

	if (!corpse_present || page >= bsf_corpse_page_count()) {
		return -EINVAL;
	}
	memset(out, 0, sizeof(*out));
	n = MIN((uint32_t)BSF_CORPSE_PAGE_DATA, total - off);

	out->wire_tag = BSF_STALL_RING_VERSION_V41;
	out->page = page;
	out->pages = bsf_corpse_page_count();
	out->form = BSF_CORPSE_PAGE_FORM;
	out->total_len = (uint16_t)total;
	out->offset = (uint16_t)off;
	out->seq = (uint16_t)retained_corpse.corpse_seq;
	memcpy(out->data, &src[off], n);
	out->crc16 = bsf_stall_ring_crc16(out->data, BSF_CORPSE_PAGE_DATA);
	return 0;
}

/*
 * The monitor. A dedicated thread -- deliberately not the BT RX workqueue and
 * not the system workqueue, and it depends on nothing that BLE depends on, so
 * it stays alive through exactly the failure it exists to record.
 *
 * TRIGGER: a non-quiescent BT RX stage whose sequence counter has not advanced
 * for BSF_BT_WEDGE_MS. It deliberately does NOT trigger on "notifications
 * stopped" -- that readmits the producer, RF, connection scheduling, the
 * central and the application, every one of which has already been excluded and
 * every one of which is a false-positive source this criterion does not have.
 */
#define BSF_BT_MONITOR_TICK_MS 1000u

/*
 * 20 s, NOT 5 s. Raised for v44, and the reason is a direct consequence of
 * bracketing rx_work_handler().
 *
 * v43 watched only conn.c, where every marked region completes in single-digit
 * CPU cycles, so 5 s had six orders of magnitude of margin. v44's bracket also
 * covers the HCI EVENT arm -- and event handlers issue SYNCHRONOUS HCI
 * commands. `hci_disconn_complete()` is one: it calls bt_hci_cmd_send_sync(),
 * which waits on sync_sem for HCI_CMD_TIMEOUT = K_SECONDS(10) (hci_core.c:100).
 *
 * So after v44 the BT RX WQ can LEGITIMATELY sit inside the bracket for ten
 * seconds on any slow disconnect. A 5 s threshold would not be a marginal
 * risk -- it would fire on healthy boards, fleet-wide, and each false positive
 * spends the one-per-power-cycle reboot budget on nothing. Two earlier rounds
 * were already lost to false triggers; this would have been the third and the
 * largest.
 *
 * 20 s is chosen against both bounds:
 *   - 2x the 10 s HCI command timeout, so a legitimate slow command is clear;
 *   - 10 s BELOW the 30 s ATT response timeout (att.c BT_ATT_TIMEOUT), so a
 *     wedge in the ATT allocation is still captured WHILE STUCK, before the
 *     timeout unwinds it and destroys the evidence.
 *
 * Stage 2 measures actual dwell at the RX_WORK_ENTER->EXIT level across BOTH
 * arms -- ACL and event -- and the threshold moves again if anything observed
 * approaches it.
 *
 * ACCEPTED COST, RECORDED SO IT IS NOT A SILENT LOSS.
 * The v42 ring holds BSF_STALL_RING_CAPACITY(200) x BSF_STALL_RING_PERIOD_MS(50)
 * = 10 s. It is frozen when the corpse is captured, i.e. at onset + threshold.
 * At 5 s the frozen ring spanned [onset-5s, onset+5s] and COVERED THE ONSET.
 * At 20 s it spans [onset+10s, onset+20s] and DOES NOT.
 *
 * That is accepted rather than fixed, because v44's primary evidence is the
 * STAGE, not the ring: bsf_bt_stage_id says where the thread is parked,
 * k_work_busy_get() gives tx_complete_work's real state, and the bt_conn
 * fields give the connection's. The ring was always a secondary trajectory.
 *
 * The alternatives were weighed and rejected FOR NOW, not overlooked:
 *   - 800 entries would give a 40 s span for +32 KB of the ~146 KB free. Real
 *     but bulky, and it buys a trajectory we are not currently reading.
 *   - Freezing the ring on a lower threshold than the reset would freeze it on
 *     every legitimate slow disconnect -- the exact false-positive class the
 *     20 s threshold exists to avoid.
 *   - Coarsening the period to 200 ms would give 40 s at zero RAM cost, but
 *     changes the geometry stamp and invalidates every retained ring.
 * If the ring tail ever becomes load-bearing, the 800-entry option is the one.
 */
#define BSF_BT_WEDGE_MS        20000u
#define BSF_BT_MONITOR_STACK   1024
#define BSF_BT_MONITOR_PRIO    6

static atomic_t corpse_force_trigger;

static void bsf_bt_monitor(void *a, void *b, void *c)
{
	uint32_t last_seq = 0u;
	uint32_t unchanged_ms = 0u;
	bool armed = false;

	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	k_thread_foreach_unlocked(bsf_bt_rx_thread_find_cb, NULL);
	LOG_INF("BT RX monitor started thread=%08x tick_ms=%u wedge_ms=%u",
		(unsigned int)bsf_bt_rx_thread, BSF_BT_MONITOR_TICK_MS,
		BSF_BT_WEDGE_MS);

	while (true) {
		uint32_t seq;
		uint16_t stage;
		uint32_t now_ms;
		bool forced;

		k_sleep(K_MSEC(BSF_BT_MONITOR_TICK_MS));

		if (bsf_bt_rx_thread == 0u) {
			k_thread_foreach_unlocked(bsf_bt_rx_thread_find_cb, NULL);
		}

		seq = bsf_bt_stage_seq;
		stage = bsf_bt_stage_id;
		now_ms = (uint32_t)k_uptime_get();
		forced = atomic_cas(&corpse_force_trigger, 1, 0);

		if (seq != last_seq) {
			last_seq = seq;
			unchanged_ms = 0u;
			armed = true;
		} else if (armed && !BSF_BT_STAGE_IS_QUIESCENT(stage)) {
			unchanged_ms += BSF_BT_MONITOR_TICK_MS;
		} else {
			unchanged_ms = 0u;
		}

		if (!forced && unchanged_ms < BSF_BT_WEDGE_MS) {
			continue;
		}

		/*
		 * Capture BEFORE recovery, always. If the budget is gone the
		 * corpse is still written and still exported after the next
		 * reset from any source -- losing the reset must not also lose
		 * the evidence.
		 */
		bsf_capture_corpse(forced ? BSF_CORPSE_TRIGGER_ARTIFICIAL
					  : BSF_CORPSE_TRIGGER_MONITOR,
				   now_ms);
		LOG_ERR("BT RX WEDGE stage=%u seq=%u age_ms=%u forced=%u -- corpse captured",
			stage, seq, unchanged_ms, forced ? 1u : 0u);

		if (bsf_reboot_budget_take(BSF_REBOOT_OWNER_BTRX)) {
			/*
			 * Software reset: NVIC_SystemReset() retains .noinit,
			 * which is the whole point. Not the watchdog -- it is
			 * fed from telemetry_work_handler on the system
			 * workqueue, which stays alive through this failure and
			 * is therefore structurally blind to it.
			 */
			sys_reboot(SYS_REBOOT_COLD);
		}
		LOG_ERR("BT RX WEDGE reboot budget already spent owner=%u; corpse retained",
			retained_reboot.owner);
		unchanged_ms = 0u;
		armed = false;
	}
}

K_THREAD_DEFINE(bt_monitor_thread_id, BSF_BT_MONITOR_STACK,
		bsf_bt_monitor, NULL, NULL, NULL,
		BSF_BT_MONITOR_PRIO, 0, 0);

/*
 * One read, two wire forms, one length. Which form is returned was decided by
 * an earlier control write; the read itself never blocks, never allocates and
 * never advances any state, so re-reading a page is free and an abandoned
 * retrieval reverts on its own once the selection ages out.
 */
static ssize_t stall_status_read(struct bt_conn *conn,
				 const struct bt_gatt_attr *attr,
				 void *buf, uint16_t len, uint16_t offset)
{
	uint32_t now_ms = k_uptime_get_32();
	uint8_t page = 0u;
	uint8_t v45_page = 0u;

	/*
	 * Fourth form, checked FIRST: a v45 corpse page. Same 232-byte envelope
	 * as every other form of this characteristic -- the DK's length check
	 * is satisfied and the master needs no change -- distinguished only by
	 * `form`. The payload is a slice of a flat image the host reassembles
	 * and CRC-checks; re-reading a slice is byte-identical, so retrieval is
	 * idempotent and restartable exactly like the ring's.
	 */
	if (bsf_stall_ring_view_page(&v45_view, now_ms, BSF_CORPSE_VIEW_TTL_MS,
				     &v45_page)) {
		uint32_t total = bsf_v45_image_len();
		uint32_t off = (uint32_t)v45_page * BSF_CORPSE_PAGE_DATA;

		if (bsf_v45_present() && off < total) {
			bsf_corpse_page_t rendered;

			memset(&rendered, 0, sizeof(rendered));
			rendered.wire_tag = BSF_STALL_RING_VERSION_V41;
			rendered.page = v45_page;
			rendered.pages = (uint8_t)MIN(
				(total + BSF_CORPSE_PAGE_DATA - 1u) /
					BSF_CORPSE_PAGE_DATA,
				255u);
			rendered.form = BSF_V45_PAGE_FORM;
			rendered.total_len = (uint16_t)total;
			rendered.offset = (uint16_t)off;
			rendered.seq = (uint16_t)bsf_v45_seq();
			(void)bsf_v45_image_read(off, rendered.data,
						 BSF_CORPSE_PAGE_DATA);
			rendered.crc16 = bsf_stall_ring_crc16(
				rendered.data, BSF_CORPSE_PAGE_DATA);
			return bt_gatt_attr_read(conn, attr, buf, len, offset,
						 &rendered, sizeof(rendered));
		}
		/* Past the end, or no corpse: fall through. */
	}

	/*
	 * Third form: a v43 corpse page. Checked before the ring because a
	 * corpse is strictly more valuable than a live ring page and its
	 * selection is only ever set deliberately by `CORPSE PAGE=`.
	 */
	if (bsf_stall_ring_view_page(&corpse_view, now_ms,
				     BSF_CORPSE_VIEW_TTL_MS, &page)) {
		bsf_corpse_page_t rendered;

		if (bsf_corpse_render_page(page, &rendered) == 0) {
			return bt_gatt_attr_read(conn, attr, buf, len, offset,
						 &rendered, sizeof(rendered));
		}
		/* Past the end, or no corpse: fall through. */
	}

	if (bsf_stall_ring_view_page(&stall_ring_view, now_ms,
				     BSF_STALL_RING_VIEW_TTL_MS, &page)) {
		bsf_stall_ring_page_t rendered;
		k_spinlock_key_t ring_key = k_spin_lock(&stall_ring_lock);
		int err = bsf_stall_ring_render_page(&stall_ring, page,
						     &rendered);

		k_spin_unlock(&stall_ring_lock, ring_key);
		if (err == 0) {
			return bt_gatt_attr_read(conn, attr, buf, len, offset,
						 &rendered, sizeof(rendered));
		}
		/* Past the end: fall through to the status snapshot. */
	}

	bsf_stall_status_t copy;
	k_spinlock_key_t key = k_spin_lock(&stall_status_lock);

	copy = stall_status;
	k_spin_unlock(&stall_status_lock, key);
	return bt_gatt_attr_read(conn, attr, buf, len, offset,
				 &copy, sizeof(copy));
}

static bsf_stall_status_t make_stall_status(uint32_t now_ms, bool armed,
					    uint8_t reason)
{
	uint32_t entry_ms = retained_stall.entry_ms;
	bsf_stall_status_t value = {
		.version = BSF_STALL_STATUS_VERSION,
		.reason = reason,
		.in_call_stream = (uint8_t)retained_stall.in_call_stream,
		.armed = armed ? 1u : 0u,
		.sample_uptime_ms = now_ms,
		.entry_count = retained_stall.entry_count,
		.exit_count = retained_stall.exit_count,
		.entry_ms = entry_ms,
		.exit_ms = retained_stall.exit_ms,
		.in_call_age_ms = retained_stall.in_call_stream != 0u ?
			(now_ms - entry_ms) : 0u,
		.last_return_code = (int32_t)retained_stall.last_return_code,
		.return_ok = (uint32_t)atomic_get(&notify_ok),
		.return_nomem = (uint32_t)atomic_get(&notify_rc_nomem),
		.return_notconn = (uint32_t)atomic_get(&notify_rc_notconn),
		.return_again = (uint32_t)atomic_get(&notify_rc_again),
		.return_other = (uint32_t)atomic_get(&notify_rc_other),
		.queue_depth_ctl = (uint16_t)k_msgq_num_used_get(&q_ctl),
		.queue_depth_uwb = (uint16_t)k_msgq_num_used_get(&q_uwb),
		.queue_depth_imu = (uint16_t)k_msgq_num_used_get(&q_imu),
		.q_drop_ctl = (uint32_t)atomic_get(&q_drop_ctl),
		.q_drop_uwb = (uint32_t)atomic_get(&q_drop_uwb),
		.q_drop_imu = (uint32_t)atomic_get(&q_drop_imu),
		.timeout_drop_ctl =
			(uint32_t)atomic_get(&notify_timeout_drop[NOTIFY_CTL]),
		.timeout_drop_uwb =
			(uint32_t)atomic_get(&notify_timeout_drop[NOTIFY_UWB]),
		.timeout_drop_imu =
			(uint32_t)atomic_get(&notify_timeout_drop[NOTIFY_IMU]),
		.producer_heartbeat =
			(uint32_t)atomic_get(&producer_heartbeat),
		.alarm_count = retained_stall.alarm_count,
		.alarm_timestamp_ms = retained_stall.alarm_timestamp_ms,
		.recovery_count = retained_stall.recovery_count,
		.pool_usage_enabled = 1u,
#if defined(CONFIG_BT_ATT_SENT_CB_AFTER_TX)
		.att_sent_cb_after_tx = 1u,
#endif
	};
	value.pool_count = sample_pool_usage(value.pools);

	return value;
}

static void publish_stall_status(const bsf_stall_status_t *value)
{
	k_spinlock_key_t key = k_spin_lock(&stall_status_lock);

	stall_status = *value;
	k_spin_unlock(&stall_status_lock, key);
}

static void stall_recovery_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	atomic_clear(&stall_recovery_pending);
	LOG_ERR("STALL RECOVERY reboot=%u/%u reason=%u",
		retained_stall.recovery_count,
		STALL_MAX_RECOVERIES_PER_POWER,
		retained_stall.alarm_reason);
	sys_reboot(SYS_REBOOT_COLD);
}

K_WORK_DELAYABLE_DEFINE(stall_recovery_work, stall_recovery_work_handler);

static int uart_send_relay(uint16_t correlation, const char *line);
static int relay_pending_reserve(uint16_t correlation);
static bool relay_pending_remove(uint16_t correlation);
static void tag_reset_recovery_work_handler(struct k_work *work);
K_WORK_DEFINE(tag_reset_recovery_work, tag_reset_recovery_work_handler);
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
		atomic_inc(&producer_heartbeat);
		return enqueue_uwb_record(record, len);
	}
	if (bytes[1] == BSF_BLE_KIND_IMU) {
		atomic_inc(&producer_heartbeat);
		return enqueue_imu_record(record, len);
	}
	return enqueue_ctl_record(PUBLISH_ATTRIBUTE_DATA, record, len);
}

static enum notify_stream record_stream(const void *record, size_t len)
{
	const uint8_t *bytes = record;

	if (len >= 2u && bytes[1] == BSF_BLE_KIND_UWB) {
		return NOTIFY_UWB;
	}
	if (len >= 2u && bytes[1] == BSF_BLE_KIND_IMU) {
		return NOTIFY_IMU;
	}
	return NOTIFY_CTL;
}

static void record_notify_timeout(enum notify_stream stream)
{
	atomic_inc(&notify_timeout_drop[stream]);
	atomic_set(&notify_fast_drop, 1);
}

static void publisher_notify(enum publish_attribute attribute,
			     const void *record, size_t len)
{
	const struct bt_gatt_attr *attr;
	atomic_t *subscribed;
	enum notify_stream stream = record_stream(record, len);
	k_timeout_t wait = atomic_get(&notify_fast_drop) != 0 ?
		K_NO_WAIT : K_MSEC(NOTIFY_ACCEPT_TIMEOUT_MS);

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
	if (len > sizeof(notify_job.payload) ||
	    k_sem_take(&notify_idle_sem, wait) != 0) {
		record_notify_timeout(stream);
		return;
	}
	notify_job.attr = attr;
	notify_job.len = (uint16_t)len;
	notify_job.stream = (uint8_t)stream;
	memcpy(notify_job.payload, record, len);
	atomic_set(&notify_in_call, 1);
	retained_stall.entry_count++;
	retained_stall.entry_ms = (uint32_t)k_uptime_get();
	retained_stall.in_call_stream = (uint32_t)stream + 1u;
	k_sem_give(&notify_job_sem);
}

#if defined(CONFIG_BSF_V45_FAULT_INJECT)
/*
 * Fault injection 2 -- NOTIFY-WORKER HANG. Validation builds only.
 *
 * Blocks the notify worker on a private semaphore with the v45 ENTER already
 * recorded and no EXIT, which is the exact shape of the real phenotype
 * (STALL STATUS e > x) and freezes watermark A, notify_exit_total.
 *
 * WHY IT IS NOT REDUNDANT WITH `V45 LEAK`. The leak starves the singleton
 * sync_evt buffer and takes the whole BLE stack down with it: an ATT read is
 * then accepted on air and never answered. This one leaves the stack healthy
 * and stops only the application's notify path, so the two exercise the
 * detector against genuinely different mechanisms.
 *
 * HOW TO GET ARM A IN ISOLATION -- read this before using it. bt_gatt_notify()
 * has exactly ONE call site (below), so data, telemetry AND control replies all
 * flow through this thread. Hang it and nothing is transmitted, so
 * ncp_packet_total stops moving too and the trigger reports CAUSE_BOTH.
 * A GATT READ, however, is answered by the ATT/RX path and not by this thread.
 * So the host must poll a read -- `STALL READ` at ~1 Hz is what the bench uses
 * -- for the duration of the hang. That keeps watermark B advancing while A
 * freezes, and the cause comes back CAUSE_NOTIFY_EXIT. Without the polling the
 * result is still a valid trigger, just not an isolated arm.
 *
 * The arming is DELAYED by one second because the reply to `V45 HANG` is itself
 * a notification: arming synchronously would swallow the acknowledgement of the
 * command that armed it.
 */
K_SEM_DEFINE(v45_inject_hang_sem, 0, 1);
static atomic_t v45_inject_hang;

static void v45_inject_hang_arm_fn(struct k_work *work)
{
	ARG_UNUSED(work);
	atomic_set(&v45_inject_hang, 1);
	LOG_WRN("V45 INJECT notify-worker hang ARMED");
}
static K_WORK_DELAYABLE_DEFINE(v45_inject_hang_arm, v45_inject_hang_arm_fn);

static int bsf_v45_notify_hang(bool on)
{
	if (on) {
		if (atomic_get(&v45_inject_hang) != 0) {
			return -EALREADY;
		}
		k_work_reschedule(&v45_inject_hang_arm, K_MSEC(1000));
		return 0;
	}
	if (atomic_set(&v45_inject_hang, 0) == 0) {
		(void)k_work_cancel_delayable(&v45_inject_hang_arm);
		return -EALREADY;
	}
	(void)k_work_cancel_delayable(&v45_inject_hang_arm);
	k_sem_give(&v45_inject_hang_sem);   /* release a worker already parked */
	LOG_WRN("V45 INJECT notify-worker hang RELEASED");
	return 0;
}
#endif /* CONFIG_BSF_V45_FAULT_INJECT */

static void notify_worker_thread(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	while (true) {
		uint64_t start_us, end_us;
		uint32_t duration_us;
		int err;

		k_sem_take(&notify_job_sem, K_FOREVER);
		start_us = bsf_time_now_us();
		/*
		 * v45 BSF_V45_CH_APP_NOTIFY. This thread is the SOLE caller of
		 * bt_gatt_notify() (DATAFLOW_MAP section 0), so the channel has
		 * exactly one writer by construction -- and the runtime TID
		 * check enforces it rather than trusting the comment.
		 *
		 * CONTEXT_AUDIT item 7: there is exactly ONE unbounded wait
		 * reachable from here, att.c's K_FOREVER on the 8-buffer
		 * att_pool, released only by tx_notify_process() on the system
		 * workqueue, driven only by Number Of Completed Packets on MPSL
		 * Work. An ENTER with no EXIT is that wait, and nothing else.
		 *
		 * notify_exit_total is one of the detector's two watermarks. It
		 * is deliberately NOT notify_ok (SUBMISSION) and NOT
		 * publisher_count -- it counts RETURNS, which is the only thing
		 * a permanently blocked call cannot fake.
		 */
		BSF_V45_ENTER(BSF_V45_CH_APP_NOTIFY, BSF_V45_NOTIFY_ENTER,
			      ((uint32_t)notify_job.stream << 16) | notify_job.len);
		BSF_V45_INC(notify_enter_total);
#if defined(CONFIG_BSF_V45_FAULT_INJECT)
		/*
		 * Deliberately AFTER the ENTER and BEFORE the call: an ENTER with
		 * no EXIT is precisely what a real wedge looks like here, and
		 * parking before bt_gatt_notify() leaves the BLE pools untouched
		 * so this is a notify-path hang and not a starvation.
		 */
		if (atomic_get(&v45_inject_hang) != 0) {
			(void)k_sem_take(&v45_inject_hang_sem, K_FOREVER);
		}
#endif
		err = bt_gatt_notify(NULL, notify_job.attr,
				     notify_job.payload, notify_job.len);
		end_us = bsf_time_now_us();
		duration_us = end_us >= start_us ?
			(uint32_t)MIN(end_us - start_us, (uint64_t)UINT32_MAX) : 0u;
		BSF_V45_EXIT2(BSF_V45_CH_APP_NOTIFY, BSF_V45_NOTIFY_EXIT,
			      (uint32_t)err, duration_us);
		BSF_V45_INC(notify_exit_total);
		atomic_inc(&publisher_count);
		atomic_update_max(&publisher_max_us, duration_us);
		atomic_inc(&publisher_hist[bsf_imu_pull_hist_bin(duration_us)]);
		retained_stall.exit_count++;
		retained_stall.exit_ms = (uint32_t)k_uptime_get();
		retained_stall.last_return_code = (uint32_t)err;
		retained_stall.in_call_stream = 0u;
		atomic_set(&notify_in_call, 0);
		atomic_set(&notify_fast_drop, 0);
		if (err == 0) {
			atomic_inc(&notify_ok);
			if (atomic_get(&data_subscribed) != 0 &&
			    atomic_get(&telemetry_subscribed) != 0) {
				atomic_inc(&subscribed_notify_ok);
			}
		} else {
			atomic_inc(&drop_err);
			atomic_set(&last_notify_error, err);
			if (err == -ENOMEM) atomic_inc(&notify_rc_nomem);
			else if (err == -ENOTCONN) atomic_inc(&notify_rc_notconn);
			else if (err == -EAGAIN) atomic_inc(&notify_rc_again);
			else atomic_inc(&notify_rc_other);
		}
		/*
		 * R4/A3. The streak counts CONSECUTIVE -ENOTCONN while the
		 * application still believes it is connected. Any other outcome,
		 * including a success, resets it -- so a transient error cannot
		 * accumulate toward the trigger across minutes of healthy work.
		 */
		if (err == -ENOTCONN && atomic_get(&ble_connected) != 0) {
			atomic_val_t s = atomic_inc(&notify_notconn_streak) + 1;

			if ((uint32_t)s >
			    (uint32_t)atomic_get(&bsf_v45_cnt.notify_notconn_max)) {
				atomic_set(&bsf_v45_cnt.notify_notconn_max, s);
			}
		} else {
			atomic_clear(&notify_notconn_streak);
		}
		k_sem_give(&notify_idle_sem);
	}
}

K_THREAD_DEFINE(notify_worker_thread_id, NOTIFY_WORKER_STACK_SIZE,
		notify_worker_thread, NULL, NULL, NULL,
		NOTIFY_WORKER_PRIORITY, 0, 0);

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

	/* A half-range backward move is the direct tag-reset discriminator. */
	atomic_inc(&tag_reset_detected);
	atomic_set(&last_sweep, (atomic_val_t)sweep);
	char alarm[128];
	snprintf(alarm, sizeof(alarm),
		 "TAG_RESET_DETECTED name=%s before=%u after=%u window_fail=1",
		 device_name, previous, sweep);
	(void)publish_control_reply(BSF_CONTROL_SOURCE_B306, 0u, alarm);
	if (atomic_cas(&tag_reset_recovery_attempted, 0, 1)) {
		k_work_submit(&tag_reset_recovery_work);
	} else {
		(void)publish_control_reply(BSF_CONTROL_SOURCE_B306, 0u,
			"TAG_RESET_RECOVERY_STOP attempts=1");
	}
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

static void imu_autostart_on_beacon_locked(uint8_t flags)
{
	char reply[BSF_CONTROL_REPLY_TEXT_MAX + 1u];
	int ret;

	if (!bsf_imu_autostart_eligible(atomic_get(&imu_touched) != 0, flags,
					(uint32_t)k_uptime_get()) ||
	    !atomic_cas(&imu_touched, 0, 1)) {
		return;
	}
	ret = bsf_imu_start(reply, sizeof(reply));
	LOG_INF("beacon-gated IMU auto-start ret=%d reply=%s", ret, reply);
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
	imu_autostart_on_beacon_locked(frame->body.flags);
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
		atomic_set(&uart_last_stop_reason,
			   (atomic_val_t)event->data.rx_stop.reason);
		break;

	case UART_RX_DISABLED: {
		int err;

		atomic_inc(&uart_restarts);
		uint32_t reason = (uint32_t)atomic_set(&uart_last_stop_reason, 0);
		if ((reason & UART_ERROR_FRAMING) != 0u) {
			atomic_inc(&uart_restart_framing);
		} else if ((reason & UART_ERROR_OVERRUN) != 0u) {
			atomic_inc(&uart_restart_overrun);
		} else if ((reason & UART_BREAK) != 0u || reason == 0u) {
			atomic_inc(&uart_restart_break_idle);
		} else {
			atomic_inc(&uart_restart_other);
		}
		if (parser_position != 0u) {
			atomic_inc(&uart_restart_discarded_frames);
		}
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

static void tag_reset_recovery_work_handler(struct k_work *work)
{
	char cfg[sizeof(cached_tag_cfg)];
	char status[128];
	uint16_t correlation;
	int ret;

	ARG_UNUSED(work);
	k_mutex_lock(&cached_tag_cfg_lock, K_FOREVER);
	snprintf(cfg, sizeof(cfg), "%s", cached_tag_cfg);
	k_mutex_unlock(&cached_tag_cfg_lock);
	if (cfg[0] == '\0' || strstr(cfg, "BEACON_SYNC=") == NULL) {
		(void)publish_control_reply(BSF_CONTROL_SOURCE_B306, 0u,
			"TAG_RESET_RECOVERY_STOP reason=no_explicit_sync_cfg attempts=1");
		return;
	}
	correlation = (uint16_t)((uint32_t)atomic_inc(&next_correlation) + 1u);
	ret = relay_pending_reserve(correlation);
	if (ret == 0) {
		ret = uart_send_relay(correlation, cfg);
	}
	if (ret != 0) {
		(void)relay_pending_remove(correlation);
	}
	snprintf(status, sizeof(status),
		 "TAG_RESET_RECOVERY name=%s attempt=1 result=%s verify=CFG_ACK_AND_STREAM_REQUIRED",
		 device_name, ret == 0 ? "QUEUED" : "FAIL");
	(void)publish_control_reply(BSF_CONTROL_SOURCE_B306, 0u, status);
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
	atomic_set(&uart_restart_framing, 0);
	atomic_set(&uart_restart_overrun, 0);
	atomic_set(&uart_restart_break_idle, 0);
	atomic_set(&uart_restart_parser, 0);
	atomic_set(&uart_restart_explicit, 0);
	atomic_set(&uart_restart_other, 0);
	atomic_set(&uart_restart_discarded_frames, 0);
	atomic_set(&last_uart_error, 0);
	atomic_set(&last_sweep, 0);
	atomic_set(&have_last_sweep, 0);
	atomic_set(&tag_reset_detected, 0);
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
	for (uint32_t i = 0u; i < NOTIFY_STREAMS; ++i) {
		atomic_set(&notify_timeout_drop[i], 0);
	}
	atomic_set(&notify_rc_nomem, 0);
	atomic_set(&notify_rc_notconn, 0);
	atomic_set(&notify_rc_again, 0);
	atomic_set(&notify_rc_other, 0);
	atomic_set(&stall_alarm_count, 0);
	atomic_set(&stall_alarm_reason, 0);
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
	} else if (strcmp(command, "CORPSE STATUS") == 0) {
		/*
		 * The host polls this. A corpse is RETAINED until positively
		 * acknowledged, so a poll is idempotent and a missed ACK simply
		 * means the next poll -- or the next boot -- offers it again.
		 */
		snprintf(reply, sizeof(reply),
			 "CORPSE present=%u seq=%u pages=%u len=%u stage=%u stage_seq=%u age_ms=%u trigger=%u rr=%08X reboot_owner=%u",
			 corpse_present ? 1u : 0u,
			 corpse_present ? retained_corpse.corpse_seq : 0u,
			 corpse_present ? corpse_pages_total : 0u,
			 (unsigned int)sizeof(bsf_corpse_t),
			 corpse_present ? retained_corpse.stage : 0u,
			 corpse_present ? retained_corpse.stage_seq : 0u,
			 corpse_present ? retained_corpse.stage_age_ms : 0u,
			 corpse_present ? retained_corpse.trigger : 0u,
			 corpse_present ? retained_corpse.boot_reset_reason : 0u,
			 retained_reboot.owner);
	} else if (strcmp(command, "CORPSE PAGE OFF") == 0) {
		bsf_stall_ring_view_clear(&corpse_view);
		snprintf(reply, sizeof(reply), "CORPSE PAGE OFF ok");
	} else if (parse_exact_u32_command(command, "CORPSE PAGE=", &value) == 0) {
		if (!corpse_present || value >= corpse_pages_total) {
			snprintf(reply, sizeof(reply),
				 "CORPSE PAGE ERR present=%u page=%u pages=%u",
				 corpse_present ? 1u : 0u, value,
				 corpse_pages_total);
		} else {
			bsf_stall_ring_view_select(&corpse_view, (uint16_t)value,
						   k_uptime_get_32());
			snprintf(reply, sizeof(reply),
				 "CORPSE PAGE ok page=%u pages=%u ttl_ms=%u",
				 value, corpse_pages_total,
				 BSF_CORPSE_VIEW_TTL_MS);
		}
	} else if (parse_exact_u32_command(command, "CORPSE ACK=", &value) == 0) {
		/*
		 * ONLY a positive ACK carrying the right sequence may clear the
		 * valid marker. Anything else and the corpse is offered again.
		 */
		if (corpse_present && value == retained_corpse.corpse_seq) {
			bsf_corpse_invalidate();
			snprintf(reply, sizeof(reply),
				 "CORPSE ACK ok seq=%u cleared=1", value);
		} else {
			snprintf(reply, sizeof(reply),
				 "CORPSE ACK REJECT seq=%u present=%u have=%u",
				 value, corpse_present ? 1u : 0u,
				 corpse_present ? retained_corpse.corpse_seq : 0u);
		}
	} else if (strcmp(command, "CORPSE FORCE") == 0) {
		/*
		 * Stage 2 pipeline validation ONLY (brief section 11): it proves
		 * capture -> CRC -> retained -> reset -> reconnect -> export ->
		 * ACK. It does NOT reproduce the BLE failure and must never be
		 * reported as one.
		 */
		atomic_set(&corpse_force_trigger, 1);
		snprintf(reply, sizeof(reply),
			 "CORPSE FORCE armed note=pipeline_validation_only");
	/*
	 * ------------------------------------------------------------------
	 * v45 corpse collection.
	 *
	 * Rides the EXISTING vendor command/read channel -- the same machinery
	 * that already serves STALL_READ, RING PAGE= and CORPSE PAGE=. That is
	 * deliberate and it is what keeps the Fusion Master firmware FROZEN at
	 * dk-v36: the master transports an opaque command string and an opaque
	 * fixed-length read, and it does not care what is in either. Adding
	 * opcodes here is append-only on the node side and invisible to it.
	 * ------------------------------------------------------------------
	 */
	} else if (strcmp(command, "V45 STATUS") == 0) {
		uint32_t len = bsf_v45_image_len();
		uint32_t blind_ms, blind_ticks, blind_discards;
		uint32_t dog_resets, dog_age_ms, dog_tick_ms;
		uint8_t dog_dwell;
		uint8_t armed;

		/*
		 * armed/blind_ms/blind_discards are not decoration. An
		 * uncollected corpse disarms the detector, and on BSF6C53 that
		 * hid a real wedge for 29 minutes with nothing to see. A
		 * disabled instrument has to be able to say it is disabled.
		 */
		bsf_v45_blind_report(&blind_ms, &blind_ticks, &blind_discards,
				     &armed);
		/*
		 * The dog fields are on the SAME line as present=/armed=, not a
		 * separate command. `present=0 armed=1` was indistinguishable
		 * from a clean node right up until 2026-08-09, when it turned
		 * out to mean "a watchdog ate the corpse"; anything a reader has
		 * to ask for separately is something they will not ask for.
		 */
		bsf_v45_dog_report(&dog_resets, &dog_dwell, &dog_age_ms,
				   &dog_tick_ms);
		snprintf(reply, sizeof(reply),
			 "V45 present=%u seq=%u cause=%u len=%u pages=%u core=%u ch=%u ring=%u flash=%u armed=%u blind_ms=%u blind_ticks=%u blind_discards=%u dog=%u dog_dwell=%u dog_age_ms=%u dog_tick_ms=%u",
			 bsf_v45_present() ? 1u : 0u, bsf_v45_seq(),
			 bsf_v45_cause(), len,
			 (unsigned int)((len + BSF_CORPSE_PAGE_DATA - 1u) /
					BSF_CORPSE_PAGE_DATA),
			 bsf_v45_core_len(), (unsigned int)BSF_V45_CH__COUNT,
			 (unsigned int)BSF_STALL_RING_CAPACITY,
			 BSF_CORPSE_FLASH_ENABLED,
			 armed, blind_ms, blind_ticks, blind_discards,
			 dog_resets, dog_dwell, dog_age_ms, dog_tick_ms);
	} else if (strcmp(command, "V45 PAGE OFF") == 0) {
		bsf_stall_ring_view_clear(&v45_view);
		snprintf(reply, sizeof(reply), "V45 PAGE OFF ok");
	} else if (parse_exact_u32_command(command, "V45 PAGE=", &value) == 0) {
		uint32_t len = bsf_v45_image_len();
		uint32_t pages = (len + BSF_CORPSE_PAGE_DATA - 1u) /
				 BSF_CORPSE_PAGE_DATA;

		if (!bsf_v45_present() || value >= pages) {
			snprintf(reply, sizeof(reply),
				 "V45 PAGE ERR present=%u page=%u pages=%u",
				 bsf_v45_present() ? 1u : 0u, value, pages);
		} else {
			bsf_stall_ring_view_select(&v45_view, (uint16_t)value,
						   k_uptime_get_32());
			snprintf(reply, sizeof(reply),
				 "V45 PAGE ok page=%u pages=%u ttl_ms=%u",
				 value, pages, BSF_CORPSE_VIEW_TTL_MS);
		}
	} else if (parse_exact_u32_command(command, "V45 ACK=", &value) == 0) {
		/*
		 * ACK-clear, and ONLY after the host has verified every CRC and
		 * written its evidence files. An unverified clear is how a
		 * corpse gets lost twice.
		 */
		if (bsf_v45_ack(value)) {
			bsf_stall_ring_view_clear(&v45_view);
			snprintf(reply, sizeof(reply),
				 "V45 ACK ok seq=%u cleared=1", value);
		} else {
			snprintf(reply, sizeof(reply),
				 "V45 ACK REJECT seq=%u present=%u have=%u",
				 value, bsf_v45_present() ? 1u : 0u,
				 bsf_v45_seq());
		}
	} else if (strcmp(command, "V45 FORCE") == 0) {
		bsf_v45_force();
		snprintf(reply, sizeof(reply),
			 "V45 FORCE armed note=pipeline_validation_only");
	} else if (strcmp(command, "V45 LEAK") == 0) {
		/*
		 * Fault injection 3. Takes the SINGLETON sync_evt buffer and
		 * never returns it.
		 *
		 * SCOPE, STATED WHERE IT IS USED: if candidate 1 is right this
		 * reproduces the full 8-invariant phenotype, which proves the
		 * starvation -> phenotype consequence chain. It does NOT prove
		 * that real wedges begin this way. Compiled out unless
		 * CONFIG_BSF_V45_FAULT_INJECT=y.
		 */
		snprintf(reply, sizeof(reply), "V45 LEAK rc=%d",
			 bsf_v45_sync_evt_leak());
	} else if (strcmp(command, "V45 LEAK OFF") == 0) {
		snprintf(reply, sizeof(reply), "V45 LEAK OFF rc=%d",
			 bsf_v45_sync_evt_release());
#if defined(CONFIG_BSF_V45_FAULT_INJECT)
	} else if (strcmp(command, "V45 HANG") == 0) {
		/*
		 * Fault injection 2. Parks the notify worker with the ENTER
		 * recorded and no EXIT, freezing watermark A while leaving the
		 * BLE stack healthy. Arms 1 s later so this reply survives.
		 *
		 * SCOPE: this proves the detector fires on a notify-path hang.
		 * It does NOT prove real wedges begin that way. To get arm A in
		 * ISOLATION the host must poll `STALL READ` for the duration --
		 * see the note at bsf_v45_notify_hang().
		 */
		snprintf(reply, sizeof(reply), "V45 HANG rc=%d arm_delay_ms=1000",
			 bsf_v45_notify_hang(true));
	} else if (strcmp(command, "V45 HANG OFF") == 0) {
		snprintf(reply, sizeof(reply), "V45 HANG OFF rc=%d",
			 bsf_v45_notify_hang(false));
#endif
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
			 "CTRU restart=%u frame=%u overrun=%u break_idle=%u parser=%u explicit=%u other=%u discarded=%u tag_reset=%u recovery=%u",
			 (uint32_t)atomic_get(&uart_restarts),
			 (uint32_t)atomic_get(&uart_restart_framing),
			 (uint32_t)atomic_get(&uart_restart_overrun),
			 (uint32_t)atomic_get(&uart_restart_break_idle),
			 (uint32_t)atomic_get(&uart_restart_parser),
			 (uint32_t)atomic_get(&uart_restart_explicit),
			 (uint32_t)atomic_get(&uart_restart_other),
			 (uint32_t)atomic_get(&uart_restart_discarded_frames),
			 (uint32_t)atomic_get(&tag_reset_detected),
			 (uint32_t)atomic_get(&tag_reset_recovery_attempted));
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
	} else if (strcmp(command, "STALL STATUS") == 0) {
		uint32_t now_ms = (uint32_t)k_uptime_get();
		uint32_t age_ms = atomic_get(&notify_in_call) != 0 ?
			(uint32_t)(now_ms - retained_stall.entry_ms) : 0u;
		snprintf(reply, sizeof(reply),
			 "STALL e=%u x=%u age=%u s=%u td=%u/%u/%u q=%u/%u/%u hb=%u rc=%d rcc=%u/%u/%u/%u alarm=%u/%u test=%08X",
			 retained_stall.entry_count, retained_stall.exit_count,
			 age_ms, retained_stall.in_call_stream,
			 (uint32_t)atomic_get(&notify_timeout_drop[NOTIFY_IMU]),
			 (uint32_t)atomic_get(&notify_timeout_drop[NOTIFY_UWB]),
			 (uint32_t)atomic_get(&notify_timeout_drop[NOTIFY_CTL]),
			 k_msgq_num_used_get(&q_imu), k_msgq_num_used_get(&q_uwb),
			 k_msgq_num_used_get(&q_ctl),
			 (uint32_t)atomic_get(&producer_heartbeat),
			 (int32_t)retained_stall.last_return_code,
			 (uint32_t)atomic_get(&notify_rc_nomem),
			 (uint32_t)atomic_get(&notify_rc_notconn),
			 (uint32_t)atomic_get(&notify_rc_again),
			 (uint32_t)atomic_get(&notify_rc_other),
			 (uint32_t)atomic_get(&stall_alarm_count),
			 (uint32_t)atomic_get(&stall_alarm_reason),
			 retained_stall.test_value);
	} else if (strcmp(command, "RING STATUS") == 0) {
		uint32_t now_ms = (uint32_t)k_uptime_get();
		uint8_t view_page = 0u;
		bool viewing = bsf_stall_ring_view_page(
			&stall_ring_view, now_ms, BSF_STALL_RING_VIEW_TTL_MS,
			&view_page);
		snprintf(reply, sizeof(reply),
			 "RING boot=%u init=%s count=%u/%u pages=%u frozen=%u reason=%u fidx=%u fms=%u writes=%u period=%u span=%u view=%u/%u ttl=%u",
			 stall_ring.boot_id,
			 bsf_stall_ring_boot_name(stall_ring_boot_result),
			 (uint32_t)stall_ring.count,
			 (uint32_t)BSF_STALL_RING_CAPACITY,
			 (uint32_t)bsf_stall_ring_pages(&stall_ring),
			 (uint32_t)stall_ring.frozen,
			 (uint32_t)stall_ring.freeze_reason,
			 (uint32_t)stall_ring.freeze_index,
			 stall_ring.freeze_uptime_ms, stall_ring.writes_total,
			 (uint32_t)BSF_STALL_RING_PERIOD_MS,
			 (uint32_t)BSF_STALL_RING_SPAN_MS,
			 viewing ? 1u : 0u, (uint32_t)view_page,
			 (uint32_t)BSF_STALL_RING_VIEW_TTL_MS);
	} else if (strcmp(command, "RING PAGE OFF") == 0) {
		bsf_stall_ring_view_clear(&stall_ring_view);
		snprintf(reply, sizeof(reply), "RING PAGE OFF ok");
	} else if (parse_exact_u32_command(command, "RING PAGE=", &value) == 0) {
		/*
		 * Selection only. The page is served by the next ordinary read
		 * of the stall characteristic, at the same 232 bytes as the
		 * status snapshot, and the selection ages out on its own.
		 */
		uint8_t pages = bsf_stall_ring_pages(&stall_ring);

		if (value >= pages) {
			snprintf(reply, sizeof(reply),
				 "RING PAGE ERR page=%u pages=%u", value,
				 (uint32_t)pages);
		} else {
			bsf_stall_ring_view_select(&stall_ring_view,
						   (uint16_t)value,
						   (uint32_t)k_uptime_get());
			snprintf(reply, sizeof(reply),
				 "RING PAGE ok page=%u pages=%u entries=%u ttl_ms=%u",
				 value, (uint32_t)pages,
				 (uint32_t)BSF_STALL_RING_PAGE_ENTRIES,
				 (uint32_t)BSF_STALL_RING_VIEW_TTL_MS);
		}
	} else if (strcmp(command, "RING FREEZE") == 0) {
		uint32_t now_ms = (uint32_t)k_uptime_get();
		bool fired = stall_ring_latch(BSF_RING_FREEZE_MANUAL, now_ms);

		snprintf(reply, sizeof(reply),
			 "RING FREEZE %s reason=%u count=%u fidx=%u",
			 fired ? "ok" : "already",
			 (uint32_t)stall_ring.freeze_reason,
			 (uint32_t)stall_ring.count,
			 (uint32_t)stall_ring.freeze_index);
	} else if (strcmp(command, "RING CLEAR") == 0) {
		k_spinlock_key_t ring_key = k_spin_lock(&stall_ring_lock);

		bsf_stall_ring_clear(&stall_ring);
		k_spin_unlock(&stall_ring_lock, ring_key);
		bsf_stall_ring_view_clear(&stall_ring_view);
		snprintf(reply, sizeof(reply),
			 "RING CLEAR ok boot=%u capacity=%u",
			 stall_ring.boot_id,
			 (uint32_t)BSF_STALL_RING_CAPACITY);
	} else if (parse_hex_u32_command(command, "STALL LATCH TEST=", &value) == 0) {
		retained_stall.test_value = value;
		snprintf(reply, sizeof(reply), "STALL LATCH TEST OK value=%08X", value);
	} else if (strcmp(command, "STACKS") == 0) {
		size_t pub = 0u, parser = 0u, imu = 0u, notify = 0u, sys = 0u;
		int ep = k_thread_stack_space_get(publisher_thread_id, &pub);
		int er = k_thread_stack_space_get(uart_parser_thread_id, &parser);
		int ei = bsf_imu_stack_unused(&imu);
		int en = k_thread_stack_space_get(notify_worker_thread_id, &notify);
		int es = k_thread_stack_space_get(&k_sys_work_q.thread, &sys);
		snprintf(reply, sizeof(reply),
			 "STACKS pub=%u/%u parser=%u/%u imu=%u/%u notify=%u/%u sys=%u/%u err=%d/%d/%d/%d/%d",
			 PUBLISHER_STACK_SIZE-(uint32_t)pub, PUBLISHER_STACK_SIZE,
			 PARSER_STACK_SIZE-(uint32_t)parser, PARSER_STACK_SIZE,
			 2048u-(uint32_t)imu, 2048u,
			 NOTIFY_WORKER_STACK_SIZE-(uint32_t)notify, NOTIFY_WORKER_STACK_SIZE,
			 CONFIG_SYSTEM_WORKQUEUE_STACK_SIZE-(uint32_t)sys,
			 CONFIG_SYSTEM_WORKQUEUE_STACK_SIZE, ep, er, ei, en, es);
	} else if (strcmp(command, "IMU START") == 0) {
		atomic_set(&imu_touched, 1);
		ret = bsf_imu_start(reply, sizeof(reply));
		ARG_UNUSED(ret);
	} else if (strcmp(command, "IMU STOP") == 0) {
		atomic_set(&imu_touched, 1);
		ret = bsf_imu_stop();
		bsf_imu_format_stop(reply, sizeof(reply), ret);
	} else if (parse_exact_u32_command(command, "IMU RATE=", &value) == 0) {
		atomic_set(&imu_touched, 1);
		ret = value <= UINT16_MAX ?
			bsf_imu_set_rate((uint16_t)value) : -EINVAL;
		snprintf(reply, sizeof(reply), "IMU RATE %s hz=%u err=%d",
				 ret == 0 ? "OK" : "FAIL", value, ret);
	} else if (parse_exact_u32_command(command, "IMU BATCH=", &value) == 0) {
		atomic_set(&imu_touched, 1);
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
				if (strncmp(tag_line, "CFG ", 4u) == 0 &&
				    strstr(tag_line, "BEACON_SYNC=") != NULL) {
					k_mutex_lock(&cached_tag_cfg_lock, K_FOREVER);
					snprintf(cached_tag_cfg, sizeof(cached_tag_cfg),
						 "%s", tag_line);
					k_mutex_unlock(&cached_tag_cfg_lock);
					atomic_set(&tag_reset_recovery_attempted, 0);
				}
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
	{
		uint32_t producer = (uint32_t)atomic_get(&producer_heartbeat);
		uint32_t exits = retained_stall.exit_count;
		uint32_t now_ms = (uint32_t)k_uptime_get();
		bool armed = atomic_get(&ble_connected) != 0 &&
			atomic_get(&data_subscribed) != 0 &&
			atomic_get(&telemetry_subscribed) != 0 &&
			(uint32_t)atomic_get(&subscribed_notify_ok) >=
				STALL_ARM_NOTIFY_OK;
		struct bsf_stall_decision decision = bsf_stall_detector_step(
			&stall_detector, atomic_get(&ble_connected) != 0,
			atomic_get(&data_subscribed) != 0 &&
				atomic_get(&telemetry_subscribed) != 0,
			(uint32_t)atomic_get(&subscribed_notify_ok),
			STALL_ARM_NOTIFY_OK, producer, exits,
			(uint8_t)retained_stall.in_call_stream, 1000u,
			STALL_DETECT_MS, STALL_MAX_RECOVERIES_PER_POWER);
		uint8_t reason = decision.reason;
		bsf_stall_status_t sampled =
			make_stall_status(now_ms, armed, reason);
		if (retained_stall.first_snapshot.version ==
		    BSF_STALL_STATUS_VERSION) {
			publish_stall_status(&retained_stall.first_snapshot);
		} else {
			publish_stall_status(&sampled);
		}
		if (decision.fire &&
		    retained_stall.alarm_reason == BSF_STALL_REASON_NONE) {
			atomic_set(&stall_alarm_reason, reason);
			atomic_inc(&stall_alarm_count);
			retained_stall.alarm_reason = reason;
			retained_stall.alarm_count++;
			retained_stall.alarm_timestamp_ms = now_ms;
			sampled.reason = reason;
			sampled.alarm_count = retained_stall.alarm_count;
			sampled.alarm_timestamp_ms = now_ms;
			if (decision.take_snapshot &&
			    retained_stall.first_snapshot.version == 0u) {
				retained_stall.first_snapshot = sampled;
			}
			publish_stall_status(&sampled);
			/*
			 * Latch the trajectory before recovery is armed, so the
			 * 1.5 s retraction window and the reboot itself cannot
			 * overwrite the run-in that triggered them.
			 */
			(void)stall_ring_latch(BSF_RING_FREEZE_ALARM, now_ms);
			LOG_ERR("STALL ALARM reason=%u e=%u x=%u age=%u ring=%u/%u@%u",
				reason,
				retained_stall.entry_count, retained_stall.exit_count,
				sampled.in_call_age_ms,
				(uint32_t)stall_ring.count,
				(uint32_t)BSF_STALL_RING_CAPACITY,
				(uint32_t)stall_ring.freeze_index);
			/* Snapshot is complete before recovery is armed. */
			if (decision.recover) {
				retained_stall.recovery_count =
					stall_detector.recovery_count;
				retained_stall.first_snapshot.recovery_count =
					retained_stall.recovery_count;
				atomic_set(&stall_recovery_pending, 1);
				k_work_reschedule(&stall_recovery_work,
						  K_MSEC(STALL_RECOVERY_RETRACT_MS));
			}
		}
	}
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
	{
		bsf_ble_pool_usage_t pools = {
			.version = BSF_BLE_PROTOCOL_VERSION,
			.kind = BSF_BLE_KIND_POOL_USAGE,
			.len = sizeof(pools),
			.node_uptime_ms = (uint32_t)k_uptime_get(),
			.pool_usage_enabled = 1u,
#if defined(CONFIG_BT_ATT_SENT_CB_AFTER_TX)
			.att_sent_cb_after_tx = 1u,
#endif
		};
		pools.pool_count = sample_pool_usage(pools.pools);
		(void)enqueue_ctl_record(PUBLISH_ATTRIBUTE_DATA, &pools,
					 sizeof(pools));
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
	uint32_t now_ms = (uint32_t)k_uptime_get();
	uint32_t alarm_age_ms = now_ms - retained_stall.alarm_timestamp_ms;
	bool alarm_retracted = bsf_stall_detector_retract_disconnect(
		&stall_detector, alarm_age_ms, STALL_RECOVERY_RETRACT_MS,
		atomic_get(&stall_recovery_pending) != 0);

	if (alarm_retracted) {
		(void)k_work_cancel_delayable(&stall_recovery_work);
		atomic_clear(&stall_recovery_pending);
		retained_stall.alarm_count = stall_detector.alarm_count;
		retained_stall.recovery_count = stall_detector.recovery_count;
		retained_stall.alarm_reason = BSF_STALL_REASON_NONE;
		retained_stall.alarm_timestamp_ms = 0u;
		memset(&retained_stall.first_snapshot, 0,
		       sizeof(retained_stall.first_snapshot));
		atomic_set(&stall_alarm_count, retained_stall.alarm_count);
		atomic_set(&stall_alarm_reason, BSF_STALL_REASON_NONE);
		bsf_stall_status_t retracted = make_stall_status(
			now_ms, false, BSF_STALL_REASON_NONE);
		publish_stall_status(&retracted);
		LOG_INF("STALL RECOVERY RETRACT disconnect age_ms=%u window_ms=%u",
			alarm_age_ms, STALL_RECOVERY_RETRACT_MS);
	}
	/*
	 * A freeze that a disconnect explains is not evidence. Retract it on
	 * the same 1500 ms terms as the recovery, or one benign disconnect
	 * would leave the ring latched -- and blind -- for the rest of the
	 * deployment. Runs on every disconnect, not only a retracted alarm,
	 * because the no_exit backstop has no alarm to be retracted with.
	 */
	{
		k_spinlock_key_t ring_key = k_spin_lock(&stall_ring_lock);
		uint8_t was = stall_ring.freeze_reason;
		bool ring_retracted = bsf_stall_ring_retract_disconnect(
			&stall_ring, now_ms, STALL_RECOVERY_RETRACT_MS,
			alarm_retracted);

		k_spin_unlock(&stall_ring_lock, ring_key);
		if (ring_retracted) {
			LOG_INF("STALL RING RETRACT disconnect was_reason=%u window_ms=%u",
				(uint32_t)was,
				(uint32_t)STALL_RECOVERY_RETRACT_MS);
		}
	}
	atomic_clear(&ble_connected);
	atomic_clear(&data_subscribed);
	atomic_clear(&telemetry_subscribed);
	atomic_clear(&subscribed_notify_ok);
	/*
	 * v45: retire the incarnation here too, not only on connect. A normal
	 * disconnect inside the supervision timeout must be a no-trigger path,
	 * and the cheapest way to guarantee that is to make the dwell state
	 * unreachable rather than to add a case to the trigger test.
	 */
	v45_new_epoch(false);
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
	/* v45: a new incarnation. Every dwell the detector held is void. */
	v45_new_epoch(true);
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
	for (size_t i = 0; i < BSF_NET_BUF_POOL_MAX; ++i) {
		atomic_set(&pool_low_water[i], UINT16_MAX);
	}

	if (retained_stall.magic != RETAINED_STALL_MAGIC) {
		memset(&retained_stall, 0, sizeof(retained_stall));
		retained_stall.magic = RETAINED_STALL_MAGIC;
	} else {
		retained_stall.in_call_stream = 0u;
	}
	stall_ring_boot_result = (uint8_t)bsf_stall_ring_boot(&stall_ring);

	/*
	 * v43 corpse recovery. `.noinit` survives the software reset we take
	 * after a wedge, but NOT power-on or brownout -- so whatever is sitting
	 * in that RAM on a cold boot is uninitialised garbage that may look
	 * entirely plausible. Nothing is trusted without magic AND schema AND
	 * length AND CRC32, and the record only counts as complete if the
	 * `valid` word (written last, after the CRC) is also set.
	 */
	corpse_present = bsf_corpse_validate();
	corpse_pages_total = bsf_corpse_page_count();
	if (corpse_present) {
		LOG_ERR("CORPSE RECOVERED seq=%u stage=%u stage_seq=%u age_ms=%u trigger=%u pages=%u rr=%08X",
			retained_corpse.corpse_seq, retained_corpse.stage,
			retained_corpse.stage_seq, retained_corpse.stage_age_ms,
			retained_corpse.trigger, corpse_pages_total,
			retained_corpse.boot_reset_reason);
	} else if (retained_corpse.magic != 0u || retained_corpse.valid != 0u) {
		/* Something was there and did not validate. Say so, then erase. */
		LOG_WRN("CORPSE REJECTED magic=%08X valid=%08X schema=%u len=%u -- treating as cold-boot garbage",
			retained_corpse.magic, retained_corpse.valid,
			retained_corpse.schema, retained_corpse.length);
		memset(&retained_corpse, 0, sizeof(retained_corpse));
	}
	if (retained_reboot.magic != BSF_CORPSE_MAGIC) {
		memset(&retained_reboot, 0, sizeof(retained_reboot));
		retained_reboot.magic = BSF_CORPSE_MAGIC;
	}
	if (retained_stall.first_snapshot.version ==
	    BSF_STALL_STATUS_VERSION) {
		publish_stall_status(&retained_stall.first_snapshot);
	} else {
		bsf_stall_status_t initial = make_stall_status(
			(uint32_t)k_uptime_get(), false,
			BSF_STALL_REASON_NONE);
		publish_stall_status(&initial);
	}

	node_identity = bsl_identity_from_ficr(deviceid0, deviceid1);

	boot_reset_reason = nrfx_reset_reason_get();
	nrfx_reset_reason_clear(boot_reset_reason);
	/*
	 * Must run before the reason is used for anything else and before the
	 * first detector tick, so the witness promotes the PREVIOUS run's dwell
	 * state and not this one's. RESETREAS has already been cleared above,
	 * which is why the decision is made here from the saved copy.
	 */
	bsf_v45_dog_boot((boot_reset_reason & NRFX_RESET_REASON_DOG_MASK) != 0u);
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
	LOG_INF("STALL RING boot_id=%u boot=%s count=%u frozen=%u reason=%u freeze_index=%u period_ms=%u capacity=%u span_ms=%u",
		stall_ring.boot_id,
		bsf_stall_ring_boot_name(stall_ring_boot_result),
		(uint32_t)stall_ring.count, (uint32_t)stall_ring.frozen,
		(uint32_t)stall_ring.freeze_reason,
		(uint32_t)stall_ring.freeze_index,
		(uint32_t)BSF_STALL_RING_PERIOD_MS,
		(uint32_t)BSF_STALL_RING_CAPACITY,
		(uint32_t)BSF_STALL_RING_SPAN_MS);

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

	/*
	 * v45 section 9, step 3: persist a retained corpse to flash BEFORE
	 * bt_enable().
	 *
	 * Not because "the radio is off so no MPSL sync is needed" -- CONTEXT_AUDIT
	 * item 10 proves that premise is FALSE: MPSL is initialised at
	 * PRE_KERNEL_1, so nrf_flash_sync_is_required() is true at every point
	 * this code can run. The real argument is different and stronger:
	 *
	 *   - a flash write at CAPTURE time could wait on a timeslot serviced
	 *     from the very thread that is wedged, so capture never touches
	 *     flash and writes `.noinit` only;
	 *   - by here the cold reboot has happened, that thread does not exist,
	 *     no BLE role is scheduled, and the EARLIEST timeslot is granted
	 *     immediately;
	 *   - we are on the `main` thread, where blocking is legal, and the call
	 *     is bounded by FLASH_TIMEOUT_MS and returns an error on timeout.
	 *
	 * So the worst case is "the corpse stayed in .noinit", never a hung boot.
	 * With BSF_CORPSE_FLASH_ENABLED=0 (the default, because the deployed
	 * partition map has zero free bytes) this is a no-op.
	 */
	bsf_v45_flash_persist_pending();

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
	k_timer_start(&stall_ring_timer, K_MSEC(BSF_STALL_RING_PERIOD_MS),
		      K_MSEC(BSF_STALL_RING_PERIOD_MS));

	/*
	 * v45 last, deliberately: bt_enable() has run, so "MPSL Work" and
	 * "BT RX WQ" now exist and the name walk in bsf_v45_init() finds them on
	 * the first pass instead of retrying for a second.
	 *
	 * The reboot budget is SHARED with the v42 ring ISR and the v43/v44 BT
	 * RX monitor. Three authorities, one reset per power cycle -- otherwise
	 * the second reset lands on top of the first one's evidence.
	 */
	bsf_v45_bind_app_threads(notify_worker_thread_id, publisher_thread_id);
	bsf_v45_init(&stall_ring, &stall_ring_lock, bsf_reboot_budget_take);
#if defined(CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS)
	mgmt_callback_register(&v45_dfu_callback);
#endif
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
