#include "gr_protocol.h"

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdarg.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>
#include <zephyr/usb/usb_device.h>

#include <bluetooth/gatt_dm.h>
#include <bluetooth/services/dfu_smp.h>
#include <bluetooth/services/nus.h>
#include <bluetooth/services/nus_client.h>

#include <zcbor_common.h>
#include <zcbor_decode.h>
#include <zcbor_encode.h>

#include "gr_ota_image.inc"

#define OTA_CHUNK_SIZE 448U
#define OTA_CMD_TIMEOUT_SEC 30
#define OTA_SMP_GROUP_IMG 0x0001U
#define OTA_SMP_GROUP_OS 0x0000U
#define OTA_SMP_CMD_IMG_STATE 0x00U
#define OTA_SMP_CMD_IMG_UPLOAD 0x01U
#define OTA_SMP_CMD_IMG_ERASE 0x05U
#define OTA_SMP_CMD_OS_RESET 0x05U
#define OTA_THREAD_STACK_SIZE 4096U
#define OTA_THREAD_PRIORITY 5

static const struct device *const cdc = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

enum master_mode {
	MODE_IDLE = 0,
	MODE_SCAN,
	MODE_RX,
	MODE_OTA,
};

enum discovery_phase {
	DISCOVERY_NUS = 0,
	DISCOVERY_DFU,
};

struct adv_parse_ctx {
	char name[32];
	bool name_seen;
	bool gr_mfg_seen;
	uint16_t gr_id;
};

struct smp_packet {
	struct bt_dfu_smp_header header;
	uint8_t payload[512];
};

struct ota_cmd_result {
	int status;
	size_t off;
	bool off_found;
};

static char line_buf[96];
static size_t line_len;
static bool scan_active;
static enum master_mode mode;
static struct bt_conn *default_conn;
static struct bt_nus_client nus_client;
static struct bt_dfu_smp dfu_smp;
static struct bt_gatt_exchange_params exchange_params;
static enum discovery_phase discovery_phase;
static bool nus_ready;
static bool dfu_ready;
static bool mtu_ready;
static bool ota_started;
static bool ota_done;
static struct k_sem nus_write_sem;
static struct k_sem ota_sem;
static struct k_sem ota_start_sem;
static struct k_sem smp_sub_sem;
static struct k_sem smp_write_sem;
K_THREAD_STACK_DEFINE(ota_thread_stack, OTA_THREAD_STACK_SIZE);
static struct k_thread ota_thread;
static struct bt_gatt_write_params smp_write_params;
static uint8_t ota_seq = 1U;
static uint8_t smp_rsp_buf[512];
static size_t smp_rsp_len;
static size_t smp_rsp_total;
static bool smp_notify_subscribed;
static int smp_subscribe_err;
static int smp_write_err;
static uint16_t smp_inflight_group;
static uint8_t smp_inflight_cmd;
static uint8_t smp_inflight_seq;

static const struct bt_conn_le_phy_param *const fast_phy_params =
	BT_CONN_LE_PHY_PARAM_2M;
static const struct bt_le_conn_param *const fast_conn_params =
	BT_LE_CONN_PARAM(6, 6, 0, 400);

static bool ota_inflight_is_upload(void)
{
	return smp_inflight_group == OTA_SMP_GROUP_IMG &&
	       smp_inflight_cmd == OTA_SMP_CMD_IMG_UPLOAD;
}

static const char *mode_name(enum master_mode m)
{
	switch (m) {
	case MODE_SCAN:
		return "scan";
	case MODE_RX:
		return "rx";
	case MODE_OTA:
		return "ota";
	default:
		return "idle";
	}
}

static void cdc_write(const char *s)
{
	while (*s != '\0') {
		uart_poll_out(cdc, *s++);
	}
}

static void cdc_printf(const char *fmt, ...)
{
	char buf[256];
	va_list args;
	int n;

	va_start(args, fmt);
	n = vsnprintk(buf, sizeof(buf), fmt, args);
	va_end(args);

	if (n < 0) {
		return;
	}

	buf[sizeof(buf) - 1U] = '\0';
	cdc_write(buf);
}

static void cdc_write_hex8(uint8_t v)
{
	static const char h[] = "0123456789ABCDEF";

	uart_poll_out(cdc, h[v >> 4]);
	uart_poll_out(cdc, h[v & 0x0f]);
}

static void cdc_write_hex_buf(const uint8_t *data, size_t len)
{
	for (size_t i = 0; i < len; i++) {
		cdc_write_hex8(data[i]);
	}
}

static bool parse_ad(struct bt_data *data, void *user_data)
{
	struct adv_parse_ctx *ctx = user_data;

	if (data->type == BT_DATA_NAME_COMPLETE || data->type == BT_DATA_NAME_SHORTENED) {
		size_t n = MIN(data->data_len, sizeof(ctx->name) - 1U);

		memcpy(ctx->name, data->data, n);
		ctx->name[n] = '\0';
		ctx->name_seen = true;
	}

	if (data->type == BT_DATA_MANUFACTURER_DATA && data->data_len >= 8U &&
	    data->data[2] == GR_ADV_MFG_MAGIC0 &&
	    data->data[3] == GR_ADV_MFG_MAGIC1 &&
	    data->data[4] == GR_ADV_MFG_VERSION) {
		ctx->gr_mfg_seen = true;
		ctx->gr_id = sys_get_le16(&data->data[6]);
	}

	return true;
}

