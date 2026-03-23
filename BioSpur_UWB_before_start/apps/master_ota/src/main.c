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
#include <bluetooth/services/nus.h>
#include <bluetooth/services/nus_client.h>

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
#define OTA_CMD_TIMEOUT_SEC 120
#define OTA_SMP_GROUP_IMG 0x0001U
#define OTA_SMP_GROUP_OS 0x0000U
#define OTA_SMP_CMD_IMG_STATE 0x00U
#define OTA_SMP_CMD_IMG_UPLOAD 0x01U
#define OTA_SMP_CMD_IMG_ERASE 0x05U
#define OTA_SMP_CMD_OS_RESET 0x05U
#define OTA_CBOR_DECODER_STATE_NUM 4U

#define OTA_DEBUG_VERBOSE 0
#if OTA_DEBUG_VERBOSE
#define OTA_VLOG(...) printk(__VA_ARGS__)
#else
#define OTA_VLOG(...) do { } while (0)
#endif

struct smp_packet {
	struct bt_dfu_smp_header header;
	uint8_t payload[256];
};

struct ota_cmd_result {
	int status;
	size_t off;
	bool off_found;
};

enum discovery_phase {
	DISCOVERY_PHASE_NUS = 0,
	DISCOVERY_PHASE_DFU,
};

static struct bt_conn *default_conn;
static struct bt_dfu_smp dfu_smp;
static struct bt_nus_client nus_client;
static struct k_sem ota_sem;
static struct k_sem ota_start_sem;
static struct k_sem nus_write_sem;
static bool leds_ready;
static bool led_scan_state;
static bool led_link_state;
static bool led_ota_state;
static bool led_error_state;
static bool ota_ready;
static bool ota_started;
static bool ota_done;
static bool ota_start_queued;
static bool mtu_ready;
static bool nus_ready;
static int ota_status;
static uint8_t ota_seq;
static enum discovery_phase discovery_phase;
static struct bt_gatt_exchange_params exchange_params;
static struct bt_gatt_subscribe_params smp_sub_params;
static bool smp_subscribed;
static struct bt_gatt_write_params smp_write_params;
static struct k_sem smp_write_sem;
static int smp_write_err;
static uint8_t smp_rsp_buf[512];
static size_t smp_rsp_len;
static size_t smp_rsp_total;
K_THREAD_STACK_DEFINE(ota_thread_stack, 3072);
static struct k_thread ota_thread;

static void gatt_discover_nus(struct bt_conn *conn);
static void gatt_discover_dfu(struct bt_conn *conn);

static void smp_write_cb(struct bt_conn *conn, uint8_t err,
			 struct bt_gatt_write_params *params)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(params);
	smp_write_err = (int)err;
	OTA_VLOG("[%u] OTA write cb err=0x%02x\n", k_uptime_get_32(), err);
	k_sem_give(&smp_write_sem);
}

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

static void nus_data_sent(struct bt_nus_client *nus, uint8_t err,
			  const uint8_t *const data, uint16_t len)
{
	ARG_UNUSED(nus);
	ARG_UNUSED(data);
	ARG_UNUSED(len);

	if (err) {
		printk("NUS write error: 0x%02x\n", err);
	}

	k_sem_give(&nus_write_sem);
}

static uint8_t nus_data_received(struct bt_nus_client *nus,
				 const uint8_t *data, uint16_t len)
{
	ARG_UNUSED(nus);

	printk("NUS notify: ");
	for (uint16_t i = 0; i < len; ++i) {
		char c = (char)data[i];

		printk("%c", (c >= 32 && c <= 126) ? c : '.');
	}
	printk("\n");

	return BT_GATT_ITER_CONTINUE;
}

static int nus_client_init(void)
{
	struct bt_nus_client_init_param init = {
		.cb = {
			.received = nus_data_received,
			.sent = nus_data_sent,
		},
	};

	return bt_nus_client_init(&nus_client, &init);
}

static int ota_send_nus_cmd(const char *cmd)
{
	int err;

	if (!nus_ready || default_conn == NULL) {
		return -ENOTCONN;
	}

	err = bt_nus_client_send(&nus_client, (const uint8_t *)cmd, strlen(cmd));
	if (err) {
		return err;
	}

	(void)k_sem_take(&nus_write_sem, K_MSEC(500));
	return 0;
}

