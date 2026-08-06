#ifndef BIOSPUR_IMU_AUTOSTART_POLICY_H
#define BIOSPUR_IMU_AUTOSTART_POLICY_H

#include <stdbool.h>
#include <stdint.h>
#include "biospur_link.h"

/*
 * The ten-board POR observation put the latest first successful boot-health
 * recovery at 1756.631 ms. Round to 2 s: 243.369 ms (13.9%) margin.
 */
#define BSF_IMU_POWER_ON_SETTLE_MS 2000u

static inline bool bsf_imu_autostart_eligible(bool touched, uint8_t flags,
					      uint32_t uptime_ms)
{
	return !touched && (flags & BSL_FLAG_SUPERFRAME_VALID) != 0u &&
	       uptime_ms >= BSF_IMU_POWER_ON_SETTLE_MS;
}

#endif
