#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <hal/nrf_gpio.h>
#include <hal/nrf_gpiote.h>
#include <hal/nrf_clock.h>
#include <hal/nrf_timer.h>
#include <nrfx_gpiote.h>
#include <nrfx_ppi.h>
#include <nrfx_timer.h>
#include <zephyr/kernel.h>
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/onoff.h>
#include <zephyr/sys/util.h>

#include "biospur_link.h"
#include "strobe_capture.h"
#include "strobe_timer_math.h"

LOG_MODULE_DECLARE(biospur_fusion);

#define STROBE_PIN NRF_GPIO_PIN_MAP(1, 3)
#define STROBE_TIMER_INSTANCE 2
#define STROBE_TIMER_HZ 1000000u
#define STROBE_TIMER_IRQ_PRIORITY 1u

#define TIMER_WRAP_CC NRF_TIMER_CC_CHANNEL0
#define RISING_CAPTURE_CC NRF_TIMER_CC_CHANNEL1
#define FALLING_CAPTURE_CC NRF_TIMER_CC_CHANNEL2
#define SOFTWARE_CAPTURE_CC NRF_TIMER_CC_CHANNEL3

#define EDGE_QUEUE_CAPACITY 32u
#define MAX_PULSE_WIDTH_US 500u
#define BOOT_EDGE_DISCARD_US 50000u
#define EXPECTED_FRAME_DELAY_US 10583u
#define WINDOW_EDGE_BAND_US 1000u

BUILD_ASSERT(BSF_CAPTURE_PAIR_WINDOW_US <= UINT16_MAX,
	     "pairing window must fit the BLE record");
BUILD_ASSERT(NRF_TIMER_CC_CHANNEL_COUNT(STROBE_TIMER_INSTANCE) >= 4,
	     "TIMER2 requires four capture/compare channels");

enum edge_kind {
	EDGE_RISING = 1,
	EDGE_FALLING = 2,
};

struct captured_edge {
	uint64_t timestamp_us;
	uint8_t kind;
};

struct captured_pulse {
	uint64_t strobe_timestamp_us;
	uint64_t rising_timestamp_us;
	uint64_t falling_timestamp_us;
	uint8_t edge_shape;
	uint8_t edge_count;
};

static const nrfx_timer_t strobe_timer =
	NRFX_TIMER_INSTANCE(STROBE_TIMER_INSTANCE);
static const nrfx_gpiote_t strobe_gpiote = NRFX_GPIOTE_INSTANCE(0);

static struct captured_edge edge_queue[EDGE_QUEUE_CAPACITY];
static struct captured_edge drained_edges[EDGE_QUEUE_CAPACITY];
static struct captured_pulse decoded_pulses[EDGE_QUEUE_CAPACITY];
static uint8_t edge_head;
static uint8_t edge_tail;
static uint8_t edge_count;
static struct k_spinlock capture_lock;

static atomic_t timer_wrap_count;
static atomic_t rising_edge_count;
static atomic_t falling_edge_count;
static atomic_t boot_discarded_edge_count;
static atomic_t edge_queue_drop_count;
static atomic_t orphan_strobe_count;
static atomic_t orphan_edge_count;
static atomic_t orphan_frame_count;
static atomic_t near_window_edge_count;

static uint64_t capture_started_us;
static uint64_t last_orphan_strobe_ts_us = BSF_CAPTURE_TS_ABSENT;
static uint8_t capture_flags;
static uint8_t rising_gpiote_channel;
static uint8_t falling_gpiote_channel;
static nrf_ppi_channel_t rising_ppi_channel;
static nrf_ppi_channel_t falling_ppi_channel;
static struct onoff_manager *hfclk_manager;
static struct onoff_client hfclk_client;
static bool hfxo_held;
static bool capture_active;

static int hfxo_hold(void)
{
	int request_result;
	int err;

	hfclk_manager = z_nrf_clock_control_get_onoff(
		CLOCK_CONTROL_NRF_SUBSYS_HF);
	if (hfclk_manager == NULL) {
		return -ENODEV;
	}

	sys_notify_init_spinwait(&hfclk_client.notify);
	err = onoff_request(hfclk_manager, &hfclk_client);
	if (err < 0) {
		return err;
	}

	while (sys_notify_fetch_result(&hfclk_client.notify,
				       &request_result) == -EAGAIN) {
		k_yield();
	}
	if (request_result < 0) {
		return request_result;
	}
	hfxo_held = true;

	if (!nrf_clock_hf_is_running(NRF_CLOCK,
				     NRF_CLOCK_HFCLK_HIGH_ACCURACY)) {
		(void)onoff_release(hfclk_manager);
		hfxo_held = false;
		return -EIO;
	}

	return 0;
}

