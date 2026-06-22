#include <errno.h>
#include <ctype.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
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
#include "../../master_ota/generated/anchor_ota_manifest.h"
#include "../../master_ota/generated/tag_ota_manifest.h"

#ifndef APP_MASTER_OTA_TARGET_NAME
#define APP_MASTER_OTA_TARGET_NAME ""
#endif

#ifndef APP_MASTER_OTA_TARGET_NAME_PREFIX
#define APP_MASTER_OTA_TARGET_NAME_PREFIX "BS"
#endif

#ifndef APP_MASTER_OTA_TARGET_TOKEN_ID
#define APP_MASTER_OTA_TARGET_TOKEN_ID -1
#endif

#ifndef APP_MASTER_BOOT_PROFILE
#define APP_MASTER_BOOT_PROFILE "neutral"
#endif

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
#define AUTOPOS_ANCHOR_REBOOT_SETTLE_MS 5000
#define AUTOPOS_SWEEP_CONVERGE_TIMEOUT_MS 4000
#define AUTOPOS_SWEEP_CONVERGE_MIN_QUALITY 90
#define AUTOPOS_SWEEP_CONVERGE_CONSECUTIVE 1
#define AUTOPOS_SWEEP_ATTACH_HANDOFF_MS 100
#define AUTOPOS_DEMOTED_MASTER_SETTLE_MS 500
#define AUTOPOS_RESULT_HISTORY_DEPTH 48
static const char autopos_labels[AUTOPOS_ANCHOR_COUNT] = {'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'};
static char autopos_uuid_map[AUTOPOS_ANCHOR_COUNT][AUTOPOS_UUID_LEN];
static int8_t autopos_target_idx = -1;
static uint32_t autopos_round_sets;
static char autopos_result_history[AUTOPOS_RESULT_HISTORY_DEPTH][256];
static uint8_t autopos_result_history_head;
static uint8_t autopos_result_history_count;

static int autopos_wait_anchor_ready(const char *uuid, int timeout_ms);
static int autopos_wait_anchor_cleared(int timeout_ms);
static void autopos_save_map_entry(int idx);
static void control_disconnect_all_links(void);
static bool control_wait_for_peer_clear(int timeout_ms);
static int autopos_send_anchor_cmd_checked(const char *cmd, const char *expect_result,
					   int result_timeout_ms);
static int autopos_reconnect_anchor_ready(int idx, int timeout_ms);
static int autopos_commit_anchor_checked(int idx, const char *expect_role);
static int autopos_enter_config_window(int idx);
static void anchor_role_work_handler(struct k_work *work);
static int8_t autopos_last_success_idx = -1;
static char autopos_state[16] = "idle";
static char autopos_last_error[96];
static char autopos_cir_mode[16] = "0";
static char anchor_role_target[40];
static char anchor_role_value[16];
static char anchor_role_cir_value[16] = "0";
static bool anchor_role_all_targets;

static int normalize_anchor_cir_mode(const char *raw, char *out, size_t out_len)
{
	if (raw == NULL || out == NULL || out_len == 0U) {
		return -EINVAL;
	}
	if (strcmp(raw, "0") == 0 || strcmp(raw, "off") == 0 ||
	    strcmp(raw, "none") == 0) {
		(void)snprintf(out, out_len, "0");
		return 0;
	}
	if (strcmp(raw, "compact") == 0 || strcmp(raw, "feature") == 0 ||
	    strcmp(raw, "1") == 0) {
		(void)snprintf(out, out_len, "COMPACT");
		return 0;
	}
	if (strcmp(raw, "full") == 0 || strcmp(raw, "raw") == 0 ||
	    strcmp(raw, "2") == 0) {
		(void)snprintf(out, out_len, "FULL");
		return 0;
	}
	return -EINVAL;
}

static void autopos_result_history_clear(void)
{
	autopos_result_history_head = 0U;
	autopos_result_history_count = 0U;
	for (size_t i = 0; i < AUTOPOS_RESULT_HISTORY_DEPTH; ++i) {
		autopos_result_history[i][0] = '\0';
	}
}

static void autopos_result_history_push(const char *result)
{
	if (result == NULL || result[0] == '\0') {
		return;
	}

	(void)snprintf(autopos_result_history[autopos_result_history_head],
		       sizeof(autopos_result_history[autopos_result_history_head]),
		       "%s",
		       result);
	autopos_result_history_head =
		(uint8_t)((autopos_result_history_head + 1U) % AUTOPOS_RESULT_HISTORY_DEPTH);
	if (autopos_result_history_count < AUTOPOS_RESULT_HISTORY_DEPTH) {
		autopos_result_history_count++;
	}
}

static void autopos_result_history_dump(void)
{
	uint8_t count = autopos_result_history_count;
	uint8_t start = 0U;

	if (count == 0U) {
		printk("AUTOPOS result history empty\n");
		return;
	}

	if (count == AUTOPOS_RESULT_HISTORY_DEPTH) {
		start = autopos_result_history_head;
	}

	for (uint8_t i = 0U; i < count; ++i) {
		uint8_t idx = (uint8_t)((start + i) % AUTOPOS_RESULT_HISTORY_DEPTH);
		if (autopos_result_history[idx][0] == '\0') {
			continue;
		}
		printk("AUTOPOS history[%u/%u]: %s\n",
		       (unsigned int)(i + 1U),
		       (unsigned int)count,
		       autopos_result_history[idx]);
	}
}

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

