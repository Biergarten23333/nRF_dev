#include "ss_twr_resp.h"
#include "uwb_ss_twr_shared.h"
#include "anchor_cir_output.h"
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

static uint8_t ss_twr_resp_count_mask_bits_before(uint8_t mask, uint8_t anchor_id)
{
    uint8_t count = 0U;

    for (uint8_t i = 0U; i < anchor_id && i < UWB_MAX_ANCHORS; ++i) {
        if ((mask & (uint8_t)(1U << i)) != 0U) {
            count++;
        }
    }

    return count;
}

static bool ss_twr_resp_matrix_poll_matches(const uint8_t *frame,
                                            uint16_t local_addr)
{
    if (frame[0] != UWB_FRAME_CTRL_LOW ||
        frame[1] != UWB_FRAME_CTRL_HIGH ||
        frame[UWB_MSG_CODE_IDX] != UWB_MSG_POLL_CODE) {
        return false;
    }

    if (uwb_frame_get_dst_addr(frame) != local_addr) {
        return false;
    }

    return uwb_short_addr_is_anchor(uwb_frame_get_src_addr(frame));
}

#define SS_TWR_RESP_TX_ANT_DLY 16436U
#define SS_TWR_RESP_RX_ANT_DLY 16436U

#ifndef APP_ANCHOR_RESP_DELAY_UUS
#define APP_ANCHOR_RESP_DELAY_UUS 1200U
#endif

#ifndef APP_ANCHOR_MATRIX_RESP_DELAY_UUS
#define APP_ANCHOR_MATRIX_RESP_DELAY_UUS 1200U
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

#ifndef APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE
#define APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE 0U
#endif

#ifndef APP_ANCHOR_RESPONDER_BLUE_LED_ENABLE
#define APP_ANCHOR_RESPONDER_BLUE_LED_ENABLE 1U
#endif

#ifndef APP_ANCHOR_RESPONDER_BLUE_LED_PIN
#define APP_ANCHOR_RESPONDER_BLUE_LED_PIN 31U
#endif

#ifndef APP_ANCHOR_RESPONDER_BLUE_LED_ACTIVE_LOW
#define APP_ANCHOR_RESPONDER_BLUE_LED_ACTIVE_LOW 1U
#endif

#ifndef APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE
#define APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE 0U
#endif

#ifndef APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_SKIP_RANK0_ENABLE
#define APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_SKIP_RANK0_ENABLE 0U
#endif

#ifndef APP_ANCHOR_RESP_RANK0_FAST_TX_ENABLE
#define APP_ANCHOR_RESP_RANK0_FAST_TX_ENABLE 0U
#endif

#ifndef APP_ANCHOR_RESP_POST_TX_DIAG_READ_ENABLE
#define APP_ANCHOR_RESP_POST_TX_DIAG_READ_ENABLE 0U
#endif

#ifndef APP_ANCHOR_RESP_POST_TX_DIAG_PAYLOAD_DELAY_ENABLE
#define APP_ANCHOR_RESP_POST_TX_DIAG_PAYLOAD_DELAY_ENABLE 0U
#endif

#ifndef APP_ANCHOR_RESP_POST_TX_DIAG_SIDECHANNEL_ENABLE
#define APP_ANCHOR_RESP_POST_TX_DIAG_SIDECHANNEL_ENABLE 0U
#endif

#ifndef APP_UWB_HW_FRAME_FILTER_ENABLE
#define APP_UWB_HW_FRAME_FILTER_ENABLE 1U
#endif

#ifndef APP_ALT_SS_TWR_ENABLE
#define APP_ALT_SS_TWR_ENABLE 1U
#endif

#ifndef APP_ALT_SS_TWR_POLL_SPACING_US
#define APP_ALT_SS_TWR_POLL_SPACING_US 200U
#endif

#ifndef APP_ALT_SS_TWR_GUARD_US
#define APP_ALT_SS_TWR_GUARD_US 1200U
#endif

#ifndef APP_ALT_SS_TWR_RESP_SPACING_US
#define APP_ALT_SS_TWR_RESP_SPACING_US 1000U
#endif

#ifndef APP_ALT_SS_TWR_TAIL_COMPRESS_ENABLE
#define APP_ALT_SS_TWR_TAIL_COMPRESS_ENABLE 0U
#endif

#ifndef APP_ALT_SS_TWR_TAIL_START_RANK
#define APP_ALT_SS_TWR_TAIL_START_RANK 5U
#endif

#ifndef APP_ALT_SS_TWR_TAIL_RESP_SPACING_US
#define APP_ALT_SS_TWR_TAIL_RESP_SPACING_US APP_ALT_SS_TWR_RESP_SPACING_US
#endif

#ifndef APP_ALT_SS_TWR_UNICAST_POLL_REARM_US
#define APP_ALT_SS_TWR_UNICAST_POLL_REARM_US 0U
#endif

#define SS_TWR_RESP_RX_BUF_LEN 127U
#define SS_TWR_RESP_ALL_MSG_COMMON_LEN 10U
#define SS_TWR_RESP_MSG_SN_IDX 2U
#define SS_TWR_RESP_POLL_RX_TS_IDX UWB_MSG_RESP_POLL_RX_TS_IDX
#define SS_TWR_RESP_RESP_TX_TS_IDX UWB_MSG_RESP_RESP_TX_TS_IDX
#define SS_TWR_RESP_MSG_TS_LEN UWB_MSG_RESP_TS_LEN

#define SS_TWR_RESP_UUS_TO_DWT_TIME 65536ULL
#define SS_TWR_RESP_POLL_RX_TO_RESP_TX_DLY_UUS APP_ANCHOR_RESP_DELAY_UUS
#define SS_TWR_RESP_DIAG_TAG_SLOTS 8U

typedef unsigned long long dwtime_u64_t;

static inline bool ss_twr_resp_full_cir_quiet(void)
{
    return anchor_cir_output_get_mode() == ANCHOR_CIR_OUTPUT_FULL;
}

#if APP_ANCHOR_RESPONDER_PRINTK_ENABLE
#define RESP_PRINTK(...) do { \
        if (!ss_twr_resp_full_cir_quiet()) { \
            printk(__VA_ARGS__); \
        } \
    } while (0)
