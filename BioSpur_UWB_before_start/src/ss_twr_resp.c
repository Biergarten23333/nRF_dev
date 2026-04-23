#include "ss_twr_resp.h"
#include "uwb_ss_twr_shared.h"
#include "anchor_mcumgr_diag.h"
#include "anchor_runtime_control.h"

#include <string.h>

#include <deca_device_api.h>
#include <deca_regs.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#define SS_TWR_RESP_TX_ANT_DLY 16436U
#define SS_TWR_RESP_RX_ANT_DLY 16436U

#ifndef APP_ANCHOR_RESP_DELAY_UUS
#define APP_ANCHOR_RESP_DELAY_UUS 500U
#endif

#ifndef APP_ANCHOR_VERBOSE_RESPONDER
#define APP_ANCHOR_VERBOSE_RESPONDER 1U
#endif

#ifndef APP_ANCHOR_VERBOSE_RESPONDER_ERRORS
#define APP_ANCHOR_VERBOSE_RESPONDER_ERRORS 1U
#endif

#ifndef APP_ANCHOR_RESPONDER_COOP_SLEEP_MS
#define APP_ANCHOR_RESPONDER_COOP_SLEEP_MS 0U
#endif

#define SS_TWR_RESP_RX_BUF_LEN 127U
#define SS_TWR_RESP_ALL_MSG_COMMON_LEN 10U
#define SS_TWR_RESP_MSG_SN_IDX 2U
#define SS_TWR_RESP_POLL_RX_TS_IDX 10U
#define SS_TWR_RESP_RESP_TX_TS_IDX 14U
#define SS_TWR_RESP_MSG_TS_LEN 4U

#define SS_TWR_RESP_UUS_TO_DWT_TIME 65536ULL
#define SS_TWR_RESP_POLL_RX_TO_RESP_TX_DLY_UUS APP_ANCHOR_RESP_DELAY_UUS

typedef unsigned long long dwtime_u64_t;

static dwt_config_t ss_twr_resp_config = {
    APP_UWB_CHANNEL,
    DWT_PRF_64M,
    DWT_PLEN_128,
    DWT_PAC8,
    9,
    9,
    1,
    DWT_BR_6M8,
    DWT_PHRMODE_STD,
    129,
};

static uint8_t ss_twr_resp_frame_seq_nb;
static uint8_t ss_twr_resp_rx_buffer[SS_TWR_RESP_RX_BUF_LEN];
static uint8_t ss_twr_resp_tx_resp_msg[20];
static uint16_t ss_twr_resp_local_addr;
static uint8_t ss_twr_resp_anchor_id;
static int ss_twr_resp_allow_tag_polls;

static void ss_twr_resp_diag_periodic(uint32 *last_ms,
                                      uint32 replies_ok,
                                      uint32 rx_error_count,
                                      uint32 ignored_tag_polls,
                                      uint32 ignored_nonpoll_frames)
{
    uint32_t now_ms = k_uptime_get_32();
    if ((now_ms - *last_ms) < 1000U) {
        return;
    }
    *last_ms = now_ms;
    printk("Responder diag anchor=%u ok=%lu rx_err=%lu ignored_tag=%lu ignored_nonpoll=%lu allow_tag_polls=%u\n",
           (unsigned int)ss_twr_resp_anchor_id,
           (unsigned long)replies_ok,
           (unsigned long)rx_error_count,
           (unsigned long)ignored_tag_polls,
           (unsigned long)ignored_nonpoll_frames,
           (unsigned int)(ss_twr_resp_allow_tag_polls != 0));
}

