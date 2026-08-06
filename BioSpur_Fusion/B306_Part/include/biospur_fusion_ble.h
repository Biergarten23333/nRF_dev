/*
 * BioSpur Fusion B306 -> Fusion Master diagnostic data contract.
 *
 * This is an internal BLE contract owned by Task B. The UWB UART contract
 * remains biospur_link.h v2 and is not changed by this header.
 */

#ifndef BIOSPUR_FUSION_BLE_H
#define BIOSPUR_FUSION_BLE_H

#include <stdint.h>

#include "biospur_link.h"

#ifdef __cplusplus
extern "C" {
#endif

#define BSF_BLE_PROTOCOL_VERSION 7u

#define BSF_BLE_KIND_UWB        1u
#define BSF_BLE_KIND_TELEMETRY  2u
#define BSF_BLE_KIND_IMU        3u
#define BSF_BLE_KIND_CONTROL_REPLY 4u
#define BSF_BLE_KIND_QUEUE_COUNTERS 5u
#define BSF_BLE_KIND_POOL_USAGE 6u
#define BSF_NET_BUF_POOL_MAX 16u

#define BSF_IMU_BATCH_MIN       1u
#define BSF_IMU_BATCH_MAX       16u
#define BSF_IMU_BATCH_DEFAULT   10u
#define BSF_CONTROL_LINE_MAX    200u
#define BSF_CONTROL_REPLY_TEXT_MAX 200u

#define BSF_CAPTURE_TS_ABSENT       UINT64_MAX
#define BSF_CAPTURE_DELTA_ABSENT    UINT32_MAX
#define BSF_CAPTURE_PAIR_WINDOW_US  50000u

enum bsf_capture_verdict {
	BSF_CAPTURE_HEALTHY = 0,
	BSF_CAPTURE_B306_MISSED_EDGE = 1,
	BSF_CAPTURE_TAG_NO_POLL_TX = 2,
	BSF_CAPTURE_CONTRADICTION = 3,
};

enum bsf_capture_edge_shape {
	BSF_CAPTURE_EDGE_NONE = 0,
	BSF_CAPTURE_EDGE_ACTIVE_HIGH = 1,
	BSF_CAPTURE_EDGE_ACTIVE_LOW = 2,
	BSF_CAPTURE_EDGE_RISING_ONLY = 3,
	BSF_CAPTURE_EDGE_FALLING_ONLY = 4,
};

enum bsf_imu_health_class {
	BSF_IMU_HEALTH_NONE = 0,
	BSF_IMU_HEALTH_BOOT_RESET = 1,
	BSF_IMU_HEALTH_CHIP_BACKWARD = 2,
	BSF_IMU_HEALTH_CHIP_FROZEN = 3,
	BSF_IMU_HEALTH_CHIP_RATE = 4,
	BSF_IMU_HEALTH_CANARY = 5,
	BSF_IMU_HEALTH_ACC_PLAUSIBILITY = 6,
	BSF_IMU_HEALTH_DEAD_BLOCK = 7,
	BSF_IMU_HEALTH_IDENTICAL_WEDGE = 8,
	BSF_IMU_HEALTH_I2C_CONSECUTIVE_FAILURES = 9,
};

#define BSF_CAPTURE_FLAG_INITIAL_HIGH  (1u << 0)
#define BSF_CAPTURE_FLAG_INPUT_NOPULL  (1u << 1)
#define BSF_CAPTURE_FLAG_TIMER2_1MHZ   (1u << 2)
#define BSF_CAPTURE_FLAG_DUAL_EDGE_PPI (1u << 3)
#define BSF_CAPTURE_FLAG_HFXO_HELD     (1u << 4)

typedef struct __attribute__((packed)) {
	uint64_t frame_rx_ts_us;
	uint64_t strobe_ts_us;
	uint64_t rising_ts_us;
	uint64_t falling_ts_us;
	uint64_t last_orphan_strobe_ts_us;
	uint32_t frame_to_strobe_us;
	uint32_t rising_edge_count;
	uint32_t falling_edge_count;
	uint32_t boot_discarded_edge_count;
	uint32_t edge_queue_drop_count;
	uint32_t orphan_strobe_count;
	uint32_t orphan_edge_count;
	uint32_t orphan_frame_count;
	uint32_t near_window_edge_count;
	uint16_t pairing_window_us;
	uint8_t verdict;
	uint8_t edge_shape;
	uint8_t pair_candidates;
	uint8_t capture_flags;
} bsf_capture_record_t;

/*
 * 7b120001-4e77-4a71-a045-7b4d3f2a9000 service
 * 7b120002-4e77-4a71-a045-7b4d3f2a9000 UWB data
 * 7b120003-4e77-4a71-a045-7b4d3f2a9000 telemetry
 * 7b120004-4e77-4a71-a045-7b4d3f2a9000 ASCII control write
 * 7b120005-4e77-4a71-a045-7b4d3f2a9000 stall status read
 *
 * Expand with BT_UUID_128_ENCODE() after including Zephyr's uuid.h.
 */
#define BSF_BLE_UUID_SERVICE_W32   0x7b120001u
#define BSF_BLE_UUID_DATA_W32      0x7b120002u
#define BSF_BLE_UUID_TELEMETRY_W32 0x7b120003u
#define BSF_BLE_UUID_CONTROL_W32   0x7b120004u
#define BSF_BLE_UUID_STALL_W32     0x7b120005u
#define BSF_BLE_UUID_W16_1         0x4e77u
#define BSF_BLE_UUID_W16_2         0x4a71u
#define BSF_BLE_UUID_W16_3         0xa045u
#define BSF_BLE_UUID_W48           0x7b4d3f2a9000ULL

#define BSF_STALL_STATUS_VERSION 2u

/*
 * The stall characteristic serves two wire forms of the SAME length. Byte 0
 * (`version`) selects which one a given read returned:
 *
 *   BSF_STALL_STATUS_VERSION (2) -- bsf_stall_status_t, the instantaneous
 *                                   snapshot. Unchanged from v38/v39.
 *   BSF_STALL_RING_VERSION   (4) -- bsf_stall_ring_page_t, one page of the
 *                                   50 ms trajectory ring. (3 = the v41
 *                                   layout, still decodable by the host.)
 *
 * Both are exactly 232 bytes, so a reader that only checks the length keeps
 * working and never has to negotiate. Which form is served is selected by a
 * control write (`RING PAGE=<n>` / `RING PAGE OFF`), never by the read itself.
 */
#define BSF_STALL_RING_VERSION 4u
/* v3 pages (b306-imu-relay-v41) remain decodable: a v41 board's ring may still
 * be retrieved if it ever reboots and rejoins. The host decoder must handle
 * both, so the version byte is the discriminator, not an assumption. */
#define BSF_STALL_RING_VERSION_V41 3u

/*
 * `available` is the pool's free count at sample time.
 *
 * `low_water` is the minimum `available` observed SINCE THE PREVIOUS RECORD,
 * not since boot. The since-boot form was removed because every board drives
 * its ATT pool to zero during its own DFU, which latched the field at 0 for the
 * rest of the deployment: it read like a live signal and carried nothing.
 * Layout is unchanged, so the kind-8 payload stays at 140 bytes.
 */
typedef struct __attribute__((packed)) {
	uint32_t name_hash;
	uint16_t available;
	uint16_t low_water;
} bsf_net_buf_pool_usage_t;

enum bsf_stall_reason {
	BSF_STALL_REASON_NONE = 0,
	BSF_STALL_REASON_PUBLISHER_FROZEN = 1,
	BSF_STALL_REASON_NOTIFY_BLOCKED = 2,
	BSF_STALL_REASON_PRODUCER_FROZEN = 3,
};

/*
 * Read-only diagnostic escape path.  This value is copied by the detector
 * (system workqueue) and read directly by ATT; it never enters q_ctl or the
 * publisher/notify-worker path.
 */
typedef struct __attribute__((packed)) {
	uint8_t version;
	uint8_t reason;
	uint8_t in_call_stream;
	uint8_t armed;
	uint32_t sample_uptime_ms;
	uint32_t entry_count;
	uint32_t exit_count;
	uint32_t entry_ms;
	uint32_t exit_ms;
	uint32_t in_call_age_ms;
	int32_t last_return_code;
	uint32_t return_ok;
	uint32_t return_nomem;
	uint32_t return_notconn;
	uint32_t return_again;
	uint32_t return_other;
	uint16_t queue_depth_ctl;
	uint16_t queue_depth_uwb;
	uint16_t queue_depth_imu;
	uint16_t reserved0;
	uint32_t q_drop_ctl;
	uint32_t q_drop_uwb;
	uint32_t q_drop_imu;
	uint32_t timeout_drop_ctl;
	uint32_t timeout_drop_uwb;
	uint32_t timeout_drop_imu;
	uint32_t producer_heartbeat;
	uint32_t alarm_count;
	uint32_t alarm_timestamp_ms;
	uint32_t recovery_count;
	uint8_t pool_count;
	uint8_t pool_usage_enabled;
	uint8_t att_sent_cb_after_tx;
	uint8_t reserved1;
	bsf_net_buf_pool_usage_t pools[BSF_NET_BUF_POOL_MAX];
} bsf_stall_status_t;

/*
 * One 50 ms trajectory sample. Written from the system-timer ISR into the
 * retained `.noinit` ring, so it must stay small, fixed and pointer-free.
 *
 * `pool_avail` is instantaneous free count only -- it deliberately does NOT
 * touch the kind-8 low-water window, which belongs to the 1 Hz sampler.
 */
/*
 * v4 (H1) adds the detector's own inputs, and pays for them by narrowing
 * pool_avail from 16 slots to 8.
 *
 * N6 caught a stall on BSF44AD in which the firmware's bounded recovery never
 * fired, and the ring as it stood could not have said why: `armed` depends on
 * `subscribed_notify_ok >= STALL_ARM_NOTIFY_OK`, the dwell accumulates in the
 * detector's own `frozen_ms`, and the alarm block is gated on
 * `retained_stall.alarm_reason`. None of those three was sampled, so a
 * retrieved ring would have shown the freeze and not the reason for the
 * silence — which would have wasted the retrieval.
 *
 * Narrowing pool_avail is safe and honest rather than lossy: every pool on this
 * board is identified by symbol and sized by Kconfig — acl_tx 8, att 8,
 * discardable 3, fragments 1, hci_cmd 2, hci_rx 10, pkt_pool 4, sync_evt 1 —
 * and there are exactly eight. `pool_count` is still the real count, so a
 * decoder can detect truncation if a ninth pool ever appears.
 *
 * Net effect: the entry stays 40 bytes, the page stays 232, the capacity stays
 * 200 and the span stays 10.0 s. Nothing is traded away for the new fields.
 */
#define BSF_STALL_RING_POOL_SLOTS 8u

typedef struct __attribute__((packed)) {
	uint32_t uptime_ms;
	uint32_t producer_heartbeat;
	uint32_t entry_count;
	uint32_t exit_count;
	/* Detector inputs — why the detector did or did not act at this sample. */
	uint32_t subscribed_notify_ok; /* vs STALL_ARM_NOTIFY_OK: is it armed? */
	uint16_t detector_frozen_ms;   /* the dwell accumulator, saturating */
	uint16_t in_call_age_ms;
	uint8_t in_call_stream;
	uint8_t flags;
	uint8_t queue_depth_ctl;
	uint8_t queue_depth_uwb;
	uint8_t queue_depth_imu;
	uint8_t pool_count; /* real count; > POOL_SLOTS means pool_avail is cut */
	uint8_t alarm_reason; /* retained: non-zero makes the alarm block inert */
	uint8_t alarm_count;
	uint8_t pool_avail[BSF_STALL_RING_POOL_SLOTS];
} bsf_stall_ring_entry_t;

#define BSF_RING_FLAG_CONNECTED      0x01u
#define BSF_RING_FLAG_DATA_SUB       0x02u
#define BSF_RING_FLAG_TELEMETRY_SUB  0x04u
#define BSF_RING_FLAG_NOTIFY_IN_CALL 0x08u
#define BSF_RING_FLAG_FAST_DROP      0x10u
#define BSF_RING_FLAG_RECOVERY_ARMED 0x20u

#define BSF_STALL_RING_PAGE_ENTRIES 5u

/* Freeze causes, in the order they are checked. */
#define BSF_RING_FREEZE_NONE    0u
#define BSF_RING_FREEZE_ALARM   1u /* the detector fired */
#define BSF_RING_FREEZE_NO_EXIT 2u /* ISR latch: producers advancing, no exits */
#define BSF_RING_FREEZE_MANUAL  3u /* operator asked, on a live board */

typedef struct __attribute__((packed)) {
	uint8_t version; /* BSF_STALL_RING_VERSION */
	uint8_t page;
	uint8_t pages;
	uint8_t entries; /* valid entries in this page */
	uint16_t capacity;
	uint16_t count; /* entries held, oldest-first ordering */
	uint32_t boot_id;
	uint32_t oldest_uptime_ms;
	uint32_t newest_uptime_ms;
	/*
	 * The freeze instant is entries_data[freeze_index - 1].uptime_ms once
	 * the page holding it is fetched, and `RING STATUS` carries it too, so
	 * it is not repeated in every page -- the 232-byte budget is tight.
	 */
	uint16_t freeze_index; /* logical index of the freeze point, or count */
	uint16_t page_crc; /* CRC-16-CCITT/FALSE over this page's entry bytes */
	uint8_t frozen;
	uint8_t freeze_reason;
	uint8_t sample_period_ms;
	uint8_t pool_count;
	uint8_t entry_size;
	uint8_t reserved0;
	uint16_t reserved1;
	bsf_stall_ring_entry_t entries_data[BSF_STALL_RING_PAGE_ENTRIES];
} bsf_stall_ring_page_t;

typedef struct __attribute__((packed)) {
	uint8_t version;
	uint8_t kind;
	uint16_t len;
	uint32_t node_uptime_ms;
	uint8_t pool_count;
	uint8_t pool_usage_enabled;
	uint8_t att_sent_cb_after_tx;
	uint8_t reserved;
	bsf_net_buf_pool_usage_t pools[BSF_NET_BUF_POOL_MAX];
} bsf_ble_pool_usage_t;

typedef struct __attribute__((packed)) {
	uint8_t version;
	uint8_t kind;
	uint16_t len;
	uint32_t node_sequence;
	uint32_t node_uptime_ms; /* diagnostic only; not the fusion timebase */
	bsl_uwb_t uwb;
	bsf_capture_record_t capture;
} bsf_ble_uwb_packet_t;

typedef struct __attribute__((packed)) {
	uint8_t version;
	uint8_t kind;
	uint16_t len;
	uint32_t node_uptime_ms;
	uint32_t uart_bytes;
	uint32_t valid_frames;
	uint32_t crc_errors;
	uint32_t header_errors;
	uint32_t ring_dropped_bytes;
	uint32_t dropped_sweeps;
	uint32_t duplicate_sweeps;
	uint32_t out_of_order_sweeps;
	uint32_t notify_ok;
	uint32_t drop_unsub;
	uint32_t drop_err;
	int32_t last_notify_error;
	uint32_t uart_restarts;
	int32_t last_uart_error;
	uint32_t last_sweep;
	uint32_t rising_edge_count;
	uint32_t falling_edge_count;
	uint32_t boot_discarded_edge_count;
	uint32_t edge_queue_drop_count;
	uint32_t orphan_strobe_count;
	uint32_t orphan_edge_count;
	uint32_t orphan_frame_count;
	uint32_t near_window_edge_count;
	uint8_t have_last_sweep;
	uint8_t data_subscribed;
	uint8_t capture_flags;
	uint8_t timer_instance;
	uint8_t timer_counter_bits;
	uint16_t pairing_window_us;
	uint32_t timer_wrap_count;
	uint32_t watchdog_feed_count;
	uint32_t reset_reason;
	uint32_t imu_pulls;
	uint32_t imu_dup;
	uint32_t imu_i2c_err;
	uint32_t imu_records;
	uint32_t ctrl_rx;
	uint32_t ctrl_bad_bsf;
	uint32_t relay_tx;
	uint32_t relay_ack;
	uint32_t relay_timeout;
	uint16_t imu_rate_hz;
	uint8_t imu_batch;
	uint8_t imu_active;
	uint8_t imu_health_class;
	uint8_t imu_health_active;
	uint8_t imu_health_latched;
	uint8_t imu_extended_burst;
	uint32_t imu_health_reset;
	uint32_t imu_health_frozen;
	uint32_t imu_health_rate;
	uint32_t imu_health_canary;
	uint32_t imu_health_plausibility;
	uint32_t imu_health_dead;
	uint32_t imu_health_identical;
	/* Legacy wire field: counts consecutive-I2C-failure escalations. */
	uint32_t imu_health_i2c_burst;
	uint32_t imu_health_recover_ok;
	uint32_t imu_health_recover_fail;
	uint32_t imu_legacy_pull_mean_us;
	uint32_t imu_extended_pull_mean_us;
	uint64_t imu_last_good_ts_us;
	uint64_t imu_fault_ts_us;
	uint64_t imu_recovered_ts_us;
	/* v5: absolute-deadline periods skipped before an accepted IMU sample. */
	uint32_t imu_missed_deadlines;
	/* v5 tail extension: exact values and timestamps remain query-only. */
	uint16_t imu_pull_lateness_max_us;
	uint16_t imu_pull_duration_max_us;
} bsf_ble_telemetry_t;

/*
 * kind=3 is variable length:
 *
 *   bsf_ble_imu_prefix_t
 *   N * bsf_ble_imu_sample_t
 *   int16_t temperature_raw
 *
 * N is derived exactly from len and must be 1..10. seq identifies the first
 * accepted sensor frame in the record. Freshness is determined by the JY61P
 * chip-ms register, not by equality of the quantized motion bytes. The
 * The kind-3 wire layout is frozen. base_timer2_ts_us carries the low 32 bits
 * of the shared TIMER2 time at that frame's TWIM pull initiation; subsequent
 * samples carry unsigned microsecond deltas from the base. A receiver must
 * extend the low word against its most recent full-width UWB timestamp,
 * telemetry wrap count, or preceding extended IMU base before exposing it to
 * downstream consumers.
 */
typedef struct __attribute__((packed)) {
	uint8_t version;
	uint8_t kind;
	uint16_t len;
	uint16_t seq;
	uint32_t base_timer2_ts_us;
} bsf_ble_imu_prefix_t;

typedef struct __attribute__((packed)) {
	uint16_t delta_us;
	int16_t acc[3];
	int16_t gyro[3];
} bsf_ble_imu_sample_t;

typedef struct __attribute__((packed)) {
	uint8_t version;
	uint8_t kind;
	uint16_t len;
	uint8_t source; /* 0=B306, 1=tag */
	uint16_t correlation;
} bsf_ble_control_reply_prefix_t;

typedef struct __attribute__((packed)) {
	uint8_t version;
	uint8_t kind;
	uint16_t len;
	uint32_t node_uptime_ms;
	uint32_t q_drop_imu;
	uint32_t q_drop_uwb;
	uint32_t q_drop_ctl;
	uint16_t q_hwm_imu;
	uint16_t q_hwm_uwb;
	uint16_t q_hwm_ctl;
	uint32_t publisher_count;
	uint32_t publisher_max_us;
	uint32_t enq_imu;
	uint32_t enq_uwb;
	uint32_t enq_ctl;
	uint32_t abort_imu;
	uint32_t abort_uwb;
	uint32_t abort_ctl;
} bsf_ble_queue_counters_t;

#define BSF_CONTROL_SOURCE_B306 0u
#define BSF_CONTROL_SOURCE_TAG  1u
#define BSF_IMU_RECORD_LEN(n) \
	(sizeof(bsf_ble_imu_prefix_t) + \
	 (size_t)(n) * sizeof(bsf_ble_imu_sample_t) + sizeof(int16_t))

_Static_assert(sizeof(bsf_capture_record_t) == 82u,
	       "Fusion capture record size drifted");
_Static_assert(sizeof(bsf_ble_uwb_packet_t) == 184u,
	       "Fusion BLE UWB packet size drifted");
_Static_assert(sizeof(bsf_ble_telemetry_t) == 243u,
	       "Fusion BLE telemetry packet size drifted");
_Static_assert(sizeof(bsf_ble_telemetry_t) <= 244u,
	       "Fusion telemetry exceeds negotiated ATT payload");
_Static_assert(sizeof(bsf_ble_imu_prefix_t) == 10u,
	       "Fusion IMU prefix size drifted");
_Static_assert(sizeof(bsf_ble_imu_sample_t) == 14u,
	       "Fusion IMU sample size drifted");
_Static_assert(BSF_IMU_RECORD_LEN(BSF_IMU_BATCH_MAX) == 236u,
		       "Fusion maximum IMU record size drifted");
_Static_assert(BSF_IMU_RECORD_LEN(BSF_IMU_BATCH_MAX) <= 244u,
		       "Fusion maximum IMU record exceeds ATT payload budget");
_Static_assert(sizeof(bsf_ble_control_reply_prefix_t) == 7u,
	       "Fusion control reply prefix size drifted");
_Static_assert(sizeof(bsf_ble_queue_counters_t) == 58u,
		       "Fusion queue-counter record size drifted");
_Static_assert(sizeof(bsf_ble_pool_usage_t) == 140u,
	       "Fusion pool-usage record size drifted");
_Static_assert(sizeof(bsf_stall_status_t) <= 244u,
	       "Fusion stall status exceeds negotiated ATT payload");
_Static_assert(sizeof(bsf_stall_ring_entry_t) == 40u,
	       "Fusion stall ring entry size drifted");
_Static_assert(sizeof(bsf_stall_ring_page_t) == 232u,
	       "Fusion stall ring page size drifted");
_Static_assert(sizeof(bsf_stall_ring_page_t) == sizeof(bsf_stall_status_t),
	       "Both stall wire forms must read back at the same length");
_Static_assert(sizeof(bsf_ble_control_reply_prefix_t) +
		       BSF_CONTROL_REPLY_TEXT_MAX <= 247u,
	       "Fusion control reply exceeds ATT payload budget");

#ifdef __cplusplus
}
#endif

#endif /* BIOSPUR_FUSION_BLE_H */
