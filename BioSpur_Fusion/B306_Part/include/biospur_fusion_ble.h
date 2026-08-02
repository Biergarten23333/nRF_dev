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

#define BSF_IMU_BATCH_MIN       1u
#define BSF_IMU_BATCH_MAX       10u
#define BSF_IMU_BATCH_DEFAULT   5u
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
 *
 * Expand with BT_UUID_128_ENCODE() after including Zephyr's uuid.h.
 */
#define BSF_BLE_UUID_SERVICE_W32   0x7b120001u
#define BSF_BLE_UUID_DATA_W32      0x7b120002u
#define BSF_BLE_UUID_TELEMETRY_W32 0x7b120003u
#define BSF_BLE_UUID_CONTROL_W32   0x7b120004u
#define BSF_BLE_UUID_W16_1         0x4e77u
#define BSF_BLE_UUID_W16_2         0x4a71u
#define BSF_BLE_UUID_W16_3         0xa045u
#define BSF_BLE_UUID_W48           0x7b4d3f2a9000ULL

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
_Static_assert(BSF_IMU_RECORD_LEN(BSF_IMU_BATCH_MAX) == 152u,
		       "Fusion maximum IMU record size drifted");
_Static_assert(BSF_IMU_RECORD_LEN(BSF_IMU_BATCH_MAX) <= 244u,
		       "Fusion maximum IMU record exceeds ATT payload budget");
_Static_assert(sizeof(bsf_ble_control_reply_prefix_t) == 7u,
	       "Fusion control reply prefix size drifted");
_Static_assert(sizeof(bsf_ble_queue_counters_t) == 58u,
		       "Fusion queue-counter record size drifted");
_Static_assert(sizeof(bsf_ble_control_reply_prefix_t) +
		       BSF_CONTROL_REPLY_TEXT_MAX <= 247u,
	       "Fusion control reply exceeds ATT payload budget");

#ifdef __cplusplus
}
#endif

#endif /* BIOSPUR_FUSION_BLE_H */
