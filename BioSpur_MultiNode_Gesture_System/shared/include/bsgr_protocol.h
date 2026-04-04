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

#define BSGR_PROTOCOL_VERSION 0x01u
#define BSGR_MAX_FRAME_PAYLOAD_LEN 240u

enum bsgr_frame_type {
	BSGR_FRAME_TYPE_STATUS = 0x01,
	BSGR_FRAME_TYPE_SENSOR = 0x02,
	BSGR_FRAME_TYPE_CONTROL = 0x03,
	BSGR_FRAME_TYPE_EVENT = 0x04,
};

enum bsgr_control_opcode {
	BSGR_CTRL_NOP = 0x00,
	BSGR_CTRL_SET_IMU_PHASE = 0x01,
	BSGR_CTRL_TSYNC = 0x02,
	BSGR_CTRL_DFU_PREPARE = 0x03,
};

enum bsgr_frame_flags {
	BSGR_FRAME_FLAG_ACK_REQUIRED = BIT(0),
	BSGR_FRAME_FLAG_FRAGMENTED = BIT(1),
};

struct bsgr_frame_header {
	uint8_t version;
	uint8_t frame_type;
	uint8_t flags;
	uint8_t payload_len;
	uint16_t device_id;
	uint16_t seq;
} __attribute__((packed));

struct bsgr_control_tsync_payload {
	int32_t host_time_offset_ms;
} __attribute__((packed));

struct bsgr_control_set_imu_phase_payload {
	int16_t host_phase_compensation_ms;
} __attribute__((packed));

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
 * Protocol freeze-gate notes:
 *
 * - The universal header intentionally does not contain an absolute timestamp.
 * - device_id is mandatory.
 * - seq is session-scoped only and must not be persisted to NVS.
 * - SET_IMU_PHASE is host-side compensation metadata, not physical phase lock.
 * - Payload semantics outside the locked control structs above remain deferred.
 */

#ifdef __cplusplus
}
#endif

#endif /* BSGR_PROTOCOL_H_ */
