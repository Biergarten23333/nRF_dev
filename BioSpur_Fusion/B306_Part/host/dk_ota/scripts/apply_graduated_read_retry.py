#!/usr/bin/env python3
"""Harden the generated B306 updater's SMP read path without retrying writes."""

from pathlib import Path
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source match, found {count}")
    return text.replace(old, new, 1)


path = Path(sys.argv[1])
source = path.read_text()

# The B306 SMP transport asks for 7.5--11.25 ms after the first SMP write.
# Match that throughput-oriented interval from connection creation onward, but
# never accept the deployed peripheral's 42-unit (420 ms) supervision request.
source = replace_once(
    source,
    "static const struct bt_le_conn_param *const fast_conn_params =\n"
    "\tBT_LE_CONN_PARAM(6, 6, 0, 400);",
    "static const struct bt_le_conn_param *const fast_conn_params =\n"
    "\tBT_LE_CONN_PARAM(6, 9, 0, 2000);",
    "fast connection parameters",
)

source = replace_once(
    source,
    "#define OTA_ERASE_MAX_ATTEMPTS 3U\n"
    "#define OTA_ERASE_POLL_ATTEMPTS 3U\n"
    "#define OTA_PENDING_RESET_MAX 2U",
    "#define OTA_ERASE_MAX_ATTEMPTS 1U\n"
    "#define OTA_ERASE_POLL_ATTEMPTS 1U\n"
    "#define OTA_READ_RETRIES 2U\n"
    "#define OTA_READ_RECONNECT_TIMEOUT_MS 180000U\n"
    "#define OTA_SUPERVISION_TIMEOUT_UNITS 2000U\n"
    "#define OTA_PENDING_RESET_MAX 2U",
    "retry constants",
)

source = replace_once(
    source,
    "static bool full_flow_attempted;\n"
    "static uint8_t pending_reset_count;\n"
    "static void master_leds_set(bool scan, bool link, bool ota, bool error);",
    "static bool full_flow_attempted;\n"
    "static uint8_t pending_reset_count;\n"
    "static bool smp_link_lost;\n"
    "static bool smp_read_only_inflight;\n"
    "static bool read_retry_waiting;\n"
    "static struct k_sem read_retry_sem;\n"
    "static void master_leds_set(bool scan, bool link, bool ota, bool error);",
    "retry state",
)

# A link loss must wake a blocked SMP waiter immediately.  The read wrapper
# decides whether it may reconnect; write callers receive -ENOTCONN and stop.
source = replace_once(
    source,
    "\tk_sem_reset(&ota_sem);\n"
    "\tsmp_rsp_len = 0U;",
    "\tk_sem_reset(&ota_sem);\n"
    "\tsmp_link_lost = false;\n"
    "\tsmp_rsp_len = 0U;",
    "SMP link-loss reset",
)
source = replace_once(
    source,
    "\tprintk(\"OTA rx complete: t=%u dt=%u conn=%p grp=0x%04x cmd=0x%02x seq=%u rsp_len=%u\\n\",",
    "\tif (smp_link_lost) {\n"
    "\t\tprintk(\"OTA read/write interrupted by disconnect: grp=0x%04x cmd=0x%02x seq=%u rsp_len=%u\\n\",\n"
    "\t\t       (unsigned int)smp_inflight_group,\n"
    "\t\t       (unsigned int)smp_inflight_cmd,\n"
    "\t\t       (unsigned int)smp_inflight_seq,\n"
    "\t\t       (unsigned int)smp_rsp_len);\n"
    "\t\treturn -ENOTCONN;\n"
    "\t}\n"
    "\tprintk(\"OTA rx complete: t=%u dt=%u conn=%p grp=0x%04x cmd=0x%02x seq=%u rsp_len=%u\\n\",",
    "SMP link-loss result",
)

# Echo is observational.  It now obeys the same two-reconnect budget instead
# of silently treating a 30-second timeout as success.
source = replace_once(
    source,
    "\trc = ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_OS, 0U, 2U, false);\n"
    "\tif (rc == -ETIMEDOUT) {\n"
    "\t\tprintk(\"OTA prime timeout (non-fatal), continue with IMG erase/upload\\n\");\n"
    "\t\treturn 0;\n"
    "\t}\n"
    "\treturn rc;\n"
    "}",
    "\trc = ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_OS, 0U, 2U, false);\n"
    "\treturn rc;\n"
    "}",
    "echo timeout policy",
)

