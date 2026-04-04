#include <errno.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "uwb_driver.h"

LOG_MODULE_REGISTER(tx_uwb, LOG_LEVEL_INF);

#define BSGR_UWB_RECORD_POOL_COUNT 6

K_FIFO_DEFINE(uwb_record_fifo);
K_HEAP_DEFINE(uwb_heap, BSGR_UWB_RECORD_POOL_COUNT * sizeof(struct bsgr_uwb_record));

static bool uwb_bound;
static bool uwb_started;

int uwb_driver_init(void)
{
	uwb_bound = false;
	uwb_started = false;
	LOG_INF("UWB driver initialized in host-timestamp stub mode");
	return 0;
}

int uwb_driver_start(void)
{
	uwb_started = true;
	return uwb_bound ? 0 : -ENODEV;
}

void uwb_driver_stop(void)
{
	uwb_started = false;
}

bool uwb_driver_is_bound(void)
{
	return uwb_bound;
}

void uwb_driver_process(void)
{
	if (!uwb_started) {
		return;
	}

	/* TODO: bind to the real DWM1001C transport once the protocol is locked. */
}

int uwb_driver_ingest_record(const uint8_t *data, size_t len, uint32_t capture_ticks)
{
	struct bsgr_uwb_record *record;

	if ((data == NULL) || (len == 0U)) {
		return -EINVAL;
	}

	record = k_heap_alloc(&uwb_heap, sizeof(*record), K_NO_WAIT);
	if (record == NULL) {
		return -ENOMEM;
	}

	memset(record, 0, sizeof(*record));
	record->host_capture_ticks = capture_ticks;
	record->raw_len = MIN(len, (size_t)BSGR_UWB_MAX_RAW_RECORD_LEN);
	memcpy(record->raw, data, record->raw_len);
	record->parser_flags = BSGR_PARSER_FLAG_STUB_DECODE;
	k_fifo_put(&uwb_record_fifo, record);
	return 0;
}

struct bsgr_uwb_record *uwb_driver_pop_record(void)
{
	return k_fifo_get(&uwb_record_fifo, K_NO_WAIT);
}
