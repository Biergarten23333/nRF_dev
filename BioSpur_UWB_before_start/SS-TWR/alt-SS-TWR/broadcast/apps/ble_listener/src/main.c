#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <dk_buttons_and_leds.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#if defined(CONFIG_USB_DEVICE_STACK)
#include <zephyr/usb/usb_device.h>
#endif

#define FW_NAME "biospur-ble-listener"
#define FW_VERSION "20260625.2"

#define SEEN_MAX 32U
#define NAME_MAX_LEN 31U
#define ID_MAX_LEN 39U
#define ROLE_MAX_LEN 11U
#define UUID_HEX_LEN 32U
#define RECENT_MS 5000LL
#define STALE_MS 15000LL
#define PRINT_MIN_INTERVAL_MS 1000LL
#define RSSI_PRINT_DELTA_DB 8
#define BSTAT_INTERVAL_MS 2000LL

#define DONGLE_LED_STATUS DK_LED1_MSK
#define DONGLE_LED_RED DK_LED2_MSK
#define DONGLE_LED_GREEN DK_LED3_MSK
#define DONGLE_LED_BLUE DK_LED4_MSK

enum biospur_kind {
	KIND_UNKNOWN = 0,
	KIND_TAG,
	KIND_ANCHOR,
	KIND_DFU,
};

struct adv_parse_result {
	enum biospur_kind kind;
	bool kind_from_name;
	bool has_name;
	char name[NAME_MAX_LEN + 1U];
	bool has_id;
	char id[ID_MAX_LEN + 1U];
	bool has_role;
	char role[ROLE_MAX_LEN + 1U];
	bool has_uuid;
	char uuid[UUID_HEX_LEN + 1U];
	bool has_dfu;
	uint8_t tag_id;
	uint8_t anchor_id_cfg;
	uint8_t anchor_role;
};

struct seen_peer {
	bool used;
	bt_addr_le_t addr;
	char addr_str[BT_ADDR_LE_STR_LEN];
	enum biospur_kind kind;
	char name[NAME_MAX_LEN + 1U];
	char id[ID_MAX_LEN + 1U];
	char role[ROLE_MAX_LEN + 1U];
	char uuid[UUID_HEX_LEN + 1U];
	bool has_dfu;
	int8_t rssi;
	int64_t first_seen_ms;
	int64_t last_seen_ms;
	int64_t last_print_ms;
};

static struct seen_peer seen[SEEN_MAX];
static uint32_t adv_count;
static uint32_t printed_count;
static bool scan_running;
static bool fatal_error;
static struct k_work_delayable led_work;
static struct k_work_delayable stat_work;

static const struct bt_le_scan_param scan_param =
	BT_LE_SCAN_PARAM_INIT(BT_LE_SCAN_TYPE_ACTIVE,
			      BT_LE_SCAN_OPT_NONE,
			      BT_GAP_SCAN_FAST_INTERVAL,
			      BT_GAP_SCAN_FAST_WINDOW);

static const char *kind_str(enum biospur_kind kind)
{
	switch (kind) {
	case KIND_TAG:
		return "TAG";
	case KIND_ANCHOR:
		return "ANCHOR";
	case KIND_DFU:
		return "DFU";
	default:
		return "UNK";
	}
}

static const char *anchor_role_str(uint8_t role)
{
	switch (role) {
	case 1:
		return "master";
	case 2:
		return "matrix";
	case 3:
		return "responder";
	default:
		return "unset";
	}
}

static bool is_hex_digit(char c)
{
	return (c >= '0' && c <= '9') ||
	       (c >= 'a' && c <= 'f') ||
	       (c >= 'A' && c <= 'F');
}

static int iabs32(int value)
{
	return value < 0 ? -value : value;
}

