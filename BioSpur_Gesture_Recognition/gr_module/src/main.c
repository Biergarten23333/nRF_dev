#include "ads1298.h"
#include "app_config.h"
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
static struct k_mutex conn_mutex;
static uint16_t status_seq;
static uint16_t emg_seq;
static int64_t host_time_offset_ms;

struct emg_sample {
	uint8_t status[3];
	int32_t ch_code[8];
};

static struct emg_sample emg_samples[EMG_SAMPLES_PER_FRAME];
static uint8_t emg_sample_count;
static uint32_t emg_sent_count;
static uint32_t emg_drop_count;

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

static struct bt_conn *conn_ref_current(void)
{
	struct bt_conn *conn = NULL;

	k_mutex_lock(&conn_mutex, K_FOREVER);
	if (current_conn != NULL) {
		conn = bt_conn_ref(current_conn);
	}
	k_mutex_unlock(&conn_mutex);

	return conn;
}

static void send_status_once(void)
{
	struct bt_conn *conn = conn_ref_current();

	if (conn == NULL) {
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

	int err = bt_nus_send(conn, frame, sizeof(frame));
	if (err) {
		printk("GR status send failed: %d\n", err);
	}
	bt_conn_unref(conn);
}

static uint16_t next_emg_seq(void)
{
	uint16_t seq = emg_seq;

	emg_seq = (uint16_t)(emg_seq + 1U);
	return seq;
}

static void emg_flush(uint8_t sample_count)
{
	struct bt_conn *conn;
	uint8_t frame[sizeof(struct gr_packet_header) + 4 +
		      (EMG_SAMPLES_PER_FRAME * (3 + 8 * 3))];
	struct gr_packet_header *hdr = (struct gr_packet_header *)frame;
	uint8_t *p = &frame[sizeof(*hdr)];
	size_t len;
	int err;

	if (sample_count == 0U) {
		return;
	}

	conn = conn_ref_current();
	if (conn == NULL) {
		emg_drop_count++;
		return;
	}

	hdr->magic = GR_PACKET_MAGIC;
	hdr->type = GR_TYPE_ADS1298;
	sys_put_le16(next_emg_seq(), (uint8_t *)&hdr->seq);
	sys_put_le16(device_id_get16(), (uint8_t *)&hdr->device_id);
	sys_put_le32(gr_now_host_ms(), (uint8_t *)&hdr->timestamp_ms);

	*p++ = sample_count;
	*p++ = 0xff;
	sys_put_le16(EMG_SAMPLE_RATE_SPS, p);
	p += 2;

	for (uint8_t s = 0; s < sample_count; s++) {
		memcpy(p, emg_samples[s].status, sizeof(emg_samples[s].status));
		p += sizeof(emg_samples[s].status);

		for (int ch = 0; ch < 8; ch++) {
			int32_t v = emg_samples[s].ch_code[ch];

			*p++ = (uint8_t)((v >> 16) & 0xff);
			*p++ = (uint8_t)((v >> 8) & 0xff);
			*p++ = (uint8_t)(v & 0xff);
		}
	}

	len = (size_t)(p - frame);
	err = bt_nus_send(conn, frame, len);
	if (err) {
		emg_drop_count++;
		if ((emg_drop_count % 100U) == 1U) {
			printk("GR EMG send failed: %d drops=%u\n",
			       err, (unsigned int)emg_drop_count);
		}
	} else {
		emg_sent_count++;
	}

	bt_conn_unref(conn);
}

static void ads_frame_received(const int32_t ch_code[8], const uint8_t status[3])
{
	struct emg_sample *sample;

	if (emg_sample_count >= EMG_SAMPLES_PER_FRAME) {
		emg_sample_count = 0;
	}

	sample = &emg_samples[emg_sample_count++];
	memcpy(sample->status, status, sizeof(sample->status));
	for (int i = 0; i < 8; i++) {
		sample->ch_code[i] = ch_code[i];
	}

	if (emg_sample_count >= EMG_SAMPLES_PER_FRAME) {
		emg_flush(emg_sample_count);
		emg_sample_count = 0;
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

	if ((len >= 11U && memcmp(data, "OTA_PREPARE", 11) == 0) ||
	    (len >= 13U && data[0] == GR_PACKET_MAGIC && data[1] == GR_TYPE_COMMAND &&
	     memcmp(&data[2], "OTA_PREPARE", 11) == 0)) {
		printk("GR OTA_PREPARE received\n");
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

	k_mutex_lock(&conn_mutex, K_FOREVER);
	if (current_conn != NULL) {
		bt_conn_unref(current_conn);
	}
	current_conn = bt_conn_ref(conn);
	k_mutex_unlock(&conn_mutex);
	printk("GR connected\n");
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	ARG_UNUSED(conn);

	printk("GR disconnected: 0x%02x\n", reason);

	k_mutex_lock(&conn_mutex, K_FOREVER);
	if (current_conn != NULL) {
		bt_conn_unref(current_conn);
		current_conn = NULL;
	}
	k_mutex_unlock(&conn_mutex);
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
	k_mutex_init(&conn_mutex);
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

	err = ads1298_init();
	if (err) {
		printk("GR ADS1298 init failed: %d\n", err);
	} else {
		ads1298_set_frame_callback(ads_frame_received);
		ads1298_start();
	}

	while (1) {
		send_status_once();
		k_sleep(K_SECONDS(1));
	}
}
