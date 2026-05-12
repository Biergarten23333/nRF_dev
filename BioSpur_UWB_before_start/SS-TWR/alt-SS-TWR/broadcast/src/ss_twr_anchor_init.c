#include "ss_twr_anchor_init.h"

#include "anchor_ble_ctrl.h"
#include "anchor_runtime_control.h"
#include "uwb_anchor_matrix.h"
#include "uwb_anchor_topology.h"
#include "uwb_range_tracker.h"
#include "uwb_ss_twr_shared.h"

#include <string.h>

#include <deca_device_api.h>
#include <deca_regs.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#define SS_TWR_ANCHOR_INIT_TX_ANT_DLY 16436U
#define SS_TWR_ANCHOR_INIT_RX_ANT_DLY 16436U

#define SS_TWR_ANCHOR_INIT_RNG_DELAY_MS 0U
#ifndef APP_ANCHOR_MATRIX_TX_TO_RX_DLY_UUS
#define APP_ANCHOR_MATRIX_TX_TO_RX_DLY_UUS 140U
#endif

#ifndef APP_ANCHOR_MATRIX_RESP_RX_TIMEOUT_UUS
#define APP_ANCHOR_MATRIX_RESP_RX_TIMEOUT_UUS 1500U
#endif

#define SS_TWR_ANCHOR_INIT_RX_BUF_LEN 127U
#define SS_TWR_ANCHOR_INIT_RESP_MSG_POLL_RX_TS_IDX 10U
#define SS_TWR_ANCHOR_INIT_RESP_MSG_RESP_TX_TS_IDX 14U
#define SS_TWR_ANCHOR_INIT_RESP_MSG_TS_LEN 4U

#ifndef APP_UWB_HW_FRAME_FILTER_ENABLE
#define APP_UWB_HW_FRAME_FILTER_ENABLE 1U
#endif

#define SS_TWR_ANCHOR_INIT_SPEED_OF_LIGHT 299702547.0
#define SS_TWR_ANCHOR_INIT_RANGE_FILTER_OUTLIER_MM 450U
#define SS_TWR_ANCHOR_INIT_SW_LINE_MAX 256U

