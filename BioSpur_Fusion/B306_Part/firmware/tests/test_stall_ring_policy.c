/*
 * Native test for the B306 stall trajectory ring (b306-imu-relay-v40).
 *
 * Covers the four properties E1 asks for offline:
 *   1. the ring keeps sampling with the outbound path stubbed dead
 *   2. it survives a simulated soft reset with its contents intact
 *   3. retrieval is idempotent and restartable under an interrupted read
 *   4. the wire layout is exactly what the host expects, and nothing that the
 *      1 Hz kind-8 record depends on has moved
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "stall_ring_policy.h"

#define CHECK(cond, name)                                                     \
	do {                                                                  \
		if (!(cond)) {                                                \
			printf("FAIL %s (%s:%d)\n", (name), __FILE__,         \
			       __LINE__);                                     \
			return 1;                                             \
		}                                                             \
		printf("  ok   %s\n", (name));                                \
	} while (0)

/*
 * A crude model of the board. `outbound_alive` is the only switch: when it is
 * false the publisher never completes a notify, so exit_count freezes while
 * the producers keep enqueuing -- which is exactly the N5 stall.
 */
struct board {
	uint32_t uptime_ms;
	uint32_t heartbeat;
	uint32_t entry;
	uint32_t exit;
	uint8_t depth_imu;
	bool outbound_alive;
	bool subscribed;
};

static bsf_stall_ring_entry_t tick(struct board *b)
{
	bsf_stall_ring_entry_t e;

	b->uptime_ms += BSF_STALL_RING_PERIOD_MS;
	/* 28.33 producer records/s -> ~1.42 per 50 ms; integer-step it. */
	b->heartbeat += 1u + (b->uptime_ms / BSF_STALL_RING_PERIOD_MS) % 2u;
	if (b->outbound_alive) {
		b->entry += 2u;
		b->exit += 2u;
		b->depth_imu = 0u;
	} else {
		/* Queues fill, publisher never returns a completion. */
		b->entry += (b->entry == b->exit) ? 1u : 0u;
		if (b->depth_imu < 64u) {
			b->depth_imu++;
		}
	}

	memset(&e, 0, sizeof(e));
	e.uptime_ms = b->uptime_ms;
	e.producer_heartbeat = b->heartbeat;
	e.entry_count = b->entry;
	e.exit_count = b->exit;
	e.queue_depth_imu = b->depth_imu;
	e.queue_depth_ctl = b->outbound_alive ? 0u : 4u;
	e.pool_count = 8u;
	for (int i = 0; i < (int)BSF_STALL_RING_POOL_SLOTS; ++i) {
		e.pool_avail[i] = b->outbound_alive ? 8u : 0u;
	}
	e.subscribed_notify_ok = b->subscribed ? 4096u : 0u;
	e.detector_frozen_ms = b->outbound_alive ? 0u : 1000u;
	e.flags = BSF_RING_FLAG_CONNECTED |
		  (b->subscribed ? (BSF_RING_FLAG_DATA_SUB |
				    BSF_RING_FLAG_TELEMETRY_SUB) : 0u);
	return e;
}

static void run(struct bsf_stall_ring *ring, struct board *b, unsigned n)
{
	for (unsigned i = 0; i < n; ++i) {
		bsf_stall_ring_entry_t e = tick(b);

		(void)bsf_stall_ring_push(ring, &e, b->subscribed);
	}
}

/* Storage that survives our simulated reset, like .noinit does. */
static struct bsf_stall_ring retained_ring;

