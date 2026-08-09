/*
 * Native test for the v45 wedge detector policy (section 12).
 *
 * Every case the brief enumerates is here, plus the ones that killed earlier
 * rounds: counter wrap, epoch replacement, and the second-trigger-in-one-power-
 * cycle rule that makes a boot loop impossible.
 *
 * The detector is a pure function precisely so this file can exist. No kernel,
 * no BLE stack, no board.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "bsf_v45_detector.h"

static int failures;

#define CHECK(cond, name)                                                     \
	do {                                                                  \
		if (!(cond)) {                                                \
			printf("FAIL %s (%s:%d)\n", (name), __FILE__,         \
			       __LINE__);                                     \
			failures++;                                           \
			return 1;                                             \
		}                                                             \
		printf("  ok   %s\n", (name));                                \
	} while (0)

/*
 * A healthy board, ticked at the monitor's 1 s cadence. Everything advances:
 * producers, notify exits, controller completions.
 */
struct sim {
	struct bsf_v45_detector d;
	struct bsf_v45_inputs in;
	uint32_t exits_at_epoch_start;
	/*
	 * R4 added watermark C (notify_ok_total, DELIVERY stage). This harness
	 * modelled only A and B, so notify_ok_total never moved and arm C
	 * fired FREEZE_MS into every single test -- including the healthy one.
	 * Ten checks failed, at the old 20 s dwell and the new 12 s alike: the
	 * detector's own policy test had been red since arm C landed, so arm C
	 * and CAUSE_CONN_RELEASED shipped with no passing host coverage.
	 *
	 * A delivered notify is also an exited notify, so C follows A here --
	 * except where a test is specifically about delivery freezing while the
	 * call keeps returning, which is exactly what arm C is for.
	 */
	bool freeze_notify_ok;
};

static void sim_init(struct sim *s)
{
	memset(s, 0, sizeof(*s));
	s->in.connected = true;
	s->in.data_subscribed = true;
	s->in.epoch = 1;
	s->in.now_ms = 1000;
	s->in.connected_at_ms = 0;
	bsf_v45_detector_reset(&s->d, 1, 0);
}

/* One monitor pass. `alive` selects which watermarks move. */
static struct bsf_v45_decision sim_tick(struct sim *s, bool producer,
					bool notify_exit, bool ncp)
{
	s->in.now_ms += 1000;
	if (producer) {
		s->in.producer_seq += 30;
	}
	if (notify_exit) {
		s->in.notify_exit_total += 25;
		if (!s->freeze_notify_ok) {
			s->in.notify_ok_total += 25;
		}
	}
	if (ncp) {
		s->in.ncp_packet_total += 25;
	}
	s->in.notify_exits_this_epoch =
		s->in.notify_exit_total - s->exits_at_epoch_start;
	return bsf_v45_detector_step(&s->d, &s->in);
}

static void sim_warm(struct sim *s, unsigned seconds)
{
	for (unsigned i = 0; i < seconds; ++i) {
		(void)sim_tick(s, true, true, true);
	}
}

static int test_healthy_never_triggers(void)
{
	struct sim s;
	struct bsf_v45_decision d = {0};

	printf("healthy progress -> no trigger\n");
	sim_init(&s);
	for (unsigned i = 0; i < 600; ++i) {
		d = sim_tick(&s, true, true, true);
		if (d.capture) {
			break;
		}
	}
	CHECK(!d.capture, "ten minutes of healthy traffic never captures");
	CHECK(d.armed, "and it IS armed, so the absence means something");
	return 0;
}

static int test_arming_conditions(void)
{
	struct sim s;
	struct bsf_v45_decision d;

	printf("arming requires all six conditions\n");

	/* Not enough completed notifications yet. */
	sim_init(&s);
	d = sim_tick(&s, true, true, true);
	CHECK(!d.armed, "one notification is not 64");

	/* Enough notifications, but not yet 10 s since connect. */
	sim_init(&s);
	s.in.notify_exit_total = 1000;
	s.in.connected_at_ms = s.in.now_ms;
	d = sim_tick(&s, true, true, true);
	CHECK(!d.armed, "under 10 s since connect stays disarmed");

	/* Unsubscribed. */
	sim_init(&s);
	sim_warm(&s, 30);
	s.in.data_subscribed = false;
	d = sim_tick(&s, true, false, false);
	CHECK(!d.armed, "unsubscribed disarms");

	/* Disconnected. */
	sim_init(&s);
	sim_warm(&s, 30);
	s.in.connected = false;
	d = sim_tick(&s, true, false, false);
	CHECK(!d.armed, "disconnected disarms");

	/* OTA in progress. */
	sim_init(&s);
	sim_warm(&s, 30);
	s.in.ota_active = true;
	d = sim_tick(&s, true, false, false);
	CHECK(!d.armed, "an OTA in progress disarms");
	return 0;
}

