#include <errno.h>
#include <ctype.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <strings.h>

#include <dk_buttons_and_leds.h>

#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/hci_types.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/sys/util.h>
#if defined(CONFIG_USB_DEVICE_STACK)
#include <zephyr/usb/usb_device.h>
#endif

#include "master_multi_app.h"
#include "master_ota.h"

#define CONTROL_SETTINGS_SUBTREE "master_ctrl"
#define CONTROL_SETTINGS_MODE_KEY "mode"
#define CONTROL_SETTINGS_AUTOPOS_TARGET_KEY "autopos_target"
#define CONTROL_BOOT_COOKIE_MAGIC 0x42534d44U
#define OTA_TARGET_BOOT_COOKIE_MAGIC 0x4f544147U
#define OTA_NUS_BOOT_COOKIE_MAGIC 0x4f54414eU

enum control_mode {
	CONTROL_MODE_RECV = 0,
	CONTROL_MODE_OTA = 1,
	CONTROL_MODE_AUTOPOS = 2,
};

enum system_device_kind {
	SYS_DEV_UNKNOWN = 0,
	SYS_DEV_ANCHOR = 1,
	SYS_DEV_TAG = 2,
};

enum system_device_caps {
	SYS_CAP_CONFIG = BIT(0),
	SYS_CAP_OTA = BIT(1),
	SYS_CAP_STREAM = BIT(2),
	SYS_CAP_STATUS = BIT(3),
};

struct system_target_profile {
	uint8_t kind;
	uint8_t caps;
};

static uint8_t control_mode = CONTROL_MODE_RECV;
static uint32_t control_boot_cookie __noinit;
static uint8_t control_boot_mode __noinit;
static uint32_t ota_target_boot_cookie __noinit;
static int16_t ota_target_boot_token __noinit;
static char ota_target_boot_name[32] __noinit;
static char ota_target_boot_prefix[32] __noinit;
static char ota_target_boot_uuid[33] __noinit;
static uint32_t ota_nus_boot_cookie __noinit;
static uint8_t ota_expect_nus_boot __noinit;
static int ota_target_token_cfg = -1;
static char ota_target_name_cfg[32];
static char ota_target_prefix_cfg[32] = "BS";
static char ota_target_uuid_cfg[33];
static bool leds_ready;
static struct k_work mode_switch_work;
static struct k_work uart_cmd_work;
static atomic_t mode_switch_pending;
static uint8_t requested_mode;
static uint8_t requested_source;
static const struct device *const console_uart = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
static char uart_pending_line[64];
static size_t uart_pending_len;

static const char *control_log_prefix(void)
{
	switch (control_mode) {
	case CONTROL_MODE_AUTOPOS:
		return "AUTOPOS";
	case CONTROL_MODE_OTA:
		return "OTA";
	case CONTROL_MODE_RECV:
	default:
		return "RECV";
	}
}

static void control_mode_printk(const char *fmt, ...)
{
	va_list ap;

	printk("[%s] ", control_log_prefix());
	va_start(ap, fmt);
	vprintk(fmt, ap);
	va_end(ap);
}

#define printk control_mode_printk
static atomic_t uart_line_ready;
static struct system_target_profile system_target;
static bool ota_expect_nus_cfg = true;
static bool ota_transition_active;
static struct k_work autopos_apply_work;
static atomic_t autopos_apply_pending;
static struct k_work anchor_role_work;
static atomic_t anchor_role_pending;
static struct k_work_q autopos_work_q;
K_THREAD_STACK_DEFINE(autopos_work_q_stack, 4096);

#define AUTOPOS_ANCHOR_COUNT 8
#define AUTOPOS_UUID_LEN 33
static const char autopos_labels[AUTOPOS_ANCHOR_COUNT] = {'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'};
static char autopos_uuid_map[AUTOPOS_ANCHOR_COUNT][AUTOPOS_UUID_LEN];
static int8_t autopos_target_idx = -1;

static int autopos_wait_anchor_ready(const char *uuid, int timeout_ms);
static int autopos_wait_anchor_cleared(int timeout_ms);
static int autopos_send_anchor_cmd_checked(const char *cmd, const char *expect_result,
					   int result_timeout_ms);
static int autopos_reconnect_anchor_ready(int idx, int timeout_ms);
static int autopos_enter_config_window(int idx);
static void anchor_role_work_handler(struct k_work *work);
static int8_t autopos_last_success_idx = -1;
static char autopos_state[16] = "idle";
static char autopos_last_error[96];
static char anchor_role_target[40];
static char anchor_role_value[16];
static bool anchor_role_all_targets;

struct disconnect_ctx {
	int requested;
};

enum request_source {
	REQ_SRC_BTN1 = 1,
	REQ_SRC_BTN2 = 2,
	REQ_SRC_BTN3 = 3,
	REQ_SRC_BTN4 = 4,
	REQ_SRC_UART = 5,
};

int master_app_run(void);

static const char *control_mode_name(uint8_t mode)
{
	switch (mode) {
	case CONTROL_MODE_OTA:
		return "OTA";
	case CONTROL_MODE_AUTOPOS:
		return "AUTOPOS";
	default:
		return "RECV";
	}
}

static void sync_master_log_mode(uint8_t mode)
{
	switch (mode) {
	case CONTROL_MODE_AUTOPOS:
		master_set_log_mode(MASTER_LOG_MODE_AUTOPOS);
		break;
	case CONTROL_MODE_OTA:
		master_set_log_mode(MASTER_LOG_MODE_OTA);
		break;
	case CONTROL_MODE_RECV:
	default:
		master_set_log_mode(MASTER_LOG_MODE_RECV);
		break;
	}
}

static void control_leds_set(uint32_t leds)
{
	if (!leds_ready) {
		return;
	}

	(void)dk_set_leds(leds);
}

static void control_blink_ack(void)
{
	for (int i = 0; i < 3; ++i) {
		control_leds_set(DK_ALL_LEDS_MSK);
		k_sleep(K_MSEC(140));
		control_leds_set(DK_NO_LEDS_MSK);
		k_sleep(K_MSEC(140));
	}
}

static void control_print_status(void)
{
	printk("Control status: mode=%s pending=%lu\n",
	       control_mode_name(control_mode),
	       (unsigned long)atomic_get(&mode_switch_pending));
}

static void control_print_help(void)
{
	printk("Commands: status | mode recv | mode ota | mode autopos | scan | conn | initiate\n");
	printk("OTA runtime cmds: ota_reset\n");
	printk("Runtime NUS cmds: cmd <raw> | oneshot <raw> | oneshot show | oneshot clear\n");
	printk("Device model cmds: device show | device kind <anchor|tag>\n");
	printk("OTA target cmds: ota_target show | ota_target token <id|-1> | ota_target name <BSxxxx|-> | ota_target prefix <BS|-> | ota_target uuid <32hex|->\n");
	printk("Anchor cmds: anchor version <A..H|UUID32|all> | anchor role <A..H|UUID32|all> <master|matrix|responder>\n");
	printk("AUTOPOS cmds: autopos status | autopos map <A..H> <UUID32> | autopos map show | autopos round <A..H> | autopos apply\n");
}

static const char *system_kind_name(uint8_t kind)
{
	switch (kind) {
	case SYS_DEV_ANCHOR:
		return "anchor";
	case SYS_DEV_TAG:
		return "tag";
	default:
		return "unknown";
	}
}

