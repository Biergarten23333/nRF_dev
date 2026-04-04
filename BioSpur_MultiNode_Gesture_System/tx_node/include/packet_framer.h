#ifndef BSGR_TX_PACKET_FRAMER_H_
#define BSGR_TX_PACKET_FRAMER_H_

#include <stddef.h>
#include <stdint.h>

#include "bsgr_protocol.h"

struct bsgr_tx_frame {
	size_t len;
	uint8_t data[BSGR_MAX_FRAME_LEN];
};

struct bsgr_imu_sample;
struct bsgr_uwb_record;

int packet_framer_build_status(uint16_t device_id,
			       uint16_t seq,
			       const struct bsgr_status_payload *payload,
			       struct bsgr_tx_frame *frame);
int packet_framer_build_cmd_ack(uint16_t device_id,
				uint16_t seq,
				const struct bsgr_cmd_ack_payload *payload,
				struct bsgr_tx_frame *frame);
int packet_framer_build_tsync(uint16_t device_id,
			      uint16_t seq,
			      const struct bsgr_tsync_payload *payload,
			      struct bsgr_tx_frame *frame);
int packet_framer_build_imu(uint16_t device_id,
			    uint16_t seq,
			    const struct bsgr_imu_batch_meta *meta,
			    const uint8_t *raw_payload,
			    size_t raw_len,
			    struct bsgr_tx_frame *frame);
int packet_framer_build_uwb(uint16_t device_id,
			    uint16_t seq,
			    const struct bsgr_uwb_report_meta *meta,
			    const uint8_t *raw_payload,
			    size_t raw_len,
			    struct bsgr_tx_frame *frame);

#endif /* BSGR_TX_PACKET_FRAMER_H_ */