static void ss_twr_resp_log_unexpected_frame(const uint8_t *frame,
                                             uint32_t frame_len,
                                             uint32_t ignored_nonpoll_frames)
{
    uint16_t dst_addr = 0U;
    uint16_t src_addr = 0U;
    uint16_t pan_id = 0U;
    uint8_t code = 0U;

    if (frame_len > UWB_MSG_CODE_IDX) {
        code = frame[UWB_MSG_CODE_IDX];
    }
    if (frame_len > (UWB_MSG_DST_IDX + 1U)) {
        dst_addr = uwb_frame_get_dst_addr(frame);
    }
    if (frame_len > (UWB_MSG_SRC_IDX + 1U)) {
        src_addr = uwb_frame_get_src_addr(frame);
    }
    if (frame_len > (UWB_MSG_PAN_IDX + 1U)) {
        pan_id = (uint16_t)frame[UWB_MSG_PAN_IDX] |
                 ((uint16_t)frame[UWB_MSG_PAN_IDX + 1U] << 8);
    }

    if (ignored_nonpoll_frames <= 5U || (ignored_nonpoll_frames % 200U) == 0U) {
        printk("Responder unexpected frame anchor=%u len=%lu fctrl=%02x%02x code=0x%02x pan=0x%04x dst=0x%04x src=0x%04x\n",
               (unsigned int)ss_twr_resp_anchor_id,
               (unsigned long)frame_len,
               (unsigned int)(frame_len > 1U ? frame[1] : 0U),
               (unsigned int)(frame_len > 0U ? frame[0] : 0U),
               (unsigned int)code,
               (unsigned int)pan_id,
               (unsigned int)dst_addr,
               (unsigned int)src_addr);
    }
}

static dwtime_u64_t ss_twr_resp_get_rx_timestamp_u64(void)
{
    uint8 ts_tab[5];
    dwtime_u64_t ts = 0;

    dwt_readrxtimestamp(ts_tab);

    for (int i = 4; i >= 0; --i) {
        ts <<= 8;
        ts |= ts_tab[i];
    }

    return ts;
}

static void ss_twr_resp_write_ts(uint8_t *ts_field, dwtime_u64_t ts)
{
    for (int i = 0; i < SS_TWR_RESP_MSG_TS_LEN; ++i) {
        ts_field[i] = (uint8_t)(ts >> (i * 8));
    }
}

static void ss_twr_resp_configure_radio(void)
{
    dwt_configure(&ss_twr_resp_config);
    dwt_setrxantennadelay(SS_TWR_RESP_RX_ANT_DLY);
    dwt_settxantennadelay(SS_TWR_RESP_TX_ANT_DLY);
    dwt_setleds(DWT_LEDS_ENABLE);
    dwt_setrxtimeout(0);
    dwt_setpreambledetecttimeout(0);
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_GOOD |
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
}

static inline void ss_twr_resp_coop_sleep(void)
{
#if APP_ANCHOR_RESPONDER_COOP_SLEEP_MS > 0
    k_msleep(APP_ANCHOR_RESPONDER_COOP_SLEEP_MS);
#endif
}

