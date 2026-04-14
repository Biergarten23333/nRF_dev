#ifndef MASTER_MULTI_APP_H
#define MASTER_MULTI_APP_H

#include <stdbool.h>
#include <stddef.h>

enum master_runtime_target_kind {
	MASTER_TARGET_UNKNOWN = 0,
	MASTER_TARGET_ANCHOR = 1,
	MASTER_TARGET_TAG = 2,
};

enum master_log_mode {
	MASTER_LOG_MODE_RECV = 0,
	MASTER_LOG_MODE_OTA = 1,
	MASTER_LOG_MODE_AUTOPOS = 2,
};

void master_set_scan_only_mode(void);
void master_set_connect_and_start_mode(void);
void master_set_background_gate(bool allow, const char *reason);
void master_disconnect_all_peers(void);
void master_quiesce_peers(void);
void master_stop_discovery(void);
void master_restart_discovery(void);
void master_process_connect_pending(void);
void master_process_setup_pending(void);
void master_set_log_mode(enum master_log_mode mode);
void master_set_runtime_target_kind(enum master_runtime_target_kind kind);
void master_set_runtime_target_token(int token);
void master_set_runtime_target_name(const char *name);
void master_set_runtime_target_prefix(const char *prefix);
void master_set_runtime_target_uuid(const char *uuid_hex);
void master_dump_ready_state(void);
int master_anchor_ctrl_ready_count(void);
int master_anchor_ctrl_target_peer_count(void);
int master_connection_count(void);
int master_anchor_ctrl_read_state(char *out, size_t out_len);
int master_anchor_ctrl_read_result(char *out, size_t out_len);
int master_send_command_now(const char *cmd);
int master_set_one_shot_command(const char *cmd, bool send_now);
void master_clear_one_shot_command(void);
void master_print_one_shot_command(void);

#endif /* MASTER_MULTI_APP_H */
