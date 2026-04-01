#ifndef MASTER_MULTI_APP_H
#define MASTER_MULTI_APP_H

#include <stdbool.h>

void master_set_scan_only_mode(void);
void master_set_connect_and_start_mode(void);
void master_disconnect_all_peers(void);
void master_restart_discovery(void);
int master_send_command_now(const char *cmd);
int master_set_one_shot_command(const char *cmd, bool send_now);
void master_clear_one_shot_command(void);
void master_print_one_shot_command(void);

#endif /* MASTER_MULTI_APP_H */
