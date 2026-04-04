#include <errno.h>
#include <stdio.h>
#include <string.h>

#include <zcbor_common.h>
#include <zcbor_decode.h>
#include <zcbor_encode.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>

#include <bluetooth/gatt_dm.h>
#include <bluetooth/services/dfu_smp.h>

#include <mgmt/mcumgr/util/zcbor_bulk.h>

#include <zephyr/mgmt/mcumgr/grp/os_mgmt/os_mgmt.h>
#include <zephyr/mgmt/mcumgr/mgmt/mgmt_defines.h>

#include "app_ble.h"
#include "bsgr_protocol.h"
#include "cdc_async.h"

LOG_MODULE_REGISTER(central_app_ble, LOG_LEVEL_INF);

#define BSGR_CENTRAL_MAX_PEERS 4
#define BSGR_SCAN_INTERVAL 0x0060
#define BSGR_SCAN_WINDOW 0x0030
#define BSGR_OTA_NAME_MAX 31
#define BSGR_OTA_RSP_BUFFER_SIZE 768
#define BSGR_OTA_DEFAULT_TIMEOUT K_SECONDS(20)
#define BSGR_IMG_MGMT_ID_STATE 0
#define BSGR_IMG_MGMT_ID_UPLOAD 1

struct bsgr_smp_message {
	struct bt_dfu_smp_header header;
	uint8_t payload[BSGR_OTA_RSP_BUFFER_SIZE - sizeof(struct bt_dfu_smp_header)];
};

struct bsgr_ota_state {
	struct bt_conn *conn;
	struct bt_dfu_smp dfu_smp;
	struct bt_gatt_exchange_params exchange_params;
	struct k_sem connect_sem;
	struct k_sem rsp_sem;
	char target_name[BSGR_OTA_NAME_MAX + 1];
	struct bsgr_smp_message rsp_msg;
	size_t rsp_total_size;
	size_t upload_size;
	size_t upload_offset;
	int connect_status;
	int rsp_status;
	bool connect_requested;
	bool dfu_ready;
	bool discovery_pending;
	bool connect_waiting;
	bool scan_paused;
};

static struct bsgr_central_peer peers[BSGR_CENTRAL_MAX_PEERS];
static bool scan_running;
static struct bsgr_ota_state ota_state;

static struct bsgr_central_peer *peer_lookup_by_nus(struct bt_nus_client *nus)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (&peers[i].nus_client == nus) {
			return &peers[i];
		}
	}

	return NULL;
}

static struct bsgr_central_peer *peer_alloc_from_addr(const bt_addr_le_t *addr)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].in_use && (bt_addr_le_cmp(&peers[i].addr, addr) == 0)) {
			return &peers[i];
		}
	}

	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (!peers[i].in_use) {
			memset(&peers[i], 0, sizeof(peers[i]));
			peers[i].in_use = true;
			bt_addr_le_copy(&peers[i].addr, addr);
			return &peers[i];
		}
	}

	return NULL;
}

static void peer_release(struct bt_conn *conn)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].conn == conn) {
			if (peers[i].conn != NULL) {
				bt_conn_unref(peers[i].conn);
			}
			memset(&peers[i], 0, sizeof(peers[i]));
			return;
		}
	}
}

static void forward_frame_summary(const struct bsgr_frame_header *hdr)
{
	char line[96];
	int len;

	len = snprintf(line, sizeof(line), "dev=%u type=0x%02x seq=%u len=%u\r\n",
		       hdr->device_id, hdr->frame_type, hdr->seq, hdr->payload_len);
	if (len > 0) {
		(void)cdc_async_write_data((const uint8_t *)line, MIN((size_t)len, sizeof(line)));
	}
}

static uint8_t nus_recv_cb(struct bt_nus_client *nus, const uint8_t *data, uint16_t len)
{
	const struct bsgr_frame_header *hdr;
	struct bsgr_central_peer *peer = peer_lookup_by_nus(nus);

	if ((peer == NULL) || (len < sizeof(*hdr))) {
		return BT_GATT_ITER_CONTINUE;
	}

	hdr = (const struct bsgr_frame_header *)data;
	if (!bsgr_frame_header_is_valid(hdr)) {
		return BT_GATT_ITER_CONTINUE;
	}

	peer->identified = true;
	peer->device_id = hdr->device_id;
	peer->last_seq = hdr->seq;
	peer->last_seen_ticks = k_uptime_ticks();
	forward_frame_summary(hdr);

	return BT_GATT_ITER_CONTINUE;
}

