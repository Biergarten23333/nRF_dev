#include "uwb_ss_twr_shared.h"

#include <errno.h>
#include <stddef.h>
#include <strings.h>

int uwb_tx_power_preset_lookup(const char *preset, uint32_t *value)
{
    static const struct {
        const char *name;
        uint32_t value;
    } presets[] = {
        { "MAX", UWB_TX_POWER_PRESET_MAX },
        { "M3", UWB_TX_POWER_PRESET_M3 },
        { "M6", UWB_TX_POWER_PRESET_M6 },
        { "M12", UWB_TX_POWER_PRESET_M12 },
        { "POR", UWB_TX_POWER_PRESET_POR },
    };

    if (preset == NULL || value == NULL) {
        return -EINVAL;
    }
    for (size_t i = 0U; i < (sizeof(presets) / sizeof(presets[0])); ++i) {
        if (strcasecmp(preset, presets[i].name) == 0) {
            *value = presets[i].value;
            return 0;
        }
    }
    return -EINVAL;
}

static void uwb_frame_write_u16(uint8_t *frame, uint8_t offset, uint16_t value)
{
    frame[offset] = (uint8_t)(value & 0xFFU);
    frame[offset + 1U] = (uint8_t)(value >> 8);
}

static void uwb_frame_write_u40(uint8_t *frame, uint8_t offset, uint64_t value)
{
    for (uint8_t i = 0U; i < 5U; ++i) {
        frame[offset + i] = (uint8_t)(value >> (i * 8U));
    }
}

static uint8_t uwb_count_mask_bits(uint8_t mask)
{
    uint8_t count = 0U;

    while (mask != 0U) {
        count += (uint8_t)(mask & 1U);
        mask >>= 1U;
    }

    return count;
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
            ((uint16_t)frame[UWB_MSG_PAN_IDX + 1U] << 8)) == APP_UWB_PAN_ID;
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
    uwb_frame_write_u16(frame, UWB_MSG_PAN_IDX, APP_UWB_PAN_ID);
    uwb_frame_write_u16(frame, UWB_MSG_DST_IDX, dst_addr);
    uwb_frame_write_u16(frame, UWB_MSG_SRC_IDX, src_addr);
    frame[UWB_MSG_CODE_IDX] = UWB_MSG_POLL_CODE;
    for (uint8_t i = UWB_MSG_COMMON_LEN;
         i <= UWB_MSG_POLL_ANCHOR_MASK_IDX; ++i) {
        frame[i] = 0U;
    }
}

void uwb_ss_twr_build_alt_poll_frame(uint8_t *frame, uint8_t seq,
                                     uint16_t dst_addr, uint16_t src_addr,
                                     uint8_t poll_index, uint8_t poll_count)
{
    uwb_ss_twr_build_poll_frame(frame, seq, dst_addr, src_addr);
    frame[UWB_MSG_POLL_INDEX_IDX] = poll_index;
    frame[UWB_MSG_POLL_COUNT_IDX] = poll_count;
    frame[UWB_MSG_POLL_ANCHOR_MASK_IDX] = 0U;
    frame[13] = 0U;
    frame[14] = 0U;
}

void uwb_ss_twr_build_alt_broadcast_poll_frame(uint8_t *frame, uint8_t seq,
                                               uint16_t src_addr,
                                               uint8_t anchor_mask,
                                               uint8_t tag_id,
                                               uint8_t rank_offset,
                                               uint64_t poll_tx_ts)
{
    uwb_ss_twr_build_poll_frame(frame, seq, UWB_BROADCAST_SHORT_ADDR, src_addr);
    frame[UWB_MSG_BCAST_POLL_TAG_ID_IDX] =
        (uint8_t)((tag_id & UWB_MSG_BCAST_POLL_TAG_ID_MASK) |
                  ((rank_offset << UWB_MSG_BCAST_POLL_RANK_OFFSET_SHIFT) &
                   UWB_MSG_BCAST_POLL_RANK_OFFSET_MASK));
    frame[UWB_MSG_BCAST_POLL_ANCHOR_MASK_IDX] = anchor_mask;
    uwb_frame_write_u40(frame, UWB_MSG_BCAST_POLL_TX_TS_IDX, poll_tx_ts);
}