static int ota_arm_target_via_nus(void)
{
	int err;

	if (!nus_ready) {
		return -ENOTSUP;
	}

	printk("OTA arm via NUS: OTA_PREPARE\n");
	err = ota_send_nus_cmd("OTA_PREPARE\n");
	if (err) {
		return err;
	}

	k_sleep(K_MSEC(300));

	printk("OTA arm via NUS: OTA_BEGIN\n");
	err = ota_send_nus_cmd("OTA_BEGIN\n");
	if (err) {
		return err;
	}

	k_sleep(K_MSEC(500));
	return 0;
}

static uint8_t smp_notify_cb(struct bt_conn *conn,
			     struct bt_gatt_subscribe_params *params,
			     const void *data, uint16_t length)
{
	ARG_UNUSED(conn);
	OTA_VLOG("OTA SMP notify thread=%p\n", k_current_get());

	if (!data) {
		params->notify = NULL;
		smp_subscribed = false;
		return BT_GATT_ITER_STOP;
	}

	if (smp_rsp_len == 0U && length >= sizeof(struct bt_dfu_smp_header)) {
		const struct bt_dfu_smp_header *header = data;

		smp_rsp_total = (((uint16_t)header->len_h8) << 8) | header->len_l8;
		smp_rsp_total += sizeof(struct bt_dfu_smp_header);
		if (smp_rsp_total > sizeof(smp_rsp_buf)) {
			smp_rsp_total = sizeof(smp_rsp_buf);
		}
	}

	if (smp_rsp_len < sizeof(smp_rsp_buf)) {
		size_t copy_len = MIN((size_t)length, sizeof(smp_rsp_buf) - smp_rsp_len);

		memcpy(&smp_rsp_buf[smp_rsp_len], data, copy_len);
		smp_rsp_len += copy_len;
	}
	OTA_VLOG("[%u] OTA SMP notify: len=%u acc=%u total=%u\n",
		 k_uptime_get_32(), (unsigned int)length, (unsigned int)smp_rsp_len,
		 (unsigned int)smp_rsp_total);

	if (smp_rsp_total > 0U && smp_rsp_len >= smp_rsp_total) {
		k_sem_give(&ota_sem);
	}

	return BT_GATT_ITER_CONTINUE;
}

static int smp_subscribe_if_needed(void)
{
	int rc;

	if (smp_subscribed) {
		return 0;
	}

	memset(&smp_sub_params, 0, sizeof(smp_sub_params));
	smp_sub_params.value_handle = dfu_smp.handles.smp;
	smp_sub_params.ccc_handle = dfu_smp.handles.smp_ccc;
	smp_sub_params.notify = smp_notify_cb;
	smp_sub_params.value = BT_GATT_CCC_NOTIFY;
	atomic_set_bit(smp_sub_params.flags, BT_GATT_SUBSCRIBE_FLAG_VOLATILE);

	rc = bt_gatt_resubscribe(BT_ID_DEFAULT, bt_conn_get_dst(dfu_smp.conn),
				 &smp_sub_params);
	if (rc == -EALREADY) {
		smp_subscribed = true;
		printk("OTA SMP subscribe already active\n");
		return 0;
	}
	if (rc) {
		return rc;
	}

	/* Fast-path enable CCC using write command to avoid waiting for write-req response. */
	{
		static const uint8_t ccc_enable[2] = { 0x01, 0x00 };
		int ccc_rc = bt_gatt_write_without_response(dfu_smp.conn, smp_sub_params.ccc_handle,
							    ccc_enable, sizeof(ccc_enable), false);
		(void)ccc_rc;
		OTA_VLOG("OTA SMP CCC fast-write rc=%d\n", ccc_rc);
	}
	OTA_VLOG("OTA SMP subscribed (val=0x%04x ccc=0x%04x)\n",
		 smp_sub_params.value_handle, smp_sub_params.ccc_handle);
	smp_subscribed = true;
	return 0;
}

