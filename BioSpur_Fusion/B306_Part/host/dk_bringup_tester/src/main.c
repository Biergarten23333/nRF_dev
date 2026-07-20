/*
 * B306 first-flash BLE/SMP bring-up tester for nRF52840 DK 683234364.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <errno.h>
#include <stdbool.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/net_buf.h>
#include <zephyr/sys/printk.h>

#define TARGET_NAME_PREFIX "BSF"
#define TARGET_NAME_LEN 7

static struct bt_uuid_128 smp_service_uuid =
	BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x8d53dc1d, 0x1db7, 0x4cd3,
					   0x868b, 0x8a527460aa84));
static struct bt_uuid_128 smp_characteristic_uuid =
	BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xda2e7828, 0xfbce, 0x4e01,
					   0xae9e, 0x261174997c48));

static struct bt_conn *target_conn;
static struct bt_gatt_exchange_params exchange_params;
static struct bt_gatt_discover_params discover_params;
static struct bt_gatt_subscribe_params subscribe_params;

static bt_addr_le_t candidate_addr;
static bool candidate_valid;
static bool candidate_has_smp;
static bool candidate_has_name;
static bool connecting;
static bool test_passed;
static int8_t candidate_rssi;
static char candidate_name[TARGET_NAME_LEN + 1];
static uint16_t service_end_handle;
static uint16_t characteristic_value_handle;

enum discovery_stage {
	DISCOVERY_SERVICE,
	DISCOVERY_CHARACTERISTIC,
	DISCOVERY_CCC,
};

static enum discovery_stage discovery_stage;

struct advertising_fields {
	bool has_smp;
	bool has_name;
	char name[TARGET_NAME_LEN + 1];
};

static void start_scan(void);
static void start_smp_discovery(struct bt_conn *conn);

static bool advertising_field(struct bt_data *data, void *user_data)
{
	struct advertising_fields *fields = user_data;
	size_t copy_len;

	switch (data->type) {
	case BT_DATA_NAME_SHORTENED:
	case BT_DATA_NAME_COMPLETE:
		copy_len = MIN(data->data_len, TARGET_NAME_LEN);
		memcpy(fields->name, data->data, copy_len);
		fields->name[copy_len] = '\0';
		fields->has_name =
			(data->data_len == TARGET_NAME_LEN) &&
			(strncmp(fields->name, TARGET_NAME_PREFIX,
				 strlen(TARGET_NAME_PREFIX)) == 0);
		break;

	case BT_DATA_UUID128_SOME:
	case BT_DATA_UUID128_ALL:
		for (size_t offset = 0;
		     offset + sizeof(smp_service_uuid.val) <= data->data_len;
		     offset += sizeof(smp_service_uuid.val)) {
			struct bt_uuid_128 advertised_uuid;

			advertised_uuid.uuid.type = BT_UUID_TYPE_128;
			memcpy(advertised_uuid.val, &data->data[offset],
			       sizeof(advertised_uuid.val));
			if (bt_uuid_cmp(&advertised_uuid.uuid,
					&smp_service_uuid.uuid) == 0) {
				fields->has_smp = true;
				break;
			}
		}
		break;

	default:
		break;
	}

	return true;
}

static void connect_candidate(void)
{
	static const struct bt_le_conn_param conn_params = {
		.interval_min = 12, /* 15 ms */
		.interval_max = 24, /* 30 ms */
		.latency = 0,
		.timeout = 400, /* 4 s */
	};
	char addr[BT_ADDR_LE_STR_LEN];
	int err;

	if (connecting || target_conn || !candidate_has_smp ||
	    !candidate_has_name) {
		return;
	}

	connecting = true;
	bt_addr_le_to_str(&candidate_addr, addr, sizeof(addr));
	printk("B306_TARGET name=%s addr=%s rssi=%d\n",
	       candidate_name, addr, candidate_rssi);

	err = bt_le_scan_stop();
	if (err != 0 && err != -EALREADY) {
		printk("B306_BRINGUP_FAIL step=scan_stop err=%d\n", err);
		connecting = false;
		return;
	}

	err = bt_conn_le_create(&candidate_addr, BT_CONN_LE_CREATE_CONN,
				&conn_params, &target_conn);
	if (err != 0) {
		printk("B306_BRINGUP_FAIL step=connect_start err=%d\n", err);
		connecting = false;
		target_conn = NULL;
		start_scan();
	}
}

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	struct advertising_fields fields = { 0 };

	if (connecting || target_conn || test_passed) {
		return;
	}

	bt_data_parse(ad, advertising_field, &fields);
	if (!fields.has_smp && !fields.has_name) {
		return;
	}

	if (!candidate_valid ||
	    bt_addr_le_cmp(addr, &candidate_addr) != 0) {
		bt_addr_le_copy(&candidate_addr, addr);
		candidate_valid = true;
		candidate_has_smp = false;
		candidate_has_name = false;
		memset(candidate_name, 0, sizeof(candidate_name));
	}

	candidate_rssi = rssi;
	if (fields.has_smp) {
		candidate_has_smp = true;
	}
	if (fields.has_name) {
		candidate_has_name = true;
		memcpy(candidate_name, fields.name, sizeof(candidate_name));
	}

	connect_candidate();
}

