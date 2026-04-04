#ifndef BSGR_CENTRAL_TSYNC_MASTER_H_
#define BSGR_CENTRAL_TSYNC_MASTER_H_

#include <stdint.h>

void tsync_master_init(void);
void tsync_master_set_epoch_ms(int64_t epoch_ms);
int64_t tsync_master_get_epoch_ms(void);

#endif /* BSGR_CENTRAL_TSYNC_MASTER_H_ */
