#include "ss_twr_init.h"
#include "uwb_imu.h"
#include "uwb_anchor_layout.h"
#include "uwb_motion.h"
#include "uwb_range_tracker.h"
#include "uwb_ss_twr_shared.h"
#include "uwb_tag_loc.h"

#include <string.h>

#include <deca_device_api.h>
#include <deca_regs.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#define SS_TWR_INIT_TX_ANT_DLY 16436U
#define SS_TWR_INIT_RX_ANT_DLY 16436U

#ifndef APP_TAG_RNG_DELAY_MS
#define APP_TAG_RNG_DELAY_MS 1000U
#endif

#ifndef APP_TAG_VERBOSE_RANGING
#define APP_TAG_VERBOSE_RANGING 1U
#endif

#ifndef APP_TAG_VERBOSE_MEASUREMENTS
#define APP_TAG_VERBOSE_MEASUREMENTS 1U
#endif

#define SS_TWR_INIT_RNG_DELAY_MS APP_TAG_RNG_DELAY_MS
#define SS_TWR_INIT_TX_TO_RX_DLY_UUS 140U
#define SS_TWR_INIT_RESP_RX_TIMEOUT_UUS 1500U

#define SS_TWR_INIT_RX_BUF_LEN 20U
#define SS_TWR_INIT_ALL_MSG_COMMON_LEN 10U
#define SS_TWR_INIT_MSG_SN_IDX 2U
#define SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX 10U
#define SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX 14U
#define SS_TWR_INIT_RESP_MSG_TS_LEN 4U

#define SS_TWR_INIT_SPEED_OF_LIGHT 299702547.0

