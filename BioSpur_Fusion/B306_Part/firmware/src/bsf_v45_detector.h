/*
 * bsf_v45_detector.h -- the v45 wedge detector, as a pure policy function.
 *
 * No kernel calls, no globals, no I/O. The caller reads the atomics and the
 * clock and hands them in; the function returns a decision. That is what makes
 * it testable on the host, and section 12 tests it there.
 *
 * WHAT IT WATCHES, AND WHY IT IS TWO WATERMARKS AND NOT ONE
 * --------------------------------------------------------
 * Every counter the fleet has ever exported is SUBMISSION-stage or
 * CALL-RETURN-stage (COUNTER_SEMANTICS.md, in bold: "There is no
 * COMPLETION-stage counter anywhere in this system"). v45 adds two
 * exit-stage/completion-stage watermarks and triggers if EITHER freezes:
 *
 *   notify_exit_total  -- the notify worker RETURNED from bt_gatt_notify().
 *                         Catches every mode where the worker ends up blocked:
 *                         any break in the refill drains the 8-buffer att_pool
 *                         in ~0.26 s and parks the call in att.c:748 forever.
 *
 *   ncp_packet_total   -- the controller CONFIRMED a packet, counted inside
 *                         hci_num_completed_packets(). Catches
 *                         submissions-that-never-complete without touching the
 *                         application path at all -- which matters, because
 *                         switching bt_gatt_notify() to the _cb variant would
 *                         change conn-TX accounting on the exact path under
 *                         suspicion, and is forbidden.
 *
 * Neither watermark is a submission counter, and neither gates on the master.
 *
 * WHY 12 s UNIFORM, WITH NO 5 s FAST ARM
 * --------------------------------------
 * Healthy notify calls of 100-400 ms happen routinely and 4.1 s was observed
 * during DFU. A false capture costs a reboot, drops the node for ~21 s and
 * contaminates the rate statistics that CROSS_RUN_NECESSITY.md needs. The
 * observed wedges last 615 s to 19 669 s and the entire near-miss population at
 * a 2 s floor is 22 events, all inside battery-depletion cascades -- so 12 s
 * sits in an empty region with two orders of magnitude of margin on the short
 * side. Onset context is not lost by waiting: the 512-entry ring spans 25.6 s,
 * so a capture at onset+12 s still contains the onset.
 *
 * The value was 20 s until 2026-08-09. It came down because the watchdog, not
 * the wedge, was eating the corpse -- see BSF_V45_FREEZE_MS below.
 */
#ifndef BSF_V45_DETECTOR_H
#define BSF_V45_DETECTOR_H

#include <stdbool.h>
#include <stdint.h>

#define BSF_V45_ARM_NOTIFY_EXITS     64u     /* completed notifications      */
#define BSF_V45_ARM_CONNECT_MS       10000u  /* since connect                */
#define BSF_V45_PRODUCER_WINDOW_MS   5000u   /* "producer advancing" horizon */
/*
 * The one threshold. 20000 -> 12000 on 2026-08-09, and the reason is the
 * watchdog, not the detector.
 *
 * WHAT FORCED IT. A `V45 HANG` re-run reset BSF6C53 with
 * `reset_reason=0x00000002` -- RESETREAS.DOG. The watchdog is fed from the
 * system workqueue (`watchdog_feed_count`, "system-workqueue heartbeat"), so
 * any wedge that stalls that queue stops the feed, and WATCHDOG_TIMEOUT_MS =
 * 30000 then resets the board. The detector tick is `k_work_reschedule()` on
 * the same queue. At a 20 s dwell the capture had 10 s of headroom against a
 * timer that had already been running since the last feed -- and lost. The
 * board came back with `V45 present=0`, which reads exactly like "the detector
 * never fired".
 *
 * WHY 12000 IS SAFE. The longest healthy notify call measured is 4.1 s, during
 * DFU. 12 s is a ~2.9x margin over that, and the 512-entry ring spans 25.6 s,
 * so a capture at onset+12 s still carries the whole pre-onset trajectory --
 * the property the 20 s value was chosen to protect is untouched.
 *
 * This buys capture time against the watchdog. It does NOT make the detector
 * cover a syswq death; nothing here can, because the detector rides that queue.
 * See BSF_V45_DOG_* in bsf_v45.h for what covers it instead.
 */