#else
#define RESP_PRINTK(...) do { } while (0)
#endif

#if APP_ANCHOR_RESPONDER_PROFILE_ENABLE
#define RESP_PROF_PRINTK(...) do { \
        if (!ss_twr_resp_full_cir_quiet()) { \
            printk(__VA_ARGS__); \
        } \
    } while (0)
#else
#define RESP_PROF_PRINTK(...) do { } while (0)
#endif

#if APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE
#define RESP_FRAME_PRINTK(...) do { \
        if (!ss_twr_resp_full_cir_quiet()) { \
            printk(__VA_ARGS__); \
        } \
    } while (0)
#else
#define RESP_FRAME_PRINTK(...) do { } while (0)
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
static uint8_t ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_V3_FRAME_LEN];
static uint16_t ss_twr_resp_local_addr;
static uint8_t ss_twr_resp_anchor_id;
static int ss_twr_resp_allow_tag_polls;

static bool ss_twr_resp_frame_diag_should_log(uint32_t count)
{
#if APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE
    return count <= 20U || (count % 100U) == 0U;
#else
    ARG_UNUSED(count);
    return false;
#endif
}

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

struct ss_twr_resp_match_diag {
    uint16_t last_src_addr;
    uint16_t last_dst_addr;
    uint16_t last_resp_dst_addr;
    uint16_t last_resp_src_addr;
    uint8_t last_code;
    uint8_t last_frame_len;
    uint8_t last_anchor_mask;
    uint8_t last_poll_count;
    uint8_t last_poll_index;
    uint8_t last_resp_rank;
    uint32_t last_resp_delay_uus;
    uint32_t matched_broadcast_count;
    uint32_t matched_unicast_count;
    uint32_t mask_miss_count;
    uint32_t tx_ok_count;
    uint32_t tx_miss_count;
    uint32_t hpdwarn_count;
    uint32_t txpute_count;
    uint32_t last_tx_status_before_clear;
    uint32_t last_tx_status_after_clear;
    uint32_t last_tx_status_after_start;
    uint32_t last_tx_status_at_done;
    uint16_t last_tx_check_hi16;
    uint32_t max_tx_wait_cycles;
    uint8_t last_starttx_ok;
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
                                      const uint32 tag_tx_miss_count[SS_TWR_RESP_DIAG_TAG_SLOTS],
                                      const struct ss_twr_resp_match_diag *match_diag)
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
    RESP_PRINTK("Responder match diag anchor=%u local=0x%04x last_len=%u last_code=0x%02x last_dst=0x%04x last_src=0x%04x last_mask=0x%02x last_poll_count=%u last_poll_index=%u last_resp_delay_uus=%lu last_resp_rank=%u last_resp_dst=0x%04x last_resp_src=0x%04x matched_broadcast=%lu matched_unicast=%lu mask_miss=%lu tx_ok=%lu tx_miss=%lu hpdwarn=%lu txpute=%lu starttx_ok=%u tx_st_before=0x%08lx tx_st_clear=0x%08lx tx_st_start=0x%08lx tx_st_done=0x%08lx tx_hi16=0x%04x max_tx_wait=%lu\n",
           (unsigned int)ss_twr_resp_anchor_id,
           (unsigned int)ss_twr_resp_local_addr,
           (unsigned int)(match_diag != NULL ? match_diag->last_frame_len : 0U),
           (unsigned int)(match_diag != NULL ? match_diag->last_code : 0U),
           (unsigned int)(match_diag != NULL ? match_diag->last_dst_addr : 0U),
           (unsigned int)(match_diag != NULL ? match_diag->last_src_addr : 0U),
           (unsigned int)(match_diag != NULL ? match_diag->last_anchor_mask : 0U),
           (unsigned int)(match_diag != NULL ? match_diag->last_poll_count : 0U),
           (unsigned int)(match_diag != NULL ? match_diag->last_poll_index : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->last_resp_delay_uus : 0U),
           (unsigned int)(match_diag != NULL ? match_diag->last_resp_rank : 0U),
           (unsigned int)(match_diag != NULL ? match_diag->last_resp_dst_addr : 0U),
           (unsigned int)(match_diag != NULL ? match_diag->last_resp_src_addr : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->matched_broadcast_count : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->matched_unicast_count : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->mask_miss_count : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->tx_ok_count : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->tx_miss_count : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->hpdwarn_count : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->txpute_count : 0U),
           (unsigned int)(match_diag != NULL ? match_diag->last_starttx_ok : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->last_tx_status_before_clear : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->last_tx_status_after_clear : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->last_tx_status_after_start : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->last_tx_status_at_done : 0U),
           (unsigned int)(match_diag != NULL ? match_diag->last_tx_check_hi16 : 0U),
           (unsigned long)(match_diag != NULL ? match_diag->max_tx_wait_cycles : 0U));
}

static bool ss_twr_resp_is_broadcast_mask_miss(const uint8_t *frame,
                                               uint32_t frame_len)
{
    uint16_t dst_addr;
    uint8_t anchor_mask;
    uint8_t local_bit = 0U;

    if (frame_len <= UWB_MSG_POLL_ANCHOR_MASK_IDX ||
        frame[0] != UWB_FRAME_CTRL_LOW ||
        frame[1] != UWB_FRAME_CTRL_HIGH ||
        frame[UWB_MSG_CODE_IDX] != UWB_MSG_POLL_CODE) {
        return false;
    }

    dst_addr = uwb_frame_get_dst_addr(frame);
    if (dst_addr != UWB_BROADCAST_SHORT_ADDR) {
        return false;
    }

    anchor_mask = uwb_ss_twr_poll_anchor_mask(frame);
    if (ss_twr_resp_anchor_id < UWB_MAX_ANCHORS) {
        local_bit = (uint8_t)(1U << ss_twr_resp_anchor_id);
    }

    return anchor_mask != 0U && (anchor_mask & local_bit) == 0U;
}

