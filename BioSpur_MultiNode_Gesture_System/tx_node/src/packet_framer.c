#include <errno.h>
#include <string.h>

#include "packet_framer.h"

int packet_framer_build_control(uint16_t device_id,
				  uint16_t seq,
				  uint8_t opcode,
				  const uint8_t *payload,
				  uint8_t payload_len,
				  uint8_t *out,
				  uint8_t *out_len)
{
	struct bsgr_frame_header *hdr;
	uint8_t total_len;

	if ((out == NULL) || (out_len == NULL)) {
		return -EINVAL;
	}

	if (payload_len > BSGR_MAX_FRAME_PAYLOAD_LEN - 1U) {
		return -EMSGSIZE;
	}

	total_len = sizeof(struct bsgr_frame_header) + 1U + payload_len;
	hdr = (struct bsgr_frame_header *)out;

	bsgr_frame_header_init(hdr, BSGR_FRAME_TYPE_CONTROL, 0U, payload_len + 1U, device_id, seq);
	out[sizeof(struct bsgr_frame_header)] = opcode;

	if ((payload != NULL) && (payload_len > 0U)) {
		memcpy(&out[sizeof(struct bsgr_frame_header) + 1U], payload, payload_len);
	}

	*out_len = total_len;
	return 0;
}
