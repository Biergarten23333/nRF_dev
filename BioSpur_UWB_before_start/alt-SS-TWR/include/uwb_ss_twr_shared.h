#ifndef UWB_SS_TWR_SHARED_H
#define UWB_SS_TWR_SHARED_H

#include <stdbool.h>
#include <stdint.h>

#define UWB_MAX_ANCHORS 8U
#define UWB_MAX_TAGS 256U

#ifndef APP_UWB_CHANNEL
#define APP_UWB_CHANNEL 5U
#endif

#ifndef APP_UWB_PAN_ID
#define APP_UWB_PAN_ID 0xDECAU
#endif

#define UWB_FRAME_CTRL_LOW 0x41U
#define UWB_FRAME_CTRL_HIGH 0x88U

#define UWB_MSG_COMMON_LEN 10U
#define UWB_MSG_SN_IDX 2U
#define UWB_MSG_PAN_IDX 3U
#define UWB_MSG_DST_IDX 5U
#define UWB_MSG_SRC_IDX 7U
#define UWB_MSG_CODE_IDX 9U

#define UWB_MSG_POLL_CODE 0xE0U
#define UWB_MSG_RESP_CODE 0xE1U

#define UWB_MSG_POLL_INDEX_IDX 10U
#define UWB_MSG_POLL_COUNT_IDX 11U
#define UWB_MSG_POLL_ANCHOR_MASK_IDX 12U

#define UWB_ANCHOR_BASE_ADDR 0xA100U
#define UWB_TAG_BASE_ADDR 0xB100U
#define UWB_BROADCAST_SHORT_ADDR 0xFFFFU

uint16_t uwb_anchor_short_addr(uint8_t anchor_id);
uint16_t uwb_tag_short_addr(uint8_t tag_id);
uint8_t uwb_anchor_id_from_addr(uint16_t short_addr);
uint8_t uwb_tag_id_from_addr(uint16_t short_addr);
bool uwb_short_addr_is_anchor(uint16_t short_addr);
bool uwb_short_addr_is_tag(uint16_t short_addr);
bool uwb_short_addr_is_ranging_initiator(uint16_t short_addr);

void uwb_ss_twr_build_poll_frame(uint8_t *frame, uint8_t seq, uint16_t dst_addr,
                                 uint16_t src_addr);
void uwb_ss_twr_build_alt_poll_frame(uint8_t *frame, uint8_t seq, uint16_t dst_addr,
                                     uint16_t src_addr, uint8_t poll_index,
                                     uint8_t poll_count);
void uwb_ss_twr_build_alt_broadcast_poll_frame(uint8_t *frame, uint8_t seq,
                                               uint16_t src_addr,
                                               uint8_t anchor_mask,
                                               uint8_t poll_count);
void uwb_ss_twr_build_resp_frame(uint8_t *frame, uint8_t seq, uint16_t dst_addr,
                                 uint16_t src_addr);

uint16_t uwb_frame_get_dst_addr(const uint8_t *frame);
uint16_t uwb_frame_get_src_addr(const uint8_t *frame);
uint8_t uwb_ss_twr_poll_index(const uint8_t *frame);
uint8_t uwb_ss_twr_poll_count(const uint8_t *frame);
uint8_t uwb_ss_twr_poll_anchor_mask(const uint8_t *frame);
bool uwb_ss_twr_poll_matches(const uint8_t *frame, uint16_t local_addr);
bool uwb_ss_twr_resp_matches(const uint8_t *frame, uint16_t local_addr,
                             uint16_t expected_peer_addr);

#endif /* UWB_SS_TWR_SHARED_H */
