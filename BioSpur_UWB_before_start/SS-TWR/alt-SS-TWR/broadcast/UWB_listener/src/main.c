#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/device.h>

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

/* Also emit per-anchor-response diagnostics (LRD) so the listener is a full
 * passive TWR observer: poll-path (LPD) + response coverage/timing (LRD),
 * both RX-timestamped. RX-only; never transmits. */
#ifndef APP_LISTENER_RESP_DIAG_ENABLE
#define APP_LISTENER_RESP_DIAG_ENABLE 1U
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

/* Event-driven blue LED (board alias led3 = P0.31, active-low): a bare "am I
 * hearing polls" indicator for the operator. Toggled once per LED_ACTIVITY_DIV
 * accepted (near-anchor) polls -> a ~sweep-rate blink while frames arrive, and
 * forced OFF by the main loop after LED_ACTIVITY_TIMEOUT_MS of poll silence, so a
 * DARK LED unambiguously means "powered but not receiving" (a plain toggle would
 * freeze half-on when idle). RX-path cost = one GPIO toggle (~us); this path is
 * the polled main loop, not an ISR, and the listener has no TX deadline. */
#ifndef APP_LISTENER_LED_ENABLE
#define APP_LISTENER_LED_ENABLE 1U
#endif
#define LISTENER_LED_ACTIVITY_DIV 6U
#define LISTENER_LED_ACTIVITY_TIMEOUT_MS 300U

/* Fix 1 (idle-wedge): if no good frame arrives for this long, force a full radio
 * recover (HW reset + re-init) so a silent RX stall self-heals without a reboot. */
#define LISTENER_RX_STALL_WATCHDOG_MS 15000U
/* Fix 2 (flood): flush at most this many buffered records to UART per main-loop
 * pass, regardless of RX busy/idle, so LPD/LRD keep streaming under heavy traffic. */
#define LISTENER_DRAIN_BUDGET 4U

/* ---- USB-CDC command interface + tag-capture (position calibration) mode ----
 *
 * The listener can be temporarily switched (over the existing UART/USB CDC, at
 * 460800 baud -- never over BLE) into a minimal broadcast Alt-SS-TWR *tag* so the
 * host can multilaterate its 3D position from per-anchor ranges, then switched
 * back to passive CIR listening. See README / calibrate_listener_positions.py.
 *
 * CRITICAL (anchor firmware is off-limits): the anchor responder only replies to
 * polls whose 16-bit source address is a tag (0xB100..0xB1FF) -- see ss_twr_resp.c
 * uwb_short_addr_is_ranging_initiator() + poll_src_is_tag gating. So the ON-AIR
 * source address MUST live in the tag range. APP_LISTENER_TAG_ADDR is that on-air
 * address; APP_LISTENER_TAG_LABEL (0xc0xx) is only a host-facing logical id echoed
 * in "MODE=TAG src=" and "LTAG;src=" so the operator/host can key results by
 * listener without colliding with the wand tags' on-air ids. */
#ifndef APP_LISTENER_TAG_ADDR
#define APP_LISTENER_TAG_ADDR 0xB1C0U   /* on-air src; MUST be in 0xB100..0xB1FF */
#endif
#ifndef APP_LISTENER_TAG_LABEL
#define APP_LISTENER_TAG_LABEL 0xC000U  /* host-facing logical id (0xc0xx) */
#endif

/* Tag-capture poll cadence + response collection window. 100 ms matches the wand
 * tags' 10 Hz. The window must cover the last responder: anchor reply delay is
 * GUARD(500us) + rank*SPACING(800us); rank-7 completes < ~7 ms after poll TX. */
#define LISTENER_TAG_POLL_INTERVAL_MS 100U
#define LISTENER_TAG_RESP_WINDOW_MS 12U
#define LISTENER_TAG_ANCHOR_MASK 0xFFU   /* poll all 8 anchors every cycle */
#define LISTENER_TAG_TXDONE_TIMEOUT_MS 5U
#define LISTENER_CMD_BUF_MAX 32U         /* line buffer; flush (drop) on overflow */
#define LISTENER_SPEED_OF_LIGHT 299702547.0  /* m/s, matches ss_twr_init.c */

enum listener_mode {
    LISTENER_MODE_LISTEN = 0,  /* boot default: passive RX-only CIR capture */
    LISTENER_MODE_IDLE,        /* radio forced off (AutoPos-safe: no RF) */
    LISTENER_MODE_TAG,         /* active broadcast Alt-SS-TWR tag (ranging) */
};

