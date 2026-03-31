#ifndef BIOSPUR_ANCHOR_BLE_CTRL_H_
#define BIOSPUR_ANCHOR_BLE_CTRL_H_

#include <stdbool.h>
#include <stdint.h>

#include "anchor_config.h"

struct anchor_ble_ctrl_boot_info {
    anchor_config_t active_cfg;
    bool active_cfg_valid;
    uint8_t runtime_anchor_id_cfg; /* 0=unassigned, 1..8=A..H */
    uint8_t runtime_role;
    uint8_t device_uuid[16];
    uint8_t mcu_uid[8];
    char bs_code[7];
};

int anchor_ble_ctrl_init(const struct anchor_ble_ctrl_boot_info *info);
void anchor_ble_ctrl_set_busy(bool busy);
void anchor_ble_ctrl_set_runtime(uint8_t runtime_anchor_id_cfg, uint8_t runtime_role,
                                 bool active_cfg_valid);

#endif
