#include <stdbool.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include <hal/nrf_gpio.h>

#undef GPIO_DIR_MASK
#include <deca_device_api.h>
#include <deca_regs.h>

#include "uwb_bringup.h"
#include "uwb_ss_twr_shared.h"

#define LISTENER_RX_BUF_LEN 127U
#define LISTENER_STALE_MS 1500U
#define LISTENER_SELECT_SHOW_MS 650U
#define LISTENER_BUTTON_DEBOUNCE_MS 35U
#define LISTENER_STATUS_PRINT_MS 1000U
#define LISTENER_CHASE_STEP_MS 90U
#define LISTENER_BUZZER_CLICK_MS 18U
#define LISTENER_DEBUG_AUTO_START_MS 5000U
#define LISTENER_ANY_FRAME_PRINT_LIMIT 32U
#define LISTENER_ANY_FRAME_PRINT_EVERY 50U

#ifndef APP_LISTENER_UL_TRACE_ENABLE
#define APP_LISTENER_UL_TRACE_ENABLE 0
#endif

#ifndef APP_LISTENER_UL_TRACE_EVERY
#define APP_LISTENER_UL_TRACE_EVERY 1U
#endif

#ifndef APP_LISTENER_UF_TRACE_ENABLE
#define APP_LISTENER_UF_TRACE_ENABLE 0
#endif

#ifndef APP_LISTENER_UF_TRACE_EVERY
#define APP_LISTENER_UF_TRACE_EVERY 1U
#endif

#ifndef APP_LISTENER_UI_ENABLE
#define APP_LISTENER_UI_ENABLE 1
#endif

#ifndef APP_LISTENER_STATUS_PRINT_ENABLE
#define APP_LISTENER_STATUS_PRINT_ENABLE 0
#endif

#ifndef APP_LISTENER_DEBUG_PRINT_ENABLE
#define APP_LISTENER_DEBUG_PRINT_ENABLE 0
#endif

/* DWM1001-DEV J10 header pins as nRF52832 GPIO numbers. */
#define SHIFT595_SER_PIN 6U
#define SHIFT595_SRCLK_PIN 4U
#define SHIFT595_SRCLK_ALT_PIN 8U
#define SHIFT595_RCLK_PIN 3U
#define SHIFT595_RCLK_ALT_PIN 7U
#define SELECT_BUTTON_PIN 26U
#define BUZZER_PIN 15U

/* ---- MODE_SCAN (handheld self-positioning UWB channel probe) --------------
 *
 * MODE_SCAN temporarily turns the passive Geiger into a broadcast Alt-SS-TWR
 * *tag*: it polls all 8 anchors at 10 Hz, computes per-anchor ranges from the
 * response timestamps, reads the DW1000 CIR accumulator for one (round-robin)
 * anchor per cycle, and streams everything over the console VCOM as an LSCAN
 * line -- while the LED bar + buzzer keep clicking, now driven by the signal
 * quality of the anchor *responses* (the anchor->Geiger channel, which is the
 * correct metric while hand-scanning).
 *
 * The passive protocol helpers already linked from the repo-root shared lib
 * (uwb_short_addr_is_anchor / uwb_anchor_id_from_addr / uwb_frame_get_src_addr /
 * uwb_ss_twr_resp_matches) are reused unchanged. Only the broadcast poll builder
 * and the SS-TWR range math -- which the repo-root lib lacks -- are vendored as
 * static helpers below, so MODE_GEIGER's object code is byte-for-byte identical.
 *
 * On-air source address MUST be a tag id (0xB100..0xB1FF) or the anchor
 * responders reject the poll. 0xB1C0 is used: it does not collide with the 7 CIR
 * listeners (0xB1C1..0xB1C7) or the wand tags. */
#define SCAN_SRC_ADDR 0xB1C0U            /* on-air tag src; in 0xB100..0xB1FF */
#define SCAN_ANCHOR_MASK 0xFFU           /* poll all 8 anchors each cycle */
#define SCAN_POLL_INTERVAL_MS 100U       /* 10 Hz poll cadence */
#define SCAN_RESP_WINDOW_MS 12U          /* ranging pass: covers rank-7 (~8.2 ms) */
#define SCAN_CIR_WINDOW_MS 6U            /* CIR pass: target is rank 0 (~1.2 ms) */
#define SCAN_TXDONE_TIMEOUT_MS 5U
#define SCAN_SPEED_OF_LIGHT 299702547.0  /* m/s, matches ss_twr_init.c */

/* Response payload layout (broadcast Alt-SS-TWR): the responder embeds its
 * poll-RX and resp-TX DW1000 timestamps so the initiator can close the SS-TWR
 * equation. Mirrors broadcast/include/uwb_ss_twr_shared.h. */
#define SCAN_RESP_POLL_RX_TS_IDX 10U
#define SCAN_RESP_RESP_TX_TS_IDX 14U
#define SCAN_RESP_TS_LEN 4U

/* Broadcast poll frame layout (17 bytes). Byte 10 packs the low-nibble tag id
 * and the high-nibble responder-rank offset; byte 11 is the anchor mask; bytes
 * 12..16 carry the poll TX timestamp (unused by SS-TWR, sent as 0). */
#define SCAN_POLL_FRAME_LEN 17U
#define SCAN_BCAST_DST_ADDR 0xFFFFU
#define SCAN_POLL_TAG_ID_IDX 10U
#define SCAN_POLL_ANCHOR_MASK_IDX 11U
#define SCAN_POLL_TX_TS_IDX 12U
#define SCAN_POLL_TAG_ID_MASK 0x0FU
#define SCAN_POLL_RANK_OFFSET_SHIFT 4U
#define SCAN_POLL_RANK_OFFSET_MASK 0xF0U

/* Full CIR accumulator: ACC_MEM_LEN (4064) real bytes = 1016 complex taps.
 * The port SPI transfer is capped at UWB_SPI_BUF_SIZE (1024 B), so the
 * accumulator MUST be read in chunks; a single 4064 B read is rejected (returns
 * zeros). SCAN_CIR_READ_CHUNK stays well under the 1024 cap incl. SPI header. */
#define SCAN_CIR_DATA_LEN ACC_MEM_LEN
#define SCAN_CIR_READ_CHUNK 512U
/* Capture a CIR every N poll cycles (1 = every cycle). A full dump is ~178 ms of
 * VCOM at 460800, so N=1 throttles the effective loop to ~5 Hz; raise N to keep
 * ranges near 10 Hz with sparser CIR. */
#ifndef SCAN_CIR_EVERY_N
#define SCAN_CIR_EVERY_N 1U
#endif
/* Hex-stream chunk (bytes) between LED/buzzer service calls, so a long CIR dump
 * never freezes the Geiger clicks. */
#define SCAN_CIR_HEX_CHUNK 48U

/* Button hold (ms) that toggles MODE_GEIGER<->MODE_SCAN. */
#define SCAN_LONG_PRESS_MS 2000U

/* SCAN-mode LED indicator: first + last bar LEDs on (bits 0 and 7). Written ONCE
 * on mode entry; the 74HC595 latches it, so it stays lit with zero loop cost and
 * does not touch the tight poll loop. */
#define SCAN_LED_INDICATOR 0x81U

/* UART command interface (host-driven mode switch), mirrors the CIR listener. */
#define SCAN_CMD_BUF_MAX 32U
#define SCAN_CMD_RX_RING 32U

enum geiger_mode {
	MODE_GEIGER = 0,   /* boot default: passive RX-only Geiger UI (unchanged) */
	MODE_SCAN,         /* active broadcast Alt-SS-TWR probe */
};

