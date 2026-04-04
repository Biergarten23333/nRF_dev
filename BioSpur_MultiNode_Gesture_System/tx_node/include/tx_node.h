#ifndef BSGR_TX_NODE_H_
#define BSGR_TX_NODE_H_

#include <stdint.h>

#define BSGR_TX_DEVICE_ID 0x1001u
#define BSGR_TX_SERVICE_PERIOD_MS 25u
#define BSGR_TX_STATUS_PERIOD_MS 1000u

int tx_main_init(void);

#endif /* BSGR_TX_NODE_H_ */
