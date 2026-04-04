#ifndef BSGR_TX_UWB_DRIVER_H_
#define BSGR_TX_UWB_DRIVER_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "bsgr_protocol.h"

struct bsgr_uwb_record {
	void *fifo_reserved;
	uint32_t host_capture_ticks;
	uint16_t parser_flags;
	uint16_t raw_len;
	uint8_t raw[BSGR_UWB_MAX_RAW_RECORD_LEN];
};

int uwb_driver_init(void);
int uwb_driver_start(void);
void uwb_driver_stop(void);
bool uwb_driver_is_bound(void);
void uwb_driver_process(void);
int uwb_driver_ingest_record(const uint8_t *data, size_t len, uint32_t capture_ticks);
struct bsgr_uwb_record *uwb_driver_pop_record(void);

#endif /* BSGR_TX_UWB_DRIVER_H_ */
