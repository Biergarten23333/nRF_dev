/* app_ble.h
 *
 * BLE / NUS 聚合模块对外接口
 */

#ifndef APP_BLE_H
#define APP_BLE_H

#include <stdint.h>

/* 由 main.c 提供，用于 BLE 模块内部打印 */
void cdc_printf(const char *fmt, ...);

/* 初始化 BLE 模块：NUS client、扫描模块、连接回调等 */
void app_ble_init(void);

/* 扫描 / 连接控制 */
void app_ble_start_scan(void);             /* 原 start_scan() */
void app_ble_connect_all_candidates(void); /* 原 connect_all_candidates() */

/* TSYNC / RSLOT 命令接口 */
void app_ble_cmd_tsync(uint64_t host_ms);                   /* 原 send_cmd_T_to_all() */
void app_ble_cmd_rslot(int slot, uint16_t fps, uint16_t phase_ms); /* 原 send_cmd_R_to_slot() */

#endif /* APP_BLE_H */