static void nus_sent_cb(struct bt_nus_client *nus, uint8_t err,
			const uint8_t *data, uint16_t len)
{
	ARG_UNUSED(nus);
	ARG_UNUSED(err);
	ARG_UNUSED(data);
	ARG_UNUSED(len);
}

static const struct bt_nus_client_init_param nus_init_param = {
	.cb = {
		.received = nus_recv_cb,
		.sent = nus_sent_cb,
	},
};

static uint16_t ota_rsp_payload_len(const struct bt_dfu_smp_header *hdr)
{
	return ((uint16_t)hdr->len_h8 << 8) | hdr->len_l8;
}

static uint16_t ota_rsp_group(const struct bt_dfu_smp_header *hdr)
{
	return ((uint16_t)hdr->group_h8 << 8) | hdr->group_l8;
}

static void ota_rsp_part_cb(struct bt_dfu_smp *dfu_smp)
{
	const struct bt_dfu_smp_rsp_state *rsp_state = bt_dfu_smp_rsp_state(dfu_smp);
	uint8_t *dst = (uint8_t *)&ota_state.rsp_msg;

	if ((rsp_state->offset + rsp_state->chunk_size) > sizeof(ota_state.rsp_msg)) {
		ota_state.rsp_status = -EMSGSIZE;
		k_sem_give(&ota_state.rsp_sem);
		return;
	}

	memcpy(dst + rsp_state->offset, rsp_state->data, rsp_state->chunk_size);
	ota_state.rsp_total_size = rsp_state->total_size;

	if (bt_dfu_smp_rsp_total_check(dfu_smp)) {
		k_sem_give(&ota_state.rsp_sem);
	}
}

static void ota_error_cb(struct bt_dfu_smp *dfu_smp, int err)
{
	ARG_UNUSED(dfu_smp);
	ota_state.rsp_status = err;
	k_sem_give(&ota_state.rsp_sem);
}

static const struct bt_dfu_smp_init_params ota_dfu_init_params = {
	.error_cb = ota_error_cb,
};

static void ota_reset_response_state(void)
{
	memset(&ota_state.rsp_msg, 0, sizeof(ota_state.rsp_msg));
	ota_state.rsp_total_size = 0U;
	ota_state.rsp_status = 0;
	k_sem_reset(&ota_state.rsp_sem);
}

static int ota_send_command(uint8_t op, uint16_t group, uint8_t id,
			    const uint8_t *payload, size_t payload_len,
			    k_timeout_t timeout)
{
	struct {
		struct bt_dfu_smp_header header;
		uint8_t payload[320];
	} cmd;
	int err;

	if (!ota_state.dfu_ready || (ota_state.conn == NULL)) {
		return -ENOTCONN;
	}

	if (payload_len > sizeof(cmd.payload)) {
		return -EMSGSIZE;
	}

	memset(&cmd, 0, sizeof(cmd));
	cmd.header.op = op;
	cmd.header.flags = 0U;
	cmd.header.len_h8 = (uint8_t)((payload_len >> 8) & 0xFF);
	cmd.header.len_l8 = (uint8_t)(payload_len & 0xFF);
	cmd.header.group_h8 = (uint8_t)((group >> 8) & 0xFF);
	cmd.header.group_l8 = (uint8_t)(group & 0xFF);
	cmd.header.id = id;
	if ((payload != NULL) && (payload_len > 0U)) {
		memcpy(cmd.payload, payload, payload_len);
	}

	ota_reset_response_state();
	err = bt_dfu_smp_command(&ota_state.dfu_smp, ota_rsp_part_cb,
				 sizeof(cmd.header) + payload_len, &cmd);
	if (err != 0) {
		return err;
	}

	if (k_sem_take(&ota_state.rsp_sem, timeout) != 0) {
		return -ETIMEDOUT;
	}

	return ota_state.rsp_status;
}