static int cbor_decode_uint_any(const uint8_t *buf, size_t len, uint64_t *value, size_t *used)
{
	uint8_t major;
	uint8_t ai;
	uint64_t v = 0U;
	size_t need = 0U;

	if (!buf || len == 0U || !value || !used) {
		return -EINVAL;
	}

	major = (uint8_t)(buf[0] >> 5);
	ai = (uint8_t)(buf[0] & 0x1fU);
	if (major != 0U && major != 1U) {
		return -EBADMSG;
	}

	if (ai < 24U) {
		v = ai;
		need = 1U;
	} else if (ai == 24U) {
		if (len < 2U) {
			return -EMSGSIZE;
		}
		v = buf[1];
		need = 2U;
	} else if (ai == 25U) {
		if (len < 3U) {
			return -EMSGSIZE;
		}
		v = (((uint64_t)buf[1]) << 8) | buf[2];
		need = 3U;
	} else if (ai == 26U) {
		if (len < 5U) {
			return -EMSGSIZE;
		}
		v = (((uint64_t)buf[1]) << 24) |
		    (((uint64_t)buf[2]) << 16) |
		    (((uint64_t)buf[3]) << 8) |
		    ((uint64_t)buf[4]);
		need = 5U;
	} else if (ai == 27U) {
		if (len < 9U) {
			return -EMSGSIZE;
		}
		v = (((uint64_t)buf[1]) << 56) |
		    (((uint64_t)buf[2]) << 48) |
		    (((uint64_t)buf[3]) << 40) |
		    (((uint64_t)buf[4]) << 32) |
		    (((uint64_t)buf[5]) << 24) |
		    (((uint64_t)buf[6]) << 16) |
		    (((uint64_t)buf[7]) << 8) |
		    ((uint64_t)buf[8]);
		need = 9U;
	} else {
		return -EBADMSG;
	}

	*value = v;
	*used = need;
	return 0;
}

static int ota_parse_response_fallback(struct ota_cmd_result *result,
				       const uint8_t *payload, size_t payload_len)
{
	size_t i = 0U;
	bool any = false;

	if (!result || !payload || payload_len == 0U) {
		return -EINVAL;
	}

	while (i < payload_len) {
		if (i + 4U <= payload_len &&
		    payload[i] == 0x63U &&
		    payload[i + 1] == 'o' &&
		    payload[i + 2] == 'f' &&
		    payload[i + 3] == 'f') {
			uint64_t v = 0U;
			size_t used = 0U;
			int rc = cbor_decode_uint_any(&payload[i + 4U], payload_len - (i + 4U),
						      &v, &used);

			if (rc == 0 && v <= SIZE_MAX) {
				result->off = (size_t)v;
				result->off_found = true;
				any = true;
				i += 4U + used;
				continue;
			}
		}

		if (i + 3U <= payload_len &&
		    payload[i] == 0x62U &&
		    payload[i + 1] == 'r' &&
		    payload[i + 2] == 'c') {
			uint64_t v = 0U;
			size_t used = 0U;
			int rc = cbor_decode_uint_any(&payload[i + 3U], payload_len - (i + 3U),
						      &v, &used);

			if (rc == 0) {
				uint8_t major = (uint8_t)(payload[i + 3U] >> 5);

				if (major == 0U && v <= INT32_MAX) {
					result->status = (int)v;
					any = true;
					i += 3U + used;
					continue;
				}

				if (major == 1U && v < (uint64_t)INT32_MAX) {
					result->status = -(int)(v + 1U);
					any = true;
					i += 3U + used;
					continue;
				}
			}
		}

		/* img state read/write may legitimately return an image list without
		 * a top-level rc/off pair. Treat presence of the "images" key as a
		 * successful response so OTA can continue to the reset step.
		 */
		if (i + 7U <= payload_len &&
		    payload[i] == 0x66U &&
		    payload[i + 1] == 'i' &&
		    payload[i + 2] == 'm' &&
		    payload[i + 3] == 'a' &&
		    payload[i + 4] == 'g' &&
		    payload[i + 5] == 'e' &&
		    payload[i + 6] == 's') {
			result->status = 0;
			any = true;
			i += 7U;
			continue;
		}

		i++;
	}

	return any ? 0 : -ENOMSG;
}

