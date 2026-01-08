/* app_cmd.h
 *
 * CDC 命令解析模块对外接口
 */

#ifndef APP_CMD_H
#define APP_CMD_H

/* 处理一条命令字符串，例如："scan" / "conn" / "TSYNC 12345" 等 */
void app_cmd_handle(const char *cmd);

#endif /* APP_CMD_H */