static bool bs_code_from_name(const char *name, char out[7])
{
	if (name == NULL) {
		return false;
	}

	for (size_t i = 0U; name[i] != '\0'; ++i) {
		if (name[i] != 'B' || name[i + 1U] != 'S') {
			continue;
		}
		if (!is_hex_digit(name[i + 2U]) ||
		    !is_hex_digit(name[i + 3U]) ||
		    !is_hex_digit(name[i + 4U]) ||
		    !is_hex_digit(name[i + 5U])) {
			continue;
		}
		out[0] = 'B';
		out[1] = 'S';
		for (size_t j = 0U; j < 4U; ++j) {
			char c = name[i + 2U + j];
			if (c >= 'a' && c <= 'f') {
				c = (char)(c - 'a' + 'A');
			}
			out[2U + j] = c;
		}
		out[6] = '\0';
		return true;
	}

	return false;
}

static bool anchor_id_from_name(const char *name, char out[2])
{
	if (name == NULL || strncmp(name, "ANCHOR-", 7) != 0) {
		return false;
	}

	char label = name[7];

	if ((label >= 'A' && label <= 'H') || label == 'U') {
		out[0] = label;
		out[1] = '\0';
		return true;
	}

	return false;
}

static void bytes_to_hex(const uint8_t *bytes, size_t len, char *out, size_t out_len)
{
	static const char hex[] = "0123456789ABCDEF";

	if (out_len == 0U) {
		return;
	}
	if (out_len < (len * 2U) + 1U) {
		out[0] = '\0';
		return;
	}

	for (size_t i = 0U; i < len; ++i) {
		out[i * 2U] = hex[bytes[i] >> 4];
		out[(i * 2U) + 1U] = hex[bytes[i] & 0x0FU];
	}
	out[len * 2U] = '\0';
}

static bool has_dfu_smp_uuid(const uint8_t *data, uint8_t len)
{
	static const uint8_t dfu_smp_uuid_le[16] = {
		0x84, 0xaa, 0x60, 0x74, 0x52, 0x8a, 0x8b, 0x86,
		0xd3, 0x4c, 0xb7, 0x1d, 0x1d, 0xdc, 0x53, 0x8d,
	};

	for (uint8_t offset = 0U; offset + sizeof(dfu_smp_uuid_le) <= len;
	     offset = (uint8_t)(offset + sizeof(dfu_smp_uuid_le))) {
		if (memcmp(&data[offset], dfu_smp_uuid_le, sizeof(dfu_smp_uuid_le)) == 0) {
			return true;
		}
	}

	return false;
}