static int test_geometry(void)
{
	printf("geometry and wire layout\n");
	CHECK(sizeof(bsf_stall_ring_entry_t) == 40u, "entry is 40 bytes");
	CHECK(sizeof(bsf_stall_ring_page_t) == 232u, "page is 232 bytes");
	CHECK(sizeof(bsf_stall_ring_page_t) == sizeof(bsf_stall_status_t),
	      "both stall wire forms read back at the same length");
	CHECK(sizeof(bsf_ble_pool_usage_t) == 140u,
	      "kind-8 pool record is still 140 bytes");
	CHECK(sizeof(bsf_ble_telemetry_t) == 243u,
	      "kind-2 telemetry record is unchanged");
	CHECK(sizeof(bsf_ble_queue_counters_t) == 58u,
	      "kind-5 queue-counter record is unchanged");
	CHECK(BSF_STALL_RING_CAPACITY % BSF_STALL_RING_PAGE_ENTRIES == 0u,
	      "capacity divides evenly into pages -- no partial last page");
	CHECK(BSF_STALL_RING_PAGES == 40u, "40 pages");
	CHECK(BSF_STALL_RING_SPAN_MS == 10000u, "span is 10.0 s");
	CHECK(BSF_STALL_RING_SPAN_MS >= 2u * 5000u,
	      "span covers the 5000 ms detector dwell on both sides of onset");
	CHECK(sizeof(struct bsf_stall_ring) < 8300u,
	      "retained footprint stays near 8 KiB");
	return 0;
}

static int test_samples_while_outbound_dead(void)
{
	struct bsf_stall_ring ring;
	struct board b = { .outbound_alive = true, .subscribed = true };
	uint32_t writes_before;
	uint16_t count_before;

	printf("keeps sampling with the outbound path stubbed dead\n");
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);

	run(&ring, &b, 300u); /* 15 s healthy: wraps the ring twice over */
	CHECK(ring.count == BSF_STALL_RING_CAPACITY, "ring filled");
	CHECK(ring.frozen == 0u, "healthy traffic never latches");
	CHECK(ring.no_exit_samples == 0u, "no-exit counter stays clear");

	writes_before = ring.writes_total;
	count_before = ring.count;
	b.outbound_alive = false;

	/* One sample short of the latch: still sampling, still not frozen. */
	run(&ring, &b, BSF_STALL_RING_NO_EXIT_SAMPLES - 1u);
	CHECK(ring.frozen == 0u, "latch has not fired one sample early");
	CHECK(ring.writes_total ==
		      writes_before + BSF_STALL_RING_NO_EXIT_SAMPLES - 1u,
	      "every sample during the dead window was stored");
	CHECK(ring.count == count_before, "count saturates, writes keep going");

	run(&ring, &b, 1u);
	CHECK(ring.frozen == 1u, "latched at 120 samples");
	CHECK(ring.freeze_reason == BSF_RING_FREEZE_NO_EXIT,
	      "latched for the right reason");

	/* Frozen means frozen: further samples must not overwrite evidence. */
	writes_before = ring.writes_total;
	run(&ring, &b, 500u);
	CHECK(ring.writes_total == writes_before,
	      "a frozen ring accepts no further writes");
	return 0;
}

static int test_latch_needs_subscription(void)
{
	struct bsf_stall_ring ring;
	struct board b = { .outbound_alive = false, .subscribed = false };

	printf("unsubscribed boards do not latch\n");
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 400u);
	CHECK(ring.frozen == 0u,
	      "no exits while unsubscribed is normal, not a stall");
	CHECK(ring.writes_total == 400u, "still sampling throughout");
	return 0;
}

static int test_alarm_freeze_centres_the_onset(void)
{
	struct bsf_stall_ring ring;
	struct board b = { .outbound_alive = true, .subscribed = true };
	uint32_t onset_ms;
	bsf_stall_ring_page_t page;
	uint32_t oldest, newest;

	printf("detector freeze at onset + 5000 ms centres the transition\n");
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 400u); /* 20 s of healthy run-in */
	b.outbound_alive = false;
	onset_ms = b.uptime_ms;
	run(&ring, &b, 100u); /* 5.0 s: the detector's dwell */
	CHECK(bsf_stall_ring_freeze(&ring, BSF_RING_FREEZE_ALARM, b.uptime_ms),
	      "alarm latches the ring");
	CHECK(ring.freeze_reason == BSF_RING_FREEZE_ALARM,
	      "alarm beat the 6.0 s no-exit backstop");

	CHECK(bsf_stall_ring_render_page(&ring, 0u, &page) == 0, "page 0 renders");
	oldest = page.oldest_uptime_ms;
	newest = page.newest_uptime_ms;
	CHECK(oldest < onset_ms, "the ring reaches back before onset");
	CHECK(onset_ms - oldest >= 4500u,
	      "at least 4.5 s of run-in survives the freeze");
	CHECK(newest >= onset_ms + 4900u,
	      "at least 4.9 s past onset survives the freeze");

	/* A second cause must not relabel the first. */
	CHECK(!bsf_stall_ring_freeze(&ring, BSF_RING_FREEZE_MANUAL,
				     b.uptime_ms + 1000u),
	      "first freeze wins");
	CHECK(ring.freeze_reason == BSF_RING_FREEZE_ALARM, "reason unchanged");
	return 0;
}