static dwt_config_t ss_twr_init_config = {
    5,
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

static uint8_t ss_twr_init_frame_seq_nb;
static uint8_t ss_twr_init_rx_buffer[SS_TWR_INIT_RX_BUF_LEN];
static uint8_t ss_twr_init_tx_poll_msg[12];
static uint16_t ss_twr_init_local_addr;
static uint8_t ss_twr_init_local_tag_id;
static struct uwb_range_tracker ss_twr_init_trackers[UWB_MAX_ANCHORS];
static uint8_t ss_twr_init_anchor_ids[UWB_MAX_ANCHORS];
static size_t ss_twr_init_anchor_count;
static size_t ss_twr_init_anchor_index;
static uint32_t ss_twr_init_sweep_count;
static bool ss_twr_init_imu_ready;

static void ss_twr_init_read_ts(const uint8_t *ts_field, uint32 *ts)
{
    *ts = 0;

    for (int i = 0; i < SS_TWR_INIT_RESP_MSG_TS_LEN; ++i) {
        *ts |= ((uint32)ts_field[i]) << (i * 8);
    }
}

static void ss_twr_init_configure_radio(void)
{
    dwt_configure(&ss_twr_init_config);
    dwt_setrxantennadelay(SS_TWR_INIT_RX_ANT_DLY);
    dwt_settxantennadelay(SS_TWR_INIT_TX_ANT_DLY);
    dwt_setleds(DWT_LEDS_ENABLE);
    dwt_setrxaftertxdelay(SS_TWR_INIT_TX_TO_RX_DLY_UUS);
    dwt_setrxtimeout(SS_TWR_INIT_RESP_RX_TIMEOUT_UUS);
    dwt_setpreambledetecttimeout(0);
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_GOOD |
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
}

static void ss_twr_init_print_location_if_ready(void)
{
    struct uwb_tag_measurement measurements[UWB_MAX_ANCHORS];
    struct uwb_tag_location_result location;
    struct uwb_motion_sample motion;
    struct uwb_imu_sample imu;

    memset(measurements, 0, sizeof(measurements));

    for (size_t i = 0; i < ss_twr_init_anchor_count; ++i) {
        const struct uwb_anchor_pose_mm *pose =
            uwb_anchor_layout_get(ss_twr_init_anchor_ids[i]);
        struct uwb_range_tracker *tracker = &ss_twr_init_trackers[i];

        measurements[i].anchor_id = ss_twr_init_anchor_ids[i];
        measurements[i].quality_percent =
            uwb_range_tracker_quality_percent(tracker);
        measurements[i].valid = tracker->filtered_valid;
        measurements[i].range_mm = tracker->filtered_mm;

        if (APP_TAG_VERBOSE_MEASUREMENTS != 0U && pose != NULL &&
            tracker->filtered_valid) {
            printk("Tag meas anchor=%c(%u) range=%lu mm q=%u%%\n", pose->label,
                   (unsigned int)measurements[i].anchor_id,
                   (unsigned long)measurements[i].range_mm,
                   (unsigned int)measurements[i].quality_percent);
        }
    }

    if (uwb_tag_loc_solve(measurements, ss_twr_init_anchor_count, &location) !=
        0) {
        printk("Tag solve pending: need >=4 valid anchors across both planes\n");
        return;
    }

    printk("Tag pos sweep=%lu used=%u lower=%u upper=%u xyz=(%ld,%ld,%ld) mm rms=%lu mm max=%lu mm anchors=[",
           (unsigned long)ss_twr_init_sweep_count,
           (unsigned int)location.used_anchor_count,
           (unsigned int)location.lower_anchor_count,
           (unsigned int)location.upper_anchor_count,
           (long)location.x_mm, (long)location.y_mm, (long)location.z_mm,
           (unsigned long)location.residual_rms_mm,
           (unsigned long)location.residual_max_mm);
    for (size_t i = 0; i < location.used_anchor_count; ++i) {
        const struct uwb_anchor_pose_mm *pose =
            uwb_anchor_layout_get(location.anchor_ids[i]);
        if (pose != NULL) {
            printk("%c", pose->label);
        } else {
            printk("%u", (unsigned int)location.anchor_ids[i]);
        }
        if (i + 1U < location.used_anchor_count) {
            printk(",");
        }
    }
    printk("]\n");

    if (uwb_motion_update(location.x_mm, location.y_mm, location.z_mm,
                          k_uptime_get(), &motion)) {
        printk("Tag motion sweep=%lu dt=%lu ms disp=%lu mm vel=(%ld,%ld,%ld) mm/s speed=%lu mm/s\n",
               (unsigned long)ss_twr_init_sweep_count,
               (unsigned long)motion.dt_ms,
               (unsigned long)motion.displacement_mm,
               (long)motion.vx_mm_s,
               (long)motion.vy_mm_s,
               (long)motion.vz_mm_s,
               (unsigned long)motion.speed_mm_s);
    }

    if (ss_twr_init_imu_ready && uwb_imu_read(&imu)) {
        const char *imu_state = "stable";
        uint32_t gravity_error_abs =
            (imu.gravity_error_milli_mps2 < 0) ?
                (uint32_t)(-imu.gravity_error_milli_mps2) :
                (uint32_t)imu.gravity_error_milli_mps2;

        if (imu.delta_magnitude_milli_mps2 > 750U ||
            gravity_error_abs > 400U) {
            imu_state = "moving";
        }

        printk("Tag accel sweep=%lu ts=%lu ms acc=(%ld,%ld,%ld) x1000mps2 norm=%ld err=%ld delta=%lu state=%s\n",
               (unsigned long)ss_twr_init_sweep_count,
               (unsigned long)imu.timestamp_ms,
               (long)imu.ax_milli_mps2, (long)imu.ay_milli_mps2,
               (long)imu.az_milli_mps2, (long)imu.norm_milli_mps2,
               (long)imu.gravity_error_milli_mps2,
               (unsigned long)imu.delta_magnitude_milli_mps2, imu_state);
    }
}

int ss_twr_init_start(unsigned int tag_id, const uint8_t *anchor_ids,
                      size_t anchor_count)
{
    if (tag_id >= UWB_MAX_TAGS || anchor_ids == NULL || anchor_count == 0U ||
        anchor_count > UWB_MAX_ANCHORS) {
        printk("Invalid SS-TWR initiator config tag=%u anchors=%u\n", tag_id,
               (unsigned int)anchor_count);
        return -1;
    }

    ss_twr_init_local_tag_id = (uint8_t)tag_id;
    ss_twr_init_local_addr = uwb_tag_short_addr(ss_twr_init_local_tag_id);
    ss_twr_init_anchor_count = anchor_count;
    ss_twr_init_anchor_index = 0U;
    ss_twr_init_sweep_count = 0U;
    uwb_motion_reset();

    for (size_t i = 0; i < anchor_count; ++i) {
        if (anchor_ids[i] >= UWB_MAX_ANCHORS) {
            printk("Invalid anchor id in table: %u\n",
                   (unsigned int)anchor_ids[i]);
            return -1;
        }

        ss_twr_init_anchor_ids[i] = anchor_ids[i];
        uwb_range_tracker_init(&ss_twr_init_trackers[i],
                               uwb_anchor_short_addr(anchor_ids[i]));
    }

    printk("SS-TWR initiator ready tag=%u addr=0x%04x anchor_count=%u\n",
           (unsigned int)ss_twr_init_local_tag_id,
           (unsigned int)ss_twr_init_local_addr,
           (unsigned int)ss_twr_init_anchor_count);
    printk("Tag motion mode rng_delay_ms=%u\n",
           (unsigned int)SS_TWR_INIT_RNG_DELAY_MS);
    ss_twr_init_imu_ready = (uwb_imu_init() == 0);
    ss_twr_init_configure_radio();

    while (1) {
        uint8_t current_anchor_id =
            ss_twr_init_anchor_ids[ss_twr_init_anchor_index];
        uint16_t current_anchor_addr = uwb_anchor_short_addr(current_anchor_id);
        struct uwb_range_tracker *tracker =
            &ss_twr_init_trackers[ss_twr_init_anchor_index];
        uint32 status_reg;

        uwb_ss_twr_build_poll_frame(ss_twr_init_tx_poll_msg,
                                    ss_twr_init_frame_seq_nb, current_anchor_addr,
                                    ss_twr_init_local_addr);

        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS);

        if (dwt_writetxdata(sizeof(ss_twr_init_tx_poll_msg),
                            ss_twr_init_tx_poll_msg, 0) != DWT_SUCCESS) {
            printk("Initiator TX buffer write failed\n");
            k_msleep(SS_TWR_INIT_RNG_DELAY_MS);
            continue;
        }

        dwt_writetxfctrl(sizeof(ss_twr_init_tx_poll_msg), 0, 1);

        if (dwt_starttx(DWT_START_TX_IMMEDIATE | DWT_RESPONSE_EXPECTED) !=
            DWT_SUCCESS) {
            printk("Initiator TX start failed\n");
            dwt_forcetrxoff();
            k_msleep(SS_TWR_INIT_RNG_DELAY_MS);
            continue;
        }

        do {
            status_reg = dwt_read32bitreg(SYS_STATUS_ID);
        } while ((status_reg & (SYS_STATUS_RXFCG | SYS_STATUS_ALL_RX_TO |
                                SYS_STATUS_ALL_RX_ERR)) == 0U);

        ss_twr_init_frame_seq_nb++;

        if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
            uint32 frame_len;
            uint16_t resp_src_addr;
            uint32 poll_tx_ts;
            uint32 resp_rx_ts;
            uint32 poll_rx_ts;
            uint32 resp_tx_ts;
            int32 rtd_init;
            int32 rtd_resp;
            double tof;
            double distance_m;
            double clock_offset_ratio;
            long raw_distance_mm;
            uint32 filtered_mm;

            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);

            frame_len = dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFLEN_MASK;
            if (frame_len > sizeof(ss_twr_init_rx_buffer)) {
                printk("Initiator RX frame too long: %lu status=0x%08lx\n",
                       (unsigned long)frame_len, (unsigned long)status_reg);
                printk("RX_FINFO raw=0x%08lx\n",
                       (unsigned long)dwt_read32bitreg(RX_FINFO_ID));
                dwt_forcetrxoff();
                dwt_rxreset();
                k_msleep(SS_TWR_INIT_RNG_DELAY_MS);
                continue;
            }

            memset(ss_twr_init_rx_buffer, 0, sizeof(ss_twr_init_rx_buffer));
            dwt_readrxdata(ss_twr_init_rx_buffer, (uint16)frame_len, 0);
            ss_twr_init_rx_buffer[SS_TWR_INIT_MSG_SN_IDX] = 0;

            if (!uwb_ss_twr_resp_matches(ss_twr_init_rx_buffer,
                                         ss_twr_init_local_addr, current_anchor_addr)) {
                printk("Initiator got unexpected frame src=0x%04x dst=0x%04x code=0x%02x\n",
                       (unsigned int)uwb_frame_get_src_addr(ss_twr_init_rx_buffer),
                       (unsigned int)uwb_frame_get_dst_addr(ss_twr_init_rx_buffer),
                       (unsigned int)ss_twr_init_rx_buffer[UWB_MSG_CODE_IDX]);
                k_msleep(SS_TWR_INIT_RNG_DELAY_MS);
                continue;
            }

            poll_tx_ts = dwt_readtxtimestamplo32();
            resp_rx_ts = dwt_readrxtimestamplo32();
            clock_offset_ratio =
                (double)dwt_readcarrierintegrator() *
                (FREQ_OFFSET_MULTIPLIER * HERTZ_TO_PPM_MULTIPLIER_CHAN_5 /
                 1.0e6);

            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_POLL_RX_TS_IDX],
                &poll_rx_ts);
            ss_twr_init_read_ts(
                &ss_twr_init_rx_buffer[SS_TWR_INIT_RESP_MSG_RESP_TX_TS_IDX],
                &resp_tx_ts);

            rtd_init = (int32)(resp_rx_ts - poll_tx_ts);
            rtd_resp = (int32)(resp_tx_ts - poll_rx_ts);
            tof = ((rtd_init - rtd_resp * (1.0 - clock_offset_ratio)) / 2.0) *
                  DWT_TIME_UNITS;
            distance_m = tof * SS_TWR_INIT_SPEED_OF_LIGHT;
            raw_distance_mm = (long)(distance_m * 1000.0);
            if (raw_distance_mm < 0L) {
                raw_distance_mm = 0L;
            }

            if (tracker == NULL) {
                k_msleep(SS_TWR_INIT_RNG_DELAY_MS);
                continue;
            }

            filtered_mm = uwb_range_tracker_record_success(
                tracker, (uint32_t)raw_distance_mm);
            resp_src_addr = uwb_frame_get_src_addr(ss_twr_init_rx_buffer);

            if (APP_TAG_VERBOSE_RANGING != 0U) {
                printk("Range anchor=%u addr=0x%04x raw=%ld mm filt=%lu mm ok=%lu fail=%lu q=%u%%\n",
                       (unsigned int)uwb_anchor_id_from_addr(resp_src_addr),
                       (unsigned int)resp_src_addr, raw_distance_mm,
                       (unsigned long)filtered_mm,
                       (unsigned long)tracker->success_count,
                       (unsigned long)tracker->failure_count,
                       (unsigned int)uwb_range_tracker_quality_percent(
                           tracker));
            }
        } else {
            if (tracker != NULL) {
                uwb_range_tracker_record_failure(tracker);
                if (APP_TAG_VERBOSE_RANGING != 0U) {
                    printk("Initiator RX timeout/error anchor=%u addr=0x%04x status=0x%08lx ok=%lu fail=%lu q=%u%%\n",
                           (unsigned int)current_anchor_id,
                           (unsigned int)current_anchor_addr,
                           (unsigned long)status_reg,
                           (unsigned long)tracker->success_count,
                           (unsigned long)tracker->failure_count,
                           (unsigned int)uwb_range_tracker_quality_percent(
                               tracker));
                }
            }
            dwt_write32bitreg(SYS_STATUS_ID,
                              SYS_STATUS_ALL_RX_TO | SYS_STATUS_ALL_RX_ERR);
            dwt_rxreset();
        }

        ss_twr_init_anchor_index =
            (ss_twr_init_anchor_index + 1U) % ss_twr_init_anchor_count;
        if (ss_twr_init_anchor_index == 0U) {
            ss_twr_init_sweep_count++;
            ss_twr_init_print_location_if_ready();
        }
        k_msleep(SS_TWR_INIT_RNG_DELAY_MS);
    }
}