struct anchor_rx_state {
	uint32_t last_seen_ms;
	uint32_t frame_count;
	uint32_t rx_power_q;
	uint8_t level;
	bool valid;
};

struct listener_rx_counters {
	uint32_t good_frames;
	uint32_t accepted_anchor_frames;
	uint32_t bad_header_frames;
	uint32_t non_anchor_frames;
	uint32_t too_long_frames;
	uint32_t rx_error_events;
	uint32_t rx_enable_failures;
	uint32_t last_frame_ms;
	uint32_t last_error_status;
	uint32_t last_rx_enable_error;
	uint16_t last_src_addr;
	uint16_t last_dst_addr;
	uint16_t last_pan_id;
	uint8_t last_code;
	uint8_t last_len;
};

static const struct device *gpio0 = DEVICE_DT_GET(DT_NODELABEL(gpio0));
static uint8_t rx_buffer[LISTENER_RX_BUF_LEN];
static struct anchor_rx_state anchor_state[UWB_MAX_ANCHORS];
static struct listener_rx_counters rx_counters;
static uint8_t selected_anchor_id;
static uint32_t selected_show_until_ms;
static uint32_t last_status_print_ms;
static uint32_t last_rx_debug_print_ms;
static uint32_t buzzer_next_click_ms;
static uint32_t buzzer_off_ms;

/* ---- MODE_SCAN state (all file-scope so a mode switch can reset cleanly) ---- */
static volatile enum geiger_mode current_mode = MODE_GEIGER;

/* UART RX ring filled by the ISR (producer), drained by the main loop (consumer).
 * SPSC: ISR advances head, loop advances tail; volatile 16-bit indices are atomic
 * on this single core, so no lock is needed. */
static const struct device *const cmd_uart =
	DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
static uint8_t cmd_rx_ring[SCAN_CMD_RX_RING];
static volatile uint16_t cmd_rx_head;
static volatile uint16_t cmd_rx_tail;
static char cmd_buf[SCAN_CMD_BUF_MAX];
static uint8_t cmd_len;
static bool cmd_overflow;

static uint8_t scan_poll_msg[SCAN_POLL_FRAME_LEN];
static uint8_t scan_seq_nb;
static uint32_t scan_last_poll_ms;
static uint32_t scan_cycle;            /* round-robin CIR-target selector */
/* Full CIR accumulator buffer (static: 4064 B of real taps off the 2 KB main
 * stack). scan_read_cir() strips the per-read dummy byte, so this holds contiguous
 * real accumulator bytes [0..SCAN_CIR_DATA_LEN-1]. */
static uint8_t scan_cir_buf[SCAN_CIR_DATA_LEN];

/* Forward decl: handle_button() (defined below, above the SCAN block) toggles
 * the mode on a long press. */
static void scan_toggle_mode(void);

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

static void shift595_delay(void)
{
	k_busy_wait(1);
}

static void shift595_write(uint8_t mask)
{
	for (int bit = 7; bit >= 0; --bit) {
		nrf_gpio_pin_write(SHIFT595_SER_PIN, (mask >> bit) & 0x1);
		shift595_delay();
		nrf_gpio_pin_set(SHIFT595_SRCLK_PIN);
		nrf_gpio_pin_set(SHIFT595_SRCLK_ALT_PIN);
		shift595_delay();
		nrf_gpio_pin_clear(SHIFT595_SRCLK_PIN);
		nrf_gpio_pin_clear(SHIFT595_SRCLK_ALT_PIN);
	}

	shift595_delay();
	nrf_gpio_pin_set(SHIFT595_RCLK_PIN);
	nrf_gpio_pin_set(SHIFT595_RCLK_ALT_PIN);
	shift595_delay();
	nrf_gpio_pin_clear(SHIFT595_RCLK_PIN);
	nrf_gpio_pin_clear(SHIFT595_RCLK_ALT_PIN);
}

static uint8_t led_bar_mask(uint8_t level)
{
	if (level == 0U) {
		return 0U;
	}
	if (level >= 8U) {
		return 0xffU;
	}

	return (uint8_t)((1U << level) - 1U);
}

static uint8_t led_anchor_mask(uint8_t anchor_id)
{
	if (anchor_id >= UWB_MAX_ANCHORS) {
		return 0U;
	}

	return (uint8_t)(1U << anchor_id);
}

static bool button_raw_pressed(void)
{
	return gpio_pin_get(gpio0, SELECT_BUTTON_PIN) == 0;
}

static void buzzer_set(bool on)
{
	nrf_gpio_pin_write(BUZZER_PIN, on ? 1U : 0U);
}

/* Follow mode: AUTO (default) tracks the strongest anchor currently on air so the
 * LED bar + buzzer react to UWB signal strength with no button press; pressing the
 * button locks onto a specific anchor A..H, and pressing past H returns to AUTO. */
#define LISTENER_ANCHOR_NONE 0xFFU
static bool follow_auto = true;

static bool anchor_is_fresh(uint8_t anchor_id, uint32_t now_ms,
			    const struct anchor_rx_state **state_out)
{
	const struct anchor_rx_state *state;

	if (anchor_id >= UWB_MAX_ANCHORS) {
		return false;
	}

	state = &anchor_state[anchor_id];
	if (!state->valid || (now_ms - state->last_seen_ms) > LISTENER_STALE_MS) {
		return false;
	}

	if (state_out != NULL) {
		*state_out = state;
	}
	return true;
}

/* Strongest fresh anchor by level; ties broken by most-recently-seen. */
static uint8_t strongest_fresh_anchor(uint32_t now_ms)
{
	uint8_t best = LISTENER_ANCHOR_NONE;
	uint8_t best_level = 0U;
	uint32_t best_seen = 0U;

	for (uint8_t id = 0U; id < UWB_MAX_ANCHORS; ++id) {
		const struct anchor_rx_state *st;

		if (!anchor_is_fresh(id, now_ms, &st)) {
			continue;
		}
		if (best == LISTENER_ANCHOR_NONE || st->level > best_level ||
		    (st->level == best_level && st->last_seen_ms > best_seen)) {
			best = id;
			best_level = st->level;
			best_seen = st->last_seen_ms;
		}
	}

	return best;
}

/* Which anchor the LED bar + buzzer should reflect right now. */
static uint8_t effective_anchor_id(uint32_t now_ms)
{
	if (follow_auto) {
		return strongest_fresh_anchor(now_ms);
	}
	return selected_anchor_id;
}

static int listener_gpio_init(void)
{
	int ret;

	if (!device_is_ready(gpio0)) {
		printk("gpio0 not ready\n");
		return -ENODEV;
	}

	ret = gpio_pin_configure(gpio0, SHIFT595_SER_PIN, GPIO_OUTPUT_INACTIVE);
	if (ret != 0) {
		return ret;
	}
	ret = gpio_pin_configure(gpio0, SHIFT595_SRCLK_PIN, GPIO_OUTPUT_INACTIVE);
	if (ret != 0) {
		return ret;
	}
	ret = gpio_pin_configure(gpio0, SHIFT595_SRCLK_ALT_PIN,
				 GPIO_OUTPUT_INACTIVE);
	if (ret != 0) {
		return ret;
	}
	ret = gpio_pin_configure(gpio0, SHIFT595_RCLK_PIN, GPIO_OUTPUT_INACTIVE);
	if (ret != 0) {
		return ret;
	}
	ret = gpio_pin_configure(gpio0, SHIFT595_RCLK_ALT_PIN,
				 GPIO_OUTPUT_INACTIVE);
	if (ret != 0) {
		return ret;
	}
	ret = gpio_pin_configure(gpio0, SELECT_BUTTON_PIN,
				 GPIO_INPUT | GPIO_PULL_UP);
	if (ret != 0) {
		return ret;
	}
	ret = gpio_pin_configure(gpio0, BUZZER_PIN, GPIO_OUTPUT_INACTIVE);
	if (ret != 0) {
		return ret;
	}

	shift595_write(0U);
	buzzer_set(false);
	return 0;
}