static bool adv_is_gr_target(const struct adv_parse_ctx *ctx)
{
	if (!ctx->name_seen ||
	    strncmp(ctx->name, GR_NAME_PREFIX, strlen(GR_NAME_PREFIX)) != 0) {
		return false;
	}

	return strncmp(ctx->name, GR_MASTER_NAME, strlen(GR_MASTER_NAME)) != 0;
}

static void reset_peer_state(void)
{
	nus_ready = false;
	dfu_ready = false;
	mtu_ready = false;
	ota_started = false;
	ota_done = false;
	smp_rsp_len = 0U;
	smp_rsp_total = 0U;
	smp_notify_subscribed = false;
	smp_subscribe_err = 0;
	smp_write_err = 0;
}

static void scan_stop_quiet(void)
{
	if (scan_active) {
		(void)bt_le_scan_stop();
		scan_active = false;
	}
}

static void gatt_discover_nus(struct bt_conn *conn);
static void gatt_discover_dfu(struct bt_conn *conn);

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	struct adv_parse_ctx ctx = { 0 };
	char addr_s[BT_ADDR_LE_STR_LEN];
	bool connectable = (type == BT_GAP_ADV_TYPE_ADV_IND ||
			    type == BT_GAP_ADV_TYPE_ADV_DIRECT_IND);
	struct bt_conn *conn = NULL;
	int err;

	if (type != BT_GAP_ADV_TYPE_ADV_IND &&
	    type != BT_GAP_ADV_TYPE_ADV_DIRECT_IND &&
	    type != BT_GAP_ADV_TYPE_ADV_SCAN_IND) {
		return;
	}

	bt_data_parse(ad, parse_ad, &ctx);
	if (!adv_is_gr_target(&ctx)) {
		return;
	}

	bt_addr_le_to_str(addr, addr_s, sizeof(addr_s));
	cdc_printf("[BioSpur-GR] found name=%s addr=%s rssi=%d conn=%u",
		   ctx.name, addr_s, rssi, connectable ? 1U : 0U);
	if (ctx.gr_mfg_seen) {
		cdc_printf(" id=0x%04X", ctx.gr_id);
	}
	cdc_write("\r\n");

	if (mode == MODE_SCAN || !connectable || default_conn != NULL) {
		return;
	}

	scan_stop_quiet();
	cdc_printf("[BioSpur-GR] connect start name=%s addr=%s mode=%s\r\n",
		   ctx.name, addr_s, mode_name(mode));
	err = bt_conn_le_create(addr, BT_CONN_LE_CREATE_CONN, fast_conn_params, &conn);
	if (err) {
		cdc_printf("[BioSpur-GR] connect start failed err=%d\r\n", err);
		return;
	}

	default_conn = conn;
}

static void scan_start(enum master_mode new_mode)
{
	struct bt_le_scan_param scan_param = {
		.type = BT_LE_SCAN_TYPE_PASSIVE,
		.options = BT_LE_SCAN_OPT_NONE,
		.interval = BT_GAP_SCAN_FAST_INTERVAL,
		.window = BT_GAP_SCAN_FAST_WINDOW,
	};
	int err;

	mode = new_mode;

	if (default_conn != NULL && mode != MODE_SCAN) {
		cdc_printf("[BioSpur-GR] already connected mode=%s\r\n", mode_name(mode));
		return;
	}

	if (scan_active) {
		cdc_printf("[BioSpur-GR] scan already active mode=%s\r\n", mode_name(mode));
		return;
	}

	err = bt_le_scan_start(&scan_param, device_found);
	if (err) {
		cdc_printf("[BioSpur-GR] scan start failed err=%d\r\n", err);
		return;
	}

	scan_active = true;
	cdc_printf("[BioSpur-GR] scan started mode=%s filter=GR* exclude=%s\r\n",
		   mode_name(mode), GR_MASTER_NAME);
}

static void scan_stop(void)
{
	if (!scan_active) {
		cdc_write("[BioSpur-GR] scan already stopped\r\n");
		return;
	}

	scan_stop_quiet();
	cdc_write("[BioSpur-GR] scan stopped\r\n");
}

