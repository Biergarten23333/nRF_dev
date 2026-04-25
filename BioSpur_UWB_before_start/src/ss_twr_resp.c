#include "ss_twr_resp.h"
#include "uwb_ss_twr_shared.h"
#include "anchor_mcumgr_diag.h"
#include "anchor_runtime_control.h"

#include <string.h>
#include <stdint.h>

#include <deca_device_api.h>
#include <deca_regs.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#if APP_ANCHOR_RESPONDER_BLUE_LED_ENABLE
#include <hal/nrf_gpio.h>
#endif

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

#ifndef APP_ANCHOR_RESPONDER_DIAG_PERIOD_MS
#define APP_ANCHOR_RESPONDER_DIAG_PERIOD_MS 5000U
#endif

#ifndef APP_ANCHOR_RESPONDER_PRINTK_ENABLE
#define APP_ANCHOR_RESPONDER_PRINTK_ENABLE 1U
#endif

#ifndef APP_ANCHOR_RESPONDER_PROFILE_ENABLE
#define APP_ANCHOR_RESPONDER_PROFILE_ENABLE 0U
#endif

#ifndef APP_ANCHOR_RESPONDER_BLUE_LED_ENABLE
#define APP_ANCHOR_RESPONDER_BLUE_LED_ENABLE 0U
#endif

#ifndef APP_ANCHOR_RESPONDER_BLUE_LED_PIN
#define APP_ANCHOR_RESPONDER_BLUE_LED_PIN 31U
#endif

#ifndef APP_ANCHOR_RESPONDER_BLUE_LED_ACTIVE_LOW
#define APP_ANCHOR_RESPONDER_BLUE_LED_ACTIVE_LOW 1U
#endif

#define SS_TWR_RESP_RX_BUF_LEN 127U
#define SS_TWR_RESP_ALL_MSG_COMMON_LEN 10U
#define SS_TWR_RESP_MSG_SN_IDX 2U
#define SS_TWR_RESP_POLL_RX_TS_IDX 10U
#define SS_TWR_RESP_RESP_TX_TS_IDX 14U
#define SS_TWR_RESP_MSG_TS_LEN 4U

#define SS_TWR_RESP_UUS_TO_DWT_TIME 65536ULL
#define SS_TWR_RESP_POLL_RX_TO_RESP_TX_DLY_UUS APP_ANCHOR_RESP_DELAY_UUS
#define SS_TWR_RESP_DIAG_TAG_SLOTS 8U

typedef unsigned long long dwtime_u64_t;

#if APP_ANCHOR_RESPONDER_PRINTK_ENABLE
#define RESP_PRINTK(...) printk(__VA_ARGS__)
#else
#define RESP_PRINTK(...) do { } while (0)
#endif

#if APP_ANCHOR_RESPONDER_PROFILE_ENABLE
#define RESP_PROF_PRINTK(...) printk(__VA_ARGS__)
#else
#define RESP_PROF_PRINTK(...) do { } while (0)
#endif

#if APP_ANCHOR_RESPONDER_BLUE_LED_ENABLE
static inline void ss_twr_resp_led_on(void)
{
#if APP_ANCHOR_RESPONDER_BLUE_LED_ACTIVE_LOW
    nrf_gpio_pin_clear(APP_ANCHOR_RESPONDER_BLUE_LED_PIN);
#else
    nrf_gpio_pin_set(APP_ANCHOR_RESPONDER_BLUE_LED_PIN);
#endif
}

static inline void ss_twr_resp_led_off(void)
{
#if APP_ANCHOR_RESPONDER_BLUE_LED_ACTIVE_LOW
    nrf_gpio_pin_set(APP_ANCHOR_RESPONDER_BLUE_LED_PIN);
#else
    nrf_gpio_pin_clear(APP_ANCHOR_RESPONDER_BLUE_LED_PIN);
#endif
}

static void ss_twr_resp_led_init(void)
{
    nrf_gpio_cfg_output(APP_ANCHOR_RESPONDER_BLUE_LED_PIN);
    ss_twr_resp_led_off();
}
#else
static inline void ss_twr_resp_led_on(void) { }
static inline void ss_twr_resp_led_off(void) { }
static inline void ss_twr_resp_led_init(void) { }
#endif

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

struct ss_twr_resp_profile_stats {
    uint32_t samples;
    uint32_t tx_attempts;
    uint32_t tx_misses;
    uint32_t max_to_frame_us;
    uint32_t max_to_ts_us;
    uint32_t max_to_txprog_us;
    uint32_t max_to_start_us;
    uint32_t max_starttx_us;
    uint64_t sum_to_frame_us;
    uint64_t sum_to_ts_us;
    uint64_t sum_to_txprog_us;
    uint64_t sum_to_start_us;
    uint64_t sum_starttx_us;
    int32_t min_slack_uus;
    int32_t last_miss_slack_uus;
};