static int test_producer_stopped_is_not_a_wedge(void)
{
	struct sim s;
	struct bsf_v45_decision d = {0};

	printf("producer stopped -> no trigger\n");
	sim_init(&s);
	sim_warm(&s, 30);
	/* Everything stops, including the producers: that is not the fault
	 * under study. Invariant 1 of the target phenotype is that UWB and IMU
	 * production CONTINUE. */
	for (unsigned i = 0; i < 60; ++i) {
		d = sim_tick(&s, false, false, false);
		if (d.capture) {
			break;
		}
	}
	CHECK(!d.capture, "a dead producer is never reported as a BT wedge");
	CHECK(!d.armed, "and the detector says so by being disarmed");
	return 0;
}

static int test_notify_exit_arm(void)
{
	struct sim s;
	struct bsf_v45_decision d = {0};
	unsigned t;

	printf("notify_exit frozen for the dwell with producer advancing -> trigger\n");
	sim_init(&s);
	sim_warm(&s, 30);
	for (t = 1; t <= 25; ++t) {
		/* The notify worker is parked in att.c's K_FOREVER. The
		 * controller keeps confirming what was already queued, so NCP
		 * still moves for a while -- exactly the asymmetry the two
		 * watermarks exist to separate. */
		d = sim_tick(&s, true, false, true);
		if (d.capture) {
			break;
		}
	}
	CHECK(d.capture, "captured");
	CHECK(t == (BSF_V45_FREEZE_MS / 1000u),
	      "at exactly the dwell, not one tick early or late");
	CHECK(d.cause == BSF_V45_CAUSE_NOTIFY_EXIT, "attributed to notify_exit");
	CHECK(d.reboot, "first trigger this power cycle reboots");
	CHECK(d.notify_exit_age_ms == BSF_V45_FREEZE_MS,
	      "reported age is the real age");
	return 0;
}

static int test_ncp_arm(void)
{
	struct sim s;
	struct bsf_v45_decision d = {0};
	unsigned t;

	printf("ncp_packet_total frozen for the dwell with notify exits advancing -> trigger\n");
	sim_init(&s);
	sim_warm(&s, 30);
	for (t = 1; t <= 25; ++t) {
		/* Submissions still return -- the app path looks perfectly
		 * healthy -- but nothing is ever confirmed. No counter that
		 * existed before v45 could see this at all. */
		d = sim_tick(&s, true, true, false);
		if (d.capture) {
			break;
		}
	}
	CHECK(d.capture, "captured");
	CHECK(t == (BSF_V45_FREEZE_MS / 1000u), "at exactly the dwell");
	CHECK(d.cause == BSF_V45_CAUSE_NCP_PACKET, "attributed to ncp_packet");
	return 0;
}

static int test_both_frozen_single_capture(void)
{
	struct sim s;
	struct bsf_v45_decision d = {0};
	unsigned captures = 0;

	printf("both frozen -> ONE capture, cause=BOTH\n");
	sim_init(&s);
	sim_warm(&s, 30);
	for (unsigned i = 0; i < 40; ++i) {
		d = sim_tick(&s, true, false, false);
		if (d.capture) {
			captures++;
			if (captures == 1) {
				CHECK(d.cause == BSF_V45_CAUSE_BOTH,
				      "cause is BOTH when both froze together");
				CHECK(d.reboot, "the first one reboots");
			} else {
				CHECK(!d.reboot,
				      "a second trigger in one power cycle "
				      "captures but NEVER reboots");
			}
		}
	}
	CHECK(captures >= 1, "at least one capture");
	return 0;
}

static int test_no_boot_loop(void)
{
	struct sim s;
	struct bsf_v45_decision d;
	unsigned reboots = 0;

	printf("one reset per power cycle, ever\n");
	sim_init(&s);
	sim_warm(&s, 30);
	for (unsigned i = 0; i < 200; ++i) {
		d = sim_tick(&s, true, false, false);
		if (d.reboot) {
			reboots++;
		}
	}
	CHECK(reboots == 1, "exactly one reboot however long it stays wedged");
	return 0;
}

static int test_counter_wrap(void)
{
	struct sim s;
	struct bsf_v45_decision d;

	printf("counter wrap across both watermarks -> no false trigger\n");
	sim_init(&s);
	s.in.notify_exit_total = 0xffffff00u;
	s.in.ncp_packet_total = 0xfffffff0u;
	s.exits_at_epoch_start = 0xffffff00u - 1000u;
	sim_warm(&s, 30);
	CHECK(s.in.notify_exit_total < 0xffffff00u, "notify_exit wrapped");
	CHECK(s.in.ncp_packet_total < 0xfffffff0u, "ncp_packet wrapped");
	/*
	 * A wrapped counter is simply a counter that MOVED, so its dwell resets.
	 * This is why every comparison is `!=` and every age uses unsigned
	 * subtraction, rather than `>` anywhere.
	 */
	for (unsigned i = 0; i < 30; ++i) {
		d = sim_tick(&s, true, true, true);
		CHECK(!d.capture, "wrap is movement, not a freeze");
	}
	return 0;
}

