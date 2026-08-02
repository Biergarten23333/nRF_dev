#include "ss_twr_init.h"
#include "biospur_link.h"
#include "biospur_uart_link.h"
#include "broadcast_tdma.h"
#include "broadcast_tdma_math.h"
#include "tag_beacon_sync.h"
#include "tag_relay6.h"
#include "tag_relay8.h"
#include "tag_run_state.h"
#include "uwb_beacon.h"
#include "uwb_tdma.h"
#if APP_TAG_BLE_ENABLE
#include "uwb_tag_ble.h"
#else
enum uwb_tag_ble_cal_status {
    UWB_TAG_BLE_CAL_STATUS_OK = 0,
    UWB_TAG_BLE_CAL_STATUS_REJECT = 1,
    UWB_TAG_BLE_CAL_STATUS_TIMEOUT = 2,
    UWB_TAG_BLE_CAL_STATUS_ERROR = 3,
};
static inline bool uwb_tag_ble_tr_enabled(void)
{
    return true;
}
static inline bool uwb_tag_ble_identity_is_nvs(void)
{
    return false;
}
static inline void uwb_tag_ble_publish_link_status(void)
{
}
#endif
#include "uwb_range_tracker.h"
#include "uwb_ss_twr_shared.h"

#include <math.h>
#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <strings.h>

#include <deca_device_api.h>
#include <deca_regs.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/base64.h>
#include <zephyr/sys/util.h>

#define SS_TWR_INIT_TX_ANT_DLY 16436U
#define SS_TWR_INIT_RX_ANT_DLY 16436U

#ifndef APP_TAG_RNG_DELAY_MS
#define APP_TAG_RNG_DELAY_MS 1000U
#endif

#ifndef APP_TAG_CAL_RNG_SETTLE_US
#define APP_TAG_CAL_RNG_SETTLE_US 0U
#endif

#ifndef APP_TAG_TX_TO_RX_DLY_UUS
#define APP_TAG_TX_TO_RX_DLY_UUS 140U
#endif

#ifndef APP_TAG_RESP_RX_TIMEOUT_UUS
#define APP_TAG_RESP_RX_TIMEOUT_UUS 1500U
#endif

#ifndef APP_ALT_SS_TWR_ENABLE
#define APP_ALT_SS_TWR_ENABLE 0U
#endif

#ifndef APP_ALT_SS_TWR_BCAST_ENABLE
#define APP_ALT_SS_TWR_BCAST_ENABLE 0U
#endif

#ifndef APP_ALT_SS_TWR_MODE
#define APP_ALT_SS_TWR_MODE 2U
#endif

#define APP_ALT_SS_TWR_MODE_UNICAST 1U
#define APP_ALT_SS_TWR_MODE_BROADCAST 2U

#ifndef APP_UWB_HW_FRAME_FILTER_ENABLE
#define APP_UWB_HW_FRAME_FILTER_ENABLE 1U
#endif

#ifndef APP_ALT_SS_TWR_POLL_SPACING_US
#define APP_ALT_SS_TWR_POLL_SPACING_US 200U
#endif

#ifndef APP_ALT_SS_TWR_GUARD_US
#define APP_ALT_SS_TWR_GUARD_US 500U
#endif

#ifndef APP_ALT_SS_TWR_RESP_SPACING_US
#define APP_ALT_SS_TWR_RESP_SPACING_US 800U
#endif

#ifndef APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP
#define APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP 1U
#endif

#ifndef APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE
#define APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE 0U
#endif

#ifndef APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE
#define APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE 0U
#endif

#ifndef APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE
#define APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE 0U
#endif

#ifndef APP_TAG_SWEEP_DIAG_ENABLE
#define APP_TAG_SWEEP_DIAG_ENABLE 0U
#endif

#ifndef APP_TAG_SWEEP_DIAG_PERIOD
#define APP_TAG_SWEEP_DIAG_PERIOD 10U
#endif

#ifndef APP_TAG_TR_BCAST_V2_ENABLE
#define APP_TAG_TR_BCAST_V2_ENABLE 0U
#endif

#ifndef APP_TAG_TDMA_SLOT_PERIOD_MS
#define APP_TAG_TDMA_SLOT_PERIOD_MS 0U
#endif

#ifndef APP_TAG_TDMA_SLOT_COUNT
#define APP_TAG_TDMA_SLOT_COUNT 0U
#endif

#ifndef APP_TAG_ALT_POLL_DIAG_PERIOD_MS
#define APP_TAG_ALT_POLL_DIAG_PERIOD_MS 5000U
#endif

#ifndef APP_TAG_ALT_RXG_BLE_DIAG_ENABLE
#define APP_TAG_ALT_RXG_BLE_DIAG_ENABLE 0U
#endif

#ifndef APP_TAG_SUMMARY_PERIOD
#define APP_TAG_SUMMARY_PERIOD 1U
#endif

#ifndef APP_TAG_PENDING_PRINT_PERIOD
#define APP_TAG_PENDING_PRINT_PERIOD 20U
#endif

#ifndef APP_TAG_NORMAL_OUTPUT_ENABLE
#define APP_TAG_NORMAL_OUTPUT_ENABLE 1U
#endif

#ifndef APP_TAG_CIR_FEATURE_OUTPUT_ENABLE
#define APP_TAG_CIR_FEATURE_OUTPUT_ENABLE 0U
#endif

#ifndef APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE
#define APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE 1U
#endif

#ifndef APP_TAG_CIR_FULL_OUTPUT_ENABLE
#define APP_TAG_CIR_FULL_OUTPUT_ENABLE 0U
#endif

#ifndef APP_TAG_CIR_FULL_CHUNK_BYTES
#define APP_TAG_CIR_FULL_CHUNK_BYTES 48U
#endif

#ifndef APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE
#define APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE 1U
#endif

#ifndef APP_TAG_CIR_FULL_PRIORITY_MASK
#define APP_TAG_CIR_FULL_PRIORITY_MASK 0U
#endif

#ifndef APP_TAG_CIR_FULL_PRIORITY_ONLY_SWEEP
#define APP_TAG_CIR_FULL_PRIORITY_ONLY_SWEEP 0U
#endif

#ifndef APP_TAG_CIR_COMPACT_SAMPLE_PERIOD
#define APP_TAG_CIR_COMPACT_SAMPLE_PERIOD 8U
#endif

#ifndef APP_TAG_RF_DIAG_OUTPUT_ENABLE
#define APP_TAG_RF_DIAG_OUTPUT_ENABLE 0U
#endif

#ifndef APP_TAG_RF_DIAG_OUTPUT_BLE_ENABLE
#define APP_TAG_RF_DIAG_OUTPUT_BLE_ENABLE 1U
#endif

#ifndef APP_TAG_RF_DIAG_OUTPUT_PERIOD
#define APP_TAG_RF_DIAG_OUTPUT_PERIOD 1U
#endif

#ifndef APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE
#define APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE 0U
#endif

#ifndef APP_TAG_TR_RF_DIAG_COMPACT_ENABLE
#define APP_TAG_TR_RF_DIAG_COMPACT_ENABLE APP_TAG_RF_DIAG_OUTPUT_ENABLE
#endif

#ifndef APP_TAG_RF_DIAG_TAG_RX_ENABLE
#define APP_TAG_RF_DIAG_TAG_RX_ENABLE 0U
#endif

/* Compile-time default for the runtime DIAG toggle (ss_twr_init_rf_diag_runtime_on).
 * 0 = boot with DIAG OFF (production ranging). This is precisely what makes it
 * SAFE to compile the Tag-side hot-path RX diag read IN
 * (APP_TAG_RF_DIAG_TAG_RX_ENABLE=1, as the freeze ships). */
#ifndef APP_TAG_RF_DIAG_RUNTIME_DEFAULT_ON
#define APP_TAG_RF_DIAG_RUNTIME_DEFAULT_ON 0U
#endif

/* freeze-clean batch6 (ii) compile-time guard — ge7=0 regression, 2026-07-14.
 * The regression was the Tag-side RX diagnostics READ running on the ranging
 * critical path. Compiling the read IN (APP_TAG_RF_DIAG_TAG_RX_ENABLE=1, as the
 * freeze ships) is SAFE as long as the runtime DIAG toggle boots OFF so the read
 * is gated off in production. The FATAL COMBO is the read compiled in AND DIAG
 * defaulting ON -- that runs it in production and collapses ge7. This guard
 * fires ONLY on that combo; the freeze (=1, runtime-default OFF) compiles. */
#if APP_TAG_RF_DIAG_TAG_RX_ENABLE != 0U && APP_TAG_RF_DIAG_RUNTIME_DEFAULT_ON != 0U
#error "fatal combo (ge7=0, 2026-07-14): APP_TAG_RF_DIAG_TAG_RX_ENABLE=1 (Tag RX diag on the ranging hot path) with APP_TAG_RF_DIAG_RUNTIME_DEFAULT_ON=1 runs the hot-path read in production and collapses ge7. Keep the runtime DIAG default OFF when the hot-path read is compiled in."
#endif

#ifndef APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE
#define APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE 255U
#endif

#define SS_TWR_INIT_TR_STATUS_MAX_LEN 256U
#define SS_TWR_INIT_RF_DIAG_COMPACT_VERSION 1U
#define SS_TWR_INIT_RF_DIAG_COMPACT_RECORD_LEN 8U
#define SS_TWR_INIT_RF_DIAG_COMPACT_MAX_RAW \
    (UWB_MAX_ANCHORS * SS_TWR_INIT_RF_DIAG_COMPACT_RECORD_LEN)
#define SS_TWR_INIT_RF_DIAG_COMPACT_MAX_B64 \
    (((SS_TWR_INIT_RF_DIAG_COMPACT_MAX_RAW + 2U) / 3U) * 4U + 1U)

/* freeze-clean batch4b: de-overload the "TR;3" token. The production broadcast
 * path (APP_TAG_TR_BCAST_V2_ENABLE) emits "TR;2"/"TR;3" via the runtime DIAG
 * gate; this legacy non-BCAST_V2 path historically used 3U/4U for the SAME
 * leading token. Renumber to 13U/14U so "TR;3" unambiguously means the
 * production compact-diag format. (This path is not compiled in the freeze.) */
#define SS_TWR_INIT_TR_RANGE_VERSION 13U

#ifndef APP_TAG_CONSOLE_SUMMARY_ENABLE
#define APP_TAG_CONSOLE_SUMMARY_ENABLE 1U
#endif

#ifndef APP_TAG_LEGACY_TETRA_VOLUME_MIN_M3
#define APP_TAG_LEGACY_TETRA_VOLUME_MIN_M3 0.1
#endif

#if APP_TAG_USB_DIAG_TRACE
static void ss_twr_diag_write(const char *msg)
{
    const struct device *console = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

    if (!device_is_ready(console) || msg == NULL) {
        return;
    }

    while (*msg != '\0') {
        uart_poll_out(console, *msg++);
    }
}
#endif

#ifndef APP_TAG_LEGACY_STATIC_SLOT_DIVIDER
#define APP_TAG_LEGACY_STATIC_SLOT_DIVIDER 1U
#endif

#ifndef APP_TAG_BLE_COMPACT_STATUS
#define APP_TAG_BLE_COMPACT_STATUS 0U
#endif

#ifndef APP_TAG_USB_MIRROR_BLE_STATUS
#define APP_TAG_USB_MIRROR_BLE_STATUS 0U
#endif

#ifndef APP_TAG_VERBOSE_RANGING
#define APP_TAG_VERBOSE_RANGING 1U
#endif

#ifndef APP_TAG_VERBOSE_MEASUREMENTS
#define APP_TAG_VERBOSE_MEASUREMENTS 1U
#endif

#ifndef APP_TAG_VERBOSE_PERF
#define APP_TAG_VERBOSE_PERF 1U
#endif

#define SS_TWR_INIT_RNG_DELAY_MS APP_TAG_RNG_DELAY_MS
#define SS_TWR_INIT_CAL_RNG_SETTLE_US APP_TAG_CAL_RNG_SETTLE_US
#define SS_TWR_INIT_TX_TO_RX_DLY_UUS APP_TAG_TX_TO_RX_DLY_UUS
#define SS_TWR_INIT_RESP_RX_TIMEOUT_UUS APP_TAG_RESP_RX_TIMEOUT_UUS

#define SS_TWR_INIT_RX_BUF_LEN 127U
#define SS_TWR_INIT_ALL_MSG_COMMON_LEN 10U
#define SS_TWR_INIT_MSG_SN_IDX 2U
#define SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX UWB_MSG_RESP_POLL_RX_TS_IDX
#define SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX UWB_MSG_RESP_RESP_TX_TS_IDX
#define SS_TWR_INIT_RESP_MSG_TS_LEN UWB_MSG_RESP_TS_LEN
#define SS_TWR_INIT_LEGACY_POLL_FRAME_LEN 13U
#define SS_TWR_INIT_UUS_TO_DWT_TIME 65536ULL
#define SS_TWR_INIT_ALT_BCAST_POLL_SCHED_UUS 3000U
#define SS_TWR_INIT_ALT_BCAST_TX_DONE_TIMEOUT_US 5000U
/*
 * Tail margin must cover the last responder's full frame airtime plus RX
 * (re)enable latency and clock jitter.  With 8 anchors at 1000 us spacing the
 * rank-7 responder transmits at ANCHOR_RESP_DELAY(1200) + 7*1000 us after poll
 * TX-done and its frame completes near ~8.45 ms.  300 us closed the collector
 * window ~235 us early, so anchor 7 (always the last responder) was dropped
 * systematically, capping every sweep at ge7 and making ge8 near-impossible.
 * 800 us keeps the window open through rank 7 with margin while staying inside
 * the 9 ms active slot.
 */
#define SS_TWR_INIT_ALT_BCAST_TAIL_MARGIN_US 800U
#define SS_TWR_INIT_ALT_BCAST_POLL_AIRTIME_US 335U
#define SS_TWR_INIT_ALT_BCAST_SLOT_RX_EARLY_US 150U
#define SS_TWR_INIT_ALT_BCAST_SLOT_RX_TIMEOUT_US 850U

#define SS_TWR_INIT_SPEED_OF_LIGHT 299702547.0
#define SS_TWR_INIT_SLOT_EXCHANGE_BUDGET_MS \
	(((SS_TWR_INIT_TX_TO_RX_DLY_UUS + SS_TWR_INIT_RESP_RX_TIMEOUT_UUS + 999U) / \
	  1000U) + 1U)
#define SS_TWR_INIT_SLOT_GUARD_MARGIN_MS 1U

enum ss_twr_init_solve_reason {
	SS_TWR_INIT_SOLVE_NONE = 0,
	SS_TWR_INIT_SOLVE_SUCCESS,
	SS_TWR_INIT_SOLVE_PENDING,
	SS_TWR_INIT_SOLVE_REJECTED,
	SS_TWR_INIT_SOLVE_SLOT_CUT_SHORT,
};

static dwt_config_t ss_twr_init_config = {
    APP_UWB_CHANNEL,
    DWT_PRF_64M,
    DWT_PLEN_128,
    DWT_PAC8,
    9,
    9,
    1,
    DWT_BR_6M8,
    DWT_PHRMODE_STD,
    129,
};

static uint8_t ss_twr_init_frame_seq_nb;
static uint8_t ss_twr_init_rx_buffer[SS_TWR_INIT_RX_BUF_LEN];
static uint8_t ss_twr_init_tx_poll_msg[UWB_MSG_ALT_POLL_FRAME_LEN];
static uint8_t ss_twr_init_tx_resp_msg[20];
static uint16_t ss_twr_init_local_addr;
static uint8_t ss_twr_init_local_tag_id;
static uint16_t ss_twr_init_identity_code;
static bool ss_twr_init_radio_configured;
static struct uwb_range_tracker ss_twr_init_trackers[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_anchor_ids[UWB_MAX_ANCHORS];
static size_t ss_twr_init_anchor_count;
static bool ss_twr_init_multitag_anchor_plan_mode;
static uint8_t ss_twr_init_active_plan_ids[UWB_TAG_ACTIVE_ANCHOR_MAX];
static size_t ss_twr_init_active_plan_count;
static uint8_t ss_twr_init_standby_plan_ids[UWB_TAG_STANDBY_ANCHOR_MAX];
static size_t ss_twr_init_standby_plan_count;
static uint8_t ss_twr_init_reserve_plan_ids[UWB_TAG_RESERVE_ANCHOR_MAX];
static size_t ss_twr_init_reserve_plan_count;
static uint8_t ss_twr_init_refresh_anchor_budget;
static uint16_t ss_twr_init_refresh_interval_sweeps;
static uint16_t ss_twr_init_full_sweep_interval_sweeps;
static uint8_t ss_twr_init_plan_refresh_cursor;
static struct uwb_tdma_schedule ss_twr_init_tdma_schedule;
static uint8_t ss_twr_init_active_anchor_ids[UWB_MAX_ANCHORS];
static size_t ss_twr_init_active_anchor_count;
static size_t ss_twr_init_active_anchor_index;
static uint8_t ss_twr_init_current_anchor_retry_count;
static uint32_t ss_twr_init_sweep_count;

static uint32_t ss_twr_init_public_sweep(void)
{
#if APP_TAG_RELAY6_COUNTER_ENABLE != 0U
    return tag_relay6_public_sweep(ss_twr_init_sweep_count);
#else
    const struct uwb_tdma_schedule *schedule = &ss_twr_init_tdma_schedule;

    return broadcast_tdma_public_sweep(
        schedule->superframe_valid, schedule->epoch_valid,
        schedule->superframe_base, schedule->sync_local_ms,
        k_uptime_get_32(), 100U, ss_twr_init_sweep_count);
#endif
}

#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U
struct ss_twr_init_rf_diag_sample {
    uint16_t fp_index;
    uint16_t fp_ampl1;
    uint16_t fp_ampl2;
    uint16_t fp_ampl3;
    uint16_t cir_pwr;
    uint16_t rxpacc;
    uint16_t std_noise;
    uint16_t lde_thresh; /* LDE_THRESH (0x2E:0000); tag-local only, 0 over-air */
    uint32_t agc_stat1;  /* AGC_STAT1 (0x23:1E) bits 20:0; tag-local only, 0 over-air */
    uint8_t flags;
    uint8_t temp_raw;  /* responder DW1000 chip temp, raw SAR (V3 frame); 0=n/a */
    uint8_t vbat_raw;  /* responder DW1000 Vbat, raw SAR (V3 frame); 0=n/a */
};
#endif
static uint8_t ss_twr_init_refresh_anchor_cursor;
static bool ss_twr_init_current_sweep_full;
static bool ss_twr_init_current_sweep_refresh;
static struct uwb_tag_runtime_params ss_twr_init_runtime_params;
static struct uwb_tag_runtime_params ss_twr_init_pending_runtime_params;
static bool ss_twr_init_runtime_update_pending;
static struct tag_relay8_epoch_label ss_twr_init_sweep_epoch;
static struct {
	uint64_t next_window_origin40;
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
	uint64_t last_origin40;
#endif
	uint32_t rx_beacon;
	uint32_t last_counter;
	uint32_t period_mismatch;
	uint32_t missed_windows;
	uint32_t last_valid_ms;
	uint32_t reacquire_jumps;
#if APP_TAG_RELAY6_COUNTER_ENABLE != 0U
	uint32_t generation_rebases;
#endif
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
	uint32_t dw_anchor_fallbacks;
#endif
	struct tag_beacon_preference preference;
	struct tag_relay8_epoch_label epoch;
	uint8_t last_generation;
	bool locked;
} ss_twr_init_beacon;
static atomic_t ss_twr_init_cir_mode;
static atomic_t ss_twr_init_poll_tx_failures;
static atomic_t ss_twr_init_poll_tx_last_error;
static atomic_t ss_twr_init_slot_sleep_late_skips;
static atomic_t ss_twr_init_slot_spin_late_skips;
static void ss_twr_init_record_poll_tx_failure(int error)
{
    atomic_inc(&ss_twr_init_poll_tx_failures);
    atomic_set(&ss_twr_init_poll_tx_last_error, error);
}

void ss_twr_init_poll_tx_stats_snapshot(
    struct ss_twr_init_poll_tx_stats *stats)
{
    if (stats == NULL) {
        return;
    }

    stats->failures =
        (uint32_t)atomic_get(&ss_twr_init_poll_tx_failures);
    stats->last_error =
        (int32_t)atomic_get(&ss_twr_init_poll_tx_last_error);
    stats->slot_sleep_late_skips =
        (uint32_t)atomic_get(&ss_twr_init_slot_sleep_late_skips);
    stats->slot_spin_late_skips =
        (uint32_t)atomic_get(&ss_twr_init_slot_spin_late_skips);
}

void ss_twr_init_beacon_status_snapshot(
	struct ss_twr_init_beacon_status *status)
{
	if (status == NULL) {
		return;
	}

	status->rx_beacon = ss_twr_init_beacon.rx_beacon;
	status->last_counter = ss_twr_init_beacon.last_counter;
	status->period_mismatch = ss_twr_init_beacon.period_mismatch;
	status->missed_windows = ss_twr_init_beacon.missed_windows;
	status->generation_rebases = 0U;
	status->dw_anchor_fallbacks = 0U;
#if APP_TAG_RELAY6_COUNTER_ENABLE != 0U
	status->generation_rebases =
		ss_twr_init_beacon.generation_rebases;
#endif
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
	status->dw_anchor_fallbacks =
		ss_twr_init_beacon.dw_anchor_fallbacks;
#endif
	status->last_generation = ss_twr_init_beacon.last_generation;
	status->promoted_source_in_use =
		ss_twr_init_beacon.preference.promoted_source_in_use;
	status->locked = ss_twr_init_beacon.locked;
	status->enabled = ss_twr_init_runtime_params.beacon_sync;
	status->beacon_win_n =
		ss_twr_init_runtime_params.beacon_win_n;
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
	status->dw_anchor = ss_twr_init_runtime_params.dw_anchor;
#else
	status->dw_anchor = false;
#endif
}

static void ss_twr_init_beacon_reset(void)
{
	memset(&ss_twr_init_beacon, 0, sizeof(ss_twr_init_beacon));
	tag_beacon_preference_init(&ss_twr_init_beacon.preference);
	tag_relay8_epoch_invalidate(&ss_twr_init_beacon.epoch);
	tag_relay8_epoch_invalidate(&ss_twr_init_sweep_epoch);
}

/*
 * Return 0 for a non-beacon, 1 for a recognized but rejected beacon, and 2
 * for an accepted beacon.  Recognition is deliberately independent of
 * BEACON_SYNC so a beacon can never fall through into SS-TWR response
 * processing.
 */
static int ss_twr_init_consume_beacon(const uint8_t *frame, size_t frame_len,
				      uint64_t rx_ts40)
{
	struct uwb_beacon_payload payload;
	uint32_t configured_period_us;
	uint64_t origin40;
	uint64_t local_before_us;
	uint64_t local_after_us;
	uint64_t local_now_us;
	uint64_t dw_now40;
	uint64_t local_origin_us;
	uint8_t dw_now_bytes[5];

	if (!uwb_beacon_parse_frame(frame, frame_len, &payload)) {
		return 0;
	}
	if (!ss_twr_init_runtime_params.beacon_sync) {
		return 1;
	}

	configured_period_us =
		(uint32_t)ss_twr_init_tdma_schedule.slot_count *
		(uint32_t)ss_twr_init_tdma_schedule.slot_period_ms * 1000U;
	if (!uwb_tdma_schedule_is_valid(&ss_twr_init_tdma_schedule) ||
	    payload.cycle_period_us != configured_period_us) {
		ss_twr_init_beacon.period_mismatch++;
		return 1;
	}
	if (!tag_beacon_preference_accept(&ss_twr_init_beacon.preference,
					  payload.beacon_index,
					  payload.flags)) {
		return 1;
	}

	origin40 = uwb_beacon_origin_from_rx(rx_ts40, payload.tx_offset_us);
	if (ss_twr_init_beacon.locked &&
	    (uint64_t)llabs(uwb_beacon_diff40(
		    origin40, ss_twr_init_beacon.next_window_origin40)) >
		    uwb_beacon_us_to_dw_ticks(
			    TAG_BEACON_DIRECT_CORRECTION_US)) {
		ss_twr_init_beacon.reacquire_jumps++;
	}

	local_before_us = k_ticks_to_us_floor64(k_uptime_ticks());
	dwt_readsystime(dw_now_bytes);
	local_after_us = k_ticks_to_us_floor64(k_uptime_ticks());
	local_now_us = local_before_us +
		       ((local_after_us - local_before_us) / 2U);
	dw_now40 = bsl_ts40_get(dw_now_bytes);
	local_origin_us =
		tag_beacon_local_origin_us(local_now_us, dw_now40, origin40);

#if APP_TAG_RELAY6_COUNTER_ENABLE != 0U
	if (tag_relay6_generation_rebase(
		    ss_twr_init_beacon.locked,
		    ss_twr_init_beacon.last_generation,
		    payload.schedule_generation)) {
		ss_twr_init_beacon.generation_rebases++;
	}
#endif
	uwb_tdma_sync_schedule_local_epoch_us(
		&ss_twr_init_tdma_schedule, local_origin_us);
	ss_twr_init_tdma_schedule.superframe_base =
		payload.superframe_counter;
	ss_twr_init_tdma_schedule.superframe_valid = true;
	ss_twr_init_runtime_params.tdma = ss_twr_init_tdma_schedule;

	ss_twr_init_beacon.rx_beacon++;
	ss_twr_init_beacon.last_counter = payload.superframe_counter;
	ss_twr_init_beacon.last_generation = payload.schedule_generation;
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
	ss_twr_init_beacon.last_origin40 = origin40;
#endif
	ss_twr_init_beacon.last_valid_ms = k_uptime_get_32();
	ss_twr_init_beacon.locked = true;
	tag_relay8_epoch_accept(&ss_twr_init_beacon.epoch,
				payload.superframe_counter);
	ss_twr_init_beacon.next_window_origin40 =
		tag_beacon_next_tracking_origin_n(
			origin40, payload.cycle_period_us,
			TAG_BEACON_WINDOW_N_DEFAULT);
	return 2;
}

static uint64_t ss_twr_init_dw_now40(void)
{
	uint8_t timestamp[5];

	dwt_readsystime(timestamp);
	return bsl_ts40_get(timestamp);
}

static void ss_twr_init_beacon_note_missed_window(
	uint32_t configured_period_us)
{
	tag_beacon_preference_note_window(&ss_twr_init_beacon.preference);
	ss_twr_init_beacon.missed_windows++;
	tag_relay8_epoch_coast(&ss_twr_init_beacon.epoch);
	ss_twr_init_beacon.next_window_origin40 =
		tag_beacon_next_tracking_origin_n(
			ss_twr_init_beacon.next_window_origin40,
			configured_period_us,
			TAG_BEACON_WINDOW_N_DEFAULT);
}

#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
static bool ss_twr_init_dw_anchor_can_schedule(void)
{
	return tag_relay6_can_anchor(
		ss_twr_init_runtime_params.dw_anchor,
		ss_twr_init_beacon.locked);
}

static void ss_twr_init_dw_anchor_note_fallback(void)
{
	if (ss_twr_init_runtime_params.dw_anchor) {
		ss_twr_init_beacon.dw_anchor_fallbacks++;
	}
}

static bool ss_twr_init_dw_anchor_target40(uint64_t *target40_out)
{
	uint32_t cycle_period_us;
	uint32_t slot_offset_us;

	if (!ss_twr_init_dw_anchor_can_schedule() ||
	    !uwb_tdma_schedule_is_valid(&ss_twr_init_tdma_schedule)) {
		return false;
	}

	cycle_period_us =
		(uint32_t)ss_twr_init_tdma_schedule.slot_count *
		(uint32_t)ss_twr_init_tdma_schedule.slot_period_ms * 1000U;
	slot_offset_us =
		(uint32_t)ss_twr_init_tdma_schedule.slot_index *
		(uint32_t)ss_twr_init_tdma_schedule.slot_period_ms * 1000U;
	return tag_relay6_next_slot_target40(
		ss_twr_init_beacon.last_origin40, ss_twr_init_dw_now40(),
		slot_offset_us, cycle_period_us, target40_out);
}
#endif

static void ss_twr_init_wait_until_dw_time(uint64_t target40)
{
	for (;;) {
		uint64_t now40 = ss_twr_init_dw_now40();
		int64_t remaining_ticks = uwb_beacon_diff40(target40, now40);
		uint32_t remaining_us;

		if (remaining_ticks <= 0) {
			return;
		}
		remaining_us = uwb_beacon_dw_ticks_to_us(
			(uint64_t)remaining_ticks);
		if (remaining_us > 1500U) {
			k_usleep(remaining_us - 1000U);
		}
	}
}

static bool ss_twr_init_beacon_listen_until(uint64_t deadline40)
{
	bool accepted = false;

	while (uwb_beacon_diff40(deadline40,
				 ss_twr_init_dw_now40()) > 0) {
		uint64_t now40 = ss_twr_init_dw_now40();
		int64_t remaining_ticks =
			uwb_beacon_diff40(deadline40, now40);
		uint32_t remaining_us;
		uint16_t timeout_uus;
		uint32_t status_reg;

		if (remaining_ticks <= 0) {
			break;
		}
		remaining_us = uwb_beacon_dw_ticks_to_us(
			(uint64_t)remaining_ticks);
		timeout_uus = (uint16_t)MIN(remaining_us, 60000U);
		if (timeout_uus == 0U) {
			break;
		}

		dwt_forcetrxoff();
		dwt_write32bitreg(SYS_STATUS_ID,
				  SYS_STATUS_ALL_RX_GOOD |
				  SYS_STATUS_ALL_RX_ERR |
				  SYS_STATUS_ALL_RX_TO);
		dwt_setrxtimeout(timeout_uus);
		if (dwt_rxenable(DWT_START_RX_IMMEDIATE) != DWT_SUCCESS) {
			break;
		}

		do {
			status_reg = dwt_read32bitreg(SYS_STATUS_ID);
			if (uwb_beacon_diff40(deadline40,
					     ss_twr_init_dw_now40()) <= 0) {
				dwt_forcetrxoff();
				break;
			}
		} while ((status_reg & (SYS_STATUS_RXFCG |
					SYS_STATUS_ALL_RX_TO |
					SYS_STATUS_ALL_RX_ERR)) == 0U);

		if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
			uint32_t frame_len =
				dwt_read32bitreg(RX_FINFO_ID) &
				RX_FINFO_RXFLEN_MASK;

			if (frame_len <= sizeof(ss_twr_init_rx_buffer)) {
				uint8_t rx_ts_bytes[5];
				int result;

				dwt_readrxdata(ss_twr_init_rx_buffer,
					       (uint16)frame_len, 0);
				dwt_readrxtimestamp(rx_ts_bytes);
				result = ss_twr_init_consume_beacon(
					ss_twr_init_rx_buffer, frame_len,
					bsl_ts40_get(rx_ts_bytes));
				if (result == 2) {
					accepted = true;
					break;
				}
			}
		}
	}

	dwt_forcetrxoff();
	dwt_write32bitreg(SYS_STATUS_ID,
			  SYS_STATUS_ALL_RX_GOOD | SYS_STATUS_ALL_RX_ERR |
			  SYS_STATUS_ALL_RX_TO);
	dwt_setrxtimeout(SS_TWR_INIT_RESP_RX_TIMEOUT_UUS);
	return accepted;
}