static int test_survives_soft_reset(void)
{
	struct board b = { .outbound_alive = true, .subscribed = true };
	bsf_stall_ring_page_t before[BSF_STALL_RING_PAGES];
	bsf_stall_ring_page_t after;
	uint32_t boot_id_before;

	printf("survives a simulated soft reset with contents intact\n");
	memset(&retained_ring, 0xa5, sizeof(retained_ring)); /* cold RAM junk */
	CHECK(bsf_stall_ring_boot(&retained_ring) == BSF_RING_BOOT_COLD,
	      "garbage RAM is rejected on the first boot");
	CHECK(retained_ring.boot_id == 1u, "boot_id starts at 1");

	run(&retained_ring, &b, 260u);
	b.outbound_alive = false;
	run(&retained_ring, &b, 100u);
	(void)bsf_stall_ring_freeze(&retained_ring, BSF_RING_FREEZE_ALARM,
				    b.uptime_ms);
	for (uint8_t p = 0; p < BSF_STALL_RING_PAGES; ++p) {
		CHECK(bsf_stall_ring_render_page(&retained_ring, p,
						 &before[p]) == 0,
		      "pre-reset page renders");
	}
	boot_id_before = retained_ring.boot_id;

	/* The reset: .noinit is not touched, the C runtime does not clear it. */
	CHECK(bsf_stall_ring_boot(&retained_ring) == BSF_RING_BOOT_RETAINED,
	      "a populated ring is reported as retained");
	CHECK(retained_ring.boot_id == boot_id_before + 1u,
	      "boot_id increments across the reset");
	CHECK(retained_ring.frozen == 1u, "still frozen after the reset");
	CHECK(retained_ring.freeze_reason == BSF_RING_FREEZE_ALARM,
	      "freeze cause survives");

	for (uint8_t p = 0; p < BSF_STALL_RING_PAGES; ++p) {
		CHECK(bsf_stall_ring_render_page(&retained_ring, p, &after) == 0,
		      "post-reset page renders");
		CHECK(after.page_crc == before[p].page_crc,
		      "page payload is bit-identical across the reset");
		CHECK(memcmp(after.entries_data, before[p].entries_data,
			     sizeof(after.entries_data)) == 0,
		      "entry bytes are unchanged across the reset");
	}

	/* And a post-reset sample must not overwrite the evidence. */
	run(&retained_ring, &b, 50u);
	CHECK(bsf_stall_ring_render_page(&retained_ring, 0u, &after) == 0,
	      "page 0 still renders");
	CHECK(after.page_crc == before[0].page_crc,
	      "post-reset sampling did not touch the frozen ring");
	return 0;
}

