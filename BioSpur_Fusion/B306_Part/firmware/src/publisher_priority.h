#ifndef BIOSPUR_PUBLISHER_PRIORITY_H_
#define BIOSPUR_PUBLISHER_PRIORITY_H_

#include <stdbool.h>

enum bsf_publish_class {
	BSF_PUBLISH_NONE = 0,
	BSF_PUBLISH_CTL,
	BSF_PUBLISH_UWB,
	BSF_PUBLISH_IMU,
};

static inline enum bsf_publish_class
bsf_publish_select(bool ctl_available, bool uwb_available,
		   bool imu_available)
{
	if (ctl_available) {
		return BSF_PUBLISH_CTL;
	}
	if (uwb_available) {
		return BSF_PUBLISH_UWB;
	}
	if (imu_available) {
		return BSF_PUBLISH_IMU;
	}
	return BSF_PUBLISH_NONE;
}

#endif /* BIOSPUR_PUBLISHER_PRIORITY_H_ */