static uint32_t ss_twr_resp_elapsed_us(uint32_t start_cyc, uint32_t end_cyc)
{
    return k_cyc_to_us_floor32(end_cyc - start_cyc);
}

static void ss_twr_resp_profile_observe(struct ss_twr_resp_profile_stats *p,
                                        uint32_t rx_cyc,
                                        uint32_t frame_cyc,
                                        uint32_t ts_cyc,
                                        uint32_t txprog_cyc,
                                        uint32_t start_done_cyc,
                                        int32_t slack_uus,
                                        uint32_t starttx_us,
                                        int starttx_ok)
{
#if APP_ANCHOR_RESPONDER_PROFILE_ENABLE
    uint32_t to_frame_us = ss_twr_resp_elapsed_us(rx_cyc, frame_cyc);
    uint32_t to_ts_us = ss_twr_resp_elapsed_us(rx_cyc, ts_cyc);
    uint32_t to_txprog_us = ss_twr_resp_elapsed_us(rx_cyc, txprog_cyc);
    uint32_t to_start_us = ss_twr_resp_elapsed_us(rx_cyc, start_done_cyc);

    p->samples++;
    p->tx_attempts++;
    p->sum_to_frame_us += to_frame_us;
    p->sum_to_ts_us += to_ts_us;
    p->sum_to_txprog_us += to_txprog_us;
    p->sum_to_start_us += to_start_us;
    p->sum_starttx_us += starttx_us;
    if (to_frame_us > p->max_to_frame_us) {
        p->max_to_frame_us = to_frame_us;
    }
    if (to_ts_us > p->max_to_ts_us) {
        p->max_to_ts_us = to_ts_us;
    }
    if (to_txprog_us > p->max_to_txprog_us) {
        p->max_to_txprog_us = to_txprog_us;
    }
    if (to_start_us > p->max_to_start_us) {
        p->max_to_start_us = to_start_us;
    }
    if (starttx_us > p->max_starttx_us) {
        p->max_starttx_us = starttx_us;
    }
    if (p->samples == 1U || slack_uus < p->min_slack_uus) {
        p->min_slack_uus = slack_uus;
    }
    if (!starttx_ok) {
        p->tx_misses++;
        p->last_miss_slack_uus = slack_uus;
    }
#else
    ARG_UNUSED(p);
    ARG_UNUSED(rx_cyc);
    ARG_UNUSED(frame_cyc);
    ARG_UNUSED(ts_cyc);
    ARG_UNUSED(txprog_cyc);
    ARG_UNUSED(start_done_cyc);
    ARG_UNUSED(slack_uus);
    ARG_UNUSED(starttx_us);
    ARG_UNUSED(starttx_ok);
#endif
}

static void ss_twr_resp_profile_periodic(struct ss_twr_resp_profile_stats *p,
                                         uint32_t *last_ms)
{
#if APP_ANCHOR_RESPONDER_PROFILE_ENABLE
    uint32_t now_ms = k_uptime_get_32();
    if ((now_ms - *last_ms) < APP_ANCHOR_RESPONDER_DIAG_PERIOD_MS) {
        return;
    }
    *last_ms = now_ms;
    if (p->samples == 0U) {
        RESP_PROF_PRINTK("Responder prof anchor=%u samples=0\n",
                         (unsigned int)ss_twr_resp_anchor_id);
        return;
    }

    RESP_PROF_PRINTK(
        "Responder prof anchor=%u samples=%lu attempts=%lu misses=%lu "
        "avg_us frame=%lu ts=%lu txprog=%lu start=%lu starttx=%lu "
        "max_us frame=%lu ts=%lu txprog=%lu start=%lu starttx=%lu "
        "min_slack_uus=%ld last_miss_slack_uus=%ld resp_delay_uus=%u\n",
        (unsigned int)ss_twr_resp_anchor_id,
        (unsigned long)p->samples,
        (unsigned long)p->tx_attempts,
        (unsigned long)p->tx_misses,
        (unsigned long)(p->sum_to_frame_us / p->samples),
        (unsigned long)(p->sum_to_ts_us / p->samples),
        (unsigned long)(p->sum_to_txprog_us / p->samples),
        (unsigned long)(p->sum_to_start_us / p->samples),
        (unsigned long)(p->sum_starttx_us / p->samples),
        (unsigned long)p->max_to_frame_us,
        (unsigned long)p->max_to_ts_us,
        (unsigned long)p->max_to_txprog_us,
        (unsigned long)p->max_to_start_us,
        (unsigned long)p->max_starttx_us,
        (long)p->min_slack_uus,
        (long)p->last_miss_slack_uus,
        (unsigned int)SS_TWR_RESP_POLL_RX_TO_RESP_TX_DLY_UUS);

    memset(p, 0, sizeof(*p));
#else
    ARG_UNUSED(p);
    ARG_UNUSED(last_ms);
#endif
}