static int test_retrieval_idempotent_and_restartable(void)
{
	struct bsf_stall_ring_view view;
	bsf_stall_ring_page_t a, b_page;
	uint8_t selected = 0u;

	printf("retrieval is idempotent, restartable and self-reverting\n");
	CHECK(bsf_stall_ring_render_page(&retained_ring, 7u, &a) == 0,
	      "page 7 renders");
	CHECK(bsf_stall_ring_render_page(&retained_ring, 7u, &b_page) == 0,
	      "page 7 renders again");
	CHECK(memcmp(&a, &b_page, sizeof(a)) == 0,
	      "re-reading a page is byte-identical");

	/* Out-of-range never wedges; it is simply refused. */
	CHECK(bsf_stall_ring_render_page(&retained_ring,
					 BSF_STALL_RING_PAGES, &a) == -1,
	      "a page past the end is refused, not clamped");

	memset(&view, 0, sizeof(view));
	CHECK(!bsf_stall_ring_view_page(&view, 1000u,
					BSF_STALL_RING_VIEW_TTL_MS, &selected),
	      "no selection means the status snapshot");

	bsf_stall_ring_view_select(&view, 12u, 1000u);
	CHECK(bsf_stall_ring_view_page(&view, 1000u,
				       BSF_STALL_RING_VIEW_TTL_MS, &selected),
	      "selection is live immediately");
	CHECK(selected == 12u, "the selected page is served");

	/* Interrupted retrieval: the host vanishes mid-sequence. */
	CHECK(bsf_stall_ring_view_page(&view,
				       1000u + BSF_STALL_RING_VIEW_TTL_MS,
				       BSF_STALL_RING_VIEW_TTL_MS, &selected),
	      "still live at exactly the TTL");
	CHECK(!bsf_stall_ring_view_page(&view,
					1001u + BSF_STALL_RING_VIEW_TTL_MS,
					BSF_STALL_RING_VIEW_TTL_MS, &selected),
	      "an abandoned selection reverts on its own, with nothing to cancel");

	/* Restart from anywhere, in any order, with no cursor to resync. */
	bsf_stall_ring_view_select(&view, 3u, 90000u);
	CHECK(bsf_stall_ring_view_page(&view, 90000u,
				       BSF_STALL_RING_VIEW_TTL_MS, &selected) &&
		      selected == 3u,
	      "re-selecting after an abandoned attempt just works");
	bsf_stall_ring_view_clear(&view);
	CHECK(!bsf_stall_ring_view_page(&view, 90000u,
					BSF_STALL_RING_VIEW_TTL_MS, &selected),
	      "explicit RING PAGE OFF reverts immediately");
	return 0;
}

static int test_pages_cover_every_entry_in_order(void)
{
	struct bsf_stall_ring ring;
	struct board b = { .outbound_alive = true, .subscribed = true };
	bsf_stall_ring_page_t page;
	uint32_t previous = 0u;
	uint16_t seen = 0u;

	printf("pages tile the ring exactly once, oldest first\n");
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 517u); /* wrapped, so head != 0 */
	CHECK(ring.count == BSF_STALL_RING_CAPACITY, "full");
	CHECK(ring.head != 0u, "wrapped to a non-zero head");

	for (uint8_t p = 0; p < bsf_stall_ring_pages(&ring); ++p) {
		CHECK(bsf_stall_ring_render_page(&ring, p, &page) == 0,
		      "page renders");
		for (uint8_t i = 0; i < page.entries; ++i) {
			uint32_t t = page.entries_data[i].uptime_ms;

			CHECK(t > previous, "timestamps are strictly increasing");
			previous = t;
			seen++;
		}
	}
	CHECK(seen == BSF_STALL_RING_CAPACITY,
	      "every stored entry appears exactly once");

	/* A partially filled ring pages correctly too. */
	bsf_stall_ring_clear(&ring);
	run(&ring, &b, 7u);
	CHECK(bsf_stall_ring_pages(&ring) == 2u, "7 entries -> 2 pages");
	CHECK(bsf_stall_ring_render_page(&ring, 1u, &page) == 0,
	      "the short tail page renders");
	CHECK(page.entries == 2u, "the tail page declares 2 valid entries");
	CHECK(bsf_stall_ring_render_page(&ring, 2u, &page) == -1,
	      "there is no third page");
	return 0;
}

static int test_clear_rearms(void)
{
	printf("RING CLEAR re-arms a latched ring\n");
	CHECK(retained_ring.frozen == 1u, "still frozen from the reset test");
	bsf_stall_ring_clear(&retained_ring);
	CHECK(retained_ring.frozen == 0u, "cleared");
	CHECK(retained_ring.count == 0u, "emptied");
	CHECK(retained_ring.writes_total == 0u, "write counter reset");
	CHECK(retained_ring.magic == BSF_STALL_RING_MAGIC,
	      "clear does not disturb the retained magic");
	CHECK(retained_ring.boot_id != 0u, "clear does not reset boot_id");
	return 0;
}

