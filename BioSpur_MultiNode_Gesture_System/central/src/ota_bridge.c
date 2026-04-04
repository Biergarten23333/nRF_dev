#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>

#include <zephyr/logging/log.h>
#include <zephyr/sys/util.h>

#include "app_ble.h"
#include "cdc_async.h"
#include "ota_bridge.h"

LOG_MODULE_REGISTER(central_ota_bridge, LOG_LEVEL_INF);

#define BSGR_OTA_CMD_MAX 96
#define BSGR_OTA_TARGET_MAX 31
#define BSGR_OTA_CHUNK_MAX 128

enum bsgr_ota_bridge_mode {
	BSGR_OTA_BRIDGE_MODE_COMMAND = 0,
	BSGR_OTA_BRIDGE_MODE_CHUNK,
};

struct bsgr_ota_bridge_state {
	enum bsgr_ota_bridge_mode mode;
	char cmd_buf[BSGR_OTA_CMD_MAX];
	size_t cmd_len;
	char target_name[BSGR_OTA_TARGET_MAX + 1];
	uint8_t chunk_buf[BSGR_OTA_CHUNK_MAX];
	size_t chunk_expected;
	size_t chunk_received;
	size_t upload_size;
	size_t upload_received;
	bool upload_active;
	char last_hash[BSGR_IMG_HASH_LEN];
	bool last_hash_valid;
};

static struct bsgr_ota_bridge_state bridge_state;

static void bridge_write_line(const char *fmt, ...)
{
	va_list args;
	char line[160];
	int len;

	va_start(args, fmt);
	len = vsnprintf(line, sizeof(line), fmt, args);
	va_end(args);

	if (len > 0) {
		(void)cdc_async_write_data((const uint8_t *)line,
					   MIN((size_t)len, sizeof(line) - 1U));
	}
}

static void hash_to_hex(const char *hash, char *hex, size_t hex_len)
{
	static const char digits[] = "0123456789abcdef";
	size_t i;

	if (hex_len < (BSGR_IMG_HASH_LEN * 2U + 1U)) {
		return;
	}

	for (i = 0; i < BSGR_IMG_HASH_LEN; ++i) {
		hex[(i * 2U)] = digits[((uint8_t)hash[i]) >> 4];
		hex[(i * 2U) + 1U] = digits[((uint8_t)hash[i]) & 0x0F];
	}

	hex[BSGR_IMG_HASH_LEN * 2U] = '\0';
}

static int bridge_list_images(void)
{
	struct bsgr_mcumgr_image_data images[4];
	struct bsgr_mcumgr_image_state state;
	size_t i;
	int err;
	char hash_hex[BSGR_IMG_HASH_LEN * 2U + 1U];

	err = app_ble_ota_read_images(&state, images, ARRAY_SIZE(images));
	if (err != 0) {
		return err;
	}

	bridge_write_line("OK LIST %d\r\n", state.image_list_length);
	for (i = 0; i < (size_t)state.image_list_length; ++i) {
		hash_to_hex(images[i].hash, hash_hex, sizeof(hash_hex));
		bridge_write_line("IMAGE slot=%u active=%u confirmed=%u pending=%u hash=%s version=%s\r\n",
				  images[i].slot_num,
				  images[i].flags.active ? 1 : 0,
				  images[i].flags.confirmed ? 1 : 0,
				  images[i].flags.pending ? 1 : 0,
				  hash_hex,
				  images[i].version);
	}

	return 0;
}

static int bridge_finish_upload(void)
{
	struct bsgr_mcumgr_image_data images[4];
	struct bsgr_mcumgr_image_state state;
	char hash_hex[BSGR_IMG_HASH_LEN * 2U + 1U];
	size_t i;
	int err;

	err = app_ble_ota_read_images(&state, images, ARRAY_SIZE(images));
	if (err != 0) {
		return err;
	}

	for (i = 0; i < (size_t)state.image_list_length; ++i) {
		if (images[i].slot_num != 0U) {
			memcpy(bridge_state.last_hash, images[i].hash, BSGR_IMG_HASH_LEN);
			bridge_state.last_hash_valid = true;
			break;
		}
	}

	if (!bridge_state.last_hash_valid) {
		return -ENOENT;
	}

	err = app_ble_ota_mark_test(bridge_state.last_hash);
	if (err != 0) {
		return err;
	}

	hash_to_hex(bridge_state.last_hash, hash_hex, sizeof(hash_hex));
	err = app_ble_ota_reset_target();
	if (err != 0) {
		return err;
	}

	bridge_state.upload_active = false;
	bridge_state.upload_size = 0U;
	bridge_state.upload_received = 0U;
	bridge_write_line("OK APPLY %s\r\n", hash_hex);
	return 0;
}