static int test_uptime_wrap(void)
{
	struct sim s;
	struct bsf_v45_decision d = {0};
	unsigned t;

	printf("k_uptime_get_32() wrap during a real wedge still triggers\n");
	sim_init(&s);
	sim_warm(&s, 30);
	/*
	 * Park now_ms just under 2^32 so the dwell straddles the wrap.
	 *
	 * connected_at_ms must move with it. Leaving it at 0 would model a link
	 * that has been up for 49.7 days, and the arm test -- correctly -- reads
	 * that as "connected 984 ms ago" the instant the clock wraps. Writing
	 * this test the lazy way produced exactly that, and it looked like a
	 * detector bug for a minute. Every real connection is orders of
	 * magnitude shorter than the wrap period, so unsigned subtraction is
	 * right and the scenario below is the one that can actually happen.
	 */
	s.in.now_ms = 0xfffffff0u - 10000u;
	s.in.connected_at_ms = s.in.now_ms - 60000u;
	s.d.notify_exit_moved_ms = s.in.now_ms;
	s.d.ncp_packet_moved_ms = s.in.now_ms;
	s.d.producer_moved_ms = s.in.now_ms;
	/* R4's arm C has its own clock, and it must be parked with the rest.
	 * Left behind, it reads as 49 days stale the moment now_ms jumps and
	 * arm C fires on tick 1 -- which is a bug in the scenario, not in the
	 * detector. Every "last moved" field the detector owns belongs here. */
	s.d.notify_ok_moved_ms = s.in.now_ms;
	for (t = 1; t <= 30; ++t) {
		d = sim_tick(&s, true, false, true);
		if (d.capture) {
			break;
		}
	}
	CHECK(d.capture, "the wedge is still detected across a uptime wrap");
	CHECK(t == (BSF_V45_FREEZE_MS / 1000u), "and still at the dwell, not 49 days later");
	return 0;
}

static int test_epoch_replacement(void)
{
	struct sim s;
	struct bsf_v45_decision d;

	printf("epoch replacement clears all dwell\n");
	sim_init(&s);
	sim_warm(&s, 30);
	for (unsigned i = 0; i < (BSF_V45_FREEZE_MS / 1000u) - 1u; ++i) {
		d = sim_tick(&s, true, false, false);
		CHECK(!d.capture, "one tick short of the dwell is not a trigger");
	}
	/* A reconnect. Everything the detector had accumulated is void. */
	s.in.epoch = 2;
	s.in.connected_at_ms = s.in.now_ms;
	s.exits_at_epoch_start = s.in.notify_exit_total;
	for (unsigned i = 0; i < 10; ++i) {
		d = sim_tick(&s, true, false, false);
		CHECK(!d.capture,
		      "the pre-reconnect dwell does not carry into the new epoch");
	}
	return 0;
}

static int test_normal_disconnect(void)
{
	struct sim s;
	struct bsf_v45_decision d;

	printf("normal disconnect -> no trigger\n");
	sim_init(&s);
	sim_warm(&s, 30);
	for (unsigned i = 0; i < 15; ++i) {
		(void)sim_tick(&s, true, false, false);
	}
	s.in.connected = false;
	s.in.epoch = 2;
	for (unsigned i = 0; i < 60; ++i) {
		d = sim_tick(&s, true, false, false);
		CHECK(!d.capture, "a disconnected board never captures");
	}
	return 0;
}

static int test_suspicion_mark(void)
{
	struct sim s;
	struct bsf_v45_decision d;

	printf("suspicion mark lands at the first frozen pass, not at the dwell\n");
	sim_init(&s);
	sim_warm(&s, 30);
	d = sim_tick(&s, true, false, false);
	CHECK(d.mark_suspect, "marked on the very first frozen pass");
	CHECK(!d.capture, "and it freezes nothing");
	d = sim_tick(&s, true, false, false);
	CHECK(!d.mark_suspect, "marked once, not every pass");
	/* Recovery drops the mark, so the next anomaly gets its own. */
	(void)sim_tick(&s, true, true, true);
	s.in.data_subscribed = false;
	(void)sim_tick(&s, true, true, true);
	s.in.data_subscribed = true;
	sim_warm(&s, 15);
	d = sim_tick(&s, true, false, false);
	CHECK(d.mark_suspect, "a fresh anomaly gets a fresh mark");
	return 0;
}