/*
 * F2 — flash-fresh garbage in, explicit invalid out.
 *
 * The dangerous case is not random bytes (the magic catches those) but a
 * *different build's* ring at the same address, or a plausible-looking header
 * whose indices lie. Either must be reported invalid and wiped, never rendered.
 */
static int test_garbage_is_never_rendered(void)
{
	struct bsf_stall_ring ring;
	struct board b = { .outbound_alive = true, .subscribed = true };
	bsf_stall_ring_page_t page;

	printf("F2: a retained ring that fails validation is refused, not rendered\n");

	/* (a) cold RAM: no magic at all. */
	memset(&ring, 0x5a, sizeof(ring));
	CHECK(bsf_stall_ring_boot(&ring) == BSF_RING_BOOT_COLD, "0x5a fill -> cold");
	CHECK(ring.count == 0u, "wiped");
	CHECK(bsf_stall_ring_pages(&ring) == 0u, "no pages exist");
	CHECK(bsf_stall_ring_render_page(&ring, 0u, &page) == -1,
	      "page 0 cannot be rendered from a rejected ring");

	memset(&ring, 0xff, sizeof(ring));
	CHECK(bsf_stall_ring_boot(&ring) == BSF_RING_BOOT_COLD, "0xff fill -> cold");
	CHECK(ring.count == 0u && ring.head == 0u && ring.frozen == 0u, "wiped");

	/* (b) the magic survives but the geometry is another build's. */
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 120u);
	CHECK(ring.count == 120u, "populated");
	ring.capacity = BSF_STALL_RING_CAPACITY / 2u;
	CHECK(bsf_stall_ring_boot(&ring) == BSF_RING_BOOT_GEOMETRY,
	      "a different capacity is refused");
	CHECK(ring.count == 0u, "and wiped");
	CHECK(ring.capacity == BSF_STALL_RING_CAPACITY, "re-stamped correctly");

	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 120u);
	ring.entry_size = 16u; /* the depth D1 originally suggested */
	CHECK(bsf_stall_ring_boot(&ring) == BSF_RING_BOOT_GEOMETRY,
	      "a different entry size is refused");
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 120u);
	ring.period_ms = 20u;
	CHECK(bsf_stall_ring_boot(&ring) == BSF_RING_BOOT_GEOMETRY,
	      "a different sample period is refused");

	/* (c) magic and geometry fine, indices impossible. */
	struct {
		const char *name;
		void (*corrupt)(struct bsf_stall_ring *);
	} cases[] = {
		{ "count past capacity", NULL },
		{ "head past capacity", NULL },
		{ "freeze_index past count", NULL },
		{ "frozen without a reason", NULL },
		{ "a reason without frozen", NULL },
		{ "unknown freeze reason", NULL },
		{ "partial ring with a wrapped head", NULL },
		{ "no_exit counter past its own limit", NULL },
	};
	for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
		memset(&ring, 0, sizeof(ring));
		(void)bsf_stall_ring_boot(&ring);
		run(&ring, &b, 120u);
		switch (i) {
		case 0: ring.count = BSF_STALL_RING_CAPACITY + 1u; break;
		case 1: ring.head = BSF_STALL_RING_CAPACITY; break;
		case 2: ring.freeze_index = (uint16_t)(ring.count + 1u); break;
		case 3: ring.frozen = 1u; break;
		case 4: ring.freeze_reason = BSF_RING_FREEZE_ALARM; break;
		case 5: ring.frozen = 1u; ring.freeze_reason = 9u; break;
		case 6: ring.head = 7u; break;
		case 7: ring.no_exit_samples =
				BSF_STALL_RING_NO_EXIT_SAMPLES + 1u; break;
		default: break;
		}
		CHECK(bsf_stall_ring_boot(&ring) == BSF_RING_BOOT_INVALID,
		      cases[i].name);
		CHECK(ring.count == 0u && bsf_stall_ring_pages(&ring) == 0u,
		      "  ...wiped, and nothing renders");
	}

	/* (d) a valid but empty ring is EMPTY, not INVALID -- they differ. */
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	CHECK(bsf_stall_ring_boot(&ring) == BSF_RING_BOOT_EMPTY,
	      "a trustworthy but empty ring is reported empty, not invalid");
	return 0;
}

