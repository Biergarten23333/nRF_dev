#ifndef BSGR_TX_DFU_AUTH_H_
#define BSGR_TX_DFU_AUTH_H_

#include <stdbool.h>
#include <stdint.h>

#include "bsgr_protocol.h"

struct bsgr_dfu_auth_state {
	bool prepared;
	bool authorized;
	bool active;
	uint16_t session_id;
	uint16_t last_request_id;
	enum bsgr_cmd_result last_result;
};

void dfu_auth_init(void);
void dfu_auth_prepare(uint16_t request_id);
void dfu_auth_set_authorized(bool authorized, uint16_t request_id);
void dfu_auth_begin(uint16_t session_id);
void dfu_auth_end(void);
bool dfu_auth_is_permitted(void);
const struct bsgr_dfu_auth_state *dfu_auth_state_get(void);

#endif /* BSGR_TX_DFU_AUTH_H_ */