static uint8_t system_caps_for_kind(uint8_t kind)
{
	switch (kind) {
	case SYS_DEV_ANCHOR:
		return SYS_CAP_CONFIG | SYS_CAP_OTA | SYS_CAP_STATUS;
	case SYS_DEV_TAG:
		return SYS_CAP_CONFIG | SYS_CAP_OTA | SYS_CAP_STREAM | SYS_CAP_STATUS;
	default:
		return SYS_CAP_STATUS;
	}
}

static void system_target_set_kind(uint8_t kind)
{
	system_target.kind = kind;
	system_target.caps = system_caps_for_kind(kind);
	/* Anchor OTA path should skip NUS arm stage and go directly DFU/SMP. */
	ota_expect_nus_cfg = (kind != SYS_DEV_ANCHOR);
	master_ota_set_expect_nus(ota_expect_nus_cfg);
}

static void system_target_print(void)
{
	printk("System target: kind=%s caps=0x%02x (config=%u ota=%u stream=%u status=%u)\n",
	       system_kind_name(system_target.kind),
	       system_target.caps,
	       (system_target.caps & SYS_CAP_CONFIG) ? 1U : 0U,
	       (system_target.caps & SYS_CAP_OTA) ? 1U : 0U,
	       (system_target.caps & SYS_CAP_STREAM) ? 1U : 0U,
	       (system_target.caps & SYS_CAP_STATUS) ? 1U : 0U);
	master_ota_target_print();
}

static int autopos_label_to_index(char label)
{
	char up = (char)toupper((unsigned char)label);

	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		if (autopos_labels[i] == up) {
			return i;
		}
	}
	return -1;
}

static void autopos_set_error(const char *text)
{
	(void)snprintf(autopos_last_error, sizeof(autopos_last_error), "%s",
		       (text != NULL) ? text : "-");
	(void)snprintf(autopos_state, sizeof(autopos_state), "failed");
}

static void autopos_print_status(void)
{
	printk("AUTOPOS: mode=%s state=%s staged=%c last_success=%c error=%s\n",
	       control_mode_name(control_mode),
	       autopos_state,
	       (autopos_target_idx >= 0) ? autopos_labels[autopos_target_idx] : '-',
	       (autopos_last_success_idx >= 0) ? autopos_labels[autopos_last_success_idx] : '-',
	       autopos_last_error[0] != '\0' ? autopos_last_error : "-");
}

static void autopos_print_map(void)
{
	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		printk("AUTOPOS map %c=%s\n", autopos_labels[i],
		       autopos_uuid_map[i][0] != '\0' ? autopos_uuid_map[i] : "-");
	}
}

static bool autopos_map_complete(void)
{
	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		if (autopos_uuid_map[i][0] == '\0') {
			return false;
		}
	}
	return true;
}

static int control_settings_set(const char *key, size_t len,
				settings_read_cb read_cb, void *cb_arg)
{
	const char *next;
	char map_key[16];

	if (!settings_name_steq(key, CONTROL_SETTINGS_MODE_KEY, &next) || next != NULL) {
		goto maybe_autopos;
	}

	if (len != sizeof(control_mode)) {
		return -EINVAL;
	}

	return read_cb(cb_arg, &control_mode, sizeof(control_mode));

maybe_autopos:
	if (settings_name_steq(key, CONTROL_SETTINGS_AUTOPOS_TARGET_KEY, &next) && next == NULL) {
		int8_t staged = -1;

		if (len != sizeof(staged)) {
			return -EINVAL;
		}
		if (read_cb(cb_arg, &staged, sizeof(staged)) >= 0) {
			autopos_target_idx = staged;
		}
		return 0;
	}

	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		(void)snprintf(map_key, sizeof(map_key), "autopos_map_%c",
			       (char)tolower((unsigned char)autopos_labels[i]));
		if (settings_name_steq(key, map_key, &next) && next == NULL) {
			if (len == 0U) {
				autopos_uuid_map[i][0] = '\0';
				return 0;
			}
			if (len >= AUTOPOS_UUID_LEN) {
				return -EINVAL;
			}
			memset(autopos_uuid_map[i], 0, sizeof(autopos_uuid_map[i]));
			(void)read_cb(cb_arg, autopos_uuid_map[i], len);
			autopos_uuid_map[i][len] = '\0';
			return 0;
		}
	}

	return -ENOENT;
}

static int control_settings_export(int (*cb)(const char *name, const void *value,
					     size_t val_len))
{
	int rc;
	char map_key[16];

	rc = cb(CONTROL_SETTINGS_MODE_KEY, &control_mode, sizeof(control_mode));
	if (rc != 0) {
		return rc;
	}
	rc = cb(CONTROL_SETTINGS_AUTOPOS_TARGET_KEY, &autopos_target_idx, sizeof(autopos_target_idx));
	if (rc != 0) {
		return rc;
	}
	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		if (autopos_uuid_map[i][0] == '\0') {
			continue;
		}
		(void)snprintf(map_key, sizeof(map_key), "autopos_map_%c",
			       (char)tolower((unsigned char)autopos_labels[i]));
		rc = cb(map_key, autopos_uuid_map[i], strlen(autopos_uuid_map[i]) + 1U);
		if (rc != 0) {
			return rc;
		}
	}
	return 0;
}

SETTINGS_STATIC_HANDLER_DEFINE(master_ctrl, CONTROL_SETTINGS_SUBTREE, NULL,
			       control_settings_set, NULL, control_settings_export);

static void autopos_save_target(void)
{
	if (!IS_ENABLED(CONFIG_SETTINGS)) {
		return;
	}
	(void)settings_save_one(CONTROL_SETTINGS_SUBTREE "/" CONTROL_SETTINGS_AUTOPOS_TARGET_KEY,
				&autopos_target_idx, sizeof(autopos_target_idx));
}

static void autopos_save_map_entry(int idx)
{
	char key[48];

	if (!IS_ENABLED(CONFIG_SETTINGS) || idx < 0 || idx >= AUTOPOS_ANCHOR_COUNT) {
		return;
	}
	(void)snprintf(key, sizeof(key), CONTROL_SETTINGS_SUBTREE "/autopos_map_%c",
		       (char)tolower((unsigned char)autopos_labels[idx]));
	(void)settings_save_one(key, autopos_uuid_map[idx],
				strlen(autopos_uuid_map[idx]) + 1U);
}

static void control_save_mode(void)
{
	control_boot_mode = control_mode;
	control_boot_cookie = CONTROL_BOOT_COOKIE_MAGIC;
	printk("Control mode staged: %s\n", control_mode_name(control_mode));
}

static void control_stage_ota_target(void)
{
	ota_target_boot_token = (int16_t)ota_target_token_cfg;
	(void)snprintf(ota_target_boot_name, sizeof(ota_target_boot_name), "%s",
		       ota_target_name_cfg);
	(void)snprintf(ota_target_boot_prefix, sizeof(ota_target_boot_prefix), "%s",
		       ota_target_prefix_cfg);
	(void)snprintf(ota_target_boot_uuid, sizeof(ota_target_boot_uuid), "%s",
		       ota_target_uuid_cfg);
	ota_target_boot_cookie = OTA_TARGET_BOOT_COOKIE_MAGIC;
	printk("OTA target staged: token=%d name=%s prefix=%s uuid=%s\n",
	       ota_target_token_cfg,
	       ota_target_name_cfg[0] != '\0' ? ota_target_name_cfg : "-",
	       ota_target_prefix_cfg[0] != '\0' ? ota_target_prefix_cfg : "-",
	       ota_target_uuid_cfg[0] != '\0' ? ota_target_uuid_cfg : "-");
	ota_expect_nus_boot = ota_expect_nus_cfg ? 1U : 0U;
	ota_nus_boot_cookie = OTA_NUS_BOOT_COOKIE_MAGIC;
}

