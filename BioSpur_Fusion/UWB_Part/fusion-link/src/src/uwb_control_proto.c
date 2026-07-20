#include "uwb_control_proto.h"

static uint8_t uwb_ctrl_checksum(const uint8_t *buf, size_t len)
{
    uint8_t sum = 0U;

    for (size_t i = 0; i < len; ++i) {
        sum ^= buf[i];
    }

    return sum;
}

static bool uwb_ctrl_prepare(struct uwb_ctrl_frame *frame, uint8_t seq,
                             uint8_t type, const uint8_t *payload,
                             size_t payload_len)
{
    if (frame == NULL || payload_len > UWB_CTRL_MAX_PAYLOAD_LEN) {
        return false;
    }

    frame->version = UWB_CTRL_VERSION;
    frame->type = type;
    frame->seq = seq;
    frame->payload_len = (uint8_t)payload_len;

    for (size_t i = 0; i < payload_len; ++i) {
        frame->payload[i] = payload[i];
    }

    for (size_t i = payload_len; i < UWB_CTRL_MAX_PAYLOAD_LEN; ++i) {
        frame->payload[i] = 0U;
    }

    return true;
}

size_t uwb_ctrl_encode_frame(const struct uwb_ctrl_frame *frame, uint8_t *out,
                             size_t out_len)
{
    size_t total_len;

    if (frame == NULL || out == NULL) {
        return 0U;
    }

    total_len = 8U + frame->payload_len;
    if (out_len < total_len) {
        return 0U;
    }

    out[0] = UWB_CTRL_MAGIC_0;
    out[1] = UWB_CTRL_MAGIC_1;
    out[2] = frame->version;
    out[3] = frame->type;
    out[4] = frame->seq;
    out[5] = frame->payload_len;
    out[6] = 0U;

    for (size_t i = 0; i < frame->payload_len; ++i) {
        out[7U + i] = frame->payload[i];
    }

    out[total_len - 1U] = uwb_ctrl_checksum(out, total_len - 1U);
    return total_len;
}

bool uwb_ctrl_build_ping(struct uwb_ctrl_frame *frame, uint8_t seq)
{
    return uwb_ctrl_prepare(frame, seq, UWB_CTRL_MSG_PING, NULL, 0U);
}

bool uwb_ctrl_build_stop(struct uwb_ctrl_frame *frame, uint8_t seq)
{
    return uwb_ctrl_prepare(frame, seq, UWB_CTRL_MSG_STOP, NULL, 0U);
}

bool uwb_ctrl_build_single_range(struct uwb_ctrl_frame *frame, uint8_t seq,
                                 uint8_t anchor_id)
{
    uint8_t payload[1];

    payload[0] = anchor_id;
    return uwb_ctrl_prepare(frame, seq, UWB_CTRL_MSG_SINGLE_RANGE, payload,
                            sizeof(payload));
}

bool uwb_ctrl_build_start_sweep(struct uwb_ctrl_frame *frame, uint8_t seq,
                                const uint8_t *anchor_ids,
                                size_t anchor_count)
{
    uint8_t payload[1U + UWB_CTRL_MAX_PAYLOAD_LEN];

    if (anchor_ids == NULL || anchor_count == 0U ||
        anchor_count > (UWB_CTRL_MAX_PAYLOAD_LEN - 1U)) {
        return false;
    }

    payload[0] = (uint8_t)anchor_count;
    for (size_t i = 0; i < anchor_count; ++i) {
        payload[1U + i] = anchor_ids[i];
    }

    return uwb_ctrl_prepare(frame, seq, UWB_CTRL_MSG_START_SWEEP, payload,
                            anchor_count + 1U);
}

bool uwb_ctrl_build_start_autopos(struct uwb_ctrl_frame *frame, uint8_t seq,
                                  const uint8_t *anchor_ids,
                                  size_t anchor_count)
{
    uint8_t payload[1U + UWB_CTRL_MAX_PAYLOAD_LEN];

    if (anchor_ids == NULL || anchor_count < 2U ||
        anchor_count > (UWB_CTRL_MAX_PAYLOAD_LEN - 1U)) {
        return false;
    }

    payload[0] = (uint8_t)anchor_count;
    for (size_t i = 0; i < anchor_count; ++i) {
        payload[1U + i] = anchor_ids[i];
    }

    return uwb_ctrl_prepare(frame, seq, UWB_CTRL_MSG_START_AUTOPOS, payload,
                            anchor_count + 1U);
}
