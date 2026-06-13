#pragma once

#include <zephyr/kernel.h>

#define EMG_STACK_SIZE 2048
#define EMG_PRIO 4

#define EMG_SAMPLE_RATE_SPS 1000
#define EMG_SAMPLES_PER_FRAME 4

#if (EMG_SAMPLE_RATE_SPS != 250)  && \
    (EMG_SAMPLE_RATE_SPS != 500)  && \
    (EMG_SAMPLE_RATE_SPS != 1000) && \
    (EMG_SAMPLE_RATE_SPS != 2000) && \
    (EMG_SAMPLE_RATE_SPS != 4000)
#error "EMG_SAMPLE_RATE_SPS must be one of: 250/500/1000/2000/4000"
#endif

#if (EMG_SAMPLES_PER_FRAME < 1) || (EMG_SAMPLES_PER_FRAME > 4)
#error "EMG_SAMPLES_PER_FRAME must be 1..4"
#endif
