/*
 * BioSpur B306 stall trajectory ring — pure policy, no Zephyr dependency.
 *
 * D1 established that the publish path dies inside a single second and that
 * the finest series that reaches the host is 1 Hz, so "the last sample looked
 * healthy" is the strongest statement the existing telemetry can make. This
 * ring samples at 50 ms into the retained `.noinit` region, which survives the
 * soft reset the recovery path already performs, so the trajectory through the
 * transition is readable after the board comes back.
 *
 * Everything here is a pure function of the state passed in. The firmware owns
 * the sampling context (a k_timer expiry, i.e. the system-clock ISR) and the
 * locking; this file owns the geometry, the freeze policy, the page rendering
 * and the retrieval-view lifetime, so all four are testable natively.
 */
#ifndef BSF_STALL_RING_POLICY_H
#define BSF_STALL_RING_POLICY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "biospur_fusion_ble.h"

/*
 * Geometry.
 *
 * The detector dwells STALL_DETECT_MS = 5000 ms before it fires, so a ring
 * that froze on alarm must still hold the run-in from 5 s earlier. 200 entries
 * at 50 ms spans 10.0 s, which centres the freeze: alarm at onset + 5.0 s
 * leaves onset - 5.0 s .. onset + 5.0 s in the buffer. 200 x 40 B = 8000 B of
 * .noinit, against 262,144 B of SoC RAM.
 */
/*
 * v45 GEOMETRY CHANGE: 200 -> 510 entries.
 *
 * v44 accepted, and recorded, that raising the wedge threshold to 20 s pushed
 * the frozen 10 s window to [onset+10 s, onset+20 s] -- past the onset it was
 * meant to cover. 510 x 50 ms = 25.5 s puts the onset back inside it: a capture
 * at onset+20 s still holds 5.5 s of run-in. That is what lets the detector keep
 * a single, generous 20 s threshold without paying for it in trajectory.
 *
 * WHY 510 AND NOT THE 512 THE BRIEF ASKS FOR. 512 is not divisible by
 * BSF_STALL_RING_PAGE_ENTRIES (5), and this file has enforced "capacity divides
 * evenly into pages -- no partial last page" since the ring shipped. Rendering
 * handles a short last page correctly, so 512 would work; but the invariant is
 * cheap, it keeps every retrieval page full, and 510 vs 512 costs 0.1 s of span
 * out of 25.5. Taking the two-entry haircut is the smaller change.
 *
 * Cost: 510 x 40 B = 20 400 B of `.noinit`, +12 400 B over v44, against
 * 262 144 B of SoC RAM. The v44 comment weighed exactly this trade at 800
 * entries and deferred it "if the ring tail ever becomes load-bearing". Under
 * v45 it is: the corpse's suspect_ring_index points INTO this ring.
 *
 * The geometry stamp below does the rest. A v44 board's retained ring has
 * capacity 200 and is rejected as BSF_RING_BOOT_GEOMETRY rather than
 * reinterpreted -- which is exactly why Stage C mandates one full-fleet power
 * cycle after the OTA batch.
 */
#define BSF_STALL_RING_MAGIC 0x52334236u /* 'R','3','B','6' */
#define BSF_STALL_RING_CAPACITY 510u
#define BSF_STALL_RING_PERIOD_MS 50u
#define BSF_STALL_RING_PAGES \
	(BSF_STALL_RING_CAPACITY / BSF_STALL_RING_PAGE_ENTRIES) /* 40 */
#define BSF_STALL_RING_SPAN_MS \
	(BSF_STALL_RING_CAPACITY * BSF_STALL_RING_PERIOD_MS) /* 10000 */

