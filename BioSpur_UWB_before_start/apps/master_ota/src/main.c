#include <errno.h>
#include <stdint.h>
#include <string.h>

#include <dk_buttons_and_leds.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include <bluetooth/gatt_dm.h>
#include <bluetooth/scan.h>
#include <bluetooth/services/dfu_smp.h>

#include <zcbor_common.h>
#include <zcbor_decode.h>
#include <zcbor_encode.h>
#include <mgmt/mcumgr/util/zcbor_bulk.h>

#include "ota_image.inc"

#define OTA_LED_SCAN DK_LED1
#define OTA_LED_LINK DK_LED2
#define OTA_LED_OTA DK_LED3
#define OTA_LED_ERROR DK_LED4

#define OTA_CHUNK_SIZE 64U
#define OTA_CMD_TIMEOUT_SEC 10
#define OTA_SMP_GROUP_IMG 0x0001U
#define OTA_SMP_GROUP_OS 0x0000U
#define OTA_SMP_CMD_IMG_STATE 0x00U
#define OTA_SMP_CMD_IMG_UPLOAD 0x01U
#define OTA_SMP_CMD_OS_RESET 0x05U
#define OTA_CBOR_DECODER_STATE_NUM 4U

struct smp_packet {
	struct bt_dfu_smp_header header;
	uint8_t payload[256];
};

struct ota_cmd_result {
	int status;
	size_t off;
	bool off_found;
};

static struct bt_conn *default_conn;
static struct bt_dfu_smp dfu_smp;
static struct k_sem ota_sem;
static struct k_work_delayable ota_work;
static bool leds_ready;
static bool led_scan_state;
static bool led_link_state;
static bool led_ota_state;
static bool led_error_state;
static bool ota_ready;
static bool ota_started;
static bool ota_done;
static bool mtu_ready;
static int ota_status;
static uint8_t ota_seq;
static struct ota_cmd_result *ota_active_result;
static struct bt_gatt_exchange_params exchange_params;

static void master_leds_apply(void)
{
	if (!leds_ready) {
		return;
	}

	(void)dk_set_led(OTA_LED_SCAN, led_scan_state);
	(void)dk_set_led(OTA_LED_LINK, led_link_state);
	(void)dk_set_led(OTA_LED_OTA, led_ota_state);
	(void)dk_set_led(OTA_LED_ERROR, led_error_state);
}

static void master_leds_set(bool scan, bool link, bool ota, bool error)
{
	led_scan_state = scan;
	led_link_state = link;
	led_ota_state = ota;
	led_error_state = error;
	master_leds_apply();
}

static int ota_parse_response(struct bt_dfu_smp *smp, struct ota_cmd_result *result)
{
	const struct bt_dfu_smp_rsp_state *rsp;
	zcbor_state_t zsd[OTA_CBOR_DECODER_STATE_NUM];
	struct zcbor_map_decode_key_val map[] = {
		ZCBOR_MAP_DECODE_KEY_DECODER("off", zcbor_size_decode, &result->off),
		ZCBOR_MAP_DECODE_KEY_DECODER("rc", zcbor_int32_decode, &result->status),
	};
	size_t decoded = 0U;
	int rc;

	rsp = bt_dfu_smp_rsp_state(smp);
	if (!rsp || !rsp->data || !rsp->chunk_size) {
		result->status = -ETIMEDOUT;
		return result->status;
	}

	result->status = 0;
	result->off = 0U;
	result->off_found = false;

	zcbor_new_decode_state(zsd, ARRAY_SIZE(zsd), rsp->data, rsp->chunk_size, 1, NULL, 0);
	rc = zcbor_map_decode_bulk(zsd, map, ARRAY_SIZE(map), &decoded);
	if (rc && rc != -ENOMSG) {
		result->status = -EBADMSG;
		return result->status;
	}

	result->off_found = zcbor_map_decode_bulk_key_found(map, ARRAY_SIZE(map), "off");
	return result->status;
}