#define BSF_V45_FREEZE_MS            12000u  /* the one threshold            */
/*
 * Consecutive -ENOTCONN from bt_gatt_notify() with the application still
 * believing it is connected, before CAUSE_CONN_RELEASED fires.
 *
 * CHOOSING N. A legitimate disconnect clears the flag from the `disconnected`
 * callback within milliseconds, so an honest race is a handful of calls. The
 * measured notify rate on a loaded node is ~32/s, so 320 is ~10 s -- two orders
 * of magnitude past any race, and still faster than the 12 s dwell arms.
 * The 2026-08-09 wedge produced 19412 consecutive failures: a 60x margin.
 */
#define BSF_V45_NOTCONN_STREAK       320u
#define BSF_V45_REBOOT_JITTER_MS     4000u   /* deterministic, node-derived  */

/* decision.cause */
#define BSF_V45_CAUSE_NONE           0u
#define BSF_V45_CAUSE_NOTIFY_EXIT    1u
#define BSF_V45_CAUSE_NCP_PACKET     2u
#define BSF_V45_CAUSE_BOTH           3u
#define BSF_V45_CAUSE_FORCED         4u      /* CORPSE FORCE, validation only */
/*
 * R4. Both added because the 2026-08-09 wedge would have been missed entirely by
 * cause 1 and caught only by cause 2, twenty seconds late.
 *
 * NOTIFY_OK -- publisher advancing while notify_ok is frozen. Watches DELIVERY
 * instead of return-from-call, so it catches the whole fast-failing-sink class
 * whatever the errno. notify_exit_total kept advancing at ~32/s through that
 * wedge, so arm A could never have fired at any dwell.
 *
 * CONN_RELEASED -- the node contradicting itself: the application believes it is
 * connected while the host stack has no connection. No dwell, because this is
 * not a slow symptom, it is an inconsistency.
 */
#define BSF_V45_CAUSE_NOTIFY_OK      5u
#define BSF_V45_CAUSE_CONN_RELEASED  6u

struct bsf_v45_inputs {
	bool     connected;
	bool     data_subscribed;
	bool     ota_active;          /* OTA or MCUboot confirmation in flight */
	uint32_t epoch;               /* connection incarnation counter        */
	uint32_t now_ms;
	uint32_t connected_at_ms;

	uint32_t producer_seq;        /* SUBMISSION-stage, only used for "alive" */
	uint32_t notify_exit_total;   /* watermark A -- exit stage             */
	uint32_t ncp_packet_total;    /* watermark B -- completion stage       */
	uint32_t notify_ok_total;     /* watermark C -- DELIVERY stage (R4)    */
	uint32_t notconn_streak;      /* consecutive -ENOTCONN, app connected  */
	uint32_t notify_exits_this_epoch;

	bool     forced;              /* CORPSE FORCE latch                    */
};

struct bsf_v45_detector {
	uint32_t epoch;
	uint32_t last_producer_seq;
	uint32_t producer_moved_ms;   /* last time producer_seq changed        */

	uint32_t last_notify_exit;
	uint32_t notify_exit_moved_ms;
	uint32_t last_ncp_packet;
	uint32_t ncp_packet_moved_ms;
	uint32_t last_notify_ok;
	uint32_t notify_ok_moved_ms;

	/* suspicion mark -- set at the FIRST pass that sees either frozen */
	uint32_t suspect_start_ms;
	uint32_t suspect_ring_index;
	bool     suspect_marked;

	bool     armed;
	bool     seeded;              /* watermarks have a baseline            */
	uint32_t trigger_count;       /* this power cycle                      */
};

struct bsf_v45_decision {
	uint8_t  cause;
	bool     capture;
	bool     reboot;              /* only if the budget is still available */
	bool     mark_suspect;
	uint32_t notify_exit_age_ms;
	uint32_t ncp_packet_age_ms;
	bool     armed;
};

