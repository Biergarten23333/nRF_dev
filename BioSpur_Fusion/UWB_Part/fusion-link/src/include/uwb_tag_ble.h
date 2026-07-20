#ifndef UWB_TAG_BLE_H
#define UWB_TAG_BLE_H

#include <stdbool.h>
#include <stdint.h>

#include "uwb_tdma.h"

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

enum uwb_tag_ble_cal_status {
	UWB_TAG_BLE_CAL_STATUS_OK = 0,
	UWB_TAG_BLE_CAL_STATUS_REJECT = 1,
	UWB_TAG_BLE_CAL_STATUS_TIMEOUT = 2,
	UWB_TAG_BLE_CAL_STATUS_ERROR = 3,
};

struct uwb_tag_ble_cal_range {
	uint32_t sweep;
	int32_t raw_mm;
	uint32_t filt_mm;
	uint32_t ok_count;
	uint32_t fail_count;
	uint8_t anchor_id;
	uint8_t status;
	uint8_t quality_percent;
};

int uwb_tag_ble_init(void);
uint16_t uwb_tag_ble_identity_code(void);
bool uwb_tag_ble_identity_is_nvs(void);
uint8_t uwb_tag_ble_tag_id(void);
const char *uwb_tag_ble_device_name(void);
bool uwb_tag_ble_tdma_slot_override_get(uint8_t *slot_index);
int uwb_tag_ble_tdma_slot_override_store(uint8_t slot_index);
bool uwb_tag_ble_runtime_config_get(struct uwb_tag_runtime_params *params);
int uwb_tag_ble_runtime_config_store(const struct uwb_tag_runtime_params *params);
int uwb_tag_ble_publish_status(const char *line);
int uwb_tag_ble_publish_sample(const struct uwb_tag_ble_sample *sample);
int uwb_tag_ble_publish_calibration_range(const struct uwb_tag_ble_cal_range *sample);
void uwb_tag_ble_set_tx_paused(bool paused);
bool uwb_tag_ble_ota_active(void);
bool uwb_tag_ble_tr_enabled(void);
void uwb_tag_ble_publish_link_status(void);

#endif /* UWB_TAG_BLE_H */