/*
 * ISR-side backstop latch. 120 samples = 6.0 s of producers advancing with
 * zero publisher exits, above the longest single notify call ever measured on
 * a healthy board in N5 (publisher_max_us = 4,006,283 us on BSF6C53).
 *
 * This latch FREEZES A RAM BUFFER AND NOTHING ELSE. It cannot arm recovery,
 * cannot reboot and cannot disconnect -- bsf_stall_ring_freeze() writes four
 * fields of this struct. It therefore cannot reintroduce the v36 spurious-
 * reboot failure that the 5000 ms dwell was raised to fix; the only thing a
 * false latch costs is further ring coverage, and
 * bsf_stall_ring_retract_disconnect() below gives that back.
 *
 * It exists because the dwell path goes permanently inert in two situations
 * where the ring is still wanted -- see the comment on that function.
 */
#define BSF_STALL_RING_NO_EXIT_SAMPLES 120u

/*
 * How long a `RING PAGE=<n>` selection keeps the stall characteristic pointed
 * at the ring before it reverts to the ordinary status snapshot. Evaluated at
 * read time from the current uptime -- there is no timer, no work item and
 * nothing to cancel, so an abandoned retrieval cannot leave anything armed.
 */
#define BSF_STALL_RING_VIEW_TTL_MS 30000u

/*
 * Why the retained ring must validate itself, not just check a magic word.
 *
 * `.noinit` is by definition not zeroed at startup, so on the first boot after
 * a DFU this struct holds whatever the previous image left in that RAM. A
 * garbage ring rendered back as a trajectory would be worse than no ring at
 * all: a plausible-looking 50 ms series that means nothing. The magic alone is
 * not enough, because the far more likely accident is not random bytes but a
 * *different build's* ring landing at the same address with the same magic and
 * a different geometry. So the geometry is stamped into the struct and checked,
 * and every index invariant is checked, before a single entry is believed.
 */
enum bsf_stall_ring_boot {
	BSF_RING_BOOT_COLD = 0, /* no magic: first boot on this RAM */
	BSF_RING_BOOT_GEOMETRY = 1, /* magic, but a different build's layout */
	BSF_RING_BOOT_INVALID = 2, /* magic and layout, but the indices lie */
	BSF_RING_BOOT_RETAINED = 3, /* trustworthy, and it holds entries */
	BSF_RING_BOOT_EMPTY = 4, /* trustworthy, but it holds nothing */
};

struct bsf_stall_ring {
	uint32_t magic;
	uint32_t boot_id;
	uint32_t writes_total;
	uint32_t freeze_uptime_ms;
	uint32_t last_exit_seen;
	uint32_t last_heartbeat_seen;
	uint16_t head; /* next slot to write */
	uint16_t count; /* entries held, saturating at capacity */
	uint16_t freeze_index; /* logical index of the freeze point */
	uint16_t no_exit_samples;
	/* Geometry stamp: retained bytes from a differently shaped build are
	 * rejected rather than reinterpreted.
	 */
	uint16_t capacity;
	uint8_t entry_size;
	uint8_t period_ms;
	uint8_t frozen;
	uint8_t freeze_reason;
	uint8_t primed; /* last_* fields carry a real previous sample */
	uint8_t boot_result; /* enum bsf_stall_ring_boot, this boot */
	/*
	 * H1 self-reset. Retained deliberately: .noinit survives sys_reboot() but
	 * NOT power removal, so `isr_resets` gives exactly "at most one per power
	 * cycle" -- it persists across the reset it caused, which is what makes a
	 * boot loop impossible, and clears when the cell is actually pulled.
	 */
	uint8_t reset_pending; /* a reset is owed; the ISR claims it */
	uint8_t isr_resets;    /* how many this power cycle; hard bound 1 */
	uint16_t reserved2;
	bsf_stall_ring_entry_t entries[BSF_STALL_RING_CAPACITY];
};

#define BSF_STALL_RING_MAX_ISR_RESETS 1u

/* Retrieval view. Plain state; the TTL is applied by the accessor. */
struct bsf_stall_ring_view {
	uint32_t selected_uptime_ms;
	uint16_t page;
	uint8_t active;
	uint8_t reserved;
};

