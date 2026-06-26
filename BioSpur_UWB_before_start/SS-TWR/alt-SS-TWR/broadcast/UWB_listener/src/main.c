#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include <deca_device_api.h>
#include <deca_regs.h>

#include "uwb_bringup.h"
#include "uwb_ss_twr_shared.h"

#define LISTENER_RX_BUF_LEN 127U
#define LISTENER_STATUS_PERIOD_MS 5000U
#define LISTENER_UNKNOWN_ID 255U
#define LISTENER_ACC_DATA_LEN ACC_MEM_LEN

#ifndef APP_LISTENER_ID
#define APP_LISTENER_ID LISTENER_UNKNOWN_ID
#endif

#ifndef APP_LISTENER_NEAR_ANCHOR_ID
#define APP_LISTENER_NEAR_ANCHOR_ID LISTENER_UNKNOWN_ID
#endif

#ifndef APP_LISTENER_POLL_DIAG_ENABLE
#define APP_LISTENER_POLL_DIAG_ENABLE 1U
#endif

#ifndef APP_LISTENER_CIR_CAPTURE_ENABLE
#define APP_LISTENER_CIR_CAPTURE_ENABLE 0U
#endif

#ifndef APP_LISTENER_CIR_SAMPLE_PERIOD
#define APP_LISTENER_CIR_SAMPLE_PERIOD 10U
#endif

#ifndef APP_LISTENER_CIR_CHUNK_BYTES
#define APP_LISTENER_CIR_CHUNK_BYTES 48U
#endif

#ifndef APP_LISTENER_POST_CIR_IDLE_MS
#define APP_LISTENER_POST_CIR_IDLE_MS 12U
#endif

#ifndef APP_LISTENER_STATUS_PRINT_ENABLE
#define APP_LISTENER_STATUS_PRINT_ENABLE 1U
#endif

struct listener_counters {
    uint32_t good_frames;
    uint32_t accepted_polls;
    uint32_t ignored_nonpoll;
    uint32_t ignored_poll_mask;
    uint32_t bad_header;
    uint32_t too_long;
    uint32_t rx_errors;
    uint32_t rx_enable_failures;
    uint32_t full_cir_captures;
    uint32_t last_rx_enable_error;
    uint32_t last_status;
    uint16_t last_src;
    uint16_t last_dst;
    uint16_t last_pan;
    uint8_t last_code;
    uint8_t last_len;
};

static uint8_t rx_buffer[LISTENER_RX_BUF_LEN];
static struct listener_counters counters;
static uint32_t last_status_print_ms;

static dwt_config_t listener_config = {
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

static uint16_t read_le16_if_present(const uint8_t *frame, uint32_t len,
                                     uint8_t offset)
{
    if (frame == NULL || len <= (uint32_t)offset + 1U) {
        return 0U;
    }

    return (uint16_t)frame[offset] | ((uint16_t)frame[offset + 1U] << 8);
}

static void listener_radio_configure(void)
{
    dwt_configure(&listener_config);
    dwt_setrxantennadelay(16436U);
    dwt_settxantennadelay(16436U);
    dwt_setpanid(APP_UWB_PAN_ID);
    dwt_setaddress16(0xB1FEU);
    dwt_enableframefilter(DWT_FF_NOTYPE_EN);
    dwt_setrxtimeout(0U);
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_TO |
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_GOOD);
}

static void listener_restart_rx(void)
{
    int ret;

    dwt_forcetrxoff();
    dwt_rxreset();
    ret = dwt_rxenable(DWT_START_RX_IMMEDIATE);
    if (ret != DWT_SUCCESS) {
        counters.rx_enable_failures++;
        counters.last_rx_enable_error = (uint32_t)ret;
    }
}

static bool frame_has_biospur_header(const uint8_t *frame, uint32_t len)
{
    uint16_t pan;

    if (frame == NULL || len <= UWB_MSG_CODE_IDX) {
        return false;
    }
    if (frame[0] != UWB_FRAME_CTRL_LOW || frame[1] != UWB_FRAME_CTRL_HIGH) {
        return false;
    }

    pan = read_le16_if_present(frame, len, UWB_MSG_PAN_IDX);
    return pan == APP_UWB_PAN_ID;
}

