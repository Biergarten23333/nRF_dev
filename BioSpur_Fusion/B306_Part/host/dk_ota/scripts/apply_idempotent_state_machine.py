#!/usr/bin/env python3
"""Make the generated B306 updater state-first, strict, and restart-safe."""

from pathlib import Path
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source match, found {count}")
    return text.replace(old, new, 1)


def replace_between(
    text: str, start: str, end: str, replacement: str, label: str
) -> str:
    first = text.find(start)
    if first < 0:
        raise RuntimeError(f"{label}: start marker not found")
    second = text.find(end, first)
    if second < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:first] + replacement + text[second:]


path = Path(sys.argv[1])
source = path.read_text()

source = replace_once(
    source,
    "#define OTA_POST_RESET_VERIFY_TIMEOUT_MS 60000U\n"
    "static void master_leds_set(bool scan, bool link, bool ota, bool error);",
    "#define OTA_POST_RESET_VERIFY_TIMEOUT_MS 180000U\n"
    "#define OTA_ERASE_MAX_ATTEMPTS 3U\n"
    "#define OTA_ERASE_POLL_ATTEMPTS 3U\n"
    "#define OTA_PENDING_RESET_MAX 2U\n"
    "static uint32_t reacquire_started_ms;\n"
    "static bool reacquire_first_seen_logged;\n"
    "static bool initial_state_probe_pending;\n"
    "static bool full_flow_attempted;\n"
    "static uint8_t pending_reset_count;\n"
    "static void master_leds_set(bool scan, bool link, bool ota, bool error);",
    "hardened state",
)

source = replace_once(
    source,
    """static void post_reset_verify_timeout_fn(struct k_work *work)
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
}""",
    """static void post_reset_verify_timeout_fn(struct k_work *work)
{
\tconst char *detail;

\tARG_UNUSED(work);
\tif (!post_reset_verify_pending) {
\t\treturn;
\t}
\tdetail = initial_state_probe_pending ?
\t\t"initial_state_scan_timeout" : "post_reset_reconnect_timeout";
\tpost_reset_verify_pending = false;
\tinitial_state_probe_pending = false;
\tota_status = -ETIMEDOUT;
\tota_done = true;
\tota_set_state(OTA_OP_VERIFY_FAILED, detail);
\tmaster_leds_set(false, false, false, true);
\tota_session_set(false, detail);
}

static void post_reset_verify_cancel(void)
{
\t(void)k_work_cancel_delayable(&post_reset_verify_timeout_work);
\tpost_reset_verify_pending = false;
\tinitial_state_probe_pending = false;
}""",
    "initial state timeout",
)

source = replace_once(
    source,
    "static int ota_prime_link(struct bt_dfu_smp *smp)\n{",
    """static bool ota_inspect_last_state(struct bsf_ota_image_state *state)
{
\tconst struct bt_dfu_smp_header *rsp =
\t\t(const struct bt_dfu_smp_header *)smp_rsp_buf;
\tsize_t payload_len = smp_rsp_len > sizeof(*rsp) ?
\t\tsmp_rsp_len - sizeof(*rsp) : 0U;
\tbool ok = payload_len > 0U &&
\t\tbsf_ota_image_state_inspect(smp_rsp_buf + sizeof(*rsp),
\t\t\tpayload_len, tag_ota_image_image_hash,
\t\t\tsizeof(tag_ota_image_image_hash), state);

\tprintk("OTA_STATE_READ parsed=%u expected=%u active=%u confirmed=%u "
\t       "pending=%u expected_secondary=%u secondary_present=%u\\n",
\t       ok && state->parsed ? 1U : 0U,
\t       ok && state->expected_found ? 1U : 0U,
\t       ok && state->expected_active ? 1U : 0U,
\t       ok && state->expected_confirmed ? 1U : 0U,
\t       ok && state->expected_pending ? 1U : 0U,
\t       ok && state->expected_secondary ? 1U : 0U,
\t       ok && state->secondary_present ? 1U : 0U);
\treturn ok && state->parsed;
}

static const char *ota_branch_name(enum bsf_ota_image_branch branch)
{
\tswitch (branch) {
\tcase BSF_OTA_IMAGE_ACTIVE_CONFIRMED:
\t\treturn "ACTIVE_CONFIRMED";
\tcase BSF_OTA_IMAGE_ACTIVE_UNCONFIRMED:
\t\treturn "ACTIVE_UNCONFIRMED";
\tcase BSF_OTA_IMAGE_SECONDARY_PENDING:
\t\treturn "SECONDARY_PENDING";
\tcase BSF_OTA_IMAGE_OLD_NO_USABLE_PENDING:
\t\treturn "OLD_NO_USABLE_PENDING";
\tdefault:
\t\treturn "INVALID";
\t}
}

static void ota_finish_pass(const char *detail)
{
\tpost_reset_verify_cancel();
\tota_status = 0;
\tota_done = true;
\tota_started = false;
\tota_set_state(OTA_OP_VERIFY_PASSED, detail);
\tmaster_leds_set(false, true, false, false);
\tota_session_set(false, "verified");
}

static void ota_finish_fail(int status, const char *detail)
{
\tpost_reset_verify_cancel();
\tota_status = status;
\tota_done = true;
\tota_started = false;
\tota_set_state(OTA_OP_VERIFY_FAILED, detail);
\tmaster_leds_set(false, default_conn != NULL, false, true);
\tota_session_set(false, detail);
}

static void ota_arm_reacquire(const char *detail)
{
\tpost_reset_verify_pending = true;
\tinitial_state_probe_pending = false;
\treacquire_started_ms = k_uptime_get_32();
\treacquire_first_seen_logged = false;
\tota_started = false;
\tota_done = false;
\tota_set_state(OTA_OP_REBOOT_PENDING, detail);
\tmaster_leds_set(false, true, false, false);
\t(void)k_work_reschedule(&post_reset_verify_timeout_work,
\t\t\t\tK_MSEC(OTA_POST_RESET_VERIFY_TIMEOUT_MS));
\tprintk("OTA_REACQUIRE armed deadline_ms=%u reason=%s\\n",
\t       OTA_POST_RESET_VERIFY_TIMEOUT_MS, detail);
}

static int ota_prime_link(struct bt_dfu_smp *smp)
{""",
    "state helpers",
)