static int ota_parse_response(struct ota_cmd_result *result)
{
	const struct bt_dfu_smp_header *header;
	zcbor_state_t zsd[OTA_CBOR_DECODER_STATE_NUM];
	struct zcbor_map_decode_key_val map[] = {
		ZCBOR_MAP_DECODE_KEY_DECODER("off", zcbor_size_decode, &result->off),
		ZCBOR_MAP_DECODE_KEY_DECODER("rc", zcbor_int32_decode, &result->status),
	};
	size_t decoded = 0U;
	size_t payload_len;
	int rc;
	const uint8_t *payload;

	if (smp_rsp_len < sizeof(struct bt_dfu_smp_header)) {
		result->status = -ETIMEDOUT;
		return result->status;
	}

	header = (const struct bt_dfu_smp_header *)smp_rsp_buf;
	payload_len = (((uint16_t)header->len_h8) << 8) | header->len_l8;
	if (payload_len + sizeof(*header) > smp_rsp_len) {
		result->status = -EMSGSIZE;
		return result->status;
	}
	payload = smp_rsp_buf + sizeof(*header);

	if (payload_len == 2U &&
	    ((payload[0] == 0xbfU && payload[1] == 0xffU) ||
	     payload[0] == 0xa0U)) {
		result->status = 0;
		result->off = 0U;
		result->off_found = false;
		return 0;
	}

	result->status = 0;
	result->off = 0U;
	result->off_found = false;

	zcbor_new_decode_state(zsd, ARRAY_SIZE(zsd), payload, payload_len, 1, NULL, 0);
	rc = zcbor_map_decode_bulk(zsd, map, ARRAY_SIZE(map), &decoded);
	if (rc && rc != -ENOMSG) {
		/* Some MCUmgr responses are encoded as indefinite CBOR maps (bf...ff). */
		if (ota_parse_response_fallback(result, payload, payload_len) == 0) {
			return result->status;
		}
		result->status = -EBADMSG;
		return result->status;
	}

	result->off_found = zcbor_map_decode_bulk_key_found(map, ARRAY_SIZE(map), "off");
	return result->status;
}

