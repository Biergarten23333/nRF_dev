#ifndef BSGR_TX_BLE_LINK_H_
#define BSGR_TX_BLE_LINK_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "packet_framer.h"

int ble_link_init(uint16_t device_id);
int ble_link_submit_frame(const struct bsgr_tx_frame *frame);
void ble_link_schedule_drain(void);
bool ble_link_is_connected(void);

#endif /* BSGR_TX_BLE_LINK_H_ */
