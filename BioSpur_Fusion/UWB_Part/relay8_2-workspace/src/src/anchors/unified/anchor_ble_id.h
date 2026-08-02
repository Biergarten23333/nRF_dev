#ifndef BIOSPUR_ANCHOR_BLE_ID_H_
#define BIOSPUR_ANCHOR_BLE_ID_H_

#include <stdint.h>

int anchor_ble_id_start(uint8_t anchor_id_cfg, uint8_t role,
                        const uint8_t device_uuid[16],
                        const char *bs_code);
int anchor_ble_id_update_role(uint8_t role);

#endif