static void ss_twr_resp_match_diag_observe_poll(struct ss_twr_resp_match_diag *diag,
                                                const uint8_t *frame,
                                                uint32_t frame_len)
{
    uint16_t dst_addr = uwb_frame_get_dst_addr(frame);

    diag->last_frame_len = (frame_len > UINT8_MAX) ? UINT8_MAX : (uint8_t)frame_len;
    diag->last_code = frame[UWB_MSG_CODE_IDX];
    diag->last_dst_addr = dst_addr;
    diag->last_src_addr = uwb_frame_get_src_addr(frame);
    diag->last_anchor_mask = uwb_ss_twr_poll_anchor_mask(frame);
    diag->last_poll_count = uwb_ss_twr_poll_count(frame);
    diag->last_poll_index = uwb_ss_twr_poll_index(frame);

    if (dst_addr == UWB_BROADCAST_SHORT_ADDR) {
        diag->matched_broadcast_count++;
    } else {
        diag->matched_unicast_count++;
    }
}

static void ss_twr_resp_match_diag_observe_resp(struct ss_twr_resp_match_diag *diag,
                                                uint16_t resp_dst_addr,
                                                uint16_t resp_src_addr,
                                                uint32_t resp_delay_uus,
                                                uint8_t resp_rank)
{
    diag->last_resp_dst_addr = resp_dst_addr;
    diag->last_resp_src_addr = resp_src_addr;
    diag->last_resp_delay_uus = resp_delay_uus;
    diag->last_resp_rank = resp_rank;
}

static void ss_twr_resp_match_diag_observe_tx_start(
    struct ss_twr_resp_match_diag *diag,
    uint32_t status_before_clear,
    uint32_t status_after_clear,
    uint32_t status_after_start,
    uint16_t tx_check_hi16,
    int starttx_ok)
{
    diag->last_tx_status_before_clear = status_before_clear;
    diag->last_tx_status_after_clear = status_after_clear;
    diag->last_tx_status_after_start = status_after_start;
    diag->last_tx_check_hi16 = tx_check_hi16;
    diag->last_starttx_ok = starttx_ok ? 1U : 0U;
    if ((tx_check_hi16 & (uint16_t)(SYS_STATUS_HPDWARN >> 24)) != 0U) {
        diag->hpdwarn_count++;
    }
    if ((tx_check_hi16 & (uint16_t)(SYS_STATUS_TXPUTE >> 24)) != 0U) {
        diag->txpute_count++;
    }
}

static void ss_twr_resp_match_diag_observe_tx_done(
    struct ss_twr_resp_match_diag *diag,
    uint32_t status_at_done,
    uint32_t wait_cycles)
{
    diag->last_tx_status_at_done = status_at_done;
    if (wait_cycles > diag->max_tx_wait_cycles) {
        diag->max_tx_wait_cycles = wait_cycles;
    }
}

static uint32_t ss_twr_resp_alt_bcast_delay_uus(uint8_t resp_rank)
{
#if APP_ALT_SS_TWR_TAIL_COMPRESS_ENABLE != 0U
    if (resp_rank >= APP_ALT_SS_TWR_TAIL_START_RANK &&
        APP_ALT_SS_TWR_TAIL_START_RANK > 0U) {
        return APP_ALT_SS_TWR_GUARD_US +
               ((uint32_t)(APP_ALT_SS_TWR_TAIL_START_RANK - 1U) *
                APP_ALT_SS_TWR_RESP_SPACING_US) +
               ((uint32_t)(resp_rank - (APP_ALT_SS_TWR_TAIL_START_RANK - 1U)) *
                APP_ALT_SS_TWR_TAIL_RESP_SPACING_US);
    }
#endif
    return APP_ALT_SS_TWR_GUARD_US +
           ((uint32_t)resp_rank * APP_ALT_SS_TWR_RESP_SPACING_US);
}

