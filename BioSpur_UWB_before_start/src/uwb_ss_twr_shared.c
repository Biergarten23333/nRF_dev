#include "uwb_ss_twr_shared.h"

static void uwb_frame_write_u16(uint8_t *frame, uint8_t offset, uint16_t value)
{
    frame[offset] = (uint8_t)(value & 0xFFU);
    frame[offset + 1U] = (uint8_t)(value >> 8);
}

static bool uwb_frame_has_common_header(const uint8_t *frame, uint8_t code)
{
    if ((frame[0] != UWB_FRAME_CTRL_LOW) || (frame[1] != UWB_FRAME_CTRL_HIGH)) {
        return false;
    }

    if (frame[UWB_MSG_CODE_IDX] != code) {
        return false;
    }

    return uwb_frame_get_dst_addr(frame) != 0U &&
           uwb_frame_get_src_addr(frame) != 0U &&
           ((uint16_t)frame[UWB_MSG_PAN_IDX] |
            ((uint16_t)frame[UWB_MSG_PAN_IDX + 1U] << 8)) == UWB_PAN_ID;
}

uint16_t uwb_anchor_short_addr(uint8_t anchor_id)
{
    return (uint16_t)(UWB_ANCHOR_BASE_ADDR + anchor_id);
}

uint16_t uwb_tag_short_addr(uint8_t tag_id)
{
    return (uint16_t)(UWB_TAG_BASE_ADDR + tag_id);
}

uint8_t uwb_anchor_id_from_addr(uint16_t short_addr)
{
    return (uint8_t)(short_addr - UWB_ANCHOR_BASE_ADDR);
}

uint8_t uwb_tag_id_from_addr(uint16_t short_addr)
{
    return (uint8_t)(short_addr - UWB_TAG_BASE_ADDR);
}

bool uwb_short_addr_is_anchor(uint16_t short_addr)
{
    return short_addr >= UWB_ANCHOR_BASE_ADDR &&
           short_addr < (UWB_ANCHOR_BASE_ADDR + UWB_MAX_ANCHORS);
}

bool uwb_short_addr_is_tag(uint16_t short_addr)
{
    return short_addr >= UWB_TAG_BASE_ADDR &&
           short_addr < (UWB_TAG_BASE_ADDR + UWB_MAX_TAGS);
}

bool uwb_short_addr_is_ranging_initiator(uint16_t short_addr)
{
    return uwb_short_addr_is_tag(short_addr) || uwb_short_addr_is_anchor(short_addr);
}

void uwb_ss_twr_build_poll_frame(uint8_t *frame, uint8_t seq, uint16_t dst_addr,
                                 uint16_t src_addr)
{
    frame[0] = UWB_FRAME_CTRL_LOW;
    frame[1] = UWB_FRAME_CTRL_HIGH;
    frame[UWB_MSG_SN_IDX] = seq;
    uwb_frame_write_u16(frame, UWB_MSG_PAN_IDX, UWB_PAN_ID);
    uwb_frame_write_u16(frame, UWB_MSG_DST_IDX, dst_addr);
    uwb_frame_write_u16(frame, UWB_MSG_SRC_IDX, src_addr);
    frame[UWB_MSG_CODE_IDX] = UWB_MSG_POLL_CODE;
    frame[10] = 0U;
    frame[11] = 0U;
}

void uwb_ss_twr_build_resp_frame(uint8_t *frame, uint8_t seq, uint16_t dst_addr,
                                 uint16_t src_addr)
{
    frame[0] = UWB_FRAME_CTRL_LOW;
    frame[1] = UWB_FRAME_CTRL_HIGH;
    frame[UWB_MSG_SN_IDX] = seq;
    uwb_frame_write_u16(frame, UWB_MSG_PAN_IDX, UWB_PAN_ID);
    uwb_frame_write_u16(frame, UWB_MSG_DST_IDX, dst_addr);
    uwb_frame_write_u16(frame, UWB_MSG_SRC_IDX, src_addr);
    frame[UWB_MSG_CODE_IDX] = UWB_MSG_RESP_CODE;

    for (uint8_t i = 10U; i < 20U; ++i) {
        frame[i] = 0U;
    }
}

uint16_t uwb_frame_get_dst_addr(const uint8_t *frame)
{
    return (uint16_t)frame[UWB_MSG_DST_IDX] |
           ((uint16_t)frame[UWB_MSG_DST_IDX + 1U] << 8);
}

uint16_t uwb_frame_get_src_addr(const uint8_t *frame)
{
    return (uint16_t)frame[UWB_MSG_SRC_IDX] |
           ((uint16_t)frame[UWB_MSG_SRC_IDX + 1U] << 8);
}

bool uwb_ss_twr_poll_matches(const uint8_t *frame, uint16_t local_addr)
{
    uint16_t src_addr;

    if (!uwb_frame_has_common_header(frame, UWB_MSG_POLL_CODE)) {
        return false;
    }

    if (uwb_frame_get_dst_addr(frame) != local_addr) {
        return false;
    }

    src_addr = uwb_frame_get_src_addr(frame);
    return uwb_short_addr_is_ranging_initiator(src_addr);
}

bool uwb_ss_twr_resp_matches(const uint8_t *frame, uint16_t local_addr,
                             uint16_t expected_peer_addr)
{
    if (!uwb_frame_has_common_header(frame, UWB_MSG_RESP_CODE)) {
        return false;
    }

    if (uwb_frame_get_dst_addr(frame) != local_addr) {
        return false;
    }

    return uwb_frame_get_src_addr(frame) == expected_peer_addr;
}