static bool listener_accepts_poll(const uint8_t *frame, uint32_t len)
{
    uint16_t src;
    uint16_t dst;
    uint8_t mask;

    if (!frame_has_biospur_header(frame, len)) {
        counters.bad_header++;
        return false;
    }
    if (len != UWB_MSG_ALT_BCAST_POLL_FRAME_LEN ||
        frame[UWB_MSG_CODE_IDX] != UWB_MSG_POLL_CODE) {
        counters.ignored_nonpoll++;
        return false;
    }

    src = uwb_frame_get_src_addr(frame);
    dst = uwb_frame_get_dst_addr(frame);
    if (!uwb_short_addr_is_tag(src) || dst != UWB_BROADCAST_SHORT_ADDR) {
        counters.ignored_nonpoll++;
        return false;
    }

    mask = uwb_ss_twr_poll_anchor_mask(frame);
    if (mask == 0U) {
        counters.ignored_poll_mask++;
        return false;
    }

#if APP_LISTENER_NEAR_ANCHOR_ID < UWB_MAX_ANCHORS
    if ((mask & (uint8_t)(1U << APP_LISTENER_NEAR_ANCHOR_ID)) == 0U) {
        counters.ignored_poll_mask++;
        return false;
    }
#endif

    return true;
}

static bool listener_cir_capture_due(void)
{
#if APP_LISTENER_CIR_CAPTURE_ENABLE == 0U
    return false;
#elif APP_LISTENER_CIR_SAMPLE_PERIOD > 1U
    return (counters.accepted_polls % APP_LISTENER_CIR_SAMPLE_PERIOD) == 0U;
#else
    return true;
#endif
}

static void print_lpd(uint32_t now_ms, uint32_t frame_len,
                      uint32_t resp_rx_ts, int32_t carrier_integrator,
                      const dwt_rxdiag_t *diag)
{
#if APP_LISTENER_POLL_DIAG_ENABLE != 0U
    uint16_t src = counters.last_src;
    uint16_t dst = counters.last_dst;
    uint8_t seq = rx_buffer[UWB_MSG_SN_IDX];
    uint8_t tag_id = uwb_ss_twr_poll_tag_id(rx_buffer);
    uint8_t mask = uwb_ss_twr_poll_anchor_mask(rx_buffer);

    printk("LPD;1;%u;%u;%lu;%lu;%u;%u;0x%04x;0x%04x;%lu;%ld;%u;%u;%u;%u;%u;%u;%u;%lu;0x%02x\n",
           (unsigned int)APP_LISTENER_ID,
           (unsigned int)APP_LISTENER_NEAR_ANCHOR_ID,
           (unsigned long)now_ms,
           (unsigned long)counters.accepted_polls,
           (unsigned int)seq,
           (unsigned int)tag_id,
           (unsigned int)src,
           (unsigned int)dst,
           (unsigned long)resp_rx_ts,
           (long)carrier_integrator,
           (unsigned int)diag->firstPath,
           (unsigned int)diag->firstPathAmp1,
           (unsigned int)diag->firstPathAmp2,
           (unsigned int)diag->firstPathAmp3,
           (unsigned int)diag->maxGrowthCIR,
           (unsigned int)diag->rxPreamCount,
           (unsigned int)diag->stdNoise,
           (unsigned long)frame_len,
           (unsigned int)mask);
#else
    ARG_UNUSED(now_ms);
    ARG_UNUSED(frame_len);
    ARG_UNUSED(resp_rx_ts);
    ARG_UNUSED(carrier_integrator);
    ARG_UNUSED(diag);
#endif
}