struct listener_counters {
    uint32_t good_frames;
    uint32_t accepted_polls;
    uint32_t accepted_resps;
    uint32_t ignored_nonpoll;
    uint32_t ignored_poll_mask;
    uint32_t bad_header;
    uint32_t too_long;
    uint32_t rx_errors;
    uint32_t rx_enable_failures;
    uint32_t full_cir_captures;
    uint32_t ring_drops;
    uint32_t self_recover;   /* RX-stall watchdog full-recover count (Fix 1) */
    uint32_t tag_polls;      /* polls sent while in MODE_TAG */
    uint32_t tag_tx_errors;  /* poll TX write/start/done failures in MODE_TAG */
    uint32_t tag_resp_total; /* anchor responses collected across all tag polls */
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

/* Command interface + mode state. All control is polled from the console UART
 * (USB CDC / J-Link VCOM) in the main loop -- never an ISR, never BLE. */
static const struct device *const cmd_uart =
    DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
static enum listener_mode listener_mode = LISTENER_MODE_LISTEN;
static char cmd_buf[LISTENER_CMD_BUF_MAX];
static uint8_t cmd_len;
static bool cmd_overflow;

/* RX byte ring filled by the UART ISR (producer) and drained by the main loop
 * (consumer). Interrupt-driven RX is required: the legacy nRF UART has a 1-byte
 * RX register with no FIFO, so polling it from the main loop drops command bytes
 * whenever the loop is busy in a printk flood (CIR) or asleep (IDLE). SPSC ring:
 * ISR advances head, main loop advances tail; volatile 16-bit indices are atomic
 * on this single core, so no lock is needed. */
#define LISTENER_CMD_RX_RING 128U
static uint8_t cmd_rx_ring[LISTENER_CMD_RX_RING];
static volatile uint16_t cmd_rx_head;
static volatile uint16_t cmd_rx_tail;

/* Tag-capture (MODE_TAG) working state. */
static uint8_t tag_poll_msg[UWB_MSG_ALT_BCAST_POLL_FRAME_LEN];
static uint8_t tag_frame_seq_nb;
static uint32_t tag_last_poll_ms;

/* RX-stall watchdog + status timers (file scope so a mode switch back to LISTEN
 * can reset them and not trip an immediate false self-recover). */
static uint32_t wd_last_good;
static uint32_t wd_last_activity_ms;

#if APP_LISTENER_LED_ENABLE != 0U
static const struct gpio_dt_spec listener_led =
    GPIO_DT_SPEC_GET(DT_ALIAS(led3), gpios);
static uint32_t led_last_poll_ms;
static uint8_t led_poll_div;
static bool led_ready;
#endif

/* Compact per-frame record. The RX path (capture_to_ring) stores one of these and
 * re-arms RX immediately (no UART), so the listener never goes deaf during the
 * 1-poll + N-response burst. The main loop drains records to UART when idle. */
struct lrec {
    uint32_t now_ms;
    uint32_t seq_count;   /* accepted_polls or accepted_resps at capture time */
    uint32_t rx_ts;       /* DW1000 RX timestamp (lo32) */
    int32_t carrier;      /* carrier integrator (CFO) */
    uint16_t src;
    uint16_t dst;
    uint16_t fp_index;
    uint16_t fp1;
    uint16_t fp2;
    uint16_t fp3;
    uint16_t cir;
    uint16_t rxpacc;
    uint16_t stdnoise;
    uint8_t frame_len;
    uint8_t code;         /* UWB_MSG_POLL_CODE (0xe0) or UWB_MSG_RESP_CODE (0xe1) */
    uint8_t peer_id;      /* tag_id (poll) or anchor_id (response) */
    uint8_t seq;          /* frame sequence number */
    uint8_t mask;         /* poll anchor mask (poll only) */
    uint8_t rcph;         /* RX_TTCKO RCPHASE: receive carrier phase adj, 7-bit */
    int32_t rxtofs;       /* RX_TTCKO RXTOFS: RX time tracking offset, 19-bit signed */
    uint32_t ttcki;       /* RX_TTCKI: receiver time tracking interval, 32-bit */
    uint16_t agc_edg1;    /* AGC_STAT1 EDG1: input noise power gain, 5-bit */
};

#define LREC_RING 128U
static struct lrec lrec_ring[LREC_RING];
static uint32_t lrec_head;
static uint32_t lrec_tail;

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
    /* Clears+enables the DW1000's 12-bit HW diagnostic counters (EVC_*) for the
     * whole session; read back in print_status(). Counters wrap at 4095, so
     * consumers must diff successive 5s LSTAT snapshots for an overnight run. */
    dwt_configeventcounters(1);
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

/* Fix 1: strongest in-firmware recovery for a silent RX stall. Replicates the boot
 * bringup (uwb_port_hw_reset = DW1000 RSTn + dwt_initialise), which the anchor app
 * already re-runs safely, then reconfigures and re-arms RX. No MCU reboot needed. */
static void listener_radio_full_recover(void)
{
    dwt_forcetrxoff();
    (void)uwb_hw_bringup_and_init();
    listener_radio_configure();
    listener_restart_rx();
    counters.self_recover++;
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

static bool listener_is_anchor_response(const uint8_t *frame, uint32_t len,
                                        uint8_t *anchor_id_out)
{
    uint16_t src;

    if (!frame_has_biospur_header(frame, len)) {
        return false;
    }
    if (len <= UWB_MSG_CODE_IDX ||
        frame[UWB_MSG_CODE_IDX] != UWB_MSG_RESP_CODE) {
        return false;
    }
    src = uwb_frame_get_src_addr(frame);
    if (!uwb_short_addr_is_anchor(src)) {
        return false;
    }
    if (anchor_id_out != NULL) {
        *anchor_id_out = uwb_anchor_id_from_addr(src);
    }
    return true;
}

static __maybe_unused bool listener_cir_capture_due(void)
{
#if APP_LISTENER_CIR_CAPTURE_ENABLE == 0U
    return false;
#elif APP_LISTENER_CIR_SAMPLE_PERIOD > 1U
    return (counters.accepted_polls % APP_LISTENER_CIR_SAMPLE_PERIOD) == 0U;
#else
    return true;
#endif
}

static void print_lpd(const struct lrec *r)
{
#if APP_LISTENER_POLL_DIAG_ENABLE != 0U
    printk("LPD;1;%u;%u;%lu;%lu;%u;%u;0x%04x;0x%04x;%lu;%ld;%u;%u;%u;%u;%u;%u;%u;%u;0x%02x"
           ";rcph=%u;rxtofs=%d;ttcki=%lu;agc=%u\n",
           (unsigned int)APP_LISTENER_ID,
           (unsigned int)APP_LISTENER_NEAR_ANCHOR_ID,
           (unsigned long)r->now_ms,
           (unsigned long)r->seq_count,
           (unsigned int)r->seq,
           (unsigned int)r->peer_id,
           (unsigned int)r->src,
           (unsigned int)r->dst,
           (unsigned long)r->rx_ts,
           (long)r->carrier,
           (unsigned int)r->fp_index,
           (unsigned int)r->fp1,
           (unsigned int)r->fp2,
           (unsigned int)r->fp3,
           (unsigned int)r->cir,
           (unsigned int)r->rxpacc,
           (unsigned int)r->stdnoise,
           (unsigned int)r->frame_len,
           (unsigned int)r->mask,
           (unsigned int)r->rcph,
           (int)r->rxtofs,
           (unsigned long)r->ttcki,
           (unsigned int)r->agc_edg1);
#else
    ARG_UNUSED(r);
#endif
}

static void print_lrd(const struct lrec *r)
{
#if APP_LISTENER_RESP_DIAG_ENABLE != 0U
    /* fields mirror LPD (minus poll_mask): anchor response heard at the listener.
     * dst = the tag this response is addressed to (the sweep it belongs to). */
    printk("LRD;1;%u;%u;%lu;%lu;%u;%u;0x%04x;0x%04x;%lu;%ld;%u;%u;%u;%u;%u;%u;%u;%u"
           ";rcph=%u;rxtofs=%d;ttcki=%lu;agc=%u\n",
           (unsigned int)APP_LISTENER_ID,
           (unsigned int)APP_LISTENER_NEAR_ANCHOR_ID,
           (unsigned long)r->now_ms,
           (unsigned long)r->seq_count,
           (unsigned int)r->seq,
           (unsigned int)r->peer_id,
           (unsigned int)r->src,
           (unsigned int)r->dst,
           (unsigned long)r->rx_ts,
           (long)r->carrier,
           (unsigned int)r->fp_index,
           (unsigned int)r->fp1,
           (unsigned int)r->fp2,
           (unsigned int)r->fp3,
           (unsigned int)r->cir,
           (unsigned int)r->rxpacc,
           (unsigned int)r->stdnoise,
           (unsigned int)r->frame_len,
           (unsigned int)r->rcph,
           (int)r->rxtofs,
           (unsigned long)r->ttcki,
           (unsigned int)r->agc_edg1);
#else
    ARG_UNUSED(r);
#endif
}

static __maybe_unused void print_full_cir(uint32_t resp_rx_ts,
                                          int32_t carrier_integrator,
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

#if APP_LISTENER_LED_ENABLE != 0U
static void listener_led_init(void)
{
    if (!gpio_is_ready_dt(&listener_led)) {
        led_ready = false;
        return;
    }
    (void)gpio_pin_configure_dt(&listener_led, GPIO_OUTPUT_INACTIVE);
    led_ready = true;
}

/* Called on every accepted (near-anchor) poll: mark activity + blink. */
static void listener_led_on_poll(uint32_t now_ms)
{
    led_last_poll_ms = now_ms;
    if (led_ready && ++led_poll_div >= LISTENER_LED_ACTIVITY_DIV) {
        led_poll_div = 0U;
        (void)gpio_pin_toggle_dt(&listener_led);
    }
}

/* Called every main-loop pass: force the LED dark once polls stop arriving. */
static void listener_led_idle(uint32_t now_ms)
{
    if (led_ready &&
        (now_ms - led_last_poll_ms) > LISTENER_LED_ACTIVITY_TIMEOUT_MS) {
        (void)gpio_pin_set_dt(&listener_led, 0);   /* logical 0 => LED off */
    }
}
#else
static inline void listener_led_init(void) { }
static inline void listener_led_on_poll(uint32_t now_ms) { ARG_UNUSED(now_ms); }
static inline void listener_led_idle(uint32_t now_ms) { ARG_UNUSED(now_ms); }
#endif

/* RX fast path: read frame + diagnostics + timestamps, classify poll/response,
 * push a compact record onto the ring, and RE-ARM RX immediately. No UART here,
 * so the receiver stays live through the whole 1-poll + N-response burst. The
 * main loop drains the ring to UART during the idle between superframes. */
static void capture_to_ring(uint32_t now_ms)
{
    uint32_t frame_len;
    uint32_t rx_ts;
    int32_t carrier_integrator;
    dwt_rxdiag_t diag;
    bool is_poll;
    bool is_resp = false;
    uint8_t peer_id = LISTENER_UNKNOWN_ID;
    uint8_t mask = 0U;
    struct lrec *r;

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
    rx_ts = dwt_readrxtimestamplo32();
    carrier_integrator = dwt_readcarrierintegrator();

    /* Read while still valid for THIS frame -- re-arming RX below overwrites
     * these clock-tracking/AGC registers, so they must be captured here, not
     * lazily at print time in the main loop. */
    uint8_t ttcko_raw[6] = {0};
    dwt_readfromdevice(RX_TTCKO_ID, 0U, sizeof(ttcko_raw), ttcko_raw);
    uint32_t ttcko_lo = (uint32_t)ttcko_raw[0] | ((uint32_t)ttcko_raw[1] << 8) |
                        ((uint32_t)ttcko_raw[2] << 16) | ((uint32_t)ttcko_raw[3] << 24);
    int32_t rxtofs = (int32_t)(ttcko_lo & RX_TTCKO_RXTOFS_MASK);
    if ((rxtofs & 0x40000) != 0) {
        rxtofs |= (int32_t)0xFFF80000UL;   /* sign-extend 19->32 bits */
    }
    uint8_t rcph = ttcko_raw[5] & 0x7FU;   /* RCPHASE lives in byte 5 (bits 40-46) */
    uint32_t ttcki = dwt_read32bitreg(RX_TTCKI_ID);
    uint32_t agc_stat1 = dwt_read32bitoffsetreg(AGC_CTRL_ID, AGC_STAT1_OFFSET) &
                         AGC_STAT1_MASK;
    uint16_t agc_edg1 = (uint16_t)((agc_stat1 & AGC_STAT1_EDG1_MASK) >> 6);

    counters.last_pan = read_le16_if_present(rx_buffer, frame_len,
                                             UWB_MSG_PAN_IDX);
    counters.last_dst = read_le16_if_present(rx_buffer, frame_len,
                                             UWB_MSG_DST_IDX);
    counters.last_src = read_le16_if_present(rx_buffer, frame_len,
                                             UWB_MSG_SRC_IDX);
    counters.last_code = frame_len > UWB_MSG_CODE_IDX ?
                         rx_buffer[UWB_MSG_CODE_IDX] : 0U;

    is_poll = listener_accepts_poll(rx_buffer, frame_len);
    if (is_poll) {
        peer_id = uwb_ss_twr_poll_tag_id(rx_buffer);
        mask = uwb_ss_twr_poll_anchor_mask(rx_buffer);
        counters.accepted_polls++;
        listener_led_on_poll(now_ms);   /* blink: heard a near-anchor poll */
    } else if (listener_is_anchor_response(rx_buffer, frame_len, &peer_id)) {
        is_resp = true;
        counters.accepted_resps++;
    }

#if APP_LISTENER_CIR_CAPTURE_ENABLE != 0U
    /* CIR probe path (opt-in, e.g. L-B): on a sampled accepted poll, dump the DW1000
     * accumulator NOW, while RX is still stopped after RXFCG, because re-arming
     * overwrites ACC_MEM. This blocks the loop for one dump (~ACC_MEM over UART) and
     * misses frames arriving during it (inherent CIR tradeoff). Fix-compat: good_frames
     * was already incremented above, so the RX-stall watchdog cannot misfire across a
     * dump; and print_full_cir writes direct to printk (not the ring), so the Fix 2
     * drain budget is untouched. print_full_cir self-gates on the sample period. */
    if (is_poll) {
        print_full_cir(rx_ts, carrier_integrator, &diag);
    }
#endif

    /* clear good-frame status and re-arm RX immediately (no UART in this path) */
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);
    listener_restart_rx();

    if (!is_poll && !is_resp) {
        return;
    }

    if ((lrec_head - lrec_tail) >= LREC_RING) {
        lrec_tail++;                 /* ring full: drop oldest */
        counters.ring_drops++;
    }
    r = &lrec_ring[lrec_head % LREC_RING];
    r->now_ms = now_ms;
    r->seq_count = is_poll ? counters.accepted_polls : counters.accepted_resps;
    r->rx_ts = rx_ts;
    r->carrier = carrier_integrator;
    r->src = counters.last_src;
    r->dst = counters.last_dst;
    r->fp_index = diag.firstPath;
    r->fp1 = diag.firstPathAmp1;
    r->fp2 = diag.firstPathAmp2;
    r->fp3 = diag.firstPathAmp3;
    r->cir = diag.maxGrowthCIR;
    r->rxpacc = diag.rxPreamCount;
    r->stdnoise = diag.stdNoise;
    r->frame_len = (uint8_t)MIN(frame_len, 255U);
    r->code = is_poll ? UWB_MSG_POLL_CODE : UWB_MSG_RESP_CODE;
    r->peer_id = peer_id;
    r->seq = rx_buffer[UWB_MSG_SN_IDX];
    r->mask = mask;
    r->rcph = rcph;
    r->rxtofs = rxtofs;
    r->ttcki = ttcki;
    r->agc_edg1 = agc_edg1;
    lrec_head++;
}

/* Drain one buffered record to UART (called from the main loop when RX is idle). */
static void drain_ring_one(void)
{
    const struct lrec *r;

    if (lrec_head == lrec_tail) {
        return;
    }
    r = &lrec_ring[lrec_tail % LREC_RING];
    if (r->code == UWB_MSG_POLL_CODE) {
        print_lpd(r);
    } else {
        print_lrd(r);
    }
    lrec_tail++;
}

static void print_status(uint32_t now_ms)
{
#if APP_LISTENER_STATUS_PRINT_ENABLE != 0U
    static uint32_t last_good_snapshot;
    uint32_t fps;
    dwt_deviceentcnts_t evc;
    if ((now_ms - last_status_print_ms) < LISTENER_STATUS_PERIOD_MS) {
        return;
    }
    /* good frames/sec over the status period (Fix 2: flood/anomaly visibility) */
    fps = (counters.good_frames - last_good_snapshot) * 1000U /
          LISTENER_STATUS_PERIOD_MS;
    last_good_snapshot = counters.good_frames;
    last_status_print_ms = now_ms;
    /* HW event counters (12-bit, free-running since dwt_configeventcounters(1) at
     * init): consumers must diff successive LSTAT snapshots to handle wraparound. */
    dwt_readeventcounters(&evc);
    printk("LSTAT;1;%u;%u;%lu;%lu;%lu;%lu;%lu;%lu;%lu;%lu;0x%08lx;0x%04x;0x%04x;0x%02x;%lu;%lu;%lu;%lu"
           ";evc_fcg=%u;evc_fce=%u;evc_ovr=%u;evc_sto=%u\n",
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
           (unsigned int)counters.last_code,
           (unsigned long)counters.ring_drops,
           (unsigned long)counters.self_recover,
           (unsigned long)counters.rx_enable_failures,
           (unsigned long)fps,
           (unsigned int)evc.CRCG,
           (unsigned int)evc.CRCB,
           (unsigned int)evc.OVER,
           (unsigned int)evc.SFDTO);
#else
    ARG_UNUSED(now_ms);
#endif
}

/* ---------------------------------------------------------------------------
 * Tag-capture mode (MODE_TAG): minimal broadcast Alt-SS-TWR initiator.
 *
 * A stripped copy of the wand-tag exchange in ss_twr_init.c (broadcast path):
 * build a 17B broadcast poll, TX it, then open a single-buffer RX window and
 * collect anchor responses, computing each per-anchor range from the standard
 * SS-TWR timestamp arithmetic. NO anchor layout / solver here -- the host
 * multilaterates from the emitted LTAG ranges. Runs only during position
 * calibration; MODE_LISTEN is byte-for-byte the original passive behaviour.
 * ------------------------------------------------------------------------- */

/* Read a little-endian 4-byte DW1000 timestamp field from a response frame. */
static void listener_read_ts(const uint8_t *field, uint32_t *ts)
{
    *ts = 0U;
    for (uint8_t i = 0U; i < UWB_MSG_RESP_TS_LEN; ++i) {
        *ts |= ((uint32_t)field[i]) << (i * 8U);
    }
}

/* Single-sided TWR range in mm from the four timestamps + carrier integrator.
 * Identical math to ss_twr_init_calc_raw_distance_mm() (channel-5 constants). */
static long listener_tag_calc_range_mm(uint32_t poll_tx_ts, uint32_t resp_rx_ts,
                                       uint32_t poll_rx_ts, uint32_t resp_tx_ts,
                                       int32_t carrier_integrator)
{
    int32_t rtd_init = (int32_t)(resp_rx_ts - poll_tx_ts);
    int32_t rtd_resp = (int32_t)(resp_tx_ts - poll_rx_ts);
    double clock_offset_ratio =
        (double)carrier_integrator *
        (FREQ_OFFSET_MULTIPLIER * HERTZ_TO_PPM_MULTIPLIER_CHAN_5 / 1.0e6);
    double tof =
        ((rtd_init - rtd_resp * (1.0 - clock_offset_ratio)) / 2.0) *
        DWT_TIME_UNITS;
    long raw_mm = (long)(tof * LISTENER_SPEED_OF_LIGHT * 1000.0);

    return raw_mm < 0L ? 0L : raw_mm;
}

/* One LTAG line per poll cycle. Anchors with no response this cycle print -1. */
static void print_ltag(const long *ranges_mm)
{
    char line[160];
    size_t pos = 0U;

    pos += (size_t)snprintk(line + pos, sizeof(line) - pos, "LTAG;src=0x%04x",
                            (unsigned int)APP_LISTENER_TAG_LABEL);
    for (uint8_t a = 0U; a < UWB_MAX_ANCHORS && pos < sizeof(line); ++a) {
        pos += (size_t)snprintk(line + pos, sizeof(line) - pos, ";a%u=%ld",
                                (unsigned int)a, ranges_mm[a]);
    }
    printk("%s\n", line);
}

/* Re-point the radio for tag mode: same PHY, just move the 16-bit short address
 * to our on-air tag id. HW frame filter stays disabled (DWT_FF_NOTYPE_EN from
 * listener_radio_configure), so every anchor response is delivered and validated
 * in SW via uwb_ss_twr_resp_matches(). Antenna delays (16436) are already set. */
static void listener_tag_configure(void)
{
    dwt_forcetrxoff();
    dwt_rxreset();
    dwt_setaddress16(APP_LISTENER_TAG_ADDR);
    dwt_setrxtimeout(0U);
    dwt_write32bitreg(SYS_STATUS_ID,
                      SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_TO |
                          SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_GOOD);
}

/* Send one broadcast poll and collect anchor responses for one window. */
static void listener_tag_poll_once(void)
{
    long ranges_mm[UWB_MAX_ANCHORS];
    bool got[UWB_MAX_ANCHORS];
    uint32_t poll_tx_ts;
    uint32_t win_start;
    uint32_t t0;
    uint8_t responses = 0U;

    for (uint8_t i = 0U; i < UWB_MAX_ANCHORS; ++i) {
        ranges_mm[i] = -1L;
        got[i] = false;
    }

    uwb_ss_twr_build_alt_broadcast_poll_frame(
        tag_poll_msg, tag_frame_seq_nb, APP_LISTENER_TAG_ADDR,
        LISTENER_TAG_ANCHOR_MASK,
        (uint8_t)(APP_LISTENER_TAG_ADDR & UWB_MSG_BCAST_POLL_TAG_ID_MASK), 0U,
        0U);

    dwt_forcetrxoff();
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_TX);
    if (dwt_writetxdata(UWB_MSG_ALT_BCAST_POLL_FRAME_LEN, tag_poll_msg, 0) !=
        DWT_SUCCESS) {
        counters.tag_tx_errors++;
        return;
    }
    dwt_writetxfctrl(UWB_MSG_ALT_BCAST_POLL_FRAME_LEN, 0, 1);
    if (dwt_starttx(DWT_START_TX_IMMEDIATE) != DWT_SUCCESS) {
        counters.tag_tx_errors++;
        dwt_forcetrxoff();
        return;
    }

    /* wait (bounded) for TX complete before reading the poll TX timestamp */
    t0 = k_uptime_get_32();
    while ((dwt_read32bitreg(SYS_STATUS_ID) & SYS_STATUS_TXFRS) == 0U) {
        if ((k_uptime_get_32() - t0) > LISTENER_TAG_TXDONE_TIMEOUT_MS) {
            counters.tag_tx_errors++;
            dwt_forcetrxoff();
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_TX);
            return;
        }
    }
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_TX);
    poll_tx_ts = dwt_readtxtimestamplo32();
    tag_frame_seq_nb++;

    (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
    win_start = k_uptime_get_32();
    while ((k_uptime_get_32() - win_start) < LISTENER_TAG_RESP_WINDOW_MS) {
        uint32_t status_reg = dwt_read32bitreg(SYS_STATUS_ID);

        if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
            uint32_t frame_len =
                dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFL_MASK_1023;
            uint32_t resp_rx_ts;
            int32_t carrier;
            uint16_t src;

            if (frame_len > sizeof(rx_buffer)) {
                dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_GOOD |
                                                     SYS_STATUS_ALL_RX_ERR |
                                                     SYS_STATUS_ALL_RX_TO);
                dwt_rxreset();
                (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
                continue;
            }
            memset(rx_buffer, 0, sizeof(rx_buffer));
            dwt_readrxdata(rx_buffer, (uint16)frame_len, 0U);
            resp_rx_ts = dwt_readrxtimestamplo32();
            carrier = dwt_readcarrierintegrator();
            src = uwb_frame_get_src_addr(rx_buffer);

            if (uwb_short_addr_is_anchor(src) &&
                uwb_ss_twr_resp_matches(rx_buffer, APP_LISTENER_TAG_ADDR, src)) {
                uint8_t aid = uwb_anchor_id_from_addr(src);

                if (aid < UWB_MAX_ANCHORS && !got[aid]) {
                    uint32_t poll_rx_ts;
                    uint32_t resp_tx_ts;

                    listener_read_ts(&rx_buffer[UWB_MSG_RESP_POLL_RX_TS_IDX],
                                     &poll_rx_ts);
                    listener_read_ts(&rx_buffer[UWB_MSG_RESP_RESP_TX_TS_IDX],
                                     &resp_tx_ts);
                    ranges_mm[aid] = listener_tag_calc_range_mm(
                        poll_tx_ts, resp_rx_ts, poll_rx_ts, resp_tx_ts, carrier);
                    got[aid] = true;
                    responses++;
                    counters.tag_resp_total++;
                }
            }
            /* release this frame + re-arm for the next ranked responder
             * (baseline single buffer, RXAUTR off: manual re-enable is
             * load-bearing -- mirrors ss_twr_init_alt_rx_release_frame). */
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_GOOD |
                                                 SYS_STATUS_ALL_RX_ERR |
                                                 SYS_STATUS_ALL_RX_TO);
            (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
            if (responses >= UWB_MAX_ANCHORS) {
                break;
            }
        } else if ((status_reg &
                    (SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO)) != 0U) {
            dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_GOOD |
                                                 SYS_STATUS_ALL_RX_ERR |
                                                 SYS_STATUS_ALL_RX_TO);
            dwt_rxreset();
            (void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
        }
    }
    dwt_forcetrxoff();

    print_ltag(ranges_mm);
    counters.tag_polls++;
}