static int test_forced(void)
{
	struct sim s;
	struct bsf_v45_decision d;

	printf("CORPSE FORCE bypasses arming, on purpose\n");
	sim_init(&s);
	s.in.forced = true;
	d = sim_tick(&s, true, true, true);
	CHECK(d.capture, "forced capture on a perfectly healthy board");
	CHECK(d.cause == BSF_V45_CAUSE_FORCED, "labelled FORCED, never as a wedge");
	CHECK(d.reboot, "and it exercises the reboot leg too");
	return 0;
}

static int test_jitter(void)
{
	uint32_t seen[10];
	unsigned distinct = 0;

	printf("reboot jitter is deterministic and inside the window\n");
	for (unsigned i = 0; i < 10; ++i) {
		uint32_t node = 0xB100u + i;
		uint32_t j = bsf_v45_reboot_jitter_ms(node);

		CHECK(j <= BSF_V45_REBOOT_JITTER_MS, "inside 0..4000 ms");
		CHECK(j == bsf_v45_reboot_jitter_ms(node),
		      "same node, same jitter, every time");
		seen[i] = j;
	}
	for (unsigned i = 0; i < 10; ++i) {
		bool dup = false;

		for (unsigned k = 0; k < i; ++k) {
			if (seen[k] == seen[i]) {
				dup = true;
			}
		}
		if (!dup) {
			distinct++;
		}
	}
	/*
	 * Ten nodes triggering on a shared cause must not all re-advertise into
	 * the master's single scan slot at once. Perfect distinctness is not
	 * required, but near-collapse would defeat the point.
	 */
	CHECK(distinct >= 8, "ten nodes spread across at least 8 distinct delays");
	return 0;
}

/*
 * R4's watermark C, which until now had no test at all -- the harness that
 * would have exercised it was the same one whose omission of notify_ok_total
 * kept this whole file red.
 *
 * The scenario is the 2026-08-09 wedge exactly: bt_gatt_notify() keeps
 * RETURNING (arm A never fires, notify_exit_total advanced ~32/s through the
 * whole event) and the controller keeps confirming, but nothing is DELIVERED.
 * Only a delivery-stage watermark can see it.
 */
static int test_notify_ok_arm(void)
{
	struct sim s;
	struct bsf_v45_decision d = {0};
	unsigned t;

	printf("delivery frozen while the call still returns -> arm C\n");
	sim_init(&s);
	sim_warm(&s, 30);
	s.freeze_notify_ok = true;
	for (t = 1; t <= 60; ++t) {
		d = sim_tick(&s, true, true, true);
		if (d.capture) {
			break;
		}
	}
	CHECK(d.capture, "a fast-failing sink is caught even though A and B move");
	CHECK(t == (BSF_V45_FREEZE_MS / 1000u), "at the dwell");
	CHECK(d.cause == BSF_V45_CAUSE_NOTIFY_OK,
	      "and it is named NOTIFY_OK, not BOTH -- A and B never froze");
	return 0;
}

/*
 * The dwell is policy, and policy changes on purpose or not at all.
 *
 * Every timing check above is written against BSF_V45_FREEZE_MS so the suite
 * survives a deliberate change. That is the right structure and it has one
 * hole: a suite that derives everything from the constant can no longer notice
 * the constant moving. This pins it, so a change is an edit to this line with
 * the reasoning next to it, never a silent drift.
 */
static int test_dwell_is_pinned(void)
{
	printf("the dwell is what we think it is\n");
	CHECK(BSF_V45_FREEZE_MS == 12000u,
	      "dwell is 12 s: 2.9x the 4.1 s worst healthy notify, and inside "
	      "the 30 s watchdog that ate the 2026-08-09 corpse at 20 s");
	CHECK(BSF_V45_FREEZE_MS < 25600u,
	      "and under the 25.6 s ring span, so onset context still survives");
	return 0;
}

int main(void)
{
	int rc = 0;

	printf("B306 v45 wedge detector policy\n");
	rc |= test_healthy_never_triggers();
	rc |= test_arming_conditions();
	rc |= test_producer_stopped_is_not_a_wedge();
	rc |= test_notify_exit_arm();
	rc |= test_ncp_arm();
	rc |= test_notify_ok_arm();
	rc |= test_both_frozen_single_capture();
	rc |= test_no_boot_loop();
	rc |= test_counter_wrap();
	rc |= test_uptime_wrap();
	rc |= test_epoch_replacement();
	rc |= test_normal_disconnect();
	rc |= test_suspicion_mark();
	rc |= test_forced();
	rc |= test_jitter();
	rc |= test_dwell_is_pinned();
	printf("v45 detector policy: %s\n", (rc || failures) ? "FAIL" : "PASS");
	return (rc || failures) ? 1 : 0;
}
