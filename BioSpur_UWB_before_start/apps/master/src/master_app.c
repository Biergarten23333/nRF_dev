#include <errno.h>
#include <stdlib.h>

#include <zephyr/shell/shell.h>
#include <zephyr/sys/printk.h>

#include "uwb_control_proto.h"

#ifndef APP_MASTER_TARGET_ANCHOR
#define APP_MASTER_TARGET_ANCHOR 4U
#endif

static uint8_t master_seq;

static void master_dump_frame(const struct shell *sh,
                              const struct uwb_ctrl_frame *frame)
{
    uint8_t encoded[UWB_CTRL_FRAME_MAX_LEN];
    size_t encoded_len = uwb_ctrl_encode_frame(frame, encoded, sizeof(encoded));

    shell_print(sh, "target_anchor=%u type=0x%02x seq=%u payload_len=%u",
                APP_MASTER_TARGET_ANCHOR, frame->type, frame->seq,
                frame->payload_len);

    shell_fprintf(sh, SHELL_NORMAL, "wire frame:");
    for (size_t i = 0; i < encoded_len; ++i) {
        shell_fprintf(sh, SHELL_NORMAL, " %02x", encoded[i]);
    }
    shell_fprintf(sh, SHELL_NORMAL, "\n");
    shell_print(sh,
                "transport=stub, next step is wiring UART from nRF54L15 DK to Anchor E");
}

static int cmd_ping(const struct shell *sh, size_t argc, char **argv)
{
    struct uwb_ctrl_frame frame;

    ARG_UNUSED(argc);
    ARG_UNUSED(argv);

    if (!uwb_ctrl_build_ping(&frame, master_seq++)) {
        return -EINVAL;
    }

    master_dump_frame(sh, &frame);
    return 0;
}

static int cmd_stop(const struct shell *sh, size_t argc, char **argv)
{
    struct uwb_ctrl_frame frame;

    ARG_UNUSED(argc);
    ARG_UNUSED(argv);

    if (!uwb_ctrl_build_stop(&frame, master_seq++)) {
        return -EINVAL;
    }

    master_dump_frame(sh, &frame);
    return 0;
}

static int cmd_single_range(const struct shell *sh, size_t argc, char **argv)
{
    struct uwb_ctrl_frame frame;
    long anchor_id;

    if (argc != 2U) {
        shell_error(sh, "usage: uwb single_range <anchor_id>");
        return -EINVAL;
    }

    anchor_id = strtol(argv[1], NULL, 0);
    if (anchor_id < 0 || anchor_id > 255) {
        shell_error(sh, "invalid anchor id");
        return -EINVAL;
    }

    if (!uwb_ctrl_build_single_range(&frame, master_seq++, (uint8_t)anchor_id)) {
        return -EINVAL;
    }

    master_dump_frame(sh, &frame);
    return 0;
}

static int cmd_sweep(const struct shell *sh, size_t argc, char **argv)
{
    struct uwb_ctrl_frame frame;
    uint8_t anchor_ids[UWB_CTRL_MAX_ANCHOR_IDS];

    if (argc < 2U || (argc - 1U) > UWB_CTRL_MAX_ANCHOR_IDS) {
        shell_error(sh, "usage: uwb sweep <anchor_id> [anchor_id...]");
        return -EINVAL;
    }

    for (size_t i = 1U; i < argc; ++i) {
        long value = strtol(argv[i], NULL, 0);

        if (value < 0 || value >= UWB_CTRL_MAX_ANCHOR_IDS) {
            shell_error(sh, "invalid anchor id: %s", argv[i]);
            return -EINVAL;
        }
        anchor_ids[i - 1U] = (uint8_t)value;
    }

    if (!uwb_ctrl_build_start_sweep(&frame, master_seq++, anchor_ids,
                                    argc - 1U)) {
        return -EINVAL;
    }

    master_dump_frame(sh, &frame);
    return 0;
}

static int cmd_autopos(const struct shell *sh, size_t argc, char **argv)
{
    struct uwb_ctrl_frame frame;
    uint8_t anchor_ids[UWB_CTRL_MAX_ANCHOR_IDS];

    if (argc < 3U || (argc - 1U) > UWB_CTRL_MAX_ANCHOR_IDS) {
        shell_error(sh, "usage: uwb autopos <anchor_id> <anchor_id> [...]");
        return -EINVAL;
    }

    for (size_t i = 1U; i < argc; ++i) {
        long value = strtol(argv[i], NULL, 0);

        if (value < 0 || value >= UWB_CTRL_MAX_ANCHOR_IDS) {
            shell_error(sh, "invalid anchor id: %s", argv[i]);
            return -EINVAL;
        }
        anchor_ids[i - 1U] = (uint8_t)value;
    }

    if (!uwb_ctrl_build_start_autopos(&frame, master_seq++, anchor_ids,
                                      argc - 1U)) {
        return -EINVAL;
    }

    master_dump_frame(sh, &frame);
    return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(
    sub_uwb, SHELL_CMD_ARG(ping, NULL, "Build a ping control frame", cmd_ping, 1,
                           0),
    SHELL_CMD_ARG(stop, NULL, "Build a stop control frame", cmd_stop, 1, 0),
    SHELL_CMD_ARG(single_range, NULL,
                  "Build a single range request for one anchor",
                  cmd_single_range, 2, 0),
    SHELL_CMD_ARG(sweep, NULL,
                  "Build a multi-anchor sweep request, example: uwb sweep 4 5 6 7",
                  cmd_sweep, 2, UWB_CTRL_MAX_ANCHOR_IDS - 1U),
    SHELL_CMD_ARG(autopos, NULL,
                  "Build an auto-positioning request, example: uwb autopos 4 5 6 7",
                  cmd_autopos, 3, UWB_CTRL_MAX_ANCHOR_IDS - 1U),
    SHELL_SUBCMD_SET_END);

SHELL_CMD_REGISTER(uwb, &sub_uwb, "BioSpur external master controls", NULL);

int master_app_run(void)
{
    printk("BioSpur master ready on nRF54L15 DK\n");
    printk("target master anchor=%u\n", APP_MASTER_TARGET_ANCHOR);
    printk("use shell commands: uwb ping | uwb sweep 4 5 6 7 | uwb autopos 4 5 6 7\n");
    return 0;
}
