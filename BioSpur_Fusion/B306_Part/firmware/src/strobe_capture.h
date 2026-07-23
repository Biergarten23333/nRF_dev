#ifndef BIOSPUR_STROBE_CAPTURE_H
#define BIOSPUR_STROBE_CAPTURE_H

#include <stdint.h>

#include "biospur_fusion_ble.h"

int bsf_strobe_capture_init(void);
void bsf_strobe_capture_stop(void);
void bsf_strobe_capture_pair(uint8_t uwb_flags,
			     bsf_capture_record_t *record);
void bsf_strobe_capture_telemetry(bsf_ble_telemetry_t *telemetry);
uint64_t bsf_time_now_us(void);
void bsf_strobe_capture_counters_clear(void);

#endif /* BIOSPUR_STROBE_CAPTURE_H */