void uwb_ss_twr_build_resp_frame(uint8_t *frame, uint8_t seq, uint16_t dst_addr,
                                 uint16_t src_addr)
{
    frame[0] = UWB_FRAME_CTRL_LOW;
    frame[1] = UWB_FRAME_CTRL_HIGH;
    frame[UWB_MSG_SN_IDX] = seq;
    uwb_frame_write_u16(frame, UWB_MSG_PAN_IDX, APP_UWB_PAN_ID);
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

uint8_t uwb_ss_twr_poll_index(const uint8_t *frame)
{
    if (uwb_frame_get_dst_addr(frame) == UWB_BROADCAST_SHORT_ADDR) {
        return 0U;
    }

    return frame[UWB_MSG_POLL_INDEX_IDX];
}

uint8_t uwb_ss_twr_poll_count(const uint8_t *frame)
{
    if (uwb_frame_get_dst_addr(frame) == UWB_BROADCAST_SHORT_ADDR) {
        return uwb_count_mask_bits(uwb_ss_twr_poll_anchor_mask(frame));
    }

    return frame[UWB_MSG_POLL_COUNT_IDX];
}

uint8_t uwb_ss_twr_poll_anchor_mask(const uint8_t *frame)
{
    if (uwb_frame_get_dst_addr(frame) == UWB_BROADCAST_SHORT_ADDR) {
        return frame[UWB_MSG_BCAST_POLL_ANCHOR_MASK_IDX];
    }

    return frame[UWB_MSG_POLL_ANCHOR_MASK_IDX];
}

uint8_t uwb_ss_twr_poll_tag_id(const uint8_t *frame)
{
    if (uwb_frame_get_dst_addr(frame) == UWB_BROADCAST_SHORT_ADDR) {
        return frame[UWB_MSG_BCAST_POLL_TAG_ID_IDX] &
               UWB_MSG_BCAST_POLL_TAG_ID_MASK;
    }

    return uwb_tag_id_from_addr(uwb_frame_get_src_addr(frame));
}

uint8_t uwb_ss_twr_poll_rank_offset(const uint8_t *frame)
{
    if (uwb_frame_get_dst_addr(frame) == UWB_BROADCAST_SHORT_ADDR) {
        return (uint8_t)((frame[UWB_MSG_BCAST_POLL_TAG_ID_IDX] &
                          UWB_MSG_BCAST_POLL_RANK_OFFSET_MASK) >>
                         UWB_MSG_BCAST_POLL_RANK_OFFSET_SHIFT);
    }

    return 0U;
}

uint64_t uwb_ss_twr_poll_tx_ts(const uint8_t *frame)
{
    uint64_t ts = 0U;

    if (uwb_frame_get_dst_addr(frame) != UWB_BROADCAST_SHORT_ADDR) {
        return 0U;
    }

    for (int i = (int)UWB_MSG_BCAST_POLL_TX_TS_LEN - 1; i >= 0; --i) {
        ts <<= 8U;
        ts |= frame[UWB_MSG_BCAST_POLL_TX_TS_IDX + (uint8_t)i];
    }

    return ts;
}

bool uwb_ss_twr_poll_matches(const uint8_t *frame, uint16_t local_addr)
{
    uint16_t src_addr;
    uint16_t dst_addr;

    if (!uwb_frame_has_common_header(frame, UWB_MSG_POLL_CODE)) {
        return false;
    }

    dst_addr = uwb_frame_get_dst_addr(frame);
    if (dst_addr == UWB_BROADCAST_SHORT_ADDR) {
        uint8_t anchor_id;
        uint8_t mask = uwb_ss_twr_poll_anchor_mask(frame);

        if (!uwb_short_addr_is_anchor(local_addr) || mask == 0U) {
            return false;
        }
        anchor_id = uwb_anchor_id_from_addr(local_addr);
        if (anchor_id >= UWB_MAX_ANCHORS ||
            (mask & (uint8_t)(1U << anchor_id)) == 0U) {
            return false;
        }
    } else if (dst_addr != local_addr) {
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