static uint8_t ss_twr_resp_rank_from_offset(uint8_t anchor_mask,
                                            uint8_t anchor_id,
                                            uint8_t rank_offset)
{
    uint8_t rank = 0U;

    if (anchor_id >= UWB_MAX_ANCHORS ||
        (anchor_mask & (uint8_t)(1U << anchor_id)) == 0U) {
        return 0xffU;
    }

    rank_offset %= UWB_MAX_ANCHORS;
    for (uint8_t step = 0U; step < UWB_MAX_ANCHORS; ++step) {
        uint8_t candidate =
            (uint8_t)((rank_offset + step) % UWB_MAX_ANCHORS);

        if ((anchor_mask & (uint8_t)(1U << candidate)) == 0U) {
            continue;
        }
        if (candidate == anchor_id) {
            return rank;
        }
        rank++;
    }

    return 0xffU;
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

static void ss_twr_resp_log_rx_frame(const uint8_t *frame, uint32_t frame_len,
                                     uint32_t seen_frames)
{
#if APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE
    uint16_t dst_addr = 0U;
    uint16_t src_addr = 0U;
    uint16_t pan_id = 0U;
    uint8_t code = 0U;
    uint8_t poll_index = 0U;
    uint8_t poll_count = 0U;
    uint8_t anchor_mask = 0U;

    if (!ss_twr_resp_frame_diag_should_log(seen_frames)) {
        return;
    }

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
    if (frame_len > UWB_MSG_POLL_ANCHOR_MASK_IDX) {
        poll_index = uwb_ss_twr_poll_index(frame);
        poll_count = uwb_ss_twr_poll_count(frame);
        anchor_mask = uwb_ss_twr_poll_anchor_mask(frame);
    }

    RESP_FRAME_PRINTK("Responder frame anchor=%u seen=%lu len=%lu fctrl=%02x%02x code=0x%02x pan=0x%04x dst=0x%04x src=0x%04x poll_idx=%u poll_count=%u mask=0x%02x local=0x%04x filter=%u\n",
                      (unsigned int)ss_twr_resp_anchor_id,
                      (unsigned long)seen_frames,
                      (unsigned long)frame_len,
                      (unsigned int)(frame_len > 1U ? frame[1] : 0U),
                      (unsigned int)(frame_len > 0U ? frame[0] : 0U),
                      (unsigned int)code,
                      (unsigned int)pan_id,
                      (unsigned int)dst_addr,
                      (unsigned int)src_addr,
                      (unsigned int)poll_index,
                      (unsigned int)poll_count,
                      (unsigned int)anchor_mask,
                      (unsigned int)ss_twr_resp_local_addr,
                      (unsigned int)APP_UWB_HW_FRAME_FILTER_ENABLE);
#else
    ARG_UNUSED(frame);
    ARG_UNUSED(frame_len);
    ARG_UNUSED(seen_frames);
#endif
}

static void ss_twr_resp_log_match_reject(const uint8_t *frame,
                                         uint32_t frame_len,
                                         uint32_t ignored_nonpoll_frames)
{
#if APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE
    uint16_t dst_addr = 0U;
    uint16_t src_addr = 0U;
    uint16_t pan_id = 0U;
    uint8_t code = 0U;
    uint8_t anchor_mask = 0U;
    uint8_t local_bit = 0U;
    const char *reason = "unknown";

    if (!ss_twr_resp_frame_diag_should_log(ignored_nonpoll_frames)) {
        return;
    }

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
    if (frame_len > UWB_MSG_POLL_ANCHOR_MASK_IDX) {
        anchor_mask = uwb_ss_twr_poll_anchor_mask(frame);
    }
    if (ss_twr_resp_anchor_id < UWB_MAX_ANCHORS) {
        local_bit = (uint8_t)(1U << ss_twr_resp_anchor_id);
    }

    if (frame_len <= UWB_MSG_CODE_IDX ||
        frame[0] != UWB_FRAME_CTRL_LOW ||
        frame[1] != UWB_FRAME_CTRL_HIGH) {
        reason = "bad_header";
    } else if (pan_id != APP_UWB_PAN_ID) {
        reason = "pan";
    } else if (code != UWB_MSG_POLL_CODE) {
        reason = "code";
    } else if (dst_addr == UWB_BROADCAST_SHORT_ADDR && anchor_mask == 0U) {
        reason = "broadcast_mask_zero";
    } else if (dst_addr == UWB_BROADCAST_SHORT_ADDR &&
               (anchor_mask & local_bit) == 0U) {
        reason = "broadcast_not_for_anchor";
    } else if (dst_addr != UWB_BROADCAST_SHORT_ADDR &&
               dst_addr != ss_twr_resp_local_addr) {
        reason = "dst";
    } else if (!uwb_short_addr_is_ranging_initiator(src_addr)) {
        reason = "src";
    }

    RESP_FRAME_PRINTK("Responder match reject anchor=%u count=%lu reason=%s len=%lu code=0x%02x pan=0x%04x dst=0x%04x src=0x%04x mask=0x%02x local_bit=0x%02x\n",
                      (unsigned int)ss_twr_resp_anchor_id,
                      (unsigned long)ignored_nonpoll_frames,
                      reason,
                      (unsigned long)frame_len,
                      (unsigned int)code,
                      (unsigned int)pan_id,
                      (unsigned int)dst_addr,
                      (unsigned int)src_addr,
                      (unsigned int)anchor_mask,
                      (unsigned int)local_bit);
#else
    ARG_UNUSED(frame);
    ARG_UNUSED(frame_len);
    ARG_UNUSED(ignored_nonpoll_frames);
#endif
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

static void ss_twr_resp_write_u16(uint8_t *field, uint16_t value)
{
    field[0] = (uint8_t)(value & 0xffU);
    field[1] = (uint8_t)(value >> 8);
}

#if APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE != 0U
static void ss_twr_resp_write_diag_v2(const dwt_rxdiag_t *diag, uint8_t flags)
{
    if (diag == NULL) {
        ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_VERSION_IDX] = 0U;
        ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_FLAGS_IDX] = 0U;
        return;
    }

    ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_VERSION_IDX] =
        UWB_MSG_RESP_DIAG_VERSION;
    ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_FLAGS_IDX] =
        (uint8_t)(flags | UWB_MSG_RESP_DIAG_FLAGS_VALID);
    ss_twr_resp_write_u16(
        &ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_FP_INDEX_IDX],
        diag->firstPath);
    ss_twr_resp_write_u16(
        &ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_FP_AMPL1_IDX],
        diag->firstPathAmp1);
    ss_twr_resp_write_u16(
        &ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_FP_AMPL2_IDX],
        diag->firstPathAmp2);
    ss_twr_resp_write_u16(
        &ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_FP_AMPL3_IDX],
        diag->firstPathAmp3);
    ss_twr_resp_write_u16(
        &ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_CIR_PWR_IDX],
        diag->maxGrowthCIR);
    ss_twr_resp_write_u16(
        &ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_RXPACC_IDX],
        diag->rxPreamCount);
    ss_twr_resp_write_u16(
        &ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_STD_NOISE_IDX],
        diag->stdNoise);
}

#if APP_ANCHOR_RESP_POST_TX_DIAG_SIDECHANNEL_ENABLE != 0U
static void ss_twr_resp_print_post_tx_diag(uint8_t tag_id, uint8_t poll_seq,
                                           uint32_t poll_rx_ts_low32,
                                           const dwt_rxdiag_t *diag)
{
    if (diag == NULL) {
        return;
    }

    RESP_FRAME_PRINTK("APD;1;%u;%u;%u;%lu;%u;%u;%u;%u;%u;%u;%u\n",
                      (unsigned int)ss_twr_resp_anchor_id,
                      (unsigned int)tag_id,
                      (unsigned int)poll_seq,
                      (unsigned long)poll_rx_ts_low32,
                      (unsigned int)diag->firstPath,
                      (unsigned int)diag->firstPathAmp1,
                      (unsigned int)diag->firstPathAmp2,
                      (unsigned int)diag->firstPathAmp3,
                      (unsigned int)diag->maxGrowthCIR,
                      (unsigned int)diag->rxPreamCount,
                      (unsigned int)diag->stdNoise);
}
#endif
#endif

static void ss_twr_resp_prepare_resp_template(void)
{
    uwb_ss_twr_build_resp_frame(ss_twr_resp_tx_resp_msg, 0U, 0U,
                                ss_twr_resp_local_addr);
}

static void ss_twr_resp_configure_radio(void)
{
    dwt_configure(&ss_twr_resp_config);
    dwt_setpanid(APP_UWB_PAN_ID);
    dwt_setaddress16(ss_twr_resp_local_addr);
#if APP_UWB_HW_FRAME_FILTER_ENABLE
    dwt_enableframefilter(SYS_CFG_FFAD);
#else
    dwt_enableframefilter(0);
#endif
    dwt_setrxantennadelay(SS_TWR_RESP_RX_ANT_DLY);
    dwt_settxantennadelay(SS_TWR_RESP_TX_ANT_DLY);
    dwt_setleds(DWT_LEDS_ENABLE);
    dwt_setrxtimeout(0);
    dwt_setpreambledetecttimeout(0);
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_GOOD |
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
}