static void hfxo_release(void)
{
	if (!hfxo_held) {
		return;
	}

	(void)onoff_release(hfclk_manager);
	hfxo_held = false;
}

static uint64_t timer_current_epoch(uint32_t current_low)
{
	atomic_val_t wraps_before;
	atomic_val_t wraps_after;
	bool wrap_pending;

	do {
		wraps_before = atomic_get(&timer_wrap_count);
		wrap_pending = nrf_timer_event_check(
			strobe_timer.p_reg, NRF_TIMER_EVENT_COMPARE0);
		wraps_after = atomic_get(&timer_wrap_count);
	} while (wraps_before != wraps_after);

	return bsf_timer_epoch_resolve((uint32_t)wraps_after, wrap_pending,
				       current_low);
}

static uint64_t timer_now_us(void)
{
	uint32_t low = nrfx_timer_capture(&strobe_timer, SOFTWARE_CAPTURE_CC);
	uint64_t epoch = timer_current_epoch(low);

	return (epoch << 32) | low;
}

static uint64_t expand_hardware_capture(uint32_t captured_low)
{
	uint32_t current_low =
		nrfx_timer_capture(&strobe_timer, SOFTWARE_CAPTURE_CC);
	atomic_val_t wraps_before;
	atomic_val_t wraps_after;
	bool wrap_pending;

	do {
		wraps_before = atomic_get(&timer_wrap_count);
		wrap_pending = nrf_timer_event_check(
			strobe_timer.p_reg, NRF_TIMER_EVENT_COMPARE0);
		wraps_after = atomic_get(&timer_wrap_count);
	} while (wraps_before != wraps_after);

	return bsf_timer_expand_capture((uint32_t)wraps_after, wrap_pending,
					current_low, captured_low);
}

static void timer_event_handler(nrf_timer_event_t event_type, void *context)
{
	ARG_UNUSED(context);

	if (event_type == NRF_TIMER_EVENT_COMPARE0) {
		atomic_inc(&timer_wrap_count);
	}
}

static void queue_edge(uint64_t timestamp_us, uint8_t kind)
{
	k_spinlock_key_t key;

	if ((timestamp_us - capture_started_us) < BOOT_EDGE_DISCARD_US) {
		atomic_inc(&boot_discarded_edge_count);
		return;
	}

	key = k_spin_lock(&capture_lock);
	if (edge_count == EDGE_QUEUE_CAPACITY) {
		atomic_inc(&edge_queue_drop_count);
	} else {
		edge_queue[edge_head].timestamp_us = timestamp_us;
		edge_queue[edge_head].kind = kind;
		edge_head = (uint8_t)((edge_head + 1u) % EDGE_QUEUE_CAPACITY);
		++edge_count;
	}
	k_spin_unlock(&capture_lock, key);
}

static void gpiote_event_handler(nrfx_gpiote_pin_t pin,
				 nrfx_gpiote_trigger_t trigger,
				 void *context)
{
	uint32_t captured_low;
	uint8_t kind;

	ARG_UNUSED(context);
	if (pin != STROBE_PIN) {
		return;
	}

	if (trigger == NRFX_GPIOTE_TRIGGER_LOTOHI) {
		captured_low = nrfx_timer_capture_get(
			&strobe_timer, RISING_CAPTURE_CC);
		kind = EDGE_RISING;
		atomic_inc(&rising_edge_count);
	} else if (trigger == NRFX_GPIOTE_TRIGGER_HITOLO) {
		captured_low = nrfx_timer_capture_get(
			&strobe_timer, FALLING_CAPTURE_CC);
		kind = EDGE_FALLING;
		atomic_inc(&falling_edge_count);
	} else {
		return;
	}

	queue_edge(expand_hardware_capture(captured_low), kind);
}

