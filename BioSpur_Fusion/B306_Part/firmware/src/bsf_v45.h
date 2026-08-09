/*
 * bsf_v45.h -- the v45 runtime's interface to the application.
 *
 * bsf_v45.c owns no application state. Everything it needs arrives through
 * bsf_v45_env_get(), which main.c defines. The dependency points that way on
 * purpose: it is what lets bsf_v45_detector.h be compiled and unit-tested on
 * the host with no kernel and no application at all.
 */
#ifndef BSF_V45_H
#define BSF_V45_H

#include <stdbool.h>
#include <stdint.h>

#include <zephyr/kernel.h>

struct bsf_stall_ring;

/* Filled by main.c on demand; read by the detector once per second. */
struct bsf_v45_env {
	uint32_t node_identity;
	uint32_t fw_marker_hash;
	uint32_t boot_reset_reason;
	uint32_t epoch;
	uint32_t connected_at_ms;
	uint32_t producer_seq;
	uint32_t publisher_count;
	uint32_t wdt_feed_count;
	uint32_t notify_timeout_drop_total;
	uint32_t notify_exits_this_epoch;
	uint32_t notify_ok_total;      /* R4/A4 watermark C: DELIVERY          */
	uint32_t notconn_streak;       /* R4/A3 consecutive -ENOTCONN          */
	bool     connected;
	bool     data_subscribed;
	bool     telemetry_subscribed;
};

/* Defined in main.c. */
void bsf_v45_env_get(struct bsf_v45_env *out);

/*
 * `budget_take` is main.c's ONE-per-power-cycle reboot budget, shared with the
 * v42 ring ISR and the v43/v44 BT RX monitor. Three authorities, one reset:
 * anything else would let the second one land on top of the first one's
 * evidence.
 */
void bsf_v45_init(struct bsf_stall_ring *ring, struct k_spinlock *ring_lock,
		  bool (*budget_take)(uint32_t owner));
void bsf_v45_bind_app_threads(k_tid_t notify_worker, k_tid_t publisher);
void bsf_v45_connection_epoch_changed(uint32_t epoch, uint32_t now_ms);
void bsf_v45_ota_mark(bool active);
void bsf_v45_force(void);
/* Detector blindness, for V45 STATUS: an instrument that is off must say so. */
void bsf_v45_blind_report(uint32_t *blind_ms, uint32_t *ticks,
			  uint32_t *discards, uint8_t *armed);

/*
 * Feed the watchdog exactly once, from inside the capture routine.
 *
 * Defined in main.c, which owns the watchdog handle. Declared with no weak
 * default on purpose: if main.c ever stops providing it this must be a link
 * error, not a silent no-op that quietly reinstates the race below.
 *
 * WHY THIS IS NOT "EXTENDING THE WATCHDOG". WATCHDOG_TIMEOUT_MS stays at 30 s
 * and the periodic feed stays exactly where it is, on the system workqueue --
 * that feed is the diagnostic, and lengthening the timeout would blind it to
 * the real syswq deaths it exists to catch. This is one feed, a few
 * milliseconds, on a path that only ever runs after the detector has ALREADY
 * decided to capture. On a healthy node it never executes.
 *
 * ORDERING THAT MATTERS. The caller samples `env` -- including
 * `env->wdt_feed_count` -- before v45_capture() is entered, so the count
 * written into the corpse is the pre-kick one. The corpse still shows the
 * stalled feed; the kick only buys wall-clock to finish writing it.
 */
void bsf_v45_wdt_kick(void);

/*
 * The watchdog witness. bsf_v45_dog_boot() is called once at boot with whether
 * RESETREAS named the dog; the report rides V45 STATUS.
 *
 * This does NOT make the detector cover a system-workqueue death -- it cannot,
 * the detector rides that queue. It makes the resulting reset READABLE, so a
 * node that ate a watchdog stops being indistinguishable from a node where
 * nothing ever happened. The distinction is the difference between "the
 * detector has a hole" and "there was nothing to detect".
 */
void bsf_v45_dog_boot(bool was_dog);
void bsf_v45_dog_report(uint32_t *resets, uint8_t *dwell_active,
			uint32_t *dwell_age_ms, uint32_t *tick_ms);

/* Corpse state and export. */
bool     bsf_v45_present(void);
bool     bsf_v45_core_validate(void);
uint32_t bsf_v45_seq(void);
uint16_t bsf_v45_cause(void);
uint32_t bsf_v45_core_len(void);
uint32_t bsf_v45_image_len(void);
int      bsf_v45_image_read(uint32_t off, uint8_t *dst, uint32_t len);
bool     bsf_v45_ack(uint32_t seq);

/* Section 9. Both are no-ops unless BSF_CORPSE_FLASH_ENABLED=1. */
int  bsf_v45_flash_persist(uint8_t slot);
void bsf_v45_flash_persist_pending(void);

#endif /* BSF_V45_H */