static int ota_send_packet(struct bt_dfu_smp *smp, struct smp_packet *pkt,
			   size_t payload_len, struct ota_cmd_result *result,
			   uint16_t group_id, uint8_t command_id)
{
	int rc;
	uint32_t t0_ms;
	static bool first_upload_dumped;

	pkt->header.op = 2U;
	pkt->header.flags = 0U;
	pkt->header.len_h8 = (uint8_t)((payload_len >> 8) & 0xffU);
	pkt->header.len_l8 = (uint8_t)(payload_len & 0xffU);
	pkt->header.group_h8 = (uint8_t)((group_id >> 8) & 0xffU);
	pkt->header.group_l8 = (uint8_t)(group_id & 0xffU);
	pkt->header.seq = ota_seq++;
	pkt->header.id = command_id;
	if (!first_upload_dumped &&
	    group_id == OTA_SMP_GROUP_IMG &&
	    command_id == OTA_SMP_CMD_IMG_UPLOAD) {
#if OTA_DEBUG_VERBOSE
		size_t total_len = sizeof(pkt->header) + payload_len;
		const uint8_t *raw = (const uint8_t *)pkt;
		OTA_VLOG("OTA tx bytes (%u):", (unsigned int)total_len);
		for (size_t i = 0; i < total_len; ++i) {
			OTA_VLOG("%02x", raw[i]);
		}
		OTA_VLOG("\n");
#endif
		first_upload_dumped = true;
	}

	rc = smp_subscribe_if_needed();
	if (rc) {
		printk("OTA subscribe failed: %d\n", rc);
		return rc;
	}

	k_sem_reset(&ota_sem);
	smp_rsp_len = 0U;
	smp_rsp_total = 0U;
	result->status = -ETIMEDOUT;
	result->off = 0U;
	result->off_found = false;
	k_sem_reset(&smp_write_sem);
	smp_write_err = 0;
	memset(&smp_write_params, 0, sizeof(smp_write_params));
	smp_write_params.handle = smp->handles.smp;
	smp_write_params.offset = 0U;
	smp_write_params.data = pkt;
	smp_write_params.length = sizeof(pkt->header) + payload_len;
	smp_write_params.func = smp_write_cb;
	rc = bt_gatt_write(smp->conn, &smp_write_params);
	if (rc) {
		printk("OTA command send failed: group=0x%04x cmd=0x%02x rc=%d\n",
		       group_id, command_id, rc);
		return rc;
	}

	t0_ms = k_uptime_get_32();
	OTA_VLOG("OTA wait thread=%p\n", k_current_get());
	{
		int64_t deadline = k_uptime_get() + (int64_t)OTA_CMD_TIMEOUT_SEC * MSEC_PER_SEC;
		bool done = false;

		while (k_uptime_get() < deadline) {
			if (smp_rsp_total > 0U && smp_rsp_len >= smp_rsp_total) {
				done = true;
				break;
			}
			k_sleep(K_MSEC(10));
		}

		if (!done) {
			uint32_t t1_ms = k_uptime_get_32();
			printk("[%u->%u] OTA command wait failed: group=0x%04x cmd=0x%02x rc=%d\n",
			       t0_ms, t1_ms, group_id, command_id, -ETIMEDOUT);
			return -ETIMEDOUT;
		}
	}

	if (group_id == OTA_SMP_GROUP_OS && command_id == 0U) {
		const struct bt_dfu_smp_header *rsp =
			(const struct bt_dfu_smp_header *)smp_rsp_buf;
		uint16_t rsp_group = (((uint16_t)rsp->group_h8) << 8) | rsp->group_l8;

		if (smp_rsp_len < sizeof(*rsp) || rsp->op != 3U ||
		    rsp_group != OTA_SMP_GROUP_OS || rsp->id != 0U) {
			return -EBADMSG;
		}
		result->status = 0;
		result->off = 0U;
		result->off_found = false;
	} else {
		ota_parse_response(result);
	}
	if (!(group_id == OTA_SMP_GROUP_IMG && command_id == OTA_SMP_CMD_IMG_UPLOAD)) {
		printk("OTA command done: group=0x%04x cmd=0x%02x status=%d off=%u\n",
		       group_id, command_id, result->status, (unsigned int)result->off);
	} else if (result->status != 0 ||
		   (result->off_found &&
		    (result->off == tag_ota_image_len ||
		     (result->off % (OTA_CHUNK_SIZE * 64U) == 0U)))) {
		printk("OTA upload ack: status=%d off=%u\n",
		       result->status, (unsigned int)result->off);
	}
	if (result->status != 0) {
		const struct bt_dfu_smp_header *rsp =
			(const struct bt_dfu_smp_header *)smp_rsp_buf;
		size_t rsp_payload_len = smp_rsp_len > sizeof(*rsp) ?
			smp_rsp_len - sizeof(*rsp) : 0U;

		printk("OTA raw rsp: op=%u flags=0x%02x len=%u group=0x%04x id=0x%02x payload=%u\n",
		       rsp->op, rsp->flags,
		       (unsigned int)(((uint16_t)rsp->len_h8 << 8) | rsp->len_l8),
		       (unsigned int)(((uint16_t)rsp->group_h8 << 8) | rsp->group_l8),
		       rsp->id, (unsigned int)rsp_payload_len);
		printk("OTA rsp bytes:");
		for (size_t i = 0; i < rsp_payload_len; i++) {
			printk(" %02x", smp_rsp_buf[sizeof(*rsp) + i]);
		}
		printk("\n");
	}
	return result->status;
}

