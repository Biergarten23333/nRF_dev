#include <errno.h>
#include <ctype.h>
#include <stdbool.h>
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
#define CONTROL_BOOT_COOKIE_MAGIC 0x42534d44U
#define OTA_TARGET_BOOT_COOKIE_MAGIC 0x4f544147U
#define OTA_NUS_BOOT_COOKIE_MAGIC 0x4f54414eU

enum control_mode {
	CONTROL_MODE_RECV = 0,
	CONTROL_MODE_OTA = 1,
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
static atomic_t uart_line_ready;
static struct system_target_profile system_target;
static bool ota_expect_nus_cfg = true;
static bool ota_transition_active;

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
	return (mode == CONTROL_MODE_OTA) ? "OTA" : "RECV";
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
	printk("Commands: status | mode recv | mode ota | scan | conn | initiate\n");
	printk("Runtime NUS cmds: cmd <raw> | oneshot <raw> | oneshot show | oneshot clear\n");
	printk("Device model cmds: device show | device kind <anchor|tag>\n");
	printk("OTA target cmds: ota_target show | ota_target token <id|-1> | ota_target name <BSxxxx|-> | ota_target prefix <BS|-> | ota_target uuid <32hex|->\n");
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

static int control_settings_set(const char *key, size_t len,
				settings_read_cb read_cb, void *cb_arg)
{
	const char *next;

	if (!settings_name_steq(key, CONTROL_SETTINGS_MODE_KEY, &next) || next != NULL) {
		return -ENOENT;
	}

	if (len != sizeof(control_mode)) {
		return -EINVAL;
	}

	return read_cb(cb_arg, &control_mode, sizeof(control_mode));
}

static int control_settings_export(int (*cb)(const char *name, const void *value,
					     size_t val_len))
{
	return cb(CONTROL_SETTINGS_MODE_KEY, &control_mode, sizeof(control_mode));
}

SETTINGS_STATIC_HANDLER_DEFINE(master_ctrl, CONTROL_SETTINGS_SUBTREE, NULL,
			       control_settings_set, NULL, control_settings_export);

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
		if (control_mode != CONTROL_MODE_RECV) {
			printk("SCAN ignored: control mode must be RECV\n");
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
		if (control_mode != CONTROL_MODE_RECV) {
			printk("CONN ignored: control mode must be RECV\n");
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
			request_mode_switch(CONTROL_MODE_RECV, REQ_SRC_UART);
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
				(void)master_ota_target_set_token(-1);
				(void)master_ota_target_set_name("");
				(void)master_ota_target_set_prefix("BS");
				(void)master_ota_target_set_uuid("");
				ota_target_token_cfg = -1;
				ota_target_name_cfg[0] = '\0';
				(void)snprintf(ota_target_prefix_cfg, sizeof(ota_target_prefix_cfg), "BS");
				ota_target_uuid_cfg[0] = '\0';
				printk("device kind set: anchor (OTA target defaults reset)\n");
				system_target_print();
				return;
			}
			if (strcmp(arg2, "tag") == 0) {
				system_target_set_kind(SYS_DEV_TAG);
				(void)master_ota_target_set_prefix("BS");
				(void)snprintf(ota_target_prefix_cfg, sizeof(ota_target_prefix_cfg), "BS");
				printk("device kind set: tag\n");
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
		   control_mode != CONTROL_MODE_OTA) {
		control_mode = CONTROL_MODE_RECV;
	}

	printk("Control mode loaded: %s\n", control_mode_name(control_mode));
	return 0;
}

int main(void)
{
	int err;

	k_work_init(&mode_switch_work, mode_switch_work_handler);
	k_work_init(&uart_cmd_work, control_uart_cmd_work_handler);

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
	printk("UART control ready: type 'status' or 'mode recv'/'mode ota'\n");
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
	master_ota_set_expect_nus(ota_expect_nus_cfg);
	system_target.kind = SYS_DEV_UNKNOWN;
	system_target.caps = system_caps_for_kind(SYS_DEV_UNKNOWN);
	master_ota_target_print();

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
	printk("Launching receiver mode\n");
	return master_app_run();
}
