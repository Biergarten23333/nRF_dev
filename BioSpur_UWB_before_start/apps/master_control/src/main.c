#include <errno.h>
#include <ctype.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include <dk_buttons_and_leds.h>

#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/reboot.h>

#include "master_multi_app.h"

#define CONTROL_SETTINGS_SUBTREE "master_ctrl"
#define CONTROL_SETTINGS_MODE_KEY "mode"
#define CONTROL_BOOT_COOKIE_MAGIC 0x42534d44U

enum control_mode {
	CONTROL_MODE_RECV = 0,
	CONTROL_MODE_OTA = 1,
};

static uint8_t control_mode = CONTROL_MODE_RECV;
static uint32_t control_boot_cookie __noinit;
static uint8_t control_boot_mode __noinit;
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

enum request_source {
	REQ_SRC_BTN1 = 1,
	REQ_SRC_BTN2 = 2,
	REQ_SRC_BTN3 = 3,
	REQ_SRC_BTN4 = 4,
	REQ_SRC_UART = 5,
};

int master_app_run(void);
int master_ota_run(void);

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
	printk("Commands: status | mode recv | mode ota | scan | conn\n");
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
	control_mode = requested_mode;
	control_save_mode();
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
	int parsed;

	if (line == NULL || line[0] == '\0') {
		return;
	}

	parsed = sscanf(line, "%15s %15s", cmd, arg);
	for (char *p = cmd; *p != '\0'; ++p) {
		*p = (char)tolower((unsigned char)*p);
	}
	for (char *p = arg; *p != '\0'; ++p) {
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
		master_set_connect_and_start_mode();
		master_restart_discovery();
		return;
	}

	if (strcmp(cmd, "mode") == 0 && parsed >= 2) {
		if (strcmp(arg, "ota") == 0) {
			request_mode_switch(CONTROL_MODE_OTA, REQ_SRC_UART);
			return;
		}

		if (strcmp(arg, "recv") == 0 || strcmp(arg, "rx") == 0) {
			request_mode_switch(CONTROL_MODE_RECV, REQ_SRC_UART);
			return;
		}
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

	control_leds_set(DK_NO_LEDS_MSK);
	printk("BioSpur BLE master control ready on nRF52840 DK\n");
	printk("BTN1 toggles RECV/OTA, BTN2 forces OTA mode, BTN3=SCAN, BTN4=CONN&START\n");
	control_print_help();

	if (control_mode == CONTROL_MODE_OTA) {
		printk("Launching OTA mode\n");
		return master_ota_run();
	}

	printk("Launching receiver mode\n");
	return master_app_run();
}
