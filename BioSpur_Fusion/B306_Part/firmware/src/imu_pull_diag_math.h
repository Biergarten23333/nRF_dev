#ifndef BIOSPUR_IMU_PULL_DIAG_MATH_H
#define BIOSPUR_IMU_PULL_DIAG_MATH_H

#include <stdbool.h>
#include <stdint.h>

#define BSF_IMU_PULL_HIST_BINS 27u
#define BSF_IMU_PULL_FINE_BIN_US 250u
#define BSF_IMU_PULL_FINE_LIMIT_US 5000u
#define BSF_IMU_PULL_OVERFLOW_US 100000u

/*
 * Bins 0..19 resolve [0, 5 ms) in 250 us increments.  Bins 20..25 are
 * [5,10), [10,20), [20,40), [40,60), [60,80), and [80,100) ms.
 * Bin 26 is the explicit >=100 ms overflow bucket.
 */
static inline uint8_t bsf_imu_pull_hist_bin(uint32_t value_us)
{
	if (value_us < BSF_IMU_PULL_FINE_LIMIT_US) {
		return (uint8_t)(value_us / BSF_IMU_PULL_FINE_BIN_US);
	}
	if (value_us < 10000u) {
		return 20u;
	}
	if (value_us < 20000u) {
		return 21u;
	}
	if (value_us < 40000u) {
		return 22u;
	}
	if (value_us < 60000u) {
		return 23u;
	}
	if (value_us < 80000u) {
		return 24u;
	}
	if (value_us < BSF_IMU_PULL_OVERFLOW_US) {
		return 25u;
	}
	return 26u;
}

/*
 * Continuous polling deliberately starts many transactions before the next
 * 200 Hz publication deadline.  Those early pulls have zero positive
 * lateness; the first pull at or after the deadline carries the measured
 * positive lateness.
 */
static inline uint32_t bsf_imu_pull_lateness_us(
	uint64_t actual_start_us, bool have_deadline, uint64_t deadline_us)
{
	uint64_t lateness;

	if (!have_deadline || actual_start_us <= deadline_us) {
		return 0u;
	}
	lateness = actual_start_us - deadline_us;
	return lateness > UINT32_MAX ? UINT32_MAX : (uint32_t)lateness;
}

#endif /* BIOSPUR_IMU_PULL_DIAG_MATH_H */