/*
 * F2 — a reader must be able to tell a partial ring from a wrapped one, and
 * must always know where the newest entry is.
 */
static int test_partial_vs_wrapped(void)
{
	struct bsf_stall_ring ring;
	struct board b = { .outbound_alive = true, .subscribed = true };
	bsf_stall_ring_page_t page;
	uint32_t newest;

	printf("F2: partial and wrapped rings are distinguishable, newest is unambiguous\n");

	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 60u);
	CHECK(ring.count == 60u && ring.count < ring.capacity, "partial");
	CHECK(ring.head == ring.count, "a partial ring has head == count");
	CHECK(bsf_stall_ring_slot(&ring, 0u) == 0u,
	      "oldest of a partial ring is slot 0");
	CHECK(bsf_stall_ring_render_page(&ring, 11u, &page) == 0, "last page renders");
	CHECK(page.entries == 5u && page.count == 60u, "header states the fill");
	newest = page.entries_data[page.entries - 1u].uptime_ms;
	CHECK(newest == page.newest_uptime_ms,
	      "newest is the last entry of the last page, and the header agrees");

	run(&ring, &b, 400u);
	CHECK(ring.count == ring.capacity, "wrapped and full");
	CHECK(ring.head != ring.count, "a wrapped ring's head is the oldest slot");
	CHECK(bsf_stall_ring_slot(&ring, 0u) == ring.head,
	      "oldest of a wrapped ring is at head");
	CHECK(bsf_stall_ring_render_page(&ring, 39u, &page) == 0, "last page renders");
	newest = page.entries_data[page.entries - 1u].uptime_ms;
	CHECK(newest == page.newest_uptime_ms, "newest still unambiguous");
	CHECK(page.count == BSF_STALL_RING_CAPACITY,
	      "count == capacity is how a reader knows it wrapped");
	return 0;
}

/*
 * F1 — the backstop's share of the v37 disconnect retraction.
 */
static int test_disconnect_retraction(void)
{
	struct bsf_stall_ring ring;
	struct board b = { .outbound_alive = true, .subscribed = true };
	const uint32_t window = 1500u;

	printf("F1: disconnect retraction, per freeze cause\n");

	/* ALARM follows the detector's verdict, in both directions. */
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 100u);
	(void)bsf_stall_ring_freeze(&ring, BSF_RING_FREEZE_ALARM, b.uptime_ms);
	CHECK(!bsf_stall_ring_retract_disconnect(&ring, b.uptime_ms + 100u,
						 window, false),
	      "an alarm the detector kept is not retracted by the ring");
	CHECK(ring.frozen == 1u, "still frozen");
	CHECK(bsf_stall_ring_retract_disconnect(&ring, b.uptime_ms + 100u,
						window, true),
	      "an alarm the detector retracted is retracted here too");
	CHECK(ring.frozen == 0u && ring.count == 0u, "re-armed");

	/* NO_EXIT is retracted on the window, since it has no alarm. */
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 100u);
	(void)bsf_stall_ring_freeze(&ring, BSF_RING_FREEZE_NO_EXIT, b.uptime_ms);
	CHECK(!bsf_stall_ring_retract_disconnect(&ring, b.uptime_ms + window + 1u,
						 window, false),
	      "a disconnect past the window leaves the backstop latched");
	CHECK(ring.frozen == 1u, "still frozen");
	CHECK(bsf_stall_ring_retract_disconnect(&ring, b.uptime_ms + window,
						window, false),
	      "a disconnect inside the window retracts it, with no alarm involved");
	CHECK(ring.frozen == 0u && ring.count == 0u, "re-armed");

	/* MANUAL is never retracted; the operator meant it. */
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 100u);
	(void)bsf_stall_ring_freeze(&ring, BSF_RING_FREEZE_MANUAL, b.uptime_ms);
	CHECK(!bsf_stall_ring_retract_disconnect(&ring, b.uptime_ms, window, true),
	      "a manual latch survives a disconnect, alarm retracted or not");
	CHECK(ring.frozen == 1u, "still frozen");

	/* An unfrozen ring is a no-op, not a spurious clear. */
	bsf_stall_ring_clear(&ring);
	run(&ring, &b, 20u);
	CHECK(!bsf_stall_ring_retract_disconnect(&ring, b.uptime_ms, window, true),
	      "an unfrozen ring is untouched");
	CHECK(ring.count == 20u, "and keeps its samples");

	/*
	 * The v36 shape cannot reach the backstop at all: the central vanishes,
	 * the disconnect lands at the 4000 ms supervision timeout, subscriptions
	 * clear, `armed` drops and the counter resets -- 2000 ms before the
	 * 6000 ms latch.
	 */
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 200u);
	b.outbound_alive = false;
	run(&ring, &b, 80u); /* 4.0 s of blocked notifies */
	CHECK(ring.frozen == 0u, "not latched at the supervision timeout");
	b.subscribed = false; /* the disconnect clears the subscriptions */
	run(&ring, &b, 200u);
	CHECK(ring.frozen == 0u,
	      "and never latches afterwards, because armed went false");
	CHECK(ring.no_exit_samples == 0u, "the counter was reset by the gate");
	return 0;
}