static void disconnect_current(void)
{
	if (default_conn == NULL) {
		cdc_write("[BioSpur-GR] no active connection\r\n");
		return;
	}

	(void)bt_conn_disconnect(default_conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
	cdc_write("[BioSpur-GR] disconnect requested\r\n");
}

static void nus_data_sent(struct bt_nus_client *nus, uint8_t err,
			  const uint8_t *const data, uint16_t len)
{
	ARG_UNUSED(nus);
	ARG_UNUSED(data);
	ARG_UNUSED(len);

	if (err) {
		cdc_printf("[BioSpur-GR] NUS write error=0x%02x\r\n", err);
	}
	k_sem_give(&nus_write_sem);
}

static void send_tsync(void)
{
	uint8_t frame[10];
	int err;

	if (!nus_ready) {
		return;
	}

	frame[0] = GR_PACKET_MAGIC;
	frame[1] = GR_TYPE_TSYNC;
	sys_put_le64((uint64_t)k_uptime_get(), &frame[2]);

	k_sem_reset(&nus_write_sem);
	err = bt_nus_client_send(&nus_client, frame, sizeof(frame));
	if (err) {
		cdc_printf("[BioSpur-GR] TSYNC send failed err=%d\r\n", err);
		return;
	}
	(void)k_sem_take(&nus_write_sem, K_MSEC(500));
}

static uint8_t nus_data_received(struct bt_nus_client *nus,
				 const uint8_t *data, uint16_t len)
{
	ARG_UNUSED(nus);

	if (len >= sizeof(struct gr_packet_header) && data[0] == GR_PACKET_MAGIC) {
		const struct gr_packet_header *hdr = (const struct gr_packet_header *)data;
		uint16_t seq = sys_get_le16((const uint8_t *)&hdr->seq);
		uint16_t dev = sys_get_le16((const uint8_t *)&hdr->device_id);
		uint32_t ts = sys_get_le32((const uint8_t *)&hdr->timestamp_ms);

		cdc_printf("RECV_HEX mcu_ms=%u type=%c seq=%u dev=0x%04X ts=%u len=%u",
			   k_uptime_get_32(), hdr->type, seq, dev, ts, len);

		if (hdr->type == GR_TYPE_ADS1298 && len >= sizeof(*hdr) + 4U) {
			const uint8_t *payload = data + sizeof(*hdr);
			uint8_t sample_count = payload[0];
			uint8_t channel_mask = payload[1];
			uint16_t rate = sys_get_le16(&payload[2]);

			cdc_printf(" samples=%u mask=0x%02X rate=%u",
				   sample_count, channel_mask, rate);
		}

		cdc_write(" data=");
		cdc_write_hex_buf(data, len);
		cdc_write("\r\n");
		return BT_GATT_ITER_CONTINUE;
	}

	cdc_printf("NUS_HEX mcu_ms=%u len=%u data=", k_uptime_get_32(), len);
	cdc_write_hex_buf(data, len);
	cdc_write("\r\n");
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

static void dfu_error_cb(struct bt_dfu_smp *smp, int err)
{
	ARG_UNUSED(smp);

	cdc_printf("[BioSpur-GR] DFU SMP error=%d\r\n", err);
}

static const struct bt_dfu_smp_init_params dfu_init_params = {
	.error_cb = dfu_error_cb,
};

static void exchange_func(struct bt_conn *conn, uint8_t err,
			  struct bt_gatt_exchange_params *params)
{
	ARG_UNUSED(params);

	if (err) {
		cdc_printf("[BioSpur-GR] MTU exchange failed err=%u\r\n", err);
		return;
	}

	mtu_ready = true;
	cdc_printf("[BioSpur-GR] MTU exchange done mtu=%u\r\n",
		   (unsigned int)bt_gatt_get_mtu(conn));
}

static void discovery_complete(struct bt_gatt_dm *dm, void *context)
{
	int err;

	ARG_UNUSED(context);

	if (discovery_phase == DISCOVERY_NUS) {
		err = bt_nus_handles_assign(dm, &nus_client);
		if (err) {
			cdc_printf("[BioSpur-GR] NUS handle assign failed err=%d\r\n", err);
		} else {
			err = bt_nus_subscribe_receive(&nus_client);
			if (err) {
				cdc_printf("[BioSpur-GR] NUS subscribe failed err=%d\r\n", err);
			} else {
				nus_ready = true;
				cdc_write("[BioSpur-GR] NUS RX ready\r\n");
				send_tsync();
			}
		}

		bt_gatt_dm_data_release(dm);
		discovery_phase = DISCOVERY_DFU;
		gatt_discover_dfu(default_conn);
		return;
	}

	err = bt_dfu_smp_handles_assign(dm, &dfu_smp);
	if (err) {
		cdc_printf("[BioSpur-GR] DFU SMP handle assign failed err=%d\r\n", err);
		bt_gatt_dm_data_release(dm);
		return;
	}

	dfu_ready = true;
	cdc_printf("[BioSpur-GR] DFU SMP ready smp=0x%04X ccc=0x%04X\r\n",
		   dfu_smp.handles.smp, dfu_smp.handles.smp_ccc);
	bt_gatt_dm_data_release(dm);

	if (mode == MODE_OTA && !ota_started && !ota_done) {
		k_sem_give(&ota_start_sem);
	}
}

static void discovery_service_not_found(struct bt_conn *conn, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);

	if (discovery_phase == DISCOVERY_NUS) {
		cdc_write("[BioSpur-GR] NUS service not found, continue DFU discovery\r\n");
		discovery_phase = DISCOVERY_DFU;
		gatt_discover_dfu(default_conn);
		return;
	}

	cdc_write("[BioSpur-GR] DFU SMP service not found\r\n");
}

static void discovery_error(struct bt_conn *conn, int err, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);

	if (discovery_phase == DISCOVERY_NUS) {
		cdc_printf("[BioSpur-GR] NUS discovery error=%d, continue DFU\r\n", err);
		discovery_phase = DISCOVERY_DFU;
		gatt_discover_dfu(default_conn);
		return;
	}

	cdc_printf("[BioSpur-GR] GATT discovery error=%d\r\n", err);
}

static struct bt_gatt_dm_cb discovery_cb = {
	.completed = discovery_complete,
	.service_not_found = discovery_service_not_found,
	.error_found = discovery_error,
};

static void gatt_discover_nus(struct bt_conn *conn)
{
	int err;

	if (conn == NULL) {
		return;
	}

	discovery_phase = DISCOVERY_NUS;
	err = bt_gatt_dm_start(conn, BT_UUID_NUS_SERVICE, &discovery_cb, NULL);
	if (err) {
		cdc_printf("[BioSpur-GR] NUS discovery start failed err=%d\r\n", err);
		gatt_discover_dfu(conn);
	}
}