int ss_twr_resp_start(unsigned int anchor_id, int allow_tag_polls)
{
    uint32 status_reg;
    uint32 rx_error_count = 0U;
    uint32 replies_ok = 0U;
    uint32 ignored_tag_polls = 0U;
    uint32 ignored_nonpoll_frames = 0U;
    uint32 delayed_tx_miss_count = 0U;
    uint32 rx_good_frame_count = 0U;
    uint32 tag_poll_count[SS_TWR_RESP_DIAG_TAG_SLOTS] = {0U};
    uint32 tag_reply_count[SS_TWR_RESP_DIAG_TAG_SLOTS] = {0U};
    uint32 tag_tx_miss_count[SS_TWR_RESP_DIAG_TAG_SLOTS] = {0U};
#if APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE != 0U && \
    APP_ANCHOR_RESP_POST_TX_DIAG_PAYLOAD_DELAY_ENABLE != 0U
    dwt_rxdiag_t delayed_poll_diag;
    bool delayed_poll_diag_valid = false;
#endif
    uint32 wait_cycles = 0U;
    uint32 diag_last_ms = k_uptime_get_32();
    uint32_t prof_last_ms = k_uptime_get_32();
    struct ss_twr_resp_profile_stats prof_stats = {0};
    struct ss_twr_resp_match_diag match_diag = {0};
#if APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE != 0U
    uint8_t resp_chip_temp_raw = 0U;
    uint8_t resp_chip_vbat_raw = 0U;
    uint32_t resp_temp_read_last_ms = 0U;
    bool resp_temp_read_valid = false;
#endif

    if (anchor_id >= UWB_MAX_ANCHORS) {
        RESP_PRINTK("Invalid SS-TWR responder anchor_id=%u\n", anchor_id);
        return -1;
    }

    ss_twr_resp_anchor_id = (uint8_t)anchor_id;
    ss_twr_resp_local_addr = uwb_anchor_short_addr(ss_twr_resp_anchor_id);
    ss_twr_resp_allow_tag_polls = allow_tag_polls;

    ss_twr_resp_led_init();
    ss_twr_resp_configure_radio();
    ss_twr_resp_prepare_resp_template();
    RESP_PRINTK("SS-TWR responder ready anchor=%u addr=0x%04x allow_tag_polls=%u resp_delay_uus=%u hw_filter=%u alt=%u guard_us=%u resp_spacing_us=%u coop_ms=%u frame_diag=%u\n",
           (unsigned int)ss_twr_resp_anchor_id,
           (unsigned int)ss_twr_resp_local_addr,
           (unsigned int)(ss_twr_resp_allow_tag_polls != 0),
           (unsigned int)SS_TWR_RESP_POLL_RX_TO_RESP_TX_DLY_UUS,
           (unsigned int)APP_UWB_HW_FRAME_FILTER_ENABLE,
           (unsigned int)APP_ALT_SS_TWR_ENABLE,
           (unsigned int)APP_ALT_SS_TWR_GUARD_US,
           (unsigned int)APP_ALT_SS_TWR_RESP_SPACING_US,
           (unsigned int)APP_ANCHOR_RESPONDER_COOP_SLEEP_MS,
           (unsigned int)APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE);

    while (1) {
        uint32 frame_len;
        uint16_t poll_src_addr;
        bool pause_for_ble_ota = false;

        if (anchor_runtime_stop_requested()) {
            dwt_forcetrxoff();
            dwt_rxreset();
            ss_twr_resp_led_off();
            RESP_PRINTK("Responder %s requested anchor=%u\n",
                   anchor_runtime_dfu_requested() ? "DFU" : "stop",
                   (unsigned int)ss_twr_resp_anchor_id);
            return 0;
        }

        if (anchor_mcumgr_diag_ota_active()) {
            /* During OTA, prioritize MCUmgr from the first SMP command through
             * upload completion. A plain anchor-control BLE link must not pause
             * ranging, because Master_Anchor keeps those links open in normal
             * responder sessions.
             */
            dwt_forcetrxoff();
            k_msleep(2);
            continue;
        }

#if APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE != 0U
        /* Periodic (~30s) DW1000 chip temperature + Vbat sample, taken here at
         * the idle loop top (before RX is armed) so it never perturbs the tight
         * poll-RX -> resp-TX turnaround. Cached raw codes are embedded in the V3
         * response payload. fastSPI=1 keeps the receiver clocks up (no XTI
         * switch); worst case it delays one RX-arm by ~1ms once per 30s.
         */
        {
            uint32_t temp_now_ms = k_uptime_get_32();

            if (!resp_temp_read_valid ||
                (uint32_t)(temp_now_ms - resp_temp_read_last_ms) >= 30000U) {
                uint16_t tv_raw = dwt_readtempvbat(1);

                resp_chip_temp_raw = (uint8_t)(tv_raw >> 8);
                resp_chip_vbat_raw = (uint8_t)(tv_raw & 0xffU);
                resp_temp_read_last_ms = temp_now_ms;
                resp_temp_read_valid = true;
            }
        }
#endif

        dwt_rxenable(DWT_START_RX_IMMEDIATE);

        do {
            status_reg = dwt_read32bitreg(SYS_STATUS_ID);
            wait_cycles++;
            if ((wait_cycles & 0x3FFU) == 0U) {
                if (anchor_runtime_stop_requested()) {
                    dwt_forcetrxoff();
                    dwt_rxreset();
                    RESP_PRINTK("Responder %s requested during RX wait anchor=%u\n",
                           anchor_runtime_dfu_requested() ? "DFU" : "stop",
                           (unsigned int)ss_twr_resp_anchor_id);
                    return 0;
                }
                if (anchor_mcumgr_diag_ota_active()) {
                    pause_for_ble_ota = true;
                    dwt_forcetrxoff();
                    break;
                }
                ss_twr_resp_diag_periodic(
                    &diag_last_ms, replies_ok, rx_error_count,
                    ignored_tag_polls, ignored_nonpoll_frames,
                    delayed_tx_miss_count, tag_poll_count, tag_reply_count,
                    tag_tx_miss_count, &match_diag);
                ss_twr_resp_profile_periodic(&prof_stats, &prof_last_ms);
            }
        } while ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_ERR)) == 0U);
        if (pause_for_ble_ota) {
            k_msleep(2);
            continue;
        }
        wait_cycles = 0U;

        if ((status_reg & SYS_STATUS_RXFCG) == 0U) {
            if ((status_reg & SYS_STATUS_AFFREJ) != 0U) {
                /*
                 * In unicast-burst mode every anchor hears poll frames for the
                 * other anchors. DW1000 frame filtering reports those as
                 * AFFREJ; treating that like a PHY error with rxreset/sleep
                 * makes the responder miss the next poll in the burst.
                 */
                dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_AFFREJ);
                continue;
            }
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
                tag_reply_count, tag_tx_miss_count, &match_diag);
            ss_twr_resp_profile_periodic(&prof_stats, &prof_last_ms);
            continue;
        }

        uint32_t prof_rx_cyc = k_cycle_get_32();

        frame_len = dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFL_MASK_1023;
        if (frame_len > sizeof(ss_twr_resp_rx_buffer)) {
            dwt_forcetrxoff();
            dwt_rxreset();
            continue;
        }

        dwt_readrxdata(ss_twr_resp_rx_buffer, (uint16)frame_len, 0);
        uint32_t prof_frame_cyc = k_cycle_get_32();
        rx_good_frame_count++;
        ss_twr_resp_log_rx_frame(ss_twr_resp_rx_buffer, frame_len,
                                 rx_good_frame_count);

        bool poll_match = (ss_twr_resp_allow_tag_polls != 0) ?
            uwb_ss_twr_poll_matches(ss_twr_resp_rx_buffer,
                                    ss_twr_resp_local_addr) :
            ss_twr_resp_matrix_poll_matches(ss_twr_resp_rx_buffer,
                                            ss_twr_resp_local_addr);

        if (!poll_match) {
            ignored_nonpoll_frames++;
            if (ss_twr_resp_is_broadcast_mask_miss(ss_twr_resp_rx_buffer,
                                                   frame_len)) {
                match_diag.mask_miss_count++;
            }
            ss_twr_resp_log_match_reject(ss_twr_resp_rx_buffer, frame_len,
                                         ignored_nonpoll_frames);
            if (APP_ANCHOR_VERBOSE_RESPONDER_ERRORS != 0U) {
                ss_twr_resp_log_unexpected_frame(ss_twr_resp_rx_buffer, frame_len,
                                                 ignored_nonpoll_frames);
            }
            ss_twr_resp_diag_periodic(
                &diag_last_ms, replies_ok, rx_error_count, ignored_tag_polls,
                ignored_nonpoll_frames, delayed_tx_miss_count, tag_poll_count,
                tag_reply_count, tag_tx_miss_count, &match_diag);
            ss_twr_resp_profile_periodic(&prof_stats, &prof_last_ms);
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
                tag_reply_count, tag_tx_miss_count, &match_diag);
            continue;
        }

        dwtime_u64_t poll_rx_ts = ss_twr_resp_get_rx_timestamp_u64();
        int32_t rx_carrier_integrator = 0;
        uint16_t resp_frame_len = UWB_MSG_RESP_V1_FRAME_LEN;