static size_t ota_build_upload_packet(const uint8_t *image, size_t image_len,
				      const uint8_t *sha256, size_t offset,
				      struct smp_packet *pkt, size_t chunk_len,
				      bool first_chunk)
{
	zcbor_state_t zse[4];
	bool ok;
	uint32_t map_count;
	size_t payload_len;

	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt->payload, sizeof(pkt->payload), 0);

	map_count = first_chunk ? 12U : 6U;

	ok = zcbor_map_start_encode(zse, map_count) &&
	     zcbor_tstr_put_lit(zse, "image") &&
	     zcbor_uint32_put(zse, 0U) &&
	     zcbor_tstr_put_lit(zse, "data") &&
	     zcbor_bstr_encode_ptr(zse, image + offset, chunk_len) &&
	     zcbor_tstr_put_lit(zse, "off") &&
	     zcbor_size_put(zse, offset);

	if (ok && first_chunk) {
		ok = zcbor_tstr_put_lit(zse, "len") &&
		     zcbor_size_put(zse, image_len) &&
		     zcbor_tstr_put_lit(zse, "sha") &&
		     zcbor_bstr_encode_ptr(zse, sha256, sizeof(tag_ota_image_sha256));
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
	size_t stall_count = 0U;
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
		size_t expected_next;
		bool log_chunk;

		memset(&pkt, 0, sizeof(pkt));
		payload_len = ota_build_upload_packet(tag_ota_image, tag_ota_image_len,
						       tag_ota_image_sha256, offset, &pkt,
						       chunk_len, first_chunk);
		if (payload_len == 0U) {
			printk("OTA upload packet build failed: off=%u len=%u first=%d\n",
			       (unsigned int)offset, (unsigned int)chunk_len, first_chunk);
			return -EIO;
		}

		log_chunk = first_chunk || (chunk_index % 64U == 0U) || (remaining <= OTA_CHUNK_SIZE);
		if (log_chunk) {
			printk("OTA upload chunk %u: off=%u len=%u first=%d\n",
			       chunk_index, (unsigned int)offset, (unsigned int)chunk_len,
			       first_chunk);
		}
		if (first_chunk) {
#if OTA_DEBUG_VERBOSE
			size_t total_len = sizeof(pkt.header) + payload_len;
			const uint8_t *raw = (const uint8_t *)&pkt;
			OTA_VLOG("OTA first packet bytes (%u):", (unsigned int)total_len);
			for (size_t i = 0; i < total_len; ++i) {
				OTA_VLOG("%02x", raw[i]);
			}
			OTA_VLOG("\n");
#endif
		}
		rc = ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
				     OTA_SMP_CMD_IMG_UPLOAD);
		if (rc) {
			return rc;
		}

		expected_next = offset + chunk_len;
		if (result.off_found && result.off != expected_next && log_chunk) {
			printk("OTA upload offset mismatch: expected %u got %u\n",
			       (unsigned int)expected_next, (unsigned int)result.off);
		}
		if (result.off_found) {
			if (result.off > tag_ota_image_len) {
				printk("OTA upload invalid offset from target: %u\n",
				       (unsigned int)result.off);
				return -EPROTO;
			}
			if (result.off == offset) {
				if (++stall_count > 3U) {
					printk("OTA upload stalled at off=%u\n", (unsigned int)offset);
					return -EAGAIN;
				}
			} else {
				stall_count = 0U;
				offset = result.off;
				remaining = tag_ota_image_len - offset;
			}
		} else {
			offset = expected_next;
			remaining = tag_ota_image_len - offset;
		}
		first_chunk = false;
	}

	printk("OTA upload complete\n");
	return 0;
}

static int ota_schedule_pending(struct bt_dfu_smp *smp)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[4];
	bool ok;
	size_t payload_len;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);

	ok = zcbor_map_start_encode(zse, 4U) &&
	     zcbor_tstr_put_lit(zse, "hash") &&
	     zcbor_bstr_encode_ptr(zse, tag_ota_image_image_hash,
				   sizeof(tag_ota_image_image_hash)) &&
	     zcbor_tstr_put_lit(zse, "confirm") &&
	     zcbor_bool_put(zse, false) &&
	     zcbor_map_end_encode(zse, 4U);
	if (!ok) {
		return -EIO;
	}

	payload_len = (size_t)(zse->payload - pkt.payload);

	printk("OTA pending/test request\n");
	master_leds_set(false, true, true, false);
	return ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
			       OTA_SMP_CMD_IMG_STATE);
}

static int ota_remote_reset(struct bt_dfu_smp *smp)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[4];
	bool ok;
	size_t payload_len;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);

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

static int ota_erase_secondary_slot(struct bt_dfu_smp *smp)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[4];
	bool ok;
	size_t payload_len;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);

	ok = zcbor_map_start_encode(zse, 2U) &&
	     zcbor_tstr_put_lit(zse, "slot") &&
	     zcbor_uint32_put(zse, 1U) &&
	     zcbor_map_end_encode(zse, 2U);
	if (!ok) {
		return -EIO;
	}

	payload_len = (size_t)(zse->payload - pkt.payload);

	printk("OTA erase secondary slot request\n");
	master_leds_set(false, true, true, false);
	return ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
			       OTA_SMP_CMD_IMG_ERASE);
}

