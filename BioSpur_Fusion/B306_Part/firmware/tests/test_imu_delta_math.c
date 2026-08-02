#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "../src/imu_delta_math.h"

int main(void)
{
	assert(bsf_imu_chip_signed_delta_ms(1050u, 1000u) == 50);
	assert(bsf_imu_chip_signed_delta_ms(20u, 3599970u) == 50);
	assert(bsf_imu_chip_signed_delta_ms(950u, 1000u) == -50);
	assert(bsf_imu_delta_residual_ms(1050u, 1000u, 50000u) == 0);
	assert(bsf_imu_delta_residual_ms(1048u, 1000u, 50000u) == -2);
	assert(bsf_imu_delta_residual_ms(1053u, 1000u, 50499u) == 3);
	assert(bsf_imu_delta_residual_ms(1053u, 1000u, 50500u) == 2);

	assert(bsf_imu_delta_hist_bin(-101) == 0u);
	assert(bsf_imu_delta_hist_bin(-100) == 1u);
	assert(bsf_imu_delta_hist_bin(-51) == 1u);
	assert(bsf_imu_delta_hist_bin(-50) == 2u);
	assert(bsf_imu_delta_hist_bin(-21) == 2u);
	assert(bsf_imu_delta_hist_bin(-20) == 3u);
	assert(bsf_imu_delta_hist_bin(-11) == 3u);
	assert(bsf_imu_delta_hist_bin(-10) == 4u);
	assert(bsf_imu_delta_hist_bin(-6) == 4u);
	for (int32_t value = -5; value <= 5; ++value) {
		assert(bsf_imu_delta_hist_bin(value) == (uint8_t)(value + 10));
	}
	assert(bsf_imu_delta_hist_bin(6) == 16u);
	assert(bsf_imu_delta_hist_bin(10) == 16u);
	assert(bsf_imu_delta_hist_bin(11) == 17u);
	assert(bsf_imu_delta_hist_bin(20) == 17u);
	assert(bsf_imu_delta_hist_bin(21) == 18u);
	assert(bsf_imu_delta_hist_bin(50) == 18u);
	assert(bsf_imu_delta_hist_bin(51) == 19u);
	assert(bsf_imu_delta_hist_bin(100) == 19u);
	assert(bsf_imu_delta_hist_bin(101) == 20u);

	puts("IMU_DELTA_MATH_PASS");
	return 0;
}
