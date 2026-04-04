#ifndef BSGR_CENTRAL_APP_BLE_H_
#define BSGR_CENTRAL_APP_BLE_H_

#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>

#include <zephyr/bluetooth/addr.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/kernel.h>
#include <zephyr/mgmt/mcumgr/mgmt/mgmt_defines.h>

#include <bluetooth/services/nus_client.h>

#define BSGR_IMG_HASH_LEN 32
#define BSGR_IMG_VER_MAX_STR_LEN (sizeof("255.255.65535.4294967295"))

struct bsgr_mcumgr_image_list_flags {
	bool bootable : 1;
	bool pending : 1;
	bool confirmed : 1;
	bool active : 1;
	bool permanent : 1;
};

struct bsgr_mcumgr_image_data {
	uint32_t slot_num;
	uint32_t img_num;
	char hash[BSGR_IMG_HASH_LEN];
	char version[BSGR_IMG_VER_MAX_STR_LEN + 1];
	struct bsgr_mcumgr_image_list_flags flags;
};

struct bsgr_mcumgr_image_state {
	enum mcumgr_err_t status;
	int image_list_length;
	struct bsgr_mcumgr_image_data *image_list;
};

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
int app_ble_ota_connect(const char *target_name, k_timeout_t timeout);
void app_ble_ota_disconnect(void);
bool app_ble_ota_ready(void);
int app_ble_ota_upload_start(size_t image_size);
int app_ble_ota_upload_chunk(const uint8_t *data, size_t len, size_t *remote_offset);
int app_ble_ota_read_images(struct bsgr_mcumgr_image_state *res_buf,
			    struct bsgr_mcumgr_image_data *image_list,
			    size_t image_list_size);
int app_ble_ota_mark_test(const char *hash);
int app_ble_ota_reset_target(void);
const char *app_ble_ota_target_name(void);

#endif /* BSGR_CENTRAL_APP_BLE_H_ */