static void print_full_cir(uint32_t resp_rx_ts, int32_t carrier_integrator,
                           const dwt_rxdiag_t *diag)
{
#if APP_LISTENER_CIR_CAPTURE_ENABLE != 0U
    static const char hex[] = "0123456789ABCDEF";
    uint8_t chunk[49];
    uint16_t offset = 0U;
    uint16_t chunk_bytes = APP_LISTENER_CIR_CHUNK_BYTES;
    uint8_t seq = rx_buffer[UWB_MSG_SN_IDX];
    uint8_t tag_id = uwb_ss_twr_poll_tag_id(rx_buffer);
    uint8_t mask = uwb_ss_twr_poll_anchor_mask(rx_buffer);

    if (!listener_cir_capture_due()) {
        return;
    }
    if (chunk_bytes == 0U || chunk_bytes > 48U) {
        chunk_bytes = 48U;
    }

    counters.full_cir_captures++;
    printk("LCIRM;1;%u;%u;%lu;%u;%u;0x%02x;%lu;%ld;%u;%u;%u;%u;%u;%u;%u\n",
           (unsigned int)APP_LISTENER_ID,
           (unsigned int)APP_LISTENER_NEAR_ANCHOR_ID,
           (unsigned long)counters.accepted_polls,
           (unsigned int)seq,
           (unsigned int)tag_id,
           (unsigned int)mask,
           (unsigned long)resp_rx_ts,
           (long)carrier_integrator,
           (unsigned int)diag->firstPath,
           (unsigned int)diag->firstPathAmp1,
           (unsigned int)diag->firstPathAmp2,
           (unsigned int)diag->firstPathAmp3,
           (unsigned int)diag->maxGrowthCIR,
           (unsigned int)diag->rxPreamCount,
           (unsigned int)LISTENER_ACC_DATA_LEN);

    while (offset < LISTENER_ACC_DATA_LEN) {
        uint16_t len = MIN(chunk_bytes,
                           (uint16_t)(LISTENER_ACC_DATA_LEN - offset));
        char line[144];
        size_t pos = 0U;

        memset(chunk, 0, (size_t)len + 1U);
        dwt_readaccdata(chunk, (uint16)(len + 1U), offset);
        pos += (size_t)snprintk(line + pos, sizeof(line) - pos,
                                "LCIRD;1;%lu;%u;%u;",
                                (unsigned long)counters.accepted_polls,
                                (unsigned int)offset,
                                (unsigned int)len);
        for (uint16_t i = 0U; i < len && pos + 2U < sizeof(line); ++i) {
            uint8_t b = chunk[1U + i];
            line[pos++] = hex[(b >> 4) & 0x0fU];
            line[pos++] = hex[b & 0x0fU];
        }
        line[pos < sizeof(line) ? pos : sizeof(line) - 1U] = '\0';
        printk("%s\n", line);
        offset = (uint16_t)(offset + len);
    }

    printk("LCIRE;1;%lu;%u\n",
           (unsigned long)counters.accepted_polls,
           (unsigned int)LISTENER_ACC_DATA_LEN);
#else
    ARG_UNUSED(resp_rx_ts);
    ARG_UNUSED(carrier_integrator);
    ARG_UNUSED(diag);
#endif
}