static void ota_response_cb(struct bt_dfu_smp *smp)
{
	if (!ota_active_result) {
		return;
	}

	printk("OTA response chunk: offset=%u chunk=%u total=%u total_check=%d\n",
	       (unsigned int)bt_dfu_smp_rsp_state(smp)->offset,
	       (unsigned int)bt_dfu_smp_rsp_state(smp)->chunk_size,
	       (unsigned int)bt_dfu_smp_rsp_state(smp)->total_size,
	       bt_dfu_smp_rsp_total_check(smp));

	if (!bt_dfu_smp_rsp_total_check(smp)) {
		return;
	}

	ota_parse_response(smp, ota_active_result);
	printk("OTA parsed response: status=%d off=%u off_found=%d\n",
	       ota_active_result->status,
	       (unsigned int)ota_active_result->off,
	       ota_active_result->off_found);
	k_sem_give(&ota_sem);
}

static int ota_send_packet(struct bt_dfu_smp *smp, struct smp_packet *pkt,
			   size_t payload_len, struct ota_cmd_result *result,
			   uint16_t group_id, uint8_t command_id)
{
	int rc;

	pkt->header.op = 2U;
	pkt->header.flags = 0U;
	pkt->header.len_h8 = (uint8_t)((payload_len >> 8) & 0xffU);
	pkt->header.len_l8 = (uint8_t)(payload_len & 0xffU);
	pkt->header.group_h8 = (uint8_t)((group_id >> 8) & 0xffU);
	pkt->header.group_l8 = (uint8_t)(group_id & 0xffU);
	pkt->header.seq = ota_seq++;
	pkt->header.id = command_id;

	k_sem_reset(&ota_sem);
	result->status = -ETIMEDOUT;
	result->off = 0U;
	result->off_found = false;
	ota_active_result = result;

	rc = bt_dfu_smp_command(smp, ota_response_cb, sizeof(pkt->header) + payload_len, pkt);
	if (rc) {
		printk("OTA command send failed: group=0x%04x cmd=0x%02x rc=%d\n",
		       group_id, command_id, rc);
		ota_active_result = NULL;
		return rc;
	}

	rc = k_sem_take(&ota_sem, K_SECONDS(OTA_CMD_TIMEOUT_SEC));
	ota_active_result = NULL;
	if (rc) {
		printk("OTA command wait failed: group=0x%04x cmd=0x%02x rc=%d\n",
		       group_id, command_id, rc);
		return rc;
	}

	printk("OTA command done: group=0x%04x cmd=0x%02x status=%d off=%u\n",
	       group_id, command_id, result->status, (unsigned int)result->off);
	return result->status;
}

static size_t ota_build_upload_packet(const uint8_t *image, size_t image_len,
				      const uint8_t *sha256, size_t offset,
				      struct smp_packet *pkt, size_t chunk_len,
				      bool first_chunk)
{
	zcbor_state_t zse[2];
	bool ok;
	uint32_t map_count;
	size_t payload_len;

	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt->payload, sizeof(pkt->payload), 0);
	zse->constant_state->stop_on_error = true;

	map_count = first_chunk ? 12U : 6U;

	ok = zcbor_map_start_encode(zse, map_count);
	if (ok && first_chunk) {
		ok = zcbor_tstr_put_lit(zse, "image") &&
		     zcbor_uint32_put(zse, 0U) &&
		     zcbor_tstr_put_lit(zse, "len") &&
		     zcbor_size_put(zse, image_len) &&
		     zcbor_tstr_put_lit(zse, "off") &&
		     zcbor_size_put(zse, offset) &&
		     zcbor_tstr_put_lit(zse, "sha") &&
		     zcbor_bstr_encode_ptr(zse, sha256, sizeof(tag_ota_image_sha256)) &&
		     zcbor_tstr_put_lit(zse, "data") &&
		     zcbor_bstr_encode_ptr(zse, image + offset, chunk_len);
	} else if (ok) {
		ok = zcbor_tstr_put_lit(zse, "off") &&
		     zcbor_size_put(zse, offset) &&
		     zcbor_tstr_put_lit(zse, "data") &&
		     zcbor_bstr_encode_ptr(zse, image + offset, chunk_len);
	}

	if (ok) {
		ok = zcbor_map_end_encode(zse, map_count);
	}

	if (!ok) {
		return 0U;
	}

	payload_len = (size_t)(zse->payload - pkt->payload);
	return payload_len;
}

