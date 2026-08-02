#!/usr/bin/env python3
"""Add B306 reboot/reconnect proof to the pinned fast-OTA generated copy."""

from pathlib import Path
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source match, found {count}")
    return text.replace(old, new, 1)


path = Path(sys.argv[1])
source = path.read_text()

source = replace_once(
    source,
    '#include "ota_image.inc"\n#include "master_ota.h"',
    '#include "ota_image.inc"\n#include "ota_image_state_verify.h"\n#include "master_ota.h"',
    "verification helper include",
)
source = replace_once(
    source,
    '#ifndef APP_MASTER_OTA_DIAG_FIRST_GATE_WRITE_REQ\n',
    '#ifndef APP_MASTER_OTA_RESET_ONLY\n'
    '#define APP_MASTER_OTA_RESET_ONLY 0\n'
    '#endif\n\n'
    '#ifndef APP_MASTER_OTA_DIAG_FIRST_GATE_WRITE_REQ\n',
    "reset-only option",
)
source = replace_once(
    source,
    'static uint8_t dfu_ready_watchdog_redrive_count;\n'
    'K_THREAD_STACK_DEFINE(ota_thread_stack, 3072);',
    'static uint8_t dfu_ready_watchdog_redrive_count;\n'
    'static struct k_work_delayable post_reset_verify_timeout_work;\n'
    'static bool post_reset_verify_pending;\n'
    '#define OTA_POST_RESET_VERIFY_TIMEOUT_MS 60000U\n'
    'static void master_leds_set(bool scan, bool link, bool ota, bool error);\n'
    'K_THREAD_STACK_DEFINE(ota_thread_stack, 3072);',
    "post-reset state",
)
source = replace_once(
    source,
    'static void ota_dfu_ready_watchdog_cancel(const char *reason)\n{',
    '''static void post_reset_verify_timeout_fn(struct k_work *work)
{
\tARG_UNUSED(work);
\tif (!post_reset_verify_pending) {
\t\treturn;
\t}
\tpost_reset_verify_pending = false;
\tota_status = -ETIMEDOUT;
\tota_done = true;
\tota_set_state(OTA_OP_VERIFY_FAILED, "post_reset_reconnect_timeout");
\tmaster_leds_set(false, false, false, true);
\tota_session_set(false, "post_reset_reconnect_timeout");
}

static void post_reset_verify_cancel(void)
{
\t(void)k_work_cancel_delayable(&post_reset_verify_timeout_work);
\tpost_reset_verify_pending = false;
}

static void ota_dfu_ready_watchdog_cancel(const char *reason)
{''',
    "post-reset timeout handlers",
)
source = replace_once(
    source,
    'if (!APP_MASTER_OTA_UPLOAD_ENABLE && !APP_MASTER_OTA_VERIFY_ONLY) {\n'
    '\t\treturn;\n\t}\n\tif (!ota_armed) {',
    'if (!APP_MASTER_OTA_UPLOAD_ENABLE && !APP_MASTER_OTA_VERIFY_ONLY &&\n'
    '\t    !APP_MASTER_OTA_RESET_ONLY && !post_reset_verify_pending) {\n'
    '\t\treturn;\n\t}\n\tif (!ota_armed) {',
    "schedule reset/verify work",
)
source = replace_once(
    source,
    'if (!APP_MASTER_OTA_UPLOAD_ENABLE && !APP_MASTER_OTA_VERIFY_ONLY) {\n'
    '\t\t\tprintk("OTA upload disabled (monitor-only mode)\\n");',
    'if (!APP_MASTER_OTA_UPLOAD_ENABLE && !APP_MASTER_OTA_VERIFY_ONLY &&\n'
    '\t\t    !APP_MASTER_OTA_RESET_ONLY && !post_reset_verify_pending) {\n'
    '\t\t\tprintk("OTA upload disabled (monitor-only mode)\\n");',
    "thread reset/verify work",
)
source = replace_once(
    source,
    '''\t\tif (ota_status) {
\t\t\tprintk("OTA upload gate failed: %d\\n", ota_status);''',
    '''\t\tif (ota_status) {
\t\t\tif (post_reset_verify_pending) {
\t\t\t\tprintk("OTA post-reset image-state read failed: %d\\n", ota_status);
\t\t\t\tpost_reset_verify_cancel();
\t\t\t\tota_done = true;
\t\t\t\tota_set_state(OTA_OP_VERIFY_FAILED, "post_reset_state_read_failed");
\t\t\t\tmaster_leds_set(false, true, false, true);
\t\t\t\tota_session_set(false, "post_reset_state_read_failed");
\t\t\t\tcontinue;
\t\t\t}
\t\t\tprintk("OTA upload gate failed: %d\\n", ota_status);''',
    "post-reset read failure",
)
source = replace_once(
    source,
    '''\t\tif (APP_MASTER_OTA_UPLOAD_ENABLE) {
\t\t\tota_set_state(OTA_OP_UPLOADING, "start");
\t\t}
\t\tif (APP_MASTER_OTA_VERIFY_ONLY) {
\t\t\tprintk("OTA verify-only image-state read complete\\n");
\t\t\tota_done = true;
\t\t\tota_set_state(OTA_OP_VERIFY_PASSED, "image_state_read");
\t\t\tmaster_leds_set(false, true, false, false);
\t\t\tota_session_set(false, "verify_only_done");
\t\t\tcontinue;
\t\t}
\t\tota_started = true;''',
    '''\t\tif (post_reset_verify_pending || APP_MASTER_OTA_VERIFY_ONLY ||
\t\t    APP_MASTER_OTA_RESET_ONLY) {
\t\t\tconst struct bt_dfu_smp_header *rsp =
\t\t\t\t(const struct bt_dfu_smp_header *)smp_rsp_buf;
\t\t\tsize_t payload_len = smp_rsp_len > sizeof(*rsp) ?
\t\t\t\tsmp_rsp_len - sizeof(*rsp) : 0U;
\t\t\tbool verified = payload_len > 0U &&
\t\t\t\tbsf_ota_image_state_verified(smp_rsp_buf + sizeof(*rsp),
\t\t\t\t\tpayload_len, tag_ota_image_image_hash,
\t\t\t\t\tsizeof(tag_ota_image_image_hash));

\t\t\tprintk("OTA image-state verdict: marker=%s hash=%s active=1 confirmed=1\\n",
\t\t\t       APP_MASTER_OTA_PAYLOAD_MARKER,
\t\t\t       verified ? "match" : "missing_or_inactive");
\t\t\tif (!verified) {
\t\t\t\tpost_reset_verify_cancel();
\t\t\t\tota_status = -EILSEQ;
\t\t\t\tota_done = true;
\t\t\t\tota_set_state(OTA_OP_VERIFY_FAILED, "hash_active_confirmed_mismatch");
\t\t\t\tmaster_leds_set(false, true, false, true);
\t\t\t\tota_session_set(false, "image_state_mismatch");
\t\t\t\tcontinue;
\t\t\t}
\t\t\tif (post_reset_verify_pending || APP_MASTER_OTA_VERIFY_ONLY) {
\t\t\t\tbool was_post_reset = post_reset_verify_pending;
\t\t\t\tpost_reset_verify_cancel();
\t\t\t\tota_done = true;
\t\t\t\tota_set_state(OTA_OP_VERIFY_PASSED,
\t\t\t\t\t      was_post_reset ? "reconnected_hash_active_confirmed" :
\t\t\t\t\t      "hash_active_confirmed");
\t\t\t\tmaster_leds_set(false, true, false, false);
\t\t\t\tota_session_set(false, "verified");
\t\t\t\tcontinue;
\t\t\t}
\t\t\tprintk("OTA reset-only preflight passed; requesting cold reboot\\n");
\t\t\tota_status = ota_remote_reset(&dfu_smp);
\t\t\tif (ota_status) {
\t\t\t\tota_done = true;
\t\t\t\tota_set_state(OTA_OP_VERIFY_FAILED, "reset_only_command_failed");
\t\t\t\tota_session_set(false, "reset_only_command_failed");
\t\t\t\tcontinue;
\t\t\t}
\t\t\tpost_reset_verify_pending = true;
\t\t\tota_started = false;
\t\t\tota_done = false;
\t\t\tota_set_state(OTA_OP_REBOOT_PENDING, "reset_only_sent");
\t\t\t(void)k_work_reschedule(&post_reset_verify_timeout_work,
\t\t\t\t\t\tK_MSEC(OTA_POST_RESET_VERIFY_TIMEOUT_MS));
\t\t\tcontinue;
\t\t}
\t\tota_set_state(OTA_OP_UPLOADING, "start");
\t\tota_started = true;''',
    "verified mode dispatch",
)
source = replace_once(
    source,
    '''\t\tprintk("OTA command sequence sent\\n");
\t\tota_set_state(OTA_OP_REBOOT_PENDING, "remote_reset_sent");
\t\tota_done = true;
\t\tota_set_state(OTA_OP_VERIFY_PASSED, "sequence_done");
\t\tmaster_leds_set(false, true, false, false);
\t\tota_session_set(false, "sequence_done");''',
    '''\t\tprintk("OTA command sequence sent\\n");
\t\tota_set_state(OTA_OP_REBOOT_PENDING, "remote_reset_sent");
\t\tpost_reset_verify_pending = true;
\t\tota_started = false;
\t\tota_done = false;
\t\tmaster_leds_set(false, true, false, false);
\t\t(void)k_work_reschedule(&post_reset_verify_timeout_work,
\t\t\t\t\tK_MSEC(OTA_POST_RESET_VERIFY_TIMEOUT_MS));
\t\tprintk("OTA waiting for reboot, reconnect, and hash/active/confirmed proof\\n");''',
    "post-upload reboot verification",
)
source = replace_once(
    source,
    'k_work_init_delayable(&dfu_ready_watchdog_work, ota_dfu_ready_watchdog_fn);\n',
    'k_work_init_delayable(&dfu_ready_watchdog_work, ota_dfu_ready_watchdog_fn);\n'
    '\tk_work_init_delayable(&post_reset_verify_timeout_work, post_reset_verify_timeout_fn);\n',
    "post-reset timeout initialization",
)
source = replace_once(
    source,
    '\tota_seq = 1U;\n\tota_runtime_active = true;',
    '\tota_seq = 1U;\n\tpost_reset_verify_pending = false;\n\tota_runtime_active = true;',
    "post-reset initial state",
)

path.chmod(0o644)
path.write_text(source)
print("post-reset verification transform applied")
