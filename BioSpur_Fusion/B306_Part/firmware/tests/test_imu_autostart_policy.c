#include <assert.h>
#include <stdint.h>
#include "imu_autostart_policy.h"
#include "biospur_fusion_ble.h"

int main(void)
{
	assert(BSF_IMU_BATCH_DEFAULT == 10u);
	assert(!bsf_imu_autostart_eligible(false, 0u, 2000u));
	assert(!bsf_imu_autostart_eligible(false, BSL_FLAG_SUPERFRAME_VALID, 1999u));
	assert(bsf_imu_autostart_eligible(false, BSL_FLAG_SUPERFRAME_VALID, 2000u));
	assert(!bsf_imu_autostart_eligible(true, BSL_FLAG_SUPERFRAME_VALID, 2000u));
	/* Lock loss after the one-shot decision cannot create a stop action. */
	assert(!bsf_imu_autostart_eligible(true, 0u, 2000u));
	return 0;
}