static int ota_upload_image(struct bt_dfu_smp *smp)
{
	size_t offset = 0U;
	size_t remaining = tag_ota_image_len;
	bool first_chunk = true;
	struct smp_packet pkt;
	struct ota_cmd_result result;
	int rc;

	printk("OTA upload starting: image_len=%u bytes\n", (unsigned int)tag_ota_image_len);
	master_leds_set(false, true, true, false);

	while (remaining > 0U) {
		size_t chunk_len = MIN(remaining, OTA_CHUNK_SIZE);
		size_t payload_len;
		unsigned int chunk_index = (unsigned int)(offset / OTA_CHUNK_SIZE);

		memset(&pkt, 0, sizeof(pkt));
		payload_len = ota_build_upload_packet(tag_ota_image, tag_ota_image_len,
						       tag_ota_image_sha256, offset, &pkt,
						       chunk_len, first_chunk);
		if (payload_len == 0U) {
			printk("OTA upload packet build failed: off=%u len=%u first=%d\n",
			       (unsigned int)offset, (unsigned int)chunk_len, first_chunk);
			return -EIO;
		}

		printk("OTA upload chunk %u: off=%u len=%u first=%d\n",
		       chunk_index, (unsigned int)offset, (unsigned int)chunk_len, first_chunk);
		printk("OTA upload packet len=%u\n", (unsigned int)payload_len);
		rc = ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
				     OTA_SMP_CMD_IMG_UPLOAD);
		if (rc) {
			return rc;
		}

		if (result.off_found && result.off != offset + chunk_len) {
			printk("OTA upload offset mismatch: expected %u got %u\n",
			       (unsigned int)(offset + chunk_len), (unsigned int)result.off);
		}

		offset += chunk_len;
		remaining -= chunk_len;
		first_chunk = false;
	}

	printk("OTA upload complete\n");
	return 0;
}

static int ota_schedule_pending(struct bt_dfu_smp *smp)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[2];
	bool ok;
	size_t payload_len;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);
	zse->constant_state->stop_on_error = true;

	ok = zcbor_map_start_encode(zse, 4U) &&
	     zcbor_tstr_put_lit(zse, "hash") &&
	     zcbor_bstr_encode_ptr(zse, tag_ota_image_sha256, sizeof(tag_ota_image_sha256)) &&
	     zcbor_tstr_put_lit(zse, "confirm") &&
	     zcbor_bool_put(zse, false) &&
	     zcbor_map_end_encode(zse, 4U);
	if (!ok) {
		return -EIO;
	}

	payload_len = (size_t)(zse->payload - pkt.payload);

	printk("OTA pending flag request\n");
	master_leds_set(false, true, true, false);
	return ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
			       OTA_SMP_CMD_IMG_STATE);
}

static int ota_remote_reset(struct bt_dfu_smp *smp)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[2];
	bool ok;
	size_t payload_len;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);
	zse->constant_state->stop_on_error = true;

	ok = zcbor_map_start_encode(zse, 0U) && zcbor_map_end_encode(zse, 0U);
	if (!ok) {
		return -EIO;
	}

	payload_len = (size_t)(zse->payload - pkt.payload);

	printk("OTA reset request\n");
	master_leds_set(false, true, true, false);
	return ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_OS,
			       OTA_SMP_CMD_OS_RESET);
}