/* ---- mode transitions (each prints a confirmation the host keys on) ---- */

static void listener_enter_idle(void)
{
    dwt_forcetrxoff();   /* no TX, no RX: safe during anchor AutoPos ranging */
    listener_mode = LISTENER_MODE_IDLE;
#if APP_LISTENER_LED_ENABLE != 0U
    if (led_ready) {
        (void)gpio_pin_set_dt(&listener_led, 0);   /* LED off */
    }
#endif
    printk("MODE=IDLE src=0x%04x\n", (unsigned int)APP_LISTENER_TAG_LABEL);
}

/* Restore passive listening -- reconfigures the radio to the exact boot state
 * (address 0xB1FE, frame filter off, event counters, RX-only) so CIR capture is
 * byte-identical to a fresh boot. Also resets the RX-stall watchdog so a switch
 * back does not immediately trip a false self-recover. */
static void listener_enter_listen(void)
{
    dwt_forcetrxoff();
    listener_radio_configure();
    lrec_head = 0U;
    lrec_tail = 0U;
    listener_restart_rx();
    wd_last_good = counters.good_frames;
    wd_last_activity_ms = k_uptime_get_32();
    listener_mode = LISTENER_MODE_LISTEN;
    printk("MODE=LISTEN src=0x%04x\n", (unsigned int)APP_LISTENER_TAG_LABEL);
}

