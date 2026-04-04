#include <errno.h>
#include <string.h>

#include "imu_uart_driver.h"
#include "packet_framer.h"
#include "uwb_driver.h"

static int packet_framer_build(uint16_t device_id,
			       uint16_t seq,
			       uint8_t frame_type,
			       uint8_t flags,
			       const void *meta,
			       size_t meta_len,
			       const uint8_t *raw_payload,
			       size_t raw_len,
			       struct bsgr_tx_frame *frame)
{
	struct bsgr_frame_header *hdr;
	size_t payload_len;

	if ((frame == NULL) || (meta == NULL)) {
		return -EINVAL;
	}

	payload_len = meta_len + raw_len;
	if (payload_len > BSGR_MAX_FRAME_PAYLOAD_LEN) {
		return -EMSGSIZE;
	}

	hdr = (struct bsgr_frame_header *)frame->data;
	bsgr_frame_header_init(hdr, frame_type, flags, (uint8_t)payload_len, device_id, seq);

	memcpy(frame->data + sizeof(*hdr), meta, meta_len);
	if ((raw_payload != NULL) && (raw_len > 0U)) {
		memcpy(frame->data + sizeof(*hdr) + meta_len, raw_payload, raw_len);
	}

	frame->len = sizeof(*hdr) + payload_len;
	return 0;
}

int packet_framer_build_status(uint16_t device_id,
			       uint16_t seq,
			       const struct bsgr_status_payload *payload,
			       struct bsgr_tx_frame *frame)
{
	return packet_framer_build(device_id, seq, BSGR_FRAME_TYPE_STATUS, 0U,
				   payload, sizeof(*payload), NULL, 0U, frame);
}

int packet_framer_build_cmd_ack(uint16_t device_id,
				uint16_t seq,
				const struct bsgr_cmd_ack_payload *payload,
				struct bsgr_tx_frame *frame)
{
	return packet_framer_build(device_id, seq, BSGR_FRAME_TYPE_CMD_ACK,
				   BSGR_FRAME_FLAG_ACK_REQUIRED, payload, sizeof(*payload),
				   NULL, 0U, frame);
}

int packet_framer_build_tsync(uint16_t device_id,
			      uint16_t seq,
			      const struct bsgr_tsync_payload *payload,
			      struct bsgr_tx_frame *frame)
{
	return packet_framer_build(device_id, seq, BSGR_FRAME_TYPE_TSYNC,
				   BSGR_FRAME_FLAG_HOST_TIMED, payload, sizeof(*payload),
				   NULL, 0U, frame);
}

int packet_framer_build_imu(uint16_t device_id,
			    uint16_t seq,
			    const struct bsgr_imu_batch_meta *meta,
			    const uint8_t *raw_payload,
			    size_t raw_len,
			    struct bsgr_tx_frame *frame)
{
	return packet_framer_build(device_id, seq, BSGR_FRAME_TYPE_IMU_BATCH,
				   BSGR_FRAME_FLAG_HOST_TIMED, meta, sizeof(*meta),
				   raw_payload, raw_len, frame);
}

int packet_framer_build_uwb(uint16_t device_id,
			    uint16_t seq,
			    const struct bsgr_uwb_report_meta *meta,
			    const uint8_t *raw_payload,
			    size_t raw_len,
			    struct bsgr_tx_frame *frame)
{
	return packet_framer_build(device_id, seq, BSGR_FRAME_TYPE_UWB_REPORT,
				   BSGR_FRAME_FLAG_HOST_TIMED | BSGR_FRAME_FLAG_STUB_DATA,
				   meta, sizeof(*meta), raw_payload, raw_len, frame);
}
