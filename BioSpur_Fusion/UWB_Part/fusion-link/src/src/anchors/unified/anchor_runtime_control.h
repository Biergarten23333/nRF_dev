#ifndef BIOSPUR_ANCHOR_RUNTIME_CONTROL_H_
#define BIOSPUR_ANCHOR_RUNTIME_CONTROL_H_

#include <stdbool.h>
#include <stdint.h>

void anchor_runtime_request_stop(void);
void anchor_runtime_clear_stop(void);
bool anchor_runtime_stop_requested(void);
void anchor_runtime_request_dfu(void);
void anchor_runtime_clear_dfu(void);
bool anchor_runtime_dfu_requested(void);
void anchor_runtime_request_role_switch(uint8_t role, uint32_t master_sweeps);
uint8_t anchor_runtime_requested_role(void);
uint32_t anchor_runtime_requested_master_sweeps(void);
void anchor_runtime_clear_role_switch(void);

#endif /* BIOSPUR_ANCHOR_RUNTIME_CONTROL_H_ */