/*
 * H1 — the bounded self-reset.
 *
 * This test REPLACES an E2 assertion that `sys_reboot` and `stall_recovery`
 * appear nowhere in the ring policy. That assertion was correct when written:
 * it guarded v36's spurious-reboot failure, and a ring backstop had no business
 * rebooting anything. It is overturned deliberately, not eroded, because N6
 * showed the ring is unreachable on a live stalled board by either path, so a
 * self-reset is the only way the trajectory is ever readable. What must still
 * hold is pinned below: freeze strictly precedes reset, the reset is bounded to
 * one per power cycle, and nothing else can trigger it.
 */
static int test_bounded_self_reset(void)
{
	struct bsf_stall_ring ring;
	struct board b = { .outbound_alive = true, .subscribed = true };

	printf("H1: bounded ISR self-reset\n");
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);

	run(&ring, &b, 300u);
	CHECK(!bsf_stall_ring_take_reset(&ring), "healthy traffic owes no reset");
	CHECK(ring.isr_resets == 0u, "and consumes none of the budget");

	b.outbound_alive = false;
	run(&ring, &b, BSF_STALL_RING_NO_EXIT_SAMPLES - 1u);
	CHECK(ring.reset_pending == 0u, "no reset owed one sample early");
	CHECK(!bsf_stall_ring_take_reset(&ring), "and none can be claimed");

	run(&ring, &b, 1u);
	/* ORDERING: the freeze must already be in place the instant the reset is
	 * owed -- this is the property the whole change hangs on. */
	CHECK(ring.frozen == 1u, "frozen BEFORE the reset is owed");
	CHECK(ring.freeze_reason == BSF_RING_FREEZE_NO_EXIT, "frozen by the latch");
	CHECK(ring.freeze_index > 0u, "and the trajectory is in the buffer");
	CHECK(ring.reset_pending == 1u, "reset now owed");

	CHECK(bsf_stall_ring_take_reset(&ring), "claimed once");
	CHECK(ring.isr_resets == 1u, "budget consumed");
	CHECK(ring.reset_pending == 0u, "claim clears the request");
	CHECK(!bsf_stall_ring_take_reset(&ring), "never twice");

	/* Across the reset it caused: .noinit survives, so the bound survives. */
	CHECK(bsf_stall_ring_boot(&ring) == BSF_RING_BOOT_RETAINED,
	      "the ring survives its own reset");
	CHECK(ring.isr_resets == 1u, "and so does the bound — no boot loop");
	CHECK(ring.frozen == 1u, "still frozen, still readable");
	CHECK(!bsf_stall_ring_take_reset(&ring),
	      "a second stall this power cycle gets no second reset");

	/* RING CLEAR re-arms the ring but must NOT refund the reset budget. */
	bsf_stall_ring_clear(&ring);
	CHECK(ring.isr_resets == 1u, "RING CLEAR does not refund the budget");
	b.outbound_alive = false;
	run(&ring, &b, BSF_STALL_RING_NO_EXIT_SAMPLES + 5u);
	CHECK(ring.frozen == 1u, "it still freezes");
	CHECK(ring.reset_pending == 0u, "but owes no further reset");

	/* Nor may the E2 disconnect retraction refund it. */
	bsf_stall_ring_clear(&ring);
	run(&ring, &b, 10u);
	(void)bsf_stall_ring_freeze(&ring, BSF_RING_FREEZE_NO_EXIT, b.uptime_ms);
	(void)bsf_stall_ring_retract_disconnect(&ring, b.uptime_ms, 1500u, false);
	CHECK(ring.isr_resets == 1u, "retraction does not refund the budget");

	/* A reset is never taken without the evidence it exists to preserve. */
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	ring.reset_pending = 1u;   /* impossible in practice; refuse anyway */
	CHECK(!bsf_stall_ring_take_reset(&ring),
	      "refuses to reset an unfrozen ring");
	CHECK(ring.isr_resets == 0u, "and spends nothing doing so");
	return 0;
}