source = replace_once(
    source,
    "static void ota_try_schedule_start(void)\n{",
    """static int ota_erase_secondary_verified(struct bt_dfu_smp *smp)
{
\tfor (uint8_t attempt = 1U; attempt <= OTA_ERASE_MAX_ATTEMPTS;
\t     ++attempt) {
\t\tint erase_rc;

\t\tprintk("OTA_ERASE attempt=%u/%u response_budget_s=%u\\n",
\t\t       attempt, OTA_ERASE_MAX_ATTEMPTS, OTA_CMD_TIMEOUT_SEC);
\t\terase_rc = ota_erase_secondary_slot(smp);
\t\tif (erase_rc != 0 && erase_rc != -ETIMEDOUT) {
\t\t\tprintk("OTA_ERASE hard_failure rc=%d\\n", erase_rc);
\t\t\treturn erase_rc;
\t\t}
\t\tif (erase_rc == -ETIMEDOUT) {
\t\t\tprintk("OTA_ERASE response_timeout; state proof required\\n");
\t\t}
\t\tfor (uint8_t poll = 1U; poll <= OTA_ERASE_POLL_ATTEMPTS;
\t\t     ++poll) {
\t\t\tstruct bsf_ota_image_state state;
\t\t\tint state_rc;

\t\t\tk_msleep(300);
\t\t\tstate_rc = ota_read_image_state(smp, false);
\t\t\tif (state_rc == 0 && ota_inspect_last_state(&state) &&
\t\t\t    !state.secondary_present) {
\t\t\t\tprintk("OTA_ERASE verified attempt=%u poll=%u "
\t\t\t\t       "secondary_present=0\\n", attempt, poll);
\t\t\t\treturn 0;
\t\t\t}
\t\t\tprintk("OTA_ERASE not_verified attempt=%u poll=%u "
\t\t\t       "state_rc=%d\\n", attempt, poll, state_rc);
\t\t}
\t}
\tprintk("OTA_ERASE FAILED bounded_retries_exhausted\\n");
\treturn -ETIMEDOUT;
}

static void ota_try_schedule_start(void)
{""",
    "verified erase",
)

