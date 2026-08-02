/*
 * Fixed-storage fault latch for the Fusion Master LED panel.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include "led_panel.h"

#include <limits.h>
#include <string.h>

void fusion_led_fault_note(struct fusion_led_fault_latch *latch,
			   enum fusion_led_fault_class fault,
			   uint32_t amount)
{
	uint32_t remaining;

	if (latch == NULL || fault >= FUSION_LED_FAULT_CLASS_COUNT ||
	    amount == 0u) {
		return;
	}
	latch->mask |= 1u << (uint32_t)fault;
	remaining = UINT32_MAX - latch->count[fault];
	latch->count[fault] += amount > remaining ? remaining : amount;
}

void fusion_led_fault_clear(struct fusion_led_fault_latch *latch)
{
	if (latch != NULL) {
		memset(latch, 0, sizeof(*latch));
	}
}
