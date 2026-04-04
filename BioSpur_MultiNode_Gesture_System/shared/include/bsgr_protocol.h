#ifndef BSGR_PROTOCOL_H_
#define BSGR_PROTOCOL_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#ifndef BIT
#define BIT(n) (1UL << (n))
#endif

#ifndef BUILD_ASSERT
#define BUILD_ASSERT(cond, msg) _Static_assert(cond, msg)
#endif

#define BSGR_PROTOCOL_VERSION 0x01u
#define BSGR_MAX_FRAME_PAYLOAD_LEN 240u
#define BSGR_MAX_FRAME_LEN (sizeof(struct bsgr_frame_header) + BSGR_MAX_FRAME_PAYLOAD_LEN)

#define BSGR_IMU_MAX_RAW_FRAME_LEN 32u
#define BSGR_UWB_MAX_RAW_RECORD_LEN 64u

enum bsgr_frame_type {
	BSGR_FRAME_TYPE_STATUS = 0x01,
	BSGR_FRAME_TYPE_IMU_BATCH = 0x02,
	BSGR_FRAME_TYPE_UWB_REPORT = 0x03,
	BSGR_FRAME_TYPE_TSYNC = 0x04,
	BSGR_FRAME_TYPE_CMD = 0x05,
	BSGR_FRAME_TYPE_CMD_ACK = 0x06,
	BSGR_FRAME_TYPE_EVENT = 0x07,
	BSGR_FRAME_TYPE_RESERVED = 0x7f,
};

enum bsgr_cmd_opcode {
	BSGR_CMD_NOP = 0x00,
	BSGR_CMD_SET_IMU_PHASE = 0x01,
	BSGR_CMD_TSYNC_REQUEST = 0x02,
	BSGR_CMD_TSYNC_RESPONSE = 0x03,
	BSGR_CMD_STREAM_ENABLE = 0x04,
	BSGR_CMD_STREAM_DISABLE = 0x05,
	BSGR_CMD_DFU_PREPARE = 0x06,
	BSGR_CMD_DFU_AUTHORIZE = 0x07,
	BSGR_CMD_DFU_BEGIN = 0x08,
	BSGR_CMD_DFU_END = 0x09,
};

enum bsgr_cmd_result {
	BSGR_CMD_RESULT_OK = 0x00,
	BSGR_CMD_RESULT_REJECTED = 0x01,
	BSGR_CMD_RESULT_UNSUPPORTED = 0x02,
	BSGR_CMD_RESULT_BUSY = 0x03,
	BSGR_CMD_RESULT_DEFERRED = 0x04,
	BSGR_CMD_RESULT_INVALID = 0x05,
};

enum bsgr_status_code {
	BSGR_STATUS_OK = 0x00,
	BSGR_STATUS_BOOT = 0x01,
	BSGR_STATUS_STREAM_IDLE = 0x02,
	BSGR_STATUS_STREAM_ACTIVE = 0x03,
	BSGR_STATUS_TSYNC_UPDATED = 0x04,
	BSGR_STATUS_DFU_READY = 0x05,
	BSGR_STATUS_DFU_ACTIVE = 0x06,
	BSGR_STATUS_DFU_DENIED = 0x07,
	BSGR_STATUS_IMU_STUB = 0x20,
	BSGR_STATUS_UWB_STUB = 0x21,
	BSGR_STATUS_PARSE_WARNING = 0x22,
	BSGR_STATUS_ERROR = 0x7f,
};

enum bsgr_origin {
	BSGR_ORIGIN_SYSTEM = 0x00,
	BSGR_ORIGIN_IMU = 0x01,
	BSGR_ORIGIN_UWB = 0x02,
	BSGR_ORIGIN_BLE = 0x03,
	BSGR_ORIGIN_USB = 0x04,
	BSGR_ORIGIN_DFU = 0x05,
};

enum bsgr_frame_flags {
	BSGR_FRAME_FLAG_ACK_REQUIRED = BIT(0),
	BSGR_FRAME_FLAG_FRAGMENTED = BIT(1),
	BSGR_FRAME_FLAG_HOST_TIMED = BIT(2),
	BSGR_FRAME_FLAG_STUB_DATA = BIT(3),
};

enum bsgr_stream_flags {
	BSGR_STREAM_FLAG_IMU = BIT(0),
	BSGR_STREAM_FLAG_UWB = BIT(1),
};

