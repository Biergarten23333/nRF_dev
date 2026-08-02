#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "../src/publisher_priority.h"

static void test_truth_table(void)
{
	assert(bsf_publish_select(false, false, false) == BSF_PUBLISH_NONE);
	assert(bsf_publish_select(false, false, true) == BSF_PUBLISH_IMU);
	assert(bsf_publish_select(false, true, false) == BSF_PUBLISH_UWB);
	assert(bsf_publish_select(false, true, true) == BSF_PUBLISH_UWB);
	assert(bsf_publish_select(true, false, false) == BSF_PUBLISH_CTL);
	assert(bsf_publish_select(true, false, true) == BSF_PUBLISH_CTL);
	assert(bsf_publish_select(true, true, false) == BSF_PUBLISH_CTL);
	assert(bsf_publish_select(true, true, true) == BSF_PUBLISH_CTL);
}

/*
 * Model a connection that services one record per step while IMU refills
 * faster than service. UWB arrives every tenth step and control every
 * hundredth. Strict per-record selection must deliver every UWB/control
 * record; only IMU may accumulate or be discarded by a finite queue.
 */
static void test_starved_connection(void)
{
	uint32_t ctl_pending = 0u;
	uint32_t uwb_pending = 0u;
	uint32_t imu_pending = 0u;
	uint32_t ctl_offered = 0u;
	uint32_t uwb_offered = 0u;
	uint32_t ctl_sent = 0u;
	uint32_t uwb_sent = 0u;

	for (uint32_t step = 0u; step < 10000u; ++step) {
		imu_pending += 2u;
		if ((step % 10u) == 0u) {
			++uwb_pending;
			++uwb_offered;
		}
		if ((step % 100u) == 0u) {
			++ctl_pending;
			++ctl_offered;
		}

		switch (bsf_publish_select(ctl_pending != 0u,
					   uwb_pending != 0u,
					   imu_pending != 0u)) {
		case BSF_PUBLISH_CTL:
			--ctl_pending;
			++ctl_sent;
			break;
		case BSF_PUBLISH_UWB:
			--uwb_pending;
			++uwb_sent;
			break;
		case BSF_PUBLISH_IMU:
			--imu_pending;
			break;
		case BSF_PUBLISH_NONE:
			assert(false);
		}
	}

	assert(ctl_pending == 0u);
	assert(uwb_pending == 0u);
	assert(ctl_sent == ctl_offered);
	assert(uwb_sent == uwb_offered);
	assert(imu_pending != 0u);
}

int main(void)
{
	test_truth_table();
	test_starved_connection();
	puts("publisher priority tests passed");
	return 0;
}