new_thread = r"""static void ota_thread_fn(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	while (1) {
		struct bsf_ota_image_state state;
		enum bsf_ota_image_branch branch;

		k_sem_take(&ota_start_sem, K_FOREVER);
		ota_start_queued = false;
		if (!APP_MASTER_OTA_UPLOAD_ENABLE && !APP_MASTER_OTA_VERIFY_ONLY &&
		    !APP_MASTER_OTA_RESET_ONLY && !post_reset_verify_pending) {
			printk("OTA upload disabled (monitor-only mode)\n");
			continue;
		}
		if (!ota_ready || !mtu_ready || ota_started || ota_done ||
		    default_conn == NULL) {
			continue;
		}

		printk("OTA start gate: mtu=%u conn=%p\n",
		       (unsigned int)bt_gatt_get_mtu(default_conn), default_conn);
		k_msleep(300);
		ota_upload_gate_ok = false;
		ota_status = ota_wait_upload_ready(&dfu_smp);
		if (ota_status != 0 || !ota_inspect_last_state(&state)) {
			printk("OTA state-first read failed: rc=%d\n", ota_status);
			if (post_reset_verify_pending && default_conn == NULL) {
				printk("OTA state read deferred: reconnect still pending\n");
				continue;
			}
			ota_finish_fail(ota_status != 0 ? ota_status : -EBADMSG,
					"state_first_read_failed");
			continue;
		}
		branch = bsf_ota_image_state_branch(&state);
		printk("OTA_BRANCH:%s post_reset=%u initial_probe=%u full_flow=%u updater_confirm=0\n",
		       ota_branch_name(branch),
		       post_reset_verify_pending ? 1U : 0U,
		       initial_state_probe_pending ? 1U : 0U,
		       full_flow_attempted ? 1U : 0U);

		if (APP_MASTER_OTA_VERIFY_ONLY) {
			if (branch == BSF_OTA_IMAGE_ACTIVE_CONFIRMED) {
				ota_finish_pass("verify_only_active_confirmed");
			} else {
				ota_finish_fail(-EILSEQ, "verify_only_mismatch");
			}
			continue;
		}

		if (APP_MASTER_OTA_RESET_ONLY) {
			if (post_reset_verify_pending &&
			    branch == BSF_OTA_IMAGE_ACTIVE_CONFIRMED) {
				ota_finish_pass("reset_only_reacquired");
				continue;
			}
			if (branch != BSF_OTA_IMAGE_ACTIVE_CONFIRMED) {
				ota_finish_fail(-EILSEQ, "reset_only_preflight_mismatch");
				continue;
			}
			ota_status = ota_remote_reset(&dfu_smp);
			if (ota_status != 0) {
				ota_finish_fail(ota_status, "reset_only_command_failed");
				continue;
			}
			ota_arm_reacquire("reset_only_sent");
			continue;
		}

		switch (branch) {
		case BSF_OTA_IMAGE_ACTIVE_CONFIRMED:
		{
			bool was_initial_probe = initial_state_probe_pending;

			printk("OTA_FINDING:APP_CONFIRMED active payload was confirmed "
			       "without updater confirm command\n");
			printk("OTA image-state verdict: marker=%s hash=match "
			       "active=1 confirmed=1\n",
			       APP_MASTER_OTA_PAYLOAD_MARKER);
			ota_finish_pass(was_initial_probe ?
					"initial_active_confirmed" :
					post_reset_verify_pending ?
					"reacquired_active_confirmed" :
					"active_confirmed");
			break;
		}

		case BSF_OTA_IMAGE_ACTIVE_UNCONFIRMED:
			printk("OTA_ACTION:handoff_app_roundtrip_confirm\n");
			printk("OTA image-state verdict: marker=%s hash=match "
			       "active=1 confirmed=0 updater_confirm=0\n",
			       APP_MASTER_OTA_PAYLOAD_MARKER);
			ota_finish_pass("active_unconfirmed_app_confirmation_required");
			break;

		case BSF_OTA_IMAGE_SECONDARY_PENDING:
			if (pending_reset_count >= OTA_PENDING_RESET_MAX) {
				ota_finish_fail(-ELOOP, "pending_reset_bound_exhausted");
				break;
			}
			++pending_reset_count;
			printk("OTA_ACTION:reset_pending count=%u/%u\n",
			       pending_reset_count, OTA_PENDING_RESET_MAX);
			ota_status = ota_remote_reset(&dfu_smp);
			if (ota_status != 0) {
				ota_finish_fail(ota_status, "pending_reset_failed");
				break;
			}
			ota_arm_reacquire("secondary_pending_reset");
			break;

		case BSF_OTA_IMAGE_OLD_NO_USABLE_PENDING:
			if (full_flow_attempted) {
				ota_finish_fail(-EILSEQ,
						"old_active_after_full_flow");
				break;
			}
			post_reset_verify_cancel();
			full_flow_attempted = true;
			ota_set_state(OTA_OP_UPLOADING, "full_flow");
			ota_started = true;
			printk("OTA_ACTION:full_flow\n");
			ota_status = ota_prime_link(&dfu_smp);
			if (ota_status != 0 && ota_status != -ETIMEDOUT) {
				ota_finish_fail(ota_status, "prime_failed");
				break;
			}
			ota_status = ota_erase_secondary_verified(&dfu_smp);
			if (ota_status != 0) {
				ota_finish_fail(ota_status,
						"erase_not_verified");
				break;
			}
			ota_status = ota_upload_image(&dfu_smp);
			if (ota_status != 0) {
				ota_finish_fail(ota_status, "upload_failed");
				break;
			}
			ota_set_state(OTA_OP_UPLOAD_COMPLETE, "upload_done");
			ota_status = ota_read_image_state(&dfu_smp, false);
			if (ota_status != 0 ||
			    !ota_inspect_last_state(&state)) {
				ota_finish_fail(ota_status != 0 ? ota_status : -EBADMSG,
						"post_upload_state_read_failed");
				break;
			}
			ota_status = ota_schedule_pending(&dfu_smp);
			if (ota_status != 0) {
				ota_finish_fail(ota_status,
						"schedule_pending_failed");
				break;
			}
			ota_status = ota_remote_reset(&dfu_smp);
			if (ota_status != 0) {
				ota_finish_fail(ota_status, "reset_failed");
				break;
			}
			ota_arm_reacquire("full_flow_reset");
			break;

		default:
			ota_finish_fail(-EBADMSG, "invalid_image_state");
			break;
		}
	}
}

"""