static void gatt_discover_dfu(struct bt_conn *conn)
{
	int err;

	if (conn == NULL) {
		return;
	}

	discovery_phase = DISCOVERY_DFU;
	err = bt_gatt_dm_start(conn, BT_UUID_DFU_SMP_SERVICE, &discovery_cb, NULL);
	if (err) {
		cdc_printf("[BioSpur-GR] DFU discovery start failed err=%d\r\n", err);
	}
}

static void connected(struct bt_conn *conn, uint8_t conn_err)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int err;

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	if (conn_err) {
		cdc_printf("[BioSpur-GR] connect failed addr=%s err=0x%02x\r\n",
			   addr, conn_err);
		if (default_conn == conn) {
			bt_conn_unref(default_conn);
			default_conn = NULL;
		}
		reset_peer_state();
		return;
	}

	if (conn != default_conn) {
		cdc_printf("[BioSpur-GR] unexpected connection %s, disconnecting\r\n", addr);
		(void)bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		return;
	}

	cdc_printf("[BioSpur-GR] connected addr=%s mode=%s\r\n", addr, mode_name(mode));

	err = bt_conn_le_phy_update(conn, fast_phy_params);
	cdc_printf("[BioSpur-GR] PHY 2M request rc=%d\r\n", err);
	err = bt_conn_le_param_update(conn, fast_conn_params);
	cdc_printf("[BioSpur-GR] conn param request rc=%d\r\n", err);

	exchange_params.func = exchange_func;
	err = bt_gatt_exchange_mtu(conn, &exchange_params);
	if (err) {
		cdc_printf("[BioSpur-GR] MTU exchange start failed err=%d\r\n", err);
	}

	if (mode == MODE_OTA) {
		cdc_write("[BioSpur-GR] OTA mode: skip NUS subscription\r\n");
		gatt_discover_dfu(conn);
	} else {
		gatt_discover_nus(conn);
	}
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	cdc_printf("[BioSpur-GR] disconnected addr=%s reason=0x%02x\r\n", addr, reason);

	if (conn == default_conn) {
		bt_conn_unref(default_conn);
		default_conn = NULL;
	}
	reset_peer_state();
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
};

static int cbor_decode_uint_any(const uint8_t *buf, size_t len, uint64_t *value,
				size_t *used)
{
	uint8_t major;
	uint8_t ai;
	uint64_t v = 0U;
	size_t need;

	if (buf == NULL || len == 0U || value == NULL || used == NULL) {
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
		v = ((uint64_t)buf[1] << 8) | buf[2];
		need = 3U;
	} else if (ai == 26U) {
		if (len < 5U) {
			return -EMSGSIZE;
		}
		v = ((uint64_t)buf[1] << 24) | ((uint64_t)buf[2] << 16) |
		    ((uint64_t)buf[3] << 8) | buf[4];
		need = 5U;
	} else {
		return -EBADMSG;
	}

	if (major == 1U) {
		if (v > INT32_MAX) {
			return -ERANGE;
		}
		*value = (uint64_t)(-(int64_t)(v + 1U));
	} else {
		*value = v;
	}
	*used = need;
	return 0;
}

static int ota_parse_response(struct ota_cmd_result *result)
{
	const struct bt_dfu_smp_header *header;
	const uint8_t *payload;
	size_t payload_len;
	bool any = false;

	if (smp_rsp_len < sizeof(*header)) {
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
	result->status = 0;
	result->off = 0U;
	result->off_found = false;

	for (size_t i = 0; i < payload_len;) {
		if (i + 4U <= payload_len &&
		    payload[i] == 0x63U && payload[i + 1] == 'o' &&
		    payload[i + 2] == 'f' && payload[i + 3] == 'f') {
			uint64_t v = 0U;
			size_t used = 0U;

			if (cbor_decode_uint_any(&payload[i + 4U],
						 payload_len - (i + 4U),
						 &v, &used) == 0) {
				result->off = (size_t)v;
				result->off_found = true;
				any = true;
				i += 4U + used;
				continue;
			}
		}

		if (i + 3U <= payload_len &&
		    payload[i] == 0x62U && payload[i + 1] == 'r' &&
		    payload[i + 2] == 'c') {
			uint64_t v = 0U;
			size_t used = 0U;
			uint8_t major;

			if (cbor_decode_uint_any(&payload[i + 3U],
						 payload_len - (i + 3U),
						 &v, &used) == 0) {
				major = (uint8_t)(payload[i + 3U] >> 5);
				if (major == 0U) {
					result->status = (int)v;
				} else {
					result->status = (int)(int64_t)v;
				}
				any = true;
				i += 3U + used;
				continue;
			}
		}

		if (i + 7U <= payload_len &&
		    payload[i] == 0x66U && memcmp(&payload[i + 1], "images", 6) == 0) {
			any = true;
			i += 7U;
			continue;
		}

		i++;
	}

	if (!any && payload_len == 0U) {
		result->status = 0;
	}

	return result->status;
}

static uint8_t ota_smp_notify_cb(struct bt_conn *conn,
				 struct bt_gatt_subscribe_params *params,
				 const void *data, uint16_t length)
{
	const struct bt_dfu_smp_header *header;
	size_t copy_len;
	size_t start_len;

	ARG_UNUSED(params);

	if (conn != default_conn) {
		cdc_printf("[BioSpur-GR] OTA rsp ignored conn=%p expect=%p len=%u\r\n",
			   conn, default_conn, length);
		return BT_GATT_ITER_CONTINUE;
	}

	if (data == NULL) {
		smp_notify_subscribed = false;
		return BT_GATT_ITER_STOP;
	}

	if (length == 0U) {
		return BT_GATT_ITER_CONTINUE;
	}

	if (smp_rsp_len == 0U && length >= sizeof(*header)) {
		header = (const struct bt_dfu_smp_header *)data;
		smp_rsp_total = ((((uint16_t)header->len_h8) << 8) | header->len_l8) +
				sizeof(*header);
		if (smp_rsp_total > sizeof(smp_rsp_buf)) {
			smp_rsp_total = sizeof(smp_rsp_buf);
		}
	}

	start_len = smp_rsp_len;
	if (smp_rsp_len < sizeof(smp_rsp_buf)) {
		copy_len = MIN((size_t)length, sizeof(smp_rsp_buf) - smp_rsp_len);
		memcpy(&smp_rsp_buf[smp_rsp_len], data, copy_len);
		smp_rsp_len += copy_len;
	}

	if (!ota_inflight_is_upload()) {
		cdc_printf("[BioSpur-GR] OTA rsp part group=0x%04X cmd=0x%02X seq=%u off=%u chunk=%u acc=%u total=%u\r\n",
			   smp_inflight_group, smp_inflight_cmd, smp_inflight_seq,
			   (unsigned int)start_len, length, (unsigned int)smp_rsp_len,
			   (unsigned int)smp_rsp_total);
	}

	if (smp_rsp_total > 0U && smp_rsp_len >= smp_rsp_total) {
		k_sem_give(&ota_sem);
	}

	return BT_GATT_ITER_CONTINUE;
}

static void ota_smp_subscribe_cb(struct bt_conn *conn, uint8_t err,
				 struct bt_gatt_subscribe_params *params)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(params);

	smp_subscribe_err = (int)err;
	k_sem_give(&smp_sub_sem);
}