static bool control_build_anchor_only(void)
{
	return strcmp(APP_MASTER_BOOT_PROFILE, "anchor") == 0;
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

static void control_apply_mode_leds(void)
{
	switch (control_mode) {
	case CONTROL_MODE_AUTOPOS:
		control_leds_set(DK_LED1_MSK);
		break;
	case CONTROL_MODE_OTA:
		control_leds_set(DK_LED3_MSK);
		break;
	case CONTROL_MODE_RECV:
	default:
		control_leds_set(DK_LED2_MSK);
		break;
	}
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
	printk("OTA runtime cmds: ota_reset | ota show | ota version\n");
	printk("Runtime NUS cmds: cmd <raw> | cmd_all <raw> | oneshot <raw> | oneshot show | oneshot clear\n");
	printk("Tag layout cmds: APOS <id> <x_mm> <y_mm> <z_mm> | APOS_TO <BSxxxx> APOS... | APOS_COMMIT | APOS_STATUS | APOS_RESET\n");
	printk("Tag CIR cmds: tag cir <off|compact|full|status> | tag cir all <off|compact|full|status>\n");
	printk("TDMA cmds: tdma show | tdma hold <0|1> | tdma roster <BSxxxx> <static|roto|motion> | tdma profile <BSxxxx> <static|roto|motion> | tdma freq <static|roto|motion> <hz> | tdma rebalance\n");
	printk("Device model cmds: device show | device kind <anchor|tag>\n");
	printk("OTA target cmds: ota_target show | ota_target token <id|-1> | ota_target name <BSxxxx|-> | ota_target prefix <BS|-> | ota_target uuid <32hex|->\n");
	printk("Anchor cmds: anchor version <A..H|UUID32|all> | anchor role <A..H|UUID32|all> <master|matrix|responder> [cir <0|compact|full>] | anchor reset <A..H|UUID32|all> <autopos|responder>\n");
	printk("AUTOPOS cmds: autopos status | autopos detach | autopos cir <0|compact|full> | autopos map <A..H> <UUID32> | autopos map show | autopos round <A..H> [sets] | autopos apply | autopos result show|clear\n");
}

static void control_print_ota_bundle_info(void)
{
	if (strcmp(APP_MASTER_OTA_ANCHOR_FW_MARKER, "-") != 0) {
		printk("OTA_BUNDLE kind=anchor fw=%s build_dir=%s dfu_zip=%s\n",
		       APP_MASTER_OTA_ANCHOR_FW_MARKER,
		       APP_MASTER_OTA_ANCHOR_BUILD_DIR,
		       APP_MASTER_OTA_ANCHOR_DFU_ZIP);
	}
	if (strcmp(APP_MASTER_OTA_TAG_FW_MARKER, "-") != 0) {
		printk("OTA_BUNDLE kind=tag fw=%s build_dir=%s dfu_zip=%s\n",
		       APP_MASTER_OTA_TAG_FW_MARKER,
		       APP_MASTER_OTA_TAG_BUILD_DIR,
		       APP_MASTER_OTA_TAG_DFU_ZIP);
	}
	master_ota_target_print();
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
	if (control_build_anchor_only() && kind == SYS_DEV_TAG) {
		printk("Anchor-only build: refusing tag system target; keeping anchor target\n");
		kind = SYS_DEV_ANCHOR;
	}

	system_target.kind = kind;
	system_target.caps = system_caps_for_kind(kind);
	/* Anchor OTA path should skip NUS arm stage and go directly DFU/SMP. */
	ota_expect_nus_cfg = (kind != SYS_DEV_ANCHOR);
	master_ota_set_expect_nus(ota_expect_nus_cfg);
}

static void control_force_anchor_runtime_target(bool wildcard_scan)
{
	system_target_set_kind(SYS_DEV_ANCHOR);
	master_set_runtime_target_kind(MASTER_TARGET_ANCHOR);
	master_set_anchor_wildcard_scan(wildcard_scan);
	master_set_runtime_target_token(-1);
	master_set_runtime_target_name("");
	master_set_runtime_target_prefix("");
	master_set_runtime_target_uuid("");
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

static bool boot_profile_is(const char *profile)
{
	return strcmp(APP_MASTER_BOOT_PROFILE, profile) == 0;
}

static void control_apply_boot_profile(void)
{
	if (control_mode == CONTROL_MODE_OTA) {
		printk("Boot profile %s skipped: OTA mode active\n", APP_MASTER_BOOT_PROFILE);
		return;
	}

	if (boot_profile_is("anchor")) {
		control_mode = CONTROL_MODE_AUTOPOS;
		sync_master_log_mode(control_mode);
		control_force_anchor_runtime_target(true);
		printk("Boot profile anchor: auto-connect ANCHOR-* control links; no tag scan\n");
		return;
	}

	if (boot_profile_is("tag")) {
		control_mode = CONTROL_MODE_RECV;
		sync_master_log_mode(control_mode);
		system_target_set_kind(SYS_DEV_TAG);
		master_set_runtime_target_kind(MASTER_TARGET_TAG);
		master_set_anchor_wildcard_scan(false);
		master_set_runtime_target_token(-1);
		master_set_runtime_target_name("");
		master_set_runtime_target_prefix("BS");
		master_set_runtime_target_uuid("");
		printk("Boot profile tag: auto-connect BS* tag links; TDMA remains host-controlled\n");
		return;
	}

	printk("Boot profile neutral: no role-specific auto target\n");
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

static void autopos_reset_runtime_state(bool keep_target)
{
	(void)snprintf(autopos_state, sizeof(autopos_state), "idle");
	autopos_last_error[0] = '\0';
	autopos_last_success_idx = -1;
	autopos_round_sets = 0U;
	if (!keep_target) {
		autopos_target_idx = -1;
	}
}

static void autopos_print_status(void)
{
	printk("AUTOPOS: mode=%s state=%s staged=%c last_success=%c sets=%lu cir=%s error=%s\n",
	       control_mode_name(control_mode),
	       autopos_state,
	       (autopos_target_idx >= 0) ? autopos_labels[autopos_target_idx] : '-',
	       (autopos_last_success_idx >= 0) ? autopos_labels[autopos_last_success_idx] : '-',
	       (unsigned long)autopos_round_sets,
	       autopos_cir_mode,
	       autopos_last_error[0] != '\0' ? autopos_last_error : "-");
}

static int autopos_refresh_uuid_map_from_anchor_scan(int timeout_ms)
{
	char labels[AUTOPOS_ANCHOR_COUNT];
	char uuids[AUTOPOS_ANCHOR_COUNT][AUTOPOS_UUID_LEN];
	int found = 0;
	int waited = 0;
	int first_new = 0;

	master_set_runtime_target_kind(MASTER_TARGET_ANCHOR);
	master_set_anchor_wildcard_scan(true);
	master_set_runtime_target_name("");
	master_set_runtime_target_prefix("");
	master_set_runtime_target_uuid("");
	master_set_scan_only_mode();
	control_disconnect_all_links();
	control_wait_for_peer_clear(5000);
	master_restart_discovery();

	while (waited <= timeout_ms) {
		int snapshot = master_anchor_snapshot_uuid_map(labels, ARRAY_SIZE(labels),
							       uuids, ARRAY_SIZE(uuids));
		if (snapshot > 0) {
			found = snapshot;
			for (int i = 0; i < snapshot; ++i) {
				int idx = autopos_label_to_index(labels[i]);

				if (idx < 0 || uuids[i][0] == '\0') {
					continue;
				}
				if (strcmp(autopos_uuid_map[idx], uuids[i]) == 0) {
					continue;
				}

				snprintk(autopos_uuid_map[idx], sizeof(autopos_uuid_map[idx]), "%s",
					 uuids[i]);
				autopos_save_map_entry(idx);
				if (first_new == 0) {
					first_new = 1;
				}
				printk("AUTOPOS map refresh: %c=%s\n", labels[i], autopos_uuid_map[idx]);
			}
		}

		bool complete = true;
		for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
			if (autopos_uuid_map[i][0] == '\0') {
				complete = false;
				break;
			}
		}
		if (complete) {
			master_quiesce_peers();
			return AUTOPOS_ANCHOR_COUNT;
		}
		if (first_new && found >= 4 && waited >= 2000) {
			break;
		}

		k_sleep(K_MSEC(200));
		waited += 200;
	}

	master_quiesce_peers();
	return found;
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

static bool control_wait_for_peer_clear(int timeout_ms)
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
			return true;
		}

		k_sleep(K_MSEC(100));
		waited += 100;
	}

	printk("Peer clear wait timeout: conn_count=%d ready_count=%d\n",
	       master_connection_count(), master_anchor_ctrl_ready_count());
	return false;
}

static void control_prepare_clean_recv_session(void)
{
	autopos_reset_runtime_state(false);
	master_clear_one_shot_command();
	master_set_anchor_wildcard_scan(false);

	/* Stop scan/auto-connect before disconnecting.  If scan remains active
	 * while we wait for links to clear, new advertisements can enqueue fresh
	 * connections and leave the central stuck with conn_count > 0 but no
	 * ready NUS links.
	 */
	master_quiesce_peers();
	if (!control_wait_for_peer_clear(3000)) {
		printk("RECV clean slate requires reboot: stale BLE links did not clear\n");
		control_mode = CONTROL_MODE_RECV;
		sync_master_log_mode(control_mode);
		control_save_mode();
		k_sleep(K_MSEC(100));
		sys_reboot(SYS_REBOOT_COLD);
		return;
	}
	master_set_connect_and_start_mode();
	master_restart_discovery();
	printk("RECV clean slate prepared\n");
}