static int ota_read_image_state(struct bt_dfu_smp *smp)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[4];
	bool ok;
	size_t payload_len;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);

	ok = zcbor_map_start_encode(zse, 0U) && zcbor_map_end_encode(zse, 0U);
	if (!ok) {
		return -EIO;
	}

	payload_len = (size_t)(zse->payload - pkt.payload);

	printk("OTA image state read request\n");
	return ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
			       OTA_SMP_CMD_IMG_STATE);
}

static int ota_prime_link(struct bt_dfu_smp *smp)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[4];
	bool ok;
	size_t payload_len;
	int rc;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);
	ok = zcbor_map_start_encode(zse, 2U) &&
	     zcbor_tstr_put_lit(zse, "d") &&
	     zcbor_tstr_put_lit(zse, "ping") &&
	     zcbor_map_end_encode(zse, 2U);
	if (!ok) {
		return -EIO;
	}

	payload_len = (size_t)(zse->payload - pkt.payload);
	printk("OTA prime echo request\n");
	printk("OTA prime payload len=%u bytes:", (unsigned int)payload_len);
	for (size_t i = 0; i < payload_len; ++i) {
		printk("%02x", pkt.payload[i]);
	}
	printk("\n");
	rc = ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_OS, 0U);
	if (rc == -ETIMEDOUT) {
		printk("OTA prime retry once after timeout\n");
		k_sleep(K_MSEC(150));
		rc = ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_OS, 0U);
	}
	return rc;
}

static void ota_try_schedule_start(void)
{
	if (!ota_ready || !mtu_ready || ota_started || ota_done || default_conn == NULL) {
		return;
	}

	if (!ota_start_queued) {
		ota_start_queued = true;
		k_sem_give(&ota_start_sem);
	}
}

static void ota_thread_fn(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	while (1) {
		k_sem_take(&ota_start_sem, K_FOREVER);
		ota_start_queued = false;

		if (!ota_ready || !mtu_ready || ota_started || ota_done || default_conn == NULL) {
			continue;
		}

		printk("OTA start gate: mtu=%u conn=%p\n",
		       (unsigned int)bt_gatt_get_mtu(default_conn), default_conn);
		ota_started = true;
		ota_status = ota_prime_link(&dfu_smp);
		if (ota_status) {
			printk("OTA prime failed: %d\n", ota_status);
			master_leds_set(false, true, false, true);
			ota_done = true;
			continue;
		}

		ota_status = ota_erase_secondary_slot(&dfu_smp);
		if (ota_status) {
			printk("OTA erase failed: %d\n", ota_status);
			master_leds_set(false, true, false, true);
			ota_done = true;
			continue;
		}

		ota_status = ota_upload_image(&dfu_smp);
		if (ota_status) {
			printk("OTA upload failed: %d\n", ota_status);
			master_leds_set(false, true, false, true);
			ota_done = true;
			continue;
		}

		ota_status = ota_read_image_state(&dfu_smp);
		if (ota_status) {
			printk("OTA state read failed: %d\n", ota_status);
			printk("OTA continuing to pending/test despite state read failure\n");
		}

		ota_status = ota_schedule_pending(&dfu_smp);
		if (ota_status) {
			printk("OTA schedule failed: %d\n", ota_status);
			master_leds_set(false, true, false, true);
			ota_done = true;
			continue;
		}

		ota_status = ota_remote_reset(&dfu_smp);
		if (ota_status) {
			printk("OTA reset failed: %d\n", ota_status);
			master_leds_set(false, true, false, true);
			ota_done = true;
			continue;
		}

		printk("OTA command sequence sent\n");
		ota_done = true;
		master_leds_set(false, true, false, false);
	}
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
		ota_try_schedule_start();
	}
}

