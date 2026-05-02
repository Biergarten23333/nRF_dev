#include <stdbool.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
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

static bool selected_anchor_is_fresh(uint32_t now_ms,
				     const struct anchor_rx_state **state_out)
{
	const struct anchor_rx_state *state = &anchor_state[selected_anchor_id];

	if (!state->valid || (now_ms - state->last_seen_ms) > LISTENER_STALE_MS) {
		return false;
	}

	if (state_out != NULL) {
		*state_out = state;
	}
	return true;
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
	uint32_t interval_ms;

	if (now_ms < selected_show_until_ms ||
	    !selected_anchor_is_fresh(now_ms, &state)) {
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

	if (now_ms < selected_show_until_ms) {
		shift595_write(led_anchor_mask(selected_anchor_id));
		return;
	}

	if (!selected_anchor_is_fresh(now_ms, &state)) {
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

static void handle_button(uint32_t now_ms)
{
	static bool last_raw_pressed;
	static bool stable_pressed;
	static uint32_t changed_ms;
	bool raw_pressed = gpio_pin_get(gpio0, SELECT_BUTTON_PIN) == 0;

	if (raw_pressed != last_raw_pressed) {
		last_raw_pressed = raw_pressed;
		changed_ms = now_ms;
		return;
	}

	if (raw_pressed == stable_pressed ||
	    (now_ms - changed_ms) < LISTENER_BUTTON_DEBOUNCE_MS) {
		return;
	}

	stable_pressed = raw_pressed;
	if (!stable_pressed) {
		return;
	}

	selected_anchor_id = (uint8_t)((selected_anchor_id + 1U) % UWB_MAX_ANCHORS);
	selected_show_until_ms = now_ms + LISTENER_SELECT_SHOW_MS;
	printk("listener selected anchor %c addr=0x%04x\n",
	       (char)('A' + selected_anchor_id),
	       (unsigned int)uwb_anchor_short_addr(selected_anchor_id));
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
	printk("listener RX-only mode ready; selected anchor A addr=0x%04x\n",
	       (unsigned int)uwb_anchor_short_addr(0U));

	while (true) {
		uint32_t now_ms = k_uptime_get_32();
		uint32_t status_reg;

		if (APP_LISTENER_UI_ENABLE) {
			handle_button(now_ms);
		}

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
