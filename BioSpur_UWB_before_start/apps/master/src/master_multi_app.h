#ifndef MASTER_MULTI_APP_H
#define MASTER_MULTI_APP_H

#include <stdbool.h>

enum master_runtime_target_kind {
	MASTER_TARGET_UNKNOWN = 0,
	MASTER_TARGET_ANCHOR = 1,
	MASTER_TARGET_TAG = 2,
};

void master_set_scan_only_mode(void);
void master_set_connect_and_start_mode(void);
void master_set_background_gate(bool allow, const char *reason);
void master_disconnect_all_peers(void);
void master_restart_discovery(void);
void master_set_runtime_target_kind(enum master_runtime_target_kind kind);
void master_set_runtime_target_token(int token);
void master_set_runtime_target_uuid(const char *uuid_hex);
int master_send_command_now(const char *cmd);
int master_set_one_shot_command(const char *cmd, bool send_now);
void master_clear_one_shot_command(void);
void master_print_one_shot_command(void);

#endif /* MASTER_MULTI_APP_H */