/*
 * Clears every dwell. Called on connect, disconnect, unsubscribe and OTA start.
 * A cleared detector cannot trigger until it re-arms from scratch, which is what
 * makes "normal disconnect", "unsubscribe" and "OTA" no-trigger paths by
 * construction rather than by a special case in the trigger test.
 */
static inline void bsf_v45_detector_reset(struct bsf_v45_detector *d,
					  uint32_t epoch, uint32_t now_ms)
{
	uint32_t triggers = d->trigger_count;

	*d = (struct bsf_v45_detector){0};
	d->epoch = epoch;
	d->producer_moved_ms = now_ms;
	d->notify_exit_moved_ms = now_ms;
	d->ncp_packet_moved_ms = now_ms;
	d->notify_ok_moved_ms = now_ms;
	/* The reboot budget is per POWER CYCLE, not per connection. */
	d->trigger_count = triggers;
}

/*
 * Unsigned difference. Every counter here is a free-running uint32 and every
 * timestamp is k_uptime_get_32(); wrap-around is normal, not exceptional. Doing
 * this in one place is what makes the "counter wrap across both watermarks ->
 * no false trigger" test pass: a wrapped counter is simply a counter that
 * MOVED, so its dwell resets.
 */
static inline uint32_t bsf_v45_delta(uint32_t now, uint32_t then)
{
	return now - then;
}

