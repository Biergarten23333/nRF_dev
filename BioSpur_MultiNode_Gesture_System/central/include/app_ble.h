#ifndef BSGR_CENTRAL_APP_BLE_H_
#define BSGR_CENTRAL_APP_BLE_H_

#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>

#include <zephyr/bluetooth/addr.h>
#include <zephyr/bluetooth/conn.h>

#include <bluetooth/services/nus_client.h>

struct bsgr_central_peer {
	bool in_use;
	bool identified;
	uint16_t device_id;
	uint16_t last_seq;
	int8_t rssi;
	uint32_t last_seen_ticks;
	bt_addr_le_t addr;
	struct bt_conn *conn;
	struct bt_nus_client nus_client;
};

int app_ble_init(void);
int app_ble_start_scan(void);
int app_ble_stop_scan(void);
void app_ble_process(void);
const struct bsgr_central_peer *app_ble_peers_get(size_t *count);

#endif /* BSGR_CENTRAL_APP_BLE_H_ */