static size_t drain_and_sort_edges(void)
{
	k_spinlock_key_t key = k_spin_lock(&capture_lock);
	size_t count = edge_count;

	for (size_t i = 0; i < count; ++i) {
		drained_edges[i] = edge_queue[edge_tail];
		edge_tail = (uint8_t)((edge_tail + 1u) % EDGE_QUEUE_CAPACITY);
	}
	edge_count = 0u;
	k_spin_unlock(&capture_lock, key);

	/* GPIOTE dispatch follows channel number, not necessarily edge time, when
	 * both events were pending. Restore hardware-timestamp order explicitly. */
	for (size_t i = 1; i < count; ++i) {
		struct captured_edge value = drained_edges[i];
		size_t j = i;

		while (j > 0u &&
		       drained_edges[j - 1u].timestamp_us > value.timestamp_us) {
			drained_edges[j] = drained_edges[j - 1u];
			--j;
		}
		drained_edges[j] = value;
	}

	return count;
}

static size_t decode_pulses(size_t edge_total)
{
	size_t pulse_total = 0u;

	for (size_t i = 0; i < edge_total;) {
		const struct captured_edge *first = &drained_edges[i];
		struct captured_pulse *pulse = &decoded_pulses[pulse_total++];
		bool paired = false;

		pulse->rising_timestamp_us = BSF_CAPTURE_TS_ABSENT;
		pulse->falling_timestamp_us = BSF_CAPTURE_TS_ABSENT;
		pulse->strobe_timestamp_us = first->timestamp_us;
		pulse->edge_count = 1u;

		if (i + 1u < edge_total) {
			const struct captured_edge *second = &drained_edges[i + 1u];
			uint64_t width = second->timestamp_us - first->timestamp_us;

			paired = first->kind != second->kind &&
				 second->timestamp_us >= first->timestamp_us &&
				 width <= MAX_PULSE_WIDTH_US;
			if (paired) {
				pulse->edge_count = 2u;
				if (first->kind == EDGE_RISING) {
					pulse->rising_timestamp_us = first->timestamp_us;
					pulse->falling_timestamp_us = second->timestamp_us;
					pulse->edge_shape = BSF_CAPTURE_EDGE_ACTIVE_HIGH;
				} else {
					pulse->falling_timestamp_us = first->timestamp_us;
					pulse->rising_timestamp_us = second->timestamp_us;
					pulse->edge_shape = BSF_CAPTURE_EDGE_ACTIVE_LOW;
				}
				i += 2u;
			}
		}

		if (!paired) {
			if (first->kind == EDGE_RISING) {
				pulse->rising_timestamp_us = first->timestamp_us;
				pulse->edge_shape = BSF_CAPTURE_EDGE_RISING_ONLY;
			} else {
				pulse->falling_timestamp_us = first->timestamp_us;
				pulse->edge_shape = BSF_CAPTURE_EDGE_FALLING_ONLY;
			}
			++i;
		}
	}

	return pulse_total;
}

static void count_orphan(const struct captured_pulse *pulse)
{
	k_spinlock_key_t key;

	atomic_inc(&orphan_strobe_count);
	atomic_add(&orphan_edge_count, pulse->edge_count);
	key = k_spin_lock(&capture_lock);
	last_orphan_strobe_ts_us = pulse->strobe_timestamp_us;
	k_spin_unlock(&capture_lock, key);
}

static uint64_t last_orphan_timestamp(void)
{
	k_spinlock_key_t key = k_spin_lock(&capture_lock);
	uint64_t timestamp = last_orphan_strobe_ts_us;

	k_spin_unlock(&capture_lock, key);
	return timestamp;
}

static uint32_t abs_delta_u32(uint32_t a, uint32_t b)
{
	return a >= b ? a - b : b - a;
}