static void ss_twr_init_beacon_listen_if_needed(void)
{
	uint32_t configured_period_us;
	uint64_t now40;
	bool accepted;

	if (!ss_twr_init_runtime_params.beacon_sync ||
	    !ss_twr_init_radio_configured ||
	    !uwb_tdma_schedule_is_valid(&ss_twr_init_tdma_schedule) ||
	    !ss_twr_init_tdma_schedule.epoch_valid) {
		return;
	}

	configured_period_us =
		(uint32_t)ss_twr_init_tdma_schedule.slot_count *
		(uint32_t)ss_twr_init_tdma_schedule.slot_period_ms * 1000U;
	if (ss_twr_init_beacon.locked &&
	    (uint32_t)(k_uptime_get_32() -
		       ss_twr_init_beacon.last_valid_ms) >=
		    TAG_BEACON_REACQUIRE_AFTER_MS) {
		ss_twr_init_beacon.locked = false;
		tag_relay8_epoch_invalidate(&ss_twr_init_beacon.epoch);
	}

	now40 = ss_twr_init_dw_now40();
	if (ss_twr_init_beacon.locked) {
		uint64_t window_start;
		uint64_t window_close;

		while (tag_beacon_tracking_window_expired(
			now40, ss_twr_init_beacon.next_window_origin40)) {
			ss_twr_init_beacon_note_missed_window(
				configured_period_us);
		}

		if (!tag_beacon_tracking_due(
			    now40, ss_twr_init_beacon.next_window_origin40,
			    configured_period_us +
			    TAG_BEACON_TRACK_CLOSE_US)) {
			return;
		}
		window_start = uwb_beacon_sub40(
			ss_twr_init_beacon.next_window_origin40,
			uwb_beacon_us_to_dw_ticks(
				TAG_BEACON_TRACK_START_EARLY_US));
		window_close = uwb_beacon_add40(
			ss_twr_init_beacon.next_window_origin40,
			uwb_beacon_us_to_dw_ticks(
				TAG_BEACON_TRACK_CLOSE_US));
		ss_twr_init_wait_until_dw_time(window_start);
		accepted = ss_twr_init_beacon_listen_until(window_close);
		if (!accepted) {
			ss_twr_init_beacon_note_missed_window(
				configured_period_us);
		}
		return;
	}

	{
		uint32_t until_slot_us =
			uwb_tdma_schedule_time_until_next_slot_us(
				&ss_twr_init_tdma_schedule);
		uint64_t deadline40;

		if (until_slot_us <= TAG_BEACON_TRACK_START_EARLY_US) {
			return;
		}
		deadline40 = uwb_beacon_add40(
			now40, uwb_beacon_us_to_dw_ticks(
				until_slot_us -
				TAG_BEACON_TRACK_START_EARLY_US));
		accepted = ss_twr_init_beacon_listen_until(deadline40);
		if (!accepted) {
			tag_beacon_preference_note_window(
				&ss_twr_init_beacon.preference);
			ss_twr_init_beacon.missed_windows++;
		}
	}
}

static void ss_twr_init_beacon_service_post_sweep_if_urgent(void)
{
	uint64_t now40;

	if (!ss_twr_init_runtime_params.beacon_sync ||
	    !ss_twr_init_beacon.locked) {
		return;
	}
	now40 = ss_twr_init_dw_now40();
	if (!tag_beacon_post_sweep_window_urgent(
		    now40, ss_twr_init_beacon.next_window_origin40)) {
		return;
	}

	/*
	 * The radio is free here, but range publication and sweep preparation
	 * have not run yet. Service an imminent beacon before that software tail
	 * can consume slot 10's sub-millisecond margin. The decision is based on
	 * time-to-window, not on a slot number.
	 */
	ss_twr_init_beacon_listen_if_needed();
}

static bool ss_twr_init_last_sweep_cut_short;
static uint32_t ss_twr_init_last_tdma_wait_ms;
static uint32_t ss_twr_init_last_slot_guard_log_ms;
static uint32_t ss_twr_init_last_solve_pending_log_ms;
static uint32_t ss_twr_init_last_solve_diag_ms;
static enum ss_twr_init_solve_reason ss_twr_init_last_solve_reason;
static uint32_t ss_twr_init_sweep_first_poll_cycle;
static uint32_t ss_twr_init_sweep_last_poll_cycle;
static uint32_t ss_twr_init_sweep_done_cycle;
static uint8_t ss_twr_init_sweep_poll_count;
static bool ss_twr_init_sweep_timing_valid;
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
static uint32_t ss_twr_init_diag_t0_cycles;
static uint32_t ss_twr_init_diag_wait_done_cycles;
static uint32_t ss_twr_init_diag_tx_done_cycles;
static uint32_t ss_twr_init_diag_rx_start_cycles;
static uint32_t ss_twr_init_diag_rx_done_cycles;
static uint32_t ss_twr_init_diag_range_done_cycles;
static uint32_t ss_twr_init_diag_solve_start_cycles;
static uint32_t ss_twr_init_diag_solve_done_cycles;
static uint32_t ss_twr_init_diag_out_start_cycles;
static uint32_t ss_twr_init_diag_out_done_cycles;
static uint32_t ss_twr_init_diag_clean_done_cycles;
static uint32_t ss_twr_init_diag_sweep_count;
#endif
static uint8_t ss_twr_init_sweep_anchor_status[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_sweep_anchor_quality[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_sweep_anchor_reason[UWB_MAX_ANCHORS];
static int32_t ss_twr_init_sweep_anchor_raw_mm[UWB_MAX_ANCHORS];
static uint32_t ss_twr_init_sweep_anchor_range_mm[UWB_MAX_ANCHORS];
static uint32_t ss_twr_init_sweep_anchor_pred_mm[UWB_MAX_ANCHORS];
static uint32_t ss_twr_init_sweep_anchor_resid_mm[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_sweep_anchor_solve_quality[UWB_MAX_ANCHORS];
static bool ss_twr_init_sweep_anchor_diag_published[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_bsl_poll_tx_ts[5];
static uint8_t
    ss_twr_init_bsl_resp_rx_ts[UWB_MAX_ANCHORS][sizeof(ss_twr_init_bsl_poll_tx_ts)];
static int32_t ss_twr_init_bsl_carrier_integrator[UWB_MAX_ANCHORS];
static bool ss_twr_init_bsl_poll_tx_valid;
static bool ss_twr_init_bsl_resp_rx_valid[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_bsl_anchor_mask;
static uint8_t ss_twr_init_bsl_rank_offset;
static uint8_t ss_twr_init_bsl_poll_count;
static uint8_t ss_twr_init_bsl_response_count;
static bool ss_twr_init_bsl_strobe_sent;
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U
static struct ss_twr_init_rf_diag_sample
    ss_twr_init_sweep_anchor_poll_diag[UWB_MAX_ANCHORS];
static struct ss_twr_init_rf_diag_sample
    ss_twr_init_sweep_tag_resp_diag[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_sweep_rf_diag_mask;
#endif

struct ss_twr_init_range_measurement {
	uint8_t anchor_id;
	uint32_t range_mm;
	uint8_t quality_percent;
	bool valid;
};

static void ss_twr_init_prepare_sweep_plan(void);
static bool ss_twr_init_runtime_any_calibration_mode(void);
static const char *ss_twr_init_plan_label(void);
static char ss_twr_init_plan_code(const char *plan_label);
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U
static bool ss_twr_init_rf_diag_output_due(void);
#endif
static const char *ss_twr_init_solve_reason_label(void);
static bool ss_twr_init_anchor_id_in_list(const uint8_t *anchor_ids, size_t count,
                                          uint8_t anchor_id);
static void ss_twr_init_publish_bsl_frame(
    const struct ss_twr_init_range_measurement *measurements, size_t measurement_count);

const char *ss_twr_init_cir_mode_label(enum uwb_tag_cir_mode mode)
{
    switch (mode) {
    case UWB_TAG_CIR_MODE_COMPACT:
        return "compact";
    case UWB_TAG_CIR_MODE_FULL:
        return "full";
    case UWB_TAG_CIR_MODE_OFF:
    default:
        return "off";
    }
}

enum uwb_tag_cir_mode ss_twr_init_cir_mode_get(void)
{
    atomic_val_t value = atomic_get(&ss_twr_init_cir_mode);

    if (value == UWB_TAG_CIR_MODE_COMPACT ||
        value == UWB_TAG_CIR_MODE_FULL) {
        return (enum uwb_tag_cir_mode)value;
    }
    return UWB_TAG_CIR_MODE_OFF;
}

int ss_twr_init_cir_mode_set(enum uwb_tag_cir_mode mode)
{
    if (mode != UWB_TAG_CIR_MODE_COMPACT &&
        mode != UWB_TAG_CIR_MODE_FULL) {
        mode = UWB_TAG_CIR_MODE_OFF;
    }
    atomic_set(&ss_twr_init_cir_mode, (atomic_val_t)mode);
    return 0;
}

int ss_twr_init_cir_mode_parse(const char *text, enum uwb_tag_cir_mode *mode)
{
    const char *value = text;

    if (text == NULL || mode == NULL) {
        return -EINVAL;
    }
    if (strncasecmp(value, "cir=", 4) == 0) {
        value += 4;
    }
    if (strcasecmp(value, "0") == 0 ||
        strcasecmp(value, "off") == 0 ||
        strcasecmp(value, "none") == 0) {
        *mode = UWB_TAG_CIR_MODE_OFF;
        return 0;
    }
    if (strcasecmp(value, "1") == 0 ||
        strcasecmp(value, "compact") == 0 ||
        strcasecmp(value, "feature") == 0) {
        *mode = UWB_TAG_CIR_MODE_COMPACT;
        return 0;
    }
    if (strcasecmp(value, "2") == 0 ||
        strcasecmp(value, "full") == 0 ||
        strcasecmp(value, "raw") == 0) {
        *mode = UWB_TAG_CIR_MODE_FULL;
        return 0;
    }
    return -EINVAL;
}

/* Runtime gate for per-response RF diagnostics on the tag RX hot path
 * (dwt_readdiagnostics + LDE_THRESH/AGC_STAT1 reads + the RFD publish). Default
 * OFF so the ranging hot path matches the stable "nodiag" timing that holds
 * ge7/ge8 at ~0.96; `DIAG ON` enables the reads for experiments (accepting a
 * possible ge7/ge8 hit). Toggled via the BLE `DIAG ON|OFF` command; boot
 * default = OFF (production ranging). This does NOT affect range computation —
 * only whether the diagnostic columns are read/emitted. */
static volatile bool ss_twr_init_rf_diag_runtime_on =
    (APP_TAG_RF_DIAG_RUNTIME_DEFAULT_ON != 0U);

void ss_twr_init_set_rf_diag_runtime(bool enable)
{
    ss_twr_init_rf_diag_runtime_on = enable;
    printk("DIAG runtime %s\n", enable ? "ON" : "OFF");
}

bool ss_twr_init_rf_diag_runtime_enabled(void)
{
    return ss_twr_init_rf_diag_runtime_on;
}

int ss_twr_init_tx_power_apply(const char *preset, uint32_t *applied)
{
    uint32_t value = 0U;
    int rc = uwb_tx_power_preset_lookup(preset, &value);

    if (rc != 0) {
        return rc;
    }
    /* Only TX_POWER (0x1E). DIS_STXP (smart TX) and TC_PGDELAY stay untouched. */
    dwt_write32bitreg(TX_POWER_ID, value);
    printk("TXPWR set 0x%08X\n", (unsigned int)value);
    if (applied != NULL) {
        *applied = value;
    }
    return 0;
}

static bool ss_twr_init_cir_compact_enabled(void)
{
    return ss_twr_init_cir_mode_get() == UWB_TAG_CIR_MODE_COMPACT;
}

static bool ss_twr_init_cir_full_enabled(void)
{
    return ss_twr_init_cir_mode_get() == UWB_TAG_CIR_MODE_FULL;
}

#define SS_TWR_INIT_SWEEP_ANCHOR_PENDING 0xffU

enum ss_twr_init_cal_reason_code {
	SS_TWR_INIT_CAL_REASON_NONE = 0,
	SS_TWR_INIT_CAL_REASON_OK,
	SS_TWR_INIT_CAL_REASON_RANGE_INVALID,
	SS_TWR_INIT_CAL_REASON_RX_TIMEOUT,
	SS_TWR_INIT_CAL_REASON_RX_ERROR,
	SS_TWR_INIT_CAL_REASON_NOT_MEASURED,
};

#if APP_TAG_CIR_FEATURE_OUTPUT_ENABLE != 0U
static void ss_twr_init_publish_cir_features(uint8_t anchor_id,
                                             long raw_distance_mm,
                                             uint32_t resp_rx_ts,
                                             int32_t carrier_integrator,
                                             const dwt_rxdiag_t *diag)
{
    char line[192];

    if (!ss_twr_init_cir_compact_enabled()) {
        return;
    }
    if (diag == NULL) {
        return;
    }

    snprintk(line, sizeof(line),
             "CRX;1;%lu;%u;%ld;%lu;%ld;%u;%u;%u;%u;%u;%u;%u;%u",
             (unsigned long)ss_twr_init_sweep_count,
             (unsigned int)anchor_id,
             raw_distance_mm,
             (unsigned long)resp_rx_ts,
             (long)carrier_integrator,
             (unsigned int)diag->firstPath,
             (unsigned int)diag->firstPathAmp1,
             (unsigned int)diag->firstPathAmp2,
             (unsigned int)diag->firstPathAmp3,
             (unsigned int)diag->maxGrowthCIR,
             (unsigned int)diag->rxPreamCount,
             (unsigned int)diag->stdNoise,
             (unsigned int)diag->maxNoise);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE && APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE != 0U
    (void)uwb_tag_ble_publish_status(line);
#endif
}
#else
static void ss_twr_init_publish_cir_features(uint8_t anchor_id,
                                             long raw_distance_mm,
                                             uint32_t resp_rx_ts,
                                             int32_t carrier_integrator,
                                             const dwt_rxdiag_t *diag)
{
    ARG_UNUSED(anchor_id);
    ARG_UNUSED(raw_distance_mm);
    ARG_UNUSED(resp_rx_ts);
    ARG_UNUSED(carrier_integrator);
    ARG_UNUSED(diag);
}
#endif

#if APP_TAG_CIR_FULL_OUTPUT_ENABLE != 0U
#define SS_TWR_INIT_CIR_ACC_DATA_LEN ACC_MEM_LEN

static uint8_t ss_twr_init_cir_full_target_cursor;
static uint8_t ss_twr_init_cir_full_priority_cursor;
static uint32_t ss_twr_init_cir_full_unicast_sweep;
static bool ss_twr_init_cir_full_unicast_sweep_valid;
static uint8_t ss_twr_init_cir_full_unicast_anchor = UWB_MAX_ANCHORS;

static bool ss_twr_init_cir_full_select_config_anchor(uint8_t *anchor_id_out)
{
    if (anchor_id_out == NULL) {
        return false;
    }

#if APP_TAG_CIR_FULL_PRIORITY_MASK != 0U
    uint8_t priority_ids[UWB_MAX_ANCHORS];
    uint8_t priority_count = 0U;

    for (uint8_t anchor_id = 0U; anchor_id < UWB_MAX_ANCHORS; ++anchor_id) {
        if ((APP_TAG_CIR_FULL_PRIORITY_MASK & (1U << anchor_id)) != 0U &&
            ss_twr_init_anchor_id_in_list(ss_twr_init_anchor_ids,
                                          ss_twr_init_anchor_count,
                                          anchor_id)) {
            priority_ids[priority_count++] = anchor_id;
        }
    }
    if (priority_count > 0U) {
        *anchor_id_out =
            priority_ids[ss_twr_init_cir_full_priority_cursor %
                         priority_count];
        ss_twr_init_cir_full_priority_cursor =
            (uint8_t)((ss_twr_init_cir_full_priority_cursor + 1U) %
                      priority_count);
        return true;
    }
#endif

    if (ss_twr_init_anchor_count == 0U) {
        return false;
    }

    *anchor_id_out =
        ss_twr_init_anchor_ids[ss_twr_init_cir_full_target_cursor %
                               ss_twr_init_anchor_count];
    ss_twr_init_cir_full_target_cursor =
        (uint8_t)((ss_twr_init_cir_full_target_cursor + 1U) %
                  ss_twr_init_anchor_count);
    return true;
}

static bool ss_twr_init_cir_full_should_publish_unicast(uint8_t anchor_id)
{
    if (!ss_twr_init_cir_full_enabled() || anchor_id >= UWB_MAX_ANCHORS) {
        return false;
    }

    if (!ss_twr_init_cir_full_unicast_sweep_valid ||
        ss_twr_init_cir_full_unicast_sweep != ss_twr_init_sweep_count) {
        ss_twr_init_cir_full_unicast_sweep = ss_twr_init_sweep_count;
        ss_twr_init_cir_full_unicast_sweep_valid = true;
        ss_twr_init_cir_full_unicast_anchor = UWB_MAX_ANCHORS;
        (void)ss_twr_init_cir_full_select_config_anchor(
            &ss_twr_init_cir_full_unicast_anchor);
    }

    return anchor_id == ss_twr_init_cir_full_unicast_anchor;
}

static void ss_twr_init_publish_full_cir(uint8_t anchor_id,
                                         long raw_distance_mm,
                                         uint32_t resp_rx_ts,
                                         int32_t carrier_integrator,
                                         const dwt_rxdiag_t *diag)
{
#if APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE != 0U
    static const char hex[] = "0123456789ABCDEF";
    uint8_t chunk[49];
    uint16_t offset = 0U;
    uint16_t chunk_bytes = APP_TAG_CIR_FULL_CHUNK_BYTES;
    uint16_t fp_amp_sum;

    if (diag == NULL || anchor_id >= UWB_MAX_ANCHORS) {
        return;
    }

    if (chunk_bytes == 0U || chunk_bytes > 48U) {
        chunk_bytes = 48U;
    }

    fp_amp_sum = (uint16_t)(diag->firstPathAmp1 + diag->firstPathAmp2 +
                            diag->firstPathAmp3);

    printk("CIRM;1;%lu;%u;%ld;%lu;%ld;%u;%u;%u;%u;%u\n",
           (unsigned long)ss_twr_init_sweep_count, (unsigned int)anchor_id,
           raw_distance_mm, (unsigned long)resp_rx_ts,
           (long)carrier_integrator, (unsigned int)diag->firstPath,
           (unsigned int)fp_amp_sum, (unsigned int)diag->maxGrowthCIR,
           (unsigned int)diag->stdNoise,
           (unsigned int)SS_TWR_INIT_CIR_ACC_DATA_LEN);

    while (offset < SS_TWR_INIT_CIR_ACC_DATA_LEN) {
        uint16_t len = MIN(chunk_bytes,
                           (uint16_t)(SS_TWR_INIT_CIR_ACC_DATA_LEN - offset));
        char line[144];
        size_t pos = 0U;

        memset(chunk, 0, (size_t)len + 1U);
        dwt_readaccdata(chunk, (uint16)(len + 1U), offset);

        pos += (size_t)snprintk(line + pos, sizeof(line) - pos,
                                "CIRD;1;%lu;%u;%u;%u;",
                                (unsigned long)ss_twr_init_sweep_count,
                                (unsigned int)anchor_id,
                                (unsigned int)offset, (unsigned int)len);
        for (uint16_t i = 0U; i < len && pos + 2U < sizeof(line); ++i) {
            uint8_t b = chunk[1U + i];
            line[pos++] = hex[(b >> 4) & 0x0fU];
            line[pos++] = hex[b & 0x0fU];
        }
        line[pos < sizeof(line) ? pos : sizeof(line) - 1U] = '\0';
        printk("%s\n", line);
        offset = (uint16_t)(offset + len);
    }

    printk("CIRE;1;%lu;%u;%u\n",
           (unsigned long)ss_twr_init_sweep_count, (unsigned int)anchor_id,
           (unsigned int)SS_TWR_INIT_CIR_ACC_DATA_LEN);
#else
    ARG_UNUSED(anchor_id);
    ARG_UNUSED(raw_distance_mm);
    ARG_UNUSED(resp_rx_ts);
    ARG_UNUSED(carrier_integrator);
    ARG_UNUSED(diag);
#endif
}
#else
static bool ss_twr_init_cir_full_should_publish_unicast(uint8_t anchor_id)
{
    ARG_UNUSED(anchor_id);
    return false;
}

static void ss_twr_init_publish_full_cir(uint8_t anchor_id,
                                         long raw_distance_mm,
                                         uint32_t resp_rx_ts,
                                         int32_t carrier_integrator,
                                         const dwt_rxdiag_t *diag)
{
    ARG_UNUSED(anchor_id);
    ARG_UNUSED(raw_distance_mm);
    ARG_UNUSED(resp_rx_ts);
    ARG_UNUSED(carrier_integrator);
    ARG_UNUSED(diag);
}
#endif

static long ss_twr_init_calc_raw_distance_mm(uint32_t poll_tx_ts,
                                             uint32_t resp_rx_ts,
                                             uint32_t poll_rx_ts,
                                             uint32_t resp_tx_ts,
                                             int32_t carrier_integrator)
{
    int32 rtd_init = (int32)(resp_rx_ts - poll_tx_ts);
    int32 rtd_resp = (int32)(resp_tx_ts - poll_rx_ts);
    double clock_offset_ratio =
        (double)carrier_integrator *
        (FREQ_OFFSET_MULTIPLIER * HERTZ_TO_PPM_MULTIPLIER_CHAN_5 / 1.0e6);
    double tof =
        ((rtd_init - rtd_resp * (1.0 - clock_offset_ratio)) / 2.0) *
        DWT_TIME_UNITS;
    long raw_mm = (long)(tof * SS_TWR_INIT_SPEED_OF_LIGHT * 1000.0);

    return raw_mm < 0L ? 0L : raw_mm;
}

#if APP_TAG_BLE_ENABLE
static void ss_twr_init_publish_cal_range(uint8_t anchor_id,
                                          enum uwb_tag_ble_cal_status status,
                                          int32_t raw_mm,
                                          uint32_t range_mm,
                                          const struct uwb_range_tracker *tracker)
{
#if APP_TAG_NORMAL_OUTPUT_ENABLE == 0U
	ARG_UNUSED(anchor_id);
	ARG_UNUSED(status);
	ARG_UNUSED(raw_mm);
	ARG_UNUSED(range_mm);
	ARG_UNUSED(tracker);
	return;
#endif
	if (!ss_twr_init_runtime_any_calibration_mode()) {
		return;
	}

	struct uwb_tag_ble_cal_range sample = {
	    .sweep = (uint32_t)ss_twr_init_sweep_count,
	    .raw_mm = raw_mm,
        .range_mm = range_mm,
        .ok_count = (tracker != NULL) ? tracker->success_count : 0U,
        .fail_count = (tracker != NULL) ? tracker->failure_count : 0U,
        .anchor_id = anchor_id,
        .status = (uint8_t)status,
	    .quality_percent =
	        (tracker != NULL) ?
	            uwb_range_tracker_quality_percent((struct uwb_range_tracker *)tracker) :
	            0U,
	};
	(void)uwb_tag_ble_publish_calibration_range(&sample);
}
#endif

static void ss_twr_init_reset_sweep_anchor_state(void)
{
    ss_twr_init_current_anchor_retry_count = 0U;
    for (size_t i = 0U; i < UWB_MAX_ANCHORS; ++i) {
        ss_twr_init_sweep_anchor_status[i] = SS_TWR_INIT_SWEEP_ANCHOR_PENDING;
        ss_twr_init_sweep_anchor_quality[i] = 0U;
        ss_twr_init_sweep_anchor_reason[i] = SS_TWR_INIT_CAL_REASON_NOT_MEASURED;
        ss_twr_init_sweep_anchor_raw_mm[i] = 0;
        ss_twr_init_sweep_anchor_range_mm[i] = 0U;
        ss_twr_init_sweep_anchor_pred_mm[i] = 0U;
        ss_twr_init_sweep_anchor_resid_mm[i] = 0U;
        ss_twr_init_sweep_anchor_solve_quality[i] = 0U;
        ss_twr_init_sweep_anchor_diag_published[i] = false;
    }
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U
    memset(ss_twr_init_sweep_anchor_poll_diag, 0,
           sizeof(ss_twr_init_sweep_anchor_poll_diag));
    memset(ss_twr_init_sweep_tag_resp_diag, 0,
           sizeof(ss_twr_init_sweep_tag_resp_diag));
    ss_twr_init_sweep_rf_diag_mask = 0U;
#endif
}

static void ss_twr_init_record_sweep_anchor_state(
    uint8_t anchor_id, enum uwb_tag_ble_cal_status status,
    const struct uwb_range_tracker *tracker)
{
    if (anchor_id >= UWB_MAX_ANCHORS) {
        return;
    }

    ss_twr_init_sweep_anchor_status[anchor_id] = (uint8_t)status;
    ss_twr_init_sweep_anchor_quality[anchor_id] =
        (tracker != NULL) ? uwb_range_tracker_quality_percent(
                                (struct uwb_range_tracker *)tracker)
                          : 0U;
    ss_twr_init_sweep_anchor_solve_quality[anchor_id] =
        ss_twr_init_sweep_anchor_quality[anchor_id];
}

static const char *ss_twr_init_cal_status_label(uint8_t status)
{
    switch (status) {
    case UWB_TAG_BLE_CAL_STATUS_OK:
        return "ok";
    case UWB_TAG_BLE_CAL_STATUS_REJECT:
        return "reject";
    case UWB_TAG_BLE_CAL_STATUS_TIMEOUT:
        return "timeout";
    case UWB_TAG_BLE_CAL_STATUS_ERROR:
        return "error";
    case SS_TWR_INIT_SWEEP_ANCHOR_PENDING:
    default:
        return "pending";
    }
}

static char ss_twr_init_range_status_code(uint8_t status)
{
    switch (status) {
    case UWB_TAG_BLE_CAL_STATUS_OK:
        return 'O';
    case UWB_TAG_BLE_CAL_STATUS_REJECT:
        return 'R';
    case UWB_TAG_BLE_CAL_STATUS_TIMEOUT:
        return 'T';
    case UWB_TAG_BLE_CAL_STATUS_ERROR:
        return 'E';
    case SS_TWR_INIT_SWEEP_ANCHOR_PENDING:
    default:
        return 'P';
    }
}

#if APP_TAG_BLE_ENABLE && APP_ALT_SS_TWR_ENABLE && \
    APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
static size_t ss_twr_init_append_csv_i32(char *buf, size_t len, size_t pos,
                                         int32_t value, bool first)
{
    if (pos >= len) {
        return pos;
    }

    pos += snprintk(&buf[pos], len - pos, first ? "%ld" : ",%ld",
                    (long)value);
    return pos;
}

static size_t ss_twr_init_append_csv_u32(char *buf, size_t len, size_t pos,
                                         uint32_t value, bool first)
{
    if (pos >= len) {
        return pos;
    }

    pos += snprintk(&buf[pos], len - pos, first ? "%lu" : ",%lu",
                    (unsigned long)value);
    return pos;
}

#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U && \
    APP_TAG_TR_RF_DIAG_COMPACT_ENABLE != 0U
static uint16_t ss_twr_init_rf_diag_fp_sum16(
    const struct ss_twr_init_rf_diag_sample *sample)
{
    uint32_t sum;

    if (sample == NULL) {
        return 0U;
    }

    sum = (uint32_t)sample->fp_ampl1 + (uint32_t)sample->fp_ampl2 +
          (uint32_t)sample->fp_ampl3;
    return (sum > UINT16_MAX) ? UINT16_MAX : (uint16_t)sum;
}

static uint8_t ss_twr_init_rf_diag_q8(uint16_t value)
{
    uint16_t q = (uint16_t)((value + 128U) >> 8);

    return (q > UINT8_MAX) ? UINT8_MAX : (uint8_t)q;
}

static bool ss_twr_init_append_compact_rf_diag(char *line, size_t line_len,
                                               uint32_t active_mask)
{
    uint8_t raw[SS_TWR_INIT_RF_DIAG_COMPACT_MAX_RAW];
    char b64[SS_TWR_INIT_RF_DIAG_COMPACT_MAX_B64];
    size_t raw_len = 0U;
    size_t b64_len = 0U;
    size_t used;
    int rc;

    if (line == NULL || line_len == 0U || active_mask == 0U ||
        !ss_twr_init_rf_diag_output_due()) {
        return false;
    }

    for (uint8_t anchor_id = 0U; anchor_id < UWB_MAX_ANCHORS; ++anchor_id) {
        const struct ss_twr_init_rf_diag_sample *anchor_diag;
        const struct ss_twr_init_rf_diag_sample *tag_diag;
        uint8_t *record;
        uint8_t valid_mask = BIT(anchor_id);

        if ((active_mask & valid_mask) == 0U) {
            continue;
        }
        if (raw_len + SS_TWR_INIT_RF_DIAG_COMPACT_RECORD_LEN > sizeof(raw)) {
            return false;
        }

        anchor_diag = &ss_twr_init_sweep_anchor_poll_diag[anchor_id];
        tag_diag = &ss_twr_init_sweep_tag_resp_diag[anchor_id];
        record = &raw[raw_len];

        record[0] = ((ss_twr_init_sweep_rf_diag_mask & valid_mask) != 0U) ?
                    anchor_diag->flags : 0U;
        record[1] = ((ss_twr_init_sweep_rf_diag_mask & valid_mask) != 0U) ?
                    tag_diag->flags : 0U;
        record[2] = ss_twr_init_rf_diag_q8(
            ss_twr_init_rf_diag_fp_sum16(anchor_diag));
        record[3] = ss_twr_init_rf_diag_q8(anchor_diag->cir_pwr);
        record[4] = (anchor_diag->rxpacc > UINT8_MAX) ?
                    UINT8_MAX : (uint8_t)anchor_diag->rxpacc;
        record[5] = ss_twr_init_rf_diag_q8(
            ss_twr_init_rf_diag_fp_sum16(tag_diag));
        record[6] = ss_twr_init_rf_diag_q8(tag_diag->cir_pwr);
        record[7] = (tag_diag->rxpacc > UINT8_MAX) ?
                    UINT8_MAX : (uint8_t)tag_diag->rxpacc;
        raw_len += SS_TWR_INIT_RF_DIAG_COMPACT_RECORD_LEN;
    }

    if (raw_len == 0U) {
        return false;
    }

    rc = base64_encode((uint8_t *)b64, sizeof(b64), &b64_len, raw, raw_len);
    if (rc != 0 || b64_len >= sizeof(b64)) {
        return false;
    }
    b64[b64_len] = '\0';

    used = strlen(line);
    if (used >= line_len || used >= SS_TWR_INIT_TR_STATUS_MAX_LEN) {
        return false;
    }

    /*
     * BLE status packets are capped at 256 bytes. RF diagnostics are optional:
     * keep the range summary intact and drop this trailer if it would not fit.
     */
    if (used + 4U + b64_len >= SS_TWR_INIT_TR_STATUS_MAX_LEN ||
        used + 4U + b64_len >= line_len) {
        return false;
    }

    (void)snprintk(&line[used], line_len - used, ";D%u,%s",
                   (unsigned int)SS_TWR_INIT_RF_DIAG_COMPACT_VERSION, b64);
    return true;
}
#endif

static void ss_twr_init_publish_tag_range_summary(
    const struct ss_twr_init_range_measurement *measurements, size_t measurement_count,
    uint8_t qf_percent)
{
    char line[384];
    char raw_csv[64];
    char range_csv[64];
    char quality_csv[40];
    char status_codes[UWB_MAX_ANCHORS + 1U];
    size_t raw_pos = 0U;
    size_t range_pos = 0U;
    size_t quality_pos = 0U;
    size_t status_pos = 0U;
    uint32_t active_mask = 0U;
    uint32_t valid_mask = 0U;
    uint32_t rx_mask = 0U;
    uint32_t first_to_last_us = 0U;
    uint32_t frame_us = 0U;
    uint32_t cycle_us = 0U;
    bool first = true;
    int line_len;
    static uint32_t tag_temp_last_ms;
    static bool tag_temp_valid;
    static uint8_t tag_temp_raw;
    static uint8_t tag_vbat_raw;

    if (!uwb_tag_ble_tr_enabled()) {
        return;
    }

    if (ss_twr_init_runtime_any_calibration_mode()) {
        return;
    }

    /* Periodic (~30s) tag DW1000 chip temperature + Vbat sample. Taken at
     * sweep completion (radio idle between sweeps); worst case it nudges one
     * poll by ~1ms once per 30s. Emitted as a raw-code ;T trailer on the TR
     * line so it rides the existing tag->master-tag->host text passthrough.
     */
    {
        uint32_t temp_now_ms = k_uptime_get_32();

        if (!tag_temp_valid ||
            (uint32_t)(temp_now_ms - tag_temp_last_ms) >= 30000U) {
            uint16_t tv_raw = dwt_readtempvbat(1);

            tag_temp_raw = (uint8_t)(tv_raw >> 8);
            tag_vbat_raw = (uint8_t)(tv_raw & 0xffU);
            tag_temp_last_ms = temp_now_ms;
            tag_temp_valid = true;
        }
    }

    for (size_t i = 0U; i < measurement_count; ++i) {
        uint8_t anchor_id = measurements[i].anchor_id;

        if (anchor_id >= UWB_MAX_ANCHORS ||
            !ss_twr_init_anchor_id_in_list(ss_twr_init_active_anchor_ids,
                                           ss_twr_init_active_anchor_count,
                                           anchor_id)) {
            continue;
        }

        active_mask |= BIT(anchor_id);
        if (measurements[i].valid) {
            valid_mask |= BIT(anchor_id);
        }
        if (ss_twr_init_sweep_anchor_status[anchor_id] !=
            SS_TWR_INIT_SWEEP_ANCHOR_PENDING) {
            rx_mask |= BIT(anchor_id);
        }

        raw_pos = ss_twr_init_append_csv_i32(
            raw_csv, sizeof(raw_csv), raw_pos,
            ss_twr_init_sweep_anchor_raw_mm[anchor_id], first);
        range_pos = ss_twr_init_append_csv_u32(
            range_csv, sizeof(range_csv), range_pos,
            measurements[i].range_mm, first);
        quality_pos = ss_twr_init_append_csv_u32(
            quality_csv, sizeof(quality_csv), quality_pos,
            measurements[i].quality_percent, first);

        if (status_pos + 1U < sizeof(status_codes)) {
            status_codes[status_pos++] = ss_twr_init_range_status_code(
                ss_twr_init_sweep_anchor_status[anchor_id]);
        }
        first = false;
    }

    if (active_mask == 0U) {
        return;
    }

    raw_csv[MIN(raw_pos, sizeof(raw_csv) - 1U)] = '\0';
    range_csv[MIN(range_pos, sizeof(range_csv) - 1U)] = '\0';
    quality_csv[MIN(quality_pos, sizeof(quality_csv) - 1U)] = '\0';
    status_codes[MIN(status_pos, sizeof(status_codes) - 1U)] = '\0';

    if (ss_twr_init_sweep_timing_valid && ss_twr_init_sweep_poll_count != 0U) {
        first_to_last_us = k_cyc_to_us_floor32(
            ss_twr_init_sweep_last_poll_cycle -
            ss_twr_init_sweep_first_poll_cycle);
        frame_us = k_cyc_to_us_floor32(
            ss_twr_init_sweep_done_cycle -
            ss_twr_init_sweep_first_poll_cycle);
        cycle_us = frame_us;
    }

#if APP_TAG_TR_BCAST_V2_ENABLE
    line_len = snprintk(
        line, sizeof(line),
        "TR;%u;%lu;%c;%u;%02lx;%02lx;%s;%s;%s;%s",
        (unsigned int)(
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U && \
    APP_TAG_TR_RF_DIAG_COMPACT_ENABLE != 0U
            /* freeze-clean batch4d: runtime-gate the TR range-format version on
             * the DIAG toggle. DIAG off (production) -> "TR;2" with no ;D1
             * trailer; DIAG on -> "TR;3" + ;D1 compact-diag trailer below. */
            (ss_twr_init_rf_diag_runtime_enabled() ? 3U : 2U)
#else
            2U
#endif
        ),
        (unsigned long)ss_twr_init_sweep_count,
        ss_twr_init_plan_code(ss_twr_init_plan_label()),
        (unsigned int)ss_twr_init_runtime_params.positioning_mode,
        (unsigned long)active_mask, (unsigned long)valid_mask,
        raw_csv, range_csv, quality_csv, status_codes);
    ARG_UNUSED(rx_mask);
    ARG_UNUSED(qf_percent);
    ARG_UNUSED(first_to_last_us);
    ARG_UNUSED(frame_us);
    ARG_UNUSED(cycle_us);
#else
    line_len = snprintk(
        line, sizeof(line),
        "TR;%u;%lu;%c;%u;%02lx;%02lx;%s;%s;%s;%s;%u;%lu;%lu;%u",
        (unsigned int)SS_TWR_INIT_TR_RANGE_VERSION,
        (unsigned long)ss_twr_init_sweep_count,
        ss_twr_init_plan_code(ss_twr_init_plan_label()),
        (unsigned int)ss_twr_init_runtime_params.positioning_mode,
        (unsigned long)active_mask, (unsigned long)valid_mask,
        raw_csv, range_csv, quality_csv, status_codes,
        (unsigned int)qf_percent,
        (unsigned long)first_to_last_us,
        (unsigned long)frame_us,
        (unsigned int)ss_twr_init_sweep_poll_count);
#endif
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U && \
    APP_TAG_TR_RF_DIAG_COMPACT_ENABLE != 0U
    /* freeze-clean batch4d: only append the ;D1 compact-diag trailer when the
     * DIAG toggle is on, keeping production output at literal "TR;2". */
    if (line_len > 0 && ss_twr_init_rf_diag_runtime_enabled()) {
        (void)ss_twr_init_append_compact_rf_diag(line, sizeof(line),
                                                active_mask);
        line_len = (int)strlen(line);
    }
#endif
#if APP_TAG_TR_IMU_SUMMARY_ENABLE != 0U
    if (line_len > 0 && ss_twr_init_imu_summary.valid) {
        size_t used = (size_t)line_len;

        if (used >= sizeof(line)) {
            used = sizeof(line) - 1U;
        }
        (void)snprintk(
            &line[used], sizeof(line) - used,
            ";I,%u,%ld,%ld,%ld,%ld,%lu",
            (unsigned int)ss_twr_init_imu_summary.sample_count,
            (long)ss_twr_init_imu_summary.mean_mg,
            (long)ss_twr_init_imu_summary.std_mg,
            (long)ss_twr_init_imu_summary.min_mg,
            (long)ss_twr_init_imu_summary.max_mg,
            (unsigned long)ss_twr_init_imu_summary.skip_count);
    }
#endif
#if APP_TAG_TR_IMU_RAW_ENABLE != 0U
    if (line_len > 0 && ss_twr_init_have_last_imu_sample) {
        size_t used = strlen(line);
        uint32_t read_start_us = 0U;
        uint32_t read_mid_us = 0U;
        uint32_t read_end_us = 0U;
        uint32_t read_duration_us = k_cyc_to_us_floor32(
            ss_twr_init_last_imu_sample.read_end_cycle -
            ss_twr_init_last_imu_sample.read_start_cycle);

        if (used >= sizeof(line)) {
            used = sizeof(line) - 1U;
        }
        if (ss_twr_init_sweep_timing_valid) {
            read_start_us = k_cyc_to_us_floor32(
                ss_twr_init_last_imu_sample.read_start_cycle -
                ss_twr_init_sweep_first_poll_cycle);
            read_mid_us = k_cyc_to_us_floor32(
                ss_twr_init_last_imu_sample.timestamp_cycle -
                ss_twr_init_sweep_first_poll_cycle);
            read_end_us = k_cyc_to_us_floor32(
                ss_twr_init_last_imu_sample.read_end_cycle -
                ss_twr_init_sweep_first_poll_cycle);
        }
        (void)snprintk(
            &line[used], sizeof(line) - used,
            ";R,%ld,%ld,%ld,%ld,%lu,%lu,%lu,%lu,%lu",
            (long)ss_twr_init_last_imu_sample.ax_mg,
            (long)ss_twr_init_last_imu_sample.ay_mg,
            (long)ss_twr_init_last_imu_sample.az_mg,
            (long)ss_twr_init_last_imu_sample.norm_mg,
            (unsigned long)ss_twr_init_last_imu_sample.timestamp_ms,
            (unsigned long)read_start_us,
            (unsigned long)read_mid_us,
            (unsigned long)read_end_us,
            (unsigned long)read_duration_us);
    }
#endif
    if (line_len > 0 && tag_temp_valid) {
        size_t used = strlen(line);

        if (used < sizeof(line)) {
            (void)snprintk(&line[used], sizeof(line) - used, ";T,%u,%u",
                           (unsigned int)tag_temp_raw,
                           (unsigned int)tag_vbat_raw);
        }
    }
    (void)uwb_tag_ble_publish_status(line);
}
#else
static void ss_twr_init_publish_tag_range_summary(
    const struct ss_twr_init_range_measurement *measurements, size_t measurement_count,
    uint8_t qf_percent)
{
    ARG_UNUSED(measurements);
    ARG_UNUSED(measurement_count);
    ARG_UNUSED(qf_percent);
}
#endif

static const char *ss_twr_init_cal_reason_label(uint8_t reason)
{
    switch (reason) {
    case SS_TWR_INIT_CAL_REASON_OK:
        return "ok";
    case SS_TWR_INIT_CAL_REASON_RANGE_INVALID:
        return "range_invalid";
    case SS_TWR_INIT_CAL_REASON_RX_TIMEOUT:
        return "rx_timeout";
    case SS_TWR_INIT_CAL_REASON_RX_ERROR:
        return "rx_error";
    case SS_TWR_INIT_CAL_REASON_NOT_MEASURED:
        return "not_measured";
    case SS_TWR_INIT_CAL_REASON_NONE:
    default:
        return "none";
    }
}

static void ss_twr_init_record_sweep_anchor_diag(
    uint8_t anchor_id, uint8_t reason, int32_t raw_mm, uint32_t range_mm,
    uint32_t pred_mm, uint32_t resid_mm, uint8_t solve_quality_percent)
{
    if (anchor_id >= UWB_MAX_ANCHORS) {
        return;
    }

    ss_twr_init_sweep_anchor_reason[anchor_id] = reason;
    ss_twr_init_sweep_anchor_raw_mm[anchor_id] = raw_mm;
    ss_twr_init_sweep_anchor_range_mm[anchor_id] = range_mm;
    ss_twr_init_sweep_anchor_pred_mm[anchor_id] = pred_mm;
    ss_twr_init_sweep_anchor_resid_mm[anchor_id] = resid_mm;
    ss_twr_init_sweep_anchor_solve_quality[anchor_id] = solve_quality_percent;
}

static void ss_twr_init_publish_cal_reason_line(uint8_t anchor_id)
{
    char line[192];

    if (!ss_twr_init_runtime_any_calibration_mode() ||
        anchor_id >= UWB_MAX_ANCHORS ||
        ss_twr_init_sweep_anchor_diag_published[anchor_id]) {
        return;
    }

    snprintk(line, sizeof(line),
             "CR;1;%lu;%s;%u;%u;%s;%s;%ld;%lu;%lu;%lu;%u;%u",
             (unsigned long)ss_twr_init_sweep_count, ss_twr_init_plan_label(),
             (unsigned int)ss_twr_init_runtime_params.positioning_mode,
             (unsigned int)anchor_id,
             ss_twr_init_cal_status_label(ss_twr_init_sweep_anchor_status[anchor_id]),
             ss_twr_init_cal_reason_label(ss_twr_init_sweep_anchor_reason[anchor_id]),
             (long)ss_twr_init_sweep_anchor_raw_mm[anchor_id],
             (unsigned long)ss_twr_init_sweep_anchor_range_mm[anchor_id],
             (unsigned long)ss_twr_init_sweep_anchor_pred_mm[anchor_id],
             (unsigned long)ss_twr_init_sweep_anchor_resid_mm[anchor_id],
             (unsigned int)ss_twr_init_sweep_anchor_quality[anchor_id],
             (unsigned int)ss_twr_init_sweep_anchor_solve_quality[anchor_id]);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE
    (void)uwb_tag_ble_publish_status(line);
#endif
    ss_twr_init_sweep_anchor_diag_published[anchor_id] = true;
}

static void ss_twr_init_publish_cal_frame_summary(const char *plan_label,
                                                  uint8_t positioning_mode,
                                                  uint8_t qf_percent,
                                                  uint32_t rms_mm,
                                                  uint32_t max_mm,
                                                  uint32_t step_mm,
                                                  size_t valid_anchor_count)
{
    char line[192];
    size_t reported_valid_count = valid_anchor_count;
    uint32_t first_to_last_us = 0U;
    uint32_t frame_us = 0U;

    if (!ss_twr_init_runtime_any_calibration_mode()) {
        return;
    }

    /*
     * In the legacy grouped capture path the active target set is the contract. A failed
     * leg must remain visible as timeout/reject in CS/CR/qf, not silently turn
     * the frame into a "3-anchor" record in CF.
     */
    if (ss_twr_init_active_anchor_count == 4U) {
        reported_valid_count = ss_twr_init_active_anchor_count;
    }

    if (ss_twr_init_sweep_timing_valid && ss_twr_init_sweep_poll_count != 0U) {
        first_to_last_us = k_cyc_to_us_floor32(
            ss_twr_init_sweep_last_poll_cycle -
            ss_twr_init_sweep_first_poll_cycle);
        frame_us = k_cyc_to_us_floor32(
            ss_twr_init_sweep_done_cycle -
            ss_twr_init_sweep_first_poll_cycle);
    }

    snprintk(line, sizeof(line), "CF;1;%lu;%s;%u;%s;%u;%u;%u;%lu;%lu;%lu;%lu;%lu;%u",
             (unsigned long)ss_twr_init_sweep_count, plan_label,
             (unsigned int)positioning_mode, ss_twr_init_solve_reason_label(),
             (unsigned int)qf_percent,
             (unsigned int)ss_twr_init_active_anchor_count,
             (unsigned int)reported_valid_count,
             (unsigned long)rms_mm, (unsigned long)max_mm,
             (unsigned long)step_mm, (unsigned long)first_to_last_us,
             (unsigned long)frame_us,
             (unsigned int)ss_twr_init_sweep_poll_count);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE && APP_TAG_ALT_RXG_BLE_DIAG_ENABLE != 0U
    (void)uwb_tag_ble_publish_status(line);
#endif
}

static void ss_twr_init_publish_solve_diag(char stage,
                                           const char *plan_label,
                                           uint8_t qf_percent,
                                           size_t valid_anchor_count,
                                           size_t used_anchor_count,
                                           uint32_t rms_mm,
                                           uint32_t max_mm,
                                           uint32_t step_mm,
                                           const char *anchors)
{
    uint32_t now_ms = (uint32_t)k_uptime_get();
    char line[192];

    if (ss_twr_init_last_solve_diag_ms != 0U &&
        (uint32_t)(now_ms - ss_twr_init_last_solve_diag_ms) < 1000U) {
        return;
    }
    ss_twr_init_last_solve_diag_ms = now_ms;

    snprintk(line, sizeof(line),
             "SD;1;%lu;%c;%s;%u;%u;%u;%u;%lu;%lu;%lu;%s;%u;%u",
             (unsigned long)ss_twr_init_sweep_count,
             stage,
             plan_label,
             (unsigned int)ss_twr_init_active_anchor_count,
             (unsigned int)valid_anchor_count,
             (unsigned int)used_anchor_count,
             (unsigned int)qf_percent,
             (unsigned long)rms_mm,
             (unsigned long)max_mm,
             (unsigned long)step_mm,
             (anchors != NULL) ? anchors : "",
             (unsigned int)ss_twr_init_tdma_schedule.slot_index,
             (unsigned int)ss_twr_init_tdma_schedule.slot_count);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE && APP_TAG_ALT_RXG_BLE_DIAG_ENABLE != 0U
    (void)uwb_tag_ble_publish_status(line);
#endif
}

#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
static uint32_t ss_twr_init_diag_delta_us(uint32_t end, uint32_t start)
{
    return k_cyc_to_us_floor32((uint32_t)(end - start));
}

static void ss_twr_init_sweep_diag_maybe_print(void)
{
    char line[192];
    uint32_t wait_ms;
    uint32_t tx_us;
    uint32_t rx_us;
    uint32_t coll_us;
    uint32_t range_us;
    uint32_t solve_us;
    uint32_t out_us;
    uint32_t clean_us;
    uint32_t total_ms;

    ss_twr_init_diag_sweep_count++;
    if (APP_TAG_SWEEP_DIAG_PERIOD != 0U &&
        (ss_twr_init_diag_sweep_count % APP_TAG_SWEEP_DIAG_PERIOD) != 0U) {
        return;
    }

    wait_ms = k_cyc_to_ms_floor32(
        (uint32_t)(ss_twr_init_diag_wait_done_cycles -
                   ss_twr_init_diag_t0_cycles));
    tx_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_tx_done_cycles,
                                      ss_twr_init_diag_wait_done_cycles);
    rx_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_rx_start_cycles,
                                      ss_twr_init_diag_tx_done_cycles);
    coll_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_rx_done_cycles,
                                        ss_twr_init_diag_rx_start_cycles);
    range_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_range_done_cycles,
                                         ss_twr_init_diag_rx_done_cycles);
    solve_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_solve_done_cycles,
                                         ss_twr_init_diag_solve_start_cycles);
    out_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_out_done_cycles,
                                       ss_twr_init_diag_out_start_cycles);
    clean_us = ss_twr_init_diag_delta_us(ss_twr_init_diag_clean_done_cycles,
                                         ss_twr_init_diag_out_done_cycles);
    total_ms = k_cyc_to_ms_floor32(
        (uint32_t)(ss_twr_init_diag_clean_done_cycles -
                   ss_twr_init_diag_t0_cycles));

    snprintk(line, sizeof(line),
             "TDIAG;wait_ms=%lu;tx_us=%lu;rx_us=%lu;coll_us=%lu;range_us=%lu;solve_us=%lu;out_us=%lu;clean_us=%lu;total_ms=%lu",
             (unsigned long)wait_ms,
             (unsigned long)tx_us,
             (unsigned long)rx_us,
             (unsigned long)coll_us,
             (unsigned long)range_us,
             (unsigned long)solve_us,
             (unsigned long)out_us,
             (unsigned long)clean_us,
             (unsigned long)total_ms);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE && APP_TAG_ALT_RXG_BLE_DIAG_ENABLE != 0U
    (void)uwb_tag_ble_publish_status(line);
#endif
}
#endif

