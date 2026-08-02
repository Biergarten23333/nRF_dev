#ifndef BIOSPUR_LED_FAULT_WINDOW_H
#define BIOSPUR_LED_FAULT_WINDOW_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/*
 * Five seconds is long enough to observe several grouped fault patterns while
 * remaining a recent-window signal. It covers about fifty 10 Hz UWB epochs
 * and self-heals without operator action.
 */
#define BSF_LED_FAULT_WINDOW_MS 5000u

/*
 * Before an explicit COUNTERS CLEAR establishes the acceptance baseline,
 * ignore the first ten parsed UWB outcomes. This is a data-driven settling
 * condition (about one second at the normal rate), not a boot-time delay.
 * A persistent fault is visible from outcome eleven onward.
 */
#define BSF_LED_STARTUP_OUTCOMES 10u

/*
 * Faults differ in kind, not merely rate: two 100 ms flashes separated by
 * 100 ms, followed by a 900 ms pause. This 1.2 s grouped pattern is legible
 * at normal viewing distance and cannot be confused with UWB event flicker.
 */
#define BSF_LED_FAULT_PATTERN_PERIOD_MS 1200u

/*
 * The physical PAIRED indicator did not make the 100 ms doublet
 * human-distinguishable: with a real health latch the operator consistently
 * saw one slow flash. Keep SENDING's accepted pattern above, but give PAIRED
 * two 250 ms flashes separated by 250 ms and a 1.25 s pause.
 */
#define BSF_LED_PAIRED_FAULT_PATTERN_PERIOD_MS 2000u

/*
 * Deliberately awkward test-only command. Normal session/control tooling never
 * emits the "TEST ONLY" namespace. This injects at the indicator input; it
 * does not claim to exercise UART corruption detection.
 */
#define BSF_LED_FAULT_TEST_COMMAND "TEST ONLY LED SENDING FAULT"

struct bsf_led_fault_window {
	uint32_t deadline_ms;
	uint8_t startup_outcomes;
	bool armed;
	bool pending;
};

static inline void
bsf_led_fault_window_clear_and_arm(struct bsf_led_fault_window *window)
{
	window->deadline_ms = 0u;
	window->startup_outcomes = BSF_LED_STARTUP_OUTCOMES;
	window->armed = true;
	window->pending = false;
}

static inline void
bsf_led_fault_window_observe(struct bsf_led_fault_window *window,
			     uint32_t now_ms, bool fault)
{
	if (!window->armed) {
		if (++window->startup_outcomes >= BSF_LED_STARTUP_OUTCOMES) {
			window->armed = true;
			window->pending = false;
		}
		return;
	}
	if (fault) {
		window->deadline_ms = now_ms + BSF_LED_FAULT_WINDOW_MS;
		window->pending = true;
	}
}

static inline bool
bsf_led_fault_window_active(struct bsf_led_fault_window *window,
			    uint32_t now_ms)
{
	if (!window->pending) {
		return false;
	}
	if ((int32_t)(window->deadline_ms - now_ms) > 0) {
		return true;
	}
	window->pending = false;
	return false;
}

static inline bool bsf_led_grouped_fault_on(uint32_t now_ms)
{
	uint32_t phase = now_ms % BSF_LED_FAULT_PATTERN_PERIOD_MS;

	return phase < 100u || (phase >= 200u && phase < 300u);
}

static inline bool bsf_led_paired_grouped_fault_on(uint32_t now_ms)
{
	uint32_t phase =
		now_ms % BSF_LED_PAIRED_FAULT_PATTERN_PERIOD_MS;

	return phase < 250u || (phase >= 500u && phase < 750u);
}

static inline bool bsf_led_fault_test_command_matches(const char *command)
{
	return strcmp(command, BSF_LED_FAULT_TEST_COMMAND) == 0;
}

#endif /* BIOSPUR_LED_FAULT_WINDOW_H */
