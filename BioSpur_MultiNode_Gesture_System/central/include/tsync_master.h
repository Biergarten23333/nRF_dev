#ifndef BSGR_CENTRAL_TSYNC_MASTER_H_
#define BSGR_CENTRAL_TSYNC_MASTER_H_

#include <stdint.h>

#include "bsgr_protocol.h"

struct bsgr_tsync_master_state {
	int64_t epoch_ms;
	uint16_t session_id;
	uint16_t request_id;
};

void tsync_master_init(void);
void tsync_master_set_epoch_ms(int64_t epoch_ms);
int64_t tsync_master_get_epoch_ms(void);
uint16_t tsync_master_next_request_id(void);
uint16_t tsync_master_get_session_id(void);
void tsync_master_fill_payload(struct bsgr_tsync_payload *payload, uint32_t reference_ticks);

#endif /* BSGR_CENTRAL_TSYNC_MASTER_H_ */
