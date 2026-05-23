#include "gr_protocol.h"

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/usb/usb_device.h>

static const struct device *const cdc = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

static char line_buf[96];
static size_t line_len;
static bool scan_active;

static void cdc_write(const char *s)
{
	while (*s != '\0') {
		uart_poll_out(cdc, *s++);
	}
}

static void cdc_write_hex8(uint8_t v)
{
	static const char h[] = "0123456789ABCDEF";
	uart_poll_out(cdc, h[v >> 4]);
	uart_poll_out(cdc, h[v & 0x0f]);
}

static void cdc_write_u16_hex(uint16_t v)
{
	cdc_write_hex8((uint8_t)(v >> 8));
	cdc_write_hex8((uint8_t)v);
}

struct adv_parse_ctx {
	char name[32];
	bool name_seen;
	bool gr_mfg_seen;
	uint16_t gr_id;
};

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

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	struct adv_parse_ctx ctx = { 0 };

	if (type != BT_GAP_ADV_TYPE_ADV_IND &&
	    type != BT_GAP_ADV_TYPE_ADV_DIRECT_IND &&
	    type != BT_GAP_ADV_TYPE_ADV_SCAN_IND) {
		return;
	}

	bt_data_parse(ad, parse_ad, &ctx);

	if (!ctx.name_seen || strncmp(ctx.name, GR_NAME_PREFIX, strlen(GR_NAME_PREFIX)) != 0) {
		return;
	}

	if (strncmp(ctx.name, "GR-Master", strlen("GR-Master")) == 0) {
		return;
	}

	char addr_s[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(addr, addr_s, sizeof(addr_s));
	cdc_write("[GR-Master] found ");
	cdc_write(ctx.name);
	cdc_write(" addr=");
	cdc_write(addr_s);
	cdc_write(" rssi=");
	if (rssi < 0) {
		uart_poll_out(cdc, '-');
		rssi = -rssi;
	}
	if (rssi >= 100) {
		uart_poll_out(cdc, '0' + (rssi / 100));
	}
	if (rssi >= 10) {
		uart_poll_out(cdc, '0' + ((rssi / 10) % 10));
	}
	uart_poll_out(cdc, '0' + (rssi % 10));
	if (ctx.gr_mfg_seen) {
		cdc_write(" id=0x");
		cdc_write_u16_hex(ctx.gr_id);
	}
	cdc_write("\r\n");
}

static void scan_start(void)
{
	if (scan_active) {
		cdc_write("[GR-Master] scan already active\r\n");
		return;
	}

	struct bt_le_scan_param scan_param = {
		.type = BT_LE_SCAN_TYPE_PASSIVE,
		.options = BT_LE_SCAN_OPT_NONE,
		.interval = BT_GAP_SCAN_FAST_INTERVAL,
		.window = BT_GAP_SCAN_FAST_WINDOW,
	};

	int err = bt_le_scan_start(&scan_param, device_found);
	if (err) {
		cdc_write("[GR-Master] scan start failed\r\n");
		return;
	}

	scan_active = true;
	cdc_write("[GR-Master] scan started filter=GR*\r\n");
}

static void scan_stop(void)
{
	if (!scan_active) {
		cdc_write("[GR-Master] scan already stopped\r\n");
		return;
	}

	(void)bt_le_scan_stop();
	scan_active = false;
	cdc_write("[GR-Master] scan stopped\r\n");
}

static void handle_line(const char *line)
{
	if (strcmp(line, "status") == 0) {
		cdc_write("[GR-Master] status ok usb=cdc ble=ready role=central\r\n");
	} else if (strcmp(line, "scan") == 0) {
		scan_start();
	} else if (strcmp(line, "stop") == 0) {
		scan_stop();
	} else if (strcmp(line, "help") == 0) {
		cdc_write("commands: status | scan | stop | help\r\n");
	} else if (line[0] != '\0') {
		cdc_write("[GR-Master] unknown command, try help\r\n");
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
			cdc_write("[GR-Master] command too long\r\n");
		}
	}
}

int main(void)
{
	int err = usb_enable(NULL);
	if (err) {
		return err;
	}

	if (!device_is_ready(cdc)) {
		return -ENODEV;
	}

	cdc_write("GR-Master native USB CDC ready\r\n");

	err = bt_enable(NULL);
	if (err) {
		cdc_write("[GR-Master] bt_enable failed\r\n");
		return err;
	}

	cdc_write("[GR-Master] BLE ready, type 'scan'\r\n");

	while (1) {
		poll_cdc();
		k_sleep(K_MSEC(5));
	}
}