static void handle_good_frame(uint32_t now_ms)
{
    uint32_t frame_len;
    uint32_t resp_rx_ts;
    int32_t carrier_integrator;
    dwt_rxdiag_t diag;
    bool accepted_poll;
    bool capture_full;

    memset(&diag, 0, sizeof(diag));
    dwt_readdiagnostics(&diag);
    frame_len = dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFL_MASK_1023;
    counters.last_len = (uint8_t)MIN(frame_len, 255U);
    counters.good_frames++;

    if (frame_len > sizeof(rx_buffer)) {
        counters.too_long++;
        dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);
        listener_restart_rx();
        return;
    }

    memset(rx_buffer, 0, sizeof(rx_buffer));
    dwt_readrxdata(rx_buffer, (uint16)frame_len, 0U);
    resp_rx_ts = dwt_readrxtimestamplo32();
    carrier_integrator = dwt_readcarrierintegrator();

    counters.last_pan = read_le16_if_present(rx_buffer, frame_len,
                                             UWB_MSG_PAN_IDX);
    counters.last_dst = read_le16_if_present(rx_buffer, frame_len,
                                             UWB_MSG_DST_IDX);
    counters.last_src = read_le16_if_present(rx_buffer, frame_len,
                                             UWB_MSG_SRC_IDX);
    counters.last_code = frame_len > UWB_MSG_CODE_IDX ?
                         rx_buffer[UWB_MSG_CODE_IDX] : 0U;

    accepted_poll = listener_accepts_poll(rx_buffer, frame_len);
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);
    if (!accepted_poll) {
        listener_restart_rx();
        return;
    }

    counters.accepted_polls++;
    capture_full = listener_cir_capture_due();
    if (capture_full) {
        dwt_forcetrxoff();
    }
    print_lpd(now_ms, frame_len, resp_rx_ts, carrier_integrator, &diag);
    if (capture_full) {
        print_full_cir(resp_rx_ts, carrier_integrator, &diag);
#if APP_LISTENER_POST_CIR_IDLE_MS > 0U
        k_msleep(APP_LISTENER_POST_CIR_IDLE_MS);
#endif
        listener_restart_rx();
        return;
    }

    listener_restart_rx();
}

static void print_status(uint32_t now_ms)
{
#if APP_LISTENER_STATUS_PRINT_ENABLE != 0U
    if ((now_ms - last_status_print_ms) < LISTENER_STATUS_PERIOD_MS) {
        return;
    }
    last_status_print_ms = now_ms;
    printk("LSTAT;1;%u;%u;%lu;%lu;%lu;%lu;%lu;%lu;%lu;%lu;0x%08lx;0x%04x;0x%04x;0x%02x\n",
           (unsigned int)APP_LISTENER_ID,
           (unsigned int)APP_LISTENER_NEAR_ANCHOR_ID,
           (unsigned long)counters.good_frames,
           (unsigned long)counters.accepted_polls,
           (unsigned long)counters.ignored_nonpoll,
           (unsigned long)counters.ignored_poll_mask,
           (unsigned long)counters.bad_header,
           (unsigned long)counters.too_long,
           (unsigned long)counters.rx_errors,
           (unsigned long)counters.full_cir_captures,
           (unsigned long)counters.last_status,
           (unsigned int)counters.last_src,
           (unsigned int)counters.last_dst,
           (unsigned int)counters.last_code);
#else
    ARG_UNUSED(now_ms);
#endif
}

int main(void)
{
    int ret;

    printk("BioSpur co-located UWB listener start id=%u near_anchor=%u cir=%u period=%u\n",
           (unsigned int)APP_LISTENER_ID,
           (unsigned int)APP_LISTENER_NEAR_ANCHOR_ID,
           (unsigned int)APP_LISTENER_CIR_CAPTURE_ENABLE,
           (unsigned int)APP_LISTENER_CIR_SAMPLE_PERIOD);

    ret = uwb_hw_bringup_and_init();
    if (ret != 0) {
        printk("listener UWB bringup failed: %d\n", ret);
        while (true) {
            k_msleep(1000);
        }
    }

    listener_radio_configure();
    listener_restart_rx();
    printk("listener RX-only poll diagnostics ready\n");

    while (true) {
        uint32_t now_ms = k_uptime_get_32();
        uint32_t status_reg = dwt_read32bitreg(SYS_STATUS_ID);
        counters.last_status = status_reg;

        if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
            handle_good_frame(now_ms);
        } else if ((status_reg & (SYS_STATUS_ALL_RX_ERR |
                                  SYS_STATUS_ALL_RX_TO)) != 0U) {
            counters.rx_errors++;
            dwt_write32bitreg(SYS_STATUS_ID,
                              SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
            dwt_rxreset();
            listener_restart_rx();
        }

        print_status(now_ms);
        k_yield();
    }
}
