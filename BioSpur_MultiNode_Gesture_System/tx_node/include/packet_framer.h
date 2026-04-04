#ifndef BSGR_TX_PACKET_FRAMER_H_
#define BSGR_TX_PACKET_FRAMER_H_

#include <stdint.h>

#include "bsgr_protocol.h"

int packet_framer_build_control(uint16_t device_id,
				  uint16_t seq,
				  uint8_t opcode,
				  const uint8_t *payload,
				  uint8_t payload_len,
				  uint8_t *out,
				  uint8_t *out_len);

#endif /* BSGR_TX_PACKET_FRAMER_H_ */
