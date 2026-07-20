/*
 * BioSpur Fusion Master diagnostic bridge for nRF52840 DK 683234364.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/net_buf.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include "biospur_fusion_ble.h"

#define TARGET_NAME_PREFIX "BSF"
#define TARGET_NAME_LEN 7

static struct bt_uuid_128 fusion_service_uuid =
	BT_UUID_INIT_128(BT_UUID_128_ENCODE(
		BSF_BLE_UUID_SERVICE_W32, BSF_BLE_UUID_W16_1,
		BSF_BLE_UUID_W16_2, BSF_BLE_UUID_W16_3, BSF_BLE_UUID_W48));
static struct bt_uuid_128 fusion_data_uuid =
	BT_UUID_INIT_128(BT_UUID_128_ENCODE(
		BSF_BLE_UUID_DATA_W32, BSF_BLE_UUID_W16_1,
		BSF_BLE_UUID_W16_2, BSF_BLE_UUID_W16_3, BSF_BLE_UUID_W48));
static struct bt_uuid_128 fusion_telemetry_uuid =
	BT_UUID_INIT_128(BT_UUID_128_ENCODE(
		BSF_BLE_UUID_TELEMETRY_W32, BSF_BLE_UUID_W16_1,
		BSF_BLE_UUID_W16_2, BSF_BLE_UUID_W16_3, BSF_BLE_UUID_W48));

static struct bt_conn *target_conn;
static struct bt_gatt_exchange_params exchange_params;
static struct bt_gatt_discover_params discover_params;
static struct bt_gatt_subscribe_params data_subscribe_params;
static struct bt_gatt_subscribe_params telemetry_subscribe_params;

static bt_addr_le_t candidate_addr;
static bool candidate_valid;
static bool candidate_has_service;
static bool candidate_has_name;
static bool connecting;
static bool bridge_ready;
static int8_t candidate_rssi;
static char candidate_name[TARGET_NAME_LEN + 1];
static uint16_t service_end_handle;
static uint16_t data_value_handle;
static uint16_t telemetry_value_handle;
static uint32_t received_packets;
static uint32_t malformed_packets;
static uint32_t reconnections;

enum discovery_stage {
	DISCOVERY_SERVICE,
	DISCOVERY_DATA_CHARACTERISTIC,
	DISCOVERY_DATA_CCC,
	DISCOVERY_TELEMETRY_CHARACTERISTIC,
	DISCOVERY_TELEMETRY_CCC,
};

static enum discovery_stage discovery_stage;

struct advertising_fields {
	bool has_service;
	bool has_name;
	char name[TARGET_NAME_LEN + 1];
};

static void start_scan(void);
static void start_fusion_discovery(struct bt_conn *conn);

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
		     offset + sizeof(fusion_service_uuid.val) <= data->data_len;
		     offset += sizeof(fusion_service_uuid.val)) {
			struct bt_uuid_128 advertised_uuid;

			advertised_uuid.uuid.type = BT_UUID_TYPE_128;
			memcpy(advertised_uuid.val, &data->data[offset],
			       sizeof(advertised_uuid.val));
			if (bt_uuid_cmp(&advertised_uuid.uuid,
					&fusion_service_uuid.uuid) == 0) {
				fields->has_service = true;
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

	if (connecting || target_conn || !candidate_has_service ||
	    !candidate_has_name) {
		return;
	}

	connecting = true;
	bt_addr_le_to_str(&candidate_addr, addr, sizeof(addr));
	printk("FUSION_TARGET name=%s addr=%s rssi=%d\n",
	       candidate_name, addr, candidate_rssi);

	err = bt_le_scan_stop();
	if (err != 0 && err != -EALREADY) {
		printk("FUSION_FAIL step=scan_stop err=%d\n", err);
		connecting = false;
		return;
	}

	err = bt_conn_le_create(&candidate_addr, BT_CONN_LE_CREATE_CONN,
				&conn_params, &target_conn);
	if (err != 0) {
		printk("FUSION_FAIL step=connect_start err=%d\n", err);
		connecting = false;
		target_conn = NULL;
		start_scan();
	}
}

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	struct advertising_fields fields = { 0 };

	ARG_UNUSED(type);

	if (connecting || target_conn) {
		return;
	}

	bt_data_parse(ad, advertising_field, &fields);
	if (!fields.has_service && !fields.has_name) {
		return;
	}

	if (!candidate_valid || bt_addr_le_cmp(addr, &candidate_addr) != 0) {
		bt_addr_le_copy(&candidate_addr, addr);
		candidate_valid = true;
		candidate_has_service = false;
		candidate_has_name = false;
		memset(candidate_name, 0, sizeof(candidate_name));
	}

	candidate_rssi = rssi;
	if (fields.has_service) {
		candidate_has_service = true;
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
	candidate_has_service = false;
	candidate_has_name = false;
	memset(candidate_name, 0, sizeof(candidate_name));

	err = bt_le_scan_start(&scan_params, device_found);
	if (err != 0 && err != -EALREADY) {
		printk("FUSION_FAIL step=scan_start err=%d\n", err);
		return;
	}

	printk("FUSION_SCAN_STARTED target=BSFxxxx service=7b120001\n");
}

static uint8_t data_notification(
	struct bt_conn *conn,
	struct bt_gatt_subscribe_params *params,
	const void *data, uint16_t length)
{
	bsf_ble_uwb_packet_t packet;
	char ranges[160];
	size_t used = 0u;
	uint64_t master_arrival_ms = (uint64_t)k_uptime_get();

	ARG_UNUSED(conn);

	if (data == NULL) {
		printk("FUSION_DATA_UNSUBSCRIBED\n");
		params->value_handle = 0;
		return BT_GATT_ITER_STOP;
	}

	if (length != sizeof(packet)) {
		++malformed_packets;
		printk("FUSION_MALFORMED kind=data len=%u expected=%u total=%u\n",
		       length, (unsigned int)sizeof(packet), malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}

	memcpy(&packet, data, sizeof(packet));
	if (packet.version != BSF_BLE_PROTOCOL_VERSION ||
	    packet.kind != BSF_BLE_KIND_UWB ||
	    packet.len != sizeof(packet)) {
		++malformed_packets;
		printk("FUSION_MALFORMED kind=data_header version=%u type=%u declared=%u total=%u\n",
		       packet.version, packet.kind, packet.len, malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}

	ranges[0] = '\0';
	for (unsigned int i = 0; i < BSL_MAX_ANCHORS; ++i) {
		int written;

		if (packet.uwb.anchor_id[i] == BSL_ANCHOR_NONE) {
			continue;
		}
		written = snprintf(&ranges[used], sizeof(ranges) - used,
				   "%s%u:%u",
				   used == 0u ? "" : ",",
				   packet.uwb.anchor_id[i],
				   packet.uwb.range_mm[i]);
		if (written < 0 || (size_t)written >= sizeof(ranges) - used) {
			break;
		}
		used += (size_t)written;
	}

	++received_packets;
	printk("FUSION_UWB master_ms=%llu node_ms=%u pkt=%u sweep=%u identity=%04X logical=%u valid=0x%02x flags=0x%02x ranges=%s\n",
	       master_arrival_ms,
	       packet.node_uptime_ms,
	       packet.node_sequence,
	       packet.uwb.sweep,
	       packet.uwb.identity_code,
	       packet.uwb.logical_tag_id,
	       packet.uwb.valid_mask,
	       packet.uwb.flags,
	       ranges[0] != '\0' ? ranges : "-");

	return BT_GATT_ITER_CONTINUE;
}

static uint8_t telemetry_notification(
	struct bt_conn *conn,
	struct bt_gatt_subscribe_params *params,
	const void *data, uint16_t length)
{
	bsf_ble_telemetry_t telemetry;

	ARG_UNUSED(conn);

	if (data == NULL) {
		printk("FUSION_TELEMETRY_UNSUBSCRIBED\n");
		params->value_handle = 0;
		return BT_GATT_ITER_STOP;
	}

	if (length != sizeof(telemetry)) {
		++malformed_packets;
		printk("FUSION_MALFORMED kind=telemetry len=%u expected=%u total=%u\n",
		       length, (unsigned int)sizeof(telemetry), malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}

	memcpy(&telemetry, data, sizeof(telemetry));
	if (telemetry.version != BSF_BLE_PROTOCOL_VERSION ||
	    telemetry.kind != BSF_BLE_KIND_TELEMETRY ||
	    telemetry.len != sizeof(telemetry)) {
		++malformed_packets;
		printk("FUSION_MALFORMED kind=telemetry_header version=%u type=%u declared=%u total=%u\n",
		       telemetry.version, telemetry.kind, telemetry.len,
		       malformed_packets);
		return BT_GATT_ITER_CONTINUE;
	}

	printk("FUSION_TELEMETRY node_ms=%u bytes=%u frames=%u crc=%u header=%u ring_drop=%u sweep_drop=%u duplicate=%u reorder=%u notify_ok=%u notify_drop=%u uart_restarts=%u uart_err=%d last_sweep=%u have=%u subscribed=%u master_rx=%u malformed=%u\n",
	       telemetry.node_uptime_ms,
	       telemetry.uart_bytes,
	       telemetry.valid_frames,
	       telemetry.crc_errors,
	       telemetry.header_errors,
	       telemetry.ring_dropped_bytes,
	       telemetry.dropped_sweeps,
	       telemetry.duplicate_sweeps,
	       telemetry.out_of_order_sweeps,
	       telemetry.notify_ok,
	       telemetry.notify_dropped,
	       telemetry.uart_restarts,
	       telemetry.last_uart_error,
	       telemetry.last_sweep,
	       telemetry.have_last_sweep,
	       telemetry.data_subscribed,
	       received_packets,
	       malformed_packets);

	return BT_GATT_ITER_CONTINUE;
}

static uint8_t discover_fusion(struct bt_conn *conn,
			       const struct bt_gatt_attr *attr,
			       struct bt_gatt_discover_params *params)
{
	int err;

	if (attr == NULL) {
		printk("FUSION_FAIL step=discover_%u err=not_found\n",
		       discovery_stage);
		memset(params, 0, sizeof(*params));
		return BT_GATT_ITER_STOP;
	}

	switch (discovery_stage) {
	case DISCOVERY_SERVICE: {
		const struct bt_gatt_service_val *service = attr->user_data;

		service_end_handle = service->end_handle;
		printk("FUSION_SERVICE start=%u end=%u\n",
		       attr->handle, service_end_handle);

		discovery_stage = DISCOVERY_DATA_CHARACTERISTIC;
		discover_params.uuid = &fusion_data_uuid.uuid;
		discover_params.start_handle = attr->handle + 1;
		discover_params.end_handle = service_end_handle;
		discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;
		err = bt_gatt_discover(conn, &discover_params);
		if (err != 0) {
			printk("FUSION_FAIL step=discover_data_start err=%d\n",
			       err);
		}
		break;
	}

	case DISCOVERY_DATA_CHARACTERISTIC: {
		const struct bt_gatt_chrc *characteristic = attr->user_data;

		data_value_handle = characteristic->value_handle;
		printk("FUSION_DATA_CHARACTERISTIC value=%u props=0x%02x\n",
		       data_value_handle, characteristic->properties);
		if ((characteristic->properties & BT_GATT_CHRC_NOTIFY) == 0u) {
			printk("FUSION_FAIL step=data_not_notifiable\n");
			break;
		}

		discovery_stage = DISCOVERY_DATA_CCC;
		discover_params.uuid = BT_UUID_GATT_CCC;
		discover_params.start_handle = data_value_handle + 1;
		discover_params.end_handle = service_end_handle;
		discover_params.type = BT_GATT_DISCOVER_DESCRIPTOR;
		err = bt_gatt_discover(conn, &discover_params);
		if (err != 0) {
			printk("FUSION_FAIL step=discover_data_ccc_start err=%d\n",
			       err);
		}
		break;
	}

	case DISCOVERY_DATA_CCC:
		data_subscribe_params.notify = data_notification;
		data_subscribe_params.value = BT_GATT_CCC_NOTIFY;
		data_subscribe_params.value_handle = data_value_handle;
		data_subscribe_params.ccc_handle = attr->handle;
		err = bt_gatt_subscribe(conn, &data_subscribe_params);
		if (err != 0 && err != -EALREADY) {
			printk("FUSION_FAIL step=subscribe_data err=%d\n", err);
			break;
		}
		printk("FUSION_DATA_SUBSCRIBED value=%u ccc=%u\n",
		       data_value_handle, attr->handle);

		discovery_stage = DISCOVERY_TELEMETRY_CHARACTERISTIC;
		discover_params.uuid = &fusion_telemetry_uuid.uuid;
		discover_params.start_handle = attr->handle + 1;
		discover_params.end_handle = service_end_handle;
		discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;
		err = bt_gatt_discover(conn, &discover_params);
		if (err != 0) {
			printk("FUSION_FAIL step=discover_telemetry_start err=%d\n",
			       err);
		}
		break;

	case DISCOVERY_TELEMETRY_CHARACTERISTIC: {
		const struct bt_gatt_chrc *characteristic = attr->user_data;

		telemetry_value_handle = characteristic->value_handle;
		printk("FUSION_TELEMETRY_CHARACTERISTIC value=%u props=0x%02x\n",
		       telemetry_value_handle, characteristic->properties);
		if ((characteristic->properties & BT_GATT_CHRC_NOTIFY) == 0u) {
			printk("FUSION_FAIL step=telemetry_not_notifiable\n");
			break;
		}

		discovery_stage = DISCOVERY_TELEMETRY_CCC;
		discover_params.uuid = BT_UUID_GATT_CCC;
		discover_params.start_handle = telemetry_value_handle + 1;
		discover_params.end_handle = service_end_handle;
		discover_params.type = BT_GATT_DISCOVER_DESCRIPTOR;
		err = bt_gatt_discover(conn, &discover_params);
		if (err != 0) {
			printk("FUSION_FAIL step=discover_telemetry_ccc_start err=%d\n",
			       err);
		}
		break;
	}

	case DISCOVERY_TELEMETRY_CCC:
		telemetry_subscribe_params.notify = telemetry_notification;
		telemetry_subscribe_params.value = BT_GATT_CCC_NOTIFY;
		telemetry_subscribe_params.value_handle = telemetry_value_handle;
		telemetry_subscribe_params.ccc_handle = attr->handle;
		err = bt_gatt_subscribe(conn, &telemetry_subscribe_params);
		if (err != 0 && err != -EALREADY) {
			printk("FUSION_FAIL step=subscribe_telemetry err=%d\n",
			       err);
			break;
		}

		bridge_ready = true;
		printk("FUSION_BRIDGE_READY name=%s mtu=%u data=%u telemetry=%u\n",
		       candidate_name, bt_gatt_get_mtu(conn),
		       data_value_handle, telemetry_value_handle);
		memset(params, 0, sizeof(*params));
		break;
	}

	return BT_GATT_ITER_STOP;
}

static void start_fusion_discovery(struct bt_conn *conn)
{
	int err;

	memset(&discover_params, 0, sizeof(discover_params));
	discovery_stage = DISCOVERY_SERVICE;
	discover_params.uuid = &fusion_service_uuid.uuid;
	discover_params.func = discover_fusion;
	discover_params.start_handle = BT_ATT_FIRST_ATTRIBUTE_HANDLE;
	discover_params.end_handle = BT_ATT_LAST_ATTRIBUTE_HANDLE;
	discover_params.type = BT_GATT_DISCOVER_PRIMARY;

	err = bt_gatt_discover(conn, &discover_params);
	if (err != 0) {
		printk("FUSION_FAIL step=discover_service_start err=%d\n", err);
	}
}

static void mtu_exchanged(struct bt_conn *conn, uint8_t err,
			  struct bt_gatt_exchange_params *params)
{
	ARG_UNUSED(params);
	printk("FUSION_ATT_MTU value=%u err=%u\n", bt_gatt_get_mtu(conn), err);
	start_fusion_discovery(conn);
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
		printk("FUSION_FAIL step=connect_complete addr=%s hci=0x%02x\n",
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
	++reconnections;
	printk("FUSION_CONNECTED addr=%s connection=%u\n",
	       addr, reconnections);

	err = bt_conn_le_phy_update(conn, &phy_params);
	printk("FUSION_PHY_REQUEST preferred=2M err=%d\n", err);

	err = bt_conn_le_data_len_update(conn, BT_LE_DATA_LEN_PARAM_MAX);
	printk("FUSION_DLE_REQUEST max=251 err=%d\n", err);

	exchange_params.func = mtu_exchanged;
	err = bt_gatt_exchange_mtu(conn, &exchange_params);
	if (err != 0) {
		printk("FUSION_ATT_MTU_REQUEST err=%d\n", err);
		start_fusion_discovery(conn);
	}
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];

	if (conn != target_conn) {
		return;
	}

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("FUSION_DISCONNECTED addr=%s reason=0x%02x packets=%u malformed=%u\n",
	       addr, reason, received_packets, malformed_packets);
	bt_conn_unref(target_conn);
	target_conn = NULL;
	connecting = false;
	bridge_ready = false;
	memset(&data_subscribe_params, 0, sizeof(data_subscribe_params));
	memset(&telemetry_subscribe_params, 0,
	       sizeof(telemetry_subscribe_params));
	start_scan();
}

static void le_phy_updated(struct bt_conn *conn,
			   struct bt_conn_le_phy_info *param)
{
	ARG_UNUSED(conn);
	printk("FUSION_PHY_UPDATED tx=%u rx=%u\n",
	       param->tx_phy, param->rx_phy);
}

static void le_data_len_updated(struct bt_conn *conn,
				struct bt_conn_le_data_len_info *info)
{
	ARG_UNUSED(conn);
	printk("FUSION_DLE_UPDATED tx_len=%u tx_time=%u rx_len=%u rx_time=%u\n",
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

	printk("FUSION_MASTER marker=dk-fusion-uart-bridge-v1 probe=683234364\n");
	err = bt_enable(NULL);
	if (err != 0) {
		printk("FUSION_FAIL step=bt_enable err=%d\n", err);
		return 0;
	}

	printk("FUSION_MASTER_BLUETOOTH_READY\n");
	start_scan();

	while (true) {
		k_sleep(K_SECONDS(10));
		if (!target_conn && !connecting) {
			printk("FUSION_SCAN_WAITING\n");
		} else if (bridge_ready) {
			printk("FUSION_HEALTH packets=%u malformed=%u connections=%u\n",
			       received_packets, malformed_packets, reconnections);
		}
	}

	return 0;
}
