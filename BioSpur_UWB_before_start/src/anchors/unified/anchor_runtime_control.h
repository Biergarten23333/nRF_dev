#ifndef BIOSPUR_ANCHOR_RUNTIME_CONTROL_H_
#define BIOSPUR_ANCHOR_RUNTIME_CONTROL_H_

#include <stdbool.h>

void anchor_runtime_request_stop(void);
void anchor_runtime_clear_stop(void);
bool anchor_runtime_stop_requested(void);

#endif /* BIOSPUR_ANCHOR_RUNTIME_CONTROL_H_ */
