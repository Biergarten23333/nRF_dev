#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "stall_detector_policy.h"

int main(void)
{
	struct bsf_stall_detector d = {0};
	struct bsf_stall_decision x;

	/* Connection/setup transients never arm the detector. */
	for (uint32_t i = 1; i <= 10; ++i) {
		x = bsf_stall_detector_step(&d, true, false, 0, 64,
			i, 0, 0, 1000, 3000, 1);
		assert(!x.fire && d.frozen_ms == 0);
	}
	for (uint32_t i = 11; i <= 20; ++i) {
		x = bsf_stall_detector_step(&d, true, true, 63, 64,
			i, 0, 0, 1000, 3000, 1);
		assert(!x.fire && d.frozen_ms == 0);
	}

	/* Armed dwell exceeds the 4 s supervision timeout: fire at 5 s. */
	x = bsf_stall_detector_step(&d, true, true, 64, 64, 21, 0, 1,
		1000, 5000, 1); assert(!x.fire);
	x = bsf_stall_detector_step(&d, true, true, 64, 64, 22, 0, 1,
		1000, 5000, 1); assert(!x.fire);
	x = bsf_stall_detector_step(&d, true, true, 64, 64, 23, 0, 1,
		1000, 5000, 1); assert(!x.fire);
	x = bsf_stall_detector_step(&d, true, true, 64, 64, 24, 0, 1,
		1000, 5000, 1); assert(!x.fire);
	x = bsf_stall_detector_step(&d, true, true, 64, 64, 25, 0, 1,
		1000, 5000, 1);
	assert(x.fire && x.reason == 2 && x.take_snapshot && x.recover);
	/* A disconnect inside the grace window retracts alarm and budget. */
	assert(bsf_stall_detector_retract_disconnect(&d, 1000, 1500, true));
	assert(d.alarm_count == 0 && d.recovery_count == 0 &&
	       !d.snapshot_valid);
	assert(!bsf_stall_detector_retract_disconnect(&d, 1600, 1500, true));

	/* A new healthy-link fault may now consume the restored budget. */
	for (uint32_t i = 26; i <= 30; ++i) {
		x = bsf_stall_detector_step(&d, true, true, 64, 64,
			i, 0, 1, 1000, 5000, 1);
	}
	assert(x.fire && x.recover && d.alarm_count == 1 &&
	       d.recovery_count == 1);

	/* Equal entry/exit context classifies a dead publisher. */
	d = (struct bsf_stall_detector){0};
	for (uint32_t i = 1; i <= 5; ++i) {
		x = bsf_stall_detector_step(&d, true, true, 64, 64,
			i, 0, 0, 1000, 5000, 1);
	}
	assert(x.fire && x.reason == 1);
	puts("stall detector policy: PASS");
	return 0;
}