static int ota_decode_image_state(struct bsgr_mcumgr_image_state *res_buf,
				  struct bsgr_mcumgr_image_data *image_list,
				  size_t image_list_size)
{
	zcbor_state_t zsd[CONFIG_MCUMGR_SMP_CBOR_MAX_DECODING_LEVELS + 2];
	struct zcbor_string value = {0};
	struct zcbor_string hash = {0};
	struct zcbor_string version = {0};
	bool bootable = false;
	bool pending = false;
	bool confirmed = false;
	bool active = false;
	bool permanent = false;
	bool ok;
	int rc = 0;
	size_t decoded;
	size_t count = 0U;
	uint32_t img_num;
	uint32_t slot_num;
	uint8_t *payload;
	size_t payload_len;

	if ((res_buf == NULL) || (image_list == NULL) || (image_list_size == 0U)) {
		return -EINVAL;
	}

	payload = ota_state.rsp_msg.payload;
	payload_len = ota_rsp_payload_len(&ota_state.rsp_msg.header);
	memset(res_buf, 0, sizeof(*res_buf));
	res_buf->image_list = image_list;

	if ((ota_state.rsp_msg.header.op != MGMT_OP_READ_RSP) &&
	    (ota_state.rsp_msg.header.op != MGMT_OP_WRITE_RSP)) {
		res_buf->status = MGMT_ERR_ECORRUPT;
		return -EBADMSG;
	}

	if (ota_rsp_group(&ota_state.rsp_msg.header) != MGMT_GROUP_ID_IMAGE) {
		res_buf->status = MGMT_ERR_ECORRUPT;
		return -EBADMSG;
	}

	zcbor_new_decode_state(zsd, ARRAY_SIZE(zsd), payload, payload_len, 1, NULL, 0);
	ok = zcbor_map_start_decode(zsd);
	if (!ok) {
		res_buf->status = MGMT_ERR_ECORRUPT;
		return -EBADMSG;
	}

	ok = zcbor_tstr_decode(zsd, &value);
	if (!ok || (value.len != 6U) || (memcmp(value.value, "images", 6U) != 0)) {
		res_buf->status = MGMT_ERR_EINVAL;
		return -EBADMSG;
	}

	ok = zcbor_list_start_decode(zsd);
	if (!ok) {
		res_buf->status = MGMT_ERR_ECORRUPT;
		return -EBADMSG;
	}

	while ((count < image_list_size) && (rc == 0)) {
		struct zcbor_map_decode_key_val decode_map[] = {
			ZCBOR_MAP_DECODE_KEY_DECODER("version", zcbor_tstr_decode, &version),
			ZCBOR_MAP_DECODE_KEY_DECODER("hash", zcbor_bstr_decode, &hash),
			ZCBOR_MAP_DECODE_KEY_DECODER("slot", zcbor_uint32_decode, &slot_num),
			ZCBOR_MAP_DECODE_KEY_DECODER("image", zcbor_uint32_decode, &img_num),
			ZCBOR_MAP_DECODE_KEY_DECODER("bootable", zcbor_bool_decode, &bootable),
			ZCBOR_MAP_DECODE_KEY_DECODER("pending", zcbor_bool_decode, &pending),
			ZCBOR_MAP_DECODE_KEY_DECODER("confirmed", zcbor_bool_decode, &confirmed),
			ZCBOR_MAP_DECODE_KEY_DECODER("active", zcbor_bool_decode, &active),
			ZCBOR_MAP_DECODE_KEY_DECODER("permanent", zcbor_bool_decode, &permanent),
		};

		bootable = false;
		pending = false;
		confirmed = false;
		active = false;
		permanent = false;
		img_num = 0U;
		slot_num = UINT32_MAX;
		hash.len = 0U;
		version.len = 0U;
		zcbor_map_decode_bulk_reset(decode_map, ARRAY_SIZE(decode_map));
		rc = zcbor_map_decode_bulk(zsd, decode_map, ARRAY_SIZE(decode_map), &decoded);
		if (rc != 0) {
			break;
		}

		if ((hash.len != BSGR_IMG_HASH_LEN) || (version.len == 0U)) {
			res_buf->status = MGMT_ERR_EINVAL;
			return -EBADMSG;
		}

		image_list[count].img_num = img_num;
		image_list[count].slot_num = slot_num;
		memcpy(image_list[count].hash, hash.value, BSGR_IMG_HASH_LEN);
		if (version.len > BSGR_IMG_VER_MAX_STR_LEN) {
			version.len = BSGR_IMG_VER_MAX_STR_LEN;
		}
		memcpy(image_list[count].version, version.value, version.len);
		image_list[count].version[version.len] = '\0';
		image_list[count].flags.bootable = bootable;
		image_list[count].flags.pending = pending;
		image_list[count].flags.confirmed = confirmed;
		image_list[count].flags.active = active;
		image_list[count].flags.permanent = permanent;
		count++;
	}

	ok = zcbor_list_end_decode(zsd);
	if (!ok) {
		res_buf->status = MGMT_ERR_ECORRUPT;
		return -EBADMSG;
	}

	res_buf->image_list_length = (int)count;
	res_buf->status = MGMT_ERR_EOK;
	return 0;
}