static void disconnect_each_cb(struct bt_conn *conn, void *user_data)
{
	struct disconnect_ctx *ctx = user_data;
	int err;

	if (!conn || !ctx) {
		return;
	}

	err = bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
	if (err == 0 || err == -EALREADY || err == -ENOTCONN) {
		ctx->requested++;
	}
}

static void control_disconnect_all_links(void)
{
	struct disconnect_ctx ctx = {0};

	bt_conn_foreach(BT_CONN_TYPE_ALL, disconnect_each_cb, &ctx);
	printk("Mode switch preflight: disconnect requests=%d\n", ctx.requested);
	/* Give BLE stack a short settle window before rebooting to next mode. */
	k_sleep(K_MSEC(250));
}

static void control_wait_for_peer_clear(int timeout_ms)
{
	int waited = 0;
	int last_conn = -1;
	int last_ready = -1;

	while (waited < timeout_ms) {
		int conn_count = master_connection_count();
		int ready_count = master_anchor_ctrl_ready_count();

		if (conn_count != last_conn || ready_count != last_ready) {
			printk("Peer clear wait: waited=%d conn_count=%d ready_count=%d\n",
			       waited, conn_count, ready_count);
			last_conn = conn_count;
			last_ready = ready_count;
		}

		if (conn_count == 0 && ready_count == 0) {
			return;
		}

		k_sleep(K_MSEC(100));
		waited += 100;
	}

	printk("Peer clear wait timeout: conn_count=%d ready_count=%d\n",
	       master_connection_count(), master_anchor_ctrl_ready_count());
}

static void control_prepare_clean_recv_session(void)
{
	master_clear_one_shot_command();
	master_set_connect_and_start_mode();
	master_disconnect_all_peers();
	control_wait_for_peer_clear(3000);
	master_restart_discovery();
	printk("RECV clean slate prepared\n");
}

static int autopos_wait_anchor_ready(const char *uuid, int timeout_ms)
{
	int waited = 0;
	int last_count = -1;

	/* For anchor sweep, match readiness by UUID only.
	 * A stale runtime name/prefix filter (e.g. BSF66F from tag session)
	 * can otherwise make ready_count stay 0 even when anchor-ctrl is ready.
	 */
	master_set_runtime_target_name("");
	master_set_runtime_target_prefix("");
	master_set_runtime_target_kind(MASTER_TARGET_ANCHOR);
	master_set_runtime_target_uuid(uuid);
	master_set_connect_and_start_mode();
	master_disconnect_all_peers();
	control_wait_for_peer_clear(3000);
	master_restart_discovery();
	printk("AUTOPOS wait anchor ready: uuid=%s timeout_ms=%d\n", uuid, timeout_ms);

	while (waited < timeout_ms) {
		master_process_connect_pending();
		master_process_setup_pending();
		int ready_count = master_anchor_ctrl_ready_count();
		if (ready_count != last_count || (waited % 1000) == 0) {
			printk("AUTOPOS wait anchor ready: waited=%d ready_count=%d\n",
			       waited, ready_count);
			master_dump_ready_state();
			last_count = ready_count;
		}
		if (ready_count > 0) {
			return 0;
		}
		k_sleep(K_MSEC(200));
		waited += 200;
	}
	printk("AUTOPOS wait anchor ready timeout: uuid=%s waited=%d\n", uuid, waited);
	master_dump_ready_state();
	return -ETIMEDOUT;
}

static int autopos_wait_anchor_cleared(int timeout_ms)
{
	int waited = 0;
	int last_conn = -1;
	int last_ready = -1;

	while (waited < timeout_ms) {
		int conn_count = master_connection_count();
		int ready_count = master_anchor_ctrl_ready_count();

		if (conn_count != last_conn || ready_count != last_ready || (waited % 1000) == 0) {
			printk("AUTOPOS wait anchor cleared: waited=%d conn_count=%d ready_count=%d\n",
			       waited, conn_count, ready_count);
			master_dump_ready_state();
			last_conn = conn_count;
			last_ready = ready_count;
		}

		if (conn_count == 0 && ready_count == 0) {
			return 0;
		}

		k_sleep(K_MSEC(100));
		waited += 100;
	}

	printk("AUTOPOS wait anchor cleared timeout: waited=%d conn_count=%d ready_count=%d\n",
	       waited, master_connection_count(), master_anchor_ctrl_ready_count());
	master_dump_ready_state();
	return -ETIMEDOUT;
}

static int autopos_wait_role_state(const char *expect_role, int timeout_ms)
{
	char state[256];
	int waited = 0;
	int rc;
	char needle[32];

	(void)snprintf(needle, sizeof(needle), "role=%s", expect_role);
	while (waited < timeout_ms) {
		rc = master_anchor_ctrl_read_state(state, sizeof(state));
		if (rc == 0) {
			printk("AUTOPOS state: %s\n", state);
			if (strstr(state, needle) != NULL) {
				return 0;
			}
		}
		k_sleep(K_MSEC(200));
		waited += 200;
	}
	return -ETIMEDOUT;
}

static int autopos_wait_state_field(const char *needle, int timeout_ms)
{
	char state[256];
	int waited = 0;
	int rc;

	while (waited < timeout_ms) {
		rc = master_anchor_ctrl_read_state(state, sizeof(state));
		if (rc == 0) {
			printk("AUTOPOS state: %s\n", state);
			if (strstr(state, needle) != NULL) {
				return 0;
			}
		}
		k_sleep(K_MSEC(200));
		waited += 200;
	}
	return -ETIMEDOUT;
}

static int autopos_wait_result_contains(const char *needle, int timeout_ms)
{
	char result[256];
	int waited = 0;
	int rc;

	while (waited < timeout_ms) {
		rc = master_anchor_ctrl_read_result(result, sizeof(result));
		if (rc == 0) {
			printk("AUTOPOS result: %s\n", result);
			if (needle != NULL && strstr(result, needle) != NULL) {
				return 0;
			}
		}
		k_sleep(K_MSEC(150));
		waited += 150;
	}
	return -ETIMEDOUT;
}

static const char *anchor_state_field_value(const char *state, const char *key,
					    char *out, size_t out_len)
{
	const char *pos;
	size_t key_len;
	const char *end;
	size_t copy_len;

	if (state == NULL || key == NULL || out == NULL || out_len == 0U) {
		return NULL;
	}

	pos = strstr(state, key);
	if (pos == NULL) {
		return NULL;
	}

	key_len = strlen(key);
	pos += key_len;
	end = pos;
	while (*end != '\0' && *end != ' ') {
		end++;
	}

	copy_len = MIN((size_t)(end - pos), out_len - 1U);
	memcpy(out, pos, copy_len);
	out[copy_len] = '\0';
	return out;
}

static int anchor_resolve_query_uuid(const char *query, char *uuid_out, size_t uuid_out_len,
				     char *label_out, size_t label_out_len)
{
	int idx;

	if (query == NULL || uuid_out == NULL || uuid_out_len < AUTOPOS_UUID_LEN) {
		return -EINVAL;
	}

	if (strlen(query) == 1U) {
		idx = autopos_label_to_index(query[0]);
		if (idx < 0) {
			return -EINVAL;
		}
		if (autopos_uuid_map[idx][0] == '\0') {
			return -ENOENT;
		}
		(void)snprintf(uuid_out, uuid_out_len, "%s", autopos_uuid_map[idx]);
		if (label_out != NULL && label_out_len > 0U) {
			(void)snprintf(label_out, label_out_len, "%c", autopos_labels[idx]);
		}
		return 0;
	}

	if (strlen(query) != 32U) {
		return -EINVAL;
	}

	for (size_t i = 0; i < 32U; ++i) {
		if (!isxdigit((unsigned char)query[i])) {
			return -EINVAL;
		}
	}

	memcpy(uuid_out, query, 32U);
	uuid_out[32] = '\0';
	if (label_out != NULL && label_out_len > 0U) {
		label_out[0] = '\0';
	}
	return 0;
}

