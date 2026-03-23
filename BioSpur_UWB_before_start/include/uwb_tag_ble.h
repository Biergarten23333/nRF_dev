#ifndef UWB_TAG_BLE_H
#define UWB_TAG_BLE_H

#include <stdbool.h>

int uwb_tag_ble_init(void);
int uwb_tag_ble_publish_status(const char *line);
bool uwb_tag_ble_ota_active(void);

#endif /* UWB_TAG_BLE_H */