void bsf_strobe_capture_pair(uint8_t uwb_flags,
			     bsf_capture_record_t *record)
{
	uint64_t frame_timestamp_us = timer_now_us();
	size_t edge_total = drain_and_sort_edges();
	size_t pulse_total = decode_pulses(edge_total);
	int best = -1;
	uint32_t best_error = UINT32_MAX;
	uint8_t candidates = 0u;
	bool strobe_sent = (uwb_flags & BSL_FLAG_STROBE_SENT) != 0u;

	memset(record, 0, sizeof(*record));
	record->frame_rx_ts_us = frame_timestamp_us;
	record->strobe_ts_us = BSF_CAPTURE_TS_ABSENT;
	record->rising_ts_us = BSF_CAPTURE_TS_ABSENT;
	record->falling_ts_us = BSF_CAPTURE_TS_ABSENT;
	record->frame_to_strobe_us = BSF_CAPTURE_DELTA_ABSENT;
	record->pairing_window_us = BSF_CAPTURE_PAIR_WINDOW_US;

	for (size_t i = 0; i < pulse_total; ++i) {
		const struct captured_pulse *pulse = &decoded_pulses[i];
		uint64_t delta64;

		if (pulse->strobe_timestamp_us > frame_timestamp_us) {
			continue;
		}
		delta64 = frame_timestamp_us - pulse->strobe_timestamp_us;
		if (delta64 <= BSF_CAPTURE_PAIR_WINDOW_US) {
			uint32_t delta = (uint32_t)delta64;
			uint32_t error = abs_delta_u32(delta,
						       EXPECTED_FRAME_DELAY_US);

			if (candidates != UINT8_MAX) {
				++candidates;
			}
			if (error < best_error) {
				best_error = error;
				best = (int)i;
			}
		}
	}

	for (size_t i = 0; i < pulse_total; ++i) {
		const struct captured_pulse *pulse = &decoded_pulses[i];

		if ((int)i != best) {
			count_orphan(pulse);
		}
	}

	if (best >= 0) {
		const struct captured_pulse *pulse = &decoded_pulses[best];
		uint32_t delta = (uint32_t)(frame_timestamp_us -
					     pulse->strobe_timestamp_us);

		record->strobe_ts_us = pulse->strobe_timestamp_us;
		record->rising_ts_us = pulse->rising_timestamp_us;
		record->falling_ts_us = pulse->falling_timestamp_us;
		record->frame_to_strobe_us = delta;
		record->edge_shape = pulse->edge_shape;
		if (delta <= WINDOW_EDGE_BAND_US ||
		    (BSF_CAPTURE_PAIR_WINDOW_US - delta) <=
			WINDOW_EDGE_BAND_US) {
			atomic_inc(&near_window_edge_count);
		}
	}

	record->pair_candidates = candidates;
	if (best < 0) {
		atomic_inc(&orphan_frame_count);
	}
	if (strobe_sent) {
		record->verdict = best >= 0 ? BSF_CAPTURE_HEALTHY :
			BSF_CAPTURE_B306_MISSED_EDGE;
	} else {
		record->verdict = best >= 0 ? BSF_CAPTURE_CONTRADICTION :
			BSF_CAPTURE_TAG_NO_POLL_TX;
	}

	record->rising_edge_count = (uint32_t)atomic_get(&rising_edge_count);
	record->falling_edge_count = (uint32_t)atomic_get(&falling_edge_count);
	record->boot_discarded_edge_count =
		(uint32_t)atomic_get(&boot_discarded_edge_count);
	record->edge_queue_drop_count =
		(uint32_t)atomic_get(&edge_queue_drop_count);
	record->orphan_strobe_count =
		(uint32_t)atomic_get(&orphan_strobe_count);
	record->orphan_edge_count =
		(uint32_t)atomic_get(&orphan_edge_count);
	record->orphan_frame_count =
		(uint32_t)atomic_get(&orphan_frame_count);
	record->near_window_edge_count =
		(uint32_t)atomic_get(&near_window_edge_count);
	record->last_orphan_strobe_ts_us = last_orphan_timestamp();
	record->capture_flags = capture_flags;
}

void bsf_strobe_capture_telemetry(bsf_ble_telemetry_t *telemetry)
{
	telemetry->timer_wrap_count =
		(uint32_t)atomic_get(&timer_wrap_count);
	telemetry->rising_edge_count =
		(uint32_t)atomic_get(&rising_edge_count);
	telemetry->falling_edge_count =
		(uint32_t)atomic_get(&falling_edge_count);
	telemetry->boot_discarded_edge_count =
		(uint32_t)atomic_get(&boot_discarded_edge_count);
	telemetry->edge_queue_drop_count =
		(uint32_t)atomic_get(&edge_queue_drop_count);
	telemetry->orphan_strobe_count =
		(uint32_t)atomic_get(&orphan_strobe_count);
	telemetry->orphan_edge_count =
		(uint32_t)atomic_get(&orphan_edge_count);
	telemetry->orphan_frame_count =
		(uint32_t)atomic_get(&orphan_frame_count);
	telemetry->near_window_edge_count =
		(uint32_t)atomic_get(&near_window_edge_count);
	telemetry->capture_flags = capture_flags;
	telemetry->timer_instance = STROBE_TIMER_INSTANCE;
	telemetry->pairing_window_us = BSF_CAPTURE_PAIR_WINDOW_US;
}