static void discovery_complete(struct bt_gatt_dm *dm, void *context)
{
	ARG_UNUSED(context);
	int err;

	if (discovery_phase == DISCOVERY_PHASE_NUS) {
		err = bt_nus_handles_assign(dm, &nus_client);
		if (err) {
			printk("NUS handle assign failed: %d\n", err);
		} else {
			err = bt_nus_subscribe_receive(&nus_client);
			if (err) {
				printk("NUS subscribe failed: %d\n", err);
			} else {
				nus_ready = true;
				printk("NUS service ready\n");
				err = ota_arm_target_via_nus();
				if (err) {
					printk("OTA arm via NUS failed: %d\n", err);
				}
			}
		}

		bt_gatt_dm_data_release(dm);
		discovery_phase = DISCOVERY_PHASE_DFU;
		gatt_discover_dfu(default_conn);
		return;
	}

	err = bt_dfu_smp_handles_assign(dm, &dfu_smp);
	if (err) {
		printk("DFU SMP handle assign failed: %d\n", err);
		bt_gatt_dm_data_release(dm);
		return;
	}

	ota_ready = true;
	printk("DFU SMP service ready\n");
	printk("DFU handles: smp=0x%04x ccc=0x%04x\n",
	       dfu_smp.handles.smp, dfu_smp.handles.smp_ccc);
	master_leds_set(false, true, false, false);
	ota_try_schedule_start();

	bt_gatt_dm_data_release(dm);
}

static void discovery_service_not_found(struct bt_conn *conn, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);

	if (discovery_phase == DISCOVERY_PHASE_NUS) {
		printk("NUS service not found, continuing with DFU\n");
		discovery_phase = DISCOVERY_PHASE_DFU;
		gatt_discover_dfu(default_conn);
		return;
	}

	printk("DFU SMP service not found\n");
	ota_status = -ENOENT;
	master_leds_set(true, false, false, true);
}

static void discovery_error(struct bt_conn *conn, int err, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);

	if (discovery_phase == DISCOVERY_PHASE_NUS) {
		printk("NUS discovery error: %d, continuing with DFU\n", err);
		discovery_phase = DISCOVERY_PHASE_DFU;
		gatt_discover_dfu(default_conn);
		return;
	}

	printk("GATT discovery error: %d\n", err);
	ota_status = err;
	master_leds_set(true, false, false, true);
}

static struct bt_gatt_dm_cb discovery_cb = {
	.completed = discovery_complete,
	.service_not_found = discovery_service_not_found,
	.error_found = discovery_error,
};

static void gatt_discover_nus(struct bt_conn *conn)
{
	int err;

	err = bt_gatt_dm_start(conn, BT_UUID_NUS_SERVICE, &discovery_cb, NULL);
	if (err) {
		printk("Could not start NUS discovery: %d\n", err);
		discovery_phase = DISCOVERY_PHASE_DFU;
		gatt_discover_dfu(conn);
	}
}

static void gatt_discover_dfu(struct bt_conn *conn)
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
	struct bt_conn_info info;

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
	if (bt_conn_get_info(conn, &info) == 0 && info.type == BT_CONN_TYPE_LE) {
		printk("Conn LE params: int=%u lat=%u timeout=%u\n",
		       info.le.interval, info.le.latency, info.le.timeout);
	}
	exchange_params.func = exchange_func;
	(void)bt_gatt_exchange_mtu(conn, &exchange_params);
	discovery_phase = DISCOVERY_PHASE_NUS;
	gatt_discover_nus(conn);
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
	ota_start_queued = false;
	nus_ready = false;
	smp_subscribed = false;
	memset(&smp_sub_params, 0, sizeof(smp_sub_params));
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
	k_sem_init(&ota_start_sem, 0, 1);
	k_sem_init(&smp_write_sem, 0, 1);
	k_sem_init(&nus_write_sem, 0, 1);
	k_thread_create(&ota_thread, ota_thread_stack, K_THREAD_STACK_SIZEOF(ota_thread_stack),
			ota_thread_fn, NULL, NULL, NULL, 5, 0, K_NO_WAIT);

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
	ota_start_queued = false;
	mtu_ready = false;
	ota_status = 0;
	ota_seq = 1U;

	err = bt_enable(NULL);
	if (err) {
		printk("Bluetooth init failed: %d\n", err);
		return err;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	err = nus_client_init();
	if (err) {
		printk("NUS client init failed: %d\n", err);
		return err;
	}

	err = scan_init();
	if (err) {
		return err;
	}

	printk("BioSpur BLE OTA master ready on nRF52840 DK\n");
	printk("Scanning for Tag_rot_ota DFU SMP service\n");

	err = bt_scan_start(BT_SCAN_TYPE_SCAN_PASSIVE);
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
