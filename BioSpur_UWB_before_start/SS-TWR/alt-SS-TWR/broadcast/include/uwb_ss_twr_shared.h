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
/* V3 appends the responder's own DW1000 chip temperature + Vbat as raw 8-bit
 * SAR codes (1 byte each) after the V2 diag block. Length-gated on the tag
 * side so a V2 (34-byte) and a V3 (36-byte) responder interoperate without
 * breaking ranging. Raw codes only; host converts to degC/volts offline.
 */
#define UWB_MSG_RESP_DIAG_TEMP_IDX 34U
#define UWB_MSG_RESP_DIAG_VBAT_IDX 35U
#define UWB_MSG_RESP_V3_FRAME_LEN 36U

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

/* Runtime TX_POWER (reg 0x1E) presets — CH5/PRF64 smart-TX. DIS_STXP and
 * TC_PGDELAY are NOT touched by these. Octet layout [P125,P250,P500,NORM],
 * each = coarse DA(bits7:5, ~3dB/step) + mixer(bits4:0, ~0.5dB/step). Reduction
 * is subtracted in half-dB from every octet (DA then mixer), floored at 0x00 so
 * the smart-TX ladder stays coherent. Operative ranging bin = BOOSTP250 (0x45).
 *   MAX = ref (P250 8.5 dB above floor)
 *   M3  = -3.0 dB (P250 0x25)
 *   M6  = -6.0 dB (P250 0x05)
 *   M12 = target -12 dB but P250 clamps to 0x00 => actual -8.5 dB (register
 *         floor: 0x45 is only 8.5 dB above 0x00; true -12 dB is unreachable
 *         with smart-TX enabled)
 *   POR = DW1000 power-on-reset default (P250 0x08 = -4.5 dB vs MAX) */
#define UWB_TX_POWER_PRESET_MAX 0x25456585UL
#define UWB_TX_POWER_PRESET_M3  0x05254565UL
#define UWB_TX_POWER_PRESET_M6  0x00052545UL
#define UWB_TX_POWER_PRESET_M12 0x00000005UL
#define UWB_TX_POWER_PRESET_POR 0x0E080222UL

/* Map a preset name (MAX|M3|M6|M12|POR, case-insensitive) to its TX_POWER
 * register value. Returns 0 and sets *value on success, -EINVAL otherwise.
 * Pure lookup: does NOT touch the radio (callers do the dwt_write). */
int uwb_tx_power_preset_lookup(const char *preset, uint32_t *value);

#endif /* UWB_SS_TWR_SHARED_H */
