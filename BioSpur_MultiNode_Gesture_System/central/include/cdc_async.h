#ifndef BSGR_CENTRAL_CDC_ASYNC_H_
#define BSGR_CENTRAL_CDC_ASYNC_H_

#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>

enum bsgr_cdc_channel {
	BSGR_CDC_CHANNEL_MCUMGR = 0,
	BSGR_CDC_CHANNEL_DATA = 1,
	BSGR_CDC_CHANNEL_COUNT = 2,
};

int cdc_async_init(void);
bool cdc_async_ready(void);
int cdc_async_write(enum bsgr_cdc_channel channel, const uint8_t *data, size_t len);
int cdc_async_write_data(const uint8_t *data, size_t len);
int cdc_async_poll_in(enum bsgr_cdc_channel channel, uint8_t *ch);

#endif /* BSGR_CENTRAL_CDC_ASYNC_H_ */