static inline uint16_t bsf_stall_ring_crc16(const uint8_t *data, size_t len)
{
	uint16_t crc = 0xffffu;

	for (size_t i = 0; i < len; ++i) {
		crc ^= (uint16_t)((uint16_t)data[i] << 8);
		for (uint8_t bit = 0; bit < 8u; ++bit) {
			crc = (crc & 0x8000u) != 0u ?
				      (uint16_t)((uint16_t)(crc << 1) ^ 0x1021u) :
				      (uint16_t)(crc << 1);
		}
	}
	return crc;
}

static inline const char *bsf_stall_ring_boot_name(uint8_t result)
{
	switch (result) {
	case BSF_RING_BOOT_COLD:
		return "cold";
	case BSF_RING_BOOT_GEOMETRY:
		return "invalid_geometry";
	case BSF_RING_BOOT_INVALID:
		return "invalid_indices";
	case BSF_RING_BOOT_RETAINED:
		return "retained";
	case BSF_RING_BOOT_EMPTY:
		return "empty";
	default:
		return "unknown";
	}
}

static inline void bsf_stall_ring_stamp(struct bsf_stall_ring *ring)
{
	ring->magic = BSF_STALL_RING_MAGIC;
	ring->capacity = BSF_STALL_RING_CAPACITY;
	ring->entry_size = (uint8_t)sizeof(bsf_stall_ring_entry_t);
	ring->period_ms = (uint8_t)BSF_STALL_RING_PERIOD_MS;
}

static inline void bsf_stall_ring_clear(struct bsf_stall_ring *ring)
{
	bsf_stall_ring_stamp(ring);
	ring->head = 0u;
	ring->count = 0u;
	ring->writes_total = 0u;
	ring->frozen = 0u;
	ring->freeze_reason = BSF_RING_FREEZE_NONE;
	ring->freeze_uptime_ms = 0u;
	ring->freeze_index = 0u;
	ring->no_exit_samples = 0u;
	ring->last_exit_seen = 0u;
	ring->last_heartbeat_seen = 0u;
	ring->primed = 0u;
	ring->reset_pending = 0u;
	/* isr_resets is deliberately NOT cleared: "one per power cycle" must not
	 * be resettable by a RING CLEAR or by the E2 disconnect retraction, or a
	 * flapping link could turn it into a reboot loop. */
}

/* Every index invariant the renderer and the sampler rely on. */
static inline bool bsf_stall_ring_consistent(const struct bsf_stall_ring *ring)
{
	if (ring->count > BSF_STALL_RING_CAPACITY ||
	    ring->head >= BSF_STALL_RING_CAPACITY ||
	    ring->freeze_index > ring->count ||
	    ring->no_exit_samples > BSF_STALL_RING_NO_EXIT_SAMPLES ||
	    ring->frozen > 1u || ring->primed > 1u ||
	    ring->reset_pending > 1u ||
	    ring->isr_resets > BSF_STALL_RING_MAX_ISR_RESETS ||
	    ring->freeze_reason > BSF_RING_FREEZE_MANUAL) {
		return false;
	}
	/* frozen and freeze_reason are set together or not at all. */
	if ((ring->frozen != 0u) != (ring->freeze_reason != BSF_RING_FREEZE_NONE)) {
		return false;
	}
	/* A partially filled ring has never wrapped, so head == count. */
	if (ring->count < BSF_STALL_RING_CAPACITY && ring->head != ring->count) {
		return false;
	}
	return true;
}

/*
 * Called once, very early, before anything can sample.
 *
 * Anything short of BSF_RING_BOOT_RETAINED means the retained bytes were NOT
 * believed: the ring is wiped and reported empty, and because count is then 0
 * there are no pages to render, so a rejected ring can never be read back as
 * data. The reason is reported by `RING STATUS` and in the boot banner, so
 * "0 pages" is never silently ambiguous between "nothing happened" and "we
 * threw it away".
 *
 * A ring that IS believed is not re-armed. If it is frozen it stays frozen
 * until an explicit `RING CLEAR`, so a recovery reboot cannot overwrite the
 * very trajectory it was triggered by. The operational consequence is
 * deliberate: a latched board keeps no further coverage until it is read and
 * cleared, and `RING STATUS` reports that state so a fleet sweep can see it.
 */
