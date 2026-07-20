/*
 * Read-only BLE scanner used to identify the Fusion PCB DWM1001C.
 *
 * It deliberately has no central role and cannot connect to or modify a peer.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdbool.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/gap.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/kernel.h>
#include <zephyr/net_buf.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#define MAX_SEEN 32
#define NAME_LEN 31

struct adv_fields {
	char name[NAME_LEN + 1];
	bool has_name;
	bool has_nus;
	bool has_mfg;
	uint8_t mfg[24];
	uint8_t mfg_len;
};

struct seen_peer {
	bt_addr_le_t addr;
	bool used;
	int8_t best_rssi;
	char name[NAME_LEN + 1];
};

static struct seen_peer seen[MAX_SEEN];

static bool parse_field(struct bt_data *data, void *user_data)
{
	struct adv_fields *fields = user_data;

	switch (data->type) {
	case BT_DATA_NAME_SHORTENED:
	case BT_DATA_NAME_COMPLETE: {
		size_t len = MIN(data->data_len, NAME_LEN);

		memcpy(fields->name, data->data, len);
		fields->name[len] = '\0';
		fields->has_name = true;
		break;
	}
	case BT_DATA_MANUFACTURER_DATA:
		fields->mfg_len = MIN(data->data_len, sizeof(fields->mfg));
		memcpy(fields->mfg, data->data, fields->mfg_len);
		fields->has_mfg = true;
		break;
	case BT_DATA_UUID128_SOME:
	case BT_DATA_UUID128_ALL:
		/* Nordic UART Service UUID in little-endian advertising order. */
		for (size_t i = 0; i + 16U <= data->data_len; i += 16U) {
			static const uint8_t nus_uuid_le[16] = {
				0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
				0x93, 0xf3, 0xa3, 0xb5, 0x01, 0x00, 0x40, 0x6e,
			};

			if (memcmp(&data->data[i], nus_uuid_le,
				   sizeof(nus_uuid_le)) == 0) {
				fields->has_nus = true;
			}
		}
		break;
	default:
		break;
	}

	return true;
}

static struct seen_peer *find_or_add(const bt_addr_le_t *addr)
{
	struct seen_peer *free_slot = NULL;

	for (size_t i = 0; i < ARRAY_SIZE(seen); ++i) {
		if (seen[i].used && bt_addr_le_cmp(addr, &seen[i].addr) == 0) {
			return &seen[i];
		}
		if (!seen[i].used && free_slot == NULL) {
			free_slot = &seen[i];
		}
	}

	if (free_slot != NULL) {
		free_slot->used = true;
		free_slot->best_rssi = INT8_MIN;
		bt_addr_le_copy(&free_slot->addr, addr);
	}
	return free_slot;
}

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	struct adv_fields fields = { 0 };
	struct seen_peer *peer;
	char addr_str[BT_ADDR_LE_STR_LEN];

	bt_data_parse(ad, parse_field, &fields);
	if (!fields.has_name && !fields.has_nus) {
		return;
	}

	peer = find_or_add(addr);
	if (peer == NULL) {
		return;
	}

	if (rssi <= peer->best_rssi &&
	    (!fields.has_name || strcmp(peer->name, fields.name) == 0)) {
		return;
	}

	peer->best_rssi = MAX(peer->best_rssi, rssi);
	if (fields.has_name) {
		strncpy(peer->name, fields.name, sizeof(peer->name) - 1U);
	}
	bt_addr_le_to_str(addr, addr_str, sizeof(addr_str));

	printk("DWM_SCAN name=%s addr=%s rssi=%d type=0x%02x nus=%u",
	       fields.has_name ? fields.name : "-",
	       addr_str, rssi, type, fields.has_nus ? 1U : 0U);
	if (fields.has_mfg) {
		printk(" mfg=");
		for (uint8_t i = 0; i < fields.mfg_len; ++i) {
			printk("%02x", fields.mfg[i]);
		}
	}
	printk("\n");
}

int main(void)
{
	static const struct bt_le_scan_param scan_params = {
		.type = BT_LE_SCAN_TYPE_ACTIVE,
		.options = BT_LE_SCAN_OPT_NONE,
		.interval = BT_GAP_SCAN_FAST_INTERVAL,
		.window = BT_GAP_SCAN_FAST_WINDOW,
	};
	int err;

	printk("DWM_SCANNER marker=dwm-scanner-v2 mode=scan-only no-connect all-named\n");
	err = bt_enable(NULL);
	if (err != 0) {
		printk("DWM_SCANNER_FAIL step=bt_enable err=%d\n", err);
		return err;
	}

	err = bt_le_scan_start(&scan_params, device_found);
	if (err != 0) {
		printk("DWM_SCANNER_FAIL step=scan_start err=%d\n", err);
		return err;
	}
	printk("DWM_SCANNER_READY\n");

	for (;;) {
		k_sleep(K_SECONDS(1));
	}
}