static void listener_enter_tag(void)
{
    listener_tag_configure();
    /* poll on the next loop pass */
    tag_last_poll_ms = k_uptime_get_32() - LISTENER_TAG_POLL_INTERVAL_MS;
    listener_mode = LISTENER_MODE_TAG;
    printk("MODE=TAG src=0x%04x\n", (unsigned int)APP_LISTENER_TAG_LABEL);
}

/* Match one complete newline-terminated command line (exact, case-sensitive).
 * Unknown/partial/binary lines are silently ignored -- no mode change. */
static void listener_apply_command(const char *cmd)
{
    if (strcmp(cmd, "MODE_TAG") == 0) {
        listener_enter_tag();
    } else if (strcmp(cmd, "MODE_LISTEN") == 0) {
        listener_enter_listen();
    } else if (strcmp(cmd, "MODE_IDLE") == 0) {
        listener_enter_idle();
    } else if (strcmp(cmd, "MODE_QUERY") == 0) {
        const char *m = (listener_mode == LISTENER_MODE_TAG)  ? "TAG" :
                        (listener_mode == LISTENER_MODE_IDLE) ? "IDLE" :
                                                                "LISTEN";
        printk("MODE=%s src=0x%04x\n", m, (unsigned int)APP_LISTENER_TAG_LABEL);
    }
    /* else: ignore (partial command, CIR hex echo, or binary garbage) */
}