static int ota_decode_upload_offset(size_t *offset_out)
{
	zcbor_state_t zsd[CONFIG_MCUMGR_SMP_CBOR_MAX_DECODING_LEVELS + 2];
	int32_t rc_value = MGMT_ERR_EOK;
	struct zcbor_map_decode_key_val decode_map[] = {
		ZCBOR_MAP_DECODE_KEY_DECODER("off", zcbor_size_decode, offset_out),
		ZCBOR_MAP_DECODE_KEY_DECODER("rc", zcbor_int32_decode, &rc_value),
	};
	size_t decoded;
	size_t offset = SIZE_MAX;
	size_t payload_len;

	payload_len = ota_rsp_payload_len(&ota_state.rsp_msg.header);
	zcbor_new_decode_state(zsd, ARRAY_SIZE(zsd), ota_state.rsp_msg.payload,
			       payload_len, 1, NULL, 0);
	*offset_out = offset;
	if (zcbor_map_decode_bulk(zsd, decode_map, ARRAY_SIZE(decode_map), &decoded) != 0) {
		return -EBADMSG;
	}

	if ((*offset_out == SIZE_MAX) || (rc_value != MGMT_ERR_EOK)) {
		return -EIO;
	}

	return 0;
}

static int ota_find_secondary_hash(char *hash_out)
{
	struct bsgr_mcumgr_image_data images[4];
	struct bsgr_mcumgr_image_state state;
	size_t i;
	int err;

	err = app_ble_ota_read_images(&state, images, ARRAY_SIZE(images));
	if (err != 0) {
		return err;
	}

	for (i = 0; i < (size_t)state.image_list_length; ++i) {
		if (images[i].slot_num != 0U) {
			memcpy(hash_out, images[i].hash, BSGR_IMG_HASH_LEN);
			return 0;
		}
	}

	return -ENOENT;
}

static void discovery_completed_cb(struct bt_gatt_dm *dm, void *context)
{
	int err;

	ARG_UNUSED(context);

	err = bt_dfu_smp_handles_assign(dm, &ota_state.dfu_smp);
	if (err != 0) {
		ota_state.connect_status = err;
	} else {
		ota_state.dfu_ready = true;
		ota_state.connect_status = 0;
	}

	ota_state.discovery_pending = false;
	(void)bt_gatt_dm_data_release(dm);
	k_sem_give(&ota_state.connect_sem);
}

static void discovery_service_not_found_cb(struct bt_conn *conn, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);
	ota_state.connect_status = -ENOTSUP;
	ota_state.discovery_pending = false;
	k_sem_give(&ota_state.connect_sem);
}

static void discovery_error_found_cb(struct bt_conn *conn, int err, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);
	ota_state.connect_status = err;
	ota_state.discovery_pending = false;
	k_sem_give(&ota_state.connect_sem);
}

static const struct bt_gatt_dm_cb ota_discovery_cb = {
	.completed = discovery_completed_cb,
	.service_not_found = discovery_service_not_found_cb,
	.error_found = discovery_error_found_cb,
};

static void exchange_func(struct bt_conn *conn, uint8_t err,
			  struct bt_gatt_exchange_params *params)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(params);
	if (err != 0U) {
		LOG_WRN("MTU exchange failed: %u", err);
	}
}