static void ota_smp_write_cb(struct bt_conn *conn, uint8_t err,
			     struct bt_gatt_write_params *params)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(params);

	smp_write_err = (int)err;
	k_sem_give(&smp_write_sem);
}

static int ota_smp_subscribe_if_needed(struct bt_dfu_smp *smp)
{
	int rc;

	if (smp == NULL || smp->conn == NULL ||
	    smp->handles.smp == 0U || smp->handles.smp_ccc == 0U) {
		return -ENOTCONN;
	}

	if (smp_notify_subscribed) {
		return 0;
	}

	memset(&smp->notification_params, 0, sizeof(smp->notification_params));
	smp->notification_params.value_handle = smp->handles.smp;
	smp->notification_params.ccc_handle = smp->handles.smp_ccc;
	smp->notification_params.notify = ota_smp_notify_cb;
	smp->notification_params.subscribe = ota_smp_subscribe_cb;
	smp->notification_params.value = BT_GATT_CCC_NOTIFY;
	atomic_set_bit(smp->notification_params.flags, BT_GATT_SUBSCRIBE_FLAG_VOLATILE);

	smp_subscribe_err = -EINPROGRESS;
	k_sem_reset(&smp_sub_sem);
	rc = bt_gatt_subscribe(smp->conn, &smp->notification_params);
	if (rc == -EALREADY) {
		smp_notify_subscribed = true;
		cdc_printf("[BioSpur-GR] OTA SMP subscribe already active smp=0x%04X ccc=0x%04X\r\n",
			   smp->handles.smp, smp->handles.smp_ccc);
		return 0;
	}
	if (rc) {
		cdc_printf("[BioSpur-GR] OTA SMP subscribe failed rc=%d smp=0x%04X ccc=0x%04X\r\n",
			   rc, smp->handles.smp, smp->handles.smp_ccc);
		return rc;
	}

	if (k_sem_take(&smp_sub_sem, K_MSEC(3000)) != 0) {
		(void)bt_gatt_unsubscribe(smp->conn, &smp->notification_params);
		cdc_printf("[BioSpur-GR] OTA SMP subscribe timeout smp=0x%04X ccc=0x%04X\r\n",
			   smp->handles.smp, smp->handles.smp_ccc);
		return -ETIMEDOUT;
	}

	if (smp_subscribe_err != 0) {
		(void)bt_gatt_unsubscribe(smp->conn, &smp->notification_params);
		cdc_printf("[BioSpur-GR] OTA SMP subscribe cb error err=%d smp=0x%04X ccc=0x%04X\r\n",
			   smp_subscribe_err, smp->handles.smp, smp->handles.smp_ccc);
		return -EACCES;
	}

	smp_notify_subscribed = true;
	cdc_printf("[BioSpur-GR] OTA SMP subscribe ok smp=0x%04X ccc=0x%04X\r\n",
		   smp->handles.smp, smp->handles.smp_ccc);
	return 0;
}

