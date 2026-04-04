#ifndef BSGR_TX_BLE_LINK_H_
#define BSGR_TX_BLE_LINK_H_

#include <stdint.h>

int ble_link_init(uint16_t device_id);
int ble_link_send_status(const uint8_t *payload, uint8_t payload_len);

#endif /* BSGR_TX_BLE_LINK_H_ */
