#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "../src/imu_pull_diag_math.h"

int main(void)
{
	assert(bsf_imu_pull_hist_bin(0u) == 0u);
	assert(bsf_imu_pull_hist_bin(249u) == 0u);
	assert(bsf_imu_pull_hist_bin(250u) == 1u);
	assert(bsf_imu_pull_hist_bin(4999u) == 19u);
	assert(bsf_imu_pull_hist_bin(5000u) == 20u);
	assert(bsf_imu_pull_hist_bin(9999u) == 20u);
	assert(bsf_imu_pull_hist_bin(10000u) == 21u);
	assert(bsf_imu_pull_hist_bin(19999u) == 21u);
	assert(bsf_imu_pull_hist_bin(20000u) == 22u);
	assert(bsf_imu_pull_hist_bin(39999u) == 22u);
	assert(bsf_imu_pull_hist_bin(40000u) == 23u);
	assert(bsf_imu_pull_hist_bin(59999u) == 23u);
	assert(bsf_imu_pull_hist_bin(60000u) == 24u);
	assert(bsf_imu_pull_hist_bin(79999u) == 24u);
	assert(bsf_imu_pull_hist_bin(80000u) == 25u);
	assert(bsf_imu_pull_hist_bin(99999u) == 25u);
	assert(bsf_imu_pull_hist_bin(100000u) == 26u);
	assert(bsf_imu_pull_hist_bin(UINT32_MAX) == 26u);

	assert(bsf_imu_pull_lateness_us(100u, false, 0u) == 0u);
	assert(bsf_imu_pull_lateness_us(99u, true, 100u) == 0u);
	assert(bsf_imu_pull_lateness_us(100u, true, 100u) == 0u);
	assert(bsf_imu_pull_lateness_us(101u, true, 100u) == 1u);
	assert(bsf_imu_pull_lateness_us(
		       (uint64_t)UINT32_MAX + 2u, true, 0u) == UINT32_MAX);

	puts("IMU_PULL_DIAG_MATH_PASS");
	return 0;
}