static bool parse_ad_cb(struct bt_data *data, void *user_data)
{
	struct adv_parse_result *res = user_data;

	switch (data->type) {
	case BT_DATA_NAME_COMPLETE:
	case BT_DATA_NAME_SHORTENED: {
		size_t copy_len = MIN((size_t)data->data_len, NAME_MAX_LEN);

		memcpy(res->name, data->data, copy_len);
		res->name[copy_len] = '\0';
		res->has_name = true;
		if (res->kind == KIND_UNKNOWN) {
			char anchor_id[2];
			char bs_code[7];

			if (anchor_id_from_name(res->name, anchor_id)) {
				res->kind = KIND_ANCHOR;
				res->kind_from_name = true;
				snprintk(res->id, sizeof(res->id), "%s", anchor_id);
				res->has_id = true;
				snprintk(res->role, sizeof(res->role), "-");
				res->has_role = true;
			} else if (bs_code_from_name(res->name, bs_code)) {
				res->kind = KIND_TAG;
				res->kind_from_name = true;
				snprintk(res->id, sizeof(res->id), "%s", bs_code);
				res->has_id = true;
				snprintk(res->role, sizeof(res->role), "-");
				res->has_role = true;
			}
		}
		break;
	}
	case BT_DATA_UUID128_ALL:
	case BT_DATA_UUID128_SOME:
		if (has_dfu_smp_uuid(data->data, data->data_len)) {
			res->has_dfu = true;
			if (res->kind == KIND_UNKNOWN) {
				res->kind = KIND_DFU;
			}
		}
		break;
	case BT_DATA_MANUFACTURER_DATA:
		if (data->data_len >= 24U &&
		    data->data[0] == 0xff && data->data[1] == 0xff &&
		    data->data[2] == 'B' && data->data[3] == 'S' &&
		    data->data[4] == 'A' && data->data[5] == 0x01U) {
			uint8_t anchor_id = data->data[22];
			uint8_t role = data->data[23];
			char label = 'U';

			if (anchor_id >= 1U && anchor_id <= 8U) {
				label = (char)('A' + anchor_id - 1U);
			}
			res->kind = KIND_ANCHOR;
			res->anchor_id_cfg = anchor_id;
			res->anchor_role = role;
			snprintk(res->id, sizeof(res->id), "%c", label);
			snprintk(res->role, sizeof(res->role), "%s", anchor_role_str(role));
			bytes_to_hex(&data->data[6], 16U, res->uuid, sizeof(res->uuid));
			res->has_id = true;
			res->has_role = true;
			res->has_uuid = true;
		} else if (data->data_len >= 6U &&
			   data->data[0] == 0xff && data->data[1] == 0xff &&
			   data->data[2] == 'B' &&
			   (data->data_len < 5U || data->data[3] != 'S')) {
			uint16_t bs_code = sys_get_le16(&data->data[4]);

			res->kind = KIND_TAG;
			res->tag_id = data->data[3];
			snprintk(res->id, sizeof(res->id), "BS%04X", (unsigned int)bs_code);
			snprintk(res->role, sizeof(res->role), "tag%u",
				 (unsigned int)res->tag_id);
			res->has_id = true;
			res->has_role = true;
		}
		break;
	default:
		break;
	}

	return true;
}

static struct seen_peer *seen_find_or_alloc(const bt_addr_le_t *addr, int64_t now_ms)
{
	struct seen_peer *oldest = &seen[0];

	for (size_t i = 0U; i < ARRAY_SIZE(seen); ++i) {
		if (seen[i].used && bt_addr_le_cmp(&seen[i].addr, addr) == 0) {
			return &seen[i];
		}
	}

	for (size_t i = 0U; i < ARRAY_SIZE(seen); ++i) {
		if (!seen[i].used) {
			oldest = &seen[i];
			goto init_slot;
		}
		if (seen[i].last_seen_ms < oldest->last_seen_ms) {
			oldest = &seen[i];
		}
	}

init_slot:
	memset(oldest, 0, sizeof(*oldest));
	oldest->used = true;
	bt_addr_le_copy(&oldest->addr, addr);
	bt_addr_le_to_str(addr, oldest->addr_str, sizeof(oldest->addr_str));
	oldest->kind = KIND_UNKNOWN;
	oldest->first_seen_ms = now_ms;
	oldest->last_print_ms = -PRINT_MIN_INTERVAL_MS;
	snprintk(oldest->name, sizeof(oldest->name), "-");
	snprintk(oldest->id, sizeof(oldest->id), "-");
	snprintk(oldest->role, sizeof(oldest->role), "-");
	snprintk(oldest->uuid, sizeof(oldest->uuid), "-");
	return oldest;
}

static bool update_text_field(char *dst, size_t dst_len, const char *src)
{
	if (src == NULL || src[0] == '\0') {
		return false;
	}
	if (strncmp(dst, src, dst_len) == 0) {
		return false;
	}
	snprintk(dst, dst_len, "%s", src);
	return true;
}

