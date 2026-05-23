#include "device_id.h"
#include "gr_protocol.h"

#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/dfu/mcuboot.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/printk.h>

#include <bluetooth/services/nus.h>

static struct bt_conn *current_conn;
static uint16_t status_seq;
static int64_t host_time_offset_ms;

static void confirm_running_image(void)
{
	int err = boot_write_img_confirmed();

	if (err == 0) {
		printk("GR MCUboot image confirmed\n");
	} else {
		printk("GR MCUboot confirm rc=%d\n", err);
	}
}

static uint32_t gr_now_host_ms(void)
{
	int64_t host_ms = (int64_t)k_uptime_get() + host_time_offset_ms;

	return (uint32_t)(host_ms < 0 ? 0 : host_ms);
}

static void send_status_once(void)
{
	if (current_conn == NULL) {
		return;
	}

	uint8_t frame[sizeof(struct gr_packet_header) + 8];
	struct gr_packet_header *hdr = (struct gr_packet_header *)frame;
	uint8_t *payload = &frame[sizeof(*hdr)];

	hdr->magic = GR_PACKET_MAGIC;
	hdr->type = GR_TYPE_STATUS;
	sys_put_le16(status_seq++, (uint8_t *)&hdr->seq);
	sys_put_le16(device_id_get16(), (uint8_t *)&hdr->device_id);
	sys_put_le32(gr_now_host_ms(), (uint8_t *)&hdr->timestamp_ms);

	payload[0] = 'B';
	payload[1] = '3';
	payload[2] = '0';
	payload[3] = '6';
	sys_put_le32((uint32_t)k_uptime_get(), &payload[4]);

	int err = bt_nus_send(current_conn, frame, sizeof(frame));
	if (err) {
		printk("GR status send failed: %d\n", err);
	}
}

static void nus_received(struct bt_conn *conn, const uint8_t *data, uint16_t len)
{
	ARG_UNUSED(conn);

	if (len == 10U && data[0] == GR_PACKET_MAGIC && data[1] == GR_TYPE_TSYNC) {
		uint64_t host_ms = sys_get_le64(&data[2]);
		host_time_offset_ms = (int64_t)host_ms - (int64_t)k_uptime_get();
		printk("GR TSYNC offset=%lld\n", (long long)host_time_offset_ms);
		return;
	}

	printk("GR command len=%u type=0x%02x\n", len, len > 1 ? data[1] : 0);
}

static struct bt_nus_cb nus_cb = {
	.received = nus_received,
};

static void connected(struct bt_conn *conn, uint8_t err)
{
	if (err) {
		printk("GR connection failed: 0x%02x\n", err);
		return;
	}

	current_conn = bt_conn_ref(conn);
	printk("GR connected\n");
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	ARG_UNUSED(conn);

	printk("GR disconnected: 0x%02x\n", reason);

	if (current_conn != NULL) {
		bt_conn_unref(current_conn);
		current_conn = NULL;
	}
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
};

static int start_advertising(void)
{
	const char *name = device_bt_name_get();
	uint8_t mfg[] = {
		0xff, 0xff,
		GR_ADV_MFG_MAGIC0,
		GR_ADV_MFG_MAGIC1,
		GR_ADV_MFG_VERSION,
		0x00,
		(uint8_t)(device_id_get16() & 0xffU),
		(uint8_t)(device_id_get16() >> 8),
	};

	const struct bt_data ad[] = {
		BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
		BT_DATA(BT_DATA_NAME_COMPLETE, name, strlen(name)),
		BT_DATA(BT_DATA_MANUFACTURER_DATA, mfg, sizeof(mfg)),
	};
	const struct bt_data sd[] = {
		BT_DATA_BYTES(BT_DATA_UUID128_ALL,
			      0x84, 0xaa, 0x60, 0x74, 0x52, 0x8a, 0x8b, 0x86,
			      0xd3, 0x4c, 0xb7, 0x1d, 0x1d, 0xdc, 0x53, 0x8d),
	};

	return bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
}

int main(void)
{
	device_id_init();
	confirm_running_image();

	int err = bt_enable(NULL);
	if (err) {
		printk("GR bt_enable failed: %d\n", err);
		return err;
	}

	err = bt_nus_init(&nus_cb);
	if (err) {
		printk("GR bt_nus_init failed: %d\n", err);
		return err;
	}

	bt_set_name(device_bt_name_get());

	err = start_advertising();
	printk("GR module ready name=%s adv=%d\n", device_bt_name_get(), err);

	while (1) {
		send_status_once();
		k_sleep(K_SECONDS(1));
	}
}