retry_helpers = r'''
static int ota_reconnect_for_read_retry(const char *operation,
					uint8_t retry_index, int cause)
{
	int rc;

	printk("OTA_READ_RETRY operation=%s retry=%u/%u cause=%d action=reconnect\n",
	       operation, (unsigned int)retry_index,
	       (unsigned int)OTA_READ_RETRIES, cause);
	read_retry_waiting = true;
	k_sem_reset(&read_retry_sem);
	if (default_conn != NULL) {
		rc = bt_conn_disconnect(default_conn,
					BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		if (rc != 0 && rc != -EALREADY && rc != -ENOTCONN) {
			read_retry_waiting = false;
			return rc;
		}
	} else {
		rc = bt_scan_start(BT_SCAN_TYPE_SCAN_ACTIVE);
		if (rc != 0 && rc != -EALREADY) {
			read_retry_waiting = false;
			return rc;
		}
	}
	if (k_sem_take(&read_retry_sem,
			K_MSEC(OTA_READ_RECONNECT_TIMEOUT_MS)) != 0) {
		read_retry_waiting = false;
		printk("OTA_READ_RETRY operation=%s retry=%u result=reconnect_timeout\n",
		       operation, (unsigned int)retry_index);
		return -ETIMEDOUT;
	}
	if (default_conn == NULL || !selected_identity_verified ||
	    !mtu_ready || !ota_ready) {
		printk("OTA_READ_RETRY operation=%s retry=%u result=not_ready\n",
		       operation, (unsigned int)retry_index);
		return -ENOTCONN;
	}
	printk("OTA_READ_RETRY operation=%s retry=%u result=reconnected mtu=%u\n",
	       operation, (unsigned int)retry_index,
	       (unsigned int)bt_gatt_get_mtu(default_conn));
	return 0;
}

static int ota_read_image_state_retrying(struct bt_dfu_smp *smp,
					 bool confirmed_write)
{
	int rc = -EIO;

	for (uint8_t attempt = 0U; attempt <= OTA_READ_RETRIES; ++attempt) {
		smp_read_only_inflight = true;
		rc = ota_read_image_state(smp, confirmed_write && attempt == 0U);
		smp_read_only_inflight = false;
		if (rc == 0) {
			return 0;
		}
		if (attempt == OTA_READ_RETRIES) {
			printk("OTA_READ_RETRY operation=image_state exhausted=%u rc=%d\n",
			       (unsigned int)OTA_READ_RETRIES, rc);
			return rc;
		}
		rc = ota_reconnect_for_read_retry("image_state", attempt + 1U, rc);
		if (rc != 0) {
			return rc;
		}
		smp = &dfu_smp;
	}
	return rc;
}

static int ota_prime_link_retrying(struct bt_dfu_smp *smp)
{
	int rc = -EIO;

	for (uint8_t attempt = 0U; attempt <= OTA_READ_RETRIES; ++attempt) {
		smp_read_only_inflight = true;
		rc = ota_prime_link(smp);
		smp_read_only_inflight = false;
		if (rc == 0) {
			return 0;
		}
		if (attempt == OTA_READ_RETRIES) {
			printk("OTA_READ_RETRY operation=echo exhausted=%u rc=%d\n",
			       (unsigned int)OTA_READ_RETRIES, rc);
			return rc;
		}
		rc = ota_reconnect_for_read_retry("echo", attempt + 1U, rc);
		if (rc != 0) {
			return rc;
		}
		smp = &dfu_smp;
	}
	return rc;
}

'''
source = replace_once(
    source,
    "static int ota_wait_upload_ready(struct bt_dfu_smp *smp)\n{",
    retry_helpers + "static int ota_wait_upload_ready(struct bt_dfu_smp *smp)\n{",
    "read retry helpers",
)

source = replace_once(
    source,
    "\tconst int max_attempts = 5;",
    "\tconst int max_attempts = 1;",
    "same-link gate retry removal",
)
source = source.replace(
    "ota_read_image_state(smp, probe_write_req && (attempt == 1))",
    "ota_read_image_state_retrying(smp, probe_write_req && (attempt == 1))",
)
source = source.replace(
    "ota_read_image_state(smp, false)",
    "ota_read_image_state_retrying(smp, false)",
)
source = source.replace(
    "ota_read_image_state(&dfu_smp, false)",
    "ota_read_image_state_retrying(&dfu_smp, false)",
)
source = replace_once(
    source,
    "\t\t\tota_status = ota_prime_link(&dfu_smp);\n"
    "\t\t\tif (ota_status != 0 && ota_status != -ETIMEDOUT) {",
    "\t\t\tota_status = ota_prime_link_retrying(&dfu_smp);\n"
    "\t\t\tif (ota_status != 0) {",
    "echo retry caller",
)

# Erase is a write and is never reissued.  A nonzero result is a hard stop;
# only the read-only proof after an acknowledged erase may reconnect/retry.
source = replace_once(
    source,
    "\t\tif (erase_rc != 0 && erase_rc != -ETIMEDOUT) {\n"
    "\t\t\tprintk(\"OTA_ERASE hard_failure rc=%d\\n\", erase_rc);\n"
    "\t\t\treturn erase_rc;\n"
    "\t\t}\n"
    "\t\tif (erase_rc == -ETIMEDOUT) {\n"
    "\t\t\tprintk(\"OTA_ERASE response_timeout; state proof required\\n\");\n"
    "\t\t}",
    "\t\tif (erase_rc != 0) {\n"
    "\t\t\tprintk(\"OTA_ERASE hard_stop rc=%d retries=0\\n\", erase_rc);\n"
    "\t\t\treturn erase_rc;\n"
    "\t\t}",
    "zero-retry erase",
)