static void connected(struct bt_conn *conn, uint8_t err)
{
	const bt_addr_le_t *dst = bt_conn_get_dst(conn);
	struct bsgr_central_peer *peer;
	int rc;

	if ((err != 0U) || (dst == NULL)) {
		if (ota_state.connect_waiting) {
			ota_state.connect_status = -ECONNREFUSED;
			k_sem_give(&ota_state.connect_sem);
		}
		return;
	}

	peer = peer_alloc_from_addr(dst);
	if (peer != NULL) {
		peer->conn = bt_conn_ref(conn);
		peer->last_seen_ticks = k_uptime_ticks();
	}

	if ((ota_state.conn != NULL) && (conn == ota_state.conn) && ota_state.connect_waiting) {
		ota_state.exchange_params.func = exchange_func;
		(void)bt_gatt_exchange_mtu(conn, &ota_state.exchange_params);
		ota_state.discovery_pending = true;
		rc = bt_gatt_dm_start(conn, BT_UUID_DFU_SMP_SERVICE, &ota_discovery_cb, NULL);
		if (rc != 0) {
			ota_state.discovery_pending = false;
			ota_state.connect_status = rc;
			k_sem_give(&ota_state.connect_sem);
		}
	}
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	LOG_INF("Peer disconnected: 0x%02x", reason);
	if ((ota_state.conn != NULL) && (conn == ota_state.conn)) {
		bt_conn_unref(ota_state.conn);
		ota_state.conn = NULL;
		ota_state.dfu_ready = false;
		ota_state.discovery_pending = false;
		if (ota_state.connect_waiting) {
			ota_state.connect_status = -ENOTCONN;
			k_sem_give(&ota_state.connect_sem);
		}
	}

	peer_release(conn);
}

BT_CONN_CB_DEFINE(central_conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
};

static bool scan_data_name_matches(struct bt_data *data, void *user_data)
{
	char *name = user_data;
	size_t copy_len;

	if ((data->type != BT_DATA_NAME_COMPLETE) && (data->type != BT_DATA_NAME_SHORTENED)) {
		return true;
	}

	copy_len = MIN((size_t)data->data_len, (size_t)BSGR_OTA_NAME_MAX);
	memcpy(name, data->data, copy_len);
	name[copy_len] = '\0';
	return false;
}

static bool ad_name_has_bsgr_prefix(struct net_buf_simple *ad)
{
	return (ad != NULL) && (ad->len >= 4U) && (ad->data[0] == 'B');
}

static bool scan_data_cb(struct bt_data *data, void *user_data)
{
	bool *matched = user_data;

	if ((data->type == BT_DATA_NAME_COMPLETE) || (data->type == BT_DATA_NAME_SHORTENED)) {
		struct net_buf_simple ad = {
			.data = data->data,
			.len = data->data_len,
			.size = data->data_len,
		};

		if (ad_name_has_bsgr_prefix(&ad)) {
			*matched = true;
			return false;
		}
	}

	return true;
}

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	bool matched = false;
	char adv_name[BSGR_OTA_NAME_MAX + 1] = {0};
	int err;

	bt_data_parse(ad, scan_data_cb, &matched);
	if (matched) {
		LOG_INF("Discovered BSGR candidate RSSI %d", rssi);
	}

	if (!ota_state.connect_requested || (ota_state.conn != NULL)) {
		return;
	}

	bt_data_parse(ad, scan_data_name_matches, adv_name);
	if ((adv_name[0] == '\0') || (strcmp(adv_name, ota_state.target_name) != 0)) {
		return;
	}

	(void)app_ble_stop_scan();
	err = bt_conn_le_create(addr, BT_CONN_LE_CREATE_CONN, BT_LE_CONN_PARAM_DEFAULT,
				&ota_state.conn);
	if (err != 0) {
		ota_state.connect_status = err;
		ota_state.connect_requested = false;
		k_sem_give(&ota_state.connect_sem);
	}
}