static void start_scan(void)
{
	static const struct bt_le_scan_param scan_params = {
		.type = BT_LE_SCAN_TYPE_ACTIVE,
		.options = BT_LE_SCAN_OPT_NONE,
		.interval = BT_GAP_SCAN_FAST_INTERVAL,
		.window = BT_GAP_SCAN_FAST_WINDOW,
	};
	int err;

	candidate_valid = false;
	candidate_has_smp = false;
	candidate_has_name = false;
	memset(candidate_name, 0, sizeof(candidate_name));

	err = bt_le_scan_start(&scan_params, device_found);
	if (err != 0) {
		printk("B306_BRINGUP_FAIL step=scan_start err=%d\n", err);
		return;
	}

	printk("B306_SCAN_STARTED target=BSFxxxx smp=8D53DC1D-1DB7-4CD3-868B-8A527460AA84\n");
}

static uint8_t smp_notification(struct bt_conn *conn,
				struct bt_gatt_subscribe_params *params,
				const void *data, uint16_t length)
{
	if (data == NULL) {
		printk("B306_SMP_UNSUBSCRIBED\n");
		params->value_handle = 0;
		return BT_GATT_ITER_STOP;
	}

	printk("B306_SMP_NOTIFICATION len=%u\n", length);
	return BT_GATT_ITER_CONTINUE;
}

static uint8_t discover_smp(struct bt_conn *conn,
			    const struct bt_gatt_attr *attr,
			    struct bt_gatt_discover_params *params)
{
	int err;

	if (attr == NULL) {
		printk("B306_BRINGUP_FAIL step=discover_%u err=not_found\n",
		       discovery_stage);
		memset(params, 0, sizeof(*params));
		return BT_GATT_ITER_STOP;
	}

	switch (discovery_stage) {
	case DISCOVERY_SERVICE: {
		const struct bt_gatt_service_val *service = attr->user_data;

		service_end_handle = service->end_handle;
		printk("B306_SMP_SERVICE start=%u end=%u\n",
		       attr->handle, service_end_handle);

		discovery_stage = DISCOVERY_CHARACTERISTIC;
		discover_params.uuid = &smp_characteristic_uuid.uuid;
		discover_params.start_handle = attr->handle + 1;
		discover_params.end_handle = service_end_handle;
		discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;
		err = bt_gatt_discover(conn, &discover_params);
		if (err != 0) {
			printk("B306_BRINGUP_FAIL step=discover_characteristic_start err=%d\n",
			       err);
		}
		break;
	}

	case DISCOVERY_CHARACTERISTIC: {
		const struct bt_gatt_chrc *characteristic = attr->user_data;
		bool can_write;
		bool can_notify;

		characteristic_value_handle = characteristic->value_handle;
		can_write = (characteristic->properties &
			     BT_GATT_CHRC_WRITE_WITHOUT_RESP) != 0;
		can_notify =
			(characteristic->properties & BT_GATT_CHRC_NOTIFY) != 0;
		printk("B306_SMP_CHARACTERISTIC decl=%u value=%u props=0x%02x write_cmd=%u notify=%u\n",
		       attr->handle, characteristic_value_handle,
		       characteristic->properties, can_write, can_notify);

		if (!can_write || !can_notify) {
			printk("B306_BRINGUP_FAIL step=characteristic_properties\n");
			break;
		}

		discovery_stage = DISCOVERY_CCC;
		discover_params.uuid = BT_UUID_GATT_CCC;
		discover_params.start_handle = characteristic_value_handle + 1;
		discover_params.end_handle = service_end_handle;
		discover_params.type = BT_GATT_DISCOVER_DESCRIPTOR;
		err = bt_gatt_discover(conn, &discover_params);
		if (err != 0) {
			printk("B306_BRINGUP_FAIL step=discover_ccc_start err=%d\n",
			       err);
		}
		break;
	}

	case DISCOVERY_CCC:
		subscribe_params.notify = smp_notification;
		subscribe_params.value = BT_GATT_CCC_NOTIFY;
		subscribe_params.value_handle = characteristic_value_handle;
		subscribe_params.ccc_handle = attr->handle;
		err = bt_gatt_subscribe(conn, &subscribe_params);
		if (err != 0 && err != -EALREADY) {
			printk("B306_BRINGUP_FAIL step=subscribe err=%d\n", err);
			break;
		}

		test_passed = true;
		printk("B306_BRINGUP_PASS name=%s rssi=%d smp_service=1 smp_write_cmd=1 smp_notify=1 mtu=%u\n",
		       candidate_name, candidate_rssi, bt_gatt_get_mtu(conn));
		memset(params, 0, sizeof(*params));
		break;
	}