static inline enum bsf_stall_ring_boot
bsf_stall_ring_boot(struct bsf_stall_ring *ring)
{
	enum bsf_stall_ring_boot result;

	if (ring->magic != BSF_STALL_RING_MAGIC) {
		result = BSF_RING_BOOT_COLD;
	} else if (ring->capacity != BSF_STALL_RING_CAPACITY ||
		   ring->entry_size != sizeof(bsf_stall_ring_entry_t) ||
		   ring->period_ms != BSF_STALL_RING_PERIOD_MS) {
		result = BSF_RING_BOOT_GEOMETRY;
	} else if (!bsf_stall_ring_consistent(ring)) {
		result = BSF_RING_BOOT_INVALID;
	} else {
		ring->boot_id++;
		/* Cross-boot deltas are meaningless; re-prime on the next sample. */
		ring->primed = 0u;
		ring->no_exit_samples = 0u;
		result = ring->count != 0u ? BSF_RING_BOOT_RETAINED :
					     BSF_RING_BOOT_EMPTY;
		ring->boot_result = (uint8_t)result;
		return result;
	}

	memset(ring, 0, sizeof(*ring));
	bsf_stall_ring_stamp(ring);
	ring->boot_id = 1u;
	ring->boot_result = (uint8_t)result;
	return result;
}

/* First freeze wins, so a later cause can never relabel an earlier one. */
static inline bool bsf_stall_ring_freeze(struct bsf_stall_ring *ring,
					 uint8_t reason, uint32_t uptime_ms)
{
	if (ring->frozen != 0u || reason == BSF_RING_FREEZE_NONE) {
		return false;
	}
	ring->frozen = 1u;
	ring->freeze_reason = reason;
	ring->freeze_uptime_ms = uptime_ms;
	ring->freeze_index = ring->count;
	return true;
}

/*
 * Push one sample. Returns true when it was stored.
 *
 * `armed` must mean connected AND both streams subscribed. Without it the
 * no-exit latch would fire on every boot, because an unsubscribed publisher
 * dequeues into drop_unsub and never touches entry/exit while the producers
 * keep enqueuing.
 */
static inline bool bsf_stall_ring_push(struct bsf_stall_ring *ring,
				       const bsf_stall_ring_entry_t *entry,
				       bool armed)
{
	if (ring->frozen != 0u) {
		return false;
	}

	ring->entries[ring->head] = *entry;
	ring->head = (uint16_t)(ring->head + 1u);
	if (ring->head >= BSF_STALL_RING_CAPACITY) {
		ring->head = 0u;
	}
	if (ring->count < BSF_STALL_RING_CAPACITY) {
		ring->count++;
	}
	ring->writes_total++;

	if (!armed || ring->primed == 0u) {
		ring->no_exit_samples = 0u;
	} else if (entry->producer_heartbeat != ring->last_heartbeat_seen &&
		   entry->exit_count == ring->last_exit_seen) {
		ring->no_exit_samples++;
	} else {
		ring->no_exit_samples = 0u;
	}
	ring->last_exit_seen = entry->exit_count;
	ring->last_heartbeat_seen = entry->producer_heartbeat;
	ring->primed = 1u;

	if (ring->no_exit_samples >= BSF_STALL_RING_NO_EXIT_SAMPLES) {
		/*
		 * ORDERING IS THE WHOLE POINT: freeze first, then ask for the
		 * reset. The freeze is what makes the trajectory survivable; the
		 * reset is what makes it retrievable. If the reset went first,
		 * the very evidence it exists to rescue could still be rolling.
		 *
		 * `reset_pending` is only ever raised here, immediately after a
		 * freeze that returned true, and only while the bound allows.
		 */
		if (bsf_stall_ring_freeze(ring, BSF_RING_FREEZE_NO_EXIT,
					  entry->uptime_ms) &&
		    ring->isr_resets < BSF_STALL_RING_MAX_ISR_RESETS) {
			ring->reset_pending = 1u;
		}
	}
	return true;
}

