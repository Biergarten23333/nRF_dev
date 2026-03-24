#ifndef UWB_TAG_BLE_H
#define UWB_TAG_BLE_H

#include <stdbool.h>
#include <stdint.h>

enum uwb_tag_ble_plan_code {
	UWB_TAG_BLE_PLAN_TRACK = 0,
	UWB_TAG_BLE_PLAN_FULL = 1,
	UWB_TAG_BLE_PLAN_FIXED = 2,
	UWB_TAG_BLE_PLAN_UNKNOWN = 255,
};

struct uwb_tag_ble_sample {
	uint32_t sweep;
	int32_t x_mm;
	int32_t y_mm;
	int32_t z_mm;
	uint16_t rms_mm;
	uint16_t max_mm;
	uint16_t motion_dt_ms;
	uint8_t anchor_mask;
	uint8_t plan_code;
	bool motion_valid;
};

int uwb_tag_ble_init(void);
int uwb_tag_ble_publish_status(const char *line);
int uwb_tag_ble_publish_sample(const struct uwb_tag_ble_sample *sample);
bool uwb_tag_ble_ota_active(void);

#endif /* UWB_TAG_BLE_H */