int app_ble_init(void)
{
	int err;
	size_t i;

	err = bt_enable(NULL);
	if (err != 0) {
		return err;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	memset(peers, 0, sizeof(peers));
	memset(&ota_state, 0, sizeof(ota_state));
	k_sem_init(&ota_state.connect_sem, 0, 1);
	k_sem_init(&ota_state.rsp_sem, 0, 1);
	err = bt_dfu_smp_init(&ota_state.dfu_smp, &ota_dfu_init_params);
	if (err != 0) {
		return err;
	}

	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		err = bt_nus_client_init(&peers[i].nus_client, &nus_init_param);
		if (err != 0) {
			return err;
		}
	}

	LOG_INF("BSGR Central BLE framework initialized");
	return 0;
}

int app_ble_start_scan(void)
{
	struct bt_le_scan_param scan_param = {
		.type = BT_LE_SCAN_TYPE_ACTIVE,
		.options = BT_LE_SCAN_OPT_NONE,
		.interval = BSGR_SCAN_INTERVAL,
		.window = BSGR_SCAN_WINDOW,
	};
	int err;

	err = bt_le_scan_start(&scan_param, device_found);
	if ((err == -EALREADY) || (err == 0)) {
		scan_running = true;
		return 0;
	}

	return err;
}

int app_ble_stop_scan(void)
{
	int err;

	err = bt_le_scan_stop();
	if ((err == -EALREADY) || (err == 0)) {
		scan_running = false;
		return 0;
	}

	return err;
}

void app_ble_process(void)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].identified) {
			peers[i].rssi = 0;
		}
	}

	if (!scan_running && !ota_state.connect_requested && (ota_state.conn == NULL)) {
		(void)app_ble_start_scan();
	}
}

const struct bsgr_central_peer *app_ble_peers_get(size_t *count)
{
	if (count != NULL) {
		*count = ARRAY_SIZE(peers);
	}

	return peers;
}

int app_ble_ota_connect(const char *target_name, k_timeout_t timeout)
{
	int err;

	if ((target_name == NULL) || (target_name[0] == '\0')) {
		return -EINVAL;
	}

	if (ota_state.dfu_ready && (strcmp(ota_state.target_name, target_name) == 0)) {
		return 0;
	}

	app_ble_ota_disconnect();
	memset(ota_state.target_name, 0, sizeof(ota_state.target_name));
	strncpy(ota_state.target_name, target_name, sizeof(ota_state.target_name) - 1U);
	ota_state.connect_status = 0;
	ota_state.connect_requested = true;
	ota_state.connect_waiting = true;
	k_sem_reset(&ota_state.connect_sem);

	err = app_ble_start_scan();
	if ((err != 0) && (err != -EALREADY)) {
		ota_state.connect_requested = false;
		ota_state.connect_waiting = false;
		return err;
	}

	if (k_sem_take(&ota_state.connect_sem, timeout) != 0) {
		ota_state.connect_requested = false;
		ota_state.connect_waiting = false;
		return -ETIMEDOUT;
	}

	ota_state.connect_requested = false;
	ota_state.connect_waiting = false;
	return ota_state.connect_status;
}