static void ota_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	if (!ota_ready || !mtu_ready || ota_started || ota_done || default_conn == NULL) {
		return;
	}

	printk("OTA start gate: mtu=%u conn=%p\n",
	       (unsigned int)bt_gatt_get_mtu(default_conn), default_conn);
	ota_started = true;
	ota_status = ota_upload_image(&dfu_smp);
	if (ota_status) {
		printk("OTA upload failed: %d\n", ota_status);
		master_leds_set(false, true, false, true);
		ota_done = true;
		return;
	}

	ota_status = ota_schedule_pending(&dfu_smp);
	if (ota_status) {
		printk("OTA schedule failed: %d\n", ota_status);
		master_leds_set(false, true, false, true);
		ota_done = true;
		return;
	}

	ota_status = ota_remote_reset(&dfu_smp);
	if (ota_status) {
		printk("OTA reset failed: %d\n", ota_status);
		master_leds_set(false, true, false, true);
		ota_done = true;
		return;
	}

	printk("OTA command sequence sent\n");
	ota_done = true;
	master_leds_set(false, true, false, false);
}

static void dfu_error_cb(struct bt_dfu_smp *smp, int err)
{
	ARG_UNUSED(smp);

	printk("DFU SMP error: %d\n", err);
	ota_status = err;
	master_leds_set(led_scan_state, led_link_state, false, true);
}

static const struct bt_dfu_smp_init_params init_params = {
	.error_cb = dfu_error_cb,
};

static void exchange_func(struct bt_conn *conn, uint8_t err,
			  struct bt_gatt_exchange_params *params)
{
	ARG_UNUSED(params);

	if (err) {
		printk("MTU exchange failed: %u\n", err);
	} else {
		printk("MTU exchange done, mtu=%u\n", (unsigned int)bt_gatt_get_mtu(conn));
		mtu_ready = true;
		if (ota_ready) {
			k_work_reschedule(&ota_work, K_NO_WAIT);
		}
	}
}

static void discovery_complete(struct bt_gatt_dm *dm, void *context)
{
	ARG_UNUSED(context);
	int err;

	err = bt_dfu_smp_handles_assign(dm, &dfu_smp);
	if (err) {
		printk("DFU SMP handle assign failed: %d\n", err);
		bt_gatt_dm_data_release(dm);
		return;
	}

	ota_ready = true;
	printk("DFU SMP service ready\n");
	master_leds_set(false, true, false, false);
	if (mtu_ready) {
		k_work_reschedule(&ota_work, K_NO_WAIT);
	}

	bt_gatt_dm_data_release(dm);
}

static void discovery_service_not_found(struct bt_conn *conn, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);
	printk("DFU SMP service not found\n");
	ota_status = -ENOENT;
	master_leds_set(true, false, false, true);
}

static void discovery_error(struct bt_conn *conn, int err, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);
	printk("GATT discovery error: %d\n", err);
	ota_status = err;
	master_leds_set(true, false, false, true);
}

static struct bt_gatt_dm_cb discovery_cb = {
	.completed = discovery_complete,
	.service_not_found = discovery_service_not_found,
	.error_found = discovery_error,
};

static void gatt_discover(struct bt_conn *conn)
{
	int err;

	err = bt_gatt_dm_start(conn, BT_UUID_DFU_SMP_SERVICE, &discovery_cb, NULL);
	if (err) {
		printk("Could not start discovery: %d\n", err);
		ota_status = err;
		master_leds_set(true, false, false, true);
	}
}