static void ss_twr_init_note_poll_started(void)
{
    uint32_t cycle = k_cycle_get_32();

    if (ss_twr_init_active_anchor_index == 0U ||
        ss_twr_init_sweep_poll_count == 0U) {
        ss_twr_init_sweep_first_poll_cycle = cycle;
        ss_twr_init_sweep_poll_count = 0U;
        ss_twr_init_sweep_timing_valid = true;
    }

    ss_twr_init_sweep_last_poll_cycle = cycle;
    if (ss_twr_init_sweep_poll_count < UINT8_MAX) {
        ss_twr_init_sweep_poll_count++;
    }
}

static void ss_twr_init_note_sweep_done(void)
{
    if (ss_twr_init_sweep_timing_valid) {
        ss_twr_init_sweep_done_cycle = k_cycle_get_32();
    }
}

static const char *ss_twr_init_slot_source_label(uint8_t slot_source)
{
	switch (slot_source) {
	case UWB_TAG_SLOT_SOURCE_MASTER:
		return "MASTER";
	case UWB_TAG_SLOT_SOURCE_SETTINGS:
		return "SETTINGS";
	default:
		return "BUILD";
	}
}

static char ss_twr_init_slot_source_code(uint8_t slot_source)
{
	switch (slot_source) {
	case UWB_TAG_SLOT_SOURCE_MASTER:
		return 'M';
	case UWB_TAG_SLOT_SOURCE_SETTINGS:
		return 'S';
	default:
		return 'B';
	}
}

static const char *ss_twr_init_solve_reason_label(void)
{
	switch (ss_twr_init_last_solve_reason) {
	case SS_TWR_INIT_SOLVE_SUCCESS:
		return "success";
	case SS_TWR_INIT_SOLVE_PENDING:
		return "pending";
	case SS_TWR_INIT_SOLVE_REJECTED:
		return "rejected";
	case SS_TWR_INIT_SOLVE_SLOT_CUT_SHORT:
		return "slot_cut_short";
	default:
		return "none";
	}
}

static char ss_twr_init_solve_reason_code(void)
{
	switch (ss_twr_init_last_solve_reason) {
	case SS_TWR_INIT_SOLVE_SUCCESS:
		return 'S';
	case SS_TWR_INIT_SOLVE_PENDING:
		return 'P';
	case SS_TWR_INIT_SOLVE_REJECTED:
		return 'R';
	case SS_TWR_INIT_SOLVE_SLOT_CUT_SHORT:
		return 'C';
	default:
		return 'N';
	}
}

static char ss_twr_init_plan_code(const char *plan_label)
{
	if (plan_label == NULL) {
		return 'x';
	}

	if (strcmp(plan_label, "track") == 0) {
		return 't';
	}
	if (strcmp(plan_label, "full") == 0) {
		return 'f';
	}
	if (strcmp(plan_label, "refresh") == 0) {
		return 'r';
	}
	return 'x';
}

static bool ss_twr_init_anchor_id_in_list(const uint8_t *anchor_ids, size_t count,
                                          uint8_t anchor_id)
{
    for (size_t i = 0; i < count; ++i) {
        if (anchor_ids[i] == anchor_id) {
            return true;
        }
    }

	return false;
}

static bool ss_twr_init_range_measurement_valid(uint32_t range_mm)
{
    /* Validity marking of failed measurements -- NOT range filtering. */
    return range_mm != 0U;
}

static void ss_twr_init_sleep_between_ranges(void)
{
    if (SS_TWR_INIT_RNG_DELAY_MS > 0U) {
        k_msleep(SS_TWR_INIT_RNG_DELAY_MS);
    }

    if (ss_twr_init_runtime_any_calibration_mode() &&
        SS_TWR_INIT_CAL_RNG_SETTLE_US > 0U) {
        k_busy_wait(SS_TWR_INIT_CAL_RNG_SETTLE_US);
    }
}

static void ss_twr_init_prepare_radio_for_poll(void)
{
    /*
     * Consecutive 4-anchor CAL sweeps stress the DW1000 state machine more than
     * single-leg debug tests.  Return to idle and clear stale TX/RX state before
     * every poll so the next immediate TX cannot inherit a previous RX timeout or
     * good-frame latch.
     */
    dwt_forcetrxoff();
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_GOOD |
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
}

static bool ss_twr_init_should_retry_current_cal_anchor(void)
{
    return false;
}

static const char *ss_twr_init_plan_label(void)
{
    if (ss_twr_init_current_sweep_refresh) {
        return "refresh";
    }

    return ss_twr_init_current_sweep_full ? "full" : "track";
}

static uint32_t ss_twr_init_alt_bcast_response_window_us(size_t anchor_count)
{
    uint32_t window_us;

    if (anchor_count == 0U) {
        return 0U;
    }

    window_us = APP_ALT_SS_TWR_GUARD_US +
                (((uint32_t)anchor_count - 1U) *
                 APP_ALT_SS_TWR_RESP_SPACING_US) +
                SS_TWR_INIT_ALT_BCAST_TAIL_MARGIN_US;

#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
    /*
     * The broadcast collector starts after TXFRS, not at poll TX start.  Keep
     * it open until the last response plus tail, but do not charge the poll
     * airtime twice.
     */
    if (window_us > SS_TWR_INIT_ALT_BCAST_POLL_AIRTIME_US) {
        window_us -= SS_TWR_INIT_ALT_BCAST_POLL_AIRTIME_US;
    }
#endif

    return window_us;
}

static uint32_t ss_twr_init_alt_bcast_response_window_estimated_us(size_t anchor_count)
{
    uint32_t window_us = ss_twr_init_alt_bcast_response_window_us(anchor_count);

    /*
     * The collector window starts after TX-done/RX-enable work has already
     * consumed part of the poll-to-last-response interval.  Use this estimate
     * only for TDMA admission budgeting; the actual collector still uses the
     * full response window.
     */
    if (window_us > 800U) {
        return window_us - 800U;
    }

    return window_us;
}

static uint32_t ss_twr_init_tdma_period_remaining_ms(void)
{
    const struct uwb_tdma_schedule *schedule = &ss_twr_init_tdma_schedule;
    uint32_t cycle_ms;
    uint32_t phase_ms;
    uint32_t slot_start_ms;
    uint32_t slot_end_ms;
    uint8_t slot;
    uint16_t slot_mask;

    if (!uwb_tdma_schedule_is_valid(schedule) ||
        schedule->slot_period_ms == 0U || schedule->slot_count == 0U) {
        return UINT32_MAX;
    }
    if (schedule->epoch_valid &&
        (int32_t)(k_uptime_get_32() - schedule->sync_local_ms) < 0) {
        return 0U;
    }

    cycle_ms = (uint32_t)schedule->slot_count *
               (uint32_t)schedule->slot_period_ms;
    if (cycle_ms == 0U) {
        return 0U;
    }

    phase_ms = uwb_tdma_schedule_now_ms(schedule) % cycle_ms;
    slot = (uint8_t)(phase_ms / (uint32_t)schedule->slot_period_ms);
    if (slot >= schedule->slot_count) {
        return 0U;
    }

    slot_mask = schedule->slot_mask;
    if (slot_mask == 0U && schedule->slot_index < schedule->slot_count) {
        slot_mask = (uint16_t)(1U << schedule->slot_index);
    }
    if ((slot_mask & (uint16_t)(1U << slot)) == 0U) {
        return 0U;
    }

    slot_start_ms = (uint32_t)slot * (uint32_t)schedule->slot_period_ms;
    slot_end_ms = slot_start_ms + (uint32_t)schedule->slot_period_ms;
    if (phase_ms < slot_start_ms || phase_ms >= slot_end_ms) {
        return 0U;
    }

    return slot_end_ms - phase_ms;
}

static bool ss_twr_init_tdma_exchange_can_start(void)
{
	uint32_t required_ms = SS_TWR_INIT_SLOT_EXCHANGE_BUDGET_MS;

#if APP_ALT_SS_TWR_ENABLE && APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
	if (ss_twr_init_active_anchor_index == 0U &&
	    ss_twr_init_active_anchor_count > 1U) {
		uint32_t window_us = ss_twr_init_alt_bcast_response_window_estimated_us(
			ss_twr_init_active_anchor_count);

		required_ms = (window_us + 999U) / 1000U;
		if (ss_twr_init_tdma_schedule.enabled) {
			return ss_twr_init_tdma_period_remaining_ms() >= required_ms;
		}
	}
#endif

	return uwb_tdma_schedule_exchange_fits(&ss_twr_init_tdma_schedule,
					       required_ms,
					       SS_TWR_INIT_SLOT_GUARD_MARGIN_MS);
}

static bool ss_twr_init_tdma_active_guard_enabled(void)
{
	return true;
}