static int ota_send_packet(struct smp_packet *pkt, size_t payload_len,
			   struct ota_cmd_result *result, uint16_t group_id,
			   uint8_t command_id, uint8_t smp_op)
{
	int rc;
	size_t tx_len;
	uint16_t mtu_payload;
	bool verbose_cmd = !(group_id == OTA_SMP_GROUP_IMG &&
			     command_id == OTA_SMP_CMD_IMG_UPLOAD);

	pkt->header.op = smp_op;
	pkt->header.flags = 0U;
	pkt->header.len_h8 = (uint8_t)((payload_len >> 8) & 0xffU);
	pkt->header.len_l8 = (uint8_t)(payload_len & 0xffU);
	pkt->header.group_h8 = (uint8_t)((group_id >> 8) & 0xffU);
	pkt->header.group_l8 = (uint8_t)(group_id & 0xffU);
	pkt->header.seq = ota_seq++;
	pkt->header.id = command_id;
	smp_inflight_group = group_id;
	smp_inflight_cmd = command_id;
	smp_inflight_seq = pkt->header.seq;

	tx_len = sizeof(pkt->header) + payload_len;
	k_sem_reset(&ota_sem);
	smp_rsp_len = 0U;
	smp_rsp_total = 0U;
	result->status = -ETIMEDOUT;
	result->off = 0U;
	result->off_found = false;

	if (verbose_cmd) {
		cdc_printf("[BioSpur-GR] OTA send op=%u group=0x%04X cmd=0x%02X seq=%u len=%u\r\n",
			   smp_op, group_id, command_id, pkt->header.seq,
			   (unsigned int)tx_len);
	}

	if (dfu_smp.conn == NULL) {
		return -ENOTCONN;
	}

	mtu_payload = bt_gatt_get_mtu(dfu_smp.conn) > 3U ?
		      (bt_gatt_get_mtu(dfu_smp.conn) - 3U) : 20U;
	if (tx_len > mtu_payload) {
		cdc_printf("[BioSpur-GR] OTA command too large len=%u mtu_payload=%u\r\n",
			   (unsigned int)tx_len, mtu_payload);
		return -EMSGSIZE;
	}

	rc = ota_smp_subscribe_if_needed(&dfu_smp);
	if (rc) {
		cdc_printf("[BioSpur-GR] OTA command blocked: subscribe rc=%d\r\n", rc);
		return rc;
	}

	memset(&smp_write_params, 0, sizeof(smp_write_params));
	smp_write_params.handle = dfu_smp.handles.smp;
	smp_write_params.offset = 0U;
	smp_write_params.data = pkt;
	smp_write_params.length = tx_len;
	smp_write_params.func = ota_smp_write_cb;
	smp_write_err = -EINPROGRESS;
	k_sem_reset(&smp_write_sem);

	rc = bt_gatt_write(dfu_smp.conn, &smp_write_params);
	if (verbose_cmd) {
		cdc_printf("[BioSpur-GR] OTA command issued(write_req) rc=%d tx_len=%u smp=0x%04X ccc=0x%04X\r\n",
			   rc, (unsigned int)tx_len, dfu_smp.handles.smp,
			   dfu_smp.handles.smp_ccc);
	}
	if (rc) {
		return rc;
	}

	if (k_sem_take(&smp_write_sem, K_MSEC(3000)) != 0) {
		cdc_printf("[BioSpur-GR] OTA write_req timeout group=0x%04X cmd=0x%02X\r\n",
			   group_id, command_id);
		return -ETIMEDOUT;
	}
	if (smp_write_err != 0) {
		cdc_printf("[BioSpur-GR] OTA write_req cb error group=0x%04X cmd=0x%02X err=%d\r\n",
			   group_id, command_id, smp_write_err);
		return -EIO;
	}

	if (k_sem_take(&ota_sem, K_SECONDS(OTA_CMD_TIMEOUT_SEC)) != 0) {
		cdc_printf("[BioSpur-GR] OTA command timeout group=0x%04X cmd=0x%02X subscribed=%u rsp=%u/%u\r\n",
			   group_id, command_id, smp_notify_subscribed ? 1U : 0U,
			   (unsigned int)smp_rsp_len, (unsigned int)smp_rsp_total);
		return -ETIMEDOUT;
	}

	rc = ota_parse_response(result);
	if (verbose_cmd || result->status != 0) {
		cdc_printf("[BioSpur-GR] OTA rsp status=%d off=%u off_found=%u len=%u\r\n",
			   result->status, (unsigned int)result->off,
			   result->off_found ? 1U : 0U, (unsigned int)smp_rsp_len);
	}
	return rc;
}

static size_t ota_build_upload_packet(size_t offset, struct smp_packet *pkt,
				      size_t chunk_len, bool first_chunk)
{
	zcbor_state_t zse[4];
	bool ok;
	uint32_t map_count = first_chunk ? 4U : 3U;

	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt->payload,
			       sizeof(pkt->payload), 0);

	ok = zcbor_map_start_encode(zse, map_count) &&
	     zcbor_tstr_put_lit(zse, "image") &&
	     zcbor_uint32_put(zse, 0U) &&
	     zcbor_tstr_put_lit(zse, "data") &&
	     zcbor_bstr_encode_ptr(zse, gr_ota_image + offset, chunk_len) &&
	     zcbor_tstr_put_lit(zse, "off") &&
	     zcbor_size_put(zse, offset);

	if (ok && first_chunk) {
		ok = zcbor_tstr_put_lit(zse, "len") &&
		     zcbor_size_put(zse, gr_ota_image_len) &&
		     zcbor_tstr_put_lit(zse, "sha") &&
		     zcbor_bstr_encode_ptr(zse, gr_ota_image_sha256,
					   sizeof(gr_ota_image_sha256));
	}

	if (ok) {
		ok = zcbor_map_end_encode(zse, map_count);
	}

	return ok ? (size_t)(zse->payload - pkt->payload) : 0U;
}

