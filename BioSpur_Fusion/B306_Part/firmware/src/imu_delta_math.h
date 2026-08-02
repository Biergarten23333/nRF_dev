#ifndef BIOSPUR_IMU_DELTA_MATH_H
#define BIOSPUR_IMU_DELTA_MATH_H

#include <stdint.h>

#define BSF_IMU_CHIP_HOUR_MS 3600000u
#define BSF_IMU_DELTA_HIST_BINS 21u

/*
 * Residual histogram, in milliseconds:
 *   0 <=-101; 1 -100..-51; 2 -50..-21; 3 -20..-11; 4 -10..-6;
 *   5 -5; 6 -4; 7 -3; 8 -2; 9 -1; 10 0; 11 +1; 12 +2;
 *   13 +3; 14 +4; 15 +5; 16 +6..+10; 17 +11..+20;
 *   18 +21..+50; 19 +51..+100; 20 >=+101.
 */
static inline uint8_t bsf_imu_delta_hist_bin(int32_t residual_ms)
{
	if (residual_ms <= -101) {
		return 0u;
	}
	if (residual_ms <= -51) {
		return 1u;
	}
	if (residual_ms <= -21) {
		return 2u;
	}
	if (residual_ms <= -11) {
		return 3u;
	}
	if (residual_ms <= -6) {
		return 4u;
	}
	if (residual_ms <= 5) {
		return (uint8_t)(residual_ms + 10);
	}
	if (residual_ms <= 10) {
		return 16u;
	}
	if (residual_ms <= 20) {
		return 17u;
	}
	if (residual_ms <= 50) {
		return 18u;
	}
	if (residual_ms <= 100) {
		return 19u;
	}
	return 20u;
}

static inline int32_t bsf_imu_chip_signed_delta_ms(uint32_t newer_ms,
						   uint32_t older_ms)
{
	uint32_t delta =
		(newer_ms + BSF_IMU_CHIP_HOUR_MS - older_ms) %
		BSF_IMU_CHIP_HOUR_MS;

	if (delta > BSF_IMU_CHIP_HOUR_MS / 2u) {
		return (int32_t)delta - (int32_t)BSF_IMU_CHIP_HOUR_MS;
	}
	return (int32_t)delta;
}

static inline int32_t bsf_imu_delta_residual_ms(uint32_t newer_ms,
						uint32_t older_ms,
						uint64_t b306_elapsed_us)
{
	uint64_t rounded_elapsed_ms = (b306_elapsed_us + 500u) / 1000u;

	/*
	 * Health observations are nominally 50 ms apart. Saturation only guards
	 * a corrupt/abandoned window; it is unreachable in normal operation.
	 */
	if (rounded_elapsed_ms > INT32_MAX) {
		rounded_elapsed_ms = INT32_MAX;
	}
	return bsf_imu_chip_signed_delta_ms(newer_ms, older_ms) -
		(int32_t)rounded_elapsed_ms;
}

#endif /* BIOSPUR_IMU_DELTA_MATH_H */
