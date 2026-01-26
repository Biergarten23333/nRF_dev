#ifndef APP_BLE_H
#define APP_BLE_H

#include <zephyr/kernel.h>
#include <stdint.h>
#include <stdbool.h>

/* Initialize Bluetooth central + NUS clients + bt_scan. */
int app_ble_init(void);

/* Start/stop scanning for TX devices ("eFxxxx" names). */
int app_ble_start_scan(void);
int app_ble_stop_scan(void);

/* Use cached candidates (from last scan) to connect.
 * Connections are created sequentially; GATT discovery for NUS is
 * internally serialized to avoid -EALREADY issues.
 */
int app_ble_connect_whitelist(void);

/* Disconnect all active connections. */
void app_ble_disconnect_all(void);

/* Return number of active connections. */
int app_ble_get_conn_count(void);

/* UWB/IMU control commands over NUS (TSYNC / RSLOT). */
void app_ble_tsync_all(uint64_t host_ms);
void app_ble_rslot(int slot, uint16_t fps, uint16_t phase_ms);

#endif /* APP_BLE_H */