static void shift595_power_on_self_test(void)
{
	for (uint8_t i = 0U; i < 3U; ++i) {
		shift595_write(0x00U);
		k_msleep(200);
		shift595_write(0xffU);
		k_msleep(200);
	}

	shift595_write(0x00U);
}

static void wait_for_start_button(void)
{
	uint8_t chase_pos = 0U;
	uint32_t start_ms = k_uptime_get_32();
	uint32_t last_chase_ms = 0U;
	uint32_t low_since_ms = 0U;

	printk("waiting for button press; running LED chase, button is active-low\n");

	while (true) {
		uint32_t now_ms = k_uptime_get_32();
		bool pressed = button_raw_pressed();

		if ((now_ms - last_chase_ms) >= LISTENER_CHASE_STEP_MS) {
			last_chase_ms = now_ms;
			shift595_write(led_anchor_mask(chase_pos));
			chase_pos = (uint8_t)((chase_pos + 1U) % UWB_MAX_ANCHORS);
		}

		if (pressed) {
			if (low_since_ms == 0U) {
				low_since_ms = now_ms;
				printk("button low detected\n");
			} else if ((now_ms - low_since_ms) >=
				   LISTENER_BUTTON_DEBOUNCE_MS) {
				printk("button accepted; starting UWB listener\n");
				shift595_write(0xffU);
				k_msleep(120);
				shift595_write(0U);

				while (button_raw_pressed()) {
					k_msleep(5);
				}
				k_msleep(LISTENER_BUTTON_DEBOUNCE_MS);
				return;
			}
		} else {
			low_since_ms = 0U;
		}

		if ((now_ms - start_ms) >= LISTENER_DEBUG_AUTO_START_MS) {
			printk("debug auto-start after %u ms without button\n",
			       LISTENER_DEBUG_AUTO_START_MS);
			shift595_write(0xffU);
			k_msleep(120);
			shift595_write(0U);
			return;
		}

		k_msleep(5);
	}
}

static bool frame_has_biospur_header(const uint8_t *frame, uint32_t len)
{
	uint16_t pan_id;

	if (len < UWB_MSG_COMMON_LEN) {
		return false;
	}
	if (frame[0] != UWB_FRAME_CTRL_LOW || frame[1] != UWB_FRAME_CTRL_HIGH) {
		return false;
	}

	pan_id = (uint16_t)frame[UWB_MSG_PAN_IDX] |
		 ((uint16_t)frame[UWB_MSG_PAN_IDX + 1U] << 8);
	return pan_id == APP_UWB_PAN_ID;
}

static uint32_t rx_power_q_from_diag(const dwt_rxdiag_t *diag)
{
	uint64_t denom;

	if (diag->rxPreamCount == 0U) {
		return 0U;
	}

	denom = (uint64_t)diag->rxPreamCount * (uint64_t)diag->rxPreamCount;
	return (uint32_t)(((uint64_t)diag->maxGrowthCIR << 17) / denom);
}

static uint8_t rx_level_from_q(uint32_t q)
{
	static const uint32_t thresholds[8] = {
		300U, 700U, 1500U, 3000U,
		5500U, 9000U, 14000U, 22000U,
	};
	uint8_t level = 0U;

	for (size_t i = 0U; i < ARRAY_SIZE(thresholds); ++i) {
		if (q < thresholds[i]) {
			break;
		}
		level++;
	}

	return level;
}

static void update_buzzer(uint32_t now_ms)
{
	static const uint16_t intervals_ms[9] = {
		1400U, 1000U, 700U, 450U, 280U,
		160U, 90U, 55U, 32U,
	};
	const struct anchor_rx_state *state;
	uint8_t anchor_id = effective_anchor_id(now_ms);
	uint32_t interval_ms;

	if (now_ms < selected_show_until_ms ||
	    !anchor_is_fresh(anchor_id, now_ms, &state)) {
		buzzer_set(false);
		buzzer_next_click_ms = now_ms;
		buzzer_off_ms = 0U;
		return;
	}

	if (buzzer_off_ms != 0U && now_ms >= buzzer_off_ms) {
		buzzer_set(false);
		buzzer_off_ms = 0U;
	}

	if (now_ms < buzzer_next_click_ms) {
		return;
	}

	interval_ms = intervals_ms[MIN(state->level, 8U)];
	buzzer_set(true);
	buzzer_off_ms = now_ms + LISTENER_BUZZER_CLICK_MS;
	buzzer_next_click_ms = now_ms + interval_ms;
}

static void display_selected_anchor(uint32_t now_ms)
{
	const struct anchor_rx_state *state;
	uint8_t anchor_id;

	if (now_ms < selected_show_until_ms) {
		/* brief confirm: AUTO -> all LEDs, locked -> that anchor's bit */
		shift595_write(follow_auto ? 0xffU :
					     led_anchor_mask(selected_anchor_id));
		return;
	}

	anchor_id = effective_anchor_id(now_ms);
	if (!anchor_is_fresh(anchor_id, now_ms, &state)) {
		shift595_write(0U);
		return;
	}

	if (state->level <= 1U) {
		shift595_write(((now_ms / 250U) & 0x1U) != 0U ? 0x01U : 0x00U);
		return;
	}

	if (state->level >= 8U) {
		shift595_write(((now_ms / 125U) & 0x1U) != 0U ? 0xffU : 0x00U);
		return;
	}

	shift595_write(led_bar_mask(state->level));
}

/* Button: a debounced SHORT press keeps the existing behaviour (MODE_GEIGER
 * anchor-select cycle; in MODE_SCAN it nudges the round-robin CIR target). A LONG
 * press (hold >= SCAN_LONG_PRESS_MS) toggles MODE_GEIGER<->MODE_SCAN.
 *
 * Discriminating short from long requires deciding on release, so the short-press
 * action now fires on release instead of the press edge -- functionally identical
 * for a human tap. The passive RX/LED/buzzer signal path is untouched. */