	return BT_GATT_ITER_STOP;
}

static void start_smp_discovery(struct bt_conn *conn)
{
	int err;

	memset(&discover_params, 0, sizeof(discover_params));
	discovery_stage = DISCOVERY_SERVICE;
	discover_params.uuid = &smp_service_uuid.uuid;
	discover_params.func = discover_smp;
	discover_params.start_handle = BT_ATT_FIRST_ATTRIBUTE_HANDLE;
	discover_params.end_handle = BT_ATT_LAST_ATTRIBUTE_HANDLE;
	discover_params.type = BT_GATT_DISCOVER_PRIMARY;

	err = bt_gatt_discover(conn, &discover_params);
	if (err != 0) {
		printk("B306_BRINGUP_FAIL step=discover_service_start err=%d\n",
		       err);
	}
}

static void mtu_exchanged(struct bt_conn *conn, uint8_t err,
			  struct bt_gatt_exchange_params *params)
{
	printk("B306_ATT_MTU value=%u err=%u\n", bt_gatt_get_mtu(conn), err);
	start_smp_discovery(conn);
}

static void connected(struct bt_conn *conn, uint8_t conn_err)
{
	static const struct bt_conn_le_phy_param phy_params = {
		.options = BT_CONN_LE_PHY_OPT_NONE,
		.pref_tx_phy = BT_GAP_LE_PHY_2M,
		.pref_rx_phy = BT_GAP_LE_PHY_2M,
	};
	char addr[BT_ADDR_LE_STR_LEN];
	int err;

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	if (conn_err != 0) {
		printk("B306_BRINGUP_FAIL step=connect_complete addr=%s hci=0x%02x\n",
		       addr, conn_err);
		if (target_conn != NULL) {
			bt_conn_unref(target_conn);
			target_conn = NULL;
		}
		connecting = false;
		start_scan();
		return;
	}

	if (conn != target_conn) {
		return;
	}

	connecting = false;
	printk("B306_CONNECTED addr=%s\n", addr);

	err = bt_conn_le_phy_update(conn, &phy_params);
	printk("B306_PHY_REQUEST preferred=2M err=%d\n", err);

	err = bt_conn_le_data_len_update(conn, BT_LE_DATA_LEN_PARAM_MAX);
	printk("B306_DLE_REQUEST max=251 err=%d\n", err);

	exchange_params.func = mtu_exchanged;
	err = bt_gatt_exchange_mtu(conn, &exchange_params);
	if (err != 0) {
		printk("B306_ATT_MTU_REQUEST err=%d\n", err);
		start_smp_discovery(conn);
	}
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];

	if (conn != target_conn) {
		return;
	}

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("B306_DISCONNECTED addr=%s reason=0x%02x\n", addr, reason);
	bt_conn_unref(target_conn);
	target_conn = NULL;
	connecting = false;

	if (!test_passed) {
		start_scan();
	}
}

static void le_phy_updated(struct bt_conn *conn,
			   struct bt_conn_le_phy_info *param)
{
	printk("B306_PHY_UPDATED tx=%u rx=%u\n", param->tx_phy, param->rx_phy);
}

static void le_data_len_updated(struct bt_conn *conn,
				struct bt_conn_le_data_len_info *info)
{
	printk("B306_DLE_UPDATED tx_len=%u tx_time=%u rx_len=%u rx_time=%u\n",
	       info->tx_max_len, info->tx_max_time,
	       info->rx_max_len, info->rx_max_time);
}

BT_CONN_CB_DEFINE(connection_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
	.le_phy_updated = le_phy_updated,
	.le_data_len_updated = le_data_len_updated,
};

int main(void)
{
	int err;

	printk("B306_DK_TESTER marker=dk-b306-bringup-v1 probe=683234364\n");
	err = bt_enable(NULL);
	if (err != 0) {
		printk("B306_BRINGUP_FAIL step=bt_enable err=%d\n", err);
		return 0;
	}

	printk("B306_DK_BLUETOOTH_READY\n");
	start_scan();

	while (true) {
		k_sleep(K_SECONDS(30));
		if (!test_passed && !target_conn && !connecting) {
			printk("B306_SCAN_WAITING no_matching_target_yet\n");
		}
	}

	return 0;
}