static void ss_twr_init_publish_tdma_diag(const char *reason,
					  uint32_t remain_ms,
					  uint32_t need_ms)
{
	static uint32_t last_diag_ms;
	uint32_t now_ms = (uint32_t)k_uptime_get();
	char line[192];

	if ((now_ms - last_diag_ms) < 5000U) {
		return;
	}
	last_diag_ms = now_ms;

	snprintk(line, sizeof(line),
		 "TD;1;%lu;%u;%s;%u;%u;%u;%u;%u;%lu;%lu;%u;%u;%lu",
		 (unsigned long)ss_twr_init_sweep_count,
		 (unsigned int)ss_twr_init_runtime_params.positioning_mode,
		 reason != NULL ? reason : "-",
		 (unsigned int)ss_twr_init_tdma_schedule.enabled,
		 (unsigned int)ss_twr_init_tdma_schedule.slot_index,
		 (unsigned int)ss_twr_init_tdma_schedule.slot_count,
		 (unsigned int)ss_twr_init_tdma_schedule.slot_mask,
		 (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms,
		 (unsigned long)remain_ms,
		 (unsigned long)need_ms,
		 (unsigned int)ss_twr_init_active_anchor_index,
		 (unsigned int)ss_twr_init_active_anchor_count,
		 (unsigned long)ss_twr_init_last_tdma_wait_ms);
#if APP_TAG_BLE_ENABLE
	(void)uwb_tag_ble_publish_status(line);
#endif
	printk("%s\n", line);
}

static bool ss_twr_init_runtime_any_calibration_mode(void)
{
	return false;
}

static bool ss_twr_init_runtime_idle_mode(void)
{
	return ss_twr_init_runtime_params.positioning_mode ==
	       UWB_TAG_POSITIONING_MODE_IDLE;
}

static bool ss_twr_init_tdma_exchange_can_start_if_needed(void)
{
	if (!ss_twr_init_tdma_active_guard_enabled()) {
		return true;
	}

	return ss_twr_init_tdma_exchange_can_start();
}

static void ss_twr_init_set_ble_tx_paused(bool paused)
{
#if APP_TAG_BLE_ENABLE
	uwb_tag_ble_set_tx_paused(paused);
#else
	ARG_UNUSED(paused);
#endif
}

static void ss_twr_init_release_ble_tx_after_active_slot(void)
{
#if APP_TAG_BLE_ENABLE
	uwb_tag_ble_set_tx_paused(false);

	if (ss_twr_init_tdma_schedule.enabled) {
		uint32_t remain_ms =
			uwb_tdma_schedule_time_remaining_ms(&ss_twr_init_tdma_schedule);

		if (remain_ms > 0U) {
			k_msleep(remain_ms + 1U);
		}
	}
#endif
}

static uint32_t ss_twr_init_wait_until_slot_if_needed(void)
{
	return uwb_tdma_wait_until_slot(&ss_twr_init_tdma_schedule);
}

static uint32_t ss_twr_init_wait_until_next_slot_if_needed(void)
{
	return uwb_tdma_wait_until_next_slot(&ss_twr_init_tdma_schedule);
}

static void ss_twr_init_reset_tracking_history(void)
{
	ss_twr_init_last_solve_pending_log_ms = 0U;
	ss_twr_init_last_solve_diag_ms = 0U;
	ss_twr_init_last_solve_reason = SS_TWR_INIT_SOLVE_NONE;

	for (size_t i = 0; i < ss_twr_init_anchor_count; ++i) {
		uint8_t anchor_id = ss_twr_init_anchor_ids[i];

		if (anchor_id < UWB_MAX_ANCHORS) {
			uwb_range_tracker_init(&ss_twr_init_trackers[anchor_id],
					       uwb_anchor_short_addr(anchor_id));
		}
	}
}

static void ss_twr_init_apply_runtime_params(
	const struct uwb_tag_runtime_params *params)
{
	bool reset_history;
	bool beacon_mode_changed;
	uint16_t previous_local_addr;

	if (params == NULL) {
		return;
	}

	reset_history =
		params->positioning_mode != ss_twr_init_runtime_params.positioning_mode ||
		params->anchor_selection_mode != ss_twr_init_runtime_params.anchor_selection_mode ||
		params->beacon_sync != ss_twr_init_runtime_params.beacon_sync ||
		params->beacon_win_n != ss_twr_init_runtime_params.beacon_win_n ||
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
		params->dw_anchor != ss_twr_init_runtime_params.dw_anchor ||
#endif
		params->tdma.generation != ss_twr_init_tdma_schedule.generation ||
		params->tdma.slot_index != ss_twr_init_tdma_schedule.slot_index ||
		params->tdma.slot_count != ss_twr_init_tdma_schedule.slot_count ||
		params->tdma.slot_period_ms != ss_twr_init_tdma_schedule.slot_period_ms ||
		params->tdma.slot_active_ms != ss_twr_init_tdma_schedule.slot_active_ms;
	beacon_mode_changed =
		params->beacon_sync != ss_twr_init_runtime_params.beacon_sync ||
		params->beacon_win_n != ss_twr_init_runtime_params.beacon_win_n ||
		params->tdma.enabled != ss_twr_init_tdma_schedule.enabled ||
		params->tdma.slot_index != ss_twr_init_tdma_schedule.slot_index ||
		params->tdma.slot_count != ss_twr_init_tdma_schedule.slot_count ||
		params->tdma.slot_period_ms != ss_twr_init_tdma_schedule.slot_period_ms ||
		params->tdma.epoch_valid != ss_twr_init_tdma_schedule.epoch_valid ||
		params->tdma.epoch_ms != ss_twr_init_tdma_schedule.epoch_ms ||
		params->tdma.generation != ss_twr_init_tdma_schedule.generation;

	ss_twr_init_runtime_params = *params;
	previous_local_addr = ss_twr_init_local_addr;
	ss_twr_init_local_tag_id = params->logical_tag_id;
	ss_twr_init_local_addr = uwb_tag_short_addr(ss_twr_init_local_tag_id);
	if (ss_twr_init_radio_configured &&
	    ss_twr_init_local_addr != previous_local_addr) {
		dwt_setaddress16(ss_twr_init_local_addr);
		printk("Tag UWB short addr updated 0x%04x -> 0x%04x\n",
		       (unsigned int)previous_local_addr,
		       (unsigned int)ss_twr_init_local_addr);
	}
	ss_twr_init_tdma_schedule = params->tdma;
	if (beacon_mode_changed) {
		ss_twr_init_beacon_reset();
	}
	ss_twr_init_runtime_params.anchor_selection_mode =
		UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
	ss_twr_init_runtime_params.fixed_anchor_count = 0U;
	memset(ss_twr_init_runtime_params.fixed_anchor_ids, 0,
	       sizeof(ss_twr_init_runtime_params.fixed_anchor_ids));

	if (reset_history) {
		ss_twr_init_reset_tracking_history();
		printk("Tag runtime tracking reset pmode=%u slot=%u/%u gen=%u\n",
		       (unsigned int)ss_twr_init_runtime_params.positioning_mode,
		       (unsigned int)ss_twr_init_tdma_schedule.slot_index,
		       (unsigned int)ss_twr_init_tdma_schedule.slot_count,
		       (unsigned int)ss_twr_init_tdma_schedule.generation);
	}
}

static void ss_twr_init_apply_pending_runtime_config_if_any(void)
{
	if (!ss_twr_init_runtime_update_pending) {
		return;
	}

	ss_twr_init_apply_runtime_params(&ss_twr_init_pending_runtime_params);
	ss_twr_init_runtime_update_pending = false;
	ss_twr_init_active_anchor_index = 0U;
	ss_twr_init_prepare_sweep_plan();
	printk("Tag runtime config applied tag=%u slot=%u/%u period=%u active=%u active_us=%u source=%s gen=%u anchor_plan=dynamic\n",
	       (unsigned int)ss_twr_init_runtime_params.logical_tag_id,
	       (unsigned int)ss_twr_init_tdma_schedule.slot_index,
	       (unsigned int)ss_twr_init_tdma_schedule.slot_count,
	       (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
	       (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms,
	       (unsigned int)ss_twr_init_tdma_schedule.slot_active_us,
	       ss_twr_init_slot_source_label(ss_twr_init_runtime_params.slot_source),
	       (unsigned int)ss_twr_init_tdma_schedule.generation);
}

static uint8_t ss_twr_init_compute_target_quality_percent(
    const struct ss_twr_init_range_measurement *measurements, size_t measurement_count)
{
    uint32_t quality_sum = 0U;
    uint8_t quality_count = 0U;

    for (size_t i = 0U; i < ss_twr_init_active_anchor_count; ++i) {
        uint8_t target_anchor_id = ss_twr_init_active_anchor_ids[i];

        for (size_t j = 0U; j < measurement_count; ++j) {
            if (measurements[j].anchor_id == target_anchor_id) {
                quality_sum += measurements[j].quality_percent;
                quality_count++;
                break;
            }
        }
    }

    return (quality_count != 0U) ? (uint8_t)(quality_sum / quality_count) : 0U;
}

static void ss_twr_init_publish_calibration_summary(
    const char *plan_label, uint8_t positioning_mode, uint8_t qf_percent)
{
    char targets[32];
    char statuses[64];
    char qualities[32];
    char line[256];
    size_t targets_pos = 0U;
    size_t statuses_pos = 0U;
    size_t qualities_pos = 0U;

    if (!ss_twr_init_runtime_any_calibration_mode() ||
        ss_twr_init_active_anchor_count == 0U) {
        return;
    }

    targets[0] = '\0';
    statuses[0] = '\0';
    qualities[0] = '\0';

    for (size_t i = 0U; i < ss_twr_init_active_anchor_count; ++i) {
        uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];

        if (i != 0U) {
            if (targets_pos + 1U < sizeof(targets)) {
                targets[targets_pos++] = ',';
                targets[targets_pos] = '\0';
            }
            statuses_pos += (size_t)snprintk(
                statuses + statuses_pos, sizeof(statuses) - statuses_pos, ",");
            qualities_pos += (size_t)snprintk(
                qualities + qualities_pos, sizeof(qualities) - qualities_pos, ",");
        }

        targets_pos += (size_t)snprintk(
            targets + targets_pos, sizeof(targets) - targets_pos, "%u",
            (unsigned int)anchor_id);

        statuses_pos += (size_t)snprintk(
            statuses + statuses_pos, sizeof(statuses) - statuses_pos, "%s",
            ss_twr_init_cal_status_label(ss_twr_init_sweep_anchor_status[anchor_id]));
        qualities_pos += (size_t)snprintk(
            qualities + qualities_pos, sizeof(qualities) - qualities_pos, "%u",
            (unsigned int)ss_twr_init_sweep_anchor_quality[anchor_id]);
    }

    snprintk(line, sizeof(line), "CS;1;%lu;%s;%u;%u;%s;%s;%s",
             (unsigned long)ss_twr_init_sweep_count, plan_label,
             (unsigned int)positioning_mode, (unsigned int)qf_percent, targets,
             statuses, qualities);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE
    (void)uwb_tag_ble_publish_status(line);
#endif
}

static void ss_twr_init_prepare_sweep_plan(void)
{
    size_t active_count = 0U;

    /* Fusion tags publish ranges only; every sweep uses the configured anchors. */
    for (size_t i = 0; i < ss_twr_init_anchor_count; ++i) {
        ss_twr_init_active_anchor_ids[active_count++] = ss_twr_init_anchor_ids[i];
    }

    ss_twr_init_current_sweep_full = true;
    ss_twr_init_current_sweep_refresh = false;
    ss_twr_init_active_anchor_count = active_count;
    ss_twr_init_active_anchor_index = 0U;
    ss_twr_init_last_sweep_cut_short = false;
    ss_twr_init_reset_sweep_anchor_state();
}

static void ss_twr_init_read_ts(const uint8_t *ts_field, uint32 *ts)
{
    *ts = 0;

    for (int i = 0; i < SS_TWR_INIT_RESP_MSG_TS_LEN; ++i) {
        *ts |= ((uint32)ts_field[i]) << (i * 8);
    }
}

#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U
static uint16_t ss_twr_init_read_le16(const uint8_t *field)
{
    return (uint16_t)field[0] | ((uint16_t)field[1] << 8);
}

static bool ss_twr_init_rf_diag_output_due(void)
{
#if APP_TAG_RF_DIAG_OUTPUT_PERIOD > 1U
    return (ss_twr_init_sweep_count % APP_TAG_RF_DIAG_OUTPUT_PERIOD) == 0U;
#else
    return true;
#endif
}

#if APP_TAG_RF_DIAG_TAG_RX_ENABLE != 0U
static void ss_twr_init_rf_diag_from_rxdiag(
    struct ss_twr_init_rf_diag_sample *out, const dwt_rxdiag_t *diag)
{
    if (out == NULL || diag == NULL) {
        return;
    }

    out->flags = UWB_MSG_RESP_DIAG_FLAGS_VALID;
    out->fp_index = diag->firstPath;
    out->fp_ampl1 = diag->firstPathAmp1;
    out->fp_ampl2 = diag->firstPathAmp2;
    out->fp_ampl3 = diag->firstPathAmp3;
    out->cir_pwr = diag->maxGrowthCIR;
    out->rxpacc = diag->rxPreamCount;
    out->std_noise = diag->stdNoise;
    /* LDE_THRESH + AGC_STAT1 are not in dwt_rxdiag_t; read directly. This runs
     * in the tag RX hot path (right after dwt_readdiagnostics), so the
     * registers still hold the just-received response frame's values. */
    out->lde_thresh = dwt_read16bitoffsetreg(LDE_IF_ID, LDE_THRESH_OFFSET);
    out->agc_stat1 =
        dwt_read32bitoffsetreg(AGC_CTRL_ID, AGC_STAT1_OFFSET) & AGC_STAT1_MASK;
    out->temp_raw = 0U;  /* tag-side resp diag carries no responder temp */
    out->vbat_raw = 0U;
}
#endif

static bool ss_twr_init_parse_resp_diag_v2(
    const uint8_t *frame, uint32_t frame_len,
    struct ss_twr_init_rf_diag_sample *out)
{
    if (frame == NULL || out == NULL || frame_len < UWB_MSG_RESP_V2_FRAME_LEN) {
        return false;
    }
    if (frame[UWB_MSG_RESP_DIAG_VERSION_IDX] != UWB_MSG_RESP_DIAG_VERSION ||
        (frame[UWB_MSG_RESP_DIAG_FLAGS_IDX] &
         UWB_MSG_RESP_DIAG_FLAGS_VALID) == 0U) {
        return false;
    }

    out->flags = frame[UWB_MSG_RESP_DIAG_FLAGS_IDX];
    out->fp_index =
        ss_twr_init_read_le16(&frame[UWB_MSG_RESP_DIAG_FP_INDEX_IDX]);
    out->fp_ampl1 =
        ss_twr_init_read_le16(&frame[UWB_MSG_RESP_DIAG_FP_AMPL1_IDX]);
    out->fp_ampl2 =
        ss_twr_init_read_le16(&frame[UWB_MSG_RESP_DIAG_FP_AMPL2_IDX]);
    out->fp_ampl3 =
        ss_twr_init_read_le16(&frame[UWB_MSG_RESP_DIAG_FP_AMPL3_IDX]);
    out->cir_pwr =
        ss_twr_init_read_le16(&frame[UWB_MSG_RESP_DIAG_CIR_PWR_IDX]);
    out->rxpacc =
        ss_twr_init_read_le16(&frame[UWB_MSG_RESP_DIAG_RXPACC_IDX]);
    out->std_noise =
        ss_twr_init_read_le16(&frame[UWB_MSG_RESP_DIAG_STD_NOISE_IDX]);
    out->lde_thresh = 0U; /* anchor-side LDE_THRESH not carried over-air */
    out->agc_stat1 = 0U;  /* anchor-side AGC_STAT1 not carried over-air */
    if (frame_len >= UWB_MSG_RESP_V3_FRAME_LEN) {
        out->temp_raw = frame[UWB_MSG_RESP_DIAG_TEMP_IDX];
        out->vbat_raw = frame[UWB_MSG_RESP_DIAG_VBAT_IDX];
    } else {
        out->temp_raw = 0U;
        out->vbat_raw = 0U;
    }
    return true;
}

#if APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE != 0U
static void ss_twr_init_publish_rf_diag(
    uint8_t poll_seq,
    uint8_t anchor_id,
    long raw_distance_mm,
    uint32_t resp_rx_ts,
    int32_t carrier_integrator,
    const struct ss_twr_init_rf_diag_sample *anchor_poll_diag,
    const struct ss_twr_init_rf_diag_sample *tag_resp_diag)
{
    char line[256];
    const struct ss_twr_init_rf_diag_sample empty = {0};
    const struct ss_twr_init_rf_diag_sample *ap =
        anchor_poll_diag != NULL ? anchor_poll_diag : &empty;
    const struct ss_twr_init_rf_diag_sample *tr =
        tag_resp_diag != NULL ? tag_resp_diag : &empty;

    if (!ss_twr_init_rf_diag_output_due()) {
        return;
    }

    snprintk(line, sizeof(line),
             /* ...;ap_temp;ap_vbat;ap_lde_thresh;ap_agc_stat1;
              *              tr_lde_thresh;tr_agc_stat1 (4 trailing columns) */
             "RFD;1;%lu;%u;%u;%ld;%lu;%ld;"
             "%u;%u;%u;%u;%u;%u;%u;%u;"
             "%u;%u;%u;%u;%u;%u;%u;%u;"
             "%u;%u;%u;%lu;%u;%lu",
             (unsigned long)ss_twr_init_sweep_count,
             (unsigned int)poll_seq,
             (unsigned int)anchor_id,
             raw_distance_mm,
             (unsigned long)resp_rx_ts,
             (long)carrier_integrator,
             (unsigned int)ap->flags,
             (unsigned int)ap->fp_index,
             (unsigned int)ap->fp_ampl1,
             (unsigned int)ap->fp_ampl2,
             (unsigned int)ap->fp_ampl3,
             (unsigned int)ap->cir_pwr,
             (unsigned int)ap->rxpacc,
             (unsigned int)ap->std_noise,
             (unsigned int)tr->flags,
             (unsigned int)tr->fp_index,
             (unsigned int)tr->fp_ampl1,
             (unsigned int)tr->fp_ampl2,
             (unsigned int)tr->fp_ampl3,
             (unsigned int)tr->cir_pwr,
             (unsigned int)tr->rxpacc,
             (unsigned int)tr->std_noise,
             (unsigned int)ap->temp_raw,
             (unsigned int)ap->vbat_raw,
             (unsigned int)ap->lde_thresh,
             (unsigned long)ap->agc_stat1,
             (unsigned int)tr->lde_thresh,
             (unsigned long)tr->agc_stat1);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE && APP_TAG_RF_DIAG_OUTPUT_BLE_ENABLE != 0U
    (void)uwb_tag_ble_publish_status(line);
#endif
}
#endif
#endif

static void ss_twr_init_write_ts(uint8_t *ts_field, uint32 ts)
{
    for (int i = 0; i < SS_TWR_INIT_RESP_MSG_TS_LEN; ++i) {
        ts_field[i] = (uint8_t)(ts >> (i * 8));
    }
}

/* Read DW1000 on-chip temperature via SAR ADC. Returns whole degrees C.
 * (The periodic ;T trailer on the TR line carries the raw SAR code; this
 * wrapper is for the one-time boot readback and the capture control loop.) */
static int8_t read_dw1000_temperature(void)
{
    uint16_t raw = dwt_readtempvbat(1); /* fastSPI=1; hi byte = raw temp */

    return (int8_t)lroundf(dwt_convertrawtemperature((uint8_t)(raw >> 8)));
}

/* Read DW1000 event counters (DIG_DIAG 0x2F). Requires EVC_EN set once at
 * init (done in ss_twr_init_apply_txrf_and_diag()). Call at cell start and
 * end; diff gives per-cell link stats. */
typedef struct {
    uint16_t evc_sto;   /* SFD timeout count */
    uint16_t evc_pto;   /* Preamble timeout count */
    uint16_t evc_fce;   /* FCS error count */
    uint16_t evc_fcg;   /* FCS good count */
    uint16_t evc_txfs;  /* TX frame sent count */
} dw_event_counters_t;

static void read_dw1000_event_counters(dw_event_counters_t *out)
{
    if (out == NULL) {
        return;
    }
    out->evc_sto  = dwt_read16bitoffsetreg(DIG_DIAG_ID, EVC_STO_OFFSET)  & 0x0FFFU;
    out->evc_pto  = dwt_read16bitoffsetreg(DIG_DIAG_ID, EVC_PTO_OFFSET)  & 0x0FFFU;
    out->evc_fce  = dwt_read16bitoffsetreg(DIG_DIAG_ID, EVC_FCE_OFFSET)  & 0x0FFFU;
    out->evc_fcg  = dwt_read16bitoffsetreg(DIG_DIAG_ID, EVC_FCG_OFFSET)  & 0x0FFFU;
    out->evc_txfs = dwt_read16bitoffsetreg(DIG_DIAG_ID, EVC_TXFS_OFFSET) & 0x0FFFU;
}

/* CH5/PRF64 TX power + PG delay + event-counter enable.
 * dwt_configuretxrf() was never called in any firmware variant, so TX_POWER
 * (0x1E) and TC_PGDELAY (0x2A:0x0B) ran at DW1000 power-on-reset defaults.
 * Program the tuned CH5/PRF64 Smart-TX reference values (DW1000 User Manual
 * Table 20). The first invocation reads the registers BEFORE writing so the
 * boot log reports the true silicon POR values, then confirms the write. */
static void ss_twr_init_apply_txrf_and_diag(void)
{
    static dwt_txconfig_t txconfig_ch5 = {
        .PGdly = TC_PGDELAY_CH5, /* 0xC0 */
        .power = 0x25456585UL,   /* CH5/PRF64 Smart-TX */
    };
    static bool logged;
    uint32_t tx_power_por = 0U;
    uint8_t pg_delay_por = 0U;
    uint16_t agc_ctrl1_por = 0U;

    if (!logged) {
        tx_power_por = dwt_read32bitreg(TX_POWER_ID);
        pg_delay_por = dwt_read8bitoffsetreg(TX_CAL_ID, TC_PGDELAY_OFFSET);
        agc_ctrl1_por = dwt_read16bitoffsetreg(AGC_CTRL_ID, AGC_CTRL1_OFFSET);
    }

    dwt_configuretxrf(&txconfig_ch5);
    dwt_write8bitoffsetreg(DIG_DIAG_ID, EVC_CTRL_OFFSET, (uint8_t)EVC_EN);

    /* Clear DIS_AM in AGC_CTRL1 (0x23:0x02) so the AGC noise-power measurement
     * runs each RX. Otherwise AGC_STAT1 (EDG1/EDG2) stays 0 at its POR default
     * (DIS_AM=1): dwt_configure() writes AGC_TUNE1/2 but never AGC_CTRL1, so the
     * measurement is left disabled and the tag-local agc_stat1 diag reads 0. */
    {
        uint16_t agc_ctrl1 = dwt_read16bitoffsetreg(AGC_CTRL_ID, AGC_CTRL1_OFFSET);

        dwt_write16bitoffsetreg(AGC_CTRL_ID, AGC_CTRL1_OFFSET,
                                (uint16_t)(agc_ctrl1 & (uint16_t)~AGC_CTRL1_DIS_AM));
    }

    if (!logged) {
        uint32_t tx_power_rb = dwt_read32bitreg(TX_POWER_ID);
        uint8_t pg_delay_rb = dwt_read8bitoffsetreg(TX_CAL_ID, TC_PGDELAY_OFFSET);
        uint16_t agc_ctrl1_rb = dwt_read16bitoffsetreg(AGC_CTRL_ID, AGC_CTRL1_OFFSET);
        dw_event_counters_t evc;

        read_dw1000_event_counters(&evc);
        printk("TXRF cfg tag: TX_POWER 0x%08lX->0x%08lX TC_PGDELAY 0x%02X->0x%02X "
               "AGC_CTRL1 0x%04X->0x%04X temp=%dC EVC sto/pto/fce/fcg/txfs=%u/%u/%u/%u/%u\n",
               (unsigned long)tx_power_por, (unsigned long)tx_power_rb,
               (unsigned int)pg_delay_por, (unsigned int)pg_delay_rb,
               (unsigned int)agc_ctrl1_por, (unsigned int)agc_ctrl1_rb,
               (int)read_dw1000_temperature(),
               (unsigned int)evc.evc_sto, (unsigned int)evc.evc_pto,
               (unsigned int)evc.evc_fce, (unsigned int)evc.evc_fcg,
               (unsigned int)evc.evc_txfs);
        logged = true;
    }
}

static void ss_twr_init_configure_radio(void)
{
    dwt_configure(&ss_twr_init_config);
    ss_twr_init_apply_txrf_and_diag();
    dwt_setpanid(APP_UWB_PAN_ID);
    dwt_setaddress16(ss_twr_init_local_addr);
#if APP_UWB_HW_FRAME_FILTER_ENABLE
    dwt_enableframefilter(SYS_CFG_FFAD);
#else
    dwt_enableframefilter(0);
#endif
    dwt_setrxantennadelay(SS_TWR_INIT_RX_ANT_DLY);
    dwt_settxantennadelay(SS_TWR_INIT_TX_ANT_DLY);
    dwt_setleds(DWT_LEDS_ENABLE);
    dwt_setrxaftertxdelay(SS_TWR_INIT_TX_TO_RX_DLY_UUS);
    dwt_setrxtimeout(SS_TWR_INIT_RESP_RX_TIMEOUT_UUS);
    dwt_setpreambledetecttimeout(0);
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_GOOD |
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
    ss_twr_init_radio_configured = true;
}

static int ss_twr_init_load_runtime_config(
    const struct uwb_tag_runtime_config *config)
{
    if (config == NULL || config->tag_id >= UWB_MAX_TAGS ||
        config->anchor_ids == NULL || config->anchor_count == 0U ||
        config->anchor_count > UWB_MAX_ANCHORS) {
        return -1;
    }

    ss_twr_init_identity_code = config->identity_code;
    ss_twr_init_local_tag_id = config->tag_id;
    ss_twr_init_local_addr = uwb_tag_short_addr(ss_twr_init_local_tag_id);
    ss_twr_init_anchor_count = config->anchor_count;
    ss_twr_init_sweep_count = 0U;
    ss_twr_init_active_anchor_count = 0U;
    ss_twr_init_active_anchor_index = 0U;
    ss_twr_init_current_anchor_retry_count = 0U;
    ss_twr_init_refresh_anchor_cursor = 0U;
    ss_twr_init_current_sweep_full = true;
    ss_twr_init_multitag_anchor_plan_mode = false;
    ss_twr_init_active_plan_count = 0U;
    ss_twr_init_standby_plan_count = 0U;
    ss_twr_init_reserve_plan_count = 0U;
    ss_twr_init_refresh_anchor_budget = 0U;
    ss_twr_init_refresh_interval_sweeps = 0U;
    ss_twr_init_full_sweep_interval_sweeps = 0U;
    ss_twr_init_plan_refresh_cursor = 0U;
    ss_twr_init_tdma_schedule = config->tdma;
    ss_twr_init_beacon_reset();
    if (ss_twr_init_tdma_schedule.enabled &&
        !ss_twr_init_tdma_schedule.epoch_valid) {
        ss_twr_init_tdma_schedule.epoch_ms = 0U;
        ss_twr_init_tdma_schedule.sync_local_ms = 0U;
        ss_twr_init_tdma_schedule.generation = 0U;
    }
    ss_twr_init_runtime_update_pending = false;
    ss_twr_init_last_sweep_cut_short = false;
    ss_twr_init_last_tdma_wait_ms = 0U;
    ss_twr_init_last_slot_guard_log_ms = 0U;
    ss_twr_init_last_solve_pending_log_ms = 0U;
    ss_twr_init_last_solve_diag_ms = 0U;
    ss_twr_init_last_solve_reason = SS_TWR_INIT_SOLVE_NONE;
    memset(ss_twr_init_trackers, 0, sizeof(ss_twr_init_trackers));

    for (size_t i = 0; i < config->anchor_count; ++i) {
        if (config->anchor_ids[i] >= UWB_MAX_ANCHORS) {
            printk("Invalid anchor id in table: %u\n",
                   (unsigned int)config->anchor_ids[i]);
            return -1;
        }

        ss_twr_init_anchor_ids[i] = config->anchor_ids[i];
        uwb_range_tracker_init(&ss_twr_init_trackers[config->anchor_ids[i]],
                               uwb_anchor_short_addr(config->anchor_ids[i]));
    }

    if (config->multitag_anchor_plan_mode) {
        const uint8_t *group_sets[3] = {
            config->active_anchor_ids,
            config->standby_anchor_ids,
            config->reserve_anchor_ids,
        };
        const size_t group_counts[3] = {
            config->active_anchor_count,
            config->standby_anchor_count,
            config->reserve_anchor_count,
        };
        uint8_t *group_dests[3] = {
            ss_twr_init_active_plan_ids,
            ss_twr_init_standby_plan_ids,
            ss_twr_init_reserve_plan_ids,
        };
        size_t *group_dest_counts[3] = {
            &ss_twr_init_active_plan_count,
            &ss_twr_init_standby_plan_count,
            &ss_twr_init_reserve_plan_count,
        };
        const size_t group_caps[3] = {
            UWB_TAG_ACTIVE_ANCHOR_MAX,
            UWB_TAG_STANDBY_ANCHOR_MAX,
            UWB_TAG_RESERVE_ANCHOR_MAX,
        };

        if (config->active_anchor_ids == NULL || config->active_anchor_count < 4U ||
            config->active_anchor_count > UWB_TAG_ACTIVE_ANCHOR_MAX) {
            printk("Invalid multitag active anchor plan count=%u\n",
                   (unsigned int)config->active_anchor_count);
            return -1;
        }

        for (size_t group = 0; group < 3; ++group) {
            if (group_counts[group] > group_caps[group]) {
                printk("Invalid multitag anchor group size=%u group=%u\n",
                       (unsigned int)group_counts[group], (unsigned int)group);
                return -1;
            }

            for (size_t i = 0; i < group_counts[group]; ++i) {
                uint8_t anchor_id = group_sets[group][i];

                if (anchor_id >= UWB_MAX_ANCHORS ||
                    !ss_twr_init_anchor_id_in_list(ss_twr_init_anchor_ids,
                                                   ss_twr_init_anchor_count,
                                                   anchor_id) ||
                    ss_twr_init_anchor_id_in_list(ss_twr_init_active_plan_ids,
                                                  ss_twr_init_active_plan_count,
                                                  anchor_id) ||
                    ss_twr_init_anchor_id_in_list(ss_twr_init_standby_plan_ids,
                                                  ss_twr_init_standby_plan_count,
                                                  anchor_id) ||
                    ss_twr_init_anchor_id_in_list(ss_twr_init_reserve_plan_ids,
                                                  ss_twr_init_reserve_plan_count,
                                                  anchor_id)) {
                    printk("Invalid multitag anchor id=%u group=%u\n",
                           (unsigned int)anchor_id, (unsigned int)group);
                    return -1;
                }

                group_dests[group][(*group_dest_counts[group])++] = anchor_id;
            }
        }

        ss_twr_init_multitag_anchor_plan_mode = true;
        ss_twr_init_refresh_anchor_budget = config->refresh_anchor_budget;
        ss_twr_init_refresh_interval_sweeps = config->refresh_interval_sweeps;
        ss_twr_init_full_sweep_interval_sweeps =
            config->full_sweep_interval_sweeps;
    }

    if (ss_twr_init_tdma_schedule.enabled) {
        if (ss_twr_init_tdma_schedule.slot_count == 0U ||
            ss_twr_init_tdma_schedule.slot_index >=
                ss_twr_init_tdma_schedule.slot_count ||
            ss_twr_init_tdma_schedule.slot_period_ms == 0U ||
            ss_twr_init_tdma_schedule.slot_active_ms == 0U ||
            ss_twr_init_tdma_schedule.slot_active_ms >
                ss_twr_init_tdma_schedule.slot_period_ms) {
            printk("Invalid TDMA config slot=%u/%u period=%u active=%u\n",
                   (unsigned int)ss_twr_init_tdma_schedule.slot_index,
                   (unsigned int)ss_twr_init_tdma_schedule.slot_count,
                   (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
                   (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms);
            return -1;
        }
    }
    if (config->beacon_sync &&
        (!ss_twr_init_tdma_schedule.enabled ||
         !ss_twr_init_tdma_schedule.epoch_valid ||
         ss_twr_init_tdma_schedule.slot_index == 0U ||
         !tag_beacon_tracking_window_precedes_slot(
             ss_twr_init_tdma_schedule.slot_index,
             ss_twr_init_tdma_schedule.slot_period_ms))) {
        printk("Invalid beacon sync config slot=%u/%u epoch_valid=%u\n",
               (unsigned int)ss_twr_init_tdma_schedule.slot_index,
               (unsigned int)ss_twr_init_tdma_schedule.slot_count,
               (unsigned int)ss_twr_init_tdma_schedule.epoch_valid);
        return -1;
    }
    if (config->beacon_win_n < TAG_BEACON_WINDOW_N_MIN ||
        config->beacon_win_n > TAG_BEACON_WINDOW_N_MAX) {
        printk("Invalid beacon window cadence=%u\n",
               (unsigned int)config->beacon_win_n);
        return -1;
    }

    ss_twr_init_runtime_params.identity_code = config->identity_code;
    ss_twr_init_runtime_params.logical_tag_id = config->tag_id;
    ss_twr_init_runtime_params.slot_source = config->slot_source;
    ss_twr_init_runtime_params.positioning_mode = config->positioning_mode;
    ss_twr_init_runtime_params.anchor_selection_mode =
        UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
    ss_twr_init_runtime_params.fixed_anchor_count = 0U;
    ss_twr_init_runtime_params.beacon_sync = config->beacon_sync;
    ss_twr_init_runtime_params.beacon_win_n = TAG_BEACON_WINDOW_N_DEFAULT;
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
    ss_twr_init_runtime_params.dw_anchor = config->dw_anchor;
#endif
    memset(ss_twr_init_runtime_params.fixed_anchor_ids, 0,
           sizeof(ss_twr_init_runtime_params.fixed_anchor_ids));
    ss_twr_init_runtime_params.tdma = ss_twr_init_tdma_schedule;

    return 0;
}

/*
 * Tier 2 phase telemetry: detect BLE-connection-event preemption of the UWB RX
 * collector busy-wait directly, as a multi-cycle time gap between spin
 * iterations.  This yields both the per-sweep in-slot RX-preempt count and the
 * in-slot offset where the BLE event landed (BLE-event <-> UWB-slot offset),
 * with zero changes to the BLE stack.  Clock = RTC @ 32768 Hz (~30.5 us/cycle);
 * a BLE conn event (>= ~150 us, typ. 0.5-2 ms) is far above the spin-jitter
 * floor.  See docs/tier2_phase_telemetry_design_20260627.md.
 */
#ifndef SS_TWR_INIT_PHASE_TELEMETRY_ENABLE
/* freeze-clean batch4a: default the Tier-2 phase-telemetry (;TP trailer) OFF.
 * It was a bring-up diagnostic; production TR must not carry ;TP. Still
 * overridable via -DSS_TWR_INIT_PHASE_TELEMETRY_ENABLE=1 for diagnostics. */
#define SS_TWR_INIT_PHASE_TELEMETRY_ENABLE 0U
#endif

#if SS_TWR_INIT_PHASE_TELEMETRY_ENABLE != 0U
/* ~122 us at 32768 Hz; BLE conn events are larger, normal spins are < 1 cycle. */
#ifndef SS_TWR_INIT_PHASE_PREEMPT_GAP_CYC
#define SS_TWR_INIT_PHASE_PREEMPT_GAP_CYC 4U
#endif
/* Emit a heartbeat TP line every N sweeps even when no preemption is seen. */
#ifndef SS_TWR_INIT_PHASE_HEARTBEAT_SWEEPS
#define SS_TWR_INIT_PHASE_HEARTBEAT_SWEEPS 50U
#endif

static uint32_t ss_twr_init_phase_window_start_cyc;
static uint32_t ss_twr_init_phase_window_cyc;
static uint32_t ss_twr_init_phase_last_spin_cyc;
static uint16_t ss_twr_init_phase_preempt_count;
static uint32_t ss_twr_init_phase_max_gap_cyc;
static uint32_t ss_twr_init_phase_total_gap_cyc;
static uint32_t ss_twr_init_phase_first_off_cyc;
static uint32_t ss_twr_init_phase_worst_off_cyc;
static bool ss_twr_init_phase_skip_next;

static inline void ss_twr_init_phase_loop_begin(uint32_t window_start_cyc,
                                                uint32_t window_cyc)
{
    ss_twr_init_phase_window_start_cyc = window_start_cyc;
    ss_twr_init_phase_window_cyc = window_cyc;
    ss_twr_init_phase_last_spin_cyc = window_start_cyc;
    ss_twr_init_phase_preempt_count = 0U;
    ss_twr_init_phase_max_gap_cyc = 0U;
    ss_twr_init_phase_total_gap_cyc = 0U;
    ss_twr_init_phase_first_off_cyc = 0U;
    ss_twr_init_phase_worst_off_cyc = 0U;
    ss_twr_init_phase_skip_next = false;
}

/*
 * Call at the top of each collector spin iteration with a fresh cycle sample.
 * Charges the gap since the previous spin sample; a frame-processing iteration
 * sets skip_next so its SPI readout time is not mistaken for a preemption.
 */
static inline void ss_twr_init_phase_loop_tick(uint32_t now_cyc)
{
    if (ss_twr_init_phase_skip_next) {
        ss_twr_init_phase_skip_next = false;
        ss_twr_init_phase_last_spin_cyc = now_cyc;
        return;
    }

    uint32_t gap = now_cyc - ss_twr_init_phase_last_spin_cyc;

    if (gap >= SS_TWR_INIT_PHASE_PREEMPT_GAP_CYC) {
        uint32_t off =
            ss_twr_init_phase_last_spin_cyc - ss_twr_init_phase_window_start_cyc;

        ss_twr_init_phase_preempt_count++;
        ss_twr_init_phase_total_gap_cyc += gap;
        if (ss_twr_init_phase_preempt_count == 1U) {
            ss_twr_init_phase_first_off_cyc = off;
        }
        if (gap > ss_twr_init_phase_max_gap_cyc) {
            ss_twr_init_phase_max_gap_cyc = gap;
            ss_twr_init_phase_worst_off_cyc = off;
        }
    }

    ss_twr_init_phase_last_spin_cyc = now_cyc;
}

/* Mark that the current iteration processed a frame/timeout (not pure spin). */
static inline void ss_twr_init_phase_loop_event(void)
{
    ss_twr_init_phase_skip_next = true;
}

/*
 * Emit one per-sweep telemetry line, but only when a preemption was seen (the
 * victim announces itself) or on the periodic heartbeat (proves the path is
 * alive).  Clean tags stay near-silent so the extra BLE traffic is negligible.
 */
static void ss_twr_init_phase_publish(uint32_t sweep, uint8_t slot, uint8_t valid)
{
    /* T17 freeze gate: DIAG-only telemetry; no TP output when DIAG runtime OFF. */
    if (!ss_twr_init_rf_diag_runtime_on) {
        return;
    }
#if APP_TAG_BLE_ENABLE
    char line[96];

    if (ss_twr_init_phase_preempt_count == 0U &&
        (SS_TWR_INIT_PHASE_HEARTBEAT_SWEEPS == 0U ||
         (sweep % SS_TWR_INIT_PHASE_HEARTBEAT_SWEEPS) != 0U)) {
        return;
    }

    snprintk(line, sizeof(line),
             "TP;1;%lu;%u;%u;%u;%lu;%lu;%lu;%lu;%lu",
             (unsigned long)sweep, (unsigned int)slot, (unsigned int)valid,
             (unsigned int)ss_twr_init_phase_preempt_count,
             (unsigned long)k_cyc_to_us_floor32(ss_twr_init_phase_max_gap_cyc),
             (unsigned long)k_cyc_to_us_floor32(ss_twr_init_phase_first_off_cyc),
             (unsigned long)k_cyc_to_us_floor32(ss_twr_init_phase_worst_off_cyc),
             (unsigned long)k_cyc_to_us_floor32(ss_twr_init_phase_total_gap_cyc),
             (unsigned long)k_cyc_to_us_floor32(ss_twr_init_phase_window_cyc));
    (void)uwb_tag_ble_publish_status(line);
#else
    ARG_UNUSED(sweep);
    ARG_UNUSED(slot);
    ARG_UNUSED(valid);
#endif
}

/*
 * Tail-RX death-mode diagnostic.  When a late responder (rank 6/7) is missing we
 * need to know WHY: (i) the DW1000 never received it (air/collision/RX not armed),
 * (ii) it was received but the single-buffer readout raced and overran
 * (SYS_STATUS_RXOVRR), or (iii) the collector window closed before it.  We OR all
 * SYS_STATUS bits seen during the window (RXOVRR / ALL_RX_ERR / ALL_RX_TO are
 * decisive) and record which anchors dropped, the highest rank serviced, the loop
 * exit reason and the elapsed time at close.  Host decodes statusOr to pick the
 * fix: RXOVRR => enable double-buffer RX/RXAUTR; errors => air; clean timeout with
 * full window => never on-air.  See docs/tier2_phase_telemetry_design_20260627.md.
 */
static uint32_t ss_twr_init_tailq_status_or;
static uint16_t ss_twr_init_tailq_errto_cnt;
static uint8_t ss_twr_init_tailq_active_mask;
static uint8_t ss_twr_init_tailq_dropmask;
static uint8_t ss_twr_init_tailq_maxrank;
static uint8_t ss_twr_init_tailq_valid;
static uint8_t ss_twr_init_tailq_exit;       /* 0 = all received, 1 = deadline */
static uint32_t ss_twr_init_tailq_close_us;
static uint32_t ss_twr_init_tailq_win_us;

static inline void ss_twr_init_tailq_begin(uint8_t active_mask, uint32_t win_us)
{
    ss_twr_init_tailq_status_or = 0U;
    ss_twr_init_tailq_errto_cnt = 0U;
    ss_twr_init_tailq_active_mask = active_mask;
    ss_twr_init_tailq_dropmask = 0U;
    ss_twr_init_tailq_maxrank = 0U;
    ss_twr_init_tailq_valid = 0U;
    ss_twr_init_tailq_exit = 1U;
    ss_twr_init_tailq_close_us = 0U;
    ss_twr_init_tailq_win_us = win_us;
}

static inline void ss_twr_init_tailq_observe(uint32_t status_reg)
{
    ss_twr_init_tailq_status_or |= status_reg;
}

static inline void ss_twr_init_tailq_note_errto(void)
{
    ss_twr_init_tailq_errto_cnt++;
}

static void ss_twr_init_tailq_finish(const bool *received, uint8_t active_mask,
                                     uint8_t poll_count, uint8_t responses,
                                     uint32_t close_elapsed_cyc)
{
    uint8_t dropmask = 0U;
    uint8_t maxrank = 0U;
    uint8_t valid = 0U;

    for (uint8_t a = 0U; a < UWB_MAX_ANCHORS; ++a) {
        if ((active_mask & (uint8_t)(1U << a)) == 0U) {
            continue;
        }
        uint8_t rank = 0U;
        for (uint8_t i = 0U; i < a; ++i) {
            if ((active_mask & (uint8_t)(1U << i)) != 0U) {
                rank++;
            }
        }
        if (received[a]) {
            valid++;
            if (rank > maxrank) {
                maxrank = rank;
            }
        } else {
            dropmask |= (uint8_t)(1U << a);
        }
    }
    ss_twr_init_tailq_dropmask = dropmask;
    ss_twr_init_tailq_maxrank = maxrank;
    ss_twr_init_tailq_valid = valid;
    ss_twr_init_tailq_exit = (responses >= poll_count) ? 0U : 1U;
    ss_twr_init_tailq_close_us = k_cyc_to_us_floor32(close_elapsed_cyc);
}

/* Emit when any anchor dropped (focus on tail) or on the periodic heartbeat. */
static void ss_twr_init_tailq_publish(uint32_t sweep, uint8_t slot)
{
    /* T17 freeze gate: DIAG-only telemetry; no TQ output when DIAG runtime OFF. */
    if (!ss_twr_init_rf_diag_runtime_on) {
        return;
    }
#if APP_TAG_BLE_ENABLE
    char line[112];

    if (ss_twr_init_tailq_dropmask == 0U &&
        (SS_TWR_INIT_PHASE_HEARTBEAT_SWEEPS == 0U ||
         (sweep % SS_TWR_INIT_PHASE_HEARTBEAT_SWEEPS) != 0U)) {
        return;
    }

    snprintk(line, sizeof(line),
             "TQ;1;%lu;%u;%u;%02x;%02x;%u;%06lx;%u;%u;%lu;%lu",
             (unsigned long)sweep, (unsigned int)slot,
             (unsigned int)ss_twr_init_tailq_valid,
             (unsigned int)ss_twr_init_tailq_active_mask,
             (unsigned int)ss_twr_init_tailq_dropmask,
             (unsigned int)ss_twr_init_tailq_maxrank,
             (unsigned long)(ss_twr_init_tailq_status_or & 0x00FFFFFFUL),
             (unsigned int)(ss_twr_init_tailq_errto_cnt > 255U ?
                            255U : ss_twr_init_tailq_errto_cnt),
             (unsigned int)ss_twr_init_tailq_exit,
             (unsigned long)ss_twr_init_tailq_close_us,
             (unsigned long)ss_twr_init_tailq_win_us);
    (void)uwb_tag_ble_publish_status(line);
#else
    ARG_UNUSED(sweep);
    ARG_UNUSED(slot);
#endif
}
#else /* telemetry disabled: compile to nothing */
static inline void ss_twr_init_phase_loop_begin(uint32_t window_start_cyc,
                                                uint32_t window_cyc)
{
    ARG_UNUSED(window_start_cyc);
    ARG_UNUSED(window_cyc);
}
static inline void ss_twr_init_phase_loop_tick(uint32_t now_cyc)
{
    ARG_UNUSED(now_cyc);
}
static inline void ss_twr_init_phase_loop_event(void)
{
}
static inline void ss_twr_init_phase_publish(uint32_t sweep, uint8_t slot,
                                             uint8_t valid)
{
    ARG_UNUSED(sweep);
    ARG_UNUSED(slot);
    ARG_UNUSED(valid);
}
static inline void ss_twr_init_tailq_begin(uint8_t active_mask, uint32_t win_us)
{
    ARG_UNUSED(active_mask);
    ARG_UNUSED(win_us);
}
static inline void ss_twr_init_tailq_observe(uint32_t status_reg)
{
    ARG_UNUSED(status_reg);
}
static inline void ss_twr_init_tailq_note_errto(void)
{
}
static inline void ss_twr_init_tailq_finish(const bool *received,
                                            uint8_t active_mask,
                                            uint8_t poll_count,
                                            uint8_t responses,
                                            uint32_t close_elapsed_cyc)
{
    ARG_UNUSED(received);
    ARG_UNUSED(active_mask);
    ARG_UNUSED(poll_count);
    ARG_UNUSED(responses);
    ARG_UNUSED(close_elapsed_cyc);
}
static inline void ss_twr_init_tailq_publish(uint32_t sweep, uint8_t slot)
{
    ARG_UNUSED(sweep);
    ARG_UNUSED(slot);
}
#endif /* SS_TWR_INIT_PHASE_TELEMETRY_ENABLE */

static void ss_twr_init_publish_ranges_if_ready(void)
{
#if APP_TAG_NORMAL_OUTPUT_ENABLE == 0U
    return;
#else
    struct ss_twr_init_range_measurement measurements[UWB_MAX_ANCHORS];
    uint8_t solution_quality_percent;
    size_t valid_anchor_count = 0U;

    memset(measurements, 0, sizeof(measurements));
    for (size_t i = 0; i < ss_twr_init_anchor_count; ++i) {
        uint8_t anchor_id = ss_twr_init_anchor_ids[i];
        struct uwb_range_tracker *tracker = &ss_twr_init_trackers[anchor_id];
        bool measured_this_sweep = ss_twr_init_anchor_id_in_list(
            ss_twr_init_active_anchor_ids, ss_twr_init_active_anchor_count,
            anchor_id);
        bool range_ok_this_sweep =
            ss_twr_init_sweep_anchor_status[anchor_id] ==
            UWB_TAG_BLE_CAL_STATUS_OK;

        measurements[i].anchor_id = anchor_id;
        measurements[i].quality_percent =
            uwb_range_tracker_quality_percent(tracker);
        /* Never reuse a previous value as a fresh measurement. */
        measurements[i].valid = measured_this_sweep && range_ok_this_sweep &&
                                tracker->range_valid;
        measurements[i].range_mm = tracker->range_mm;

        if (APP_TAG_VERBOSE_MEASUREMENTS != 0U && tracker->range_valid) {
            printk("Tag meas anchor=%u range=%lu mm q=%u%%\n",
                   (unsigned int)anchor_id,
                   (unsigned long)measurements[i].range_mm,
                   (unsigned int)measurements[i].quality_percent);
        }
        if (measurements[i].valid) {
            valid_anchor_count++;
        }
    }

    ss_twr_init_publish_bsl_frame(measurements, ss_twr_init_anchor_count);
    solution_quality_percent =
        ss_twr_init_compute_target_quality_percent(measurements,
                                                   ss_twr_init_anchor_count);
    ss_twr_init_last_solve_reason =
        (valid_anchor_count >= 4U) ? SS_TWR_INIT_SOLVE_SUCCESS :
                                     SS_TWR_INIT_SOLVE_PENDING;
    ss_twr_init_publish_tag_range_summary(measurements,
                                          ss_twr_init_anchor_count,
                                          solution_quality_percent);
    ss_twr_init_phase_publish(
        (uint32_t)ss_twr_init_sweep_count,
        (uint8_t)ss_twr_init_tdma_schedule.slot_index,
        (uint8_t)valid_anchor_count);
    ss_twr_init_tailq_publish(
        (uint32_t)ss_twr_init_sweep_count,
        (uint8_t)ss_twr_init_tdma_schedule.slot_index);
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_solve_start_cycles = k_cycle_get_32();
    ss_twr_init_diag_solve_done_cycles = ss_twr_init_diag_solve_start_cycles;
    ss_twr_init_diag_out_start_cycles = ss_twr_init_diag_solve_done_cycles;
    ss_twr_init_diag_out_done_cycles = ss_twr_init_diag_out_start_cycles;
#endif
#endif
}

#if APP_ALT_SS_TWR_ENABLE
static uint32_t ss_twr_init_alt_last_poll_diag_ms;
static uint32_t ss_twr_init_alt_last_poll_timing_diag_ms;
static uint32_t ss_twr_init_alt_last_rx_diag_ms;
static uint32_t ss_twr_init_alt_last_rx_gap_diag_ms;
static uint32_t ss_twr_init_alt_ltdma_slot_start_cycles;
static uint32_t ss_twr_init_alt_last_sweep_entry_cycles;
static uint32_t ss_twr_init_alt_last_tx_sched_cycles;
static uint32_t ss_twr_init_alt_last_tx_write_done_cycles;
static uint32_t ss_twr_init_alt_last_tx_cmd_cycles;
static bool ss_twr_init_alt_bcast_tx_prearmed;
static bool ss_twr_init_alt_last_tx_prearmed;
static uint8_t ss_twr_init_alt_bcast_prearmed_seq;
static uint8_t ss_twr_init_alt_bcast_prearmed_mask;
static uint8_t ss_twr_init_alt_bcast_prearmed_count;

static void ss_twr_init_alt_publish_rx_gap_diag(uint32_t tx_done_cycles,
                                                uint32_t rx_start_cycles,
                                                uint32_t rx_done_cycles,
                                                uint32_t response_window_us,
                                                uint8_t poll_count,
                                                uint8_t anchor_mask,
                                                int rxenable_rc)
{
#if APP_TAG_NORMAL_OUTPUT_ENABLE == 0U
    ARG_UNUSED(tx_done_cycles);
    ARG_UNUSED(rx_start_cycles);
    ARG_UNUSED(rx_done_cycles);
    ARG_UNUSED(response_window_us);
    ARG_UNUSED(poll_count);
    ARG_UNUSED(anchor_mask);
    ARG_UNUSED(rxenable_rc);
    return;
#endif
    uint32_t now_ms = (uint32_t)k_uptime_get();
    char line[384];
    uint32_t slot_to_txdone_us = broadcast_tdma_slot_to_us(
        ss_twr_init_alt_ltdma_slot_start_cycles, tx_done_cycles);
    uint32_t slot_to_entry_us = broadcast_tdma_slot_to_us(
        ss_twr_init_alt_ltdma_slot_start_cycles,
        ss_twr_init_alt_last_sweep_entry_cycles);
    uint32_t slot_to_sched_us = broadcast_tdma_slot_to_us(
        ss_twr_init_alt_ltdma_slot_start_cycles,
        ss_twr_init_alt_last_tx_sched_cycles);
    uint32_t slot_to_write_done_us = broadcast_tdma_slot_to_us(
        ss_twr_init_alt_ltdma_slot_start_cycles,
        ss_twr_init_alt_last_tx_write_done_cycles);
    uint32_t slot_to_txcmd_us = broadcast_tdma_slot_to_us(
        ss_twr_init_alt_ltdma_slot_start_cycles,
        ss_twr_init_alt_last_tx_cmd_cycles);
    uint32_t txcmd_to_txdone_us =
        (ss_twr_init_alt_last_tx_cmd_cycles != 0U &&
         tx_done_cycles != 0U) ?
            k_cyc_to_us_floor32(tx_done_cycles -
                                ss_twr_init_alt_last_tx_cmd_cycles) :
            UINT_MAX;

    if (tx_done_cycles == 0U || rx_start_cycles == 0U || rx_done_cycles == 0U) {
        return;
    }

    if (ss_twr_init_alt_last_rx_gap_diag_ms != 0U &&
        (uint32_t)(now_ms - ss_twr_init_alt_last_rx_gap_diag_ms) < 1000U) {
        return;
    }
    ss_twr_init_alt_last_rx_gap_diag_ms = now_ms;

    snprintk(line, sizeof(line),
             "RXG;1;%lu;tag=%u;mask=0x%02x;pc=%u;guard=%u;spacing=%u;win=%lu;pre=%u;slot_to_entry_us=%lu;slot_to_sched_us=%lu;slot_to_write_done_us=%lu;slot_to_txcmd_us=%lu;slot_to_txdone_us=%lu;txcmd_to_txdone_us=%lu;txdone_to_rxstart_us=%lu;txdone_to_rxend_us=%lu;rxenable_us=%lu;rc=%d;slot=%u/%u;period=%u;active=%u;active_us=%u;lperiod=%u;lcount=%u",
             (unsigned long)ss_twr_init_sweep_count,
             (unsigned int)ss_twr_init_local_tag_id,
             (unsigned int)anchor_mask,
             (unsigned int)poll_count,
             (unsigned int)APP_ALT_SS_TWR_GUARD_US,
             (unsigned int)APP_ALT_SS_TWR_RESP_SPACING_US,
             (unsigned long)response_window_us,
             (unsigned int)ss_twr_init_alt_last_tx_prearmed,
             (unsigned long)slot_to_entry_us,
             (unsigned long)slot_to_sched_us,
             (unsigned long)slot_to_write_done_us,
             (unsigned long)slot_to_txcmd_us,
             (unsigned long)slot_to_txdone_us,
             (unsigned long)txcmd_to_txdone_us,
             (unsigned long)k_cyc_to_us_floor32(rx_start_cycles - tx_done_cycles),
             (unsigned long)k_cyc_to_us_floor32(rx_done_cycles - tx_done_cycles),
             (unsigned long)k_cyc_to_us_floor32(rx_done_cycles - rx_start_cycles),
             rxenable_rc,
             (unsigned int)ss_twr_init_tdma_schedule.slot_index,
             (unsigned int)ss_twr_init_tdma_schedule.slot_count,
             (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
             (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms,
             (unsigned int)ss_twr_init_tdma_schedule.slot_active_us,
             (unsigned int)APP_TAG_TDMA_SLOT_PERIOD_MS,
             (unsigned int)APP_TAG_TDMA_SLOT_COUNT);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE && APP_TAG_ALT_RXG_BLE_DIAG_ENABLE != 0U
    (void)uwb_tag_ble_publish_status(line);
#endif
}

static void ss_twr_init_alt_publish_rx_diag(uint32_t status_reg,
                                            uint32_t rx_finfo,
                                            uint32_t response_window_us,
                                            uint8_t poll_count,
                                            uint8_t anchor_mask,
                                            uint8_t responses,
                                            uint8_t unexpected_count,
                                            uint32_t last_frame_len,
                                            uint16_t last_src_addr,
                                            uint16_t last_dst_addr,
                                            uint8_t last_code)
{
#if APP_TAG_NORMAL_OUTPUT_ENABLE == 0U
    ARG_UNUSED(status_reg);
    ARG_UNUSED(rx_finfo);
    ARG_UNUSED(response_window_us);
    ARG_UNUSED(poll_count);
    ARG_UNUSED(anchor_mask);
    ARG_UNUSED(responses);
    ARG_UNUSED(unexpected_count);
    ARG_UNUSED(last_frame_len);
    ARG_UNUSED(last_src_addr);
    ARG_UNUSED(last_dst_addr);
    ARG_UNUSED(last_code);
    return;
#endif
    uint32_t now_ms = (uint32_t)k_uptime_get();
    char line[160];

    if (ss_twr_init_alt_last_rx_diag_ms != 0U &&
        (uint32_t)(now_ms - ss_twr_init_alt_last_rx_diag_ms) < 1000U) {
        return;
    }
    ss_twr_init_alt_last_rx_diag_ms = now_ms;

    snprintk(line, sizeof(line),
             "CD;1;%lu;tag=%u;local=0x%04x;status=0x%08lx;rxf=0x%08lx;win=%lu;pc=%u;mask=0x%02x;resp=%u;unexp=%u;last_len=%lu;last_src=0x%04x;last_dst=0x%04x;last_code=0x%02x",
             (unsigned long)ss_twr_init_sweep_count,
             (unsigned int)ss_twr_init_local_tag_id,
             (unsigned int)ss_twr_init_local_addr,
             (unsigned long)status_reg,
             (unsigned long)rx_finfo,
             (unsigned long)response_window_us,
             (unsigned int)poll_count,
             (unsigned int)anchor_mask,
             (unsigned int)responses,
             (unsigned int)unexpected_count,
             (unsigned long)last_frame_len,
             (unsigned int)last_src_addr,
             (unsigned int)last_dst_addr,
             (unsigned int)last_code);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE && APP_TAG_ALT_RXG_BLE_DIAG_ENABLE != 0U
    (void)uwb_tag_ble_publish_status(line);
#endif
}

static void ss_twr_init_alt_mark_scheduled_poll_timing(uint32_t poll_cycle,
                                                       uint8_t poll_count)
{
    ss_twr_init_sweep_first_poll_cycle = poll_cycle;
    /*
     * Alt v3 uses one broadcast poll carrying the active anchor mask. All
     * selected anchors share the same measurement instant, so first-to-last
     * poll skew is intentionally zero even when poll_count is 4/8.
     */
    ss_twr_init_sweep_last_poll_cycle = poll_cycle;
    ss_twr_init_sweep_poll_count = poll_count;
    ss_twr_init_sweep_timing_valid = true;
}

static void ss_twr_init_alt_mark_unicast_poll_timing(uint32_t first_cycle,
                                                     uint32_t last_cycle,
                                                     uint8_t poll_count)
{
    ss_twr_init_sweep_first_poll_cycle = first_cycle;
    ss_twr_init_sweep_last_poll_cycle = last_cycle;
    ss_twr_init_sweep_poll_count = poll_count;
    ss_twr_init_sweep_timing_valid = true;
}

static void ss_twr_init_alt_print_poll_diag(uint8_t poll_count,
                                            uint8_t anchor_mask)
{
#if APP_TAG_NORMAL_OUTPUT_ENABLE == 0U
    ARG_UNUSED(poll_count);
    ARG_UNUSED(anchor_mask);
    return;
#endif
    uint32_t now_ms = (uint32_t)k_uptime_get();
    uint16_t poll_dst = uwb_frame_get_dst_addr(ss_twr_init_tx_poll_msg);
    uint8_t frame_tag_id = uwb_ss_twr_poll_tag_id(ss_twr_init_tx_poll_msg);
    uint64_t frame_poll_tx_ts = uwb_ss_twr_poll_tx_ts(ss_twr_init_tx_poll_msg);

    if (ss_twr_init_alt_last_poll_diag_ms != 0U &&
        (uint32_t)(now_ms - ss_twr_init_alt_last_poll_diag_ms) <
            APP_TAG_ALT_POLL_DIAG_PERIOD_MS) {
        return;
    }

    ss_twr_init_alt_last_poll_diag_ms = now_ms;
    printk("Alt poll diag tag=%u src=0x%04x dst=0x%04x seq=%u mode=%u poll_count=%u mask=0x%02x "
           "frame_tag=%u poll_tx_ts=0x%08lx%02lx active=%u,%u,%u,%u,%u,%u,%u,%u\n",
           (unsigned int)ss_twr_init_local_tag_id,
           (unsigned int)ss_twr_init_local_addr,
           (unsigned int)poll_dst,
           (unsigned int)ss_twr_init_frame_seq_nb,
           (unsigned int)APP_ALT_SS_TWR_MODE,
           (unsigned int)poll_count,
           (unsigned int)anchor_mask,
           (unsigned int)frame_tag_id,
           (unsigned long)(frame_poll_tx_ts >> 8U),
           (unsigned long)(frame_poll_tx_ts & 0xffU),
           (unsigned int)ss_twr_init_active_anchor_ids[0],
           (unsigned int)ss_twr_init_active_anchor_ids[1],
           (unsigned int)ss_twr_init_active_anchor_ids[2],
           (unsigned int)ss_twr_init_active_anchor_ids[3],
           (unsigned int)ss_twr_init_active_anchor_ids[4],
           (unsigned int)ss_twr_init_active_anchor_ids[5],
           (unsigned int)ss_twr_init_active_anchor_ids[6],
           (unsigned int)ss_twr_init_active_anchor_ids[7]);
}

static void ss_twr_init_alt_print_unicast_timing_diag(
    uint8_t poll_count,
    const uint32_t *target_poll_cycles,
    const uint32_t *write_start_cycles,
    const uint32_t *write_done_cycles,
    const uint32_t *starttx_cycles,
    const uint32_t *txfrs_cycles)
{
#if APP_TAG_NORMAL_OUTPUT_ENABLE == 0U
    ARG_UNUSED(poll_count);
    ARG_UNUSED(target_poll_cycles);
    ARG_UNUSED(write_start_cycles);
    ARG_UNUSED(write_done_cycles);
    ARG_UNUSED(starttx_cycles);
    ARG_UNUSED(txfrs_cycles);
    return;
#endif
    uint32_t now_ms = (uint32_t)k_uptime_get();
    uint32_t poll_start_gap_us[4] = {0};
    uint32_t write_us[4] = {0};
    uint32_t start_to_frs_us[4] = {0};
    int32_t lateness_us[4] = {0};
    uint8_t diag_count;
    char line[192];

    if (poll_count == 0U) {
        return;
    }
    if (ss_twr_init_alt_last_poll_timing_diag_ms != 0U &&
        (uint32_t)(now_ms - ss_twr_init_alt_last_poll_timing_diag_ms) <
            APP_TAG_ALT_POLL_DIAG_PERIOD_MS) {
        return;
    }
    ss_twr_init_alt_last_poll_timing_diag_ms = now_ms;

    diag_count = (poll_count > 4U) ? 4U : poll_count;
    for (uint8_t i = 0U; i < diag_count; ++i) {
        write_us[i] = k_cyc_to_us_floor32(
            write_done_cycles[i] - write_start_cycles[i]);
        start_to_frs_us[i] = k_cyc_to_us_floor32(
            txfrs_cycles[i] - starttx_cycles[i]);
        lateness_us[i] = (int32_t)k_cyc_to_us_floor32(
            starttx_cycles[i] - target_poll_cycles[i]);
        if (i > 0U) {
            poll_start_gap_us[i - 1U] = k_cyc_to_us_floor32(
                starttx_cycles[i] - starttx_cycles[i - 1U]);
        }
    }

    snprintk(line, sizeof(line),
             "CD;2;%lu;tag=%u;src=0x%04x;pc=%u;spacing=%u;gap=%lu,%lu,%lu;write=%lu,%lu,%lu,%lu;txfrs=%lu,%lu,%lu,%lu;late=%ld,%ld,%ld,%ld",
             (unsigned long)ss_twr_init_sweep_count,
             (unsigned int)ss_twr_init_local_tag_id,
             (unsigned int)ss_twr_init_local_addr,
             (unsigned int)poll_count,
             (unsigned int)APP_ALT_SS_TWR_POLL_SPACING_US,
             (unsigned long)poll_start_gap_us[0],
             (unsigned long)poll_start_gap_us[1],
             (unsigned long)poll_start_gap_us[2],
             (unsigned long)write_us[0],
             (unsigned long)write_us[1],
             (unsigned long)write_us[2],
             (unsigned long)write_us[3],
             (unsigned long)start_to_frs_us[0],
             (unsigned long)start_to_frs_us[1],
             (unsigned long)start_to_frs_us[2],
             (unsigned long)start_to_frs_us[3],
             (long)lateness_us[0],
             (long)lateness_us[1],
             (long)lateness_us[2],
             (long)lateness_us[3]);
    printk("%s\n", line);
#if APP_TAG_BLE_ENABLE && APP_TAG_ALT_RXG_BLE_DIAG_ENABLE != 0U
    (void)uwb_tag_ble_publish_status(line);
#endif
}

static void ss_twr_init_alt_record_range(uint8_t anchor_id, long raw_distance_mm)
{
    struct uwb_range_tracker *tracker = &ss_twr_init_trackers[anchor_id];
    uint32_t range_mm;

    if (raw_distance_mm < 0L) {
        raw_distance_mm = 0L;
    }

    if (!ss_twr_init_range_measurement_valid((uint32_t)raw_distance_mm)) {
        uwb_range_tracker_record_failure(tracker);
        ss_twr_init_record_sweep_anchor_state(anchor_id,
                                              UWB_TAG_BLE_CAL_STATUS_REJECT,
                                              tracker);
        ss_twr_init_record_sweep_anchor_diag(
            anchor_id, SS_TWR_INIT_CAL_REASON_RANGE_INVALID, raw_distance_mm,
            tracker->range_mm, 0U, 0U,
            uwb_range_tracker_quality_percent(tracker));
#if APP_TAG_BLE_ENABLE
        ss_twr_init_publish_cal_range(anchor_id, UWB_TAG_BLE_CAL_STATUS_REJECT,
                                      raw_distance_mm, tracker->range_mm,
                                      tracker);
#endif
        return;
    }

    range_mm = uwb_range_tracker_record_success(tracker, (uint32_t)raw_distance_mm);
    ss_twr_init_record_sweep_anchor_state(anchor_id, UWB_TAG_BLE_CAL_STATUS_OK,
                                          tracker);
    ss_twr_init_record_sweep_anchor_diag(
        anchor_id, SS_TWR_INIT_CAL_REASON_OK, raw_distance_mm, range_mm, 0U,
        0U, uwb_range_tracker_quality_percent(tracker));
#if APP_TAG_BLE_ENABLE
    ss_twr_init_publish_cal_range(anchor_id, UWB_TAG_BLE_CAL_STATUS_OK,
                                  raw_distance_mm, range_mm, tracker);
#endif
}

static void ss_twr_init_alt_record_timeout(uint8_t anchor_id, uint8_t reason)
{
    struct uwb_range_tracker *tracker = &ss_twr_init_trackers[anchor_id];

    uwb_range_tracker_record_failure(tracker);
    ss_twr_init_record_sweep_anchor_state(anchor_id,
                                          UWB_TAG_BLE_CAL_STATUS_TIMEOUT,
                                          tracker);
    ss_twr_init_record_sweep_anchor_diag(
        anchor_id, reason, 0, tracker->range_mm, 0U, 0U,
        uwb_range_tracker_quality_percent(tracker));
#if APP_TAG_BLE_ENABLE
    ss_twr_init_publish_cal_range(anchor_id, UWB_TAG_BLE_CAL_STATUS_TIMEOUT, 0,
                                  tracker->range_mm, tracker);
#endif
}

static void ss_twr_init_alt_finish_sweep(void)
{
    ss_twr_init_sweep_count++;
#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST && \
    APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE != 0U
    ss_twr_init_note_sweep_done();
    ss_twr_init_beacon_service_post_sweep_if_urgent();
    ss_twr_init_publish_ranges_if_ready();
    ss_twr_init_apply_pending_runtime_config_if_any();
    ss_twr_init_prepare_sweep_plan();
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_clean_done_cycles = k_cycle_get_32();
    ss_twr_init_sweep_diag_maybe_print();
#endif
#else
    ss_twr_init_release_ble_tx_after_active_slot();
    ss_twr_init_note_sweep_done();
    ss_twr_init_publish_ranges_if_ready();
    ss_twr_init_apply_pending_runtime_config_if_any();
    ss_twr_init_last_tdma_wait_ms = ss_twr_init_wait_until_next_slot_if_needed();
    ss_twr_init_prepare_sweep_plan();
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_clean_done_cycles = k_cycle_get_32();
    ss_twr_init_sweep_diag_maybe_print();
#endif
#endif
}

static bool ss_twr_init_alt_wait_tx_done(uint32_t timeout_us)
{
    uint32_t start = k_cycle_get_32();
    uint32_t timeout_cycles = k_us_to_cyc_floor32(timeout_us);

    while ((uint32_t)(k_cycle_get_32() - start) < timeout_cycles) {
        uint32_t status = dwt_read32bitreg(SYS_STATUS_ID);
        if ((status & SYS_STATUS_TXFRS) != 0U) {
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
            return true;
        }
    }

    return false;
}

static void ss_twr_init_alt_wait_until_cycle(uint32_t target_cycle)
{
    while ((int32_t)(k_cycle_get_32() - target_cycle) < 0) {
    }
}

static uint8_t ss_twr_init_alt_mask_rank(uint8_t mask, uint8_t anchor_id)
{
    uint8_t rank = 0U;

    for (uint8_t i = 0U; i < anchor_id && i < UWB_MAX_ANCHORS; ++i) {
        if ((mask & (uint8_t)(1U << i)) != 0U) {
            rank++;
        }
    }

    return rank;
}

static uint8_t ss_twr_init_alt_mask_rank_from_offset(uint8_t mask,
                                                      uint8_t anchor_id,
                                                      uint8_t rank_offset)
{
    uint8_t rank = 0U;

    if (anchor_id >= UWB_MAX_ANCHORS ||
        (mask & (uint8_t)(1U << anchor_id)) == 0U) {
        return BSL_ANCHOR_NONE;
    }

    rank_offset %= UWB_MAX_ANCHORS;
    for (uint8_t step = 0U; step < UWB_MAX_ANCHORS; ++step) {
        uint8_t candidate =
            (uint8_t)((rank_offset + step) % UWB_MAX_ANCHORS);

        if ((mask & (uint8_t)(1U << candidate)) == 0U) {
            continue;
        }
        if (candidate == anchor_id) {
            return rank;
        }
        rank++;
    }

    return BSL_ANCHOR_NONE;
}

static int16_t ss_twr_init_bsl_cfo_ppm_q8(int32_t carrier_integrator)
{
    double ppm_q8 =
        (double)carrier_integrator * FREQ_OFFSET_MULTIPLIER *
        HERTZ_TO_PPM_MULTIPLIER_CHAN_5 * 256.0;

    if (ppm_q8 > (double)INT16_MAX) {
        return INT16_MAX;
    }
    if (ppm_q8 < (double)INT16_MIN) {
        return INT16_MIN;
    }

    return (int16_t)(ppm_q8 >= 0.0 ? ppm_q8 + 0.5 : ppm_q8 - 0.5);
}

static uint16_t ss_twr_init_bsl_t_round_us(uint8_t anchor_id)
{
    const uint64_t ts40_mask = (1ULL << 40) - 1ULL;
    const uint64_t ticks_per_5_us = 319488ULL;
    uint64_t poll_tx;
    uint64_t resp_rx;
    uint64_t delta_ticks;
    uint64_t rounded_us;

    if (anchor_id >= UWB_MAX_ANCHORS ||
        !ss_twr_init_bsl_poll_tx_valid ||
        !ss_twr_init_bsl_resp_rx_valid[anchor_id]) {
        return BSL_TROUND_INVALID;
    }

    poll_tx = bsl_ts40_get(ss_twr_init_bsl_poll_tx_ts);
    resp_rx = bsl_ts40_get(ss_twr_init_bsl_resp_rx_ts[anchor_id]);
    delta_ticks = (resp_rx - poll_tx) & ts40_mask;

    /*
     * DW1000 time is 499.2 MHz * 128 = 63897.6 ticks/us. Express the
     * conversion as 319488 ticks per 5 us and round to the nearest us.
     */
    rounded_us =
        (delta_ticks * 5ULL + (ticks_per_5_us / 2ULL)) / ticks_per_5_us;
    if (rounded_us >= BSL_TROUND_INVALID) {
        return BSL_TROUND_INVALID;
    }

    return (uint16_t)rounded_us;
}

static void ss_twr_init_publish_bsl_frame(
    const struct ss_twr_init_range_measurement *measurements, size_t measurement_count)
{
    bsl_uwb_t body = {0};
    uint8_t slot_count =
        (uint8_t)MIN(ss_twr_init_active_anchor_count, BSL_MAX_ANCHORS);

    body.sweep = ss_twr_init_public_sweep();
    memcpy(body.poll_tx_ts, ss_twr_init_bsl_poll_tx_ts,
           sizeof(body.poll_tx_ts));
    body.identity_code = ss_twr_init_runtime_params.identity_code;
    body.logical_tag_id = ss_twr_init_runtime_params.logical_tag_id;
    body.guard_us = (uint16_t)APP_ALT_SS_TWR_GUARD_US;
    body.spacing_us = (uint16_t)APP_ALT_SS_TWR_RESP_SPACING_US;

    for (uint8_t slot = 0U; slot < BSL_MAX_ANCHORS; ++slot) {
        body.anchor_id[slot] = BSL_ANCHOR_NONE;
        body.rank[slot] = BSL_ANCHOR_NONE;
        body.range_mm[slot] = BSL_RANGE_INVALID;
        body.t_round_us[slot] = BSL_TROUND_INVALID;
    }

    for (uint8_t slot = 0U; slot < slot_count; ++slot) {
        uint8_t anchor_id = ss_twr_init_active_anchor_ids[slot];
        const struct ss_twr_init_range_measurement *measurement = NULL;

        if (anchor_id >= UWB_MAX_ANCHORS) {
            continue;
        }

        for (size_t i = 0U; i < measurement_count; ++i) {
            if (measurements[i].anchor_id == anchor_id) {
                measurement = &measurements[i];
                break;
            }
        }

        body.anchor_id[slot] = anchor_id;
        body.rank[slot] = ss_twr_init_alt_mask_rank_from_offset(
            ss_twr_init_bsl_anchor_mask, anchor_id,
            ss_twr_init_bsl_rank_offset);
        body.t_round_us[slot] =
            ss_twr_init_bsl_t_round_us(anchor_id);
        body.cfo_ppm_q8[slot] = ss_twr_init_bsl_cfo_ppm_q8(
            ss_twr_init_bsl_carrier_integrator[anchor_id]);

        if (measurement == NULL) {
            continue;
        }

        body.quality[slot] = measurement->quality_percent;
        if (measurement->valid &&
            measurement->range_mm < BSL_RANGE_INVALID &&
            body.rank[slot] != BSL_ANCHOR_NONE) {
            body.range_mm[slot] = (uint16_t)measurement->range_mm;
            body.valid_mask |= (uint8_t)BIT(slot);
        }
    }

    if (ss_twr_init_bsl_strobe_sent) {
        body.flags |= BSL_FLAG_STROBE_SENT;
    }
    if (ss_twr_init_bsl_response_count < ss_twr_init_bsl_poll_count) {
        body.flags |= BSL_FLAG_SWEEP_PARTIAL;
    }
    if (uwb_tag_ble_identity_is_nvs()) {
        body.flags |= BSL_FLAG_IDENTITY_NVS;
    }
	body.flags = tag_relay8_epoch_encode_flags(
		body.flags, &ss_twr_init_sweep_epoch);

    (void)biospur_uart_link_submit(&body);
    if ((ss_twr_init_sweep_count % 100U) == 0U) {
        uwb_tag_ble_publish_link_status();
    }
}

static void ss_twr_init_alt_rx_restart(uint32_t response_window_us)
{
    dwt_forcetrxoff();
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_RX_GOOD | SYS_STATUS_ALL_RX_ERR |
                          SYS_STATUS_ALL_RX_TO);
    dwt_setrxtimeout(response_window_us);
    dwt_rxenable(DWT_START_RX_IMMEDIATE);
}

/*
 * RXAUTR fix (2026-06-27): in BROADCAST mode let the DW1000 hardware auto
 * re-enable RX after each good frame (SYS_CFG_RXAUTR) instead of relying on the
 * host to manually re-arm between responders. TQ telemetry showed the multi-tag
 * "random victim" loses ALL responders after the first 1-3 with a clean
 * non-detection (no RX error, no overrun, no timeout, window not closed early):
 * the manual re-arm is skipped when a BLE connection event preempts the host in
 * the gap right after a frame, leaving the receiver disarmed for the rest of the
 * slot. Hardware auto re-enable keeps the receiver listening across that
 * preemption. With RXAUTR on the good-frame manual dwt_rxenable() calls are
 * redundant (they would re-issue an RX enable on an already-armed receiver), so
 * they compile out; the error/timeout path still re-arms manually because RXAUTR
 * does NOT auto re-enable after RX errors or timeouts. If RXOVRR shows up in the
 * TQ status_or after this (single-buffer readout racing the auto re-arm), the
 * companion step is dwt_setdblrxbuffmode() double-buffer RX.
 *
 * RESULT 2026-06-27 (FALSIFIED, kept OFF): enabling RXAUTR with SINGLE buffer was
 * a catastrophic regression. A/B 6-tag@10Hz/120s vs the manual-re-arm baseline:
 * the baseline gives 4/6 tags a full anchor set (ge7 60-97%) with only 2 BLE-phase
 * victims; RXAUTR collapsed ALL 6 tags to rank-0 (ge7 0%, mValid 0.1-1.0). In this
 * single-buffer manual-poll collector the hardware auto re-enable does NOT deliver
 * the 2nd..Nth responder, so removing the good-frame manual dwt_rxenable() kills
 * the receiver after the first frame. The manual re-arm path is NOT the bottleneck
 * (RXOVRR=0% in baseline, 4/6 tags perfect) -- the victim loss is a narrow per-tag
 * BLE-event/UWB-slot phase collision, not re-arm/readout fragility. Leave RXAUTR
 * OFF. The only principled RXAUTR variant left is RXAUTR + dwt_setdblrxbuffmode()
 * double-buffer with the swap-based readout protocol, but baseline RXOVRR=0% says
 * readout races aren't the bottleneck, so it is unlikely to rescue the victims.
 * See [[tdma-capacity-ble-phase-beat]].
 */
#ifndef SS_TWR_INIT_BCAST_RXAUTR_ENABLE
#define SS_TWR_INIT_BCAST_RXAUTR_ENABLE 0U
#endif

static void ss_twr_init_alt_set_rx_auto_reenable(bool enable)
{
    uint32_t sys_cfg = dwt_read32bitreg(SYS_CFG_ID);

    if (enable) {
        sys_cfg |= SYS_CFG_RXAUTR;
    } else {
        sys_cfg &= ~SYS_CFG_RXAUTR;
    }
    dwt_write32bitreg(SYS_CFG_ID, sys_cfg);
}

/*
 * RXAUTR + DOUBLE-BUFFER test (2026-06-28): single-buffer RXAUTR was falsified
 * (see comment above). The remaining principled variant is hardware auto re-enable
 * (RXAUTR) PLUS double receive buffer (clear SYS_CFG_DIS_DRXB): the IC keeps
 * receiving into the alternate buffer and auto re-arms across a host stall (a BLE
 * connection event preempting the collector), and the host reads the previous
 * buffer + toggles the Host Receive Buffer Pointer. This is exactly the DW1000
 * design intent for "host can fall behind by up to one frame", which is the victim's
 * BLE-preemption failure mode. When RXDBLBUF is on, RXAUTR is forced on too (double
 * buffer needs hw re-enable to survive the stall).
 *
 * RESULT 2026-06-28 (NOT A WIN, kept OFF): 6-tag@10Hz/120s vs the manual-re-arm
 * baseline. It DID rescue one victim (BSCCF4 ge7 10%->54%) -- the preemption-survival
 * mechanism is real -- but DEGRADED the healthy tags (BSDC91 98%->34%, BS955A 87->71,
 * BS2DCE 60->47), left the other victim dead (BS9336), and aggregate ge7 dropped
 * 60->50%. TQ status_or shows RXOVRR on 26-85% of sweeps (worst on the previously
 * perfect tags) vs baseline 0%: with the receiver always on (RXAUTR), it grabs every
 * frame on air incl. dense cross-slot traffic and the host cannot drain two buffers
 * fast enough -> pervasive overrun -> the recover path dumps frame bursts. Baseline's
 * single-buffer manual re-arm PACES the receiver to the tag's own responder train
 * (RXOVRR=0%), which is why it is the best-behaved config. A frame-VOLUME problem is
 * not fixable by adding a second buffer. Keep OFF. See [[tdma-capacity-ble-phase-beat]].
 */
#ifndef SS_TWR_INIT_BCAST_RXDBLBUF_ENABLE
#define SS_TWR_INIT_BCAST_RXDBLBUF_ENABLE 0U
#endif

/* Effective hardware RX auto re-enable: explicit RXAUTR, or implied by double buffer. */
#define SS_TWR_INIT_BCAST_RX_HW_REENABLE \
    (SS_TWR_INIT_BCAST_RXAUTR_ENABLE != 0U || \
     SS_TWR_INIT_BCAST_RXDBLBUF_ENABLE != 0U)

/*
 * Release a responder frame we just finished reading out of the host RX buffer and
 * make the receiver ready for the next responder. Mode-specific:
 *   - double buffer + RXAUTR: clear the host-buffer good-frame flags, then toggle the
 *     Host Receive Buffer Pointer (HRBPT) to release this buffer and advance the masked
 *     status to the other buffer (mirrors the driver dwt_isr()); the IC keeps RX armed.
 *   - single-buffer RXAUTR (falsified): clear status; the IC re-arms.
 *   - baseline single buffer: clear status + manual dwt_rxenable() (load-bearing).
 */
static inline void ss_twr_init_alt_rx_release_frame(void)
{
#if SS_TWR_INIT_BCAST_RXDBLBUF_ENABLE != 0U
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_GOOD);
    dwt_write8bitoffsetreg(SYS_CTRL_ID, SYS_CTRL_HRBT_OFFSET, 1);
#elif SS_TWR_INIT_BCAST_RXAUTR_ENABLE != 0U
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_GOOD |
                                         SYS_STATUS_ALL_RX_ERR |
                                         SYS_STATUS_ALL_RX_TO);
#else
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_GOOD |
                                         SYS_STATUS_ALL_RX_ERR |
                                         SYS_STATUS_ALL_RX_TO);
    (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
#endif
}

/*
 * Recover the receiver after an RX error/timeout/overrun or a corrupt-length frame.
 * RXAUTR does NOT auto re-enable after errors, so always reset + re-enable; in double
 * buffer also force the transceiver off and resync the host/IC buffer pointers.
 */
static inline void ss_twr_init_alt_rx_recover(void)
{
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_GOOD |
                                         SYS_STATUS_ALL_RX_ERR |
                                         SYS_STATUS_ALL_RX_TO);
#if SS_TWR_INIT_BCAST_RXDBLBUF_ENABLE != 0U
    dwt_forcetrxoff();
    dwt_rxreset();
    dwt_syncrxbufptrs();
    (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
#else
    dwt_rxreset();
    (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
#endif
}

static uint8_t ss_twr_init_alt_active_anchor_mask(uint8_t poll_count)
{
    uint8_t anchor_mask = 0U;

    for (uint8_t i = 0U; i < poll_count; ++i) {
        uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];
        if (anchor_id < UWB_MAX_ANCHORS) {
            anchor_mask |= (uint8_t)(1U << anchor_id);
        }
    }

    return anchor_mask;
}

static uint8_t ss_twr_init_compact_cir_target_anchor(uint8_t poll_count)
{
    uint32_t sample_index;

    if (poll_count == 0U) {
        return UWB_MAX_ANCHORS;
    }

#if APP_TAG_CIR_COMPACT_SAMPLE_PERIOD > 1U
    sample_index = ss_twr_init_sweep_count / APP_TAG_CIR_COMPACT_SAMPLE_PERIOD;
#else
    sample_index = ss_twr_init_sweep_count;
#endif
    return ss_twr_init_active_anchor_ids[sample_index % poll_count];
}

static bool ss_twr_init_compact_cir_sample_due(void)
{
    if (ss_twr_init_cir_mode_get() != UWB_TAG_CIR_MODE_COMPACT) {
        return false;
    }

#if APP_TAG_CIR_COMPACT_SAMPLE_PERIOD > 1U
    return (ss_twr_init_sweep_count % APP_TAG_CIR_COMPACT_SAMPLE_PERIOD) == 0U;
#else
    return true;
#endif
}

static uint8_t ss_twr_init_alt_bcast_rank_offset(uint8_t poll_count)
{
    uint8_t target_anchor_id;

#if APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE < UWB_MAX_ANCHORS
    ARG_UNUSED(poll_count);
    return (uint8_t)APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE;
#else
    if (!ss_twr_init_compact_cir_sample_due()) {
        return 0U;
    }

    target_anchor_id = ss_twr_init_compact_cir_target_anchor(poll_count);
    if (target_anchor_id >= UWB_MAX_ANCHORS) {
        return 0U;
    }

    return (uint8_t)((target_anchor_id + 1U) % UWB_MAX_ANCHORS);
#endif
}

static bool ss_twr_init_alt_bcast_prewrite_tx(void)
{
#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST && \
    APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE != 0U && \
    APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE != 0U
    uint8_t poll_count = (uint8_t)ss_twr_init_active_anchor_count;
    uint8_t anchor_mask;

    ss_twr_init_alt_bcast_tx_prearmed = false;
    if (poll_count == 0U || poll_count > UWB_MAX_ANCHORS) {
        return false;
    }

    anchor_mask = ss_twr_init_alt_active_anchor_mask(poll_count);
    if (anchor_mask == 0U) {
        return false;
    }

    ss_twr_init_prepare_radio_for_poll();
    uwb_ss_twr_build_alt_broadcast_poll_frame(ss_twr_init_tx_poll_msg,
                                              ss_twr_init_frame_seq_nb,
                                              ss_twr_init_local_addr,
                                              anchor_mask,
                                              ss_twr_init_local_tag_id,
                                              ss_twr_init_alt_bcast_rank_offset(
                                                  poll_count),
                                              0U);
    if (dwt_writetxdata(UWB_MSG_ALT_POLL_FRAME_LEN,
                        ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
        return false;
    }
    dwt_writetxfctrl(UWB_MSG_ALT_POLL_FRAME_LEN, 0, 1);

    ss_twr_init_alt_bcast_prearmed_seq = ss_twr_init_frame_seq_nb;
    ss_twr_init_alt_bcast_prearmed_mask = anchor_mask;
    ss_twr_init_alt_bcast_prearmed_count = poll_count;
    ss_twr_init_alt_bcast_tx_prearmed = true;
    return true;
#else
    return false;
#endif
}

static bool ss_twr_init_alt_burst_sweep_once(void)
{
    ss_twr_init_alt_last_sweep_entry_cycles = k_cycle_get_32();
	tag_relay8_epoch_snapshot(
		&ss_twr_init_sweep_epoch, &ss_twr_init_beacon.epoch);
    /*
     * T17 freeze gate: the phase/tail-RX telemetry is a DIAG-only diagnostic.
     * When the runtime DIAG flag is OFF (production) NO telemetry work runs in the
     * RX collector hot path -- not even a k_cycle_get_32() read. The flag is
     * sampled once per sweep so behaviour is consistent within a sweep.
     */
    const bool phase_tel_on = ss_twr_init_rf_diag_runtime_on;
    /*
     * Clear phase counters at entry so a sweep that aborts before the RX
     * collector (e.g. poll TX failure) reports zero preemption rather than the
     * previous sweep's stale values.  Re-initialised with the real window once
     * the collector starts.
     */
    if (phase_tel_on) {
        ss_twr_init_phase_loop_begin(ss_twr_init_alt_last_sweep_entry_cycles, 0U);
    }
    ss_twr_init_alt_last_tx_sched_cycles = 0U;
    ss_twr_init_alt_last_tx_write_done_cycles = 0U;
    ss_twr_init_alt_last_tx_cmd_cycles = 0U;
    ss_twr_init_alt_last_tx_prearmed = false;

    uint8_t poll_count = (uint8_t)ss_twr_init_active_anchor_count;
    uint32_t poll_tx_ts[UWB_MAX_ANCHORS] = {0};
    uint32_t resp_rx_ts_by_anchor[UWB_MAX_ANCHORS] = {0};
    uint32_t poll_rx_ts_by_anchor[UWB_MAX_ANCHORS] = {0};
    uint32_t resp_tx_ts_by_anchor[UWB_MAX_ANCHORS] = {0};
    int32_t carrier_integrator_by_anchor[UWB_MAX_ANCHORS] = {0};
    long raw_distance_mm[UWB_MAX_ANCHORS] = {0};
    bool received[UWB_MAX_ANCHORS] = {0};
    uint8_t responses = 0U;
    uint8_t unexpected_count = 0U;
    uint32_t response_window_us;
    uint32_t last_status_reg = 0U;
    uint32_t last_rx_finfo = 0U;
    uint32_t last_frame_len = 0U;
    uint16_t last_src_addr = 0U;
    uint16_t last_dst_addr = 0U;
    uint8_t last_code = 0U;
    uint8_t anchor_mask = 0U;
    uint32_t response_window_start_cycles = 0U;
    uint32_t response_window_cycles = 0U;
    uint32_t first_poll_cycle = 0U;
    uint32_t last_poll_cycle = 0U;
    uint32_t poll_tx_done_cycles = 0U;
    uint32_t rx_enable_start_cycles = 0U;
    uint32_t rx_enable_done_cycles = 0U;
    int rxenable_rc = 0;
    bool use_prearmed_tx = false;
    uint8_t rank_offset = 0U;
    enum uwb_tag_cir_mode cir_mode = ss_twr_init_cir_mode_get();
    bool cir_diag_valid = false;
    bool cir_diag_pending = false;
    uint8_t cir_diag_anchor_id = UWB_MAX_ANCHORS;
    dwt_rxdiag_t cir_rx_diag;
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U
#if APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE != 0U
    uint8_t rf_diag_poll_seq = ss_twr_init_frame_seq_nb;
#endif
    struct ss_twr_init_rf_diag_sample anchor_poll_diag_by_anchor[UWB_MAX_ANCHORS] = {0};
    struct ss_twr_init_rf_diag_sample tag_resp_diag_by_anchor[UWB_MAX_ANCHORS] = {0};
#endif

    if (poll_count == 0U || poll_count > UWB_MAX_ANCHORS) {
        return false;
    }

    memset(ss_twr_init_bsl_poll_tx_ts, 0,
           sizeof(ss_twr_init_bsl_poll_tx_ts));
    memset(ss_twr_init_bsl_resp_rx_ts, 0,
           sizeof(ss_twr_init_bsl_resp_rx_ts));
    memset(ss_twr_init_bsl_carrier_integrator, 0,
           sizeof(ss_twr_init_bsl_carrier_integrator));
    ss_twr_init_bsl_poll_tx_valid = false;
    memset(ss_twr_init_bsl_resp_rx_valid, 0,
           sizeof(ss_twr_init_bsl_resp_rx_valid));
    ss_twr_init_bsl_anchor_mask = 0U;
    ss_twr_init_bsl_poll_count = poll_count;
    ss_twr_init_bsl_response_count = 0U;
    ss_twr_init_bsl_strobe_sent = false;
    rank_offset = ss_twr_init_alt_bcast_rank_offset(poll_count);
    ss_twr_init_bsl_rank_offset = rank_offset;

#if APP_ALT_SS_TWR_MODE != APP_ALT_SS_TWR_MODE_BROADCAST || \
    APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE == 0U
    ss_twr_init_set_ble_tx_paused(true);
#endif
    anchor_mask = ss_twr_init_alt_active_anchor_mask(poll_count);
    ss_twr_init_bsl_anchor_mask = anchor_mask;
    response_window_us = ss_twr_init_alt_bcast_response_window_us(poll_count);
    response_window_cycles = k_us_to_cyc_floor32(response_window_us);
#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
    /* Order matters: setdblrxbuffmode() writes the driver's CACHED sysCFGreg (which
     * has no RXAUTR), so it must run BEFORE set_rx_auto_reenable()'s direct read-
     * modify-write that adds RXAUTR; setrxtimeout(0) only rewrites SYS_CFG byte 3 read
     * fresh from HW, so it preserves both DIS_DRXB (byte 1) and RXAUTR (byte 3). */
#if SS_TWR_INIT_BCAST_RXDBLBUF_ENABLE != 0U
    dwt_setdblrxbuffmode(1);
#else
    dwt_setdblrxbuffmode(0);
#endif
    ss_twr_init_alt_set_rx_auto_reenable(SS_TWR_INIT_BCAST_RX_HW_REENABLE);
    dwt_setrxtimeout(0U);
#endif

#if !(APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST && \
      APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE != 0U && \
      APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE != 0U)
    ss_twr_init_prepare_radio_for_poll();
#else
    use_prearmed_tx =
        ss_twr_init_alt_bcast_tx_prearmed &&
        ss_twr_init_alt_bcast_prearmed_seq == ss_twr_init_frame_seq_nb &&
        ss_twr_init_alt_bcast_prearmed_mask == anchor_mask &&
        ss_twr_init_alt_bcast_prearmed_count == poll_count;

    if (!use_prearmed_tx) {
        ss_twr_init_prepare_radio_for_poll();
    }
#endif

#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_UNICAST
    ss_twr_init_alt_print_poll_diag(poll_count, 0U);
    uint32_t poll_spacing_cycles =
        k_us_to_cyc_floor32(APP_ALT_SS_TWR_POLL_SPACING_US);
    uint32_t target_poll_cycles[UWB_MAX_ANCHORS] = {0};
    uint32_t write_start_cycles[UWB_MAX_ANCHORS] = {0};
    uint32_t write_done_cycles[UWB_MAX_ANCHORS] = {0};
    uint32_t starttx_cycles[UWB_MAX_ANCHORS] = {0};
    uint32_t txfrs_cycles[UWB_MAX_ANCHORS] = {0};
    for (uint8_t i = 0U; i < poll_count; ++i) {
        uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];
        uint16_t anchor_addr = uwb_anchor_short_addr(anchor_id);
        uint32_t target_poll_cycle = 0U;
        uint32_t poll_start_cycle;

        write_start_cycles[i] = k_cycle_get_32();
        uwb_ss_twr_build_alt_poll_frame(ss_twr_init_tx_poll_msg,
                                        ss_twr_init_frame_seq_nb,
                                        anchor_addr,
                                        ss_twr_init_local_addr,
                                        i, poll_count);
        if (dwt_writetxdata(UWB_MSG_ALT_UNICAST_POLL_FRAME_LEN,
                            ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
            for (uint8_t j = 0U; j < poll_count; ++j) {
                ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[j],
                                               SS_TWR_INIT_CAL_REASON_RX_ERROR);
            }
            ss_twr_init_alt_finish_sweep();
            return true;
        }
        dwt_writetxfctrl(UWB_MSG_ALT_UNICAST_POLL_FRAME_LEN, 0, 1);
        write_done_cycles[i] = k_cycle_get_32();
        if (i > 0U) {
            target_poll_cycle = first_poll_cycle +
                ((uint32_t)i * poll_spacing_cycles);
            ss_twr_init_alt_wait_until_cycle(target_poll_cycle);
        } else {
            target_poll_cycle = k_cycle_get_32();
        }
        poll_start_cycle = k_cycle_get_32();
        if (first_poll_cycle == 0U) {
            first_poll_cycle = poll_start_cycle;
            target_poll_cycle = poll_start_cycle;
        }
        last_poll_cycle = poll_start_cycle;
        target_poll_cycles[i] = target_poll_cycle;
        starttx_cycles[i] = poll_start_cycle;
        int poll_tx_rc = dwt_starttx(DWT_START_TX_IMMEDIATE);

        if (poll_tx_rc != DWT_SUCCESS) {
            ss_twr_init_record_poll_tx_failure(poll_tx_rc);
            for (uint8_t j = 0U; j < poll_count; ++j) {
                ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[j],
                                               SS_TWR_INIT_CAL_REASON_RX_ERROR);
            }
            ss_twr_init_alt_finish_sweep();
            return true;
        }
        if (!ss_twr_init_alt_wait_tx_done(APP_ALT_SS_TWR_POLL_SPACING_US + 1000U)) {
            dwt_forcetrxoff();
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
            for (uint8_t j = 0U; j < poll_count; ++j) {
                ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[j],
                                               SS_TWR_INIT_CAL_REASON_RX_ERROR);
            }
            ss_twr_init_alt_finish_sweep();
            return true;
        }
        txfrs_cycles[i] = k_cycle_get_32();
        poll_tx_ts[anchor_id] = dwt_readtxtimestamplo32();
        ss_twr_init_frame_seq_nb++;
    }
    ss_twr_init_alt_mark_unicast_poll_timing(first_poll_cycle, last_poll_cycle,
                                             poll_count);
    ss_twr_init_alt_print_unicast_timing_diag(
        poll_count, target_poll_cycles, write_start_cycles, write_done_cycles,
        starttx_cycles, txfrs_cycles);
#else
#if APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE != 0U
    ss_twr_init_alt_last_tx_prearmed = use_prearmed_tx;
    if (use_prearmed_tx) {
        ss_twr_init_alt_last_tx_sched_cycles =
            ss_twr_init_alt_ltdma_slot_start_cycles;
        ss_twr_init_alt_last_tx_write_done_cycles =
            ss_twr_init_alt_ltdma_slot_start_cycles;
    } else {
        ss_twr_init_alt_last_tx_sched_cycles = k_cycle_get_32();
        uwb_ss_twr_build_alt_broadcast_poll_frame(ss_twr_init_tx_poll_msg,
                                                  ss_twr_init_frame_seq_nb,
                                                  ss_twr_init_local_addr,
                                                  anchor_mask,
                                                  ss_twr_init_local_tag_id,
                                                  rank_offset,
                                                  0U);
        ss_twr_init_alt_print_poll_diag(poll_count, anchor_mask);
        if (dwt_writetxdata(UWB_MSG_ALT_POLL_FRAME_LEN,
                            ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
            for (uint8_t i = 0U; i < poll_count; ++i) {
                ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
                                               SS_TWR_INIT_CAL_REASON_RX_ERROR);
            }
            ss_twr_init_alt_finish_sweep();
            return true;
        }
        dwt_writetxfctrl(UWB_MSG_ALT_POLL_FRAME_LEN, 0, 1);
        ss_twr_init_alt_last_tx_write_done_cycles = k_cycle_get_32();
    }
    ss_twr_init_alt_last_tx_cmd_cycles = k_cycle_get_32();
    ss_twr_init_alt_bcast_tx_prearmed = false;
    int poll_tx_rc = dwt_starttx(DWT_START_TX_IMMEDIATE);

    if (poll_tx_rc != DWT_SUCCESS) {
        ss_twr_init_record_poll_tx_failure(poll_tx_rc);
        for (uint8_t i = 0U; i < poll_count; ++i) {
            ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
                                           SS_TWR_INIT_CAL_REASON_RX_ERROR);
        }
        ss_twr_init_alt_finish_sweep();
        return true;
    }
    if (!ss_twr_init_alt_wait_tx_done(SS_TWR_INIT_ALT_BCAST_TX_DONE_TIMEOUT_US)) {
        dwt_forcetrxoff();
        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
        for (uint8_t i = 0U; i < poll_count; ++i) {
            ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
                                           SS_TWR_INIT_CAL_REASON_RX_ERROR);
        }
        ss_twr_init_alt_finish_sweep();
        return true;
    }
    ss_twr_init_bsl_strobe_sent = biospur_uart_link_strobe_pulse();
    dwt_readtxtimestamp(ss_twr_init_bsl_poll_tx_ts);
    ss_twr_init_bsl_poll_tx_valid = true;
    poll_tx_done_cycles = k_cycle_get_32();
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_tx_done_cycles = poll_tx_done_cycles;
#endif
#else
    uint32_t first_tx_time_hi;
    uint64_t scheduled_poll_sys_ts;
    uint64_t scheduled_poll_tx_ts;
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
    uint64_t dw_target_actual40 = 0U;
    uint64_t dw_target_system40 = 0U;
    bool use_dw_anchor =
        ss_twr_init_dw_anchor_target40(&dw_target_actual40);

    if (use_dw_anchor) {
        uint64_t arm_lead_ticks =
            (uint64_t)SS_TWR_INIT_ALT_BCAST_POLL_SCHED_UUS *
            SS_TWR_INIT_UUS_TO_DWT_TIME;
        uint64_t arm_time40;
        uint64_t now40;

        dw_target_system40 = uwb_beacon_sub40(
            dw_target_actual40, SS_TWR_INIT_TX_ANT_DLY);
        arm_time40 = uwb_beacon_sub40(
            dw_target_system40, arm_lead_ticks);
        ss_twr_init_wait_until_dw_time(arm_time40);

        first_tx_time_hi = (uint32_t)(dw_target_system40 >> 8);
        scheduled_poll_sys_ts =
            ((uint64_t)(first_tx_time_hi & 0xFFFFFFFEUL)) << 8;
        scheduled_poll_tx_ts = uwb_beacon_add40(
            scheduled_poll_sys_ts, SS_TWR_INIT_TX_ANT_DLY);
        uwb_ss_twr_build_alt_broadcast_poll_frame(
            ss_twr_init_tx_poll_msg, ss_twr_init_frame_seq_nb,
            ss_twr_init_local_addr, anchor_mask,
            ss_twr_init_local_tag_id, rank_offset,
            scheduled_poll_tx_ts);

        /*
         * Proven relay4/beacon-sub arm sequence: frame prepared first,
         * force RX off, clear low and high TX status, then re-read the
         * DW clock immediately before DX_TIME and delayed start.
         */
        dwt_forcetrxoff();
        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_TX);
        dwt_write16bitoffsetreg(
            SYS_STATUS_ID, 3U, SYS_STATUS_TXERR);
        now40 = ss_twr_init_dw_now40();
        if (tag_relay6_arm_state40(
                dw_target_system40, now40,
                arm_lead_ticks) != TAG_RELAY6_ARM_READY) {
            use_dw_anchor = false;
            ss_twr_init_dw_anchor_note_fallback();
        }
    }

    if (!use_dw_anchor) {
#endif
        first_tx_time_hi =
            dwt_readsystimestamphi32() +
            (uint32_t)((SS_TWR_INIT_ALT_BCAST_POLL_SCHED_UUS *
                        SS_TWR_INIT_UUS_TO_DWT_TIME) >> 8);
        scheduled_poll_sys_ts =
            ((uint64_t)(first_tx_time_hi & 0xFFFFFFFEUL)) << 8;
        scheduled_poll_tx_ts =
            scheduled_poll_sys_ts + SS_TWR_INIT_TX_ANT_DLY;
        uwb_ss_twr_build_alt_broadcast_poll_frame(
            ss_twr_init_tx_poll_msg, ss_twr_init_frame_seq_nb,
            ss_twr_init_local_addr, anchor_mask,
            ss_twr_init_local_tag_id, rank_offset,
            scheduled_poll_tx_ts);
        ss_twr_init_alt_print_poll_diag(poll_count, anchor_mask);
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
    }