/*
 * Claim an owed reset. Returns true at most BSF_STALL_RING_MAX_ISR_RESETS times
 * per power cycle, and the caller must reset immediately when it does.
 *
 * Why this exists at all. N6 showed the ring is unreachable on a live stalled
 * board: `RING PAGE=n` + read needs the GATT read, which dies at onset + 0.0 s,
 * and `RING STATUS` answers over the control-reply plane, which is the stalled
 * publisher. Both retrieval paths run through the subsystems the stall
 * disables. The retrieval window is only ever AFTER a reboot -- and N6 also
 * showed no path to that reboot existed: the detector did not fire, `RECONNECT`
 * removed the board permanently, and a brownout is a power collapse that takes
 * .noinit with it.
 *
 * `sys_reboot()` goes through NVIC_SystemReset(), which RETAINS .noinit. It is
 * called from the k_timer expiry -- an ISR, which cannot block by construction
 * and therefore still runs when the publisher, the notify worker, BT RX/TX and
 * the workqueue are all wedged. The context that detects the freeze is the one
 * that can act on it.
 */
static inline bool bsf_stall_ring_take_reset(struct bsf_stall_ring *ring)
{
	if (ring->reset_pending == 0u) {
		return false;
	}
	ring->reset_pending = 0u;
	if (ring->isr_resets >= BSF_STALL_RING_MAX_ISR_RESETS) {
		return false;
	}
	if (ring->frozen == 0u) {
		/* Unreachable by construction; refuse anyway rather than reset
		 * without the evidence the reset exists to preserve. */
		return false;
	}
	ring->isr_resets++;
	return true;
}

/*
 * Disconnect retraction, the ring's share of the v37 fix.
 *
 * v36 fired the detector on nine of ten boards during a rollout: the Master
 * vanished, the peripheral did not learn it until the 4000 ms supervision
 * timeout, TX buffers filled and the notify blocked. v37 raised the dwell to
 * 5000 ms -- longer than the supervision timeout, so a vanished central always
 * produces a disconnect first -- and added a 1500 ms window that withdraws a
 * recovery if a disconnect follows the alarm.
 *
 * The two freeze causes inherit different parts of that protection, and they
 * should:
 *
 *   ALARM   - retracted exactly when the alarm is. The caller passes the
 *             detector's own verdict, so the ring and the recovery can never
 *             disagree about whether the event happened.
 *   NO_EXIT - retracted when the disconnect lands within the same 1500 ms of
 *             the latch. A disconnect that prompt means the peer went away,
 *             which is the benign cause v37 was built around. Note the
 *             backstop is already structurally immune to the v36 shape: a
 *             vanished central disconnects at 4.0 s, which clears the
 *             subscriptions, which drops `armed` and resets the counter long
 *             before 6.0 s.
 *   MANUAL  - never. The operator asked for it and a disconnect is not an
 *             argument against that.
 */
static inline bool bsf_stall_ring_retract_disconnect(
	struct bsf_stall_ring *ring, uint32_t now_ms, uint32_t window_ms,
	bool alarm_retracted)
{
	if (ring->frozen == 0u) {
		return false;
	}
	if (ring->freeze_reason == BSF_RING_FREEZE_ALARM) {
		if (!alarm_retracted) {
			return false;
		}
	} else if (ring->freeze_reason == BSF_RING_FREEZE_NO_EXIT) {
		if ((uint32_t)(now_ms - ring->freeze_uptime_ms) > window_ms) {
			return false;
		}
	} else {
		return false;
	}
	bsf_stall_ring_clear(ring);
	return true;
}

/* Map an oldest-first logical index onto the physical slot. */
static inline uint16_t bsf_stall_ring_slot(const struct bsf_stall_ring *ring,
					   uint16_t logical)
{
	uint16_t oldest = ring->count < BSF_STALL_RING_CAPACITY ?
				  0u :
				  ring->head;
	uint32_t slot = (uint32_t)oldest + (uint32_t)logical;

	return (uint16_t)(slot % BSF_STALL_RING_CAPACITY);
}