# Discovery completion is the resume point for a read retry.  It must not
# queue a second top-level OTA thread pass for the same operation.
source = replace_once(
    source,
    "\tk_sleep(K_MSEC(1200));\n"
    "\tota_try_schedule_start();",
    "\tk_sleep(K_MSEC(1200));\n"
    "\tif (read_retry_waiting) {\n"
    "\t\tread_retry_waiting = false;\n"
    "\t\tk_sem_give(&read_retry_sem);\n"
    "\t} else {\n"
    "\t\tota_try_schedule_start();\n"
    "\t}",
    "read retry discovery resume",
)

# Enforce the raised timeout even when the B306 SMP transport requests its
# deployed 42-unit value after the first packet, and record the actual result.
param_callbacks = r'''
static bool le_param_req(struct bt_conn *conn, struct bt_le_conn_param *param)
{
	uint16_t requested_timeout = param->timeout;

	ARG_UNUSED(conn);
	if (param->timeout < OTA_SUPERVISION_TIMEOUT_UNITS) {
		param->timeout = OTA_SUPERVISION_TIMEOUT_UNITS;
	}
	printk("OTA_CI_PEER_REQUEST interval_min=%u interval_max=%u latency=%u requested_timeout_units=%u accepted_timeout_units=%u\n",
	       param->interval_min, param->interval_max, param->latency,
	       requested_timeout, param->timeout);
	return true;
}

static void le_param_updated(struct bt_conn *conn, uint16_t interval,
			     uint16_t latency, uint16_t timeout)
{
	ARG_UNUSED(conn);
	printk("OTA_CI_UPDATED interval_units=%u interval_us=%u latency=%u timeout_units=%u\n",
	       interval, (uint32_t)interval * 1250U, latency, timeout);
}

'''
source = replace_once(
    source,
    "static void disconnected(struct bt_conn *conn, uint8_t reason)\n{",
    param_callbacks + "static void disconnected(struct bt_conn *conn, uint8_t reason)\n{",
    "connection parameter callbacks",
)
source = replace_once(
    source,
    "\tprintk(\"Disconnected: %s reason 0x%02x\\n\", addr, reason);\n"
    "\tota_dfu_ready_watchdog_cancel(\"disconnect\");",
    "\tprintk(\"Disconnected: %s reason 0x%02x read_only=%u read_retry_waiting=%u\\n\",\n"
    "\t       addr, reason, smp_read_only_inflight ? 1U : 0U,\n"
    "\t       read_retry_waiting ? 1U : 0U);\n"
    "\tif (was_default && smp_read_only_inflight) {\n"
    "\t\tsmp_link_lost = true;\n"
    "\t\tk_sem_give(&ota_sem);\n"
    "\t}\n"
    "\tota_dfu_ready_watchdog_cancel(\"disconnect\");",
    "disconnect wakes read",
)
source = replace_once(
    source,
    "static struct bt_conn_cb conn_callbacks = {\n"
    "\t.connected = connected,\n"
    "\t.disconnected = disconnected,\n"
    "\t.security_changed = security_changed,\n"
    "};",
    "static struct bt_conn_cb conn_callbacks = {\n"
    "\t.connected = connected,\n"
    "\t.disconnected = disconnected,\n"
    "\t.security_changed = security_changed,\n"
    "\t.le_param_req = le_param_req,\n"
    "\t.le_param_updated = le_param_updated,\n"
    "};",
    "connection callback registration",
)

source = replace_once(
    source,
    "\tk_sem_init(&smp_write_sem, 0, 1);\n"
    "\tk_work_init_delayable(&dfu_ready_watchdog_work, ota_dfu_ready_watchdog_fn);",
    "\tk_sem_init(&smp_write_sem, 0, 1);\n"
    "\tk_sem_init(&read_retry_sem, 0, 1);\n"
    "\tk_work_init_delayable(&dfu_ready_watchdog_work, ota_dfu_ready_watchdog_fn);",
    "read retry semaphore init",
)
source = replace_once(
    source,
    "\tpending_reset_count = 0U;\n"
    "\tota_runtime_active = true;",
    "\tpending_reset_count = 0U;\n"
    "\tsmp_link_lost = false;\n"
    "\tsmp_read_only_inflight = false;\n"
    "\tread_retry_waiting = false;\n"
    "\tota_runtime_active = true;",
    "read retry runtime init",
)

path.chmod(0o644)
path.write_text(source)
print("graduated read-retry and supervision hardening applied")
