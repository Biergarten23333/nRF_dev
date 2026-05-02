#ifndef BROADCAST_TDMA_H
#define BROADCAST_TDMA_H

#include <stdint.h>

#include "uwb_tdma.h"

uint32_t broadcast_tdma_wait_next_slot_start(
	const struct uwb_tdma_schedule *schedule);
uint32_t broadcast_tdma_slot_to_us(uint32_t slot_start_cycle,
				   uint32_t event_cycle);

#endif /* BROADCAST_TDMA_H */
