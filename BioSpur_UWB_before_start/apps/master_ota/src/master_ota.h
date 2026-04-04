#ifndef BIOSPUR_MASTER_OTA_H_
#define BIOSPUR_MASTER_OTA_H_

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

int master_ota_run(void);
void master_ota_target_reset(void);
int master_ota_target_set_token(int token_id);
int master_ota_target_set_name(const char *name);
int master_ota_target_set_prefix(const char *prefix);
int master_ota_target_set_uuid(const char *uuid_hex);
void master_ota_target_print(void);
int master_ota_initiate(void);
void master_ota_set_expect_nus(bool expect_nus);
void master_ota_prepare_mode_switch(void);

#ifdef __cplusplus
}
#endif

#endif /* BIOSPUR_MASTER_OTA_H_ */