static int anchor_query_version(const char *query)
{
	char uuid[33];
	char label[4];
	char state[256];
	char fw[48] = { 0 };
	char state_label[8] = { 0 };
	char role[24] = { 0 };
	int rc;

	rc = anchor_resolve_query_uuid(query, uuid, sizeof(uuid), label, sizeof(label));
	if (rc != 0) {
		printk("anchor version invalid target=%s rc=%d\n",
		       (query != NULL) ? query : "-", rc);
		return rc;
	}

	rc = autopos_wait_anchor_ready(uuid, 12000);
	if (rc != 0) {
		printk("anchor version connect failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}

	rc = master_anchor_ctrl_read_state(state, sizeof(state));
	if (rc != 0) {
		printk("anchor version state read failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}

	(void)anchor_state_field_value(state, "fw=", fw, sizeof(fw));
	(void)anchor_state_field_value(state, "label=", state_label, sizeof(state_label));
	(void)anchor_state_field_value(state, "role=", role, sizeof(role));
	printk("ANCHOR_VERSION query=%s uuid=%s fw=%s label=%s role=%s\n",
	       label[0] != '\0' ? label : query,
	       uuid,
	       fw[0] != '\0' ? fw : "-",
	       state_label[0] != '\0' ? state_label : "-",
	       role[0] != '\0' ? role : "-");
	printk("ANCHOR_VERSION state=%s\n", state);
	return 0;
}

static int anchor_query_version_all(void)
{
	int rc = 0;
	int first_err = 0;
	char label[2];

	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		if (autopos_uuid_map[i][0] == '\0') {
			printk("ANCHOR_VERSION query=%c skipped: uuid not mapped\n",
			       autopos_labels[i]);
			continue;
		}

		label[0] = autopos_labels[i];
		label[1] = '\0';
		rc = anchor_query_version(label);
		if (rc != 0 && first_err == 0) {
			first_err = rc;
		}
		k_sleep(K_MSEC(150));
	}

	return first_err;
}

static int anchor_apply_role_uuid(const char *query, const char *role_cmd,
				  const char *expect_role)
{
	char uuid[33];
	char label[4];
	int rc;

	rc = anchor_resolve_query_uuid(query, uuid, sizeof(uuid), label, sizeof(label));
	if (rc != 0) {
		printk("anchor role invalid target=%s rc=%d\n",
		       (query != NULL) ? query : "-", rc);
		return rc;
	}

	rc = autopos_wait_anchor_ready(uuid, 12000);
	if (rc != 0) {
		printk("anchor role connect failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}

	if (label[0] != '\0') {
		rc = autopos_enter_config_window(autopos_label_to_index(label[0]));
	} else {
		/* For direct UUID queries not present in the A-H map, fall back to a
		 * simple busy wait without indexed reboot recovery.
		 */
		rc = autopos_wait_state_field("busy=0", 3000);
	}
	if (rc != 0) {
		printk("anchor role config window failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}

	rc = autopos_send_anchor_cmd_checked(role_cmd, "OK PENDING_ROLE", 1200);
	if (rc != 0) {
		printk("anchor role pending failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}
	rc = autopos_send_anchor_cmd_checked("VALIDATE", "OK VALID", 1200);
	if (rc != 0) {
		printk("anchor role validate failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}
	rc = autopos_send_anchor_cmd_checked("COMMIT", "OK COMMIT REBOOT_REQUIRED", 1500);
	if (rc != 0) {
		printk("anchor role commit failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}
	rc = autopos_send_anchor_cmd_checked("REBOOT", NULL, 0);
	if (rc != 0) {
		printk("anchor role reboot failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}

	if (label[0] != '\0') {
		rc = autopos_reconnect_anchor_ready(autopos_label_to_index(label[0]), 15000);
	} else {
		master_disconnect_all_peers();
		(void)autopos_wait_anchor_cleared(2500);
		k_sleep(K_MSEC(1400));
		rc = autopos_wait_anchor_ready(uuid, 15000);
	}
	if (rc != 0) {
		printk("anchor role reconnect failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}

	rc = autopos_wait_role_state(expect_role, 5000);
	printk("anchor role rc=%d target=%s uuid=%s role=%s\n",
	       rc,
	       label[0] != '\0' ? label : query,
	       uuid,
	       expect_role);
	return rc;
}

static int anchor_apply_role(const char *query, const char *role)
{
	char role_cmd[24];
	char expect_role[16];

	if (role == NULL) {
		return -EINVAL;
	}

	if (strcmp(role, "master") == 0) {
		(void)snprintf(role_cmd, sizeof(role_cmd), "R MASTER");
		(void)snprintf(expect_role, sizeof(expect_role), "master");
	} else if (strcmp(role, "matrix") == 0) {
		(void)snprintf(role_cmd, sizeof(role_cmd), "R MATRIX");
		(void)snprintf(expect_role, sizeof(expect_role), "matrix");
	} else if (strcmp(role, "responder") == 0) {
		(void)snprintf(role_cmd, sizeof(role_cmd), "R RESPONDER");
		(void)snprintf(expect_role, sizeof(expect_role), "responder");
	} else {
		return -EINVAL;
	}

	return anchor_apply_role_uuid(query, role_cmd, expect_role);
}

static int anchor_apply_role_all(const char *role)
{
	int rc = 0;
	int first_err = 0;
	char label[2];

	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		if (autopos_uuid_map[i][0] == '\0') {
			printk("anchor role query=%c skipped: uuid not mapped\n",
			       autopos_labels[i]);
			continue;
		}

		label[0] = autopos_labels[i];
		label[1] = '\0';
		rc = anchor_apply_role(label, role);
		if (rc != 0 && first_err == 0) {
			first_err = rc;
		}
		k_sleep(K_MSEC(150));
	}

	return first_err;
}

static int autopos_send_anchor_cmd_checked(const char *cmd, const char *expect_result,
					   int result_timeout_ms)
{
	int rc;

	rc = master_send_command_now(cmd);
	if (rc < 0) {
		return rc;
	}
	if (expect_result == NULL || expect_result[0] == '\0') {
		return 0;
	}
	return autopos_wait_result_contains(expect_result, result_timeout_ms);
}

static int autopos_reconnect_anchor_ready(int idx, int timeout_ms)
{
	int rc;

	master_disconnect_all_peers();
	(void)autopos_wait_anchor_cleared(2500);
	k_sleep(K_MSEC(1400));

	rc = autopos_wait_anchor_ready(autopos_uuid_map[idx], timeout_ms);
	if (rc != 0) {
		return rc;
	}

	return 0;
}

static int autopos_enter_config_window(int idx)
{
	int rc;

	for (int attempt = 0; attempt < 3; ++attempt) {
		rc = autopos_wait_state_field("busy=0", attempt == 0 ? 1000 : 2000);
		if (rc == 0) {
			return 0;
		}

		printk("AUTOPOS anchor %c busy; rebooting into config window attempt=%d/3\n",
		       autopos_labels[idx], attempt + 1);
		rc = autopos_send_anchor_cmd_checked("REBOOT", NULL, 0);
		if (rc < 0) {
			return rc;
		}

		rc = autopos_reconnect_anchor_ready(idx, 15000);
		if (rc != 0) {
			return rc;
		}
	}

	return -ETIMEDOUT;
}

static int autopos_apply_one_anchor(int idx, bool is_master)
{
	int rc;
	const char *role_cmd = is_master ? "R MASTER" : "R MATRIX";
	const char *expect_role = is_master ? "master" : "matrix";

	rc = autopos_wait_anchor_ready(autopos_uuid_map[idx], 12000);
	if (rc != 0) {
		return rc;
	}

	rc = autopos_enter_config_window(idx);
	if (rc != 0) {
		return rc;
	}

	rc = autopos_send_anchor_cmd_checked(role_cmd, "OK PENDING_ROLE", 1200);
	if (rc != 0) {
		return rc;
	}
	rc = autopos_send_anchor_cmd_checked("VALIDATE", "OK VALID", 1200);
	if (rc != 0) {
		return rc;
	}
	rc = autopos_send_anchor_cmd_checked("COMMIT", "OK COMMIT REBOOT_REQUIRED", 1500);
	if (rc != 0) {
		return rc;
	}
	rc = autopos_send_anchor_cmd_checked("REBOOT", NULL, 0);
	if (rc != 0) {
		return rc;
	}

	rc = autopos_reconnect_anchor_ready(idx, 15000);
	if (rc != 0) {
		return rc;
	}
	return autopos_wait_role_state(expect_role, 5000);
}

static void autopos_apply_work_handler(struct k_work *work)
{
	int rc = 0;

	ARG_UNUSED(work);

	if (control_mode != CONTROL_MODE_AUTOPOS) {
		autopos_set_error("not in AUTOPOS mode");
		goto done;
	}
	if (autopos_target_idx < 0 || autopos_target_idx >= AUTOPOS_ANCHOR_COUNT) {
		autopos_set_error("round target not set");
		goto done;
	}
	if (!autopos_map_complete()) {
		autopos_set_error("incomplete AUTOPOS map");
		goto done;
	}

	(void)snprintf(autopos_state, sizeof(autopos_state), "applying");
	autopos_last_error[0] = '\0';
	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		rc = autopos_apply_one_anchor(i, i == autopos_target_idx);
		if (rc != 0) {
			(void)snprintf(autopos_last_error, sizeof(autopos_last_error),
				       "anchor %c step failed rc=%d", autopos_labels[i], rc);
			(void)snprintf(autopos_state, sizeof(autopos_state), "failed");
			printk("AUTOPOS apply failed: %s\n", autopos_last_error);
			goto done;
		}
		printk("AUTOPOS anchor %c role verified\n", autopos_labels[i]);
	}

	autopos_last_success_idx = autopos_target_idx;
	(void)snprintf(autopos_state, sizeof(autopos_state), "ready");
	printk("AUTOPOS apply success: master=%c\n", autopos_labels[autopos_target_idx]);
	printk("AUTOPOS sweep listen attach: master=%c uuid=%s\n",
	       autopos_labels[autopos_target_idx],
	       autopos_uuid_map[autopos_target_idx]);
	master_set_runtime_target_kind(MASTER_TARGET_ANCHOR);
	master_set_runtime_target_uuid(autopos_uuid_map[autopos_target_idx]);
	master_set_connect_and_start_mode();
	master_disconnect_all_peers();
	master_restart_discovery();

done:
	atomic_set(&autopos_apply_pending, 0);
}

static void anchor_role_work_handler(struct k_work *work)
{
	int rc;

	ARG_UNUSED(work);

	if (control_mode == CONTROL_MODE_OTA) {
		printk("anchor role worker ignored: control mode must not be OTA\n");
		goto done;
	}

	if (anchor_role_all_targets) {
		rc = anchor_apply_role_all(anchor_role_value);
		printk("anchor role rc=%d target=all role=%s\n", rc, anchor_role_value);
	} else {
		rc = anchor_apply_role(anchor_role_target, anchor_role_value);
		printk("anchor role rc=%d target=%s role=%s\n",
		       rc, anchor_role_target, anchor_role_value);
	}

done:
	atomic_set(&anchor_role_pending, 0);
}

static void mode_switch_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	switch (requested_source) {
	case REQ_SRC_BTN1:
		printk("BTN1 pressed: switching to %s\n", control_mode_name(requested_mode));
		break;
	case REQ_SRC_BTN2:
		printk("BTN2 pressed: switching to %s\n", control_mode_name(requested_mode));
		break;
	case REQ_SRC_BTN3:
		printk("BTN3 pressed: SCAN only\n");
		break;
	case REQ_SRC_BTN4:
		printk("BTN4 pressed: CONN & START\n");
		break;
	case REQ_SRC_UART:
	default:
		printk("UART requested: switching to %s\n", control_mode_name(requested_mode));
		break;
	}

	control_blink_ack();
	if (requested_mode == CONTROL_MODE_OTA) {
		ota_transition_active = true;
		printk("MODE_TRANSITION: RECV->OTA transition_active=1\n");
		master_set_background_gate(false, "mode_switch_to_ota");
		master_disconnect_all_peers();
	} else if (requested_mode == CONTROL_MODE_RECV) {
		/* Ensure OTA side releases scan/conn ownership before rebooting back
		 * to RECV mode. This keeps mode handoff deterministic in unified image.
		 */
		master_ota_prepare_mode_switch();
	}
	control_disconnect_all_links();
	control_mode = requested_mode;
	sync_master_log_mode(control_mode);
	control_save_mode();
	control_stage_ota_target();
	printk("Control mode now %s, rebooting\n", control_mode_name(control_mode));
	k_sleep(K_MSEC(100));
	sys_reboot(SYS_REBOOT_WARM);
}

static void request_mode_switch(uint8_t new_mode, uint8_t button_no)
{
	if (!atomic_cas(&mode_switch_pending, 0, 1)) {
		printk("Mode switch already pending\n");
		return;
	}

	if (new_mode == control_mode) {
		printk("Control mode already %s\n", control_mode_name(control_mode));
		atomic_set(&mode_switch_pending, 0);
		return;
	}

	if (new_mode == CONTROL_MODE_OTA && control_mode == CONTROL_MODE_RECV) {
		/* Close RECV background gate immediately to minimize transition
		 * window before mode-switch worker runs.
		 */
		ota_transition_active = true;
		printk("MODE_TRANSITION: request RECV->OTA (pre-gate)\n");
		master_set_background_gate(false, "request_to_ota");
	}

	requested_mode = new_mode;
	requested_source = button_no;
	k_work_submit(&mode_switch_work);
}

static void button_handler(uint32_t button_state, uint32_t has_changed)
{
	if ((has_changed & DK_BTN1_MSK) && (button_state & DK_BTN1_MSK)) {
		uint8_t next_mode = (control_mode == CONTROL_MODE_RECV) ?
				    CONTROL_MODE_OTA : CONTROL_MODE_RECV;
		request_mode_switch(next_mode, REQ_SRC_BTN1);
		return;
	}

	if ((has_changed & DK_BTN2_MSK) && (button_state & DK_BTN2_MSK)) {
		request_mode_switch(CONTROL_MODE_OTA, REQ_SRC_BTN2);
		return;
	}

	if ((has_changed & DK_BTN3_MSK) && (button_state & DK_BTN3_MSK)) {
		if (control_mode == CONTROL_MODE_RECV) {
			requested_source = REQ_SRC_BTN3;
			master_set_scan_only_mode();
			master_disconnect_all_peers();
			master_restart_discovery();
		}
		return;
	}

	if ((has_changed & DK_BTN4_MSK) && (button_state & DK_BTN4_MSK)) {
		if (control_mode == CONTROL_MODE_RECV) {
			requested_source = REQ_SRC_BTN4;
			master_set_connect_and_start_mode();
			master_restart_discovery();
		}
		return;
	}
}

static void control_handle_uart_command(const char *line)
{
	char cmd[16];
	char arg[16];
	char arg2[64] = { 0 };
	int parsed;
	int rc;
	const char *payload;

	if (line == NULL || line[0] == '\0') {
		return;
	}

	if (strncasecmp(line, "cmd ", 4) == 0) {
		payload = line + 4;
		rc = master_send_command_now(payload);
		printk("cmd rc=%d payload=%s\n", rc, payload);
		return;
	}

	if (strncasecmp(line, "oneshot ", 8) == 0) {
		payload = line + 8;
		if (strcasecmp(payload, "show") == 0) {
			master_print_one_shot_command();
			return;
		}
		if (strcasecmp(payload, "clear") == 0) {
			master_clear_one_shot_command();
			return;
		}
		rc = master_set_one_shot_command(payload, true);
		printk("oneshot rc=%d payload=%s\n", rc, payload);
		return;
	}

	parsed = sscanf(line, "%15s %15s %63s", cmd, arg, arg2);
	for (char *p = cmd; *p != '\0'; ++p) {
		*p = (char)tolower((unsigned char)*p);
	}
	for (char *p = arg; *p != '\0'; ++p) {
		*p = (char)tolower((unsigned char)*p);
	}
	for (char *p = arg2; *p != '\0'; ++p) {
		*p = (char)tolower((unsigned char)*p);
	}

	if (strcmp(cmd, "status") == 0) {
		control_print_status();
		return;
	}

	if (strcmp(cmd, "scan") == 0) {
		if (control_mode != CONTROL_MODE_RECV && control_mode != CONTROL_MODE_AUTOPOS) {
			printk("SCAN ignored: control mode must be RECV/AUTOPOS\n");
			control_print_help();
			return;
		}

		requested_source = REQ_SRC_BTN3;
		if (ota_transition_active) {
			printk("SCAN ignored: OTA transition active\n");
			return;
		}
		master_set_scan_only_mode();
		master_disconnect_all_peers();
		master_restart_discovery();
		return;
	}

	if (strcmp(cmd, "conn") == 0) {
		if (control_mode != CONTROL_MODE_RECV && control_mode != CONTROL_MODE_AUTOPOS) {
			printk("CONN ignored: control mode must be RECV/AUTOPOS\n");
			control_print_help();
			return;
		}

		requested_source = REQ_SRC_BTN4;
		if (ota_transition_active) {
			printk("CONN ignored: OTA transition active\n");
			return;
		}
		master_set_connect_and_start_mode();
		master_restart_discovery();
		return;
	}

	if (strcmp(cmd, "initiate") == 0) {
		if (control_mode != CONTROL_MODE_OTA) {
			printk("INITIATE ignored: control mode must be OTA\n");
			control_print_help();
			return;
		}
		ota_transition_active = false;
		rc = master_ota_initiate();
		printk("initiate rc=%d\n", rc);
		return;
	}

	if (strcmp(cmd, "anchor") == 0 && parsed >= 3) {
		if (strcmp(arg, "version") == 0) {
			if (control_mode == CONTROL_MODE_OTA) {
				printk("anchor version ignored: control mode must not be OTA\n");
				return;
			}
			if (strcmp(arg2, "all") == 0) {
				rc = anchor_query_version_all();
				printk("anchor version rc=%d target=all\n", rc);
				return;
			}
			rc = anchor_query_version(arg2);
			printk("anchor version rc=%d target=%s\n", rc, arg2);
			return;
		}
		if (strcmp(arg, "role") == 0) {
			char role_raw[24];

			if (control_mode == CONTROL_MODE_OTA) {
				printk("anchor role ignored: control mode must not be OTA\n");
				return;
			}
			if (sscanf(line, "%*s %*s %*s %23s", role_raw) != 1) {
				printk("anchor role usage: anchor role <A..H|UUID32|all> <master|matrix|responder>\n");
				return;
			}
			for (char *p = role_raw; *p != '\0'; ++p) {
				*p = (char)tolower((unsigned char)*p);
			}
			if (strcmp(arg2, "all") == 0) {
				if (!atomic_cas(&anchor_role_pending, 0, 1)) {
					printk("anchor role already running\n");
					return;
				}
				anchor_role_all_targets = true;
				(void)snprintf(anchor_role_target, sizeof(anchor_role_target), "all");
				(void)snprintf(anchor_role_value, sizeof(anchor_role_value), "%s", role_raw);
				k_work_submit_to_queue(&autopos_work_q, &anchor_role_work);
				printk("anchor role started target=all role=%s\n", role_raw);
				return;
			}
			if (!atomic_cas(&anchor_role_pending, 0, 1)) {
				printk("anchor role already running\n");
				return;
			}
			anchor_role_all_targets = false;
			(void)snprintf(anchor_role_target, sizeof(anchor_role_target), "%s", arg2);
			(void)snprintf(anchor_role_value, sizeof(anchor_role_value), "%s", role_raw);
			k_work_submit_to_queue(&autopos_work_q, &anchor_role_work);
			printk("anchor role started target=%s role=%s\n", arg2, role_raw);
			return;
		}
	}

	if (strcmp(cmd, "ota_reset") == 0) {
		if (control_mode != CONTROL_MODE_OTA) {
			printk("ota_reset ignored: control mode must be OTA\n");
			control_print_help();
			return;
		}
		ota_transition_active = false;
		rc = master_ota_reset_target();
		printk("ota_reset rc=%d\n", rc);
		return;
	}

	if (strcmp(cmd, "mode") == 0 && parsed >= 2) {
		if (strcmp(arg, "ota") == 0) {
			if (control_mode == CONTROL_MODE_OTA) {
				ota_transition_active = false;
				rc = master_ota_initiate();
				printk("mode ota (already ota) -> initiate rc=%d\n", rc);
				return;
			}
			request_mode_switch(CONTROL_MODE_OTA, REQ_SRC_UART);
			return;
		}

		if (strcmp(arg, "recv") == 0 || strcmp(arg, "rx") == 0) {
			if (control_mode == CONTROL_MODE_RECV) {
				control_prepare_clean_recv_session();
				return;
			}
			request_mode_switch(CONTROL_MODE_RECV, REQ_SRC_UART);
			return;
		}

		if (strcmp(arg, "autopos") == 0) {
			if (control_mode == CONTROL_MODE_OTA) {
				printk("mode autopos ignored: switch to RECV first\n");
				return;
			}
			control_mode = CONTROL_MODE_AUTOPOS;
			sync_master_log_mode(control_mode);
			control_save_mode();
			(void)snprintf(autopos_state, sizeof(autopos_state), "idle");
			autopos_last_error[0] = '\0';
			master_set_runtime_target_kind(MASTER_TARGET_TAG);
			master_set_runtime_target_uuid("");
			master_set_connect_and_start_mode();
			master_disconnect_all_peers();
			control_wait_for_peer_clear(3000);
			master_restart_discovery();
			autopos_print_status();
			return;
		}
	}

	if (strcmp(cmd, "autopos") == 0 && parsed >= 2) {
		if (strcmp(arg, "status") == 0) {
			autopos_print_status();
			return;
		}
		if (strcmp(arg, "map") == 0) {
			if (parsed >= 3 && strcmp(arg2, "show") == 0) {
				autopos_print_map();
				return;
			}
			if (parsed < 3) {
				printk("autopos map usage: autopos map <A..H> <UUID32>\n");
				return;
			}
			if (strlen(arg2) != 1U) {
				printk("autopos map invalid label: %s\n", arg2);
				return;
			}
			int idx = autopos_label_to_index(arg2[0]);
			char uuid_raw[33];
			if (idx < 0) {
				printk("autopos map invalid label: %s\n", arg2);
				return;
			}
			if (sscanf(line, "%*s %*s %*s %32s", uuid_raw) != 1) {
				printk("autopos map parse failed\n");
				return;
			}
			for (char *p = uuid_raw; *p != '\0'; ++p) {
				*p = (char)toupper((unsigned char)*p);
			}
			if (strlen(uuid_raw) != 32U) {
				printk("autopos map invalid uuid len=%u\n", (unsigned int)strlen(uuid_raw));
				return;
			}
			(void)snprintf(autopos_uuid_map[idx], sizeof(autopos_uuid_map[idx]), "%s", uuid_raw);
			autopos_save_map_entry(idx);
			printk("AUTOPOS map set: %c=%s\n", autopos_labels[idx], autopos_uuid_map[idx]);
			return;
		}
		if (strcmp(arg, "round") == 0 && parsed >= 3) {
			if (strlen(arg2) != 1U) {
				printk("autopos round invalid label: %s\n", arg2);
				return;
			}
			autopos_target_idx = autopos_label_to_index(arg2[0]);
			if (autopos_target_idx < 0) {
				printk("autopos round invalid label: %s\n", arg2);
				return;
			}
			autopos_save_target();
			(void)snprintf(autopos_state, sizeof(autopos_state), "staged");
			autopos_last_error[0] = '\0';
			printk("AUTOPOS round staged: master=%c\n", autopos_labels[autopos_target_idx]);
			return;
		}
		if (strcmp(arg, "apply") == 0) {
			if (control_mode != CONTROL_MODE_AUTOPOS) {
				printk("autopos apply ignored: mode must be AUTOPOS\n");
				return;
			}
			if (!atomic_cas(&autopos_apply_pending, 0, 1)) {
				printk("autopos apply already running\n");
				return;
			}
			k_work_submit_to_queue(&autopos_work_q, &autopos_apply_work);
			printk("AUTOPOS apply started\n");
			return;
		}
	}

	if (strcmp(cmd, "ota_target") == 0 && parsed >= 2) {
		if (strcmp(arg, "show") == 0) {
			master_ota_target_print();
			return;
		}

		if (strcmp(arg, "token") == 0 && parsed >= 3) {
			int token = -1;
			int rc;

			if (sscanf(arg2, "%d", &token) != 1) {
				printk("ota_target token parse failed\n");
				return;
			}
			rc = master_ota_target_set_token(token);
			printk("ota_target token rc=%d value=%d\n", rc, token);
			if (rc == 0) {
				ota_target_token_cfg = token;
				master_set_runtime_target_token(token);
			}
			master_ota_target_print();
			return;
		}

		if (strcmp(arg, "name") == 0 && parsed >= 3) {
			char value[32];
			int rc;

			(void)snprintf(value, sizeof(value), "%s", arg2);
			if (strcmp(value, "-") == 0) {
				value[0] = '\0';
			}
			rc = master_ota_target_set_name(value);
			printk("ota_target name rc=%d value=%s\n", rc,
			       value[0] != '\0' ? value : "-");
			if (rc == 0) {
				(void)snprintf(ota_target_name_cfg, sizeof(ota_target_name_cfg), "%s",
					       value);
				master_set_runtime_target_name(value);
			}
			master_ota_target_print();
			return;
		}

		if (strcmp(arg, "prefix") == 0 && parsed >= 3) {
			char value[32];
			int rc;

			(void)snprintf(value, sizeof(value), "%s", arg2);
			if (strcmp(value, "-") == 0) {
				value[0] = '\0';
			}
			rc = master_ota_target_set_prefix(value);
			printk("ota_target prefix rc=%d value=%s\n", rc,
			       value[0] != '\0' ? value : "-");
			if (rc == 0) {
				(void)snprintf(ota_target_prefix_cfg, sizeof(ota_target_prefix_cfg), "%s",
					       value);
				master_set_runtime_target_prefix(value);
			}
			master_ota_target_print();
			return;
		}

		if (strcmp(arg, "uuid") == 0 && parsed >= 3) {
			char value[33];
			int rc;

			(void)snprintf(value, sizeof(value), "%s", arg2);
			if (strcmp(value, "-") == 0) {
				value[0] = '\0';
			}
			rc = master_ota_target_set_uuid(value);
			printk("ota_target uuid rc=%d value=%s\n", rc,
			       value[0] != '\0' ? value : "-");
			if (rc == 0) {
				(void)snprintf(ota_target_uuid_cfg, sizeof(ota_target_uuid_cfg), "%s",
					       value);
				master_set_runtime_target_uuid(value);
			}
			master_ota_target_print();
			return;
		}

		printk("Unknown ota_target command: %s\n", line);
		control_print_help();
		return;
	}

	if (strcmp(cmd, "device") == 0 && parsed >= 2) {
		if (strcmp(arg, "show") == 0) {
			system_target_print();
			return;
		}

		if (strcmp(arg, "kind") == 0 && parsed >= 3) {
			if (strcmp(arg2, "anchor") == 0) {
				system_target_set_kind(SYS_DEV_ANCHOR);
				master_clear_one_shot_command();
				master_disconnect_all_peers();
				(void)master_ota_target_set_token(-1);
				(void)master_ota_target_set_name("");
				(void)master_ota_target_set_prefix("BS");
				(void)master_ota_target_set_uuid("");
				ota_target_token_cfg = -1;
				ota_target_name_cfg[0] = '\0';
				(void)snprintf(ota_target_prefix_cfg, sizeof(ota_target_prefix_cfg), "BS");
				ota_target_uuid_cfg[0] = '\0';
				master_set_runtime_target_kind(MASTER_TARGET_ANCHOR);
				master_set_runtime_target_token(-1);
				master_set_runtime_target_name("");
				master_set_runtime_target_prefix("BS");
				master_set_runtime_target_uuid("");
				printk("device kind set: anchor (OTA target defaults reset)\n");
				if (control_mode == CONTROL_MODE_RECV) {
					master_set_connect_and_start_mode();
					control_wait_for_peer_clear(3000);
					master_restart_discovery();
				}
				system_target_print();
				return;
			}
			if (strcmp(arg2, "tag") == 0) {
				system_target_set_kind(SYS_DEV_TAG);
				master_clear_one_shot_command();
				master_disconnect_all_peers();
				(void)master_ota_target_set_token(-1);
				(void)master_ota_target_set_name("");
				(void)master_ota_target_set_prefix("BS");
				(void)master_ota_target_set_uuid("");
				ota_target_token_cfg = -1;
				ota_target_name_cfg[0] = '\0';
				(void)snprintf(ota_target_prefix_cfg, sizeof(ota_target_prefix_cfg), "BS");
				ota_target_uuid_cfg[0] = '\0';
				master_set_runtime_target_kind(MASTER_TARGET_TAG);
				master_set_runtime_target_token(-1);
				master_set_runtime_target_name("");
				master_set_runtime_target_prefix("BS");
				master_set_runtime_target_uuid("");
				printk("device kind set: tag\n");
				if (control_mode == CONTROL_MODE_RECV) {
					master_set_connect_and_start_mode();
					control_wait_for_peer_clear(3000);
					master_restart_discovery();
				}
				system_target_print();
				return;
			}
			printk("device kind invalid: %s\n", arg2);
			return;
		}

		printk("Unknown device command: %s\n", line);
		control_print_help();
		return;
	}

	printk("Unknown command: %s\n", line);
	control_print_help();
}

static void control_uart_cmd_work_handler(struct k_work *work)
{
	char line[sizeof(uart_pending_line)];

	ARG_UNUSED(work);

	if (!atomic_cas(&uart_line_ready, 1, 0)) {
		return;
	}

	memcpy(line, uart_pending_line, sizeof(line));
	line[sizeof(line) - 1U] = '\0';
	control_handle_uart_command(line);
}

static void control_uart_irq_handler(const struct device *dev, void *user_data)
{
	ARG_UNUSED(user_data);

	if (!uart_irq_update(dev)) {
		return;
	}

	while (uart_irq_rx_ready(dev)) {
		uint8_t ch;
		int rc = uart_fifo_read(dev, &ch, 1);

		if (rc <= 0) {
			break;
		}

		if (ch == '\r' || ch == '\n') {
			if (uart_pending_len > 0U) {
				unsigned int key = irq_lock();

				uart_pending_line[uart_pending_len] = '\0';
				atomic_set(&uart_line_ready, 1);
				uart_pending_len = 0U;
				irq_unlock(key);
				k_work_submit(&uart_cmd_work);
			}
			continue;
		}

		if (uart_pending_len + 1U < sizeof(uart_pending_line)) {
			uart_pending_line[uart_pending_len++] = (char)ch;
			continue;
		}

		uart_pending_len = 0U;
		printk("UART command too long\n");
	}
}

static int control_init_ui(void)
{
	int err;

	err = dk_leds_init();
	if (err) {
		printk("LED init failed: %d\n", err);
	} else {
		leds_ready = true;
		control_leds_set(DK_LED1_MSK);
		printk("LED map: 0=scan 1=link 2=ota 3=error\n");
	}

	err = dk_buttons_init(button_handler);
	if (err) {
		printk("Button init failed: %d\n", err);
		return err;
	}

	return 0;
}

static int control_load_mode(void)
{
	if (control_boot_cookie == CONTROL_BOOT_COOKIE_MAGIC) {
		control_mode = control_boot_mode;
		control_boot_cookie = 0U;
	} else if (control_mode != CONTROL_MODE_RECV &&
		   control_mode != CONTROL_MODE_OTA &&
		   control_mode != CONTROL_MODE_AUTOPOS) {
		control_mode = CONTROL_MODE_RECV;
	}
	sync_master_log_mode(control_mode);

	printk("Control mode loaded: %s\n", control_mode_name(control_mode));
	return 0;
}

int main(void)
{
	int err;

	k_work_init(&mode_switch_work, mode_switch_work_handler);
	k_work_init(&uart_cmd_work, control_uart_cmd_work_handler);
	k_work_init(&autopos_apply_work, autopos_apply_work_handler);
	k_work_init(&anchor_role_work, anchor_role_work_handler);
	k_work_queue_init(&autopos_work_q);
	k_work_queue_start(&autopos_work_q,
			   autopos_work_q_stack,
			   K_THREAD_STACK_SIZEOF(autopos_work_q_stack),
			   K_PRIO_PREEMPT(8),
			   NULL);

	err = control_init_ui();
	if (err) {
		return err;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		err = settings_load();
		if (err) {
			printk("Control settings load failed: %d\n", err);
		}
	}

	err = control_load_mode();
	if (err) {
		return err;
	}

#if defined(CONFIG_USB_DEVICE_STACK)
	err = usb_enable(NULL);
	if (err) {
		printk("USB CDC enable failed: %d\n", err);
	}
#endif

	if (!device_is_ready(console_uart)) {
		printk("Console UART not ready\n");
		return -ENODEV;
	}

	err = uart_irq_callback_user_data_set(console_uart, control_uart_irq_handler, NULL);
	if (err) {
		printk("UART RX callback setup failed: %d\n", err);
		return err;
	}

	uart_irq_rx_enable(console_uart);
	printk("UART control ready: type 'status' or 'mode recv'/'mode ota'/'mode autopos'\n");
	master_ota_target_reset();
	ota_target_token_cfg = -1;
	ota_target_name_cfg[0] = '\0';
	(void)snprintf(ota_target_prefix_cfg, sizeof(ota_target_prefix_cfg), "BS");
	ota_target_uuid_cfg[0] = '\0';
	if (ota_target_boot_cookie == OTA_TARGET_BOOT_COOKIE_MAGIC) {
		ota_target_token_cfg = ota_target_boot_token;
		(void)snprintf(ota_target_name_cfg, sizeof(ota_target_name_cfg), "%s",
			       ota_target_boot_name);
		(void)snprintf(ota_target_prefix_cfg, sizeof(ota_target_prefix_cfg), "%s",
			       ota_target_boot_prefix);
		(void)snprintf(ota_target_uuid_cfg, sizeof(ota_target_uuid_cfg), "%s",
			       ota_target_boot_uuid);
		printk("OTA target restored: token=%d name=%s prefix=%s uuid=%s\n",
		       ota_target_token_cfg,
		       ota_target_name_cfg[0] != '\0' ? ota_target_name_cfg : "-",
		       ota_target_prefix_cfg[0] != '\0' ? ota_target_prefix_cfg : "-",
		       ota_target_uuid_cfg[0] != '\0' ? ota_target_uuid_cfg : "-");
		ota_target_boot_cookie = 0U;
	}
	if (ota_nus_boot_cookie == OTA_NUS_BOOT_COOKIE_MAGIC) {
		ota_expect_nus_cfg = (ota_expect_nus_boot != 0U);
		ota_nus_boot_cookie = 0U;
	}
	(void)master_ota_target_set_token(ota_target_token_cfg);
	(void)master_ota_target_set_name(ota_target_name_cfg);
	(void)master_ota_target_set_prefix(ota_target_prefix_cfg);
	(void)master_ota_target_set_uuid(ota_target_uuid_cfg);
	master_set_runtime_target_token(ota_target_token_cfg);
	master_set_runtime_target_name(ota_target_name_cfg);
	master_set_runtime_target_prefix(ota_target_prefix_cfg);
	master_set_runtime_target_uuid(ota_target_uuid_cfg);
	master_ota_set_expect_nus(ota_expect_nus_cfg);
	system_target.kind = SYS_DEV_UNKNOWN;
	system_target.caps = system_caps_for_kind(SYS_DEV_UNKNOWN);
	master_set_runtime_target_kind(MASTER_TARGET_UNKNOWN);
	master_ota_target_print();
	if (autopos_state[0] == '\0') {
		(void)snprintf(autopos_state, sizeof(autopos_state), "idle");
	}

	control_leds_set(DK_NO_LEDS_MSK);
	printk("BioSpur BLE master control ready on nRF52840 DK\n");
	printk("BTN1 toggles RECV/OTA, BTN2 forces OTA mode, BTN3=SCAN, BTN4=CONN&START\n");
	control_print_help();

	if (control_mode == CONTROL_MODE_OTA) {
		ota_transition_active = false;
		master_set_background_gate(false, "boot_ota_mode");
		printk("Launching OTA mode\n");
		return master_ota_run();
	}

	ota_transition_active = false;
	if (control_mode == CONTROL_MODE_AUTOPOS) {
		printk("Launching AUTOPOS mode (internal receive active)\n");
	} else {
		printk("Launching receiver mode\n");
	}
	return master_app_run();
}
