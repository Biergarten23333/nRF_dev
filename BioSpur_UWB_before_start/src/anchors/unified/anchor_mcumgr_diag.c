#include "anchor_mcumgr_diag.h"

#include <errno.h>

#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/bluetooth/conn.h>

#if defined(CONFIG_MCUMGR_MGMT_NOTIFICATION_HOOKS)
#include <zephyr/mgmt/mcumgr/mgmt/callbacks.h>
#include <zephyr/mgmt/mcumgr/grp/img_mgmt/img_mgmt.h>
#include <zephyr/mgmt/mcumgr/grp/img_mgmt/img_mgmt_callbacks.h>
#endif

#if defined(CONFIG_MCUMGR_MGMT_NOTIFICATION_HOOKS)
static volatile bool g_anchor_ota_active;
static struct bt_conn *g_last_conn;
static char g_last_peer[BT_ADDR_LE_STR_LEN];
static uint32_t g_req_seq;
static uint32_t g_done_seq;

static void diag_peer_set(struct bt_conn *conn)
{
    if (conn == NULL) {
        g_last_conn = NULL;
        (void)snprintk(g_last_peer, sizeof(g_last_peer), "-");
        return;
    }

    g_last_conn = conn;
    bt_addr_le_to_str(bt_conn_get_dst(conn), g_last_peer, sizeof(g_last_peer));
}

static void diag_conn_cb(struct bt_conn *conn, uint8_t err)
{
    if (err == 0U) {
        diag_peer_set(conn);
        printk("ANCHOR_SMP_CONN connected conn=%p peer=%s\n", conn, g_last_peer);
    }
}

static void diag_disconn_cb(struct bt_conn *conn, uint8_t reason)
{
    char peer[BT_ADDR_LE_STR_LEN];

    bt_addr_le_to_str(bt_conn_get_dst(conn), peer, sizeof(peer));
    printk("ANCHOR_SMP_CONN disconnected conn=%p peer=%s reason=0x%02x\n",
           conn, peer, (unsigned int)reason);
    if (conn == g_last_conn) {
        diag_peer_set(NULL);
    }
}

static struct bt_conn_cb g_diag_conn_cb = {
    .connected = diag_conn_cb,
    .disconnected = diag_disconn_cb,
};

static const char *smp_event_name(uint32_t event)
{
    switch (event) {
    case MGMT_EVT_OP_CMD_RECV:
        return "CMD_RECV";
    case MGMT_EVT_OP_CMD_STATUS:
        return "CMD_STATUS";
    case MGMT_EVT_OP_CMD_DONE:
        return "CMD_DONE";
    default:
        return "CMD_OTHER";
    }
}

static enum mgmt_cb_return smp_cb(uint32_t event, enum mgmt_cb_return prev_status,
                                  int32_t *rc, uint16_t *group, bool *abort_more, void *data,
                                  size_t data_size)
{
    const struct mgmt_evt_op_cmd_arg *arg = data;
    int32_t rc_val = (rc != NULL) ? *rc : 0;
    uint16_t grp_val = (group != NULL) ? *group : 0U;

    ARG_UNUSED(prev_status);
    ARG_UNUSED(rc);
    ARG_UNUSED(group);
    ARG_UNUSED(abort_more);
    ARG_UNUSED(data_size);

    if (arg != NULL) {
        if (event == MGMT_EVT_OP_CMD_RECV) {
            ++g_req_seq;
            printk("ANCHOR_SMP_INGRESS req_no=%lu conn=%p peer=%s chr=dfu_smp handle=NA len=NA op=%u grp=0x%04x id=0x%02x seq=NA\n",
                   (unsigned long)g_req_seq,
                   g_last_conn,
                   g_last_peer[0] != '\0' ? g_last_peer : "-",
                   (unsigned int)arg->op,
                   (unsigned int)arg->group,
                   (unsigned int)arg->id);
            if (arg->group == IMG_MGMT_GROUP_ID && arg->id == IMG_MGMT_ID_STATE) {
                printk("ANCHOR_IMG_HANDLER_ENTER type=state req_no=%lu grp=0x%04x id=0x%02x\n",
                       (unsigned long)g_req_seq,
                       (unsigned int)arg->group,
                       (unsigned int)arg->id);
            } else if (arg->group == IMG_MGMT_GROUP_ID && arg->id == IMG_MGMT_ID_UPLOAD) {
                printk("ANCHOR_IMG_HANDLER_ENTER type=upload req_no=%lu grp=0x%04x id=0x%02x\n",
                       (unsigned long)g_req_seq,
                       (unsigned int)arg->group,
                       (unsigned int)arg->id);
            }
        } else if (event == MGMT_EVT_OP_CMD_DONE) {
            ++g_done_seq;
            printk("ANCHOR_SMP_DONE done_no=%lu conn=%p peer=%s grp=0x%04x id=0x%02x err=%d rsp_generated=%u notify_send_rc=unknown\n",
                   (unsigned long)g_done_seq,
                   g_last_conn,
                   g_last_peer[0] != '\0' ? g_last_peer : "-",
                   (unsigned int)arg->group,
                   (unsigned int)arg->id,
                   (int)arg->err,
                   (unsigned int)(arg->err == 0 ? 1U : 0U));
        } else {
            printk("MCUMGR_SMP_EVT %s grp=0x%04x id=%u val=%d rc=%d evt_grp=0x%04x\n",
                   smp_event_name(event), (unsigned int)arg->group, (unsigned int)arg->id,
                   (int)arg->op, (int)rc_val, (unsigned int)grp_val);
        }
    } else {
        printk("MCUMGR_SMP_EVT %s (no-arg) rc=%d evt_grp=0x%04x\n",
               smp_event_name(event), (int)rc_val, (unsigned int)grp_val);
    }

    return MGMT_CB_OK;
}