static void handle_button(uint32_t now_ms)
{
	static bool last_raw_pressed;
	static bool stable_pressed;
	static uint32_t changed_ms;
	static uint32_t press_start_ms;
	static bool long_fired;
	bool raw_pressed = gpio_pin_get(gpio0, SELECT_BUTTON_PIN) == 0;

	if (raw_pressed != last_raw_pressed) {
		last_raw_pressed = raw_pressed;
		changed_ms = now_ms;
	}

	/* long-press toggle: fires once, mid-hold, in either mode */
	if (stable_pressed && !long_fired &&
	    (now_ms - press_start_ms) >= SCAN_LONG_PRESS_MS) {
		long_fired = true;
		if (APP_LISTENER_UI_ENABLE) {
			shift595_write(0xffU);   /* brief all-on confirm of the toggle */
		}
		scan_toggle_mode();
		selected_show_until_ms = now_ms + LISTENER_SELECT_SHOW_MS;
	}

	if (raw_pressed == stable_pressed ||
	    (now_ms - changed_ms) < LISTENER_BUTTON_DEBOUNCE_MS) {
		return;
	}

	stable_pressed = raw_pressed;
	if (stable_pressed) {
		/* press confirmed: start timing; the short action is deferred to release */
		press_start_ms = now_ms;
		long_fired = false;
		return;
	}

	/* release confirmed */
	if (long_fired) {
		return;   /* the hold already toggled the mode; no short action */
	}

	if (current_mode == MODE_SCAN) {
		/* short press in SCAN: advance the CIR-target round-robin phase */
		scan_cycle++;
		return;
	}

	/* MODE_GEIGER short press: existing anchor-select cycle (unchanged logic) */
	/* cycle: AUTO -> A -> B -> ... -> H -> AUTO */
	if (follow_auto) {
		follow_auto = false;
		selected_anchor_id = 0U;
	} else if ((selected_anchor_id + 1U) >= UWB_MAX_ANCHORS) {
		follow_auto = true;
	} else {
		selected_anchor_id = (uint8_t)(selected_anchor_id + 1U);
	}

	selected_show_until_ms = now_ms + LISTENER_SELECT_SHOW_MS;
	if (follow_auto) {
		printk("listener follow=AUTO (strongest anchor on air)\n");
	} else {
		printk("listener selected anchor %c addr=0x%04x\n",
		       (char)('A' + selected_anchor_id),
		       (unsigned int)uwb_anchor_short_addr(selected_anchor_id));
	}
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
		rx_counters.rx_enable_failures++;
		rx_counters.last_rx_enable_error = (uint32_t)ret;
	}
}

static uint16_t read_le16_if_present(const uint8_t *frame, uint32_t len,
				     uint8_t offset)
{
	if (len <= (uint32_t)offset + 1U) {
		return 0U;
	}

	return (uint16_t)frame[offset] | ((uint16_t)frame[offset + 1U] << 8);
}

static void print_any_good_frame(uint32_t now_ms, const uint8_t *frame,
				 uint32_t frame_len, const dwt_rxdiag_t *diag,
				 uint32_t q, uint8_t level)
{
	uint16_t pan_id = read_le16_if_present(frame, frame_len, UWB_MSG_PAN_IDX);
	uint16_t dst_addr = read_le16_if_present(frame, frame_len, UWB_MSG_DST_IDX);
	uint16_t src_addr = read_le16_if_present(frame, frame_len, UWB_MSG_SRC_IDX);
	uint8_t code = frame_len > UWB_MSG_CODE_IDX ? frame[UWB_MSG_CODE_IDX] : 0U;
	bool should_print = rx_counters.good_frames <= LISTENER_ANY_FRAME_PRINT_LIMIT ||
			    (rx_counters.good_frames %
			     LISTENER_ANY_FRAME_PRINT_EVERY) == 0U;

	if (!should_print) {
		return;
	}

	printk("uwb any-frame t=%lu good=%lu len=%lu fctrl=%02x%02x pan=0x%04x dst=0x%04x src=0x%04x code=0x%02x q=%lu level=%u cir=%u rxpacc=%u fp=%u/%u/%u\n",
	       (unsigned long)now_ms,
	       (unsigned long)rx_counters.good_frames,
	       (unsigned long)frame_len,
	       (unsigned int)(frame_len > 1U ? frame[1] : 0U),
	       (unsigned int)(frame_len > 0U ? frame[0] : 0U),
	       (unsigned int)pan_id,
	       (unsigned int)dst_addr,
	       (unsigned int)src_addr,
	       (unsigned int)code,
	       (unsigned long)q,
	       (unsigned int)level,
	       (unsigned int)diag->maxGrowthCIR,
	       (unsigned int)diag->rxPreamCount,
	       (unsigned int)diag->firstPathAmp1,
	       (unsigned int)diag->firstPathAmp2,
	       (unsigned int)diag->firstPathAmp3);
}

static void print_ul_trace(uint32_t now_ms, uint32_t frame_len,
			   const dwt_rxdiag_t *diag, uint32_t q,
			   uint8_t level, uint16_t src_addr,
			   uint16_t dst_addr, uint8_t code, uint8_t anchor_id)
{
#if APP_LISTENER_UL_TRACE_ENABLE
	if (APP_LISTENER_UL_TRACE_EVERY > 1U &&
	    (rx_counters.accepted_anchor_frames %
	     APP_LISTENER_UL_TRACE_EVERY) != 0U) {
		return;
	}

	printk("UL;%lu;%lu;%c;%u;0x%04x;0x%04x;0x%02x;%lu;%u;%u;%u;%u;%u\n",
	       (unsigned long)now_ms,
	       (unsigned long)rx_counters.accepted_anchor_frames,
	       (char)('A' + anchor_id),
	       (unsigned int)anchor_id,
	       (unsigned int)src_addr,
	       (unsigned int)dst_addr,
	       (unsigned int)code,
	       (unsigned long)q,
	       (unsigned int)level,
	       (unsigned int)diag->maxGrowthCIR,
	       (unsigned int)diag->rxPreamCount,
	       (unsigned int)diag->firstPathAmp1,
	       (unsigned int)frame_len);
#else
	ARG_UNUSED(now_ms);
	ARG_UNUSED(frame_len);
	ARG_UNUSED(diag);
	ARG_UNUSED(q);
	ARG_UNUSED(level);
	ARG_UNUSED(src_addr);
	ARG_UNUSED(dst_addr);
	ARG_UNUSED(code);
	ARG_UNUSED(anchor_id);
#endif
}

static void print_uf_trace(uint32_t now_ms, uint32_t frame_len,
			   const dwt_rxdiag_t *diag, uint32_t q,
			   uint8_t level)
{
#if APP_LISTENER_UF_TRACE_ENABLE
	uint16_t pan_id = rx_counters.last_pan_id;
	uint16_t src_addr = rx_counters.last_src_addr;
	uint16_t dst_addr = rx_counters.last_dst_addr;
	uint8_t code = rx_counters.last_code;

	if (APP_LISTENER_UF_TRACE_EVERY > 1U &&
	    (rx_counters.good_frames % APP_LISTENER_UF_TRACE_EVERY) != 0U) {
		return;
	}

	printk("UF;%lu;%lu;0x%04x;0x%04x;0x%04x;0x%02x;%lu;%u;%u;%u;%u;%u\n",
	       (unsigned long)now_ms,
	       (unsigned long)rx_counters.good_frames,
	       (unsigned int)pan_id,
	       (unsigned int)src_addr,
	       (unsigned int)dst_addr,
	       (unsigned int)code,
	       (unsigned long)q,
	       (unsigned int)level,
	       (unsigned int)diag->maxGrowthCIR,
	       (unsigned int)diag->rxPreamCount,
	       (unsigned int)diag->firstPathAmp1,
	       (unsigned int)frame_len);
#else
	ARG_UNUSED(now_ms);
	ARG_UNUSED(frame_len);
	ARG_UNUSED(diag);
	ARG_UNUSED(q);
	ARG_UNUSED(level);
#endif
}

