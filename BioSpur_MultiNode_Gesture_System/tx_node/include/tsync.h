#ifndef BSGR_TX_TSYNC_H_
#define BSGR_TX_TSYNC_H_

#include <stdint.h>

#include "bsgr_protocol.h"

struct bsgr_tsync_state {
	int32_t host_offset_ms;
	uint16_t session_id;
	uint16_t last_request_id;
};

void tsync_init(void);
void tsync_set_host_offset_ms(int32_t offset_ms);
int32_t tsync_get_host_offset_ms(void);
uint16_t tsync_next_request_id(void);
uint16_t tsync_get_session_id(void);
void tsync_advance_session(void);
void tsync_fill_payload(struct bsgr_tsync_payload *payload, uint8_t role, uint32_t reference_ticks);

#endif /* BSGR_TX_TSYNC_H_ */
