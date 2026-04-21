#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#define MAX_TRACKED 32U
#define NAME_LEN 40U
#define UUID_LEN 64U
#define ADDR_LEN BT_ADDR_LE_STR_LEN
#define STALE_ONLINE_MS 3000U
#define EMIT_MIN_INTERVAL_MS 200U

enum peer_kind {
	PEER_KIND_UNKNOWN = 0,
	PEER_KIND_ANCHOR,
	PEER_KIND_TAG,
};

struct peer_state {
	bool used;
	enum peer_kind kind;
	char address[ADDR_LEN];
	char name[NAME_LEN];
	char uuid[UUID_LEN];
	char bs_code[8];
	int8_t rssi;
	uint32_t last_seen_ms;
	uint32_t last_emit_ms;
	uint8_t anchor_id_cfg;
	uint8_t role_code;
	uint8_t tag_id;
	uint16_t identity_code;
};

struct parse_ctx {
	enum peer_kind kind;
	char name[NAME_LEN];
	char uuid[UUID_LEN];
	char bs_code[8];
	uint8_t anchor_id_cfg;
	uint8_t role_code;
	uint8_t tag_id;
	uint16_t identity_code;
	bool saw_name;
	bool saw_anchor;
	bool saw_tag;
};

static struct peer_state g_peers[MAX_TRACKED];

static void hex_encode(const uint8_t *src, size_t len, char *dst, size_t dst_len)
{
	static const char hex[] = "0123456789ABCDEF";
	size_t pos = 0U;

	if (dst_len == 0U) {
		return;
	}

	for (size_t i = 0U; i < len && pos + 2U < dst_len; ++i) {
		dst[pos++] = hex[(src[i] >> 4) & 0x0FU];
		dst[pos++] = hex[src[i] & 0x0FU];
	}
	dst[pos] = '\0';
}

static void copy_trimmed_name(const uint8_t *data, size_t len, char *dst, size_t dst_len)
{
	size_t copy_len = MIN(len, dst_len - 1U);

	memcpy(dst, data, copy_len);
	dst[copy_len] = '\0';
}

static bool parse_ad(struct bt_data *data, void *user_data)
{
	struct parse_ctx *ctx = user_data;
	uint16_t company_id;
	const uint8_t *payload;
	size_t payload_len;

	switch (data->type) {
	case BT_DATA_NAME_SHORTENED:
	case BT_DATA_NAME_COMPLETE:
		copy_trimmed_name(data->data, data->data_len, ctx->name, sizeof(ctx->name));
		ctx->saw_name = true;
		if (strncmp(ctx->name, "ANCHOR-", 7) == 0) {
			ctx->kind = PEER_KIND_ANCHOR;
		} else if (strncmp(ctx->name, "BS", 2) == 0) {
			ctx->kind = PEER_KIND_TAG;
		}
		return true;

	case BT_DATA_MANUFACTURER_DATA:
		if (data->data_len < 2U) {
			return true;
		}
		company_id = sys_get_le16(data->data);
		if (company_id != 0xFFFFU) {
			return true;
		}
		payload = data->data + 2U;
		payload_len = data->data_len - 2U;
		if (payload_len >= 24U &&
		    payload[0] == 'B' && payload[1] == 'S' &&
		    payload[2] == 'A' && payload[3] == 0x01U) {
			ctx->kind = PEER_KIND_ANCHOR;
			ctx->saw_anchor = true;
			hex_encode(&payload[4], 16U, ctx->uuid, sizeof(ctx->uuid));
			ctx->anchor_id_cfg = payload[20];
			ctx->role_code = payload[21];
			return true;
		}
		if (payload_len >= 4U && payload[0] == 'B') {
			ctx->kind = PEER_KIND_TAG;
			ctx->saw_tag = true;
			ctx->tag_id = payload[1];
			ctx->identity_code = (uint16_t)payload[2] | ((uint16_t)payload[3] << 8);
			return true;
		}
		return true;

	default:
		return true;
	}
}

static struct peer_state *find_peer(const char *address)
{
	for (size_t i = 0U; i < MAX_TRACKED; ++i) {
		if (g_peers[i].used && strcmp(g_peers[i].address, address) == 0) {
			return &g_peers[i];
		}
	}

	for (size_t i = 0U; i < MAX_TRACKED; ++i) {
		if (!g_peers[i].used) {
			g_peers[i].used = true;
			(void)snprintk(g_peers[i].address, sizeof(g_peers[i].address), "%s", address);
			return &g_peers[i];
		}
	}

	return NULL;
}