/* UART RX ISR: latch every incoming byte into the ring immediately. Minimal work
 * (no parsing); the main loop consumes the ring in listener_poll_commands(). */
static void listener_cmd_uart_isr(const struct device *dev, void *user_data)
{
    uint8_t byte;

    ARG_UNUSED(user_data);
    if (!uart_irq_update(dev)) {
        return;
    }
    while (uart_irq_rx_ready(dev)) {
        while (uart_fifo_read(dev, &byte, 1) == 1) {
            uint16_t next = (uint16_t)((cmd_rx_head + 1U) % LISTENER_CMD_RX_RING);

            if (next != cmd_rx_tail) {
                cmd_rx_ring[cmd_rx_head] = byte;
                cmd_rx_head = next;
            }
            /* else ring full: drop byte; host resends the command line */
        }
    }
}

/* Drain the RX ring into the line buffer and dispatch on newline. Called from the
 * main loop (parsing is NOT in the ISR); never blocks; flushes an over-length line
 * so a stray binary blob (e.g. host-echoed CIR hex) cannot wedge the parser. */
static void listener_poll_commands(void)
{
    while (cmd_rx_tail != cmd_rx_head) {
        unsigned char ch = cmd_rx_ring[cmd_rx_tail];

        cmd_rx_tail = (uint16_t)((cmd_rx_tail + 1U) % LISTENER_CMD_RX_RING);

        if (ch == '\n' || ch == '\r') {
            if (cmd_overflow) {
                cmd_overflow = false;
            } else if (cmd_len > 0U) {
                cmd_buf[cmd_len] = '\0';
                listener_apply_command(cmd_buf);
            }
            cmd_len = 0U;
        } else if (cmd_len >= (LISTENER_CMD_BUF_MAX - 1U)) {
            cmd_overflow = true;   /* too long: drop the whole line at newline */
            cmd_len = 0U;
        } else {
            cmd_buf[cmd_len++] = (char)ch;
        }
    }
}

