#pragma once

#include <stdint.h>

typedef void (*ads1298_frame_cb_t)(const int32_t ch_code[8],
				   const uint8_t status[3]);

int ads1298_init(void);
void ads1298_start(void);
void ads1298_set_frame_callback(ads1298_frame_cb_t cb);
