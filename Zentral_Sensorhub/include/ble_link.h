#ifndef BLE_LINK_H
#define BLE_LINK_H

#include <stdint.h>
#include <stdbool.h>
#include <zephyr/sys/atomic.h>

#include "app_config.h"

bool ble_enqueue_frame_norm(const uint8_t *buf, uint16_t len);
bool ble_enqueue_frame_hi(const uint8_t *buf, uint16_t len);

/* BLE init (bt_enable + NUS + 广播)，成功返回 0 */
int ble_init(void);

/* 统计信息（在 DEBUG_LOG_ENABLE 时，可用于 main 打印） */
extern atomic_t ble_drops_norm;
extern atomic_t ble_drops_hi;

#endif /* BLE_LINK_H */