#if APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE != 0U
        dwt_rxdiag_t poll_rx_diag;
        bool poll_rx_diag_valid = false;
        bool poll_rx_diag_delayed = false;
#endif
        uint32_t prof_ts_cyc = k_cycle_get_32();

        uint32_t resp_delay_uus = poll_src_is_tag ?
            SS_TWR_RESP_POLL_RX_TO_RESP_TX_DLY_UUS :
            APP_ANCHOR_MATRIX_RESP_DELAY_UUS;
        uint8_t resp_rank = 0xffU;
        ss_twr_resp_match_diag_observe_poll(&match_diag, ss_twr_resp_rx_buffer,
                                            frame_len);
#if APP_ALT_SS_TWR_ENABLE
        /*
         * Alt SS-TWR broadcast poll: one poll carries the participating anchor
         * mask. All selected anchors receive the same measurement instant, then
         * respond by rank in the mask. Legacy/unicast burst polls keep the older
         * index-based fallback for compatibility while this experiment evolves.
         */
        if (poll_src_is_tag) {
            uint8_t alt_poll_count = uwb_ss_twr_poll_count(ss_twr_resp_rx_buffer);
            uint8_t alt_poll_index = uwb_ss_twr_poll_index(ss_twr_resp_rx_buffer);
            uint8_t alt_anchor_mask = uwb_ss_twr_poll_anchor_mask(ss_twr_resp_rx_buffer);
            if (alt_anchor_mask != 0U &&
                (alt_anchor_mask & (uint8_t)(1U << ss_twr_resp_anchor_id)) != 0U) {
                resp_rank = ss_twr_resp_rank_from_offset(
                    alt_anchor_mask, ss_twr_resp_anchor_id,
                    uwb_ss_twr_poll_rank_offset(ss_twr_resp_rx_buffer));
                resp_delay_uus = ss_twr_resp_alt_bcast_delay_uus(resp_rank);
            } else if (alt_poll_count > 0U && alt_poll_index < alt_poll_count) {
                resp_rank = alt_poll_index;
                uint32_t alt_unicast_poll_slot_us =
                    APP_ALT_SS_TWR_POLL_SPACING_US +
                    APP_ALT_SS_TWR_UNICAST_POLL_REARM_US;
                resp_delay_uus =
                    ((uint32_t)(alt_poll_count - 1U - alt_poll_index) *
                     alt_unicast_poll_slot_us) +
                    APP_ALT_SS_TWR_GUARD_US +
                    ((uint32_t)alt_poll_index * APP_ALT_SS_TWR_RESP_SPACING_US);
            }
        }
#endif
#if APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE != 0U
#if APP_ANCHOR_RESP_POST_TX_DIAG_PAYLOAD_DELAY_ENABLE != 0U
        /* fixed-a19 (ALL ranks): in deferred-diag mode NEVER read diagnostics
         * before starttx. The pre-TX dwt_readdiagnostics (~55-90us @ 8MHz SPI)
         * was busting the delayed-TX deadline for tag polls (~50% coin-flip).
         * The read is moved AFTER starttx (below) and pipelined into the NEXT
         * response from this anchor, marked DELAYED. */
        bool diag_v2_allowed = false;