#endif
    ss_twr_init_alt_last_tx_sched_cycles = k_cycle_get_32();

	    dwt_setdelayedtrxtime(first_tx_time_hi);
	    if (dwt_writetxdata(UWB_MSG_ALT_POLL_FRAME_LEN,
	                        ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
        for (uint8_t i = 0U; i < poll_count; ++i) {
            ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
                                           SS_TWR_INIT_CAL_REASON_RX_ERROR);
        }
        ss_twr_init_alt_finish_sweep();
        return true;
	    }
	    dwt_writetxfctrl(UWB_MSG_ALT_POLL_FRAME_LEN, 0, 1);
    ss_twr_init_alt_last_tx_write_done_cycles = k_cycle_get_32();
    ss_twr_init_alt_last_tx_cmd_cycles = k_cycle_get_32();
	    int poll_tx_rc = dwt_starttx(DWT_START_TX_DELAYED);
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
    if (use_dw_anchor) {
        ss_twr_init_alt_print_poll_diag(poll_count, anchor_mask);
    }
#endif

	    if (poll_tx_rc != DWT_SUCCESS) {
	        ss_twr_init_record_poll_tx_failure(poll_tx_rc);
	        for (uint8_t i = 0U; i < poll_count; ++i) {
	            ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
	                                           SS_TWR_INIT_CAL_REASON_RX_ERROR);
        }
        ss_twr_init_alt_finish_sweep();
        return true;
    }
    if (!ss_twr_init_alt_wait_tx_done(SS_TWR_INIT_ALT_BCAST_TX_DONE_TIMEOUT_US)) {
        dwt_forcetrxoff();
        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
        for (uint8_t i = 0U; i < poll_count; ++i) {
            ss_twr_init_alt_record_timeout(ss_twr_init_active_anchor_ids[i],
                                           SS_TWR_INIT_CAL_REASON_RX_ERROR);
        }
        ss_twr_init_alt_finish_sweep();
        return true;
    }
    ss_twr_init_bsl_strobe_sent = biospur_uart_link_strobe_pulse();
    dwt_readtxtimestamp(ss_twr_init_bsl_poll_tx_ts);
    ss_twr_init_bsl_poll_tx_valid = true;
    poll_tx_done_cycles = k_cycle_get_32();
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_tx_done_cycles = poll_tx_done_cycles;
#endif
    for (uint8_t i = 0U; i < poll_count; ++i) {
        uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];
        poll_tx_ts[anchor_id] = (uint32_t)scheduled_poll_tx_ts;
    }
