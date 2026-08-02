#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "../src/led_fault_window.h"

int main(void)
{
	struct bsf_led_fault_window window = {0};

	assert(bsf_led_fault_test_command_matches(
		"TEST ONLY LED SENDING FAULT"));
	assert(!bsf_led_fault_test_command_matches("LED SENDING FAULT"));
	assert(!bsf_led_fault_test_command_matches(
		"TEST ONLY LED SENDING FAULT "));
	assert(!bsf_led_fault_test_command_matches(
		"TEST ONLY LED PAIRED FAULT"));

	for (uint8_t i = 0u; i < BSF_LED_STARTUP_OUTCOMES; ++i) {
		bsf_led_fault_window_observe(&window, 100u * i, true);
		assert(!bsf_led_fault_window_active(&window, 100u * i));
	}
	assert(window.armed);

	bsf_led_fault_window_observe(&window, 1000u, true);
	assert(bsf_led_fault_window_active(&window, 1000u));
	assert(bsf_led_fault_window_active(
		&window, 1000u + BSF_LED_FAULT_WINDOW_MS - 1u));
	assert(!bsf_led_fault_window_active(
		&window, 1000u + BSF_LED_FAULT_WINDOW_MS));

	bsf_led_fault_window_observe(&window, 9000u, true);
	assert(bsf_led_fault_window_active(&window, 9000u));
	bsf_led_fault_window_observe(&window, 10000u, true);
	assert(bsf_led_fault_window_active(
		&window, 10000u + BSF_LED_FAULT_WINDOW_MS - 1u));

	bsf_led_fault_window_clear_and_arm(&window);
	assert(!bsf_led_fault_window_active(&window, UINT32_MAX));

	/* Signed-delta expiry remains correct across the uint32_t wrap. */
	bsf_led_fault_window_observe(&window, UINT32_MAX - 1000u, true);
	assert(bsf_led_fault_window_active(&window, UINT32_MAX - 500u));
	assert(bsf_led_fault_window_active(&window, 3998u));
	assert(!bsf_led_fault_window_active(&window, 3999u));

	assert(bsf_led_grouped_fault_on(0u));
	assert(!bsf_led_grouped_fault_on(100u));
	assert(bsf_led_grouped_fault_on(200u));
	assert(!bsf_led_grouped_fault_on(300u));
	assert(!bsf_led_grouped_fault_on(1199u));
	assert(bsf_led_grouped_fault_on(1200u));

	assert(bsf_led_paired_grouped_fault_on(0u));
	assert(bsf_led_paired_grouped_fault_on(249u));
	assert(!bsf_led_paired_grouped_fault_on(250u));
	assert(bsf_led_paired_grouped_fault_on(500u));
	assert(bsf_led_paired_grouped_fault_on(749u));
	assert(!bsf_led_paired_grouped_fault_on(750u));
	assert(!bsf_led_paired_grouped_fault_on(1999u));
	assert(bsf_led_paired_grouped_fault_on(2000u));

	puts("LED_FAULT_WINDOW_PASS");
	return 0;
}