static int32_t ss_twr_resp_slack_uus(uint32_t resp_tx_time)
{
    uint32_t now_hi = dwt_readsystimestamphi32();
    return (int32_t)(resp_tx_time - now_hi) / 256;
}

static void ss_twr_resp_diag_periodic(uint32 *last_ms,
                                      uint32 replies_ok,
                                      uint32 rx_error_count,
                                      uint32 ignored_tag_polls,
                                      uint32 ignored_nonpoll_frames,
                                      uint32 delayed_tx_miss_count,
                                      const uint32 tag_poll_count[SS_TWR_RESP_DIAG_TAG_SLOTS],
                                      const uint32 tag_reply_count[SS_TWR_RESP_DIAG_TAG_SLOTS],
                                      const uint32 tag_tx_miss_count[SS_TWR_RESP_DIAG_TAG_SLOTS])
{
    uint32_t now_ms = k_uptime_get_32();
    if ((now_ms - *last_ms) < APP_ANCHOR_RESPONDER_DIAG_PERIOD_MS) {
        return;
    }
    *last_ms = now_ms;
    RESP_PRINTK("Responder diag anchor=%u ok=%lu rx_err=%lu ignored_tag=%lu ignored_nonpoll=%lu tx_miss=%lu allow_tag_polls=%u tag_poll=%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu tag_ok=%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu tag_tx_miss=%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu\n",
           (unsigned int)ss_twr_resp_anchor_id,
           (unsigned long)replies_ok,
           (unsigned long)rx_error_count,
           (unsigned long)ignored_tag_polls,
           (unsigned long)ignored_nonpoll_frames,
           (unsigned long)delayed_tx_miss_count,
           (unsigned int)(ss_twr_resp_allow_tag_polls != 0),
           (unsigned long)tag_poll_count[0],
           (unsigned long)tag_poll_count[1],
           (unsigned long)tag_poll_count[2],
           (unsigned long)tag_poll_count[3],
           (unsigned long)tag_poll_count[4],
           (unsigned long)tag_poll_count[5],
           (unsigned long)tag_poll_count[6],
           (unsigned long)tag_poll_count[7],
           (unsigned long)tag_reply_count[0],
           (unsigned long)tag_reply_count[1],
           (unsigned long)tag_reply_count[2],
           (unsigned long)tag_reply_count[3],
           (unsigned long)tag_reply_count[4],
           (unsigned long)tag_reply_count[5],
           (unsigned long)tag_reply_count[6],
           (unsigned long)tag_reply_count[7],
           (unsigned long)tag_tx_miss_count[0],
           (unsigned long)tag_tx_miss_count[1],
           (unsigned long)tag_tx_miss_count[2],
           (unsigned long)tag_tx_miss_count[3],
           (unsigned long)tag_tx_miss_count[4],
           (unsigned long)tag_tx_miss_count[5],
           (unsigned long)tag_tx_miss_count[6],
           (unsigned long)tag_tx_miss_count[7]);
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
        RESP_PRINTK("Responder unexpected frame anchor=%u len=%lu fctrl=%02x%02x code=0x%02x pan=0x%04x dst=0x%04x src=0x%04x\n",
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
    uint32 delayed_tx_miss_count = 0U;
    uint32 tag_poll_count[SS_TWR_RESP_DIAG_TAG_SLOTS] = {0U};
    uint32 tag_reply_count[SS_TWR_RESP_DIAG_TAG_SLOTS] = {0U};
    uint32 tag_tx_miss_count[SS_TWR_RESP_DIAG_TAG_SLOTS] = {0U};
    uint32 wait_cycles = 0U;
    uint32 diag_last_ms = k_uptime_get_32();
    uint32 prof_last_ms = k_uptime_get_32();
    struct ss_twr_resp_profile_stats prof_stats = {0};

    if (anchor_id >= UWB_MAX_ANCHORS) {
        RESP_PRINTK("Invalid SS-TWR responder anchor_id=%u\n", anchor_id);
        return -1;
    }

    ss_twr_resp_anchor_id = (uint8_t)anchor_id;
    ss_twr_resp_local_addr = uwb_anchor_short_addr(ss_twr_resp_anchor_id);
    ss_twr_resp_allow_tag_polls = allow_tag_polls;

    ss_twr_resp_led_init();
    ss_twr_resp_configure_radio();
    RESP_PRINTK("SS-TWR responder ready anchor=%u addr=0x%04x allow_tag_polls=%u resp_delay_uus=%u\n",
           (unsigned int)ss_twr_resp_anchor_id,
           (unsigned int)ss_twr_resp_local_addr,
           (unsigned int)(ss_twr_resp_allow_tag_polls != 0),
           (unsigned int)SS_TWR_RESP_POLL_RX_TO_RESP_TX_DLY_UUS);

    while (1) {
        uint32 frame_len;
        uint16_t poll_src_addr;

        if (anchor_runtime_stop_requested()) {
            dwt_forcetrxoff();
            dwt_rxreset();
            ss_twr_resp_led_off();
            RESP_PRINTK("Responder stop requested anchor=%u\n",
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
                    RESP_PRINTK("Responder stop requested during RX wait anchor=%u\n",
                           (unsigned int)ss_twr_resp_anchor_id);
                    return 0;
                }
                ss_twr_resp_diag_periodic(
                    &diag_last_ms, replies_ok, rx_error_count,
                    ignored_tag_polls, ignored_nonpoll_frames,
                    delayed_tx_miss_count, tag_poll_count, tag_reply_count,
                    tag_tx_miss_count);
                ss_twr_resp_profile_periodic(&prof_stats, &prof_last_ms);
                k_yield();
                ss_twr_resp_coop_sleep();
            }
        } while ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_ERR)) == 0U);
        wait_cycles = 0U;

        if ((status_reg & SYS_STATUS_RXFCG) == 0U) {
            rx_error_count++;
            if (APP_ANCHOR_VERBOSE_RESPONDER_ERRORS != 0U &&
                (rx_error_count <= 5U || (rx_error_count % 50U) == 0U)) {
                RESP_PRINTK("Responder RX error/status: 0x%08lx\n",
                       (unsigned long)status_reg);
            }
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_ERR);
            dwt_rxreset();
            ss_twr_resp_diag_periodic(
                &diag_last_ms, replies_ok, rx_error_count, ignored_tag_polls,
                ignored_nonpoll_frames, delayed_tx_miss_count, tag_poll_count,
                tag_reply_count, tag_tx_miss_count);
            ss_twr_resp_profile_periodic(&prof_stats, &prof_last_ms);
            ss_twr_resp_coop_sleep();
            continue;
        }

        uint32_t prof_rx_cyc = k_cycle_get_32();
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
        uint32_t prof_frame_cyc = k_cycle_get_32();

        if (!uwb_ss_twr_poll_matches(ss_twr_resp_rx_buffer,
                                     ss_twr_resp_local_addr)) {
            ignored_nonpoll_frames++;
            if (APP_ANCHOR_VERBOSE_RESPONDER_ERRORS != 0U) {
                ss_twr_resp_log_unexpected_frame(ss_twr_resp_rx_buffer, frame_len,
                                                 ignored_nonpoll_frames);
            }
            ss_twr_resp_diag_periodic(
                &diag_last_ms, replies_ok, rx_error_count, ignored_tag_polls,
                ignored_nonpoll_frames, delayed_tx_miss_count, tag_poll_count,
                tag_reply_count, tag_tx_miss_count);
            ss_twr_resp_profile_periodic(&prof_stats, &prof_last_ms);
            ss_twr_resp_coop_sleep();
            continue;
        }

        poll_src_addr = uwb_frame_get_src_addr(ss_twr_resp_rx_buffer);
        bool poll_src_is_tag = uwb_short_addr_is_tag(poll_src_addr);
        uint8_t poll_tag_id = poll_src_is_tag ?
            uwb_tag_id_from_addr(poll_src_addr) : 0xffU;

        if (poll_src_is_tag && poll_tag_id < SS_TWR_RESP_DIAG_TAG_SLOTS) {
            tag_poll_count[poll_tag_id]++;
        }
        if (!ss_twr_resp_allow_tag_polls && poll_src_is_tag) {
            ignored_tag_polls++;
            ss_twr_resp_diag_periodic(
                &diag_last_ms, replies_ok, rx_error_count, ignored_tag_polls,
                ignored_nonpoll_frames, delayed_tx_miss_count, tag_poll_count,
                tag_reply_count, tag_tx_miss_count);
            ss_twr_resp_coop_sleep();
            continue;
        }

        dwtime_u64_t poll_rx_ts = ss_twr_resp_get_rx_timestamp_u64();
        uint32_t prof_ts_cyc = k_cycle_get_32();
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
                RESP_PRINTK("Responder TX buffer write failed\n");
            }
            ss_twr_resp_diag_periodic(
                &diag_last_ms, replies_ok, rx_error_count, ignored_tag_polls,
                ignored_nonpoll_frames, delayed_tx_miss_count, tag_poll_count,
                tag_reply_count, tag_tx_miss_count);
            ss_twr_resp_coop_sleep();
            continue;
        }

        dwt_writetxfctrl(sizeof(ss_twr_resp_tx_resp_msg), 0, 1);
        uint32_t prof_txprog_cyc = k_cycle_get_32();
        int32_t prof_slack_uus = ss_twr_resp_slack_uus(resp_tx_time);

        uint32_t prof_starttx_cyc = k_cycle_get_32();
        ss_twr_resp_led_on();
        int starttx_ok = (dwt_starttx(DWT_START_TX_DELAYED) == DWT_SUCCESS);
        uint32_t prof_start_done_cyc = k_cycle_get_32();
        uint32_t prof_starttx_us =
            ss_twr_resp_elapsed_us(prof_starttx_cyc, prof_start_done_cyc);
        ss_twr_resp_profile_observe(
            &prof_stats, prof_rx_cyc, prof_frame_cyc, prof_ts_cyc,
            prof_txprog_cyc, prof_start_done_cyc, prof_slack_uus,
            prof_starttx_us, starttx_ok);

        if (!starttx_ok) {
            ss_twr_resp_led_off();
            delayed_tx_miss_count++;
            if (poll_src_is_tag && poll_tag_id < SS_TWR_RESP_DIAG_TAG_SLOTS) {
                tag_tx_miss_count[poll_tag_id]++;
            }
            if (APP_ANCHOR_VERBOSE_RESPONDER_ERRORS != 0U) {
                RESP_PRINTK("Responder delayed TX missed slot\n");
            }
            dwt_forcetrxoff();
            dwt_rxreset();
            ss_twr_resp_diag_periodic(
                &diag_last_ms, replies_ok, rx_error_count, ignored_tag_polls,
                ignored_nonpoll_frames, delayed_tx_miss_count, tag_poll_count,
                tag_reply_count, tag_tx_miss_count);
            ss_twr_resp_profile_periodic(&prof_stats, &prof_last_ms);
            ss_twr_resp_coop_sleep();
            continue;
        }

        while ((dwt_read32bitreg(SYS_STATUS_ID) & SYS_STATUS_TXFRS) == 0U) {
            wait_cycles++;
            if ((wait_cycles & 0x3FFU) == 0U) {
                if (anchor_runtime_stop_requested()) {
                    dwt_forcetrxoff();
                    dwt_rxreset();
                    ss_twr_resp_led_off();
                    RESP_PRINTK("Responder stop requested during TX wait anchor=%u\n",
                           (unsigned int)ss_twr_resp_anchor_id);
                    return 0;
                }
                k_yield();
                ss_twr_resp_coop_sleep();
            }
        }
        wait_cycles = 0U;

        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
        ss_twr_resp_led_off();
        replies_ok++;
        if (poll_src_is_tag && poll_tag_id < SS_TWR_RESP_DIAG_TAG_SLOTS) {
            tag_reply_count[poll_tag_id]++;
        }
        if (APP_ANCHOR_VERBOSE_RESPONDER != 0U) {
            if (uwb_short_addr_is_anchor(poll_src_addr)) {
                RESP_PRINTK("Responder replied to anchor poll %u anchor=%u src=0x%04x\n",
                       (unsigned int)ss_twr_resp_frame_seq_nb,
                       (unsigned int)uwb_anchor_id_from_addr(poll_src_addr),
                       (unsigned int)poll_src_addr);
            } else {
                RESP_PRINTK("Responder replied to tag poll %u tag=%u src=0x%04x\n",
                       (unsigned int)ss_twr_resp_frame_seq_nb,
                       (unsigned int)uwb_tag_id_from_addr(poll_src_addr),
                       (unsigned int)poll_src_addr);
            }
        }
        ss_twr_resp_diag_periodic(
            &diag_last_ms, replies_ok, rx_error_count, ignored_tag_polls,
            ignored_nonpoll_frames, delayed_tx_miss_count, tag_poll_count,
            tag_reply_count, tag_tx_miss_count);
        ss_twr_resp_profile_periodic(&prof_stats, &prof_last_ms);
        ss_twr_resp_frame_seq_nb++;
        ss_twr_resp_coop_sleep();
    }
}