#endif
#endif

		#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
	    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_TX);
	    rx_enable_start_cycles = k_cycle_get_32();
	    rxenable_rc = dwt_rxenable(DWT_START_RX_IMMEDIATE);
	    rx_enable_done_cycles = k_cycle_get_32();
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_rx_start_cycles = rx_enable_done_cycles;
#endif
    response_window_start_cycles = rx_enable_done_cycles;
    if (phase_tel_on) {
        ss_twr_init_phase_loop_begin(response_window_start_cycles,
                                     response_window_cycles);
        ss_twr_init_tailq_begin(anchor_mask, response_window_us);
    }

    {
        uint32_t actual_poll_tx_ts = dwt_readtxtimestamplo32();

        for (uint8_t i = 0U; i < poll_count; ++i) {
            uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];
            poll_tx_ts[anchor_id] = actual_poll_tx_ts;
        }
    }

    while ((uint32_t)(k_cycle_get_32() - response_window_start_cycles) <
           response_window_cycles) {
        if (phase_tel_on) {
            ss_twr_init_phase_loop_tick(k_cycle_get_32());
        }
        uint32_t status_reg = dwt_read32bitreg(SYS_STATUS_ID);
        last_status_reg = status_reg;
        if (phase_tel_on) {
            ss_twr_init_tailq_observe(status_reg);
        }

#if SS_TWR_INIT_BCAST_RXDBLBUF_ENABLE != 0U
        /* Both RX buffers filled before the host drained them (host stalled > 1 frame):
         * the buffers/pointers are now suspect. Reset + resync (discards the backlog). */
        if ((status_reg & SYS_STATUS_RXOVRR) != 0U) {
            if (phase_tel_on) {
                ss_twr_init_tailq_note_errto();
            }
            ss_twr_init_alt_rx_recover();
            continue;
        }
#endif

        if ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_TO |
                           SYS_STATUS_ALL_RX_ERR)) == 0U) {
            continue;
        }
        if (phase_tel_on) {
            ss_twr_init_phase_loop_event();
        }
        last_rx_finfo = dwt_read32bitreg(RX_FINFO_ID);

        if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
            uint32 frame_len;
            uint16_t resp_src_addr;
            uint8_t anchor_id;
            uint32 resp_rx_ts;
            uint8_t resp_rx_ts40[5];
            uint32 poll_rx_ts;
            uint32 resp_tx_ts;
            int32_t carrier_integrator;
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U && \
    APP_TAG_RF_DIAG_TAG_RX_ENABLE != 0U
            dwt_rxdiag_t tag_resp_diag = {0};