enum bsgr_parser_flags {
	BSGR_PARSER_FLAG_PARTIAL = BIT(0),
	BSGR_PARSER_FLAG_CANDIDATE_FRAME = BIT(1),
	BSGR_PARSER_FLAG_CHECKSUM_UNVERIFIED = BIT(2),
	BSGR_PARSER_FLAG_STUB_DECODE = BIT(3),
};

struct bsgr_frame_header {
	uint8_t version;
	uint8_t frame_type;
	uint8_t flags;
	uint8_t payload_len;
	uint16_t device_id;
	uint16_t seq;
} __attribute__((packed));

struct bsgr_imu_batch_meta {
	uint8_t stream_flags;
	uint8_t sample_count;
	uint8_t parser_flags;
	uint8_t reserved;
	uint32_t host_capture_ticks;
	uint16_t sample_stride;
	uint16_t raw_bytes;
} __attribute__((packed));

struct bsgr_uwb_report_meta {
	uint8_t stream_flags;
	uint8_t record_count;
	uint8_t parser_flags;
	uint8_t reserved;
	uint32_t host_capture_ticks;
	uint16_t record_stride;
	uint16_t raw_bytes;
} __attribute__((packed));

struct bsgr_tsync_payload {
	uint8_t role;
	uint8_t reserved0;
	uint16_t session_id;
	int32_t host_time_offset_ms;
	uint32_t reference_ticks;
} __attribute__((packed));

struct bsgr_cmd_payload {
	uint8_t opcode;
	uint8_t arg_len;
	uint16_t request_id;
	uint32_t arg0;
} __attribute__((packed));

struct bsgr_cmd_ack_payload {
	uint8_t opcode;
	uint8_t result;
	uint16_t request_id;
	uint32_t detail;
} __attribute__((packed));

struct bsgr_status_payload {
	uint8_t status_code;
	uint8_t origin;
	uint16_t detail;
	uint32_t value;
} __attribute__((packed));

BUILD_ASSERT(sizeof(struct bsgr_frame_header) == 8, "Unexpected bsgr_frame_header size");
BUILD_ASSERT(sizeof(struct bsgr_imu_batch_meta) == 12, "Unexpected bsgr_imu_batch_meta size");
BUILD_ASSERT(sizeof(struct bsgr_uwb_report_meta) == 12, "Unexpected bsgr_uwb_report_meta size");
BUILD_ASSERT(sizeof(struct bsgr_tsync_payload) == 12, "Unexpected bsgr_tsync_payload size");
BUILD_ASSERT(sizeof(struct bsgr_cmd_payload) == 8, "Unexpected bsgr_cmd_payload size");
BUILD_ASSERT(sizeof(struct bsgr_cmd_ack_payload) == 8, "Unexpected bsgr_cmd_ack_payload size");
BUILD_ASSERT(sizeof(struct bsgr_status_payload) == 8, "Unexpected bsgr_status_payload size");

static inline void bsgr_frame_header_init(struct bsgr_frame_header *hdr,
					  uint8_t frame_type,
					  uint8_t flags,
					  uint8_t payload_len,
					  uint16_t device_id,
					  uint16_t seq)
{
	if (hdr == NULL) {
		return;
	}

	hdr->version = BSGR_PROTOCOL_VERSION;
	hdr->frame_type = frame_type;
	hdr->flags = flags;
	hdr->payload_len = payload_len;
	hdr->device_id = device_id;
	hdr->seq = seq;
}

static inline bool bsgr_frame_header_is_valid(const struct bsgr_frame_header *hdr)
{
	if (hdr == NULL) {
		return false;
	}

	if (hdr->version != BSGR_PROTOCOL_VERSION) {
		return false;
	}

	return hdr->payload_len <= BSGR_MAX_FRAME_PAYLOAD_LEN;
}

/*
 * Locked semantics:
 *
 * - The universal header remains minimal and intentionally contains no
 *   absolute timestamp. Host-relative timing lives in payload metadata.
 * - device_id is the canonical machine identity. Visible names remain labels.
 * - seq is session-scoped only and must never be persisted to NVS.
 * - SET_IMU_PHASE is host-side timestamp phase compensation only.
 * - JY61P and UWB binary payload details remain hardware-gated; the IMU/UWB
 *   metadata above is stable, while raw transport bytes after the metadata
 *   remain intentionally opaque until the authoritative baseline is supplied.
 */

#ifdef __cplusplus
}
#endif

#endif /* BSGR_PROTOCOL_H_ */
