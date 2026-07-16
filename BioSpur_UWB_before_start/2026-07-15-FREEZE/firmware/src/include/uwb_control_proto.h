#ifndef UWB_CONTROL_PROTO_H
#define UWB_CONTROL_PROTO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define UWB_CTRL_MAX_ANCHOR_IDS 8U
#define UWB_CTRL_MAX_PAYLOAD_LEN 24U
#define UWB_CTRL_FRAME_MAX_LEN (8U + UWB_CTRL_MAX_PAYLOAD_LEN)

#define UWB_CTRL_MAGIC_0 0x55U
#define UWB_CTRL_MAGIC_1 0x42U
#define UWB_CTRL_VERSION 0x01U

enum uwb_ctrl_msg_type {
    UWB_CTRL_MSG_PING = 0x01,
    UWB_CTRL_MSG_START_SWEEP = 0x02,
    UWB_CTRL_MSG_STOP = 0x03,
    UWB_CTRL_MSG_SINGLE_RANGE = 0x04,
    UWB_CTRL_MSG_START_AUTOPOS = 0x05,
};

struct uwb_ctrl_frame {
    uint8_t version;
    uint8_t type;
    uint8_t seq;
    uint8_t payload_len;
    uint8_t payload[UWB_CTRL_MAX_PAYLOAD_LEN];
};

size_t uwb_ctrl_encode_frame(const struct uwb_ctrl_frame *frame, uint8_t *out,
                             size_t out_len);

bool uwb_ctrl_build_ping(struct uwb_ctrl_frame *frame, uint8_t seq);
bool uwb_ctrl_build_stop(struct uwb_ctrl_frame *frame, uint8_t seq);
bool uwb_ctrl_build_single_range(struct uwb_ctrl_frame *frame, uint8_t seq,
                                 uint8_t anchor_id);
bool uwb_ctrl_build_start_sweep(struct uwb_ctrl_frame *frame, uint8_t seq,
                                const uint8_t *anchor_ids,
                                size_t anchor_count);
bool uwb_ctrl_build_start_autopos(struct uwb_ctrl_frame *frame, uint8_t seq,
                                  const uint8_t *anchor_ids,
                                  size_t anchor_count);

#endif /* UWB_CONTROL_PROTO_H */