static void connected(struct bt_conn *conn, uint8_t conn_err)
{
	char addr[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	if (conn_err) {
		printk("Connect failed %s err 0x%02x\n", addr, conn_err);
		if (default_conn == conn) {
			bt_conn_unref(default_conn);
			default_conn = NULL;
		}
		master_leds_set(true, false, false, true);
		return;
	}

	printk("Connected: %s\n", addr);
	printk("Connected MTU: %u\n", (unsigned int)bt_gatt_get_mtu(conn));
	exchange_params.func = exchange_func;
	(void)bt_gatt_exchange_mtu(conn, &exchange_params);
	gatt_discover(conn);
	(void)bt_scan_stop();
	master_leds_set(false, true, false, false);
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("Disconnected: %s reason 0x%02x\n", addr, reason);

	if (default_conn == conn) {
		bt_conn_unref(default_conn);
		default_conn = NULL;
	}

	ota_ready = false;
	ota_started = false;
	mtu_ready = false;
	k_work_cancel_delayable(&ota_work);
	master_leds_set(true, false, false, false);
	(void)bt_scan_start(BT_SCAN_TYPE_SCAN_ACTIVE);
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
};

static void scan_filter_match(struct bt_scan_device_info *device_info,
			      struct bt_scan_filter_match *filter_match,
			      bool connectable)
{
	char addr[BT_ADDR_LE_STR_LEN];

	ARG_UNUSED(filter_match);
	bt_addr_le_to_str(device_info->recv_info->addr, addr, sizeof(addr));
	printk("Scan match: %s connectable=%d\n", addr, connectable);
}

static void scan_connecting_error(struct bt_scan_device_info *device_info)
{
	ARG_UNUSED(device_info);
	printk("Connecting failed\n");
}

static void scan_connecting(struct bt_scan_device_info *device_info,
			    struct bt_conn *conn)
{
	ARG_UNUSED(device_info);
	default_conn = bt_conn_ref(conn);
}

BT_SCAN_CB_INIT(scan_cb, scan_filter_match, NULL, scan_connecting_error,
		scan_connecting);

static int scan_init(void)
{
	int err;
	struct bt_scan_init_param scan_init = {
		.connect_if_match = 1,
	};

	bt_scan_init(&scan_init);
	bt_scan_cb_register(&scan_cb);

	err = bt_scan_filter_add(BT_SCAN_FILTER_TYPE_UUID, BT_UUID_DFU_SMP_SERVICE);
	if (err) {
		printk("Scan filter add failed: %d\n", err);
		return err;
	}

	err = bt_scan_filter_enable(BT_SCAN_UUID_FILTER, false);
	if (err) {
		printk("Scan filter enable failed: %d\n", err);
		return err;
	}

	master_leds_set(true, false, false, false);
	return 0;
}

static int ota_bootstrap(void)
{
	int err;

	k_sem_init(&ota_sem, 0, 1);
	k_work_init_delayable(&ota_work, ota_work_handler);

	err = dk_leds_init();
	if (err) {
		printk("LED init failed: %d\n", err);
	} else {
		leds_ready = true;
		master_leds_set(true, false, false, false);
		printk("LED map: 0=scan 1=link 2=ota 3=error\n");
	}

	bt_dfu_smp_init(&dfu_smp, &init_params);
	ota_ready = false;
	ota_started = false;
	ota_done = false;
	mtu_ready = false;
	ota_status = 0;
	ota_seq = 0U;

	err = bt_enable(NULL);
	if (err) {
		printk("Bluetooth init failed: %d\n", err);
		return err;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	err = scan_init();
	if (err) {
		return err;
	}

	printk("BioSpur BLE OTA master ready on nRF54L15 DK\n");
	printk("Scanning for Tag_rot_ota DFU SMP service\n");

	err = bt_scan_start(BT_SCAN_TYPE_SCAN_ACTIVE);
	if (err) {
		printk("Failed to start scan: %d\n", err);
		return err;
	}

	return 0;
}

int main(void)
{
	int err;

	err = ota_bootstrap();
	if (err) {
		master_leds_set(false, false, false, true);
		return err;
	}

	while (1) {
		k_sleep(K_SECONDS(1));
	}

	return 0;
}