static void emit_peer_json(const struct peer_state *peer)
{
	char out[320];
	const int64_t now_ms = k_uptime_get();
	const uint32_t age_ms = (now_ms > peer->last_seen_ms) ?
		(uint32_t)(now_ms - peer->last_seen_ms) : 0U;
	const bool online = age_ms <= STALE_ONLINE_MS;

	if (peer->kind == PEER_KIND_ANCHOR) {
		(void)snprintk(
			out, sizeof(out),
			"{\"type\":\"peer\",\"kind\":\"anchor\",\"display_name\":\"%s\",\"address\":\"%s\",\"uuid\":\"%s\",\"bs_code\":\"%s\",\"anchor_id_cfg\":%u,\"role_code\":%u,\"rssi\":%d,\"online\":%s,\"age_ms\":%u}",
			peer->name[0] ? peer->name : "ANCHOR-?",
			peer->address,
			peer->uuid[0] ? peer->uuid : "-",
			peer->bs_code[0] ? peer->bs_code : "-",
			(unsigned int)peer->anchor_id_cfg,
			(unsigned int)peer->role_code,
			(int)peer->rssi,
			online ? "true" : "false",
			(unsigned int)age_ms);
		printk("%s\n", out);
		return;
	}

	(void)snprintk(
		out, sizeof(out),
		"{\"type\":\"peer\",\"kind\":\"tag\",\"display_name\":\"%s\",\"address\":\"%s\",\"uuid\":\"%s\",\"tag_id\":%u,\"identity_code\":%u,\"rssi\":%d,\"online\":%s,\"age_ms\":%u}",
		peer->name[0] ? peer->name : "BS_AUTO",
		peer->address,
		peer->uuid[0] ? peer->uuid : "-",
		(unsigned int)peer->tag_id,
		(unsigned int)peer->identity_code,
		(int)peer->rssi,
		online ? "true" : "false",
		(unsigned int)age_ms);
	printk("%s\n", out);
}

static void scan_recv(const struct bt_le_scan_recv_info *info,
		      struct net_buf_simple *ad)
{
	struct parse_ctx ctx = {0};
	char addr_str[ADDR_LEN];
	struct peer_state *peer;
	const int64_t now_ms = k_uptime_get();

	if (info == NULL || info->addr == NULL) {
		return;
	}

	if (!(info->adv_type == BT_GAP_ADV_TYPE_ADV_IND ||
	      info->adv_type == BT_GAP_ADV_TYPE_ADV_DIRECT_IND ||
	      info->adv_type == BT_GAP_ADV_TYPE_EXT_ADV ||
	      info->adv_type == BT_GAP_ADV_TYPE_SCAN_RSP)) {
		return;
	}

	bt_data_parse(ad, parse_ad, &ctx);

	if (ctx.kind == PEER_KIND_UNKNOWN) {
		return;
	}

	bt_addr_le_to_str(info->addr, addr_str, sizeof(addr_str));
	peer = find_peer(addr_str);
	if (peer == NULL) {
		return;
	}

	if (now_ms - peer->last_emit_ms < EMIT_MIN_INTERVAL_MS &&
	    peer->rssi == info->rssi &&
	    peer->kind == ctx.kind &&
	    strcmp(peer->name, ctx.name) == 0) {
		peer->last_seen_ms = (uint32_t)now_ms;
		return;
	}

	peer->kind = ctx.kind;
	peer->rssi = info->rssi;
	peer->last_seen_ms = (uint32_t)now_ms;
	peer->last_emit_ms = (uint32_t)now_ms;
	peer->anchor_id_cfg = ctx.anchor_id_cfg;
	peer->role_code = ctx.role_code;
	peer->tag_id = ctx.tag_id;
	peer->identity_code = ctx.identity_code;

	if (ctx.saw_anchor) {
		(void)snprintk(peer->uuid, sizeof(peer->uuid), "%s", ctx.uuid);
		(void)snprintk(peer->bs_code, sizeof(peer->bs_code), "%s",
			       ctx.name[0] ? strchr(ctx.name, '-') ? strrchr(ctx.name, '-') + 1 : "-" : "-");
	}
	if (ctx.saw_tag) {
		(void)snprintk(peer->uuid, sizeof(peer->uuid), "%s", ctx.name);
	}
	if (ctx.saw_name) {
		(void)snprintk(peer->name, sizeof(peer->name), "%s", ctx.name);
	} else if (ctx.kind == PEER_KIND_ANCHOR) {
		char label = (peer->anchor_id_cfg >= 1U && peer->anchor_id_cfg <= 8U) ?
			(char)('A' + (peer->anchor_id_cfg - 1U)) : '?';
		(void)snprintk(peer->name, sizeof(peer->name), "ANCHOR-%c", label);
	} else {
		(void)snprintk(peer->name, sizeof(peer->name), "BS_AUTO");
	}

	if (ctx.kind == PEER_KIND_ANCHOR && peer->bs_code[0] == '\0') {
		const char *bs = strrchr(peer->name, '-');
		if (bs != NULL && *(bs + 1) != '\0') {
			(void)snprintk(peer->bs_code, sizeof(peer->bs_code), "%s", bs + 1);
		}
	}

	emit_peer_json(peer);
}

static struct bt_le_scan_cb scan_cb = {
	.recv = scan_recv,
};

int main(void)
{
	int err;
	static struct bt_le_scan_param scan_param = {
		.type = BT_LE_SCAN_TYPE_ACTIVE,
		.options = BT_LE_SCAN_OPT_NONE,
		.interval = BT_GAP_SCAN_FAST_INTERVAL,
		.window = BT_GAP_SCAN_FAST_WINDOW,
	};

	printk("{\"type\":\"ready\",\"product\":\"BS-BLE-SCANNER\"}\n");
	err = bt_enable(NULL);
	if (err) {
		printk("{\"type\":\"error\",\"message\":\"bt_enable failed\",\"rc\":%d}\n", err);
		return err;
	}

	bt_le_scan_cb_register(&scan_cb);
	err = bt_le_scan_start(&scan_param, NULL);
	if (err) {
		printk("{\"type\":\"error\",\"message\":\"scan start failed\",\"rc\":%d}\n", err);
		return err;
	}

	printk("{\"type\":\"status\",\"state\":\"scanning\",\"mode\":\"bioSpur-only\"}\n");

	while (1) {
		k_sleep(K_SECONDS(1));
	}

	return 0;
}
