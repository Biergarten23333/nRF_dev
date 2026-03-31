#ifndef BIOSPUR_UART_ROLE_SWITCH_H_
#define BIOSPUR_UART_ROLE_SWITCH_H_

#include <stdbool.h>
#include <stdint.h>

#include "anchor_config.h"

struct uart_role_switch_runtime_info {
    uint8_t active_anchor_id_runtime; /* 0..7 */
    uint8_t active_role;              /* enum anchor_runtime_role */
    uint8_t device_uuid[16];
    uint8_t mcu_uid[8];
    char bs_code[7];
    bool cfg_valid_on_boot;
};

int uart_role_switch_init(const struct uart_role_switch_runtime_info *info,
                          const anchor_config_t *initial_cfg,
                          bool initial_cfg_valid);
void uart_role_switch_set_ranging_active(bool active);

#endif
