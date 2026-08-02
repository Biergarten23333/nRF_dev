#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

int anchor_ultrasound_init(void);
int anchor_ultrasound_start(uint32_t duration_s);
void anchor_ultrasound_stop(void);
bool anchor_ultrasound_busy(void);
void anchor_ultrasound_status(char *buf, size_t len);