int ss_twr_resp_start(unsigned int anchor_id, int allow_tag_polls)
{
    uint32 status_reg;
    uint32 rx_error_count = 0U;
    uint32 replies_ok = 0U;
    uint32 ignored_tag_polls = 0U;
    uint32 ignored_nonpoll_frames = 0U;
    uint32 wait_cycles = 0U;
    uint32 diag_last_ms = k_uptime_get_32();

    if (anchor_id >= UWB_MAX_ANCHORS) {
        printk("Invalid SS-TWR responder anchor_id=%u\n", anchor_id);
        return -1;
    }

    ss_twr_resp_anchor_id = (uint8_t)anchor_id;
    ss_twr_resp_local_addr = uwb_anchor_short_addr(ss_twr_resp_anchor_id);
    ss_twr_resp_allow_tag_polls = allow_tag_polls;

    ss_twr_resp_configure_radio();
    printk("SS-TWR responder ready anchor=%u addr=0x%04x allow_tag_polls=%u\n",
           (unsigned int)ss_twr_resp_anchor_id,
           (unsigned int)ss_twr_resp_local_addr,
           (unsigned int)(ss_twr_resp_allow_tag_polls != 0));

    while (1) {
        uint32 frame_len;
        uint16_t poll_src_addr;

        if (anchor_runtime_stop_requested()) {
            dwt_forcetrxoff();
            dwt_rxreset();
            printk("Responder stop requested anchor=%u\n",
                   (unsigned int)ss_twr_resp_anchor_id);
            return 0;
        }

        if (anchor_mcumgr_diag_ota_active()) {
            /* During OTA, prioritize BLE/MCUmgr responsiveness over UWB
             * ranging workload so first SMP upload chunks are not starved.
             */
            k_msleep(2);
            continue;
        }

        dwt_rxenable(DWT_START_RX_IMMEDIATE);

        do {
            status_reg = dwt_read32bitreg(SYS_STATUS_ID);
            wait_cycles++;
            if ((wait_cycles & 0x3FFU) == 0U) {
                /* Responder runs forever on main thread; periodically yield so
                 * BLE/mcumgr workqueues can make progress under heavy UWB load.
                 */
                if (anchor_runtime_stop_requested()) {
                    dwt_forcetrxoff();
                    dwt_rxreset();
                    printk("Responder stop requested during RX wait anchor=%u\n",
                           (unsigned int)ss_twr_resp_anchor_id);
                    return 0;
                }
                ss_twr_resp_diag_periodic(&diag_last_ms, replies_ok, rx_error_count,
                                          ignored_tag_polls, ignored_nonpoll_frames);
                k_yield();
                ss_twr_resp_coop_sleep();
            }
        } while ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_ERR)) == 0U);
        wait_cycles = 0U;

        if ((status_reg & SYS_STATUS_RXFCG) == 0U) {
            rx_error_count++;
            if (APP_ANCHOR_VERBOSE_RESPONDER_ERRORS != 0U &&
                (rx_error_count <= 5U || (rx_error_count % 50U) == 0U)) {
                printk("Responder RX error/status: 0x%08lx\n",
                       (unsigned long)status_reg);
            }
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_ERR);
            dwt_rxreset();
            ss_twr_resp_diag_periodic(&diag_last_ms, replies_ok, rx_error_count,
                                      ignored_tag_polls, ignored_nonpoll_frames);
            ss_twr_resp_coop_sleep();
            continue;
        }

        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);

        frame_len = dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFL_MASK_1023;
        if (frame_len > sizeof(ss_twr_resp_rx_buffer)) {
            dwt_forcetrxoff();
            dwt_rxreset();
            ss_twr_resp_coop_sleep();
            continue;
        }

        memset(ss_twr_resp_rx_buffer, 0, sizeof(ss_twr_resp_rx_buffer));
        dwt_readrxdata(ss_twr_resp_rx_buffer, (uint16)frame_len, 0);

        if (!uwb_ss_twr_poll_matches(ss_twr_resp_rx_buffer,
                                     ss_twr_resp_local_addr)) {
            ignored_nonpoll_frames++;
            if (APP_ANCHOR_VERBOSE_RESPONDER_ERRORS != 0U) {
                ss_twr_resp_log_unexpected_frame(ss_twr_resp_rx_buffer, frame_len,
                                                 ignored_nonpoll_frames);
            }
            ss_twr_resp_diag_periodic(&diag_last_ms, replies_ok, rx_error_count,
                                      ignored_tag_polls, ignored_nonpoll_frames);
            ss_twr_resp_coop_sleep();
            continue;
        }

        poll_src_addr = uwb_frame_get_src_addr(ss_twr_resp_rx_buffer);
        if (!ss_twr_resp_allow_tag_polls && uwb_short_addr_is_tag(poll_src_addr)) {
            ignored_tag_polls++;
            ss_twr_resp_diag_periodic(&diag_last_ms, replies_ok, rx_error_count,
                                      ignored_tag_polls, ignored_nonpoll_frames);
            ss_twr_resp_coop_sleep();
            continue;
        }

        dwtime_u64_t poll_rx_ts = ss_twr_resp_get_rx_timestamp_u64();
        uint32 resp_tx_time =
            (uint32)((poll_rx_ts +
                      (SS_TWR_RESP_POLL_RX_TO_RESP_TX_DLY_UUS *
                       SS_TWR_RESP_UUS_TO_DWT_TIME)) >>
                     8);
        dwtime_u64_t resp_tx_ts =
            (((dwtime_u64_t)(resp_tx_time & 0xFFFFFFFEUL)) << 8) +
            SS_TWR_RESP_TX_ANT_DLY;

        dwt_setdelayedtrxtime(resp_tx_time);

        uwb_ss_twr_build_resp_frame(ss_twr_resp_tx_resp_msg,
                                    ss_twr_resp_frame_seq_nb, poll_src_addr,
                                    ss_twr_resp_local_addr);
        ss_twr_resp_write_ts(
            &ss_twr_resp_tx_resp_msg[SS_TWR_RESP_POLL_RX_TS_IDX], poll_rx_ts);
        ss_twr_resp_write_ts(
            &ss_twr_resp_tx_resp_msg[SS_TWR_RESP_RESP_TX_TS_IDX], resp_tx_ts);

        if (dwt_writetxdata(sizeof(ss_twr_resp_tx_resp_msg),
                            ss_twr_resp_tx_resp_msg, 0) != DWT_SUCCESS) {
            if (APP_ANCHOR_VERBOSE_RESPONDER_ERRORS != 0U) {
                printk("Responder TX buffer write failed\n");
            }
            ss_twr_resp_diag_periodic(&diag_last_ms, replies_ok, rx_error_count,
                                      ignored_tag_polls, ignored_nonpoll_frames);
            ss_twr_resp_coop_sleep();
            continue;
        }

        dwt_writetxfctrl(sizeof(ss_twr_resp_tx_resp_msg), 0, 1);

        if (dwt_starttx(DWT_START_TX_DELAYED) != DWT_SUCCESS) {
            if (APP_ANCHOR_VERBOSE_RESPONDER_ERRORS != 0U) {
                printk("Responder delayed TX missed slot\n");
            }
            dwt_forcetrxoff();
            dwt_rxreset();
            ss_twr_resp_diag_periodic(&diag_last_ms, replies_ok, rx_error_count,
                                      ignored_tag_polls, ignored_nonpoll_frames);
            ss_twr_resp_coop_sleep();
            continue;
        }

        while ((dwt_read32bitreg(SYS_STATUS_ID) & SYS_STATUS_TXFRS) == 0U) {
            wait_cycles++;
            if ((wait_cycles & 0x3FFU) == 0U) {
                if (anchor_runtime_stop_requested()) {
                    dwt_forcetrxoff();
                    dwt_rxreset();
                    printk("Responder stop requested during TX wait anchor=%u\n",
                           (unsigned int)ss_twr_resp_anchor_id);
                    return 0;
                }
                k_yield();
                ss_twr_resp_coop_sleep();
            }
        }
        wait_cycles = 0U;

        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
        replies_ok++;
        if (APP_ANCHOR_VERBOSE_RESPONDER != 0U) {
            if (uwb_short_addr_is_anchor(poll_src_addr)) {
                printk("Responder replied to anchor poll %u anchor=%u src=0x%04x\n",
                       (unsigned int)ss_twr_resp_frame_seq_nb,
                       (unsigned int)uwb_anchor_id_from_addr(poll_src_addr),
                       (unsigned int)poll_src_addr);
            } else {
                printk("Responder replied to tag poll %u tag=%u src=0x%04x\n",
                       (unsigned int)ss_twr_resp_frame_seq_nb,
                       (unsigned int)uwb_tag_id_from_addr(poll_src_addr),
                       (unsigned int)poll_src_addr);
            }
        }
        ss_twr_resp_diag_periodic(&diag_last_ms, replies_ok, rx_error_count,
                                  ignored_tag_polls, ignored_nonpoll_frames);
        ss_twr_resp_frame_seq_nb++;
        ss_twr_resp_coop_sleep();
    }
}