/*
 * H1/§4 — the ring must be able to answer WHY the detector stayed quiet.
 */
static int test_detector_inputs_present(void)
{
	struct bsf_stall_ring ring;
	struct board b = { .outbound_alive = true, .subscribed = true };
	bsf_stall_ring_page_t page;

	printf("H1: the detector's own inputs are sampled\n");
	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 20u);
	CHECK(bsf_stall_ring_render_page(&ring, 0u, &page) == 0, "page renders");
	CHECK(page.entries_data[0].subscribed_notify_ok == 4096u,
	      "the arming counter is on the wire");
	CHECK(page.version == BSF_STALL_RING_VERSION, "page is v4");

	b.subscribed = false;
	run(&ring, &b, 20u);
	CHECK(bsf_stall_ring_render_page(&ring, 7u, &page) == 0, "later page renders");
	CHECK(page.entries_data[0].subscribed_notify_ok == 0u,
	      "an unarmed board is visibly unarmed in the ring");
	return 0;
}

/*
 * `--emit-page N` writes one real rendered page to stdout as raw bytes, so the
 * host decoder can be tested against the firmware's own struct layout instead
 * of against a Python re-implementation of it.
 */
static int emit_page(int page)
{
	struct bsf_stall_ring ring;
	struct board b = { .outbound_alive = true, .subscribed = true };
	bsf_stall_ring_page_t out;

	memset(&ring, 0, sizeof(ring));
	(void)bsf_stall_ring_boot(&ring);
	run(&ring, &b, 260u);
	b.outbound_alive = false;
	run(&ring, &b, 100u);
	(void)bsf_stall_ring_freeze(&ring, BSF_RING_FREEZE_ALARM, b.uptime_ms);
	if (bsf_stall_ring_render_page(&ring, (uint8_t)page, &out) != 0) {
		return 1;
	}
	return fwrite(&out, sizeof(out), 1, stdout) == 1 ? 0 : 1;
}

int main(int argc, char **argv)
{
	if (argc == 3 && strcmp(argv[1], "--emit-page") == 0) {
		return emit_page(atoi(argv[2]));
	}
	printf("B306 stall trajectory ring policy\n");
	if (test_geometry() || test_samples_while_outbound_dead() ||
	    test_latch_needs_subscription() ||
	    test_alarm_freeze_centres_the_onset() ||
	    test_survives_soft_reset() ||
	    test_retrieval_idempotent_and_restartable() ||
	    test_pages_cover_every_entry_in_order() ||
	    test_garbage_is_never_rendered() || test_partial_vs_wrapped() ||
	    test_disconnect_retraction() || test_bounded_self_reset() ||
	    test_detector_inputs_present() || test_clear_rearms()) {
		printf("stall ring policy: FAIL\n");
		return 1;
	}
	printf("stall ring policy: PASS\n");
	return 0;
}