static int autopos_wait_anchor_ready(const char *uuid, int timeout_ms)
{
	int waited = 0;
	int last_count = -1;
	int warm_reuse_window_ms = MIN(timeout_ms, 3000);

	/* For anchor sweep, match readiness by UUID only.
	 * A stale runtime name/prefix filter (e.g. BSF66F from tag session)
	 * can otherwise make ready_count stay 0 even when anchor-ctrl is ready.
	 */
	master_set_runtime_target_name("");
	master_set_runtime_target_prefix("");
	master_set_runtime_target_kind(MASTER_TARGET_ANCHOR);
	master_set_runtime_target_uuid(uuid);
	master_set_anchor_wildcard_scan(false);
	master_set_connect_and_start_mode();

	/* Reuse an in-flight or ready control link when it already targets the
	 * requested anchor. Tearing it down here is what creates the
	 * connect->setup->disconnect churn that later shows up as -12/-116.
	 */
	if (master_anchor_ctrl_ready_count() > 0) {
		printk("AUTOPOS wait anchor ready: uuid=%s reuse existing ready control link (discovery remains active)\n",
		       uuid);
		return 0;
	}
	if (master_anchor_ctrl_target_peer_count() > 0) {
		printk("AUTOPOS wait anchor ready: uuid=%s target peer already present; waiting for setup/discovery\n",
		       uuid);
		while (waited < warm_reuse_window_ms) {
			master_process_connect_pending();
			master_process_setup_pending();
			int ready_count = master_anchor_ctrl_ready_count();
			if (ready_count != last_count || (waited % 1000) == 0) {
				printk("AUTOPOS wait anchor ready(reuse): waited=%d ready_count=%d target_peers=%d\n",
				       waited, ready_count, master_anchor_ctrl_target_peer_count());
				master_dump_ready_state();
				last_count = ready_count;
			}
			if (ready_count > 0) {
				printk("AUTOPOS wait anchor ready: uuid=%s reused target control link after setup\n",
				       uuid);
				return 0;
			}
			if (master_anchor_ctrl_target_peer_count() == 0) {
				break;
			}
			k_sleep(K_MSEC(200));
			waited += 200;
		}
	}

	master_set_connect_and_start_mode();
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
			printk("AUTOPOS wait anchor ready: uuid=%s control link ready while discovery stays active\n",
			       uuid);
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

static int autopos_wait_active_role_state(const char *expect_role, int timeout_ms)
{
	char state[256];
	int waited = 0;
	int rc;
	char role_needle[32];

	(void)snprintf(role_needle, sizeof(role_needle), "role=%s", expect_role);
	while (waited < timeout_ms) {
		rc = master_anchor_ctrl_read_state(state, sizeof(state));
		if (rc == 0) {
			printk("AUTOPOS state: %s\n", state);
			if (strstr(state, role_needle) != NULL &&
			    strstr(state, "cfg_valid=1") != NULL &&
			    strstr(state, "reboot_required=0") != NULL) {
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
			autopos_result_history_push(result);
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

static int autopos_wait_runtime_master_started(int idx, int timeout_ms)
{
	char result[256];
	char state[256];
	int waited = 0;
	int result_log_period_ms = 300;
	char sw_prefix[8];
	char role_needle[32];

	if (idx < 0 || idx >= AUTOPOS_ANCHOR_COUNT) {
		return -EINVAL;
	}

	(void)snprintf(sw_prefix, sizeof(sw_prefix), "SW-%c,", autopos_labels[idx]);
	(void)snprintf(role_needle, sizeof(role_needle), "role=master");

	while (waited < timeout_ms) {
		int rc_result = master_anchor_ctrl_read_result(result, sizeof(result));
		if (rc_result == 0) {
			autopos_result_history_push(result);
			if ((waited % result_log_period_ms) == 0 || strstr(result, "OK RUNTIME") != NULL ||
			    strstr(result, sw_prefix) != NULL) {
				printk("AUTOPOS result: %s\n", result);
			}
			if (strstr(result, "OK RUNTIME") != NULL) {
				return 0;
			}
			if (strstr(result, sw_prefix) != NULL) {
				printk("AUTOPOS runtime accept via sweep stream: master=%c\n",
				       autopos_labels[idx]);
				return 0;
			}
		}

		if ((waited % 300) == 0) {
			int rc_state = master_anchor_ctrl_read_state(state, sizeof(state));
			if (rc_state == 0) {
				printk("AUTOPOS state: %s\n", state);
				if (strstr(state, role_needle) != NULL &&
				    strstr(state, "cfg_valid=1") != NULL &&
				    strstr(state, "reboot_required=0") != NULL) {
					printk("AUTOPOS runtime accept via role state: master=%c\n",
					       autopos_labels[idx]);
					return 0;
				}
			}
		}

		k_sleep(K_MSEC(150));
		waited += 150;
	}

	return -ETIMEDOUT;
}

static bool autopos_sweep_line_converged(const char *line, char master_label,
					 uint8_t min_quality,
					 uint8_t *peer_count_out,
					 uint8_t *min_quality_out)
{
	const char *pos;
	bool saw_peer = false;
	uint8_t peer_count = 0U;
	uint8_t min_seen = 100U;

	if (peer_count_out != NULL) {
		*peer_count_out = 0U;
	}
	if (min_quality_out != NULL) {
		*min_quality_out = 0U;
	}

	if (line == NULL || strncmp(line, "SW-", 3) != 0 || line[3] != master_label ||
	    line[4] != ',') {
		return false;
	}

	pos = line + 5;
	while (*pos != '\0') {
		unsigned long distance_mm;
		unsigned long quality;
		char *end = NULL;

		if (!isalpha((unsigned char)pos[0]) || pos[1] != ',') {
			return false;
		}
		pos += 2;

		distance_mm = strtoul(pos, &end, 10);
		if (end == pos || *end != ',') {
			return false;
		}
		pos = end + 1;

		quality = strtoul(pos, &end, 10);
		if (end == pos) {
			return false;
		}

		/* Sweep attach should wait for a structurally complete SW line,
		 * not for a quality threshold. Quality is reported for analysis
		 * and should not block the control-plane flow.
		 */
		if (distance_mm == 0U) {
			return false;
		}

		if (quality < min_seen) {
			min_seen = (uint8_t)quality;
		}

		saw_peer = true;
		peer_count++;

		if (*end == '\0') {
			pos = end;
			break;
		}
		if (*end != ',') {
			return false;
		}
		pos = end + 1;
	}

	if (!saw_peer) {
		return false;
	}

	if (peer_count_out != NULL) {
		*peer_count_out = peer_count;
	}
	if (min_quality_out != NULL) {
		*min_quality_out = min_seen;
	}
	return true;
}

static int autopos_wait_sweep_converged(int idx, int timeout_ms)
{
	char result[256];
	char previous_result[256] = { 0 };
	int waited = 0;
	int stable_count = 0;
	int rc;

	while (waited < timeout_ms) {
		rc = master_anchor_ctrl_read_result(result, sizeof(result));
		if (rc == 0) {
			autopos_result_history_push(result);
			if (strcmp(result, previous_result) != 0) {
				uint8_t peer_count = 0U;
				uint8_t min_quality = 0U;

				printk("AUTOPOS result: %s\n", result);
				(void)snprintf(previous_result, sizeof(previous_result), "%s", result);

				if (autopos_sweep_line_converged(result, autopos_labels[idx],
							       AUTOPOS_SWEEP_CONVERGE_MIN_QUALITY,
							       &peer_count, &min_quality)) {
					stable_count++;
					printk("AUTOPOS sweep converge: master=%c stable=%d/%d peers=%u min_q=%u\n",
					       autopos_labels[idx],
					       stable_count,
					       AUTOPOS_SWEEP_CONVERGE_CONSECUTIVE,
					       (unsigned int)peer_count,
					       (unsigned int)min_quality);
					if (stable_count >= AUTOPOS_SWEEP_CONVERGE_CONSECUTIVE) {
						return 0;
					}
				} else if (strncmp(result, "SW-", 3) == 0 &&
					   result[3] == autopos_labels[idx]) {
					stable_count = 0;
				}
			}
		}
		k_sleep(K_MSEC(200));
		waited += 200;
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

static int autopos_current_role_matches(const char *expect_role)
{
	char state[256];
	char role[24] = { 0 };
	char cfg_valid[8] = { 0 };
	char busy[8] = { 0 };
	int rc;

	rc = master_anchor_ctrl_read_state(state, sizeof(state));
	if (rc != 0) {
		return rc;
	}

	printk("AUTOPOS state: %s\n", state);
	if (anchor_state_field_value(state, "role=", role, sizeof(role)) == NULL ||
	    strcmp(role, expect_role) != 0) {
		return 0;
	}

	if (anchor_state_field_value(state, "cfg_valid=", cfg_valid, sizeof(cfg_valid)) != NULL &&
	    strcmp(cfg_valid, "1") != 0) {
		return 0;
	}

	if (anchor_state_field_value(state, "busy=", busy, sizeof(busy)) == NULL ||
	    strcmp(busy, "1") != 0) {
		return 0;
	}

	return 1;
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
	char result[256];
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

	rc = master_anchor_ctrl_send_command_wait_result("VERSION", result, sizeof(result), 3000);
	if (rc != 0) {
		printk("anchor version result notify failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}

	(void)anchor_state_field_value(result, "fw=", fw, sizeof(fw));
	(void)anchor_state_field_value(result, "label=", state_label, sizeof(state_label));
	(void)anchor_state_field_value(result, "role=", role, sizeof(role));
	printk("ANCHOR_VERSION query=%s uuid=%s fw=%s label=%s role=%s\n",
	       label[0] != '\0' ? label : query,
	       uuid,
	       fw[0] != '\0' ? fw : "-",
	       state_label[0] != '\0' ? state_label : "-",
	       role[0] != '\0' ? role : "-");
	printk("ANCHOR_VERSION result=%s\n", result);
	return 0;
}

static int anchor_query_version_all(void)
{
	int rc = 0;
	int first_err = 0;
	char label[2];
	bool missing_map = false;

	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		if (autopos_uuid_map[i][0] == '\0') {
			missing_map = true;
			break;
		}
	}

	if (missing_map) {
		char labels[AUTOPOS_ANCHOR_COUNT];
		char uuids[AUTOPOS_ANCHOR_COUNT][AUTOPOS_UUID_LEN];
		int snapshot = master_anchor_snapshot_uuid_map(labels, ARRAY_SIZE(labels),
							       uuids, ARRAY_SIZE(uuids));

		for (int i = 0; i < snapshot; ++i) {
			int idx = autopos_label_to_index(labels[i]);

			if (idx < 0 || uuids[i][0] == '\0') {
				continue;
			}
			snprintk(autopos_uuid_map[idx], sizeof(autopos_uuid_map[idx]), "%s",
				 uuids[i]);
		}

		missing_map = false;
		for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
			if (autopos_uuid_map[i][0] == '\0') {
				missing_map = true;
				break;
			}
		}

		printk("ANCHOR_VERSION map snapshot count=%d complete=%u\n",
		       snapshot, missing_map ? 0U : 1U);
		if (missing_map) {
			int refresh_count = autopos_refresh_uuid_map_from_anchor_scan(8000);

			printk("ANCHOR_VERSION map refresh count=%d\n", refresh_count);
		}
	}

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
	rc = autopos_commit_anchor_checked(label[0] != '\0' ? autopos_label_to_index(label[0]) : -1,
					   expect_role);
	if (rc < 0) {
		printk("anchor role commit failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}
	if (rc == 0) {
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
	} else {
		printk("anchor role commit already active: target=%s role=%s; skip reboot\n",
		       label[0] != '\0' ? label : "-", expect_role);
	}

	rc = autopos_wait_role_state(expect_role, 5000);
	printk("anchor role rc=%d target=%s uuid=%s role=%s\n",
	       rc,
	       label[0] != '\0' ? label : query,
	       uuid,
	       expect_role);
	return rc;
}

static int anchor_apply_runtime_role_uuid(const char *query, const char *role_cmd,
					  const char *expect_role)
{
	char uuid[33];
	char label[4];
	int rc;

	rc = anchor_resolve_query_uuid(query, uuid, sizeof(uuid), label, sizeof(label));
	if (rc != 0) {
		printk("anchor runtime role invalid target=%s rc=%d\n",
		       (query != NULL) ? query : "-", rc);
		return rc;
	}

	rc = autopos_wait_anchor_ready(uuid, 12000);
	if (rc != 0) {
		printk("anchor runtime role connect failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}

	rc = autopos_current_role_matches(expect_role);
	if (rc > 0) {
		printk("anchor runtime role already active: target=%s uuid=%s role=%s\n",
		       label[0] != '\0' ? label : query, uuid, expect_role);
		return 0;
	}
	if (rc < 0) {
		printk("anchor runtime role state precheck failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : query, uuid, rc);
	}

	rc = autopos_send_anchor_cmd_checked(role_cmd, "OK RUNTIME", 1500);
	if (rc != 0) {
		int state_rc = autopos_current_role_matches(expect_role);

		if (state_rc > 0) {
			printk("anchor runtime role accepted after missed ACK: target=%s uuid=%s role=%s\n",
			       label[0] != '\0' ? label : query, uuid, expect_role);
			return 0;
		}
		printk("anchor runtime role command failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : query, uuid, rc);
		return rc;
	}

	rc = autopos_wait_active_role_state(expect_role, 5000);
	printk("anchor runtime role rc=%d target=%s uuid=%s role=%s\n",
	       rc,
	       label[0] != '\0' ? label : query,
	       uuid,
	       expect_role);
	return rc;
}

static int anchor_apply_role(const char *query, const char *role, const char *cir_mode)
{
	char role_cmd[48];
	char expect_role[16];
	const char *cir = (cir_mode != NULL && cir_mode[0] != '\0') ? cir_mode : "0";

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
		(void)snprintf(role_cmd, sizeof(role_cmd), "RUNTIME RESPONDER FORCE CIR=%s", cir);
		(void)snprintf(expect_role, sizeof(expect_role), "responder");
		return anchor_apply_runtime_role_uuid(query, role_cmd, expect_role);
	} else {
		return -EINVAL;
	}

	return anchor_apply_role_uuid(query, role_cmd, expect_role);
}

static int anchor_apply_role_all(const char *role, const char *cir_mode)
{
	int rc = 0;
	int first_err = 0;
	char label[2];
	const char *cir = (cir_mode != NULL && cir_mode[0] != '\0') ? cir_mode : "0";

	if (role != NULL && (strcmp(role, "matrix") == 0 || strcmp(role, "responder") == 0)) {
		int ready_count;
		int sent;
		int total_sent = 0;
		int waited = 0;
		int last_ready = -1;
		char runtime_cmd[48];

		(void)snprintf(runtime_cmd, sizeof(runtime_cmd), "RUNTIME %s FORCE CIR=%s",
			       (strcmp(role, "matrix") == 0) ? "MATRIX" : "RESPONDER",
			       cir);

		/* Session role guard/finalizer: matrix/responder are runtime state changes.
		 * Do not depend on persisted A-H maps or the config/commit path here;
		 * anchors may be busy in matrix/responder loops, and they are explicitly
		 * designed to accept RUNTIME role switches while busy.
		 */
		master_set_runtime_target_name("");
		master_set_runtime_target_prefix("");
		master_set_runtime_target_uuid("");
		master_set_runtime_target_kind(MASTER_TARGET_ANCHOR);
		master_set_connect_and_start_mode();

		while (waited < 45000) {
			ready_count = master_anchor_ctrl_ready_count();
			if (ready_count >= AUTOPOS_ANCHOR_COUNT) {
				break;
			}
			if (ready_count != last_ready || (waited % 3000) == 0) {
				printk("anchor role all %s wait ready: waited=%d ready=%d/%d\n",
				       role, waited, ready_count, AUTOPOS_ANCHOR_COUNT);
				master_dump_ready_state();
				last_ready = ready_count;
			}
			k_sleep(K_MSEC(250));
			waited += 250;
		}

		ready_count = master_anchor_ctrl_ready_count();
		if (ready_count < AUTOPOS_ANCHOR_COUNT) {
			printk("anchor role all %s aborted: ctrl ready gate failed ready=%d/%d\n",
			       role, ready_count, AUTOPOS_ANCHOR_COUNT);
			master_dump_ready_state();
			return -EAGAIN;
		}

		sent = master_send_command_now(runtime_cmd);
		if (sent > 0) {
			total_sent += sent;
		}
		printk("anchor role all %s runtime sent=%d ready=%d/%d\n",
		       role, sent, ready_count, AUTOPOS_ANCHOR_COUNT);
		if (sent < 0) {
			return sent;
		}
		if (ready_count <= 0 && sent == 0) {
			return sent < 0 ? sent : -ENOTCONN;
		}

		k_sleep(K_MSEC(700));
		/* Repeat to catch peers that became ready during the first send window. */
		sent = master_send_command_now(runtime_cmd);
		if (sent > 0) {
			total_sent += sent;
		}
		printk("anchor role all %s runtime repeat sent=%d ready=%d/%d\n",
		       role, sent, master_anchor_ctrl_ready_count(), AUTOPOS_ANCHOR_COUNT);
		k_sleep(K_MSEC(700));
		sent = master_send_command_now(runtime_cmd);
		if (sent > 0) {
			total_sent += sent;
		}
		printk("anchor role all %s runtime final sent=%d ready=%d/%d total_sent=%d\n",
		       role, sent, master_anchor_ctrl_ready_count(), AUTOPOS_ANCHOR_COUNT, total_sent);
		if (total_sent <= 0) {
			return -ENOTCONN;
		}
		return 0;
	}

	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		if (autopos_uuid_map[i][0] == '\0') {
			printk("anchor role query=%c skipped: uuid not mapped\n",
			       autopos_labels[i]);
			continue;
		}

		label[0] = autopos_labels[i];
		label[1] = '\0';
		rc = anchor_apply_role(label, role, cir);
		if (rc != 0 && first_err == 0) {
			first_err = rc;
		}
		k_sleep(K_MSEC(150));
	}

	return first_err;
}

static int anchor_reset_mode_uuid(const char *query, const char *reset_cmd,
				  const char *expect_role)
{
	char uuid[33];
	char label[4];
	int rc;

	rc = anchor_resolve_query_uuid(query, uuid, sizeof(uuid), label, sizeof(label));
	if (rc != 0) {
		printk("anchor reset invalid target=%s rc=%d\n",
		       (query != NULL) ? query : "-", rc);
		return rc;
	}

	rc = autopos_wait_anchor_ready(uuid, 12000);
	if (rc != 0) {
		printk("anchor reset connect failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}

	if (label[0] != '\0') {
		rc = autopos_enter_config_window(autopos_label_to_index(label[0]));
	} else {
		rc = autopos_wait_state_field("busy=0", 3000);
	}
	if (rc != 0) {
		printk("anchor reset config window failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}

	rc = autopos_send_anchor_cmd_checked(reset_cmd, NULL, 0);
	if (rc != 0) {
		printk("anchor reset command failed: target=%s uuid=%s rc=%d\n",
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
		printk("anchor reset reconnect failed: target=%s uuid=%s rc=%d\n",
		       label[0] != '\0' ? label : "-", uuid, rc);
		return rc;
	}

	rc = autopos_wait_role_state(expect_role, 5000);
	printk("anchor reset rc=%d target=%s uuid=%s mode=%s\n",
	       rc,
	       label[0] != '\0' ? label : query,
	       uuid,
	       expect_role);
	return rc;
}

static int anchor_reset_mode(const char *query, const char *mode)
{
	if (mode == NULL) {
		return -EINVAL;
	}

	if (strcmp(mode, "autopos") == 0) {
		return anchor_reset_mode_uuid(query, "RESET AUTOPOS", "matrix");
	}
	if (strcmp(mode, "responder") == 0) {
		return anchor_reset_mode_uuid(query, "RESET RESPONDER", "responder");
	}
	return -EINVAL;
}

static int anchor_reset_mode_all(const char *mode)
{
	int rc = 0;
	int first_err = 0;
	char label[2];

	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		if (autopos_uuid_map[i][0] == '\0') {
			printk("anchor reset query=%c skipped: uuid not mapped\n",
			       autopos_labels[i]);
			continue;
		}

		label[0] = autopos_labels[i];
		label[1] = '\0';
		rc = anchor_reset_mode(label, mode);
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

	/* Recover only the target control link. Global quiesce tears down
	 * already-healthy anchor-ctrl peers and turns a single stale target
	 * into a whole-session reconnect storm.
	 */
	master_disconnect_runtime_target_peers();
	k_sleep(K_MSEC(250));

	rc = autopos_wait_anchor_ready(autopos_uuid_map[idx], timeout_ms);
	if (rc != 0) {
		return rc;
	}

	return 0;
}

static int autopos_commit_anchor_checked(int idx, const char *expect_role)
{
	char state[256];
	char role_needle[24];
	int rc;

	rc = autopos_send_anchor_cmd_checked("COMMIT", "OK COMMIT REBOOT_REQUIRED", 1500);
	if (rc == 0) {
		return 0;
	}

	printk("AUTOPOS commit ack missing rc=%d; reconnecting to verify reboot_required\n", rc);
	if (idx >= 0 && idx < AUTOPOS_ANCHOR_COUNT) {
		int reconnect_rc = autopos_reconnect_anchor_ready(idx, 15000);

		if (reconnect_rc != 0) {
			printk("AUTOPOS commit verify reconnect failed idx=%d rc=%d\n", idx, reconnect_rc);
			return rc;
		}
	}

	if (master_anchor_ctrl_read_state(state, sizeof(state)) == 0) {
		printk("AUTOPOS commit verify state: %s\n", state);
		if (strstr(state, "reboot_required=1") != NULL) {
			printk("AUTOPOS commit accepted after missed ACK\n");
			return 0;
		}
		if (expect_role != NULL) {
			(void)snprintf(role_needle, sizeof(role_needle), "role=%s", expect_role);
			if (strstr(state, role_needle) != NULL &&
			    strstr(state, "cfg_valid=1") != NULL &&
			    strstr(state, "reboot_required=0") != NULL) {
				printk("AUTOPOS commit already active after missed ACK: role=%s\n",
				       expect_role);
				return 1;
			}
		}
	}

	return rc;
}

static void autopos_detach_sweep_listen(void)
{
	int rc;

	/* Drop the previous round's BLE result stream before reconfiguring roles. */
	master_set_background_gate(false, "autopos_detach");
	if (master_anchor_ctrl_ready_count() > 0 && autopos_round_sets == 0U) {
		rc = master_send_command_now("STOP");
		printk("AUTOPOS detach stop current stream rc=%d\n", rc);
		k_sleep(K_MSEC(200));
	} else if (master_anchor_ctrl_ready_count() > 0) {
		printk("AUTOPOS detach skip STOP: finite master auto-returns to matrix sets=%lu\n",
		       (unsigned long)autopos_round_sets);
	}
	master_set_background_gate(true, "autopos_detach_done");
}

static int autopos_enter_config_window(int idx)
{
	int rc;

	for (int attempt = 0; attempt < 3; ++attempt) {
		rc = autopos_wait_state_field("busy=0", attempt == 0 ? 1000 : 2000);
		if (rc == 0) {
			return 0;
		}

		printk("AUTOPOS anchor %c busy; requesting runtime stop attempt=%d/3\n",
		       autopos_labels[idx], attempt + 1);
		rc = autopos_send_anchor_cmd_checked("STOP", "OK STOP", 1200);
		if (rc == 0) {
			rc = autopos_wait_state_field("busy=0", 3000);
			if (rc == 0) {
				printk("AUTOPOS anchor %c stopped for config window\n",
				       autopos_labels[idx]);
				return 0;
			}
			printk("AUTOPOS anchor %c stop did not clear busy rc=%d; falling back to reboot\n",
			       autopos_labels[idx], rc);
		} else {
			printk("AUTOPOS anchor %c stop unsupported/failed rc=%d; falling back to reboot\n",
			       autopos_labels[idx], rc);
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
	char runtime_cmd_buf[40];
	const char *runtime_cmd = runtime_cmd_buf;
	const char *expect_role = is_master ? "master" : "matrix";
	bool demoting_active_master = false;
	bool allow_reconnect_retry = true;

	(void)snprintf(runtime_cmd_buf, sizeof(runtime_cmd_buf), "RUNTIME %s CIR=%s",
		       is_master ? "MASTER" : "MATRIX",
		       autopos_cir_mode);

retry_after_reconnect:
	demoting_active_master = false;

	rc = autopos_wait_anchor_ready(autopos_uuid_map[idx], 12000);
	if (rc != 0) {
		if (allow_reconnect_retry && rc == -ETIMEDOUT) {
			printk("AUTOPOS anchor %c ready timeout; forcing control-link reconnect retry\n",
			       autopos_labels[idx]);
			allow_reconnect_retry = false;
			rc = autopos_reconnect_anchor_ready(idx, 15000);
			if (rc != 0) {
				return rc;
			}
			goto retry_after_reconnect;
		}
		return rc;
	}

	rc = autopos_current_role_matches("master");
	if (rc < 0) {
		if (allow_reconnect_retry && (rc == -ETIMEDOUT || rc == -ENOTCONN)) {
			printk("AUTOPOS anchor %c state read failed rc=%d; forcing control-link reconnect retry\n",
			       autopos_labels[idx], rc);
			allow_reconnect_retry = false;
			rc = autopos_reconnect_anchor_ready(idx, 15000);
			if (rc != 0) {
				return rc;
			}
			goto retry_after_reconnect;
		}
		return rc;
	}
	demoting_active_master = !is_master && rc > 0;

	rc = autopos_current_role_matches(expect_role);
	if (rc > 0 && !(is_master && autopos_round_sets != 0U)) {
		printk("AUTOPOS anchor %c already active role=%s cfg_valid=1; keeping current config\n",
		       autopos_labels[idx], expect_role);
		return 0;
	}
	if (!is_master) {
		rc = autopos_current_role_matches("responder");
		if (rc > 0) {
			printk("AUTOPOS anchor %c active role=responder; switching to matrix for sweep\n",
			       autopos_labels[idx]);
		}
	}

	if (is_master && autopos_round_sets != 0U) {
		(void)snprintf(runtime_cmd_buf, sizeof(runtime_cmd_buf),
			       "RUNTIME MASTER SWEEP %lu CIR=%s",
			       (unsigned long)autopos_round_sets,
			       autopos_cir_mode);
		runtime_cmd = runtime_cmd_buf;
	}

	if (is_master && autopos_round_sets != 0U) {
		rc = master_send_command_now(runtime_cmd);
		if (rc >= 0) {
			rc = autopos_wait_runtime_master_started(idx, 1500);
		}
	} else {
		rc = autopos_send_anchor_cmd_checked(runtime_cmd, "OK RUNTIME", 1500);
	}
	if (rc != 0) {
		if (allow_reconnect_retry && (rc == -ETIMEDOUT || rc == -ENOTCONN)) {
			printk("AUTOPOS anchor %c runtime cmd failed rc=%d; forcing control-link reconnect retry\n",
			       autopos_labels[idx], rc);
			allow_reconnect_retry = false;
			rc = autopos_reconnect_anchor_ready(idx, 15000);
			if (rc != 0) {
				return rc;
			}
			goto retry_after_reconnect;
		}
		return rc;
	}

	rc = autopos_wait_active_role_state(expect_role, 7000);
	if (rc != 0) {
		if (allow_reconnect_retry && rc == -ETIMEDOUT) {
			printk("AUTOPOS anchor %c role transition timeout; forcing control-link reconnect retry\n",
			       autopos_labels[idx]);
			allow_reconnect_retry = false;
			rc = autopos_reconnect_anchor_ready(idx, 15000);
			if (rc != 0) {
				return rc;
			}
			goto retry_after_reconnect;
		}
		return rc;
	}

	if (demoting_active_master) {
		int settle_rc = autopos_wait_state_field("busy=1", 1500);

		printk("AUTOPOS demoted master %c settle: state_rc=%d wait_ms=%d\n",
		       autopos_labels[idx], settle_rc, AUTOPOS_DEMOTED_MASTER_SETTLE_MS / 2);
		k_sleep(K_MSEC(AUTOPOS_DEMOTED_MASTER_SETTLE_MS / 2));
	}

	return 0;
}

static int autopos_apply_full_sync(void)
{
	int rc;

	for (int i = 0; i < AUTOPOS_ANCHOR_COUNT; ++i) {
		if (i == autopos_target_idx) {
			continue;
		}
		rc = autopos_apply_one_anchor(i, false);
		if (rc != 0) {
			(void)snprintf(autopos_last_error, sizeof(autopos_last_error),
				       "anchor %c step failed rc=%d", autopos_labels[i], rc);
			(void)snprintf(autopos_state, sizeof(autopos_state), "failed");
			printk("AUTOPOS apply failed: %s\n", autopos_last_error);
			return rc;
		}
		printk("AUTOPOS anchor %c role verified\n", autopos_labels[i]);
	}

	rc = autopos_apply_one_anchor(autopos_target_idx, true);
	if (rc != 0) {
		(void)snprintf(autopos_last_error, sizeof(autopos_last_error),
			       "anchor %c step failed rc=%d", autopos_labels[autopos_target_idx], rc);
		(void)snprintf(autopos_state, sizeof(autopos_state), "failed");
		printk("AUTOPOS apply failed: %s\n", autopos_last_error);
		return rc;
	}
	printk("AUTOPOS anchor %c role verified\n", autopos_labels[autopos_target_idx]);
	return 0;
}

static int autopos_apply_bootstrap_target_only(void)
{
	int rc;

	/* On a fresh session, the anchors are already expected to be in their
	 * persisted matrix runtime. Forcing a BLE control verify on all seven
	 * non-target anchors before the first master promotion turns the first
	 * round into an 8-peer discovery storm and trips anchor-ctrl discovery
	 * resource failures (-120). Promote the requested master first and let
	 * the existing invariant carry the rest.
	 */
	rc = autopos_apply_one_anchor(autopos_target_idx, true);
	if (rc != 0) {
		(void)snprintf(autopos_last_error, sizeof(autopos_last_error),
			       "anchor %c promote failed rc=%d",
			       autopos_labels[autopos_target_idx], rc);
		(void)snprintf(autopos_state, sizeof(autopos_state), "failed");
		printk("AUTOPOS apply failed: %s\n", autopos_last_error);
		return rc;
	}

	printk("AUTOPOS bootstrap target-only promote verified: master=%c\n",
	       autopos_labels[autopos_target_idx]);
	return 0;
}

static int autopos_apply_incremental_sync(void)
{
	int rc;
	int prev_master_idx = autopos_last_success_idx;

	if (prev_master_idx < 0 || prev_master_idx >= AUTOPOS_ANCHOR_COUNT) {
		return autopos_apply_full_sync();
	}

	printk("AUTOPOS apply incremental: prev_master=%c new_master=%c\n",
	       autopos_labels[prev_master_idx], autopos_labels[autopos_target_idx]);

	if (prev_master_idx != autopos_target_idx) {
		rc = autopos_apply_one_anchor(prev_master_idx, false);
		if (rc != 0) {
			printk("AUTOPOS incremental demote failed: anchor=%c rc=%d; forcing clean full-sync retry\n",
			       autopos_labels[prev_master_idx], rc);
			autopos_last_success_idx = -1;
			autopos_detach_sweep_listen();
			rc = autopos_apply_full_sync();
			if (rc != 0) {
				(void)snprintf(autopos_last_error, sizeof(autopos_last_error),
					       "incremental recovery full-sync failed after %c demote rc=%d",
					       autopos_labels[prev_master_idx], rc);
				(void)snprintf(autopos_state, sizeof(autopos_state), "failed");
				printk("AUTOPOS apply failed: %s\n", autopos_last_error);
				return rc;
			}
			return 0;
		}
		printk("AUTOPOS anchor %c role verified\n", autopos_labels[prev_master_idx]);
	}

	rc = autopos_apply_one_anchor(autopos_target_idx, true);
	if (rc != 0) {
		(void)snprintf(autopos_last_error, sizeof(autopos_last_error),
			       "anchor %c promote failed rc=%d",
			       autopos_labels[autopos_target_idx], rc);
		(void)snprintf(autopos_state, sizeof(autopos_state), "failed");
		printk("AUTOPOS apply failed: %s\n", autopos_last_error);
		return rc;
	}
	printk("AUTOPOS anchor %c role verified\n", autopos_labels[autopos_target_idx]);
	return 0;
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
	autopos_detach_sweep_listen();

	/*
	 * First successful round establishes the invariant:
	 * one anchor is master, all others are matrix.
	 * After that, only the previous master and the new master need
	 * touching for the next sweep round.
	 */
	if (autopos_last_success_idx < 0) {
		printk("AUTOPOS apply mode: bootstrap-target-only\n");
		rc = autopos_apply_bootstrap_target_only();
	} else {
		printk("AUTOPOS apply mode: incremental\n");
		rc = autopos_apply_incremental_sync();
	}
	if (rc != 0) {
		goto done;
	}

	autopos_last_success_idx = autopos_target_idx;
	(void)snprintf(autopos_state, sizeof(autopos_state), "ready");
	printk("AUTOPOS apply success: master=%c\n", autopos_labels[autopos_target_idx]);

	if (autopos_round_sets != 0U) {
		printk("AUTOPOS finite sweep handoff: master=%c sets=%lu; host waits for SWEEP_DONE\n",
		       autopos_labels[autopos_target_idx],
		       (unsigned long)autopos_round_sets);
		goto done;
	}

	printk("AUTOPOS sweep converge: master=%c discovery kept active for background anchor retention\n",
	       autopos_labels[autopos_target_idx]);
	rc = autopos_wait_sweep_converged(autopos_target_idx,
					 AUTOPOS_SWEEP_CONVERGE_TIMEOUT_MS);
	printk("AUTOPOS sweep converge rc=%d master=%c timeout_ms=%d min_q_observe=%d consecutive=%d\n",
	       rc,
	       autopos_labels[autopos_target_idx],
	       AUTOPOS_SWEEP_CONVERGE_TIMEOUT_MS,
	       AUTOPOS_SWEEP_CONVERGE_MIN_QUALITY,
	       AUTOPOS_SWEEP_CONVERGE_CONSECUTIVE);
	k_sleep(K_MSEC(AUTOPOS_SWEEP_ATTACH_HANDOFF_MS));
	printk("AUTOPOS sweep listen attach: master=%c uuid=%s\n",
	       autopos_labels[autopos_target_idx],
	       autopos_uuid_map[autopos_target_idx]);
	master_set_runtime_target_kind(MASTER_TARGET_ANCHOR);
	master_set_runtime_target_uuid(autopos_uuid_map[autopos_target_idx]);
	master_set_connect_and_start_mode();

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
		rc = anchor_apply_role_all(anchor_role_value, anchor_role_cir_value);
		printk("anchor role rc=%d target=all role=%s cir=%s\n",
		       rc, anchor_role_value, anchor_role_cir_value);
	} else {
		rc = anchor_apply_role(anchor_role_target, anchor_role_value, anchor_role_cir_value);
		printk("anchor role rc=%d target=%s role=%s cir=%s\n",
		       rc, anchor_role_target, anchor_role_value, anchor_role_cir_value);
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
	control_apply_mode_leds();
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
	char arg3[16] = { 0 };
	int parsed;
	int rc;
	const char *payload;

	if (line == NULL || line[0] == '\0') {
		return;
	}

	if (strncasecmp(line, "APOS_TO ", 8) == 0) {
		const char *rest = line + 8;
		const char *space = strchr(rest, ' ');
		char target[32];
		const char *apos_payload;
		size_t target_len;

		if (control_build_anchor_only()) {
			printk("apos_to ignored: anchor-only build never targets BS tags\n");
			control_force_anchor_runtime_target(control_mode == CONTROL_MODE_AUTOPOS);
			return;
		}

		if (space == NULL) {
			printk("apos_to rc=%d reason=no_payload payload=%s\n", -EINVAL, line);
			return;
		}
		target_len = (size_t)(space - rest);
		if (target_len == 0U || target_len >= sizeof(target)) {
			printk("apos_to rc=%d reason=bad_target payload=%s\n", -EINVAL, line);
			return;
		}
		memcpy(target, rest, target_len);
		target[target_len] = '\0';
		apos_payload = space + 1;
		if (strncasecmp(apos_payload, "APOS", 4) != 0) {
			printk("apos_to rc=%d target=%s reason=bad_apos payload=%s\n",
			       -EINVAL, target, apos_payload);
			return;
		}

		master_set_runtime_target_kind(MASTER_TARGET_TAG);
		master_set_runtime_target_name(target);
		master_set_runtime_target_prefix("");
		master_set_runtime_target_uuid("");
		rc = master_send_command_now(apos_payload);
		printk("apos_to rc=%d target=%s payload=%s\n", rc, target, apos_payload);
		return;
	}

	if (strncasecmp(line, "APOS", 4) == 0) {
		rc = master_send_command_now(line);
		printk("apos rc=%d payload=%s\n", rc, line);
		return;
	}

	if (strncasecmp(line, "cmd ", 4) == 0) {
		payload = line + 4;
		rc = master_send_command_now(payload);
		printk("cmd rc=%d payload=%s\n", rc, payload);
		return;
	}

	if (strncasecmp(line, "cmd_all ", 8) == 0) {
		payload = line + 8;
		rc = master_send_command_all_now(payload);
		printk("cmd_all rc=%d payload=%s\n", rc, payload);
		return;
	}

	if (strncasecmp(line, "tag cir ", 8) == 0) {
		char mode[24] = { 0 };
		bool send_all = false;
		const char *cir_payload;

		if (control_build_anchor_only()) {
			printk("tag cir ignored: anchor-only build never targets BS tags\n");
			control_force_anchor_runtime_target(control_mode == CONTROL_MODE_AUTOPOS);
			return;
		}

		if (sscanf(line + 8, "%23s", mode) != 1) {
			printk("tag cir usage: tag cir <off|compact|full|status> | tag cir all <off|compact|full|status>\n");
			return;
		}
		for (char *p = mode; *p != '\0'; ++p) {
			*p = (char)tolower((unsigned char)*p);
		}
		if (strcmp(mode, "all") == 0) {
			send_all = true;
			if (sscanf(line + 8, "%*s %23s", mode) != 1) {
				printk("tag cir usage: missing mode after all\n");
				return;
			}
			for (char *p = mode; *p != '\0'; ++p) {
				*p = (char)tolower((unsigned char)*p);
			}
		}

		if (strcmp(mode, "status") == 0 || strcmp(mode, "?") == 0) {
			cir_payload = "CIR?";
		} else if (strcmp(mode, "off") == 0 || strcmp(mode, "0") == 0) {
			cir_payload = "CIR OFF";
		} else if (strcmp(mode, "compact") == 0 || strcmp(mode, "1") == 0) {
			cir_payload = "CIR COMPACT";
		} else if (strcmp(mode, "full") == 0 || strcmp(mode, "2") == 0) {
			cir_payload = "CIR FULL";
		} else {
			printk("tag cir invalid mode: %s\n", mode);
			return;
		}

		system_target_set_kind(SYS_DEV_TAG);
		master_set_runtime_target_kind(MASTER_TARGET_TAG);
		if (send_all) {
			rc = master_send_command_all_now(cir_payload);
		} else {
			rc = master_send_command_now(cir_payload);
		}
		printk("tag cir rc=%d all=%u payload=%s\n", rc,
		       (unsigned int)(send_all ? 1U : 0U), cir_payload);
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

	if (strncasecmp(line, "tdma ", 5) == 0) {
		char sub[16] = { 0 };
		char a[32] = { 0 };
		char b[32] = { 0 };
		int n;

		n = sscanf(line + 5, "%15s %31s %31s", sub, a, b);
		for (char *p = sub; *p != '\0'; ++p) {
			*p = (char)tolower((unsigned char)*p);
		}
		for (char *p = b; *p != '\0'; ++p) {
			*p = (char)tolower((unsigned char)*p);
		}

		if (n >= 1 && strcmp(sub, "show") == 0) {
			master_tdma_print_status();
			return;
		}
		if (n >= 1 && strcmp(sub, "rebalance") == 0) {
			rc = master_tdma_rebalance_now();
			printk("tdma rebalance rc=%d\n", rc);
			return;
		}
		if (n >= 1 && strcmp(sub, "clear") == 0) {
			rc = master_tdma_clear_profiles();
			printk("tdma clear rc=%d\n", rc);
			return;
		}
		if (n >= 2 && strcmp(sub, "hold") == 0) {
			unsigned long hold;
			char *end = NULL;

			hold = strtoul(a, &end, 10);
			if (end == a || *end != '\0' || hold > 1UL) {
				printk("tdma hold parse failed: %s\n", a);
				return;
			}
			rc = master_tdma_set_rebalance_hold(hold != 0UL);
			printk("tdma hold rc=%d hold=%lu\n", rc, hold);
			return;
		}
			if (n >= 3 && strcmp(sub, "profile") == 0) {
				rc = master_tdma_set_profile(a, b);
				printk("tdma profile rc=%d target=%s profile=%s\n", rc, a, b);
				return;
			}
			if (n >= 3 && strcmp(sub, "roster") == 0) {
				rc = master_tdma_add_roster_target(a, b);
				printk("tdma roster rc=%d target=%s profile=%s\n", rc, a, b);
				return;
			}
		if (n >= 3 && strcmp(sub, "freq") == 0) {
			unsigned long hz;
			char *end = NULL;

			hz = strtoul(b, &end, 10);
			if (end == b || *end != '\0' || hz > UINT8_MAX) {
				printk("tdma freq parse failed: %s\n", b);
				return;
			}
			rc = master_tdma_set_profile_freq(a, (uint8_t)hz);
			printk("tdma freq rc=%d profile=%s hz=%lu\n", rc, a, hz);
			return;
		}

		printk("Unknown tdma command: %s\n", line);
		control_print_help();
		return;
	}

	parsed = sscanf(line, "%15s %15s %63s %15s", cmd, arg, arg2, arg3);
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

	if (strcmp(cmd, "ota") == 0) {
		if (parsed >= 2 &&
		    (strcmp(arg, "show") == 0 || strcmp(arg, "version") == 0)) {
			control_print_ota_bundle_info();
			return;
		}
		printk("Unknown ota command: %s\n", line);
		control_print_help();
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
			char opt1[24] = {0};
			char opt2[24] = {0};
			char cir_raw[24] = "0";
			char cir_norm[16] = "0";
			int role_argc;

			if (control_mode == CONTROL_MODE_OTA) {
				printk("anchor role ignored: control mode must not be OTA\n");
				return;
			}
			role_argc = sscanf(line, "%*s %*s %*s %23s %23s %23s",
					   role_raw, opt1, opt2);
			if (role_argc < 1) {
				printk("anchor role usage: anchor role <A..H|UUID32|all> <master|matrix|responder> [cir <0|compact|full>|cir=<0|compact|full>]\n");
				return;
			}
			for (char *p = role_raw; *p != '\0'; ++p) {
				*p = (char)tolower((unsigned char)*p);
			}
			for (char *p = opt1; *p != '\0'; ++p) {
				*p = (char)tolower((unsigned char)*p);
			}
			for (char *p = opt2; *p != '\0'; ++p) {
				*p = (char)tolower((unsigned char)*p);
			}
			if (role_argc >= 2) {
				if (strcmp(opt1, "cir") == 0) {
					if (role_argc < 3) {
						printk("anchor role usage: missing cir mode\n");
						return;
					}
					(void)snprintf(cir_raw, sizeof(cir_raw), "%s", opt2);
				} else if (strncmp(opt1, "cir=", 4) == 0) {
					(void)snprintf(cir_raw, sizeof(cir_raw), "%s", opt1 + 4);
				} else {
					printk("anchor role unsupported option: %s\n", opt1);
					return;
				}
			}
			if (normalize_anchor_cir_mode(cir_raw, cir_norm, sizeof(cir_norm)) != 0) {
				printk("anchor role invalid cir mode: %s\n", cir_raw);
				return;
			}
			if (strcmp(arg2, "all") == 0) {
				if (!atomic_cas(&anchor_role_pending, 0, 1)) {
					printk("anchor role already running\n");
					return;
				}
				anchor_role_all_targets = true;
				(void)snprintf(anchor_role_target, sizeof(anchor_role_target), "all");
				(void)snprintf(anchor_role_value, sizeof(anchor_role_value), "%s", role_raw);
				(void)snprintf(anchor_role_cir_value, sizeof(anchor_role_cir_value),
					       "%s", cir_norm);
				k_work_submit_to_queue(&autopos_work_q, &anchor_role_work);
				printk("anchor role started target=all role=%s cir=%s\n",
				       role_raw, cir_norm);
				return;
			}
			if (!atomic_cas(&anchor_role_pending, 0, 1)) {
				printk("anchor role already running\n");
				return;
			}
			anchor_role_all_targets = false;
			(void)snprintf(anchor_role_target, sizeof(anchor_role_target), "%s", arg2);
			(void)snprintf(anchor_role_value, sizeof(anchor_role_value), "%s", role_raw);
			(void)snprintf(anchor_role_cir_value, sizeof(anchor_role_cir_value),
				       "%s", cir_norm);
			k_work_submit_to_queue(&autopos_work_q, &anchor_role_work);
			printk("anchor role started target=%s role=%s cir=%s\n",
			       arg2, role_raw, cir_norm);
			return;
		}
		if (strcmp(arg, "reset") == 0) {
			char mode_raw[24];

			if (control_mode == CONTROL_MODE_OTA) {
				printk("anchor reset ignored: control mode must not be OTA\n");
				return;
			}
			if (sscanf(line, "%*s %*s %*s %23s", mode_raw) != 1) {
				printk("anchor reset usage: anchor reset <A..H|UUID32|all> <autopos|responder>\n");
				return;
			}
			for (char *p = mode_raw; *p != '\0'; ++p) {
				*p = (char)tolower((unsigned char)*p);
			}
			if (strcmp(arg2, "all") == 0) {
				rc = anchor_reset_mode_all(mode_raw);
				printk("anchor reset rc=%d target=all mode=%s\n", rc, mode_raw);
				return;
			}
			rc = anchor_reset_mode(arg2, mode_raw);
			printk("anchor reset rc=%d target=%s mode=%s\n", rc, arg2, mode_raw);
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
			if (control_build_anchor_only()) {
				printk("mode recv redirected: anchor-only build stays in AUTOPOS anchor control\n");
				control_mode = CONTROL_MODE_AUTOPOS;
				sync_master_log_mode(control_mode);
				control_save_mode();
				autopos_reset_runtime_state(false);
				control_force_anchor_runtime_target(true);
				ota_transition_active = false;
				master_set_background_gate(true, "anchor_only_recv_redirect");
				master_set_connect_and_start_mode();
				master_disconnect_all_peers();
				control_wait_for_peer_clear(3000);
				master_restart_discovery();
				autopos_print_status();
				return;
			}
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
			autopos_reset_runtime_state(false);
			ota_transition_active = false;
			master_set_background_gate(true, "mode_autopos");
			master_set_anchor_wildcard_scan(true);
			master_set_runtime_target_name("");
			master_set_runtime_target_prefix("");
			master_set_runtime_target_kind(MASTER_TARGET_ANCHOR);
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
		if (strcmp(arg, "detach") == 0) {
			autopos_detach_sweep_listen();
			autopos_reset_runtime_state(false);
			printk("AUTOPOS detach complete\n");
			autopos_print_status();
			return;
		}
		if (strcmp(arg, "cir") == 0) {
			char cir_norm[16];

			if (parsed < 3) {
				printk("autopos cir usage: autopos cir <0|compact|full>\n");
				return;
			}
			if (normalize_anchor_cir_mode(arg2, cir_norm, sizeof(cir_norm)) != 0) {
				printk("autopos cir invalid mode: %s\n", arg2);
				return;
			}
			(void)snprintf(autopos_cir_mode, sizeof(autopos_cir_mode), "%s", cir_norm);
			printk("AUTOPOS cir mode set: %s\n", autopos_cir_mode);
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
		if (strcmp(arg, "result") == 0 && parsed >= 3) {
			if (strcmp(arg2, "show") == 0) {
				autopos_result_history_dump();
				return;
			}
			if (strcmp(arg2, "clear") == 0) {
				autopos_result_history_clear();
				printk("AUTOPOS result history cleared\n");
				return;
			}
			printk("autopos result invalid arg: %s\n", arg2);
			return;
		}
		if (strcmp(arg, "round") == 0 && parsed >= 3) {
			if (strlen(arg2) != 1U) {
				printk("autopos round invalid label: %s\n", arg2);
				return;
			}
			if (parsed >= 4) {
				char *endp = NULL;
				unsigned long sets = strtoul(arg3, &endp, 10);

				if (endp == arg3 || *endp != '\0' || sets > 10000UL) {
					printk("autopos round invalid sets: %s\n", arg3);
					return;
				}
				autopos_round_sets = (uint32_t)sets;
			} else {
				autopos_round_sets = 0U;
			}
			autopos_target_idx = autopos_label_to_index(arg2[0]);
			if (autopos_target_idx < 0) {
				printk("autopos round invalid label: %s\n", arg2);
				return;
			}
			autopos_result_history_clear();
			autopos_save_target();
			(void)snprintf(autopos_state, sizeof(autopos_state), "staged");
			autopos_last_error[0] = '\0';
			printk("AUTOPOS round staged: master=%c sets=%lu\n",
			       autopos_labels[autopos_target_idx],
			       (unsigned long)autopos_round_sets);
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
				master_clear_one_shot_command();
				(void)master_ota_target_set_token(-1);
				(void)master_ota_target_set_name("");
				(void)master_ota_target_set_prefix("");
				(void)master_ota_target_set_uuid("");
				ota_target_token_cfg = -1;
				ota_target_name_cfg[0] = '\0';
				ota_target_prefix_cfg[0] = '\0';
				ota_target_uuid_cfg[0] = '\0';
				control_force_anchor_runtime_target(control_mode == CONTROL_MODE_AUTOPOS);
				control_disconnect_all_links();
				printk("device kind set: anchor (OTA target defaults reset, no BS prefix)\n");
				if (control_mode == CONTROL_MODE_RECV) {
					master_set_connect_and_start_mode();
					control_wait_for_peer_clear(3000);
					master_restart_discovery();
				}
				system_target_print();
				return;
			}
			if (strcmp(arg2, "tag") == 0) {
				if (control_build_anchor_only()) {
					printk("device kind tag ignored: anchor-only build never connects BS tags\n");
					control_force_anchor_runtime_target(control_mode == CONTROL_MODE_AUTOPOS);
					system_target_print();
					return;
				}
				system_target_set_kind(SYS_DEV_TAG);
				master_clear_one_shot_command();
				master_disconnect_all_peers();
					/* Preserve ota_target name/prefix/uuid across kind switches.
					 *
					 * Host-side scripts may set `ota_target name <BSxxxx>` before switching to
					 * `device kind tag`, expecting the filter to be active immediately when RECV
					 * restarts discovery. Resetting the filter here creates a race where the
					 * master can connect to the wrong Tag first in a multi-Tag environment.
					 *
					 * We only reset token to the safe default (-1). */
					(void)master_ota_target_set_token(-1);
					ota_target_token_cfg = -1;
					master_set_runtime_target_kind(MASTER_TARGET_TAG);
					master_set_anchor_wildcard_scan(false);
					master_set_runtime_target_token(-1);
					printk("device kind set: tag (OTA target preserved)\n");
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
		control_apply_mode_leds();
		printk("LED map: 1=AUTOPOS 2=RECV 3=OTA 4=FLOW\n");
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
	control_apply_mode_leds();
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
		ota_target_token_cfg = APP_MASTER_OTA_TARGET_TOKEN_ID;
		(void)snprintf(ota_target_name_cfg, sizeof(ota_target_name_cfg), "%s",
			       APP_MASTER_OTA_TARGET_NAME);
		(void)snprintf(ota_target_prefix_cfg, sizeof(ota_target_prefix_cfg), "%s",
			       APP_MASTER_OTA_TARGET_NAME_PREFIX);
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
	if (control_build_anchor_only()) {
		ota_target_token_cfg = -1;
		ota_target_name_cfg[0] = '\0';
		ota_target_prefix_cfg[0] = '\0';
		ota_expect_nus_cfg = false;
		printk("Anchor-only build: sanitized restored OTA target to anchor/uuid-only\n");
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
	if (control_build_anchor_only()) {
		system_target_set_kind(SYS_DEV_ANCHOR);
		master_set_runtime_target_kind(MASTER_TARGET_ANCHOR);
		master_set_anchor_wildcard_scan(control_mode == CONTROL_MODE_AUTOPOS);
	} else if (ota_target_name_cfg[0] != '\0') {
		system_target.kind = SYS_DEV_TAG;
		system_target.caps = system_caps_for_kind(SYS_DEV_TAG);
		master_set_runtime_target_kind(MASTER_TARGET_TAG);
	} else {
		system_target.kind = SYS_DEV_UNKNOWN;
		system_target.caps = system_caps_for_kind(SYS_DEV_UNKNOWN);
		master_set_runtime_target_kind(MASTER_TARGET_UNKNOWN);
	}
	control_apply_boot_profile();
	master_ota_target_print();
	if (autopos_state[0] == '\0') {
		(void)snprintf(autopos_state, sizeof(autopos_state), "idle");
	}

	control_leds_set(DK_NO_LEDS_MSK);
	printk("BioSpur BLE master control ready on %s\n", CONFIG_BOARD_TARGET);
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
