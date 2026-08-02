#ifndef TAG_LED_POLICY_H
#define TAG_LED_POLICY_H

#include <stdbool.h>
#include <stdint.h>

enum tag_led_ble_state {
	TAG_LED_BLE_OFF = 0,
	TAG_LED_BLE_ADVERTISING = 1,
	TAG_LED_BLE_CONNECTED = 2,
};

struct tag_led_policy_input {
	bool uwb_ready;
	bool health_fault;
	bool tdma_configured;
	bool tdma_running;
	uint8_t ble_state;
	bool slow_phase_on;
	bool fast_phase_on;
};

struct tag_led_policy_output {
	bool health_on;
	bool tdma_on;
	bool ble_on;
};

static inline struct tag_led_policy_output
tag_led_policy_evaluate(const struct tag_led_policy_input *in)
{
	struct tag_led_policy_output out = {0};

	if (in->uwb_ready) {
		out.health_on =
			in->health_fault ? in->fast_phase_on : in->slow_phase_on;
	}
	if (in->tdma_configured) {
		out.tdma_on = in->tdma_running ? true : in->slow_phase_on;
	}
	if (in->ble_state == TAG_LED_BLE_ADVERTISING) {
		out.ble_on = in->slow_phase_on;
	} else if (in->ble_state == TAG_LED_BLE_CONNECTED) {
		out.ble_on = true;
	}

	return out;
}

#endif /* TAG_LED_POLICY_H */