static void handle_good_frame(uint32_t now_ms)
{
	dwt_rxdiag_t diag;
	uint32 frame_len;
	uint16_t src_addr;
	uint8_t anchor_id;
	uint32_t q;
	uint8_t level;

	memset(&diag, 0, sizeof(diag));
	dwt_readdiagnostics(&diag);

	frame_len = dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFL_MASK_1023;
	dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG);
	rx_counters.good_frames++;
	rx_counters.last_frame_ms = now_ms;
	rx_counters.last_len = (uint8_t)MIN(frame_len, 255U);

	if (frame_len > sizeof(rx_buffer)) {
		rx_counters.too_long_frames++;
		listener_restart_rx();
		return;
	}

	memset(rx_buffer, 0, sizeof(rx_buffer));
	dwt_readrxdata(rx_buffer, (uint16)frame_len, 0U);

	rx_counters.last_pan_id = read_le16_if_present(rx_buffer, frame_len,
						       UWB_MSG_PAN_IDX);
	rx_counters.last_dst_addr = read_le16_if_present(rx_buffer, frame_len,
						       UWB_MSG_DST_IDX);
	rx_counters.last_src_addr = read_le16_if_present(rx_buffer, frame_len,
						       UWB_MSG_SRC_IDX);
	rx_counters.last_code = frame_len > UWB_MSG_CODE_IDX ?
				rx_buffer[UWB_MSG_CODE_IDX] : 0U;

	q = rx_power_q_from_diag(&diag);
	level = rx_level_from_q(q);
	print_any_good_frame(now_ms, rx_buffer, frame_len, &diag, q, level);

	if (!frame_has_biospur_header(rx_buffer, frame_len)) {
		rx_counters.bad_header_frames++;
		listener_restart_rx();
		return;
	}

	print_uf_trace(now_ms, frame_len, &diag, q, level);

	src_addr = uwb_frame_get_src_addr(rx_buffer);

	if (!uwb_short_addr_is_anchor(src_addr)) {
		rx_counters.non_anchor_frames++;
		listener_restart_rx();
		return;
	}

	anchor_id = uwb_anchor_id_from_addr(src_addr);
	if (anchor_id >= UWB_MAX_ANCHORS) {
		listener_restart_rx();
		return;
	}

	rx_counters.accepted_anchor_frames++;
	anchor_state[anchor_id].last_seen_ms = now_ms;
	anchor_state[anchor_id].frame_count++;
	anchor_state[anchor_id].rx_power_q = q;
	anchor_state[anchor_id].level = level;
	anchor_state[anchor_id].valid = true;
	print_ul_trace(now_ms, frame_len, &diag, q, level, src_addr,
		       rx_counters.last_dst_addr, rx_counters.last_code,
		       anchor_id);

	if (APP_LISTENER_STATUS_PRINT_ENABLE &&
	    anchor_id == selected_anchor_id &&
	    (now_ms - last_status_print_ms) >= LISTENER_STATUS_PRINT_MS) {
		last_status_print_ms = now_ms;
		printk("listener anchor=%c src=0x%04x code=0x%02x level=%u q=%lu cir=%u rxpacc=%u fp=%u/%u/%u frames=%lu\n",
		       (char)('A' + anchor_id), (unsigned int)src_addr,
		       (unsigned int)rx_buffer[UWB_MSG_CODE_IDX],
		       (unsigned int)anchor_state[anchor_id].level,
		       (unsigned long)q, (unsigned int)diag.maxGrowthCIR,
		       (unsigned int)diag.rxPreamCount,
		       (unsigned int)diag.firstPathAmp1,
		       (unsigned int)diag.firstPathAmp2,
		       (unsigned int)diag.firstPathAmp3,
		       (unsigned long)anchor_state[anchor_id].frame_count);
	}

	listener_restart_rx();
}

static void print_rx_debug(uint32_t now_ms)
{
	const struct anchor_rx_state *selected = &anchor_state[selected_anchor_id];
	uint8_t sys_state[SYS_STATE_LEN] = { 0 };
	uint32_t sys_status;
	uint32_t rx_finfo;
	uint32_t age_ms = selected->valid ? now_ms - selected->last_seen_ms : 0U;

	if (!APP_LISTENER_DEBUG_PRINT_ENABLE) {
		return;
	}

	if ((now_ms - last_rx_debug_print_ms) < 1000U) {
		return;
	}
	last_rx_debug_print_ms = now_ms;

	sys_status = dwt_read32bitreg(SYS_STATUS_ID);
	rx_finfo = dwt_read32bitreg(RX_FINFO_ID);
	dwt_readfromdevice(SYS_STATE_ID, 0U, SYS_STATE_LEN, sys_state);

	printk("listener debug sel=%c valid=%u age=%lu level=%u good=%lu accepted=%lu badhdr=%lu nonanchor=%lu toolong=%lu rxerr=%lu rxen_fail=%lu rxen_last=%ld sys_status=0x%08lx sys_state=%02x%02x%02x%02x%02x rx_finfo=0x%08lx last_status=0x%08lx last_len=%u pan=0x%04x dst=0x%04x src=0x%04x code=0x%02x\n",
	       (char)('A' + selected_anchor_id),
	       selected->valid ? 1U : 0U,
	       (unsigned long)age_ms,
	       (unsigned int)selected->level,
	       (unsigned long)rx_counters.good_frames,
	       (unsigned long)rx_counters.accepted_anchor_frames,
	       (unsigned long)rx_counters.bad_header_frames,
	       (unsigned long)rx_counters.non_anchor_frames,
	       (unsigned long)rx_counters.too_long_frames,
	       (unsigned long)rx_counters.rx_error_events,
	       (unsigned long)rx_counters.rx_enable_failures,
	       (long)rx_counters.last_rx_enable_error,
	       (unsigned long)sys_status,
	       (unsigned int)sys_state[0],
	       (unsigned int)sys_state[1],
	       (unsigned int)sys_state[2],
	       (unsigned int)sys_state[3],
	       (unsigned int)sys_state[4],
	       (unsigned long)rx_finfo,
	       (unsigned long)rx_counters.last_error_status,
	       (unsigned int)rx_counters.last_len,
	       (unsigned int)rx_counters.last_pan_id,
	       (unsigned int)rx_counters.last_dst_addr,
	       (unsigned int)rx_counters.last_src_addr,
	       (unsigned int)rx_counters.last_code);
}

/* ======================================================================== *
 *  MODE_SCAN: broadcast Alt-SS-TWR probe (poll -> ranges + CIR)
 *  Vendored broadcast-protocol helpers (repo-root shared lib is unicast-only).
 * ======================================================================== */

/* Build the 17-byte broadcast Alt-SS-TWR poll. dst = 0xFFFF broadcast, src =
 * SCAN_SRC_ADDR (tag id). Byte 10 packs the low-nibble tag id + high-nibble
 * responder-rank offset; byte 11 is the anchor mask; bytes 12..16 are the poll
 * TX timestamp field (unused by SS-TWR, sent as 0). Mirrors
 * uwb_ss_twr_build_alt_broadcast_poll_frame() in the broadcast shared lib. */
static void scan_build_bcast_poll(uint8_t *frame, uint8_t seq, uint8_t rank_offset)
{
	uint8_t tag_id = (uint8_t)(SCAN_SRC_ADDR & SCAN_POLL_TAG_ID_MASK);

	frame[0] = UWB_FRAME_CTRL_LOW;
	frame[1] = UWB_FRAME_CTRL_HIGH;
	frame[UWB_MSG_SN_IDX] = seq;
	frame[UWB_MSG_PAN_IDX] = (uint8_t)(APP_UWB_PAN_ID & 0xFFU);
	frame[UWB_MSG_PAN_IDX + 1U] = (uint8_t)((APP_UWB_PAN_ID >> 8) & 0xFFU);
	frame[UWB_MSG_DST_IDX] = (uint8_t)(SCAN_BCAST_DST_ADDR & 0xFFU);
	frame[UWB_MSG_DST_IDX + 1U] = (uint8_t)((SCAN_BCAST_DST_ADDR >> 8) & 0xFFU);
	frame[UWB_MSG_SRC_IDX] = (uint8_t)(SCAN_SRC_ADDR & 0xFFU);
	frame[UWB_MSG_SRC_IDX + 1U] = (uint8_t)((SCAN_SRC_ADDR >> 8) & 0xFFU);
	frame[UWB_MSG_CODE_IDX] = UWB_MSG_POLL_CODE;
	frame[SCAN_POLL_TAG_ID_IDX] =
		(uint8_t)((tag_id & SCAN_POLL_TAG_ID_MASK) |
			  ((rank_offset << SCAN_POLL_RANK_OFFSET_SHIFT) &
			   SCAN_POLL_RANK_OFFSET_MASK));
	frame[SCAN_POLL_ANCHOR_MASK_IDX] = SCAN_ANCHOR_MASK;
	for (uint8_t i = 0U; i < 5U; ++i) {   /* 5-byte poll TX ts field, sent as 0 */
		frame[SCAN_POLL_TX_TS_IDX + i] = 0U;
	}
}