static bool update_seen_peer(struct seen_peer *peer, const struct adv_parse_result *res,
			     int8_t rssi, int64_t now_ms)
{
	bool changed = false;

	peer->last_seen_ms = now_ms;
	if (peer->rssi == 0 ||
	    iabs32((int)rssi - (int)peer->rssi) >= RSSI_PRINT_DELTA_DB) {
		changed = true;
	}
	peer->rssi = rssi;

	if (res->kind != KIND_UNKNOWN && peer->kind != res->kind &&
	    (!res->kind_from_name || peer->kind == KIND_UNKNOWN)) {
		peer->kind = res->kind;
		changed = true;
	}
	if (res->has_name) {
		changed |= update_text_field(peer->name, sizeof(peer->name), res->name);
	}
	if (res->has_id) {
		changed |= update_text_field(peer->id, sizeof(peer->id), res->id);
	}
	if (res->has_role) {
		changed |= update_text_field(peer->role, sizeof(peer->role), res->role);
	}
	if (res->has_uuid) {
		changed |= update_text_field(peer->uuid, sizeof(peer->uuid), res->uuid);
	}
	if (res->has_dfu && !peer->has_dfu) {
		peer->has_dfu = true;
		changed = true;
	}

	return changed;
}

static bool should_print_peer(const struct seen_peer *peer, bool changed, int64_t now_ms)
{
	if (peer->kind == KIND_UNKNOWN) {
		return false;
	}
	if (peer->last_print_ms < 0) {
		return true;
	}
	if (changed && now_ms - peer->last_print_ms >= 250LL) {
		return true;
	}
	if (now_ms - peer->last_print_ms >= PRINT_MIN_INTERVAL_MS) {
		return true;
	}

	return false;
}

static void print_peer_line(struct seen_peer *peer, int64_t now_ms)
{
	printk("BADV;1;%lld;%s;%d;%s;%s;%s;%s;%s;%u\n",
	       (long long)now_ms,
	       peer->addr_str,
	       (int)peer->rssi,
	       kind_str(peer->kind),
	       peer->name[0] != '\0' ? peer->name : "-",
	       peer->id[0] != '\0' ? peer->id : "-",
	       peer->role[0] != '\0' ? peer->role : "-",
	       peer->uuid[0] != '\0' ? peer->uuid : "-",
	       peer->has_dfu ? 1U : 0U);
	peer->last_print_ms = now_ms;
	printed_count++;
}

static void scan_recv(const struct bt_le_scan_recv_info *info, struct net_buf_simple *buf)
{
	struct adv_parse_result parsed = { 0 };
	struct net_buf_simple copy = *buf;
	struct seen_peer *peer;
	int64_t now_ms = k_uptime_get();
	bool changed;

	adv_count++;
	bt_data_parse(&copy, parse_ad_cb, &parsed);
	if (parsed.kind == KIND_UNKNOWN && !parsed.has_name && !parsed.has_dfu) {
		return;
	}

	peer = seen_find_or_alloc(info->addr, now_ms);
	changed = update_seen_peer(peer, &parsed, info->rssi, now_ms);
	if (should_print_peer(peer, changed, now_ms)) {
		print_peer_line(peer, now_ms);
	}
}

static struct bt_le_scan_cb scan_callbacks = {
	.recv = scan_recv,
};

struct peer_counts {
	uint8_t tags;
	uint8_t anchors;
	uint8_t dfu;
	uint8_t unknown;
	uint8_t stale;
	uint8_t total;
};

static void count_recent_peers(struct peer_counts *counts, int64_t now_ms)
{
	memset(counts, 0, sizeof(*counts));

	for (size_t i = 0U; i < ARRAY_SIZE(seen); ++i) {
		if (!seen[i].used) {
			continue;
		}
		if (now_ms - seen[i].last_seen_ms > STALE_MS) {
			counts->stale++;
			continue;
		}
		if (now_ms - seen[i].last_seen_ms > RECENT_MS) {
			continue;
		}
		counts->total++;
		if (seen[i].has_dfu) {
			counts->dfu++;
		}
		switch (seen[i].kind) {
		case KIND_TAG:
			counts->tags++;
			break;
		case KIND_ANCHOR:
			counts->anchors++;
			break;
		case KIND_DFU:
		case KIND_UNKNOWN:
		default:
			counts->unknown++;
			break;
		}
	}
}

