#ifndef CDC_ASYNC_H
#define CDC_ASYNC_H

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Initialize USB CDC ACM UART.
 * Returns 0 on success, negative errno on error.
 */
int  cdc_async_init(void);

/* Enable/disable binary data streaming to CDC.
 * - When disabled, cdc_async_write() will drop payloads.
 * - Logs via cdc_printf() are always printed regardless of this flag.
 */
void cdc_async_enable(bool enable);
bool cdc_async_is_enabled(void);

/* Low-level binary write (used for IMU/UWB multiplexed frames). */
int  cdc_async_write(const uint8_t *data, size_t len);

/* Simple printf-style logging over USB CDC (blocking). */
void cdc_printf(const char *fmt, ...);
/* Simple printf-style logging over USB CDC (blocking). */
void cdc_async_printf(const char *fmt, ...);

/* Poll one byte from CDC (non-blocking).
 * Returns 0 on success, -EAGAIN if no data, or other negative errno.
 */
int  cdc_async_poll_in(uint8_t *ch);

#endif /* CDC_ASYNC_H */
