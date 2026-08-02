#ifndef UWB_BEACON_H
#define UWB_BEACON_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define UWB_BEACON_CODE 0xE2U
#define UWB_BEACON_SOURCE_ADDR 0xBC00U
#define UWB_BEACON_PROTOCOL_VERSION 1U

#define UWB_BEACON_INDEX_MAIN 0U
#define UWB_BEACON_INDEX_SUB 1U
#define UWB_BEACON_FLAG_PROMOTED 0x01U

#define UWB_BEACON_VERSION_IDX 10U
#define UWB_BEACON_FLAGS_IDX 11U
#define UWB_BEACON_INDEX_IDX 12U
#define UWB_BEACON_GENERATION_IDX 13U
#define UWB_BEACON_COUNTER_IDX 14U
#define UWB_BEACON_PERIOD_US_IDX 18U
#define UWB_BEACON_TX_OFFSET_US_IDX 22U
/* The last two octets are reserved for the DW1000-generated IEEE 802.15.4 FCS. */
#define UWB_BEACON_FRAME_LEN 26U

#define UWB_BEACON_DW_TIME_MASK ((UINT64_C(1) << 40) - UINT64_C(1))
#define UWB_BEACON_DW_TIME_HALF (UINT64_C(1) << 39)

struct uwb_beacon_payload {
	uint32_t superframe_counter;
	uint32_t cycle_period_us;
	uint16_t tx_offset_us;
	uint8_t schedule_generation;
	uint8_t beacon_index;
	uint8_t flags;
};

void uwb_beacon_build_frame(uint8_t *frame, uint8_t sequence,
			    const struct uwb_beacon_payload *payload);
bool uwb_beacon_parse_frame(const uint8_t *frame, size_t frame_len,
			    struct uwb_beacon_payload *payload);

uint64_t uwb_beacon_us_to_dw_ticks(uint32_t microseconds);
uint64_t uwb_beacon_dw_ticks_to_us(uint64_t ticks);
uint64_t uwb_beacon_add40(uint64_t timestamp, uint64_t delta);
uint64_t uwb_beacon_sub40(uint64_t timestamp, uint64_t delta);
int64_t uwb_beacon_diff40(uint64_t left, uint64_t right);
uint64_t uwb_beacon_origin_from_rx(uint64_t rx_timestamp,
				   uint16_t tx_offset_us);

#endif /* UWB_BEACON_H */