static void stat_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	struct peer_counts counts;
	int64_t now_ms = k_uptime_get();

	count_recent_peers(&counts, now_ms);
	printk("BSTAT;1;%lld;tags=%u;anchors=%u;dfu=%u;unknown=%u;total=%u;stale=%u;adv=%lu;printed=%lu;scan=%u\n",
	       (long long)now_ms,
	       (unsigned int)counts.tags,
	       (unsigned int)counts.anchors,
	       (unsigned int)counts.dfu,
	       (unsigned int)counts.unknown,
	       (unsigned int)counts.total,
	       (unsigned int)counts.stale,
	       (unsigned long)adv_count,
	       (unsigned long)printed_count,
	       scan_running ? 1U : 0U);
	(void)k_work_reschedule(&stat_work, K_MSEC(BSTAT_INTERVAL_MS));
}

static uint32_t led_mask_for_state(void)
{
	struct peer_counts counts;
	int64_t now_ms = k_uptime_get();

	if (fatal_error) {
		return DONGLE_LED_RED;
	}
	if (!scan_running) {
		return DONGLE_LED_RED | DONGLE_LED_BLUE;
	}

	count_recent_peers(&counts, now_ms);
	if (counts.dfu > 0U) {
		return DONGLE_LED_RED | DONGLE_LED_GREEN | DONGLE_LED_STATUS;
	}
	if ((counts.tags + counts.anchors) > 0U) {
		return DONGLE_LED_GREEN | DONGLE_LED_STATUS;
	}

	return DONGLE_LED_BLUE;
}

static void led_work_handler(struct k_work *work)
{
	static bool blink_on;
	struct peer_counts counts;
	int64_t now_ms = k_uptime_get();
	uint32_t mask;
	uint32_t delay_ms = 750U;

	ARG_UNUSED(work);

	blink_on = !blink_on;
	mask = led_mask_for_state();
	if (fatal_error) {
		delay_ms = 150U;
	} else if (scan_running) {
		count_recent_peers(&counts, now_ms);
		delay_ms = ((counts.tags + counts.anchors + counts.dfu) > 0U) ? 250U : 750U;
	}

	(void)dk_set_leds(blink_on ? mask : 0U);
	(void)k_work_reschedule(&led_work, K_MSEC(delay_ms));
}

int main(void)
{
	int err;

#if defined(CONFIG_USB_DEVICE_STACK)
	err = usb_enable(NULL);
	if (err != 0 && err != -EALREADY) {
		printk("USB CDC enable failed: %d\n", err);
	}
#endif

	printk("BL;1;BOOT;fw=%s;version=%s\n", FW_NAME, FW_VERSION);
	printk("BL;1;LED;map=status=led0;rgb=red:led1,green:led2,blue:led3\n");

	err = dk_leds_init();
	if (err) {
		printk("BL;1;WARN;led_init=%d\n", err);
	} else {
		k_work_init_delayable(&led_work, led_work_handler);
		(void)k_work_schedule(&led_work, K_NO_WAIT);
	}

	err = bt_enable(NULL);
	if (err) {
		fatal_error = true;
		printk("BL;1;ERROR;bt_enable=%d\n", err);
		return err;
	}

	bt_le_scan_cb_register(&scan_callbacks);

	err = bt_le_scan_start(&scan_param, NULL);
	if (err) {
		fatal_error = true;
		printk("BL;1;ERROR;scan_start=%d\n", err);
		return err;
	}

	scan_running = true;
	printk("BL;1;READY;mode=active_scan_only;connect=0;commands=0;line=BADV/BSTAT\n");

	k_work_init_delayable(&stat_work, stat_work_handler);
	(void)k_work_schedule(&stat_work, K_MSEC(500));

	for (;;) {
		k_sleep(K_SECONDS(60));
	}

	return 0;
}