int main(void)
{
    int ret;

    printk("BioSpur co-located UWB listener start id=%u near_anchor=%u cir=%u period=%u tag_addr=0x%04x label=0x%04x\n",
           (unsigned int)APP_LISTENER_ID,
           (unsigned int)APP_LISTENER_NEAR_ANCHOR_ID,
           (unsigned int)APP_LISTENER_CIR_CAPTURE_ENABLE,
           (unsigned int)APP_LISTENER_CIR_SAMPLE_PERIOD,
           (unsigned int)APP_LISTENER_TAG_ADDR,
           (unsigned int)APP_LISTENER_TAG_LABEL);

    ret = uwb_hw_bringup_and_init();
    if (ret != 0) {
        printk("listener UWB bringup failed: %d\n", ret);
        while (true) {
            k_msleep(1000);
        }
    }

    listener_radio_configure();
    listener_led_init();
    listener_restart_rx();

    /* Interrupt-driven RX for the command interface (see prj.conf note). */
    if (device_is_ready(cmd_uart)) {
        uart_irq_rx_disable(cmd_uart);
        uart_irq_tx_disable(cmd_uart);
        uart_irq_callback_user_data_set(cmd_uart, listener_cmd_uart_isr, NULL);
        uart_irq_rx_enable(cmd_uart);
    } else {
        printk("listener WARN: command UART not ready; MODE_* control disabled\n");
    }

    printk("listener RX-only poll diagnostics ready (MODE=LISTEN; USB-CDC cmd iface: MODE_TAG/MODE_LISTEN/MODE_IDLE/MODE_QUERY)\n");

    listener_mode = LISTENER_MODE_LISTEN;
    wd_last_good = 0U;
    wd_last_activity_ms = k_uptime_get_32();

    while (true) {
        uint32_t now_ms = k_uptime_get_32();

        /* Poll the USB-CDC command interface every pass, in every mode. May
         * switch listener_mode (which reconfigures the radio synchronously). */
        listener_poll_commands();

        if (listener_mode == LISTENER_MODE_TAG) {
            if ((uint32_t)(now_ms - tag_last_poll_ms) >=
                LISTENER_TAG_POLL_INTERVAL_MS) {
                tag_last_poll_ms = now_ms;
                listener_tag_poll_once();
            }
            k_msleep(1);
            continue;
        }

        if (listener_mode == LISTENER_MODE_IDLE) {
            /* Radio forced off (AutoPos-safe). Only the UART cmd path is live. */
            k_msleep(5);
            continue;
        }

        /* ---- LISTENER_MODE_LISTEN: original passive capture loop (unchanged) ---- */
        {
            uint32_t status_reg = dwt_read32bitreg(SYS_STATUS_ID);
            uint32_t drain_budget = LISTENER_DRAIN_BUDGET;
            counters.last_status = status_reg;

            if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
                capture_to_ring(now_ms);          /* priority: never miss a frame */
            } else if ((status_reg & (SYS_STATUS_ALL_RX_ERR |
                                      SYS_STATUS_ALL_RX_TO)) != 0U) {
                counters.rx_errors++;
                dwt_write32bitreg(SYS_STATUS_ID,
                                  SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_TO);
                dwt_rxreset();
                listener_restart_rx();
            }

            /* Fix 2: always flush a bounded budget to UART, even while RX is busy,
             * so LPD/LRD keep streaming under flood (drain not gated on RX idle). */
            while (drain_budget-- != 0U && lrec_head != lrec_tail) {
                drain_ring_one();
            }

            /* Fix 1: RX-stall watchdog. A new good frame resets the timer; if none
             * for WATCHDOG_MS, self-heal with a full radio recover. */
            if (counters.good_frames != wd_last_good) {
                wd_last_good = counters.good_frames;
                wd_last_activity_ms = now_ms;
            } else if ((now_ms - wd_last_activity_ms) >=
                       LISTENER_RX_STALL_WATCHDOG_MS) {
                listener_radio_full_recover();
                wd_last_activity_ms = now_ms;
            }

            print_status(now_ms);
            listener_led_idle(now_ms);
        }
        k_yield();
    }
}
