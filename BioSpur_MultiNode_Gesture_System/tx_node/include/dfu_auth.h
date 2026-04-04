#ifndef BSGR_TX_DFU_AUTH_H_
#define BSGR_TX_DFU_AUTH_H_

#include <stdbool.h>

void dfu_auth_init(void);
bool dfu_auth_is_permitted(void);

#endif /* BSGR_TX_DFU_AUTH_H_ */