static inline uint8_t bsf_stall_ring_pages(const struct bsf_stall_ring *ring)
{
	uint16_t pages = (uint16_t)((ring->count + BSF_STALL_RING_PAGE_ENTRIES -
				     1u) /
				    BSF_STALL_RING_PAGE_ENTRIES);

	return (uint8_t)pages;
}

/*
 * Render one page. Pure read: rendering the same page twice on a frozen ring
 * produces byte-identical output, which is what makes retrieval idempotent and
 * restartable -- the host may re-ask for any page, in any order, any number of
 * times, and no state on the board advances.
 *
 * Returns 0, or -1 when `page` is past the end.
 */
static inline int bsf_stall_ring_render_page(const struct bsf_stall_ring *ring,
					     uint8_t page,
					     bsf_stall_ring_page_t *out)
{
	uint8_t pages = bsf_stall_ring_pages(ring);
	uint16_t first;
	uint16_t entries;

	if (page >= pages) {
		return -1;
	}
	first = (uint16_t)(page * BSF_STALL_RING_PAGE_ENTRIES);
	entries = (uint16_t)(ring->count - first);
	if (entries > BSF_STALL_RING_PAGE_ENTRIES) {
		entries = BSF_STALL_RING_PAGE_ENTRIES;
	}

	memset(out, 0, sizeof(*out));
	out->version = BSF_STALL_RING_VERSION;
	out->page = page;
	out->pages = pages;
	out->entries = (uint8_t)entries;
	out->capacity = BSF_STALL_RING_CAPACITY;
	out->count = ring->count;
	out->boot_id = ring->boot_id;
	out->freeze_index = ring->freeze_index;
	out->frozen = ring->frozen;
	out->freeze_reason = ring->freeze_reason;
	out->sample_period_ms = (uint8_t)BSF_STALL_RING_PERIOD_MS;
	out->entry_size = (uint8_t)sizeof(bsf_stall_ring_entry_t);
	if (ring->count != 0u) {
		out->oldest_uptime_ms =
			ring->entries[bsf_stall_ring_slot(ring, 0u)].uptime_ms;
		out->newest_uptime_ms =
			ring->entries[bsf_stall_ring_slot(
					      ring, (uint16_t)(ring->count - 1u))]
				.uptime_ms;
		out->pool_count =
			ring->entries[bsf_stall_ring_slot(ring, 0u)].pool_count;
	}
	for (uint16_t i = 0u; i < entries; ++i) {
		out->entries_data[i] =
			ring->entries[bsf_stall_ring_slot(
				ring, (uint16_t)(first + i))];
	}
	out->page_crc = bsf_stall_ring_crc16(
		(const uint8_t *)out->entries_data, sizeof(out->entries_data));
	return 0;
}

static inline void bsf_stall_ring_view_select(struct bsf_stall_ring_view *view,
					      uint16_t page, uint32_t now_ms)
{
	view->page = page;
	view->selected_uptime_ms = now_ms;
	view->active = 1u;
}

static inline void bsf_stall_ring_view_clear(struct bsf_stall_ring_view *view)
{
	view->active = 0u;
	view->page = 0u;
	view->selected_uptime_ms = 0u;
}

/*
 * True when this read should return a ring page. The expiry is computed here,
 * at read time, so an abandoned retrieval self-heals with no cleanup path to
 * get wrong -- the failure mode this project has now hit three times.
 */
static inline bool bsf_stall_ring_view_page(
	const struct bsf_stall_ring_view *view, uint32_t now_ms,
	uint32_t ttl_ms, uint8_t *page)
{
	if (view->active == 0u) {
		return false;
	}
	if ((uint32_t)(now_ms - view->selected_uptime_ms) > ttl_ms) {
		return false;
	}
	*page = (uint8_t)view->page;
	return true;
}

#endif /* BSF_STALL_RING_POLICY_H */