#else
        bool diag_v2_allowed = poll_src_is_tag;
#endif
        bool diag_v2_frame = poll_src_is_tag;
#if APP_ANCHOR_RESP_RANK0_FAST_TX_ENABLE != 0U
        if (resp_rank == 0U) {
            diag_v2_allowed = false;
        }
#endif
#if APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_SKIP_RANK0_ENABLE != 0U
        if (resp_rank == 0U) {
            diag_v2_allowed = false;
        }
#endif
        if (diag_v2_frame) {
            resp_frame_len = UWB_MSG_RESP_V3_FRAME_LEN;
        }
        if (diag_v2_allowed) {
            dwt_readdiagnostics(&poll_rx_diag);
            poll_rx_diag_valid = true;
        } else if (diag_v2_frame
#if APP_ANCHOR_RESP_POST_TX_DIAG_PAYLOAD_DELAY_ENABLE != 0U
                   /* fixed-a19: ALL ranks consume the pipelined (previous-poll)
                    * diag, not just rank0. First poll / post-drop: cache is
                    * invalid -> falls through to the invalid-diag path so the
                    * host DROPS that frame (never fills stale/misaligned data). */
                   && delayed_poll_diag_valid
#else
                   && false
#endif
                   ) {
#if APP_ANCHOR_RESP_POST_TX_DIAG_PAYLOAD_DELAY_ENABLE != 0U
            poll_rx_diag = delayed_poll_diag;
            poll_rx_diag_valid = true;
            poll_rx_diag_delayed = true;
            delayed_poll_diag_valid = false;
#endif
        }
#endif
        uint32 resp_tx_time =
            (uint32)((poll_rx_ts +
                      ((dwtime_u64_t)resp_delay_uus *
                       SS_TWR_RESP_UUS_TO_DWT_TIME)) >>
                     8);
        dwtime_u64_t resp_tx_ts =
            (((dwtime_u64_t)(resp_tx_time & 0xFFFFFFFEUL)) << 8) +
            SS_TWR_RESP_TX_ANT_DLY;

        ss_twr_resp_tx_resp_msg[SS_TWR_RESP_MSG_SN_IDX] =
            ss_twr_resp_frame_seq_nb;
        ss_twr_resp_write_u16(&ss_twr_resp_tx_resp_msg[UWB_MSG_DST_IDX],
                              poll_src_addr);
        ss_twr_resp_write_u16(&ss_twr_resp_tx_resp_msg[UWB_MSG_SRC_IDX],
                              ss_twr_resp_local_addr);
        ss_twr_resp_match_diag_observe_resp(&match_diag, poll_src_addr,
                                            ss_twr_resp_local_addr,
                                            resp_delay_uus, resp_rank);
        ss_twr_resp_write_ts(
            &ss_twr_resp_tx_resp_msg[SS_TWR_RESP_POLL_RX_TS_IDX], poll_rx_ts);
        ss_twr_resp_write_ts(
            &ss_twr_resp_tx_resp_msg[SS_TWR_RESP_RESP_TX_TS_IDX], resp_tx_ts);
#if APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE != 0U
        if (poll_rx_diag_valid) {
            uint8_t diag_flags = UWB_MSG_RESP_DIAG_FLAGS_VALID;
            if (poll_rx_diag_delayed) {
                diag_flags |= UWB_MSG_RESP_DIAG_FLAGS_DELAYED;
            }
            ss_twr_resp_write_diag_v2(&poll_rx_diag, diag_flags);
        } else if (diag_v2_frame) {
            ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_VERSION_IDX] = 0U;
            ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_FLAGS_IDX] = 0U;
        }
        /* Chip temp + Vbat ride in every V3 (tag-facing) response, independent
         * of poll-RX diag validity (it's the responder's own chip state).
         */
        if (diag_v2_frame) {
            ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_TEMP_IDX] =
                resp_chip_temp_raw;
            ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_VBAT_IDX] =
                resp_chip_vbat_raw;
        }
#else
        memset(&ss_twr_resp_tx_resp_msg[UWB_MSG_RESP_DIAG_VERSION_IDX], 0,
               UWB_MSG_RESP_V2_FRAME_LEN - UWB_MSG_RESP_DIAG_VERSION_IDX);
#endif

        if (dwt_writetxdata(resp_frame_len,
                            ss_twr_resp_tx_resp_msg, 0) != DWT_SUCCESS) {
            if (APP_ANCHOR_VERBOSE_RESPONDER_ERRORS != 0U) {
                RESP_PRINTK("Responder TX buffer write failed\n");
            }
            ss_twr_resp_diag_periodic(
                &diag_last_ms, replies_ok, rx_error_count, ignored_tag_polls,
                ignored_nonpoll_frames, delayed_tx_miss_count, tag_poll_count,
                tag_reply_count, tag_tx_miss_count, &match_diag);
            continue;
        }

        dwt_writetxfctrl(resp_frame_len, 0, 1);
        dwt_setdelayedtrxtime(resp_tx_time);
        uint32_t prof_txprog_cyc = k_cycle_get_32();
#if APP_ANCHOR_RESPONDER_PROFILE_ENABLE || APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE
        int32_t prof_slack_uus = ss_twr_resp_slack_uus(resp_tx_time);
#else
        int32_t prof_slack_uus = 0;
#endif

        uint32_t prof_starttx_cyc = k_cycle_get_32();
        ss_twr_resp_led_on();
        int starttx_ok = (dwt_starttx(DWT_START_TX_DELAYED) == DWT_SUCCESS);
        uint32_t prof_start_done_cyc = k_cycle_get_32();
        uint32_t prof_starttx_us =
            ss_twr_resp_elapsed_us(prof_starttx_cyc, prof_start_done_cyc);

        /*
         * The delayed-TX deadline is now behind us, so diagnostics and stale
         * status cleanup can happen without making rank0 late.
         */
#if APP_ANCHOR_RESPONDER_PRINTK_ENABLE || APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE
        uint32_t tx_status_before_clear = dwt_read32bitreg(SYS_STATUS_ID);
