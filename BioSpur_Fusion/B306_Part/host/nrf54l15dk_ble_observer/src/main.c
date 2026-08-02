#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/kernel.h>
#include <zephyr/net_buf.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#define FW_MARKER "nrf54l15dk-ble-observer-v3"
#define PEER_MAX 32U
#define NAME_MAX_LEN 31U
#define BS_PRINT_INTERVAL_MS 1000LL
#define SCAN_RESTART_TICKS 29U

struct parsed_adv {
	bool has_name;
	bool has_bs;
	char name[NAME_MAX_LEN + 1U];
	char bs[7];
	uint8_t tag_id;
};

struct peer {
	bool used;
	bt_addr_le_t addr;
	char addr_str[BT_ADDR_LE_STR_LEN];
	char name[NAME_MAX_LEN + 1U];
	char bs[7];
	uint8_t tag_id;
	int8_t rssi;
	int64_t last_seen_ms;
	int64_t last_print_ms;
};

static struct peer peers[PEER_MAX];
static atomic_t adv_count;
static atomic_t unique_count;
static atomic_t bs_packet_count;

static bool is_hex_digit(char c)
{
	return (c >= '0' && c <= '9') ||
	       (c >= 'a' && c <= 'f') ||
	       (c >= 'A' && c <= 'F');
}

static bool bs_code_from_name(const char *name, char out[7])
{
	for (size_t i = 0U; name != NULL && name[i] != '\0'; ++i) {
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

static bool parse_ad_cb(struct bt_data *data, void *user_data)
{
	struct parsed_adv *parsed = user_data;

	switch (data->type) {
	case BT_DATA_NAME_COMPLETE:
	case BT_DATA_NAME_SHORTENED: {
		size_t copy_len = MIN((size_t)data->data_len, NAME_MAX_LEN);

		memcpy(parsed->name, data->data, copy_len);
		parsed->name[copy_len] = '\0';
		parsed->has_name = true;
		parsed->has_bs = bs_code_from_name(parsed->name, parsed->bs);
		break;
	}
	case BT_DATA_MANUFACTURER_DATA:
		if (data->data_len >= 6U &&
		    data->data[0] == 0xff && data->data[1] == 0xff &&
		    data->data[2] == 'B' &&
		    (data->data_len < 5U || data->data[3] != 'S')) {
			uint16_t bs_code = sys_get_le16(&data->data[4]);

			parsed->tag_id = data->data[3];
			snprintk(parsed->bs, sizeof(parsed->bs), "BS%04X",
				 (unsigned int)bs_code);
			parsed->has_bs = true;
		}
		break;
	default:
		break;
	}

	return true;
}

static struct peer *find_or_alloc_peer(const bt_addr_le_t *addr, int64_t now_ms)
{
	struct peer *oldest = &peers[0];

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].used && bt_addr_le_cmp(&peers[i].addr, addr) == 0) {
			return &peers[i];
		}
	}

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (!peers[i].used) {
			oldest = &peers[i];
			goto init_peer;
		}
		if (peers[i].last_seen_ms < oldest->last_seen_ms) {
			oldest = &peers[i];
		}
	}

init_peer:
	memset(oldest, 0, sizeof(*oldest));
	oldest->used = true;
	oldest->last_seen_ms = now_ms;
	oldest->last_print_ms = -BS_PRINT_INTERVAL_MS;
	bt_addr_le_copy(&oldest->addr, addr);
	bt_addr_le_to_str(addr, oldest->addr_str, sizeof(oldest->addr_str));
	atomic_inc(&unique_count);
	printk("OBS_NEW t_ms=%lld addr=%s\n",
	       (long long)now_ms, oldest->addr_str);
	return oldest;
}

static void scan_recv(const struct bt_le_scan_recv_info *info,
		      struct net_buf_simple *buf)
{
	struct parsed_adv parsed = { 0 };
	struct net_buf_simple copy = *buf;
	struct peer *peer;
	int64_t now_ms = k_uptime_get();

	atomic_inc(&adv_count);
	bt_data_parse(&copy, parse_ad_cb, &parsed);
	peer = find_or_alloc_peer(info->addr, now_ms);
	peer->rssi = info->rssi;
	peer->last_seen_ms = now_ms;

	if (parsed.has_name) {
		snprintk(peer->name, sizeof(peer->name), "%s", parsed.name);
	}
	if (parsed.has_bs) {
		snprintk(peer->bs, sizeof(peer->bs), "%s", parsed.bs);
		peer->tag_id = parsed.tag_id;
		atomic_inc(&bs_packet_count);
	}

	if (peer->bs[0] != '\0' &&
	    now_ms - peer->last_print_ms >= BS_PRINT_INTERVAL_MS) {
		printk("OBS_BS t_ms=%lld addr=%s rssi=%d name=%s id=%s tag=%u\n",
		       (long long)now_ms,
		       peer->addr_str,
		       (int)peer->rssi,
		       peer->name[0] != '\0' ? peer->name : "-",
		       peer->bs,
		       (unsigned int)peer->tag_id);
		peer->last_print_ms = now_ms;
	}
}

static struct bt_le_scan_cb scan_callbacks = {
	.recv = scan_recv,
};

static const struct bt_le_scan_param scan_param =
	BT_LE_SCAN_PARAM_INIT(BT_LE_SCAN_TYPE_ACTIVE,
			      BT_LE_SCAN_OPT_NONE,
			      BT_GAP_SCAN_FAST_INTERVAL,
			      BT_GAP_SCAN_FAST_WINDOW);

int main(void)
{
	uint32_t scan_ticks = 0U;
	int err;

	printk("OBSERVER_BOOT fw=%s board=nrf54l15dk output=RTT\n", FW_MARKER);

	err = bt_enable(NULL);
	if (err != 0) {
		printk("OBSERVER_FATAL stage=bt_enable err=%d\n", err);
		return err;
	}

	bt_le_scan_cb_register(&scan_callbacks);
	err = bt_le_scan_start(&scan_param, NULL);
	if (err != 0) {
		printk("OBSERVER_FATAL stage=scan_start err=%d\n", err);
		return err;
	}

	printk("OBSERVER_READY mode=active_scan connect=0 restart_s=58\n");
	for (;;) {
		k_sleep(K_SECONDS(2));
		++scan_ticks;
		printk("OBS_STAT up_ms=%lld adv=%ld unique=%ld bs_packets=%ld scan=1\n",
		       (long long)k_uptime_get(),
		       (long)atomic_get(&adv_count),
		       (long)atomic_get(&unique_count),
		       (long)atomic_get(&bs_packet_count));

		if (scan_ticks < SCAN_RESTART_TICKS) {
			continue;
		}

		err = bt_le_scan_stop();
		printk("OBS_SCAN action=stop err=%d\n", err);
		k_sleep(K_SECONDS(2));
		err = bt_le_scan_start(&scan_param, NULL);
		printk("OBS_SCAN action=start err=%d\n", err);
		if (err != 0) {
			return err;
		}
		scan_ticks = 0U;
	}

	return 0;
}
