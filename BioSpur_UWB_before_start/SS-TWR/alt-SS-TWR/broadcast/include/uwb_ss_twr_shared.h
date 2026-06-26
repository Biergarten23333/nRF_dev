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

#define UWB_MSG_RESP_POLL_RX_TS_IDX 10U
#define UWB_MSG_RESP_RESP_TX_TS_IDX 14U
#define UWB_MSG_RESP_TS_LEN 4U
#define UWB_MSG_RESP_V1_FRAME_LEN 20U

#define UWB_MSG_RESP_DIAG_VERSION 2U
#define UWB_MSG_RESP_DIAG_FLAGS_VALID 0x01U
#define UWB_MSG_RESP_DIAG_FLAGS_DELAYED 0x02U
#define UWB_MSG_RESP_DIAG_VERSION_IDX 18U
#define UWB_MSG_RESP_DIAG_FLAGS_IDX 19U
#define UWB_MSG_RESP_DIAG_FP_INDEX_IDX 20U
#define UWB_MSG_RESP_DIAG_FP_AMPL1_IDX 22U
#define UWB_MSG_RESP_DIAG_FP_AMPL2_IDX 24U
#define UWB_MSG_RESP_DIAG_FP_AMPL3_IDX 26U
#define UWB_MSG_RESP_DIAG_CIR_PWR_IDX 28U
#define UWB_MSG_RESP_DIAG_RXPACC_IDX 30U
#define UWB_MSG_RESP_DIAG_STD_NOISE_IDX 32U
#define UWB_MSG_RESP_V2_FRAME_LEN 34U

/* Legacy/unicast Alt poll fields. */
#define UWB_MSG_POLL_INDEX_IDX 10U
#define UWB_MSG_POLL_COUNT_IDX 11U
#define UWB_MSG_POLL_ANCHOR_MASK_IDX 12U
#define UWB_MSG_ALT_UNICAST_POLL_FRAME_LEN 15U

/* Clean broadcast Alt poll fields.
 * Byte 10 packs low nibble tag id and high nibble responder-rank offset.
 * Normal/off/full CIR uses offset 0. Compact CIR rotates the offset so the
 * diagnostic target becomes the final responder without reading diagnostics
 * inside the RX window.
 */
#define UWB_MSG_BCAST_POLL_TAG_ID_IDX 10U
#define UWB_MSG_BCAST_POLL_ANCHOR_MASK_IDX 11U
#define UWB_MSG_BCAST_POLL_TX_TS_IDX 12U
#define UWB_MSG_BCAST_POLL_TX_TS_LEN 5U
#define UWB_MSG_ALT_BCAST_POLL_FRAME_LEN 17U
#define UWB_MSG_BCAST_POLL_TAG_ID_MASK 0x0fU
#define UWB_MSG_BCAST_POLL_RANK_OFFSET_SHIFT 4U
#define UWB_MSG_BCAST_POLL_RANK_OFFSET_MASK 0xf0U

#define UWB_MSG_ALT_POLL_FRAME_LEN UWB_MSG_ALT_BCAST_POLL_FRAME_LEN

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
                                               uint8_t tag_id,
                                               uint8_t rank_offset,
                                               uint64_t poll_tx_ts);
void uwb_ss_twr_build_resp_frame(uint8_t *frame, uint8_t seq, uint16_t dst_addr,
                                 uint16_t src_addr);

uint16_t uwb_frame_get_dst_addr(const uint8_t *frame);
uint16_t uwb_frame_get_src_addr(const uint8_t *frame);
uint8_t uwb_ss_twr_poll_index(const uint8_t *frame);
uint8_t uwb_ss_twr_poll_count(const uint8_t *frame);
uint8_t uwb_ss_twr_poll_anchor_mask(const uint8_t *frame);
uint8_t uwb_ss_twr_poll_tag_id(const uint8_t *frame);
uint8_t uwb_ss_twr_poll_rank_offset(const uint8_t *frame);
uint64_t uwb_ss_twr_poll_tx_ts(const uint8_t *frame);
bool uwb_ss_twr_poll_matches(const uint8_t *frame, uint16_t local_addr);
bool uwb_ss_twr_resp_matches(const uint8_t *frame, uint16_t local_addr,
                             uint16_t expected_peer_addr);

#endif /* UWB_SS_TWR_SHARED_H */
