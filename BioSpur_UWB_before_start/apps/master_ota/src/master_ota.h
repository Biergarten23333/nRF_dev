#ifndef BIOSPUR_MASTER_OTA_H_
#define BIOSPUR_MASTER_OTA_H_

#ifdef __cplusplus
extern "C" {
#endif

int master_ota_run(void);
void master_ota_target_reset(void);
int master_ota_target_set_token(int token_id);
int master_ota_target_set_name(const char *name);
int master_ota_target_set_prefix(const char *prefix);
void master_ota_target_print(void);

#ifdef __cplusplus
}
#endif

#endif /* BIOSPUR_MASTER_OTA_H_ */