#endif

            frame_len = last_rx_finfo & RX_FINFO_RXFLEN_MASK;
            last_frame_len = frame_len;
            if (frame_len > sizeof(ss_twr_init_rx_buffer)) {
                ss_twr_init_alt_rx_recover();
                continue;
            }

            dwt_readrxdata(ss_twr_init_rx_buffer, (uint16)frame_len, 0);
            dwt_readrxtimestamp(resp_rx_ts40);
            resp_rx_ts = (uint32_t)bsl_ts40_get(resp_rx_ts40);
            if (ss_twr_init_consume_beacon(
                    ss_twr_init_rx_buffer, frame_len,
                    bsl_ts40_get(resp_rx_ts40)) != 0) {
                ss_twr_init_alt_rx_release_frame();
                continue;
            }
            carrier_integrator = dwt_readcarrierintegrator();
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U && \
    APP_TAG_RF_DIAG_TAG_RX_ENABLE != 0U
            if (ss_twr_init_rf_diag_runtime_on) {
                dwt_readdiagnostics(&tag_resp_diag);
            }
#endif

            resp_src_addr = uwb_frame_get_src_addr(ss_twr_init_rx_buffer);
            last_src_addr = resp_src_addr;
            last_dst_addr = uwb_frame_get_dst_addr(ss_twr_init_rx_buffer);
            last_code = frame_len > UWB_MSG_CODE_IDX ?
                        ss_twr_init_rx_buffer[UWB_MSG_CODE_IDX] : 0U;
            if (!uwb_short_addr_is_anchor(resp_src_addr) ||
                !uwb_ss_twr_resp_matches(ss_twr_init_rx_buffer,
                                         ss_twr_init_local_addr,
                                         resp_src_addr)) {
                unexpected_count++;
                ss_twr_init_alt_rx_release_frame();
                continue;
            }

            anchor_id = uwb_anchor_id_from_addr(resp_src_addr);
            if (anchor_id >= UWB_MAX_ANCHORS || received[anchor_id] ||
                poll_tx_ts[anchor_id] == 0U) {
                unexpected_count++;
                ss_twr_init_alt_rx_release_frame();
                continue;
            }
            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX],
                &poll_rx_ts);
            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX],
                &resp_tx_ts);

            resp_rx_ts_by_anchor[anchor_id] = resp_rx_ts;
            poll_rx_ts_by_anchor[anchor_id] = poll_rx_ts;
            resp_tx_ts_by_anchor[anchor_id] = resp_tx_ts;
            carrier_integrator_by_anchor[anchor_id] = carrier_integrator;
            memcpy(ss_twr_init_bsl_resp_rx_ts[anchor_id], resp_rx_ts40,
                   sizeof(resp_rx_ts40));
            ss_twr_init_bsl_resp_rx_valid[anchor_id] = true;
            ss_twr_init_bsl_carrier_integrator[anchor_id] =
                carrier_integrator;
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U
            if (ss_twr_init_rf_diag_runtime_on) {
#if APP_TAG_RF_DIAG_TAG_RX_ENABLE != 0U
                ss_twr_init_rf_diag_from_rxdiag(
                    &tag_resp_diag_by_anchor[anchor_id], &tag_resp_diag);
#endif
                (void)ss_twr_init_parse_resp_diag_v2(
                    ss_twr_init_rx_buffer, frame_len,
                    &anchor_poll_diag_by_anchor[anchor_id]);
                ss_twr_init_sweep_tag_resp_diag[anchor_id] =
                    tag_resp_diag_by_anchor[anchor_id];
                ss_twr_init_sweep_anchor_poll_diag[anchor_id] =
                    anchor_poll_diag_by_anchor[anchor_id];
                ss_twr_init_sweep_rf_diag_mask |= BIT(anchor_id);
            }
#endif
            if ((cir_mode == UWB_TAG_CIR_MODE_COMPACT &&
                 ss_twr_init_compact_cir_sample_due()) ||
                cir_mode == UWB_TAG_CIR_MODE_FULL) {
                cir_diag_anchor_id = anchor_id;
                cir_diag_pending = true;
            }
            received[anchor_id] = true;
            responses++;
            ss_twr_init_alt_rx_release_frame();
            if (responses >= poll_count) {
                break;
            }
            continue;
        }

        if (phase_tel_on) {
            ss_twr_init_tailq_note_errto();
        }
        ss_twr_init_alt_rx_recover();
    }
    if (phase_tel_on) {
        ss_twr_init_tailq_finish(received, anchor_mask, poll_count, responses,
                                 k_cycle_get_32() - response_window_start_cycles);
    }
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_rx_done_cycles = k_cycle_get_32();
#endif
    ss_twr_init_frame_seq_nb++;
    ss_twr_init_alt_mark_scheduled_poll_timing(poll_tx_done_cycles,
                                               poll_count);
    ss_twr_init_alt_publish_rx_gap_diag(poll_tx_done_cycles,
                                        rx_enable_start_cycles,
                                        rx_enable_done_cycles,
                                        response_window_us, poll_count,
                                        anchor_mask, rxenable_rc);
#else
    response_window_start_cycles = k_cycle_get_32();
    ss_twr_init_alt_rx_restart(response_window_us);

    while (responses < poll_count) {
        uint32_t status_reg;
        do {
            status_reg = dwt_read32bitreg(SYS_STATUS_ID);
            if ((uint32_t)(k_cycle_get_32() - response_window_start_cycles) >=
                response_window_cycles) {
                break;
            }
        } while ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_TO |
                                SYS_STATUS_ALL_RX_ERR)) == 0U);
        last_status_reg = status_reg;
        last_rx_finfo = dwt_read32bitreg(RX_FINFO_ID);

        if ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_TO |
                           SYS_STATUS_ALL_RX_ERR)) == 0U) {
            dwt_forcetrxoff();
            break;
        }

        if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
            uint32 frame_len;
            uint16_t resp_src_addr;
            uint8_t anchor_id;
            uint32 resp_rx_ts;
            uint8_t resp_rx_ts40[5];
            uint32 poll_rx_ts;
            uint32 resp_tx_ts;
            int32_t carrier_integrator;
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U && \
    APP_TAG_RF_DIAG_TAG_RX_ENABLE != 0U
            dwt_rxdiag_t tag_resp_diag = {0};
#endif

            frame_len = last_rx_finfo & RX_FINFO_RXFLEN_MASK;
            last_frame_len = frame_len;
            if (frame_len > sizeof(ss_twr_init_rx_buffer)) {
                dwt_write32bitreg(SYS_STATUS_ID,
                                  SYS_STATUS_ALL_RX_GOOD |
                                      SYS_STATUS_ALL_RX_ERR |
                                      SYS_STATUS_ALL_RX_TO);
                dwt_forcetrxoff();
                dwt_rxreset();
                break;
            }

            dwt_readrxdata(ss_twr_init_rx_buffer, (uint16)frame_len, 0);
            dwt_readrxtimestamp(resp_rx_ts40);
            resp_rx_ts = (uint32_t)bsl_ts40_get(resp_rx_ts40);
            if (ss_twr_init_consume_beacon(
                    ss_twr_init_rx_buffer, frame_len,
                    bsl_ts40_get(resp_rx_ts40)) != 0) {
                dwt_write32bitreg(SYS_STATUS_ID,
                                  SYS_STATUS_ALL_RX_GOOD |
                                      SYS_STATUS_ALL_RX_ERR |
                                      SYS_STATUS_ALL_RX_TO);
#if APP_ALT_SS_TWR_MODE != APP_ALT_SS_TWR_MODE_BROADCAST
                ss_twr_init_alt_rx_restart(response_window_us);
#endif
                continue;
            }
            resp_src_addr = uwb_frame_get_src_addr(ss_twr_init_rx_buffer);
            last_src_addr = resp_src_addr;
            last_dst_addr = uwb_frame_get_dst_addr(ss_twr_init_rx_buffer);
            last_code = frame_len > UWB_MSG_CODE_IDX ?
                        ss_twr_init_rx_buffer[UWB_MSG_CODE_IDX] : 0U;
            if (!uwb_short_addr_is_anchor(resp_src_addr) ||
                !uwb_ss_twr_resp_matches(ss_twr_init_rx_buffer,
                                         ss_twr_init_local_addr,
                                         resp_src_addr)) {
                unexpected_count++;
                dwt_write32bitreg(SYS_STATUS_ID,
                                  SYS_STATUS_ALL_RX_GOOD |
                                      SYS_STATUS_ALL_RX_ERR |
                                      SYS_STATUS_ALL_RX_TO);
#if APP_ALT_SS_TWR_MODE != APP_ALT_SS_TWR_MODE_BROADCAST
                ss_twr_init_alt_rx_restart(response_window_us);
#endif
                continue;
            }
            anchor_id = uwb_anchor_id_from_addr(resp_src_addr);
            if (anchor_id >= UWB_MAX_ANCHORS || received[anchor_id] ||
                poll_tx_ts[anchor_id] == 0U) {
                unexpected_count++;
                dwt_write32bitreg(SYS_STATUS_ID,
                                  SYS_STATUS_ALL_RX_GOOD |
                                      SYS_STATUS_ALL_RX_ERR |
                                      SYS_STATUS_ALL_RX_TO);
#if APP_ALT_SS_TWR_MODE != APP_ALT_SS_TWR_MODE_BROADCAST
                ss_twr_init_alt_rx_restart(response_window_us);
#endif
                continue;
            }

            carrier_integrator = dwt_readcarrierintegrator();
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U && \
    APP_TAG_RF_DIAG_TAG_RX_ENABLE != 0U
            if (ss_twr_init_rf_diag_runtime_on) {
                dwt_readdiagnostics(&tag_resp_diag);
            }
#endif
            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX],
                &poll_rx_ts);
            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX],
                &resp_tx_ts);

            resp_rx_ts_by_anchor[anchor_id] = resp_rx_ts;
            poll_rx_ts_by_anchor[anchor_id] = poll_rx_ts;
            resp_tx_ts_by_anchor[anchor_id] = resp_tx_ts;
            carrier_integrator_by_anchor[anchor_id] = carrier_integrator;
            memcpy(ss_twr_init_bsl_resp_rx_ts[anchor_id], resp_rx_ts40,
                   sizeof(resp_rx_ts40));
            ss_twr_init_bsl_resp_rx_valid[anchor_id] = true;
            ss_twr_init_bsl_carrier_integrator[anchor_id] =
                carrier_integrator;
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U
            if (ss_twr_init_rf_diag_runtime_on) {
#if APP_TAG_RF_DIAG_TAG_RX_ENABLE != 0U
                ss_twr_init_rf_diag_from_rxdiag(
                    &tag_resp_diag_by_anchor[anchor_id], &tag_resp_diag);
#endif
                (void)ss_twr_init_parse_resp_diag_v2(
                    ss_twr_init_rx_buffer, frame_len,
                    &anchor_poll_diag_by_anchor[anchor_id]);
                ss_twr_init_sweep_tag_resp_diag[anchor_id] =
                    tag_resp_diag_by_anchor[anchor_id];
                ss_twr_init_sweep_anchor_poll_diag[anchor_id] =
                    anchor_poll_diag_by_anchor[anchor_id];
                ss_twr_init_sweep_rf_diag_mask |= BIT(anchor_id);
            }
#endif
            if ((cir_mode == UWB_TAG_CIR_MODE_COMPACT &&
                 ss_twr_init_compact_cir_sample_due()) ||
                cir_mode == UWB_TAG_CIR_MODE_FULL) {
                cir_diag_anchor_id = anchor_id;
                cir_diag_pending = true;
            }
            received[anchor_id] = true;
            responses++;
            dwt_write32bitreg(SYS_STATUS_ID,
                              SYS_STATUS_ALL_RX_GOOD | SYS_STATUS_ALL_RX_ERR |
                                  SYS_STATUS_ALL_RX_TO);
#if APP_ALT_SS_TWR_MODE != APP_ALT_SS_TWR_MODE_BROADCAST
            if (responses < poll_count) {
                ss_twr_init_alt_rx_restart(response_window_us);
            }
#endif
            continue;
        }

        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_TO |
                                             SYS_STATUS_ALL_RX_ERR);
        dwt_rxreset();
        break;
    }
#endif

    if (cir_diag_pending) {
        dwt_readdiagnostics(&cir_rx_diag);
        cir_diag_valid = true;
    }
    dwt_forcetrxoff();

    ss_twr_init_alt_publish_rx_diag(last_status_reg, last_rx_finfo,
                                    response_window_us, poll_count, anchor_mask,
                                    responses, unexpected_count, last_frame_len,
                                    last_src_addr, last_dst_addr, last_code);

#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    if (ss_twr_init_diag_rx_done_cycles == 0U) {
        ss_twr_init_diag_rx_done_cycles = k_cycle_get_32();
    }
#endif
    for (uint8_t i = 0U; i < poll_count; ++i) {
        uint8_t anchor_id = ss_twr_init_active_anchor_ids[i];
        if (received[anchor_id]) {
            int32 rtd_init =
                (int32)(resp_rx_ts_by_anchor[anchor_id] - poll_tx_ts[anchor_id]);
            int32 rtd_resp =
                (int32)(resp_tx_ts_by_anchor[anchor_id] -
                        poll_rx_ts_by_anchor[anchor_id]);
            double clock_offset_ratio =
                (double)carrier_integrator_by_anchor[anchor_id] *
                (FREQ_OFFSET_MULTIPLIER * HERTZ_TO_PPM_MULTIPLIER_CHAN_5 /
                 1.0e6);
            double tof =
                ((rtd_init - rtd_resp * (1.0 - clock_offset_ratio)) / 2.0) *
                DWT_TIME_UNITS;
            double distance_m = tof * SS_TWR_INIT_SPEED_OF_LIGHT;
            raw_distance_mm[anchor_id] = (long)(distance_m * 1000.0);
            if (raw_distance_mm[anchor_id] < 0L) {
                raw_distance_mm[anchor_id] = 0L;
            }
            if (cir_diag_valid && anchor_id == cir_diag_anchor_id &&
                cir_mode == UWB_TAG_CIR_MODE_COMPACT) {
                ss_twr_init_publish_cir_features(
                    anchor_id, raw_distance_mm[anchor_id],
                    resp_rx_ts_by_anchor[anchor_id],
                    carrier_integrator_by_anchor[anchor_id],
                    &cir_rx_diag);
            } else if (cir_diag_valid && anchor_id == cir_diag_anchor_id &&
                       cir_mode == UWB_TAG_CIR_MODE_FULL) {
                ss_twr_init_publish_full_cir(
                    anchor_id, raw_distance_mm[anchor_id],
                    resp_rx_ts_by_anchor[anchor_id],
                    carrier_integrator_by_anchor[anchor_id],
                    &cir_rx_diag);
            }
#if APP_TAG_RF_DIAG_OUTPUT_ENABLE != 0U && \
    APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE != 0U
            if (ss_twr_init_rf_diag_runtime_on) {
                ss_twr_init_publish_rf_diag(
                    rf_diag_poll_seq,
                    anchor_id, raw_distance_mm[anchor_id],
                    resp_rx_ts_by_anchor[anchor_id],
                    carrier_integrator_by_anchor[anchor_id],
                    &anchor_poll_diag_by_anchor[anchor_id],
                    &tag_resp_diag_by_anchor[anchor_id]);
            }
#endif
            ss_twr_init_alt_record_range(anchor_id, raw_distance_mm[anchor_id]);
        } else {
            ss_twr_init_alt_record_timeout(anchor_id,
                                           SS_TWR_INIT_CAL_REASON_RX_TIMEOUT);
        }
    }
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
    ss_twr_init_diag_range_done_cycles = k_cycle_get_32();
#endif

    dwt_forcetrxoff();
#if APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
    ss_twr_init_alt_set_rx_auto_reenable(false);
#if SS_TWR_INIT_BCAST_RXDBLBUF_ENABLE != 0U
    dwt_setdblrxbuffmode(0);
