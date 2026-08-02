#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "../src/led_panel.h"

int main(void)
{
	struct fusion_led_fault_latch latch = {0};

	for (uint32_t fault = 0u;
	     fault < FUSION_LED_FAULT_CLASS_COUNT; ++fault) {
		fusion_led_fault_note(
			&latch, (enum fusion_led_fault_class)fault, fault + 1u);
		assert((latch.mask & (1u << fault)) != 0u);
		assert(latch.count[fault] == fault + 1u);
	}
	assert(latch.mask ==
	       ((1u << FUSION_LED_FAULT_CLASS_COUNT) - 1u));

	fusion_led_fault_note(&latch, FUSION_LED_FAULT_QUEUE, UINT32_MAX);
	assert(latch.count[FUSION_LED_FAULT_QUEUE] == UINT32_MAX);
	fusion_led_fault_note(&latch, FUSION_LED_FAULT_QUEUE, 1u);
	assert(latch.count[FUSION_LED_FAULT_QUEUE] == UINT32_MAX);

	fusion_led_fault_clear(&latch);
	assert(latch.mask == 0u);
	for (uint32_t fault = 0u;
	     fault < FUSION_LED_FAULT_CLASS_COUNT; ++fault) {
		assert(latch.count[fault] == 0u);
	}

	puts("led panel latch tests: PASS");
	return 0;
}