int bsf_strobe_capture_init(void)
{
	nrfx_timer_config_t timer_config =
		NRFX_TIMER_DEFAULT_CONFIG(STROBE_TIMER_HZ);
	static const nrf_gpio_pin_pull_t pull_config =
		NRFX_GPIOTE_DEFAULT_PULL_CONFIG;
	nrfx_gpiote_trigger_config_t rising_trigger = {
		.trigger = NRFX_GPIOTE_TRIGGER_LOTOHI,
		.p_in_channel = &rising_gpiote_channel,
	};
	nrfx_gpiote_handler_config_t handler_config = {
		.handler = gpiote_event_handler,
		.p_context = NULL,
	};
	nrfx_gpiote_input_pin_config_t input_config = {
		.p_pull_config = &pull_config,
		.p_trigger_config = &rising_trigger,
		.p_handler_config = &handler_config,
	};
	nrf_gpiote_event_t rising_event;
	nrf_gpiote_event_t falling_event;
	nrfx_err_t err;
	int ret;

	ret = hfxo_hold();
	if (ret != 0) {
		LOG_ERR("persistent HFXO request failed: %d", ret);
		return ret;
	}

	timer_config.bit_width = NRF_TIMER_BIT_WIDTH_32;
	timer_config.interrupt_priority = STROBE_TIMER_IRQ_PRIORITY;
	err = nrfx_timer_init(&strobe_timer, &timer_config, timer_event_handler);
	if (err != NRFX_SUCCESS) {
		hfxo_release();
		return -EIO;
	}
	/* Announce the next epoch at UINT32_MAX, but do not CLEAR the timer.  The
	 * counter still wraps naturally after exactly 2^32 ticks.  Avoiding a
	 * compare at zero removes the boundary associated with the deployed
	 * first-wrap interrupt/telemetry outage. */
	atomic_set(&timer_wrap_count, 0);
	nrfx_timer_extended_compare(&strobe_timer, TIMER_WRAP_CC, UINT32_MAX,
				    0u, true);
	nrfx_timer_clear(&strobe_timer);
	nrfx_timer_enable(&strobe_timer);

	if (!nrfx_gpiote_init_check(&strobe_gpiote)) {
		nrfx_timer_disable(&strobe_timer);
		hfxo_release();
		return -ENODEV;
	}
	err = nrfx_gpiote_channel_alloc(&strobe_gpiote,
				       &rising_gpiote_channel);
	if (err != NRFX_SUCCESS) {
		nrfx_timer_disable(&strobe_timer);
		hfxo_release();
		return -ENOMEM;
	}
	err = nrfx_gpiote_channel_alloc(&strobe_gpiote,
				       &falling_gpiote_channel);
	if (err != NRFX_SUCCESS) {
		nrfx_timer_disable(&strobe_timer);
		hfxo_release();
		return -ENOMEM;
	}
	err = nrfx_gpiote_input_configure(&strobe_gpiote, STROBE_PIN,
					  &input_config);
	if (err != NRFX_SUCCESS) {
		nrfx_timer_disable(&strobe_timer);
		hfxo_release();
		return -EIO;
	}

	/* nrfx associates one event channel with a pin. The nRF52840 hardware
	 * permits a second event channel for the same PSEL; configure that channel
	 * explicitly so rising and falling timestamps land in separate CC
	 * registers even if the shared GPIOTE ISR is delayed beyond the pulse. */
	falling_event = nrf_gpiote_in_event_get(falling_gpiote_channel);
	nrf_gpiote_event_disable(NRF_GPIOTE, falling_gpiote_channel);
	nrf_gpiote_event_configure(NRF_GPIOTE, falling_gpiote_channel,
				   STROBE_PIN, NRF_GPIOTE_POLARITY_HITOLO);
	nrf_gpiote_event_clear(NRF_GPIOTE, falling_event);

	err = nrfx_ppi_channel_alloc(&rising_ppi_channel);
	if (err != NRFX_SUCCESS) {
		nrfx_timer_disable(&strobe_timer);
		hfxo_release();
		return -ENOMEM;
	}
	err = nrfx_ppi_channel_alloc(&falling_ppi_channel);
	if (err != NRFX_SUCCESS) {
		nrfx_timer_disable(&strobe_timer);
		hfxo_release();
		return -ENOMEM;
	}
	rising_event = nrf_gpiote_in_event_get(rising_gpiote_channel);
	err = nrfx_ppi_channel_assign(
		rising_ppi_channel,
		nrf_gpiote_event_address_get(NRF_GPIOTE, rising_event),
		nrfx_timer_capture_task_address_get(&strobe_timer,
						    RISING_CAPTURE_CC));
	if (err != NRFX_SUCCESS) {
		nrfx_timer_disable(&strobe_timer);
		hfxo_release();
		return -EIO;
	}
	err = nrfx_ppi_channel_assign(
		falling_ppi_channel,
		nrf_gpiote_event_address_get(NRF_GPIOTE, falling_event),
		nrfx_timer_capture_task_address_get(&strobe_timer,
						    FALLING_CAPTURE_CC));
	if (err != NRFX_SUCCESS) {
		nrfx_timer_disable(&strobe_timer);
		hfxo_release();
		return -EIO;
	}
	err = nrfx_ppi_channel_enable(rising_ppi_channel);
	if (err != NRFX_SUCCESS) {
		nrfx_timer_disable(&strobe_timer);
		hfxo_release();
		return -EIO;
	}
	err = nrfx_ppi_channel_enable(falling_ppi_channel);
	if (err != NRFX_SUCCESS) {
		nrfx_timer_disable(&strobe_timer);
		hfxo_release();
		return -EIO;
	}

	capture_flags = BSF_CAPTURE_FLAG_INPUT_NOPULL |
		BSF_CAPTURE_FLAG_TIMER2_1MHZ |
		BSF_CAPTURE_FLAG_DUAL_EDGE_PPI |
		BSF_CAPTURE_FLAG_HFXO_HELD;
	if (nrf_gpio_pin_read(STROBE_PIN) != 0u) {
		capture_flags |= BSF_CAPTURE_FLAG_INITIAL_HIGH;
	}
	capture_started_us = timer_now_us();

	nrf_gpiote_event_clear(NRF_GPIOTE, rising_event);
	nrf_gpiote_event_clear(NRF_GPIOTE, falling_event);
	nrf_gpiote_event_enable(NRF_GPIOTE, falling_gpiote_channel);
	nrf_gpiote_int_enable(NRF_GPIOTE, BIT(falling_gpiote_channel));
	nrfx_gpiote_trigger_enable(&strobe_gpiote, STROBE_PIN, true);
	capture_active = true;

	LOG_INF("strobe capture ready: pin=P1.03 timer=TIMER2@1MHz hfclk=HFXO-held rise_ch=%u fall_ch=%u ppi=%u/%u initial=%s pull=none window_us=%u",
		rising_gpiote_channel, falling_gpiote_channel,
		(unsigned int)rising_ppi_channel,
		(unsigned int)falling_ppi_channel,
		(capture_flags & BSF_CAPTURE_FLAG_INITIAL_HIGH) != 0u ?
			"high" : "low",
		BSF_CAPTURE_PAIR_WINDOW_US);
	return 0;
}

void bsf_strobe_capture_stop(void)
{
	if (!capture_active) {
		hfxo_release();
		return;
	}

	nrfx_gpiote_trigger_disable(&strobe_gpiote, STROBE_PIN);
	nrf_gpiote_int_disable(NRF_GPIOTE, BIT(falling_gpiote_channel));
	(void)nrfx_ppi_channel_disable(rising_ppi_channel);
	(void)nrfx_ppi_channel_disable(falling_ppi_channel);
	nrfx_timer_disable(&strobe_timer);
	capture_active = false;
	hfxo_release();
}