#else
        uint32_t tx_status_before_clear = 0U;
#endif
        dwt_write32bitreg(SYS_STATUS_ID,
                          SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_GOOD |
                              SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO |
                              SYS_STATUS_HPDWARN);
#if APP_ANCHOR_RESPONDER_PRINTK_ENABLE || APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE
        uint32_t tx_status_after_clear = dwt_read32bitreg(SYS_STATUS_ID);
        uint32_t tx_status_after_start = dwt_read32bitreg(SYS_STATUS_ID);
        uint16_t tx_check_hi16 = dwt_read16bitoffsetreg(SYS_STATUS_ID, 3);
#else
        uint32_t tx_status_after_clear = 0U;
        uint32_t tx_status_after_start = 0U;
        uint16_t tx_check_hi16 = 0U;
#endif
        ss_twr_resp_match_diag_observe_tx_start(
            &match_diag, tx_status_before_clear, tx_status_after_clear,
            tx_status_after_start, tx_check_hi16, starttx_ok);
#if APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE != 0U && \
    APP_ANCHOR_RESP_POST_TX_DIAG_READ_ENABLE != 0U
        if (starttx_ok && poll_src_is_tag) {
            /* fixed-a19: read poll-RX diagnostics AFTER the delayed TX is safely
             * started (deadline already met), for EVERY rank, and cache it for
             * the next response from THIS anchor (one-poll pipeline). Single
             * per-anchor var: each anchor MCU runs its own ss_twr_resp_start, so
             * this is NOT rank-indexed (rank rotates per poll, must not index). */
            dwt_rxdiag_t post_tx_diag;
            dwt_readdiagnostics(&post_tx_diag);
#if APP_ANCHOR_RESP_POST_TX_DIAG_PAYLOAD_DELAY_ENABLE != 0U
            delayed_poll_diag = post_tx_diag;
            delayed_poll_diag_valid = true;
#endif
#if APP_ANCHOR_RESP_POST_TX_DIAG_SIDECHANNEL_ENABLE != 0U
            ss_twr_resp_print_post_tx_diag(poll_tag_id,
                                           ss_twr_resp_rx_buffer[SS_TWR_RESP_MSG_SN_IDX],
                                           (uint32_t)poll_rx_ts,
                                           &post_tx_diag);
#endif
        }
#endif
        ss_twr_resp_profile_observe(
            &prof_stats, prof_rx_cyc, prof_frame_cyc, prof_ts_cyc,
            prof_txprog_cyc, prof_start_done_cyc, prof_slack_uus,
            prof_starttx_us, starttx_ok);
        uint32_t tx_diag_count = poll_src_is_tag &&
                                 poll_tag_id < SS_TWR_RESP_DIAG_TAG_SLOTS
                                     ? tag_poll_count[poll_tag_id]
                                     : replies_ok + delayed_tx_miss_count + 1U;
        if (APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE != 0U &&
            ss_twr_resp_frame_diag_should_log(tx_diag_count)) {
            RESP_FRAME_PRINTK("Responder tx attempt anchor=%u tag=%u src=0x%04x delay_uus=%lu rank=%u slack_uus=%ld starttx_ok=%d starttx_us=%lu tx_st_before=0x%08lx tx_st_clear=0x%08lx tx_st_start=0x%08lx tx_hi16=0x%04x\n",
                              (unsigned int)ss_twr_resp_anchor_id,
                              (unsigned int)poll_tag_id,
                              (unsigned int)poll_src_addr,
                              (unsigned long)resp_delay_uus,
                              (unsigned int)resp_rank,
                              (long)prof_slack_uus,
                              starttx_ok,
                              (unsigned long)prof_starttx_us,
                              (unsigned long)tx_status_before_clear,
                              (unsigned long)tx_status_after_clear,
                              (unsigned long)tx_status_after_start,
                              (unsigned int)tx_check_hi16);
        }

        if (!starttx_ok) {
            ss_twr_resp_led_off();
            delayed_tx_miss_count++;
            match_diag.tx_miss_count++;
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
                tag_reply_count, tag_tx_miss_count, &match_diag);
            ss_twr_resp_profile_periodic(&prof_stats, &prof_last_ms);
            continue;
        }

        uint32_t tx_status_at_done = 0U;
        while (((tx_status_at_done = dwt_read32bitreg(SYS_STATUS_ID)) &
                SYS_STATUS_TXFRS) == 0U) {
            wait_cycles++;
            if ((wait_cycles & 0x3FFU) == 0U) {
                if (anchor_runtime_stop_requested()) {
                    dwt_forcetrxoff();
                    dwt_rxreset();
                    ss_twr_resp_led_off();
                    RESP_PRINTK("Responder %s requested during TX wait anchor=%u\n",
                           anchor_runtime_dfu_requested() ? "DFU" : "stop",
                           (unsigned int)ss_twr_resp_anchor_id);
                    return 0;
                }
            }
        }
        ss_twr_resp_match_diag_observe_tx_done(
            &match_diag, tx_status_at_done, wait_cycles);
        wait_cycles = 0U;

        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);
        ss_twr_resp_led_off();
        replies_ok++;
        match_diag.tx_ok_count++;
        enum anchor_cir_output_mode cir_mode = anchor_cir_output_get_mode();
        if (cir_mode != ANCHOR_CIR_OUTPUT_OFF) {
            dwt_rxdiag_t rx_diag;

            rx_carrier_integrator = dwt_readcarrierintegrator();
            dwt_readdiagnostics(&rx_diag);
            anchor_cir_output_publish_feature(
                replies_ok, ss_twr_resp_anchor_id, poll_src_addr, -1L,
                (uint32_t)poll_rx_ts, rx_carrier_integrator, &rx_diag);
            anchor_cir_output_publish_full(
                replies_ok, ss_twr_resp_anchor_id, poll_src_addr, -1L,
                (uint32_t)poll_rx_ts, rx_carrier_integrator, &rx_diag);
        }
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
            tag_reply_count, tag_tx_miss_count, &match_diag);
        ss_twr_resp_profile_periodic(&prof_stats, &prof_last_ms);
        ss_twr_resp_frame_seq_nb++;
    }
}