/* Little-endian 32-bit timestamp read from a response payload field. */
static void scan_read_ts(const uint8_t *field, uint32_t *ts)
{
	*ts = 0U;
	for (uint8_t i = 0U; i < SCAN_RESP_TS_LEN; ++i) {
		*ts |= ((uint32_t)field[i]) << (i * 8U);
	}
}

/* Single-sided TWR range in mm from the four timestamps + carrier integrator.
 * Identical math to ss_twr_init_calc_raw_distance_mm() / the CIR listener's
 * listener_tag_calc_range_mm() (channel-5 constants). */
static long scan_calc_range_mm(uint32_t poll_tx_ts, uint32_t resp_rx_ts,
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
	long raw_mm = (long)(tof * SCAN_SPEED_OF_LIGHT * 1000.0);

	return raw_mm < 0L ? 0L : raw_mm;
}

/* Per-frame clock-tracking + AGC diagnostics for the current RX frame. MUST be
 * read before re-arming RX (which overwrites these registers). Mirrors the CIR
 * listener's capture_to_ring register reads. */
static void scan_read_frame_diag(uint8_t *rcph, int32_t *rxtofs,
				 uint32_t *ttcki, uint16_t *agc_edg1)
{
	uint8_t ttcko_raw[6] = { 0 };
	uint32_t ttcko_lo;
	int32_t ofs;
	uint32_t agc_stat1;

	dwt_readfromdevice(RX_TTCKO_ID, 0U, sizeof(ttcko_raw), ttcko_raw);
	ttcko_lo = (uint32_t)ttcko_raw[0] | ((uint32_t)ttcko_raw[1] << 8) |
		   ((uint32_t)ttcko_raw[2] << 16) | ((uint32_t)ttcko_raw[3] << 24);
	ofs = (int32_t)(ttcko_lo & RX_TTCKO_RXTOFS_MASK);
	if ((ofs & 0x40000) != 0) {
		ofs |= (int32_t)0xFFF80000UL;   /* sign-extend 19->32 bits */
	}
	*rxtofs = ofs;
	*rcph = ttcko_raw[5] & 0x7FU;       /* RCPHASE lives in byte 5 (bits 40-46) */
	*ttcki = dwt_read32bitreg(RX_TTCKI_ID);
	agc_stat1 = dwt_read32bitoffsetreg(AGC_CTRL_ID, AGC_STAT1_OFFSET) &
		    AGC_STAT1_MASK;
	*agc_edg1 = (uint16_t)((agc_stat1 & AGC_STAT1_EDG1_MASK) >> 6);
}

/* Read the full CIR accumulator into scan_cir_buf in SPI-sized chunks. A single
 * 4064 B read exceeds the port's UWB_SPI_BUF_SIZE cap and is silently rejected
 * (returns zeros), so we read SCAN_CIR_READ_CHUNK real bytes per dwt_readaccdata()
 * call. Each call returns a leading dummy byte (SPI access delay), stripped here
 * so scan_cir_buf holds contiguous real bytes. MUST be called before re-arming RX
 * (a new reception overwrites ACC_MEM); the accumulator is stable across chunks. */
static void scan_read_cir(void)
{
	static uint8_t tmp[SCAN_CIR_READ_CHUNK + 1U];
	uint16_t off = 0U;

	while (off < SCAN_CIR_DATA_LEN) {
		uint16_t n = (uint16_t)MIN((uint16_t)SCAN_CIR_READ_CHUNK,
					   (uint16_t)(SCAN_CIR_DATA_LEN - off));

		dwt_readaccdata(tmp, (uint16)(n + 1U), off);
		memcpy(&scan_cir_buf[off], &tmp[1], n);
		off = (uint16_t)(off + n);
	}
}

/* Re-point the radio for SCAN: same PHY/antenna delays/PAN/frame-filter as the
 * passive boot config, just move the 16-bit short address to our on-air tag id.
 * HW frame filter stays disabled (DWT_FF_NOTYPE_EN), so every anchor response is
 * delivered and validated in SW via uwb_ss_twr_resp_matches(). */
static void scan_configure_radio(void)
{
	dwt_forcetrxoff();
	dwt_rxreset();
	dwt_setaddress16(SCAN_SRC_ADDR);
	dwt_setrxtimeout(0U);
	dwt_write32bitreg(SYS_STATUS_ID,
			  SYS_STATUS_ALL_TX | SYS_STATUS_ALL_RX_TO |
				  SYS_STATUS_ALL_RX_ERR | SYS_STATUS_ALL_RX_GOOD);
}

/* Emit one LSCAN line: ranges for all 8 anchors, then (when a CIR was captured)
 * the target's per-frame diagnostics + the full accumulator as hex. Streamed in
 * chunks; nothing else writes the UART, so the whole line stays contiguous. The
 * LED/buzzer are off in SCAN mode, so there is no UI to service during the dump. */
static void scan_print_lscan(const long *ranges_mm, bool cir,
			     uint8_t target, uint8_t rcph, int32_t rxtofs,
			     uint32_t ttcki, uint16_t agc)
{
	static const char hexchars[] = "0123456789ABCDEF";
	char head[160];
	size_t pos = 0U;

	pos += (size_t)snprintk(head + pos, sizeof(head) - pos,
				"LSCAN;src=0x%04x", (unsigned int)SCAN_SRC_ADDR);
	for (uint8_t a = 0U; a < UWB_MAX_ANCHORS && pos < sizeof(head); ++a) {
		pos += (size_t)snprintk(head + pos, sizeof(head) - pos,
					";a%u=%ld", (unsigned int)a, ranges_mm[a]);
	}

	if (!cir) {
		printk("%s\n", head);
		return;
	}

	printk("%s;cir_aid=%u;rcph=%u;rxtofs=%d;ttcki=%lu;agc=%u;cir=",
	       head, (unsigned int)target, (unsigned int)rcph, (int)rxtofs,
	       (unsigned long)ttcki, (unsigned int)agc);

	/* scan_cir_buf holds contiguous real taps [0..SCAN_CIR_DATA_LEN-1]. */
	for (uint16_t off = 0U; off < SCAN_CIR_DATA_LEN; off += SCAN_CIR_HEX_CHUNK) {
		char line[SCAN_CIR_HEX_CHUNK * 2U + 1U];
		uint16_t n = (uint16_t)MIN((uint16_t)SCAN_CIR_HEX_CHUNK,
					   (uint16_t)(SCAN_CIR_DATA_LEN - off));
		size_t lp = 0U;

		for (uint16_t i = 0U; i < n; ++i) {
			uint8_t b = scan_cir_buf[off + i];

			line[lp++] = hexchars[(b >> 4) & 0x0FU];
			line[lp++] = hexchars[b & 0x0FU];
		}
		line[lp] = '\0';
		printk("%s", line);
	}
	printk("\n");
}

