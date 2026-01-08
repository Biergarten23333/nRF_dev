#include <zephyr/kernel.h>
#include <zephyr/sys/reboot.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>

#include "cdc_async.h"
#include "app_ble.h"
#include "app_cmd.h"

K_THREAD_STACK_DEFINE(app_cmd_stack, 1024);
static struct k_thread app_cmd_thread;

/* ========== 小工具：忽略前导空格 + 不区分大小写匹配命令字 ========== */

static const char *skip_spaces(const char *s)
{
    while (*s == ' ' || *s == '\t') {
        s++;
    }
    return s;
}

/* 判断 cmd 是否以 word 开头（大小写不敏感），并且后面是空格或结束
 * 例如：
 *   cmd = "  TSYNC 123", word="tsync" → true，返回 true
 *   cmd = "TSYNCHHH",   word="tsync" → false
 */
static bool cmd_starts_with_word(const char *cmd, const char *word)
{
    cmd = skip_spaces(cmd);

    while (*word) {
        char c1 = *cmd;
        char c2 = *word;

        if (c1 == '\0') {
            return false;
        }
        /* 转小写比较 */
        if (tolower((unsigned char)c1) != tolower((unsigned char)c2)) {
            return false;
        }
        cmd++;
        word++;
    }

    /* word 比完了，cmd 当前字符必须是空白或 '\0' 才算匹配完整单词 */
    if (*cmd == '\0' || *cmd == ' ' || *cmd == '\t') {
        return true;
    }
    return false;
}

/* 从一行命令中取出命令字后面的“尾部参数串”，自动跳过命令自身和后面的空格。
 * 例如：
 *   line = "  TSYNC  12345"
 *   word = "tsync"
 *   返回指向 "12345"
 */
static const char *get_args_after_word(const char *line, const char *word)
{
    line = skip_spaces(line);

    /* 先跳过命令字本身 */
    while (*word && *line) {
        line++;
        word++;
    }

    /* 再跳过紧跟着的空格 */
    return skip_spaces(line);
}

/* ========== 命令处理 ========== */

static void handle_cmd(const char *cmd_in)
{
    const char *cmd = skip_spaces(cmd_in);

    if (*cmd == '\0') {
        return;
    }

    /* ---- scan ---- */
    if (cmd_starts_with_word(cmd, "scan")) {
        cdc_printf("CMD: scan\r\n");
        app_ble_start_scan();
        return;
    }

    /* ---- conn ---- */
    if (cmd_starts_with_word(cmd, "conn")) {
        cdc_printf("CMD: conn\r\n");
        app_ble_connect_whitelist();
        return;
    }

    /* ---- reboot ---- */
    if (cmd_starts_with_word(cmd, "reboot")) {
        cdc_printf("CMD: reboot -> sys_reboot\r\n");
        sys_reboot(SYS_REBOOT_COLD);
        return;
    }

    /* ---- stream_on / stream_off ---- */
    if (cmd_starts_with_word(cmd, "stream_on")) {
        cdc_printf("CMD: stream_on\r\n");
        cdc_async_enable(true);
        return;
    }

    if (cmd_starts_with_word(cmd, "stream_off")) {
        cdc_printf("CMD: stream_off\r\n");
        cdc_async_enable(false);
        return;
    }

    /* ---- disc / disconnect ---- */
    if (cmd_starts_with_word(cmd, "disc") ||
        cmd_starts_with_word(cmd, "disconnect")) {
        cdc_printf("CMD: disconnect all\r\n");
        app_ble_disconnect_all();
        return;
    }

    /* ---- TSYNC / tsync ----
     *
     * 支持两种用法：
     *   tsync            -> 用 RX 的 k_uptime_get() 做 host_ms
     *   tsync <host_ms>  -> 用 PC 传过来的 host_ms（你现在用的）
     */
    if (cmd_starts_with_word(cmd, "tsync")) {
        const char *args = get_args_after_word(cmd, "tsync");
        uint64_t host_ms = 0;

        if (*args == '\0') {
            /* 无参数：用 RX 自己的 uptime 作 host_ms */
            host_ms = (uint64_t)k_uptime_get();
            cdc_printf("CMD: TSYNC with local uptime=%llu ms\r\n",
                       (unsigned long long)host_ms);
        } else {
            /* 有参数：解析十进制 host_ms */
            unsigned long long tmp = 0ULL;
            int n = sscanf(args, "%llu", &tmp);
            if (n != 1) {
                cdc_printf("CMD TSYNC parse error. Usage:\r\n");
                cdc_printf("  TSYNC <host_ms>\r\n");
                return;
            }
            host_ms = (uint64_t)tmp;
            cdc_printf("CMD: TSYNC host_ms=%llu\r\n",
                       (unsigned long long)host_ms);
        }

        app_ble_tsync_all(host_ms);
        return;
    }

    /* ---- RSLOT / rslot <slot> <fps> <phase_ms> ---- */
    if (cmd_starts_with_word(cmd, "rslot")) {
        const char *args = get_args_after_word(cmd, "rslot");
        int slot = 0;
        unsigned int fps = 0;
        unsigned int phase_ms = 0;

        int n = sscanf(args, "%d %u %u", &slot, &fps, &phase_ms);
        if (n == 3) {
            cdc_printf("CMD: RSLOT slot=%d fps=%u phase=%u\r\n",
                       slot, fps, phase_ms);
            app_ble_rslot(slot, (uint16_t)fps, (uint16_t)phase_ms);
        } else {
            cdc_printf("CMD RSLOT parse error. Usage:\r\n");
            cdc_printf("  RSLOT <slot> <fps> <phase_ms>\r\n");
        }
        return;
    }

    /* ---- 未知命令 ---- */
    cdc_printf("CMD: unknown '%s'\r\n", cmd_in);
}

/* ========== CDC 读取线程：按“整行”处理命令 ========== */

static void app_cmd_thread_fn(void *a, void *b, void *c)
{
    ARG_UNUSED(a);
    ARG_UNUSED(b);
    ARG_UNUSED(c);

    char buf[64];
    int  pos = 0;

    cdc_printf("app_cmd thread started. Type commands: scan/conn/disc/TSYNC/RSLOT...\r\n");

    while (1) {
        uint8_t ch;
        int ret = cdc_async_poll_in(&ch);
        if (ret == 0) {
            if (ch == '\r' || ch == '\n') {
                if (pos > 0) {
                    buf[pos] = '\0';
                    handle_cmd(buf);
                    pos = 0;
                }
                continue;
            }

            if (pos < (int)sizeof(buf) - 1) {
                buf[pos++] = (char)ch;
                buf[pos]   = '\0';
            } else {
                /* 太长了，直接丢掉这一行，避免一直占满缓冲 */
                pos = 0;
                cdc_printf("CMD: line too long, dropped.\r\n");
            }
        } else {
            k_msleep(10);
        }
    }
}

int app_cmd_init(void)
{
    k_thread_create(&app_cmd_thread,
                    app_cmd_stack,
                    K_THREAD_STACK_SIZEOF(app_cmd_stack),
                    app_cmd_thread_fn,
                    NULL, NULL, NULL,
                    5, 0, K_NO_WAIT);

    k_thread_name_set(&app_cmd_thread, "app_cmd");
    return 0;
}
