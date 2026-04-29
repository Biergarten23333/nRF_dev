#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/usb/usb_device.h>
#include <string.h>

static const struct device *const cdc = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

static void write_str(const char *s)
{
	while (*s) {
		uart_poll_out(cdc, *s++);
	}
}

int main(void)
{
	int rc;
	char line[64];
	size_t len = 0;

	rc = usb_enable(NULL);
	if (rc != 0) {
		return rc;
	}

	if (!device_is_ready(cdc)) {
		return -ENODEV;
	}

	write_str("B120_CDC_PROBE_READY internal_clock\r\n");
	write_str("commands: status | ota version | echo <text>\r\n");

	while (1) {
		unsigned char ch;
		if (uart_poll_in(cdc, &ch) == 0) {
			if (ch == '\r') {
				continue;
			}
			if (ch == '\n') {
				line[len] = '\0';
				if (strcmp(line, "status") == 0) {
					write_str("[PROBE] status ok mode=CDC_PROBE\r\n");
				} else if (strcmp(line, "ota version") == 0) {
					write_str("[PROBE] fw=b120-cdc-probe-internal-synth\r\n");
				} else if (strncmp(line, "echo ", 5) == 0) {
					write_str("[PROBE] ");
					write_str(line + 5);
					write_str("\r\n");
				} else if (len > 0) {
					write_str("[PROBE] unknown cmd\r\n");
				}
				len = 0;
			} else if (len + 1 < sizeof(line)) {
				line[len++] = (char)ch;
			} else {
				len = 0;
				write_str("[PROBE] line too long\r\n");
			}
		} else {
			k_sleep(K_MSEC(5));
		}
	}
}