static int ota_upload_image(void)
{
	size_t offset = 0U;
	size_t remaining = gr_ota_image_len;
	bool first_chunk = true;
	unsigned int last_percent = 0U;
	struct smp_packet pkt;
	struct ota_cmd_result result;
	int rc;

	cdc_printf("[BioSpur-GR] OTA upload start image_len=%u sha=",
		   (unsigned int)gr_ota_image_len);
	cdc_write_hex_buf(gr_ota_image_sha256, sizeof(gr_ota_image_sha256));
	cdc_write("\r\n");

	while (remaining > 0U) {
		size_t max_att_payload = (default_conn != NULL &&
					  bt_gatt_get_mtu(default_conn) > 3U) ?
						 (bt_gatt_get_mtu(default_conn) - 3U) : 20U;
		size_t chunk_len = MIN(remaining, OTA_CHUNK_SIZE);
		size_t payload_len = 0U;
		size_t expected_next;
		bool fit = false;

		memset(&pkt, 0, sizeof(pkt));
		while (chunk_len > 0U) {
			payload_len = ota_build_upload_packet(offset, &pkt, chunk_len,
							     first_chunk);
			if (payload_len != 0U &&
			    sizeof(pkt.header) + payload_len <= max_att_payload) {
				fit = true;
				break;
			}
			chunk_len /= 2U;
		}

		if (!fit) {
			cdc_printf("[BioSpur-GR] OTA packet build/fit failed off=%u mtu=%u\r\n",
				   (unsigned int)offset,
				   default_conn != NULL ? bt_gatt_get_mtu(default_conn) : 0U);
			return -EMSGSIZE;
		}

		expected_next = offset + chunk_len;
		unsigned int percent = (unsigned int)((expected_next * 100U) /
						      gr_ota_image_len);
		if (first_chunk || percent != last_percent || expected_next == gr_ota_image_len) {
			cdc_printf("[BioSpur-GR] OTA upload %u%% (%u/%u)\r\n",
				   percent, (unsigned int)expected_next,
				   (unsigned int)gr_ota_image_len);
			last_percent = percent;
		}

		rc = ota_send_packet(&pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
				     OTA_SMP_CMD_IMG_UPLOAD, 2U);
		if (rc == -ETIMEDOUT) {
			cdc_printf("[BioSpur-GR] OTA upload retry off=%u\r\n",
				   (unsigned int)offset);
			rc = ota_send_packet(&pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
					     OTA_SMP_CMD_IMG_UPLOAD, 2U);
		}
		if (rc) {
			return rc;
		}

		if (result.off_found) {
			if (result.off > gr_ota_image_len) {
				return -EPROTO;
			}
			offset = result.off;
		} else {
			offset = expected_next;
		}
		remaining = gr_ota_image_len - offset;
		first_chunk = false;
	}

	cdc_write("[BioSpur-GR] OTA upload complete\r\n");
	return 0;
}

static int ota_read_image_state(void)
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

	return ota_send_packet(&pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
			       OTA_SMP_CMD_IMG_STATE, 0U);
}

static int ota_erase_secondary_slot(void)
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

	return ota_send_packet(&pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
			       OTA_SMP_CMD_IMG_ERASE, 2U);
}

static int ota_schedule_pending(void)
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
	     zcbor_bstr_encode_ptr(zse, gr_ota_image_image_hash,
				   sizeof(gr_ota_image_image_hash)) &&
	     zcbor_tstr_put_lit(zse, "confirm") &&
	     zcbor_bool_put(zse, false) &&
	     zcbor_map_end_encode(zse, 4U);
	if (!ok) {
		return -EIO;
	}
	payload_len = (size_t)(zse->payload - pkt.payload);

	return ota_send_packet(&pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
			       OTA_SMP_CMD_IMG_STATE, 2U);
}

static int ota_remote_reset(void)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[4];
	bool ok;
	size_t payload_len;
	int rc;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);
	ok = zcbor_map_start_encode(zse, 0U) && zcbor_map_end_encode(zse, 0U);
	if (!ok) {
		return -EIO;
	}
	payload_len = (size_t)(zse->payload - pkt.payload);

	rc = ota_send_packet(&pkt, payload_len, &result, OTA_SMP_GROUP_OS,
			     OTA_SMP_CMD_OS_RESET, 2U);
	if (rc == -ETIMEDOUT) {
		cdc_write("[BioSpur-GR] OTA reset timeout treated as reboot\r\n");
		return 0;
	}
	return rc;
}