/* Send one broadcast poll (rank_offset selects the responder ordering). On TX
 * complete, latch the poll TX timestamp and return true. */
static bool scan_send_poll(uint8_t rank_offset, uint32_t *poll_tx_ts)
{
	uint32_t t0;

	scan_build_bcast_poll(scan_poll_msg, scan_seq_nb, rank_offset);

	dwt_forcetrxoff();
	dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_TX);
	if (dwt_writetxdata(SCAN_POLL_FRAME_LEN, scan_poll_msg, 0) != DWT_SUCCESS) {
		return false;
	}
	dwt_writetxfctrl(SCAN_POLL_FRAME_LEN, 0, 1);
	if (dwt_starttx(DWT_START_TX_IMMEDIATE) != DWT_SUCCESS) {
		dwt_forcetrxoff();
		return false;
	}

	t0 = k_uptime_get_32();
	while ((dwt_read32bitreg(SYS_STATUS_ID) & SYS_STATUS_TXFRS) == 0U) {
		if ((k_uptime_get_32() - t0) > SCAN_TXDONE_TIMEOUT_MS) {
			dwt_forcetrxoff();
			dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_TX);
			return false;
		}
	}
	dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_TX);
	*poll_tx_ts = dwt_readtxtimestamplo32();
	scan_seq_nb++;
	return true;
}

/* Validate the just-received frame as an anchor response to our poll; on success
 * compute its range into ranges_mm[] and return the anchor id (else UWB_MAX_ANCHORS).
 * Deliberately minimal -- NO dwt_readdiagnostics (that ~1 ms/frame LED/buzzer read
 * made this loop slower than the 1 ms inter-responder gap, so every other, odd-rank,
 * responder was dropped). The LED/buzzer are off in SCAN mode, so they are not
 * needed here; MODE_GEIGER's own path is unchanged. Does NOT re-arm RX or touch
 * ACC_MEM, so the caller may still read the accumulator for this frame. */
static uint8_t scan_handle_response(uint32_t poll_tx_ts, long *ranges_mm)
{
	uint32_t frame_len =
		dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXFL_MASK_1023;
	uint32_t resp_rx_ts;
	int32_t carrier;
	uint16_t src;

	if (frame_len > sizeof(rx_buffer)) {
		return UWB_MAX_ANCHORS;
	}
	memset(rx_buffer, 0, sizeof(rx_buffer));
	dwt_readrxdata(rx_buffer, (uint16)frame_len, 0U);
	resp_rx_ts = dwt_readrxtimestamplo32();
	carrier = dwt_readcarrierintegrator();
	src = uwb_frame_get_src_addr(rx_buffer);

	if (uwb_short_addr_is_anchor(src) &&
	    uwb_ss_twr_resp_matches(rx_buffer, SCAN_SRC_ADDR, src)) {
		uint8_t aid = uwb_anchor_id_from_addr(src);

		if (aid < UWB_MAX_ANCHORS) {
			uint32_t poll_rx_ts;
			uint32_t resp_tx_ts;

			scan_read_ts(&rx_buffer[SCAN_RESP_POLL_RX_TS_IDX], &poll_rx_ts);
			scan_read_ts(&rx_buffer[SCAN_RESP_RESP_TX_TS_IDX], &resp_tx_ts);
			ranges_mm[aid] = scan_calc_range_mm(poll_tx_ts, resp_rx_ts,
							    poll_rx_ts, resp_tx_ts,
							    carrier);
			return aid;
		}
	}
	return UWB_MAX_ANCHORS;
}

/* Full-ranging pass: natural anchor ranks (rank_offset 0), collect every anchor's
 * range. No accumulator read and no per-frame diagnostics, so processing stays
 * well under the 1 ms inter-responder gap and no responder is missed -- this is
 * what keeps all 8 ranges available every cycle. */