static dwt_config_t ss_twr_anchor_init_config = {
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

static uint8_t ss_twr_anchor_init_frame_seq_nb;
static uint8_t ss_twr_anchor_init_rx_buffer[SS_TWR_ANCHOR_INIT_RX_BUF_LEN];
static uint8_t ss_twr_anchor_init_tx_poll_msg[13];
static uint16_t ss_twr_anchor_init_local_addr;
static uint8_t ss_twr_anchor_init_local_id;
static struct uwb_range_tracker ss_twr_anchor_init_trackers[UWB_MAX_ANCHORS];
static uint8_t ss_twr_anchor_init_peer_ids[UWB_MAX_ANCHORS];
static size_t ss_twr_anchor_init_peer_count;
static size_t ss_twr_anchor_init_peer_index;
static size_t ss_twr_anchor_init_sweep_count;
static struct uwb_anchor_matrix ss_twr_anchor_init_matrix;

static inline void ss_twr_anchor_init_range_delay(void)
{
    if (SS_TWR_ANCHOR_INIT_RNG_DELAY_MS > 0U) {
        k_msleep(SS_TWR_ANCHOR_INIT_RNG_DELAY_MS);
    }
}

static void ss_twr_anchor_init_publish_sweep_summary(void)
{
    char line[SS_TWR_ANCHOR_INIT_SW_LINE_MAX];
    size_t used;

    used = (size_t)snprintk(line, sizeof(line), "SW-%c",
                            uwb_anchor_label(ss_twr_anchor_init_local_id));
    if (used >= sizeof(line)) {
        return;
    }

    for (uint8_t peer_id = 0U; peer_id < UWB_MAX_ANCHORS; ++peer_id) {
        const struct uwb_anchor_matrix_cell *cell;

        if (peer_id == ss_twr_anchor_init_local_id) {
            continue;
        }

        cell = &ss_twr_anchor_init_matrix.cells[ss_twr_anchor_init_local_id][peer_id];
        used += (size_t)snprintk(
            &line[used], sizeof(line) - used, ",%c,%lu,%u",
            uwb_anchor_label(peer_id),
            (unsigned long)(cell->valid ? cell->last_raw_mm : 0U),
            (unsigned int)(cell->valid ? cell->quality_percent : 0U));
        if (used >= sizeof(line)) {
            line[sizeof(line) - 1U] = '\0';
            break;
        }
    }

    printk("Anchor sweep summary prepared: %s\n", line);
    (void)anchor_ble_ctrl_publish_result_line(line);
}

static void ss_twr_anchor_init_read_ts(const uint8_t *ts_field, uint32_t *ts)
{
    *ts = 0U;

    for (int i = 0; i < SS_TWR_ANCHOR_INIT_RESP_MSG_TS_LEN; ++i) {
        *ts |= ((uint32_t)ts_field[i]) << (i * 8);
    }
}

static void ss_twr_anchor_init_configure_radio(void)
{
    dwt_configure(&ss_twr_anchor_init_config);
    dwt_setpanid(APP_UWB_PAN_ID);
    dwt_setaddress16(ss_twr_anchor_init_local_addr);
#if APP_UWB_HW_FRAME_FILTER_ENABLE
    dwt_enableframefilter(SYS_CFG_FFAD);
#else
    dwt_enableframefilter(0);
#endif
    dwt_setrxantennadelay(SS_TWR_ANCHOR_INIT_RX_ANT_DLY);
    dwt_settxantennadelay(SS_TWR_ANCHOR_INIT_TX_ANT_DLY);
    dwt_setleds(DWT_LEDS_ENABLE);
    dwt_setrxaftertxdelay(APP_ANCHOR_MATRIX_TX_TO_RX_DLY_UUS);
    dwt_setrxtimeout(APP_ANCHOR_MATRIX_RESP_RX_TIMEOUT_UUS);
    dwt_setpreambledetecttimeout(0);
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_GOOD |
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
}

static bool ss_twr_anchor_init_raw_range_plausible(
    const struct uwb_range_tracker *tracker, uint32_t raw_mm)
{
    ARG_UNUSED(tracker);
    return raw_mm != 0U;
}

int ss_twr_anchor_init_start(unsigned int anchor_id, const uint8_t *peer_ids,
                             size_t peer_count, uint32_t max_sweeps)
{
    if (anchor_id >= UWB_MAX_ANCHORS || peer_ids == NULL || peer_count == 0U ||
        peer_count > UWB_MAX_ANCHORS) {
        printk("Invalid anchor master config anchor=%u peers=%u\n", anchor_id,
               (unsigned int)peer_count);
        return -1;
    }

    ss_twr_anchor_init_local_id = (uint8_t)anchor_id;
    ss_twr_anchor_init_local_addr =
        uwb_anchor_short_addr(ss_twr_anchor_init_local_id);
    ss_twr_anchor_init_peer_count = peer_count;
    ss_twr_anchor_init_peer_index = 0U;
    ss_twr_anchor_init_sweep_count = 0U;
    uwb_anchor_matrix_init(&ss_twr_anchor_init_matrix);

    for (size_t i = 0; i < peer_count; ++i) {
        if (peer_ids[i] >= UWB_MAX_ANCHORS || peer_ids[i] == anchor_id) {
            printk("Invalid peer anchor id: %u\n", (unsigned int)peer_ids[i]);
            return -1;
        }

        ss_twr_anchor_init_peer_ids[i] = peer_ids[i];
        uwb_range_tracker_init(&ss_twr_anchor_init_trackers[i],
                               uwb_anchor_short_addr(peer_ids[i]));
    }

    printk("Anchor master ready anchor=%c(%u) addr=0x%04x peer_count=%u\n",
           uwb_anchor_label(ss_twr_anchor_init_local_id),
           (unsigned int)ss_twr_anchor_init_local_id,
           (unsigned int)ss_twr_anchor_init_local_addr,
           (unsigned int)ss_twr_anchor_init_peer_count);
    if (max_sweeps != 0U) {
        printk("Anchor master finite sweep limit %c sets=%lu\n",
               uwb_anchor_label(ss_twr_anchor_init_local_id),
               (unsigned long)max_sweeps);
    }
    ss_twr_anchor_init_configure_radio();

    while (1) {
        uint8_t current_peer_id =
            ss_twr_anchor_init_peer_ids[ss_twr_anchor_init_peer_index];
        uint16_t current_peer_addr = uwb_anchor_short_addr(current_peer_id);
        struct uwb_range_tracker *tracker =
            &ss_twr_anchor_init_trackers[ss_twr_anchor_init_peer_index];
        uint32_t status_reg;

        if (anchor_runtime_stop_requested()) {
            dwt_forcetrxoff();
            dwt_rxreset();
            printk("Anchor master stop requested for %c\n",
                   uwb_anchor_label(ss_twr_anchor_init_local_id));
            return 0;
        }

        uwb_ss_twr_build_poll_frame(ss_twr_anchor_init_tx_poll_msg,
                                    ss_twr_anchor_init_frame_seq_nb,
                                    current_peer_addr,
                                    ss_twr_anchor_init_local_addr);

        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);

        if (dwt_writetxdata(sizeof(ss_twr_anchor_init_tx_poll_msg),
                            ss_twr_anchor_init_tx_poll_msg, 0) != DWT_SUCCESS) {
            printk("Anchor master TX buffer write failed\n");
            ss_twr_anchor_init_range_delay();
            continue;
        }

        dwt_writetxfctrl(sizeof(ss_twr_anchor_init_tx_poll_msg), 0, 1);

        if (dwt_starttx(DWT_START_TX_IMMEDIATE | DWT_RESPONSE_EXPECTED) !=
            DWT_SUCCESS) {
            printk("Anchor master TX start failed peer=%u\n",
                   (unsigned int)current_peer_id);
            dwt_forcetrxoff();
            ss_twr_anchor_init_range_delay();
            continue;
        }

        do {
            status_reg = dwt_read32bitreg(SYS_STATUS_ID);
            if (anchor_runtime_stop_requested()) {
                dwt_forcetrxoff();
                dwt_rxreset();
                printk("Anchor master stop requested during RX wait for %c\n",
                       uwb_anchor_label(ss_twr_anchor_init_local_id));
                return 0;
            }
        } while ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_TO |
                                SYS_STATUS_ALL_RX_ERR)) == 0U);

        ss_twr_anchor_init_frame_seq_nb++;

        if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
            uint16_t resp_src_addr;
            uint32_t frame_len;
            uint32_t poll_tx_ts;
            uint32_t resp_rx_ts;
            uint32_t poll_rx_ts;
            uint32_t resp_tx_ts;
            int32_t rtd_init;
            int32_t rtd_resp;
            double tof;
            double distance_m;
            double clock_offset_ratio;
            long raw_distance_mm;
            uint32_t filtered_mm;

            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);

            frame_len = dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFLEN_MASK;
            if (frame_len > sizeof(ss_twr_anchor_init_rx_buffer)) {
                dwt_forcetrxoff();
                dwt_rxreset();
                ss_twr_anchor_init_range_delay();
                continue;
            }

            memset(ss_twr_anchor_init_rx_buffer, 0,
                   sizeof(ss_twr_anchor_init_rx_buffer));
            dwt_readrxdata(ss_twr_anchor_init_rx_buffer, (uint16_t)frame_len, 0);
            ss_twr_anchor_init_rx_buffer[UWB_MSG_SN_IDX] = 0U;

            if (!uwb_ss_twr_resp_matches(ss_twr_anchor_init_rx_buffer,
                                         ss_twr_anchor_init_local_addr,
                                         current_peer_addr)) {
#if APP_TAG_VERBOSE_MEASUREMENTS
                printk("Anchor master unexpected frame src=0x%04x dst=0x%04x code=0x%02x\n",
                       (unsigned int)uwb_frame_get_src_addr(
                           ss_twr_anchor_init_rx_buffer),
                       (unsigned int)uwb_frame_get_dst_addr(
                           ss_twr_anchor_init_rx_buffer),
                       (unsigned int)ss_twr_anchor_init_rx_buffer[UWB_MSG_CODE_IDX]);
#endif
                ss_twr_anchor_init_range_delay();
                continue;
            }

            poll_tx_ts = dwt_readtxtimestamplo32();
            resp_rx_ts = dwt_readrxtimestamplo32();
            clock_offset_ratio =
                (double)dwt_readcarrierintegrator() *
                (FREQ_OFFSET_MULTIPLIER * HERTZ_TO_PPM_MULTIPLIER_CHAN_5 /
                 1.0e6);

            ss_twr_anchor_init_read_ts(
                &ss_twr_anchor_init_rx_buffer
                     [SS_TWR_ANCHOR_INIT_RESP_MSG_POLL_RX_TS_IDX],
                &poll_rx_ts);
            ss_twr_anchor_init_read_ts(
                &ss_twr_anchor_init_rx_buffer
                     [SS_TWR_ANCHOR_INIT_RESP_MSG_RESP_TX_TS_IDX],
                &resp_tx_ts);

            rtd_init = (int32_t)(resp_rx_ts - poll_tx_ts);
            rtd_resp = (int32_t)(resp_tx_ts - poll_rx_ts);
            tof = ((rtd_init - rtd_resp * (1.0 - clock_offset_ratio)) / 2.0) *
                  DWT_TIME_UNITS;
            distance_m = tof * SS_TWR_ANCHOR_INIT_SPEED_OF_LIGHT;
            raw_distance_mm = (long)(distance_m * 1000.0);
            if (raw_distance_mm < 0L) {
                raw_distance_mm = 0L;
            }

            if (!ss_twr_anchor_init_raw_range_plausible(
                    tracker, (uint32_t)raw_distance_mm)) {
                if (tracker->filtered_valid) {
                    uwb_range_tracker_record_failure(tracker);
                }
                printk("Anchor master reject peer=%u addr=0x%04x raw=%ld mm last=%lu mm ok=%lu fail=%lu q=%u%%\n",
                       (unsigned int)current_peer_id,
                       (unsigned int)current_peer_addr,
                       raw_distance_mm,
                       (unsigned long)tracker->last_raw_mm,
                       (unsigned long)tracker->success_count,
                       (unsigned long)tracker->failure_count,
                       (unsigned int)uwb_range_tracker_quality_percent(tracker));
                ss_twr_anchor_init_range_delay();
                continue;
            }

            filtered_mm = uwb_range_tracker_record_success(
                tracker, (uint32_t)raw_distance_mm);
            resp_src_addr = uwb_frame_get_src_addr(ss_twr_anchor_init_rx_buffer);

            uwb_anchor_matrix_update(
                &ss_twr_anchor_init_matrix, ss_twr_anchor_init_local_id,
                uwb_anchor_id_from_addr(resp_src_addr), tracker);

            printk("Matrix %c-%c addr=0x%04x raw=%ld mm last=%lu mm ok=%lu fail=%lu q=%u%%\n",
                   uwb_anchor_label(ss_twr_anchor_init_local_id),
                   uwb_anchor_label(uwb_anchor_id_from_addr(resp_src_addr)),
                   (unsigned int)resp_src_addr, raw_distance_mm,
                   (unsigned long)filtered_mm,
                   (unsigned long)tracker->success_count,
                   (unsigned long)tracker->failure_count,
                   (unsigned int)uwb_range_tracker_quality_percent(tracker));
        } else {
            if (tracker->filtered_valid) {
                uwb_range_tracker_record_failure(tracker);
            }
            printk("Anchor master timeout peer=%u addr=0x%04x status=0x%08lx ok=%lu fail=%lu q=%u%%\n",
                   (unsigned int)current_peer_id, (unsigned int)current_peer_addr,
                   (unsigned long)status_reg,
                   (unsigned long)tracker->success_count,
                   (unsigned long)tracker->failure_count,
                   (unsigned int)uwb_range_tracker_quality_percent(tracker));
            dwt_write32bitreg(SYS_STATUS_ID,
                              SYS_STATUS_ALL_RX_TO | SYS_STATUS_ALL_RX_ERR);
            dwt_rxreset();
        }

        ss_twr_anchor_init_peer_index =
            (ss_twr_anchor_init_peer_index + 1U) % ss_twr_anchor_init_peer_count;
        if (ss_twr_anchor_init_peer_index == 0U) {
            ss_twr_anchor_init_sweep_count++;
            printk("Anchor sweep %u complete for %c\n",
                   (unsigned int)ss_twr_anchor_init_sweep_count,
                   uwb_anchor_label(ss_twr_anchor_init_local_id));
            uwb_anchor_matrix_print_row(&ss_twr_anchor_init_matrix,
                                        ss_twr_anchor_init_local_id);
            uwb_anchor_matrix_print_valid_edges(&ss_twr_anchor_init_matrix);
            ss_twr_anchor_init_publish_sweep_summary();
            if (max_sweeps != 0U &&
                ss_twr_anchor_init_sweep_count >= (size_t)max_sweeps) {
                char done_line[80];

                dwt_forcetrxoff();
                dwt_rxreset();
                snprintk(done_line, sizeof(done_line),
                         "SWEEP_DONE master=%c sets=%lu role=matrix",
                         uwb_anchor_label(ss_twr_anchor_init_local_id),
                         (unsigned long)ss_twr_anchor_init_sweep_count);
                printk("Anchor master finite sweep complete: %s\n", done_line);
                (void)anchor_ble_ctrl_publish_result_line(done_line);
                return 0;
            }
        }
        ss_twr_anchor_init_range_delay();
    }
}