static int bridge_handle_command(const char *cmd)
{
	char target[BSGR_OTA_TARGET_MAX + 1];
	unsigned long size_ul;
	unsigned int chunk_len;
	int err;

	if (strncmp(cmd, "PING", 4) == 0) {
		bridge_write_line("OK PONG\r\n");
		return 0;
	}

	if (sscanf(cmd, "CONNECT %31s", target) == 1) {
		err = app_ble_ota_connect(target, K_SECONDS(15));
		if (err != 0) {
			bridge_write_line("ERR CONNECT %d\r\n", err);
			return err;
		}

		strncpy(bridge_state.target_name, target, sizeof(bridge_state.target_name) - 1U);
		bridge_write_line("OK CONNECT %s\r\n", target);
		return 0;
	}

	if (strcmp(cmd, "LIST") == 0) {
		err = bridge_list_images();
		if (err != 0) {
			bridge_write_line("ERR LIST %d\r\n", err);
		}
		return err;
	}

	if (sscanf(cmd, "BEGIN %31s %lu", target, &size_ul) == 2) {
		err = app_ble_ota_connect(target, K_SECONDS(15));
		if (err != 0) {
			bridge_write_line("ERR CONNECT %d\r\n", err);
			return err;
		}

		err = app_ble_ota_upload_start((size_t)size_ul);
		if (err != 0) {
			bridge_write_line("ERR BEGIN %d\r\n", err);
			return err;
		}

		memset(bridge_state.target_name, 0, sizeof(bridge_state.target_name));
		strncpy(bridge_state.target_name, target, sizeof(bridge_state.target_name) - 1U);
		bridge_state.upload_active = true;
		bridge_state.upload_size = (size_t)size_ul;
		bridge_state.upload_received = 0U;
		bridge_state.last_hash_valid = false;
		bridge_write_line("OK BEGIN %s %zu\r\n", bridge_state.target_name, bridge_state.upload_size);
		return 0;
	}

	if (sscanf(cmd, "CHUNK %u", &chunk_len) == 1) {
		if (!bridge_state.upload_active) {
			bridge_write_line("ERR STATE %d\r\n", -EINVAL);
			return -EINVAL;
		}

		if ((chunk_len == 0U) || (chunk_len > BSGR_OTA_CHUNK_MAX)) {
			bridge_write_line("ERR CHUNK %d\r\n", -EMSGSIZE);
			return -EMSGSIZE;
		}

		bridge_state.mode = BSGR_OTA_BRIDGE_MODE_CHUNK;
		bridge_state.chunk_expected = (size_t)chunk_len;
		bridge_state.chunk_received = 0U;
		return 0;
	}

	if (strcmp(cmd, "END") == 0) {
		err = bridge_finish_upload();
		if (err != 0) {
			bridge_write_line("ERR END %d\r\n", err);
		}
		return err;
	}

	if (strcmp(cmd, "REBOOT") == 0) {
		err = app_ble_ota_reset_target();
		if (err != 0) {
			bridge_write_line("ERR REBOOT %d\r\n", err);
			return err;
		}

		bridge_write_line("OK REBOOT %s\r\n",
				  bridge_state.target_name[0] != '\0' ?
				  bridge_state.target_name : "TARGET");
		return 0;
	}

	if (strcmp(cmd, "DISCONNECT") == 0) {
		app_ble_ota_disconnect();
		memset(&bridge_state, 0, sizeof(bridge_state));
		bridge_write_line("OK DISCONNECT\r\n");
		return 0;
	}

	bridge_write_line("ERR UNKNOWN\r\n");
	return -EINVAL;
}

static void bridge_process_chunk_byte(uint8_t ch)
{
	size_t remote_offset;
	int err;

	if (bridge_state.chunk_received < bridge_state.chunk_expected) {
		bridge_state.chunk_buf[bridge_state.chunk_received++] = ch;
	}

	if (bridge_state.chunk_received != bridge_state.chunk_expected) {
		return;
	}

	err = app_ble_ota_upload_chunk(bridge_state.chunk_buf, bridge_state.chunk_expected,
				       &remote_offset);
	if (err != 0) {
		bridge_write_line("ERR UPLOAD %d\r\n", err);
		bridge_state.upload_active = false;
	} else {
		bridge_state.upload_received += bridge_state.chunk_expected;
		bridge_write_line("OK CHUNK %zu/%zu off=%zu\r\n",
				  bridge_state.upload_received,
				  bridge_state.upload_size,
				  remote_offset);
	}

	bridge_state.mode = BSGR_OTA_BRIDGE_MODE_COMMAND;
	bridge_state.chunk_expected = 0U;
	bridge_state.chunk_received = 0U;
}

int ota_bridge_init(void)
{
	memset(&bridge_state, 0, sizeof(bridge_state));
	return 0;
}

void ota_bridge_process(void)
{
	uint8_t ch;

	while (cdc_async_poll_in(BSGR_CDC_CHANNEL_DATA, &ch) == 0) {
		if (bridge_state.mode == BSGR_OTA_BRIDGE_MODE_CHUNK) {
			bridge_process_chunk_byte(ch);
			continue;
		}

		if (ch == '\r') {
			continue;
		}

		if (ch == '\n') {
			bridge_state.cmd_buf[bridge_state.cmd_len] = '\0';
			if (bridge_state.cmd_len > 0U) {
				(void)bridge_handle_command(bridge_state.cmd_buf);
			}
			bridge_state.cmd_len = 0U;
			continue;
		}

		if (bridge_state.cmd_len < (sizeof(bridge_state.cmd_buf) - 1U)) {
			bridge_state.cmd_buf[bridge_state.cmd_len++] = (char)ch;
		}
	}
}