static void scan_ranging_pass(long *ranges_mm)
{
	uint32_t poll_tx_ts;
	uint32_t win_start;

	if (!scan_send_poll(0U, &poll_tx_ts)) {
		return;
	}
	(void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
	win_start = k_uptime_get_32();
	while ((k_uptime_get_32() - win_start) < SCAN_RESP_WINDOW_MS) {
		uint32_t status_reg = dwt_read32bitreg(SYS_STATUS_ID);

		if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
			(void)scan_handle_response(poll_tx_ts, ranges_mm);
			dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_GOOD |
							     SYS_STATUS_ALL_RX_ERR |
							     SYS_STATUS_ALL_RX_TO);
			(void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
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
}

/* CIR pass: a dedicated poll with `target` forced to reply FIRST (rank_offset =
 * target -> rank 0: the earliest, least-contended slot with the shortest reply
 * delay, so the target's own range stays accurate). On the target's response,
 * read its per-frame diagnostics + the full accumulator before re-arm, then stop.
 * Returns true iff the CIR was captured this cycle. */
static bool scan_cir_pass(uint8_t target, long *ranges_mm,
			  uint8_t *rcph, int32_t *rxtofs, uint32_t *ttcki,
			  uint16_t *agc)
{
	uint32_t poll_tx_ts;
	uint32_t win_start;
	bool captured = false;

	if (!scan_send_poll(target, &poll_tx_ts)) {
		return false;
	}
	(void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
	win_start = k_uptime_get_32();
	while ((k_uptime_get_32() - win_start) < SCAN_CIR_WINDOW_MS) {
		uint32_t status_reg = dwt_read32bitreg(SYS_STATUS_ID);

		if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
			uint8_t aid = scan_handle_response(poll_tx_ts, ranges_mm);

			if (aid == target) {
				/* registers + ACC still hold THIS frame; read
				 * before the re-arm below overwrites them */
				scan_read_frame_diag(rcph, rxtofs, ttcki, agc);
				scan_read_cir();
				captured = true;
			}
			dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_GOOD |
							     SYS_STATUS_ALL_RX_ERR |
							     SYS_STATUS_ALL_RX_TO);
			(void)dwt_rxenable(DWT_START_RX_IMMEDIATE);
			if (captured) {
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
	return captured;
}

/* One 10 Hz cycle: a full-ranging pass (all anchors, accurate ranges + LED/buzzer)
 * followed -- when due -- by a dedicated CIR pass for the round-robin target. */
static void scan_poll_once(void)
{
	long ranges_mm[UWB_MAX_ANCHORS];
	uint8_t target = (uint8_t)(scan_cycle % UWB_MAX_ANCHORS);
	bool do_cir = (SCAN_CIR_EVERY_N <= 1U) ||
		      ((scan_cycle % SCAN_CIR_EVERY_N) == 0U);
	bool cir = false;
	uint8_t rcph = 0U;
	int32_t rxtofs = 0;
	uint32_t ttcki = 0U;
	uint16_t agc = 0U;

	for (uint8_t i = 0U; i < UWB_MAX_ANCHORS; ++i) {
		ranges_mm[i] = -1L;
	}

	scan_ranging_pass(ranges_mm);
	if (do_cir) {
		cir = scan_cir_pass(target, ranges_mm, &rcph, &rxtofs,
				    &ttcki, &agc);
	}

	scan_print_lscan(ranges_mm, cir, target, rcph, rxtofs, ttcki, agc);
	scan_cycle++;
}

/* ---- mode transitions (each prints a confirmation the host keys on) ---- */

static void scan_enter_geiger(void)
{
	current_mode = MODE_GEIGER;
	/* Restore the exact passive boot radio state (addr 0xB1FE, frame filter off,
	 * RX-only) so MODE_GEIGER capture is byte-identical to a fresh boot. */
	listener_radio_configure();
	listener_restart_rx();
	printk("MODE=GEIGER\n");
}

static void scan_enter_scan(void)
{
	current_mode = MODE_SCAN;
	scan_configure_radio();
	/* SCAN is a calibration/probe mode: the buzzer is silenced and the LED bar
	 * shows a static "SCAN mode" indicator (first + last LED). Both are set once
	 * here; nothing is serviced in the tight poll loop, so there is no timing cost.
	 * Returning to GEIGER lets display_selected_anchor() reclaim the bar. */
	if (APP_LISTENER_UI_ENABLE) {
		shift595_write(SCAN_LED_INDICATOR);
		buzzer_set(false);
	}
	scan_last_poll_ms = k_uptime_get_32() - SCAN_POLL_INTERVAL_MS;  /* poll next pass */
	printk("MODE=SCAN src=0x%04x\n", (unsigned int)SCAN_SRC_ADDR);
}

static void scan_toggle_mode(void)
{
	if (current_mode == MODE_GEIGER) {
		scan_enter_scan();
	} else {
		scan_enter_geiger();
	}
}

/* Match one complete newline-terminated command (exact, case-sensitive). Unknown
 * / partial / binary lines are silently ignored. */
static void scan_apply_command(const char *cmd)
{
	if (strcmp(cmd, "MODE_SCAN") == 0) {
		scan_enter_scan();
	} else if (strcmp(cmd, "MODE_GEIGER") == 0) {
		scan_enter_geiger();
	} else if (strcmp(cmd, "MODE_QUERY") == 0) {
		if (current_mode == MODE_SCAN) {
			printk("MODE=SCAN src=0x%04x\n", (unsigned int)SCAN_SRC_ADDR);
		} else {
			printk("MODE=GEIGER\n");
		}
	}
	/* else: ignore (partial command, CIR hex echo, or binary garbage) */
}

/* UART RX ISR: latch every incoming byte into the ring immediately (no parsing);
 * the main loop consumes the ring in scan_poll_commands(). */
static void scan_cmd_uart_isr(const struct device *dev, void *user_data)
{
	uint8_t byte;

	ARG_UNUSED(user_data);
	if (!uart_irq_update(dev)) {
		return;
	}
	while (uart_irq_rx_ready(dev)) {
		while (uart_fifo_read(dev, &byte, 1) == 1) {
			uint16_t next = (uint16_t)((cmd_rx_head + 1U) % SCAN_CMD_RX_RING);

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
 * so a stray binary blob cannot wedge the parser. */
static void scan_poll_commands(void)
{
	while (cmd_rx_tail != cmd_rx_head) {
		unsigned char ch = cmd_rx_ring[cmd_rx_tail];

		cmd_rx_tail = (uint16_t)((cmd_rx_tail + 1U) % SCAN_CMD_RX_RING);

		if (ch == '\n' || ch == '\r') {
			if (cmd_overflow) {
				cmd_overflow = false;
			} else if (cmd_len > 0U) {
				cmd_buf[cmd_len] = '\0';
				scan_apply_command(cmd_buf);
			}
			cmd_len = 0U;
		} else if (cmd_len >= (SCAN_CMD_BUF_MAX - 1U)) {
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

	printk("BioSpur UWB passive listener start\n");
	printk("anchor IDs: A=0x%04x B=0x%04x C=0x%04x D=0x%04x E=0x%04x F=0x%04x G=0x%04x H=0x%04x\n",
	       (unsigned int)uwb_anchor_short_addr(0U),
	       (unsigned int)uwb_anchor_short_addr(1U),
	       (unsigned int)uwb_anchor_short_addr(2U),
	       (unsigned int)uwb_anchor_short_addr(3U),
	       (unsigned int)uwb_anchor_short_addr(4U),
	       (unsigned int)uwb_anchor_short_addr(5U),
	       (unsigned int)uwb_anchor_short_addr(6U),
	       (unsigned int)uwb_anchor_short_addr(7U));

	if (APP_LISTENER_UI_ENABLE) {
		ret = listener_gpio_init();
		if (ret != 0) {
			printk("listener GPIO init failed: %d\n", ret);
			return ret;
		}

		shift595_power_on_self_test();
		wait_for_start_button();
	} else {
		printk("listener UI disabled; serial-only capture starts immediately\n");
	}

	ret = uwb_hw_bringup_and_init();
	if (ret != 0) {
		printk("listener UWB bringup failed: %d\n", ret);
		while (true) {
			if (APP_LISTENER_UI_ENABLE) {
				shift595_write(0x55U);
			}
			k_msleep(150);
			if (APP_LISTENER_UI_ENABLE) {
				shift595_write(0xaaU);
				k_msleep(150);
			}
		}
	}

	listener_radio_configure();
	listener_restart_rx();
	selected_show_until_ms = k_uptime_get_32() + LISTENER_SELECT_SHOW_MS;

	/* Interrupt-driven RX for the MODE_SCAN/MODE_GEIGER command interface. The
	 * legacy nRF UART has a 1-byte RX register with no FIFO, so the ISR latches
	 * every byte into a ring the main loop drains (see prj.conf note). */
	if (device_is_ready(cmd_uart)) {
		uart_irq_rx_disable(cmd_uart);
		uart_irq_tx_disable(cmd_uart);
		uart_irq_callback_user_data_set(cmd_uart, scan_cmd_uart_isr, NULL);
		uart_irq_rx_enable(cmd_uart);
	} else {
		printk("listener WARN: command UART not ready; MODE_* control disabled\n");
	}

	printk("listener ready; boot MODE=GEIGER (passive); follow=AUTO. Button: short=cycle A..H/AUTO, hold>2s=toggle SCAN. Cmd: MODE_SCAN/MODE_GEIGER/MODE_QUERY (VCOM 460800)\n");

	while (true) {
		uint32_t now_ms = k_uptime_get_32();
		uint32_t status_reg;

		if (APP_LISTENER_UI_ENABLE) {
			handle_button(now_ms);
		}
		scan_poll_commands();   /* host MODE_* switch; RX-safe in both modes */

		if (current_mode == MODE_SCAN) {
			/* SCAN: LED/buzzer are off (see scan_enter_scan); the loop
			 * just polls. GEIGER's UI path below is untouched. */
			if ((uint32_t)(now_ms - scan_last_poll_ms) >=
			    SCAN_POLL_INTERVAL_MS) {
				scan_last_poll_ms = now_ms;
				scan_poll_once();
			}
			k_msleep(2);
			continue;
		}

		/* ---- MODE_GEIGER: original passive capture loop (unchanged) ---- */
		status_reg = dwt_read32bitreg(SYS_STATUS_ID);
		if ((status_reg & SYS_STATUS_RXFCG) != 0U) {
			handle_good_frame(now_ms);
		} else if ((status_reg & (SYS_STATUS_ALL_RX_ERR |
					  SYS_STATUS_ALL_RX_TO)) != 0U) {
			rx_counters.rx_error_events++;
			rx_counters.last_error_status = status_reg;
			dwt_write32bitreg(SYS_STATUS_ID,
					  SYS_STATUS_ALL_RX_ERR |
						  SYS_STATUS_ALL_RX_TO);
			listener_restart_rx();
		}

		if (APP_LISTENER_UI_ENABLE) {
			display_selected_anchor(now_ms);
			update_buzzer(now_ms);
		}
		print_rx_debug(now_ms);
		k_msleep(5);
	}
}