static inline struct bsf_v45_decision
bsf_v45_detector_step(struct bsf_v45_detector *d,
		      const struct bsf_v45_inputs *in)
{
	struct bsf_v45_decision out = {0};
	bool producer_alive;
	bool arm_conditions;

	/* A new connection incarnation replaces all dwell state. */
	if (in->epoch != d->epoch) {
		bsf_v45_detector_reset(d, in->epoch, in->now_ms);
	}

	/* --- watermark movement, always tracked, even while disarmed ----- */
	if (!d->seeded) {
		d->last_producer_seq = in->producer_seq;
		d->last_notify_exit = in->notify_exit_total;
		d->last_ncp_packet = in->ncp_packet_total;
		d->last_notify_ok = in->notify_ok_total;
		d->producer_moved_ms = in->now_ms;
		d->notify_exit_moved_ms = in->now_ms;
		d->ncp_packet_moved_ms = in->now_ms;
		d->notify_ok_moved_ms = in->now_ms;
		d->seeded = true;
	}
	if (in->producer_seq != d->last_producer_seq) {
		d->last_producer_seq = in->producer_seq;
		d->producer_moved_ms = in->now_ms;
	}
	if (in->notify_exit_total != d->last_notify_exit) {
		d->last_notify_exit = in->notify_exit_total;
		d->notify_exit_moved_ms = in->now_ms;
	}
	if (in->ncp_packet_total != d->last_ncp_packet) {
		d->last_ncp_packet = in->ncp_packet_total;
		d->ncp_packet_moved_ms = in->now_ms;
	}
	if (in->notify_ok_total != d->last_notify_ok) {
		d->last_notify_ok = in->notify_ok_total;
		d->notify_ok_moved_ms = in->now_ms;
	}

	out.notify_exit_age_ms = bsf_v45_delta(in->now_ms, d->notify_exit_moved_ms);
	out.ncp_packet_age_ms = bsf_v45_delta(in->now_ms, d->ncp_packet_moved_ms);

	producer_alive = bsf_v45_delta(in->now_ms, d->producer_moved_ms) <
			 BSF_V45_PRODUCER_WINDOW_MS;

	arm_conditions = in->connected && in->data_subscribed && !in->ota_active &&
			 in->notify_exits_this_epoch >= BSF_V45_ARM_NOTIFY_EXITS &&
			 bsf_v45_delta(in->now_ms, in->connected_at_ms) >=
				 BSF_V45_ARM_CONNECT_MS &&
			 producer_alive;

	d->armed = arm_conditions;
	out.armed = arm_conditions;

	/*
	 * CORPSE FORCE is the pipeline-validation path (section 12.1). It
	 * bypasses arming on purpose -- it exists to prove capture -> reboot ->
	 * persist -> collect -> ACK end to end on a healthy board.
	 */
	if (in->forced) {
		out.cause = BSF_V45_CAUSE_FORCED;
		out.capture = true;
		out.reboot = (d->trigger_count == 0u);
		d->trigger_count++;
		d->suspect_marked = false;
		return out;
	}

	if (!arm_conditions) {
		/* Disarmed: drop the suspicion mark, never trigger. */
		d->suspect_marked = false;
		return out;
	}

	/* --- suspicion mark: first pass that sees either frozen ---------- */
	if (!d->suspect_marked &&
	    (out.notify_exit_age_ms > 0u || out.ncp_packet_age_ms > 0u)) {
		/*
		 * "Frozen" at this stage means only "did not move since the
		 * previous pass", i.e. ~2-3 s at the monitor's cadence. It
		 * freezes nothing and costs nothing; it just remembers where in
		 * the ring the anomaly began, so the corpse can point at the
		 * onset instead of at the trigger.
		 */
		d->suspect_marked = true;
		d->suspect_start_ms = in->now_ms;
		out.mark_suspect = true;
	}

	/*
	 * --- R4/A3: the self-contradiction, no dwell ----------------------
	 *
	 * The application says connected; the host stack returns -ENOTCONN for
	 * every notify. That is not a symptom that needs time to become
	 * convincing, it is two parts of the same node disagreeing, so it fires
	 * on the streak alone and names the fault directly instead of waiting
	 * 12 s for a watermark to age out. It does not replace arm B; both stay.
	 */
	if (in->notconn_streak >= BSF_V45_NOTCONN_STREAK) {
		out.cause = BSF_V45_CAUSE_CONN_RELEASED;
		out.capture = true;
		out.reboot = (d->trigger_count == 0u);
		d->trigger_count++;
		return out;
	}

	/* --- the one primary trigger ------------------------------------- */
	{
		bool a = out.notify_exit_age_ms >= BSF_V45_FREEZE_MS;
		bool b = out.ncp_packet_age_ms >= BSF_V45_FREEZE_MS;
		/*
		 * R4/A4, watermark C. Delivery frozen while the producer is
		 * still advancing -- the arm that would have caught 2026-08-09
		 * on its own merits rather than by luck of the ncp arm.
		 */
		bool c = bsf_v45_delta(in->now_ms, d->notify_ok_moved_ms) >=
			 BSF_V45_FREEZE_MS;

		if (!a && !b && !c) {
			return out;
		}
		out.cause = a && b ? BSF_V45_CAUSE_BOTH :
			    a ? BSF_V45_CAUSE_NOTIFY_EXIT :
			    b ? BSF_V45_CAUSE_NCP_PACKET :
				BSF_V45_CAUSE_NOTIFY_OK;
		out.capture = true;
		/*
		 * ONE reset per physical power cycle. A second trigger captures
		 * to the other slot and stays up wedged -- deliberately, so a
		 * board can never boot-loop and so the second corpse survives
		 * for an SWD or post-power-cycle read.
		 */
		out.reboot = (d->trigger_count == 0u);
		d->trigger_count++;
	}
	return out;
}

/*
 * Deterministic 0..BSF_V45_REBOOT_JITTER_MS spread, derived from node identity
 * alone. Ten nodes triggering on a shared cause must not all re-advertise in the
 * same 100 ms window and collide on the master's single scan slot. Deterministic
 * rather than random so a run is reproducible and so nothing calls into the
 * entropy driver from a capture path.
 */
static inline uint32_t bsf_v45_reboot_jitter_ms(uint32_t node_identity)
{
	uint32_t h = node_identity * 2654435761u;   /* Knuth multiplicative */

	return (h >> 16) % (BSF_V45_REBOOT_JITTER_MS + 1u);
}

#endif /* BSF_V45_DETECTOR_H */