void app_ble_ota_disconnect(void)
{
	ota_state.connect_requested = false;
	ota_state.connect_waiting = false;
	ota_state.dfu_ready = false;
	ota_state.discovery_pending = false;
	ota_state.upload_offset = 0U;
	ota_state.upload_size = 0U;
	if (ota_state.conn != NULL) {
		bt_conn_disconnect(ota_state.conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		bt_conn_unref(ota_state.conn);
		ota_state.conn = NULL;
	}
}

bool app_ble_ota_ready(void)
{
	return ota_state.dfu_ready && (ota_state.conn != NULL);
}

int app_ble_ota_upload_start(size_t image_size)
{
	if (!app_ble_ota_ready() || (image_size == 0U)) {
		return -EINVAL;
	}

	ota_state.upload_size = image_size;
	ota_state.upload_offset = 0U;
	return 0;
}

int app_ble_ota_upload_chunk(const uint8_t *data, size_t len, size_t *remote_offset)
{
	zcbor_state_t zse[CONFIG_MCUMGR_SMP_CBOR_MAX_DECODING_LEVELS + 2];
	uint8_t payload[256];
	bool ok;
	int err;
	size_t payload_len;
	size_t rsp_offset = SIZE_MAX;

	if (!app_ble_ota_ready() || (data == NULL) || (len == 0U)) {
		return -EINVAL;
	}

	if (len > 128U) {
		return -EMSGSIZE;
	}

	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), payload, sizeof(payload), 0);
	ok = zcbor_map_start_encode(zse, ota_state.upload_offset == 0U ? 8 : 6) &&
	     zcbor_tstr_put_lit(zse, "image") && zcbor_uint32_put(zse, 0U) &&
	     zcbor_tstr_put_lit(zse, "data") && zcbor_bstr_encode_ptr(zse, data, len) &&
	     zcbor_tstr_put_lit(zse, "off") && zcbor_size_put(zse, ota_state.upload_offset);
	if (ok && (ota_state.upload_offset == 0U)) {
		ok = zcbor_tstr_put_lit(zse, "len") &&
		     zcbor_size_put(zse, ota_state.upload_size);
	}
	if (ok) {
		ok = zcbor_map_end_encode(zse, ota_state.upload_offset == 0U ? 8 : 6);
	}
	if (!ok) {
		return -ENOMEM;
	}

	payload_len = zse->payload - payload;
	err = ota_send_command(MGMT_OP_WRITE, MGMT_GROUP_ID_IMAGE, BSGR_IMG_MGMT_ID_UPLOAD,
			      payload, payload_len, K_SECONDS(15));
	if (err != 0) {
		return err;
	}

	err = ota_decode_upload_offset(&rsp_offset);
	if (err != 0) {
		return err;
	}

	ota_state.upload_offset = rsp_offset;
	if (remote_offset != NULL) {
		*remote_offset = rsp_offset;
	}

	return 0;
}

int app_ble_ota_read_images(struct bsgr_mcumgr_image_state *res_buf,
			    struct bsgr_mcumgr_image_data *image_list,
			    size_t image_list_size)
{
	zcbor_state_t zse[CONFIG_MCUMGR_SMP_CBOR_MAX_DECODING_LEVELS];
	uint8_t payload[16];
	bool ok;
	size_t payload_len;
	int err;

	if (!app_ble_ota_ready()) {
		return -ENOTCONN;
	}

	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), payload, sizeof(payload), 0);
	ok = zcbor_map_start_encode(zse, 1) && zcbor_map_end_encode(zse, 1);
	if (!ok) {
		return -ENOMEM;
	}

	payload_len = zse->payload - payload;
	err = ota_send_command(MGMT_OP_READ, MGMT_GROUP_ID_IMAGE, BSGR_IMG_MGMT_ID_STATE,
			      payload, payload_len, BSGR_OTA_DEFAULT_TIMEOUT);
	if (err != 0) {
		return err;
	}

	return ota_decode_image_state(res_buf, image_list, image_list_size);
}

int app_ble_ota_mark_test(const char *hash)
{
	zcbor_state_t zse[CONFIG_MCUMGR_SMP_CBOR_MAX_DECODING_LEVELS];
	uint8_t payload[80];
	bool ok;
	size_t payload_len;

	if (!app_ble_ota_ready() || (hash == NULL)) {
		return -EINVAL;
	}

	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), payload, sizeof(payload), 0);
	ok = zcbor_map_start_encode(zse, 4) &&
	     zcbor_tstr_put_lit(zse, "confirm") &&
	     zcbor_bool_put(zse, false) &&
	     zcbor_tstr_put_lit(zse, "hash") &&
	     zcbor_bstr_encode_ptr(zse, hash, BSGR_IMG_HASH_LEN) &&
	     zcbor_map_end_encode(zse, 4);
	if (!ok) {
		return -ENOMEM;
	}

	payload_len = zse->payload - payload;
	return ota_send_command(MGMT_OP_WRITE, MGMT_GROUP_ID_IMAGE, BSGR_IMG_MGMT_ID_STATE,
			       payload, payload_len, BSGR_OTA_DEFAULT_TIMEOUT);
}

int app_ble_ota_reset_target(void)
{
	return ota_send_command(MGMT_OP_WRITE, MGMT_GROUP_ID_OS, OS_MGMT_ID_RESET,
			       NULL, 0U, K_SECONDS(5));
}

const char *app_ble_ota_target_name(void)
{
	return ota_state.target_name;
}