source = replace_between(
    source,
    "static void ota_thread_fn(void *a, void *b, void *c)\n",
    "static void dfu_error_cb(struct bt_dfu_smp *smp, int err)\n",
    new_thread,
    "state-machine thread",
)

source = replace_once(
    source,
    "\taccept = accept &&\n"
    "\t\t (connectable || (APP_MASTER_OTA_BSF_PROFILE && eval.strict_identity_ok)) &&",
    "\tif (post_reset_verify_pending && eval.strict_identity_ok &&\n"
    "\t    !reacquire_first_seen_logged) {\n"
    "\t\treacquire_first_seen_logged = true;\n"
    "\t\tprintk(\"OTA_REACQUIRE first_seen_advertisement_ms=%u "
    "addr=%s name=%s\\n\",\n"
    "\t\t       (unsigned int)(k_uptime_get_32() - reacquire_started_ms),\n"
    "\t\t       addr, identity_name[0] != '\\0' ? identity_name : \"-\");\n"
    "\t}\n"
    "\taccept = accept &&\n"
    "\t\t (connectable || (APP_MASTER_OTA_BSF_PROFILE && eval.strict_identity_ok)) &&",
    "reacquire first advertisement",
)

source = replace_once(
    source,
    "\tdfu_ready_watchdog_redrive_count = 0U;\n"
    "\tif (default_conn != NULL) {",
    "\tdfu_ready_watchdog_redrive_count = 0U;\n"
    "\tpost_reset_verify_pending = true;\n"
    "\tinitial_state_probe_pending = true;\n"
    "\treacquire_started_ms = k_uptime_get_32();\n"
    "\treacquire_first_seen_logged = false;\n"
    "\tfull_flow_attempted = false;\n"
    "\tpending_reset_count = 0U;\n"
    "\tif (default_conn != NULL) {",
    "initiate state reset",
)

source = replace_once(
    source,
    '\tota_session_set(true, "initiate");\n'
    '\tprintk("OTA initiate: scan restarted (passive), armed=1\\n");',
    '\tota_session_set(true, "initiate");\n'
    '\t(void)k_work_reschedule(&post_reset_verify_timeout_work,\n'
    '\t\t\t\tK_MSEC(OTA_POST_RESET_VERIFY_TIMEOUT_MS));\n'
    '\tprintk("OTA_INITIAL_SCAN armed deadline_ms=%u\\n",\n'
    '\t       OTA_POST_RESET_VERIFY_TIMEOUT_MS);\n'
    '\tprintk("OTA initiate: scan restarted (passive), armed=1\\n");',
    "initial scan timeout arm",
)

source = replace_once(
    source,
    "\tpost_reset_verify_pending = false;\n"
    "\tota_runtime_active = true;",
    "\tpost_reset_verify_pending = false;\n"
    "\tinitial_state_probe_pending = false;\n"
    "\treacquire_started_ms = 0U;\n"
    "\treacquire_first_seen_logged = false;\n"
    "\tfull_flow_attempted = false;\n"
    "\tpending_reset_count = 0U;\n"
    "\tota_runtime_active = true;",
    "bootstrap state",
)

source = replace_once(
    source,
    "\tif (APP_MASTER_OTA_AUTO_START) {\n"
    "\t\terr = master_ota_initiate();",
    "\tif (APP_MASTER_OTA_AUTO_START) {\n"
    "\t\tprintk(\"OTA auto-start delay_ms=15000 for RTT logger attach\\n\");\n"
    "\t\tk_sleep(K_MSEC(15000));\n"
    "\t\terr = master_ota_initiate();",
    "RTT logger attach window",
)

path.chmod(0o644)
path.write_text(source)
print("idempotent state-machine transform applied")
