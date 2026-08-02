#include "uwb_beacon.h"

#include <string.h>

#include "uwb_ss_twr_shared.h"

static void write_u16(uint8_t *frame, size_t offset, uint16_t value)
{
	frame[offset] = (uint8_t)value;
	frame[offset + 1U] = (uint8_t)(value >> 8);
}

static void write_u32(uint8_t *frame, size_t offset, uint32_t value)
{
	for (uint8_t i = 0U; i < 4U; ++i) {
		frame[offset + i] = (uint8_t)(value >> (8U * i));
	}
}

static uint16_t read_u16(const uint8_t *frame, size_t offset)
{
	return (uint16_t)frame[offset] |
	       (uint16_t)((uint16_t)frame[offset + 1U] << 8);
}

static uint32_t read_u32(const uint8_t *frame, size_t offset)
{
	uint32_t value = 0U;

	for (uint8_t i = 0U; i < 4U; ++i) {
		value |= (uint32_t)frame[offset + i] << (8U * i);
	}
	return value;
}

void uwb_beacon_build_frame(uint8_t *frame, uint8_t sequence,
			    const struct uwb_beacon_payload *payload)
{
	memset(frame, 0, UWB_BEACON_FRAME_LEN);
	frame[0] = UWB_FRAME_CTRL_LOW;
	frame[1] = UWB_FRAME_CTRL_HIGH;
	frame[UWB_MSG_SN_IDX] = sequence;
	write_u16(frame, UWB_MSG_PAN_IDX, APP_UWB_PAN_ID);
	write_u16(frame, UWB_MSG_DST_IDX, UWB_BROADCAST_SHORT_ADDR);
	write_u16(frame, UWB_MSG_SRC_IDX, UWB_BEACON_SOURCE_ADDR);
	frame[UWB_MSG_CODE_IDX] = UWB_BEACON_CODE;
	frame[UWB_BEACON_VERSION_IDX] = UWB_BEACON_PROTOCOL_VERSION;
	frame[UWB_BEACON_FLAGS_IDX] = payload->flags;
	frame[UWB_BEACON_INDEX_IDX] = payload->beacon_index;
	frame[UWB_BEACON_GENERATION_IDX] = payload->schedule_generation;
	write_u32(frame, UWB_BEACON_COUNTER_IDX, payload->superframe_counter);
	write_u32(frame, UWB_BEACON_PERIOD_US_IDX, payload->cycle_period_us);
	write_u16(frame, UWB_BEACON_TX_OFFSET_US_IDX, payload->tx_offset_us);
}

bool uwb_beacon_parse_frame(const uint8_t *frame, size_t frame_len,
			    struct uwb_beacon_payload *payload)
{
	uint16_t pan;
	uint16_t dst;
	uint16_t src;
	uint32_t period_us;
	uint16_t offset_us;
	uint8_t index;

	if (frame == NULL || payload == NULL || frame_len != UWB_BEACON_FRAME_LEN) {
		return false;
	}
	pan = read_u16(frame, UWB_MSG_PAN_IDX);
	dst = read_u16(frame, UWB_MSG_DST_IDX);
	src = read_u16(frame, UWB_MSG_SRC_IDX);
	index = frame[UWB_BEACON_INDEX_IDX];
	period_us = read_u32(frame, UWB_BEACON_PERIOD_US_IDX);
	offset_us = read_u16(frame, UWB_BEACON_TX_OFFSET_US_IDX);

	if (frame[0] != UWB_FRAME_CTRL_LOW ||
	    frame[1] != UWB_FRAME_CTRL_HIGH ||
	    pan != APP_UWB_PAN_ID ||
	    dst != UWB_BROADCAST_SHORT_ADDR ||
	    src != UWB_BEACON_SOURCE_ADDR ||
	    frame[UWB_MSG_CODE_IDX] != UWB_BEACON_CODE ||
	    frame[UWB_BEACON_VERSION_IDX] != UWB_BEACON_PROTOCOL_VERSION ||
	    index > UWB_BEACON_INDEX_SUB ||
	    period_us < 10000U ||
	    period_us > 1000000U ||
	    offset_us >= period_us) {
		return false;
	}

	payload->flags = frame[UWB_BEACON_FLAGS_IDX];
	payload->beacon_index = index;
	payload->schedule_generation = frame[UWB_BEACON_GENERATION_IDX];
	payload->superframe_counter = read_u32(frame, UWB_BEACON_COUNTER_IDX);
	payload->cycle_period_us = period_us;
	payload->tx_offset_us = offset_us;
	return true;
}

uint64_t uwb_beacon_us_to_dw_ticks(uint32_t microseconds)
{
	/* DW1000 time unit = 1 / (499.2 MHz * 128): 1 us = 63,897.6 ticks. */
	return ((uint64_t)microseconds * UINT64_C(638976) + UINT64_C(5)) /
	       UINT64_C(10);
}

uint64_t uwb_beacon_dw_ticks_to_us(uint64_t ticks)
{
	return (ticks * UINT64_C(10) + UINT64_C(319488)) /
	       UINT64_C(638976);
}

uint64_t uwb_beacon_add40(uint64_t timestamp, uint64_t delta)
{
	return (timestamp + delta) & UWB_BEACON_DW_TIME_MASK;
}

uint64_t uwb_beacon_sub40(uint64_t timestamp, uint64_t delta)
{
	return (timestamp - delta) & UWB_BEACON_DW_TIME_MASK;
}

int64_t uwb_beacon_diff40(uint64_t left, uint64_t right)
{
	uint64_t delta = uwb_beacon_sub40(left, right);

	if ((delta & UWB_BEACON_DW_TIME_HALF) != 0U) {
		return (int64_t)(delta - (UINT64_C(1) << 40));
	}
	return (int64_t)delta;
}

uint64_t uwb_beacon_origin_from_rx(uint64_t rx_timestamp,
				   uint16_t tx_offset_us)
{
	return uwb_beacon_sub40(rx_timestamp,
				uwb_beacon_us_to_dw_ticks(tx_offset_us));
}
