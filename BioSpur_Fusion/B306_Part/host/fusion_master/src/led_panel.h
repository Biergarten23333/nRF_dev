/*
 * Fixed-storage fault latch for the Fusion Master LED panel.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef BIOSPUR_FUSION_MASTER_LED_PANEL_H_
#define BIOSPUR_FUSION_MASTER_LED_PANEL_H_

#include <stdint.h>

enum fusion_led_fault_class {
	FUSION_LED_FAULT_CRC_HEADER = 0,
	FUSION_LED_FAULT_SEQUENCE,
	FUSION_LED_FAULT_QUEUE,
	FUSION_LED_FAULT_NOTIFY_UART,
	FUSION_LED_FAULT_DISCONNECT,
	FUSION_LED_FAULT_CLASS_COUNT,
};

struct fusion_led_fault_latch {
	uint32_t mask;
	uint32_t count[FUSION_LED_FAULT_CLASS_COUNT];
};

void fusion_led_fault_note(struct fusion_led_fault_latch *latch,
			   enum fusion_led_fault_class fault,
			   uint32_t amount);
void fusion_led_fault_clear(struct fusion_led_fault_latch *latch);

#endif /* BIOSPUR_FUSION_MASTER_LED_PANEL_H_ */