#endif
#endif
    ss_twr_init_active_anchor_index = 0U;
    ss_twr_init_current_anchor_retry_count = 0U;
    ss_twr_init_bsl_response_count = responses;
    ss_twr_init_alt_finish_sweep();
    return true;
}
#endif /* APP_ALT_SS_TWR_ENABLE */

int ss_twr_init_start_with_config(const struct uwb_tag_runtime_config *config)
{
    if (ss_twr_init_load_runtime_config(config) != 0) {
        printk("Invalid SS-TWR initiator runtime config\n");
        return -1;
    }

    printk("SS-TWR initiator ready tag=%u addr=0x%04x anchor_count=%u\n",
           (unsigned int)ss_twr_init_local_tag_id,
           (unsigned int)ss_twr_init_local_addr,
           (unsigned int)ss_twr_init_anchor_count);
    printk("Tag runtime rng_delay_ms=%u settle_us=%u tx_to_rx_uus=%u resp_timeout_uus=%u range_plan=all_configured tdma=%u slot=%u/%u period=%u active=%u source=%s epoch_valid=%u gen=%u\n",
		           (unsigned int)SS_TWR_INIT_RNG_DELAY_MS,
		           (unsigned int)SS_TWR_INIT_CAL_RNG_SETTLE_US,
		           (unsigned int)SS_TWR_INIT_TX_TO_RX_DLY_UUS,
	           (unsigned int)SS_TWR_INIT_RESP_RX_TIMEOUT_UUS,
	           (unsigned int)ss_twr_init_tdma_schedule.enabled,
           (unsigned int)ss_twr_init_tdma_schedule.slot_index,
           (unsigned int)ss_twr_init_tdma_schedule.slot_count,
           (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
           (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms,
           ss_twr_init_slot_source_label(ss_twr_init_runtime_params.slot_source),
           (unsigned int)ss_twr_init_tdma_schedule.epoch_valid,
           (unsigned int)ss_twr_init_tdma_schedule.generation);
    printk("Tag multitag plan enabled=%u active=%u standby=%u reserve=%u refresh_budget=%u refresh_interval=%u maintenance_full=%u\n",
           (unsigned int)ss_twr_init_multitag_anchor_plan_mode,
           (unsigned int)ss_twr_init_active_plan_count,
           (unsigned int)ss_twr_init_standby_plan_count,
           (unsigned int)ss_twr_init_reserve_plan_count,
           (unsigned int)ss_twr_init_refresh_anchor_budget,
           (unsigned int)ss_twr_init_refresh_interval_sweeps,
           (unsigned int)ss_twr_init_full_sweep_interval_sweeps);
    printk("Tag output config summary_period=%u tr_ver=%u normal_out=%u cir_feature=%u range=instantaneous_unsmoothed\n",
           (unsigned int)APP_TAG_SUMMARY_PERIOD,
           (unsigned int)SS_TWR_INIT_TR_RANGE_VERSION,
           (unsigned int)APP_TAG_NORMAL_OUTPUT_ENABLE,
           (unsigned int)APP_TAG_CIR_FEATURE_OUTPUT_ENABLE);
    printk("SS-TWR init trace: waiting for TDMA slot\n");
#if APP_TAG_USB_DIAG_TRACE
    ss_twr_diag_write("SS-TWR: wait_tdma enter\n");
#endif
    ss_twr_init_configure_radio();
	{
	    uint32_t tdma_wait_ms = ss_twr_init_wait_until_slot_if_needed();
	    ss_twr_init_last_tdma_wait_ms = tdma_wait_ms;
#if APP_TAG_USB_DIAG_TRACE
        ss_twr_diag_write("SS-TWR: wait_tdma done\n");
#endif
	    printk("SS-TWR init trace: TDMA wait complete wait_ms=%lu slot=%u/%u period=%u active=%u cal_mode=%u\n",
	           (unsigned long)tdma_wait_ms,
	           (unsigned int)ss_twr_init_tdma_schedule.slot_index,
	           (unsigned int)ss_twr_init_tdma_schedule.slot_count,
	           (unsigned int)ss_twr_init_tdma_schedule.slot_period_ms,
	           (unsigned int)ss_twr_init_tdma_schedule.slot_active_ms,
	           (unsigned int)ss_twr_init_runtime_any_calibration_mode());
	}
    ss_twr_init_prepare_sweep_plan();
    printk("SS-TWR init trace: sweep plan prepared active=%u sweep=%lu full=%u refresh=%u plan=%s\n",
           (unsigned int)ss_twr_init_active_anchor_count,
           (unsigned long)ss_twr_init_sweep_count,
           (unsigned int)ss_twr_init_current_sweep_full,
           (unsigned int)ss_twr_init_current_sweep_refresh,
           ss_twr_init_plan_label());
#if APP_TAG_USB_DIAG_TRACE
    ss_twr_diag_write("SS-TWR: sweep plan ready\n");
#endif
    printk("SS-TWR init trace: main loop enter\n");
#if APP_TAG_USB_DIAG_TRACE
    ss_twr_diag_write("SS-TWR: main loop enter\n");
#endif

    while (1) {
#if APP_TAG_BLE_ENABLE
        if (uwb_tag_ble_ota_active()) {
            ss_twr_init_publish_tdma_diag("ota_active", 0U, 0U);
            ss_twr_init_set_ble_tx_paused(false);
            dwt_forcetrxoff();
            k_msleep(20);
            continue;
        }
#endif
	ss_twr_init_apply_pending_runtime_config_if_any();

	if (ss_twr_init_runtime_idle_mode()) {
	    ss_twr_init_publish_tdma_diag("idle", 0U, 0U);
	    ss_twr_init_set_ble_tx_paused(false);
	    dwt_forcetrxoff();
	    k_msleep(20);
	    continue;
	}

	if (tag_run_state_holds_radio(&ss_twr_init_runtime_params)) {
	    ss_twr_init_publish_tdma_diag("cfg_stopped", 0U, 0U);
	    ss_twr_init_set_ble_tx_paused(false);
	    dwt_forcetrxoff();
	    k_msleep(20);
	    continue;
	}

	if (ss_twr_init_active_anchor_index == 0U) {
	    ss_twr_init_beacon_listen_if_needed();
	}

#if APP_ALT_SS_TWR_ENABLE && \
    APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST && \
    APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE != 0U
        if (ss_twr_init_active_anchor_index == 0U &&
            ss_twr_init_active_anchor_count > 1U) {
			struct broadcast_tdma_wait_stats wait_stats;

#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
            ss_twr_init_diag_t0_cycles = k_cycle_get_32();
            ss_twr_init_diag_wait_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_tx_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_rx_start_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_rx_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_range_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_solve_start_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_solve_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_out_start_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_out_done_cycles = ss_twr_init_diag_t0_cycles;
            ss_twr_init_diag_clean_done_cycles = ss_twr_init_diag_t0_cycles;
#endif
            (void)ss_twr_init_alt_bcast_prewrite_tx();
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
            if (ss_twr_init_dw_anchor_can_schedule()) {
                memset(&wait_stats, 0, sizeof(wait_stats));
                ss_twr_init_alt_ltdma_slot_start_cycles =
                    k_cycle_get_32();
            } else
#endif
            {
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
                ss_twr_init_dw_anchor_note_fallback();
#endif
                ss_twr_init_alt_ltdma_slot_start_cycles =
                    broadcast_tdma_wait_next_slot_start(
                        &ss_twr_init_tdma_schedule, &wait_stats);
                atomic_add(&ss_twr_init_slot_sleep_late_skips,
                           (atomic_val_t)wait_stats.sleep_late_skips);
                atomic_add(&ss_twr_init_slot_spin_late_skips,
                           (atomic_val_t)wait_stats.spin_late_skips);
            }
#if APP_TAG_SWEEP_DIAG_ENABLE != 0U
            ss_twr_init_diag_wait_done_cycles =
                ss_twr_init_alt_ltdma_slot_start_cycles;
#endif

            if (ss_twr_init_alt_burst_sweep_once()) {
                continue;
            }
        }
#endif

        if (!ss_twr_init_tdma_exchange_can_start_if_needed()) {
            uint32_t remain_ms =
                uwb_tdma_schedule_time_remaining_ms(&ss_twr_init_tdma_schedule);

            ss_twr_init_last_sweep_cut_short = true;
            ss_twr_init_last_solve_reason = SS_TWR_INIT_SOLVE_SLOT_CUT_SHORT;
            ss_twr_init_publish_tdma_diag("slot_guard", remain_ms,
                                          SS_TWR_INIT_SLOT_EXCHANGE_BUDGET_MS +
                                              SS_TWR_INIT_SLOT_GUARD_MARGIN_MS);
            {
                uint32_t now_ms = (uint32_t)k_uptime_get();
                if ((now_ms - ss_twr_init_last_slot_guard_log_ms) >= 1000U) {
                    ss_twr_init_last_slot_guard_log_ms = now_ms;
                    printk("Tag slot guard: cut short plan=%s next_anchor=%u active=%u remain=%lu ms slot=%u/%u gen=%u\n",
                           ss_twr_init_plan_label(),
                           (unsigned int)ss_twr_init_active_anchor_index,
                           (unsigned int)ss_twr_init_active_anchor_count,
                           (unsigned long)remain_ms,
                           (unsigned int)ss_twr_init_tdma_schedule.slot_index,
                           (unsigned int)ss_twr_init_tdma_schedule.slot_count,
                           (unsigned int)ss_twr_init_tdma_schedule.generation);
                }
            }
            ss_twr_init_sweep_count++;
            ss_twr_init_release_ble_tx_after_active_slot();
            ss_twr_init_note_sweep_done();
            ss_twr_init_publish_ranges_if_ready();
            ss_twr_init_apply_pending_runtime_config_if_any();
	        ss_twr_init_last_tdma_wait_ms = ss_twr_init_wait_until_next_slot_if_needed();
            ss_twr_init_prepare_sweep_plan();
            continue;
        }

	        if (ss_twr_init_active_anchor_index == 0U &&
	            ss_twr_init_active_anchor_count > 1U &&
	            ss_twr_init_tdma_schedule.enabled &&
	            ss_twr_init_tdma_active_guard_enabled()) {
            uint32_t remain_ms =
                uwb_tdma_schedule_time_remaining_ms(&ss_twr_init_tdma_schedule);
            uint32_t sweep_budget_ms =
                ((uint32_t)ss_twr_init_active_anchor_count *
                 SS_TWR_INIT_SLOT_EXCHANGE_BUDGET_MS) +
                SS_TWR_INIT_SLOT_GUARD_MARGIN_MS;

#if APP_ALT_SS_TWR_ENABLE && APP_ALT_SS_TWR_MODE == APP_ALT_SS_TWR_MODE_BROADCAST
            sweep_budget_ms =
                (ss_twr_init_alt_bcast_response_window_estimated_us(
                     ss_twr_init_active_anchor_count) + 999U) /
                1000U;
            remain_ms = ss_twr_init_tdma_period_remaining_ms();
#endif

            if (remain_ms < sweep_budget_ms) {
                ss_twr_init_publish_tdma_diag("sweep_budget", remain_ms,
                                              sweep_budget_ms);
                dwt_forcetrxoff();
                ss_twr_init_release_ble_tx_after_active_slot();
                ss_twr_init_last_tdma_wait_ms =
                    ss_twr_init_wait_until_next_slot_if_needed();
                continue;
            }
        }

#if APP_ALT_SS_TWR_ENABLE
        if (ss_twr_init_active_anchor_index == 0U &&
            ss_twr_init_active_anchor_count > 1U) {
            ss_twr_init_publish_tdma_diag("alt_burst",
                                          uwb_tdma_schedule_time_remaining_ms(
                                              &ss_twr_init_tdma_schedule),
                                          0U);
            if (ss_twr_init_alt_burst_sweep_once()) {
                continue;
            }
        }
#endif

        ss_twr_init_set_ble_tx_paused(true);

        uint8_t current_anchor_id =
            ss_twr_init_active_anchor_ids[ss_twr_init_active_anchor_index];
        uint16_t current_anchor_addr = uwb_anchor_short_addr(current_anchor_id);
        struct uwb_range_tracker *tracker =
            &ss_twr_init_trackers[current_anchor_id];
        uint32 status_reg;

        uwb_ss_twr_build_poll_frame(ss_twr_init_tx_poll_msg,
                                    ss_twr_init_frame_seq_nb, current_anchor_addr,
                                    ss_twr_init_local_addr);

	        ss_twr_init_prepare_radio_for_poll();

        if (ss_twr_init_sweep_count == 0U &&
            ss_twr_init_active_anchor_index == 0U) {
#if APP_TAG_USB_DIAG_TRACE
            ss_twr_diag_write("SS-TWR: first poll tx prepare\n");
#endif
        }
        if (dwt_writetxdata(SS_TWR_INIT_LEGACY_POLL_FRAME_LEN,
                            ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
            printk("Initiator TX buffer write failed\n");
            ss_twr_init_sleep_between_ranges();
            continue;
        }

        dwt_writetxfctrl(SS_TWR_INIT_LEGACY_POLL_FRAME_LEN, 0, 1);

        int poll_tx_rc =
            dwt_starttx(DWT_START_TX_IMMEDIATE | DWT_RESPONSE_EXPECTED);

        if (poll_tx_rc != DWT_SUCCESS) {
            ss_twr_init_record_poll_tx_failure(poll_tx_rc);
            printk("Initiator TX start failed\n");
            dwt_forcetrxoff();
            ss_twr_init_sleep_between_ranges();
            continue;
        }
        ss_twr_init_note_poll_started();

        if (ss_twr_init_sweep_count == 0U &&
            ss_twr_init_active_anchor_index == 0U) {
#if APP_TAG_USB_DIAG_TRACE
            ss_twr_diag_write("SS-TWR: first poll tx started\n");
#endif
        }

        do {
            status_reg = dwt_read32bitreg(SYS_STATUS_ID);
        } while ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_TO |
                                SYS_STATUS_ALL_RX_ERR)) == 0U);

        if (ss_twr_init_sweep_count == 0U &&
            ss_twr_init_active_anchor_index == 0U) {
#if APP_TAG_USB_DIAG_TRACE
            ss_twr_diag_write("SS-TWR: first poll rx status\n");
#endif
            printk("SS-TWR init trace: first poll status=0x%08lx\n",
                   (unsigned long)status_reg);
        }

        ss_twr_init_frame_seq_nb++;

        if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
            uint32 frame_len;
            uint16_t resp_src_addr;
            uint32 poll_tx_ts;
            uint32 resp_rx_ts;
            uint32 poll_rx_ts;
            uint32 resp_tx_ts;
            int32 rtd_init;
            int32 rtd_resp;
            double tof;
            double distance_m;
            double clock_offset_ratio;
            int32_t carrier_integrator;
            dwt_rxdiag_t rx_diag;
            bool rx_diag_valid = false;
            long raw_distance_mm;
            uint32 range_mm;

            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);

            frame_len = dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFLEN_MASK;
            if (frame_len > sizeof(ss_twr_init_rx_buffer)) {
                printk("Initiator RX frame too long: %lu status=0x%08lx\n",
                       (unsigned long)frame_len, (unsigned long)status_reg);
                printk("RX_FINFO raw=0x%08lx\n",
                       (unsigned long)dwt_read32bitreg(RX_FINFO_ID));
                dwt_forcetrxoff();
                dwt_rxreset();
                ss_twr_init_sleep_between_ranges();
                continue;
            }

            memset(ss_twr_init_rx_buffer, 0, sizeof(ss_twr_init_rx_buffer));
            dwt_readrxdata(ss_twr_init_rx_buffer, (uint16)frame_len, 0);
            {
                uint8_t resp_rx_ts40[5];

                dwt_readrxtimestamp(resp_rx_ts40);
                if (ss_twr_init_consume_beacon(
                        ss_twr_init_rx_buffer, frame_len,
                        bsl_ts40_get(resp_rx_ts40)) != 0) {
                    dwt_write32bitreg(SYS_STATUS_ID,
                                      SYS_STATUS_ALL_RX_GOOD |
                                          SYS_STATUS_ALL_RX_ERR |
                                          SYS_STATUS_ALL_RX_TO);
                    dwt_forcetrxoff();
                    ss_twr_init_sleep_between_ranges();
                    continue;
                }
            }
            ss_twr_init_rx_buffer[SS_TWR_INIT_MSG_SN_IDX] = 0;

            if (!uwb_ss_twr_resp_matches(ss_twr_init_rx_buffer,
                                         ss_twr_init_local_addr, current_anchor_addr)) {
#if APP_TAG_VERBOSE_MEASUREMENTS
                printk("Initiator got unexpected frame src=0x%04x dst=0x%04x code=0x%02x\n",
                       (unsigned int)uwb_frame_get_src_addr(ss_twr_init_rx_buffer),
                       (unsigned int)uwb_frame_get_dst_addr(ss_twr_init_rx_buffer),
                       (unsigned int)ss_twr_init_rx_buffer[UWB_MSG_CODE_IDX]);
#endif
                if (ss_twr_init_sweep_count == 0U &&
                    ss_twr_init_active_anchor_index == 0U) {
#if APP_TAG_USB_DIAG_TRACE
                    ss_twr_diag_write("SS-TWR: first poll unexpected frame\n");
#endif
                    printk("SS-TWR init trace: first poll unexpected frame src=0x%04x dst=0x%04x code=0x%02x\n",
                           (unsigned int)uwb_frame_get_src_addr(ss_twr_init_rx_buffer),
                           (unsigned int)uwb_frame_get_dst_addr(ss_twr_init_rx_buffer),
                           (unsigned int)ss_twr_init_rx_buffer[UWB_MSG_CODE_IDX]);
#if APP_TAG_USB_DIAG_TRACE
                    {
                        char buf[96];
                        snprintk(buf, sizeof(buf),
                                 "SS-TWR: first poll frame src=0x%04x dst=0x%04x code=0x%02x\n",
                                 (unsigned int)uwb_frame_get_src_addr(
                                     ss_twr_init_rx_buffer),
                                 (unsigned int)uwb_frame_get_dst_addr(
                                     ss_twr_init_rx_buffer),
                                 (unsigned int)ss_twr_init_rx_buffer
                                     [UWB_MSG_CODE_IDX]);
                        ss_twr_diag_write(buf);
                    }
#endif
                }
                ss_twr_init_sleep_between_ranges();
                continue;
            }

            poll_tx_ts = dwt_readtxtimestamplo32();
            resp_rx_ts = dwt_readrxtimestamplo32();
            carrier_integrator = dwt_readcarrierintegrator();
            if (ss_twr_init_cir_compact_enabled() ||
                ss_twr_init_cir_full_enabled()) {
                dwt_readdiagnostics(&rx_diag);
                rx_diag_valid = true;
            }
            clock_offset_ratio =
                (double)carrier_integrator *
                (FREQ_OFFSET_MULTIPLIER * HERTZ_TO_PPM_MULTIPLIER_CHAN_5 /
                 1.0e6);

            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX],
                &poll_rx_ts);
            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX],
                &resp_tx_ts);

            rtd_init = (int32)(resp_rx_ts - poll_tx_ts);
            rtd_resp = (int32)(resp_tx_ts - poll_rx_ts);
            tof = ((rtd_init - rtd_resp * (1.0 - clock_offset_ratio)) / 2.0) *
                  DWT_TIME_UNITS;
            distance_m = tof * SS_TWR_INIT_SPEED_OF_LIGHT;
            raw_distance_mm = (long)(distance_m * 1000.0);
            if (raw_distance_mm < 0L) {
                raw_distance_mm = 0L;
            }
            if (rx_diag_valid && ss_twr_init_cir_compact_enabled()) {
                ss_twr_init_publish_cir_features(current_anchor_id,
                                                 raw_distance_mm,
                                                 resp_rx_ts,
                                                 carrier_integrator,
                                                 &rx_diag);
            }
#if APP_TAG_CIR_FULL_OUTPUT_ENABLE != 0U
            if (rx_diag_valid &&
                ss_twr_init_cir_full_should_publish_unicast(current_anchor_id)) {
                ss_twr_init_publish_full_cir(current_anchor_id, raw_distance_mm,
                                             resp_rx_ts, carrier_integrator,
                                             &rx_diag);
            }
#endif

            if (tracker == NULL) {
                ss_twr_init_sleep_between_ranges();
                continue;
            }

            if (!ss_twr_init_range_measurement_valid(
                    (uint32_t)raw_distance_mm)) {
                uwb_range_tracker_record_failure(tracker);
                ss_twr_init_record_sweep_anchor_state(
                    current_anchor_id, UWB_TAG_BLE_CAL_STATUS_REJECT, tracker);
                ss_twr_init_record_sweep_anchor_diag(
                    current_anchor_id, SS_TWR_INIT_CAL_REASON_RANGE_INVALID,
                    raw_distance_mm, tracker->range_mm, 0U, 0U,
                    uwb_range_tracker_quality_percent(tracker));
#if APP_TAG_BLE_ENABLE
                ss_twr_init_publish_cal_range(current_anchor_id,
                                              UWB_TAG_BLE_CAL_STATUS_REJECT,
                                              raw_distance_mm,
                                              tracker->range_mm,
                                              tracker);
#endif
                if (APP_TAG_VERBOSE_RANGING != 0U) {
                    printk("Initiator range reject anchor=%u addr=0x%04x raw=%ld mm last_range=%lu mm ok=%lu fail=%lu q=%u%%\n",
                           (unsigned int)current_anchor_id,
                           (unsigned int)current_anchor_addr,
                           raw_distance_mm,
                           (unsigned long)tracker->range_mm,
                           (unsigned long)tracker->success_count,
                           (unsigned long)tracker->failure_count,
                           (unsigned int)uwb_range_tracker_quality_percent(
                               tracker));
	                }
	                ss_twr_init_sleep_between_ranges();
                continue;
            }

            range_mm = uwb_range_tracker_record_success(
                tracker, (uint32_t)raw_distance_mm);
            ss_twr_init_record_sweep_anchor_state(
                current_anchor_id, UWB_TAG_BLE_CAL_STATUS_OK, tracker);
            ss_twr_init_record_sweep_anchor_diag(
                current_anchor_id, SS_TWR_INIT_CAL_REASON_OK, raw_distance_mm,
                range_mm, 0U, 0U, uwb_range_tracker_quality_percent(tracker));
            resp_src_addr = uwb_frame_get_src_addr(ss_twr_init_rx_buffer);

            if (APP_TAG_VERBOSE_RANGING != 0U) {
                printk("Range anchor=%u addr=0x%04x raw=%ld mm range=%lu mm ok=%lu fail=%lu q=%u%%\n",
                       (unsigned int)uwb_anchor_id_from_addr(resp_src_addr),
                       (unsigned int)resp_src_addr, raw_distance_mm,
                       (unsigned long)range_mm,
                       (unsigned long)tracker->success_count,
                       (unsigned long)tracker->failure_count,
                       (unsigned int)uwb_range_tracker_quality_percent(
                           tracker));
            }
#if APP_TAG_BLE_ENABLE
            ss_twr_init_publish_cal_range(current_anchor_id,
                                          UWB_TAG_BLE_CAL_STATUS_OK,
                                          raw_distance_mm,
                                          range_mm,
                                          tracker);
#endif
        } else {
            if (tracker != NULL) {
                uint8_t timeout_reason =
                    ((status_reg & SYS_STATUS_ALL_RX_TO) != 0U)
                        ? SS_TWR_INIT_CAL_REASON_RX_TIMEOUT
                        : SS_TWR_INIT_CAL_REASON_RX_ERROR;

                if (ss_twr_init_should_retry_current_cal_anchor()) {
                    ss_twr_init_current_anchor_retry_count++;
                    dwt_write32bitreg(SYS_STATUS_ID,
                                      SYS_STATUS_ALL_RX_TO | SYS_STATUS_ALL_RX_ERR);
	                    dwt_rxreset();
	                    if (!ss_twr_init_tdma_exchange_can_start_if_needed()) {
	                        dwt_forcetrxoff();
	                        ss_twr_init_release_ble_tx_after_active_slot();
	                        ss_twr_init_last_tdma_wait_ms =
	                            ss_twr_init_wait_until_next_slot_if_needed();
	                    }
                    continue;
                }

                uwb_range_tracker_record_failure(tracker);
                ss_twr_init_record_sweep_anchor_state(
                    current_anchor_id, UWB_TAG_BLE_CAL_STATUS_TIMEOUT, tracker);
                ss_twr_init_record_sweep_anchor_diag(
                    current_anchor_id, timeout_reason, 0, tracker->range_mm,
                    0U, 0U, uwb_range_tracker_quality_percent(tracker));
#if APP_TAG_BLE_ENABLE
                ss_twr_init_publish_cal_range(current_anchor_id,
                                              UWB_TAG_BLE_CAL_STATUS_TIMEOUT,
                                              0,
                                              tracker->range_mm,
                                              tracker);
#endif
                if (APP_TAG_VERBOSE_RANGING != 0U) {
                    printk("Initiator RX timeout/error anchor=%u addr=0x%04x status=0x%08lx ok=%lu fail=%lu q=%u%%\n",
                           (unsigned int)current_anchor_id,
                           (unsigned int)current_anchor_addr,
                           (unsigned long)status_reg,
                           (unsigned long)tracker->success_count,
                           (unsigned long)tracker->failure_count,
                           (unsigned int)uwb_range_tracker_quality_percent(
                               tracker));
                }
                if (ss_twr_init_sweep_count == 0U &&
                    ss_twr_init_active_anchor_index == 0U) {
#if APP_TAG_USB_DIAG_TRACE
                    ss_twr_diag_write("SS-TWR: first poll timeout/error\n");
#endif
                    printk("SS-TWR init trace: first poll timeout/error anchor=%u addr=0x%04x status=0x%08lx\n",
                           (unsigned int)current_anchor_id,
                           (unsigned int)current_anchor_addr,
                           (unsigned long)status_reg);
                }
            }
            dwt_write32bitreg(SYS_STATUS_ID,
                              SYS_STATUS_ALL_RX_TO | SYS_STATUS_ALL_RX_ERR);
            dwt_rxreset();
        }

        ss_twr_init_active_anchor_index =
            (ss_twr_init_active_anchor_index + 1U) %
            ss_twr_init_active_anchor_count;
	        ss_twr_init_current_anchor_retry_count = 0U;
	        if (ss_twr_init_active_anchor_index == 0U) {
	            ss_twr_init_sweep_count++;
	            ss_twr_init_release_ble_tx_after_active_slot();
	            ss_twr_init_note_sweep_done();
	            ss_twr_init_publish_ranges_if_ready();
	            ss_twr_init_apply_pending_runtime_config_if_any();
	            ss_twr_init_last_tdma_wait_ms =
	                ss_twr_init_wait_until_next_slot_if_needed();
	            ss_twr_init_prepare_sweep_plan();
	        }
        ss_twr_init_sleep_between_ranges();
    }
}

int ss_twr_init_start(unsigned int tag_id, const uint8_t *anchor_ids,
                      size_t anchor_count)
{
    const struct uwb_tag_runtime_config config = {
        .tag_id = (uint8_t)tag_id,
        .anchor_ids = anchor_ids,
        .anchor_count = anchor_count,
        .fixed_anchor_mode = false,
        .fixed_anchor_ids = NULL,
        .fixed_anchor_count = 0U,
        .multitag_anchor_plan_mode = false,
        .active_anchor_ids = NULL,
        .active_anchor_count = 0U,
        .standby_anchor_ids = NULL,
        .standby_anchor_count = 0U,
        .reserve_anchor_ids = NULL,
        .reserve_anchor_count = 0U,
        .refresh_anchor_budget = 0U,
        .refresh_interval_sweeps = 0U,
        .full_sweep_interval_sweeps = 0U,
        .beacon_sync = false,
        .beacon_win_n = TAG_BEACON_WINDOW_N_DEFAULT,
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
        .dw_anchor = false,
#endif
        .tdma =
            {
                .enabled = false,
                .slot_index = 0U,
                .slot_count = 1U,
                .slot_period_ms = 0U,
                .slot_active_ms = 0U,
            },
    };

    return ss_twr_init_start_with_config(&config);
}

int ss_twr_init_tdma_set_slot(uint8_t slot_index)
{
    struct uwb_tag_runtime_params params = ss_twr_init_runtime_params;

    if (!ss_twr_init_tdma_schedule.enabled ||
        ss_twr_init_tdma_schedule.slot_count == 0U) {
        return -EINVAL;
    }

    if (slot_index >= ss_twr_init_tdma_schedule.slot_count) {
        return -ERANGE;
    }

    params.slot_source = UWB_TAG_SLOT_SOURCE_SETTINGS;
    params.tdma.slot_index = slot_index;
    return ss_twr_init_runtime_configure(&params);
}

int ss_twr_init_runtime_configure(const struct uwb_tag_runtime_params *params)
{
	if (params == NULL) {
		return -EINVAL;
	}

	if (params->logical_tag_id >= UWB_MAX_TAGS) {
		return -ERANGE;
	}

	if (params->positioning_mode != UWB_TAG_POSITIONING_MODE_DYNAMIC &&
	    params->positioning_mode != UWB_TAG_POSITIONING_MODE_IDLE) {
		return -ERANGE;
	}
	if (params->beacon_win_n < TAG_BEACON_WINDOW_N_MIN ||
	    params->beacon_win_n > TAG_BEACON_WINDOW_N_MAX) {
		return -ERANGE;
	}

	if (params->tdma.enabled &&
	    !uwb_tdma_schedule_is_valid(&params->tdma)) {
		return -EINVAL;
	}
	if (params->beacon_sync &&
	    (!params->tdma.enabled || !params->tdma.epoch_valid ||
	     params->tdma.slot_index == 0U ||
	     !tag_beacon_tracking_window_precedes_slot(
		     params->tdma.slot_index,
		     params->tdma.slot_period_ms))) {
		return -EINVAL;
	}

	ss_twr_init_pending_runtime_params = *params;
	ss_twr_init_pending_runtime_params.beacon_win_n =
		TAG_BEACON_WINDOW_N_DEFAULT;
	ss_twr_init_pending_runtime_params.anchor_selection_mode =
		UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
	ss_twr_init_pending_runtime_params.fixed_anchor_count = 0U;
	memset(ss_twr_init_pending_runtime_params.fixed_anchor_ids, 0,
	       sizeof(ss_twr_init_pending_runtime_params.fixed_anchor_ids));
	if (ss_twr_init_pending_runtime_params.tdma.epoch_valid) {
		uwb_tdma_sync_schedule_epoch(&ss_twr_init_pending_runtime_params.tdma,
					     ss_twr_init_pending_runtime_params.tdma.epoch_ms,
					     ss_twr_init_pending_runtime_params.tdma.generation);
	}
	ss_twr_init_runtime_update_pending = true;
	return 0;
}

bool ss_twr_init_runtime_config_snapshot(struct uwb_tag_runtime_params *params)
{
	if (params == NULL) {
		return false;
	}

	*params = ss_twr_init_runtime_params;
	return true;
}