static void ota_run_once(void)
{
	int rc;
	int wait_ms = 0;

	if (ota_started || ota_done) {
		return;
	}
	if (!dfu_ready || default_conn == NULL) {
		cdc_write("[BioSpur-GR] OTA blocked: DFU not ready\r\n");
		return;
	}

	ota_started = true;
	cdc_write("[BioSpur-GR] OTA sequence starting\r\n");

	while (!mtu_ready && wait_ms < 3000) {
		k_msleep(100);
		wait_ms += 100;
	}

	if (nus_ready) {
		static const char prep[] = "OTA_PREPARE\n";

		k_sem_reset(&nus_write_sem);
		rc = bt_nus_client_send(&nus_client, (const uint8_t *)prep,
					sizeof(prep) - 1U);
		cdc_printf("[BioSpur-GR] OTA_PREPARE via NUS rc=%d\r\n", rc);
		(void)k_sem_take(&nus_write_sem, K_MSEC(500));
		k_msleep(300);
	}

	rc = ota_read_image_state();
	if (rc) {
		cdc_printf("[BioSpur-GR] OTA image state warning rc=%d\r\n", rc);
	}

	rc = ota_erase_secondary_slot();
	if (rc == -ETIMEDOUT) {
		cdc_write("[BioSpur-GR] OTA erase timeout, continue upload\r\n");
	} else if (rc) {
		cdc_printf("[BioSpur-GR] OTA erase failed rc=%d\r\n", rc);
		ota_done = true;
		return;
	}

	rc = ota_upload_image();
	if (rc) {
		cdc_printf("[BioSpur-GR] OTA upload failed rc=%d\r\n", rc);
		ota_done = true;
		return;
	}

	rc = ota_schedule_pending();
	if (rc) {
		cdc_printf("[BioSpur-GR] OTA schedule warning rc=%d\r\n", rc);
	}

	rc = ota_remote_reset();
	if (rc) {
		cdc_printf("[BioSpur-GR] OTA reset failed rc=%d\r\n", rc);
		ota_done = true;
		return;
	}

	ota_done = true;
	mode = MODE_IDLE;
	if (default_conn != NULL) {
		struct bt_conn *old_conn = default_conn;

		rc = bt_conn_disconnect(old_conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		cdc_printf("[BioSpur-GR] OTA post-reset disconnect rc=%d\r\n", rc);
		default_conn = NULL;
		bt_conn_unref(old_conn);
		nus_ready = false;
		dfu_ready = false;
		mtu_ready = false;
		smp_notify_subscribed = false;
	}
	cdc_write("[BioSpur-GR] OTA sequence complete, target reboot requested\r\n");
}

static void ota_thread_fn(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	while (1) {
		k_sem_take(&ota_start_sem, K_FOREVER);
		ota_run_once();
	}
}

static void handle_line(const char *line)
{
	if (strcmp(line, "status") == 0) {
		cdc_printf("[BioSpur-GR] status ok mode=%s scan=%u conn=%u nus=%u dfu=%u mtu=%u image=%u\r\n",
			   mode_name(mode), scan_active ? 1U : 0U,
			   default_conn != NULL ? 1U : 0U,
			   nus_ready ? 1U : 0U, dfu_ready ? 1U : 0U,
			   mtu_ready ? 1U : 0U,
			   (unsigned int)gr_ota_image_len);
	} else if (strcmp(line, "scan") == 0) {
		scan_start(MODE_SCAN);
	} else if (strcmp(line, "rx") == 0 || strcmp(line, "connect") == 0) {
		reset_peer_state();
		scan_start(MODE_RX);
	} else if (strcmp(line, "ota") == 0) {
		reset_peer_state();
		scan_start(MODE_OTA);
	} else if (strcmp(line, "ota-reset") == 0) {
		if (!dfu_ready) {
			cdc_write("[BioSpur-GR] DFU SMP not ready\r\n");
		} else {
			int rc = ota_remote_reset();

			cdc_printf("[BioSpur-GR] ota-reset rc=%d\r\n", rc);
		}
	} else if (strcmp(line, "disconnect") == 0) {
		disconnect_current();
	} else if (strcmp(line, "stop") == 0) {
		scan_stop();
		mode = MODE_IDLE;
	} else if (strcmp(line, "help") == 0) {
		cdc_write("commands: status | scan | rx | ota | ota-reset | disconnect | stop | help\r\n");
	} else if (line[0] != '\0') {
		cdc_write("[BioSpur-GR] unknown command, try help\r\n");
	}
}

static void poll_cdc(void)
{
	unsigned char ch;

	while (uart_poll_in(cdc, &ch) == 0) {
		if (ch == '\r') {
			continue;
		}

		if (ch == '\n') {
			line_buf[line_len] = '\0';
			handle_line(line_buf);
			line_len = 0;
			continue;
		}

		if (line_len + 1U < sizeof(line_buf)) {
			line_buf[line_len++] = (char)ch;
		} else {
			line_len = 0;
			cdc_write("[BioSpur-GR] command too long\r\n");
		}
	}
}

int main(void)
{
	int err;

	err = usb_enable(NULL);
	if (err) {
		return err;
	}

	if (!device_is_ready(cdc)) {
		return -ENODEV;
	}

	cdc_write("BioSpur-GR native USB CDC ready\r\n");
	cdc_write("[BioSpur-GR] commands: status | scan | rx | ota | help\r\n");

	k_sem_init(&nus_write_sem, 0, 1);
	k_sem_init(&ota_sem, 0, 1);
	k_sem_init(&ota_start_sem, 0, 1);
	k_sem_init(&smp_sub_sem, 0, 1);
	k_sem_init(&smp_write_sem, 0, 1);
	k_thread_create(&ota_thread, ota_thread_stack,
			K_THREAD_STACK_SIZEOF(ota_thread_stack),
			ota_thread_fn, NULL, NULL, NULL,
			OTA_THREAD_PRIORITY, 0, K_NO_WAIT);

	err = bt_enable(NULL);
	if (err) {
		cdc_printf("[BioSpur-GR] bt_enable failed err=%d\r\n", err);
		return err;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	err = nus_client_init();
	if (err) {
		cdc_printf("[BioSpur-GR] NUS client init failed err=%d\r\n", err);
		return err;
	}

	err = bt_dfu_smp_init(&dfu_smp, &dfu_init_params);
	if (err) {
		cdc_printf("[BioSpur-GR] DFU SMP init failed err=%d\r\n", err);
		return err;
	}

	cdc_printf("[BioSpur-GR] BLE ready image_len=%u, type 'rx' for EMG or 'ota'\r\n",
		   (unsigned int)gr_ota_image_len);

	while (1) {
		poll_cdc();
		k_sleep(K_MSEC(5));
	}
}