#if defined(CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS)
static const char *img_event_name(uint32_t event)
{
    switch (event) {
    case MGMT_EVT_OP_IMG_MGMT_DFU_CHUNK:
        return "DFU_CHUNK";
    case MGMT_EVT_OP_IMG_MGMT_DFU_STOPPED:
        return "DFU_STOPPED";
    case MGMT_EVT_OP_IMG_MGMT_DFU_STARTED:
        return "DFU_STARTED";
    case MGMT_EVT_OP_IMG_MGMT_DFU_PENDING:
        return "DFU_PENDING";
    case MGMT_EVT_OP_IMG_MGMT_DFU_CONFIRMED:
        return "DFU_CONFIRMED";
    case MGMT_EVT_OP_IMG_MGMT_DFU_CHUNK_WRITE_COMPLETE:
        return "DFU_CHUNK_WRITE_COMPLETE";
    case MGMT_EVT_OP_IMG_MGMT_IMAGE_SLOT_STATE:
        return "IMAGE_SLOT_STATE";
    default:
        return "IMG_OTHER";
    }
}

static enum mgmt_cb_return img_cb(uint32_t event, enum mgmt_cb_return prev_status,
                                  int32_t *rc, uint16_t *group, bool *abort_more, void *data,
                                  size_t data_size)
{
    ARG_UNUSED(prev_status);
    ARG_UNUSED(rc);
    ARG_UNUSED(group);
    ARG_UNUSED(abort_more);
    ARG_UNUSED(data);
    ARG_UNUSED(data_size);

    if (event == MGMT_EVT_OP_IMG_MGMT_DFU_STARTED ||
        event == MGMT_EVT_OP_IMG_MGMT_DFU_CHUNK ||
        event == MGMT_EVT_OP_IMG_MGMT_DFU_CHUNK_WRITE_COMPLETE) {
        g_anchor_ota_active = true;
    } else if (event == MGMT_EVT_OP_IMG_MGMT_DFU_STOPPED ||
               event == MGMT_EVT_OP_IMG_MGMT_DFU_PENDING ||
               event == MGMT_EVT_OP_IMG_MGMT_DFU_CONFIRMED) {
        g_anchor_ota_active = false;
    }

    if (event == MGMT_EVT_OP_IMG_MGMT_DFU_CHUNK && data != NULL &&
        data_size >= sizeof(struct img_mgmt_upload_check)) {
        const struct img_mgmt_upload_check *check = data;
        const struct img_mgmt_upload_req *req = check->req;
        const struct img_mgmt_upload_action *action = check->action;
        size_t data_len = (req != NULL) ? req->img_data.len : 0U;
        size_t off = (req != NULL) ? req->off : SIZE_MAX;
        size_t total = (req != NULL) ? req->size : SIZE_MAX;
        int write_bytes = (action != NULL) ? action->write_bytes : -1;
        int area_id = (action != NULL) ? action->area_id : -1;
        unsigned int proceed = (action != NULL && action->proceed) ? 1U : 0U;
        unsigned int erase = (action != NULL && action->erase) ? 1U : 0U;

        printk("ANCHOR_IMG_UPLOAD_CHUNK off=%u total=%u data_len=%u write_bytes=%d area=%d proceed=%u erase=%u\n",
               (unsigned int)off,
               (unsigned int)total,
               (unsigned int)data_len,
               write_bytes,
               area_id,
               proceed,
               erase);
    } else if (event == MGMT_EVT_OP_IMG_MGMT_IMAGE_SLOT_STATE) {
        printk("ANCHOR_IMG_HANDLER_SLOT_STATE emitted\n");
    }

    printk("MCUMGR_IMG_EVT %s\n", img_event_name(event));
    return MGMT_CB_OK;
}
#endif

static struct mgmt_callback smp_evt_cb = {
    .callback = smp_cb,
    .event_id = MGMT_EVT_OP_CMD_ALL,
};

#if defined(CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS)
static struct mgmt_callback img_evt_cb = {
    .callback = img_cb,
    .event_id = MGMT_EVT_OP_IMG_MGMT_ALL,
};
#endif
#endif

int anchor_mcumgr_diag_init(void)
{
#if !defined(CONFIG_MCUMGR_MGMT_NOTIFICATION_HOOKS)
    return -ENOTSUP;
#else
    g_anchor_ota_active = false;
    g_req_seq = 0U;
    g_done_seq = 0U;
    diag_peer_set(NULL);
    bt_conn_cb_register(&g_diag_conn_cb);
    printk("ANCHOR_SMP_DIAG note: handle/seq/raw_len are not exposed by mcumgr callback API; use transport DBG logs for raw SMP frame details\n");
    mgmt_callback_register(&smp_evt_cb);
#if defined(CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS)
    mgmt_callback_register(&img_evt_cb);
#endif
    printk("anchor mcumgr diag callbacks registered\n");
    return 0;
#endif
}

bool anchor_mcumgr_diag_ota_active(void)
{
#if !defined(CONFIG_MCUMGR_MGMT_NOTIFICATION_HOOKS) || \
    !defined(CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS)
    return false;
#else
    return g_anchor_ota_active;
#endif
}
