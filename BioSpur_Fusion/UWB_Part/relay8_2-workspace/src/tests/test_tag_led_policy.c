#include <assert.h>
#include <stdbool.h>

#include "../apps/tag/src/tag_led_policy.h"

static void expect(bool health, bool tdma, bool ble,
		   struct tag_led_policy_input input)
{
	struct tag_led_policy_output output = tag_led_policy_evaluate(&input);

	assert(output.health_on == health);
	assert(output.tdma_on == tdma);
	assert(output.ble_on == ble);
}

int main(void)
{
	struct tag_led_policy_input input = {0};

	expect(false, false, false, input);

	input.uwb_ready = true;
	input.slow_phase_on = true;
	expect(true, false, false, input);
	input.slow_phase_on = false;
	expect(false, false, false, input);

	input.health_fault = true;
	input.fast_phase_on = true;
	expect(true, false, false, input);
	input.fast_phase_on = false;
	expect(false, false, false, input);

	input.health_fault = false;
	input.tdma_configured = true;
	input.slow_phase_on = true;
	expect(true, true, false, input);
	input.slow_phase_on = false;
	expect(false, false, false, input);
	input.tdma_running = true;
	expect(false, true, false, input);

	input.ble_state = TAG_LED_BLE_ADVERTISING;
	input.slow_phase_on = true;
	expect(true, true, true, input);
	input.slow_phase_on = false;
	expect(false, true, false, input);
	input.ble_state = TAG_LED_BLE_CONNECTED;
	expect(false, true, true, input);

	return 0;
}
