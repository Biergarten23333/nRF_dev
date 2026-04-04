#ifndef BSGR_TX_TSYNC_H_
#define BSGR_TX_TSYNC_H_

#include <stdint.h>

void tsync_init(void);
void tsync_set_host_offset_ms(int32_t offset_ms);
int32_t tsync_get_host_offset_ms(void);

#endif /* BSGR_TX_TSYNC_H_ */
