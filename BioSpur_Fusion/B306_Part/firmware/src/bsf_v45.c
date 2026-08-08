/*
 * bsf_v45.c -- v45 runtime: channel storage, detector, capture, persistence,
 *              export.
 *
 * WHERE THIS RUNS, AND WHY IT MATTERS MORE THAN WHAT IT DOES
 * ----------------------------------------------------------
 * Three instrument failures in a row shared one shape: the measurement shared
 * execution context or authority with the measured system. v43's stage
 * semantics, v44's multi-writer global stage, and the 1 Hz pool sampler that
 * ran on the same workqueue immediately after the frees it was measuring --
 * each produced a reading that looked like evidence and was not.
 *
 * So the placement here is a decision, not a convenience:
 *   - the four TRACE CHANNELS are written only by the thread each one names,
 *     and a foreign write is dropped and counted (bsf_v45_trace.h);
 *   - the DETECTOR and the CAPTURE routine run on the SYSTEM WORKQUEUE, the one
 *     context proven alive through a 4 h 38 min wedge (BSF1120 fed the watchdog
 *     ~5400 consecutive times with zero resets);
 *   - the pool LOW-WATER is folded in at every successful allocation inside
 *     net_buf, never sampled by an observer that could be biased by its own
 *     position in the schedule.
 *
 * The residual risk of putting the detector on the system workqueue is stated
 * plainly in DECISIONS.md: a wedge that DOES block the system workqueue is
 * invisible to it. That class is excluded for all four observed events and for
 * no future one, so the TX_WORK channel records system-workqueue liveness
 * explicitly -- a decoded corpse can always answer whether the syswq was
 * running, instead of the question being begged by where the detector lives.
 */
#include <stdbool.h>
#include <stddef.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/crc.h>
#include <zephyr/sys/reboot.h>

#include "bsf_v45.h"
#include "bsf_v45_corpse.h"
#include "bsf_v45_detector.h"
#include "bsf_v45_trace.h"
#include "stall_ring_policy.h"

#if defined(BSF_CORPSE_FLASH_ENABLED) && (BSF_CORPSE_FLASH_ENABLED == 1)
#include <zephyr/storage/flash_map.h>
#endif

LOG_MODULE_REGISTER(bsf_v45, LOG_LEVEL_INF);

/* ================================================================== */
/* Storage                                                             */
/* ================================================================== */

/*
 * `.noinit` throughout. nRF52840 RAM keeps its contents across watchdog,
 * SYSRESETREQ, pin and lockup resets; only power-on and brownout lose them.
 * That is exactly the reset class the recovery path uses, and exactly the class
 * section 9 exists to survive. Nothing here is trusted without magic + CRC.
 */
__attribute__((section(".noinit"))) struct bsf_v45_channel_state
	bsf_v45_ch[BSF_V45_CH__COUNT];
__attribute__((section(".noinit"))) static bsf_v45_core_t bsf_v45_core;
__attribute__((section(".noinit"))) static bsf_v45_bank_header_t
	bsf_v45_bank[BSF_V45_BANK__COUNT];

volatile uint32_t bsf_v45_frozen;
struct bsf_v45_counters bsf_v45_cnt;

/* Filled by bsf_v45_init(); the ring itself stays owned by main.c. */
static struct bsf_stall_ring *v45_ring;
static struct k_spinlock *v45_ring_lock;
static bool (*v45_budget_take)(uint32_t owner);

static struct bsf_v45_detector v45_det;
static atomic_t v45_force_trigger;
static atomic_t v45_ota_active;
static atomic_t v45_ota_last_ms;
static bool v45_corpse_present;
#if defined(BSF_CORPSE_FLASH_ENABLED) && (BSF_CORPSE_FLASH_ENABLED == 1)
static uint8_t v45_flash_slot_next;
#endif

#define BSF_V45_OTA_KEEPALIVE_MS 30000u
#define BSF_V45_MONITOR_TICK_MS  1000u
#define BSF_V45_REBOOT_OWNER     3u   /* BSF_REBOOT_OWNER_{NONE,RING,BTRX}=0,1,2 */

/*
 * Thread handles. "MPSL Work", "BT RX WQ" and "sysworkq" are private to the SDK
 * with no accessor, so they are found by NAME -- CONFIG_THREAD_NAME and
 * CONFIG_THREAD_MONITOR are already on, so this needs no extra SDK patch. The
 * two application threads are handed in directly; guessing at a name we own
 * would be silly.
 */
static k_tid_t v45_thread[BSF_V45_THREAD__COUNT];

struct v45_find_ctx {
	const char *name;
	uint8_t slot;
};

static void v45_find_cb(const struct k_thread *thread, void *user)
{
	struct v45_find_ctx *ctx = user;
	const char *name = k_thread_name_get((k_tid_t)thread);

	if (name != NULL && strcmp(name, ctx->name) == 0) {
		v45_thread[ctx->slot] = (k_tid_t)thread;
	}
}

static void v45_find_threads(void)
{
	static const struct { const char *name; uint8_t slot; } wanted[] = {
		{ "MPSL Work", BSF_V45_THREAD_MPSL_RX },
		{ "BT RX WQ",  BSF_V45_THREAD_BT_RX },
		{ "sysworkq",  BSF_V45_THREAD_SYS_WQ },
	};

	for (size_t i = 0; i < ARRAY_SIZE(wanted); ++i) {
		struct v45_find_ctx ctx = { wanted[i].name, wanted[i].slot };

		if (v45_thread[wanted[i].slot] == NULL) {
			k_thread_foreach_unlocked(v45_find_cb, &ctx);
		}
	}
}

void bsf_v45_bind_app_threads(k_tid_t notify_worker, k_tid_t publisher)
{
	v45_thread[BSF_V45_THREAD_NOTIFY] = notify_worker;
	v45_thread[BSF_V45_THREAD_PUBLISHER] = publisher;
}

/* ================================================================== */
/* Environment -- provided by main.c, which owns the application state */
/* ================================================================== */

static bool v45_ota_active_now(uint32_t now_ms)
{
	if (atomic_get(&v45_ota_active) == 0) {
		return false;
	}
	/*
	 * A dropped OTA must not disarm the detector forever. The keepalive is
	 * refreshed on every chunk; if it goes stale the transfer is over,
	 * whatever the host thinks.
	 */
	if ((now_ms - (uint32_t)atomic_get(&v45_ota_last_ms)) >
	    BSF_V45_OTA_KEEPALIVE_MS) {
		atomic_set(&v45_ota_active, 0);
		return false;
	}
	return true;
}

void bsf_v45_ota_mark(bool active)
{
	atomic_set(&v45_ota_last_ms, (atomic_val_t)k_uptime_get_32());
	atomic_set(&v45_ota_active, active ? 1 : 0);
}

/* ================================================================== */
/* Capture -- the exact order of section 10                            */
/* ================================================================== */

static uint32_t v45_bank_payload_len(uint8_t bank)
{
	if (bank == BSF_V45_BANK_RING) {
		return (uint32_t)BSF_STALL_RING_CAPACITY *
		       (uint32_t)sizeof(bsf_stall_ring_entry_t);
	}
	return (uint32_t)BSF_V45_TRACE_ENTRIES *
	       (uint32_t)sizeof(struct bsf_v45_trace_entry);
}

static const uint8_t *v45_bank_payload(uint8_t bank)
{
	if (bank == BSF_V45_BANK_RING) {
		return (const uint8_t *)v45_ring->entries;
	}
	return (const uint8_t *)bsf_v45_ch[bank].trace;
}

static void v45_seal_bank(uint8_t bank, uint32_t corpse_seq)
{
	bsf_v45_bank_header_t *h = &bsf_v45_bank[bank];
	uint32_t len = v45_bank_payload_len(bank);

	memset(h, 0, sizeof(*h));
	h->magic = BSF_V45_BANK_MAGIC;
	h->schema = BSF_V45_SCHEMA;
	h->bank = bank;
	h->length = len;
	h->corpse_seq = corpse_seq;
	if (bank == BSF_V45_BANK_RING) {
		h->entry_size = (uint8_t)sizeof(bsf_stall_ring_entry_t);
		h->entries = v45_ring->count;
		h->head = v45_ring->head;
	} else {
		h->entry_size = (uint8_t)sizeof(struct bsf_v45_trace_entry);
		h->entries = (uint16_t)MIN(bsf_v45_ch[bank].trace_head,
					   (uint32_t)BSF_V45_TRACE_ENTRIES);
		h->head = (uint16_t)(bsf_v45_ch[bank].trace_head &
				     (BSF_V45_TRACE_ENTRIES - 1u));
	}
	h->crc32 = crc32_ieee(v45_bank_payload(bank), len);
	__DMB();
	h->valid = BSF_V45_BANK_MAGIC;   /* LAST */
	__DMB();
}

static void v45_snapshot_threads(void)
{
	unsigned int key;

	v45_find_threads();

	/*
	 * Fixed fields under a BRIEF irq_lock -- long enough that tid, state,
	 * prio, pended_on and psp are mutually consistent, short enough that it
	 * is not a scheduling event. Stack scanning and CRCs are deliberately
	 * OUTSIDE it (brief section 4): k_thread_stack_space_get() walks the
	 * whole stack, and holding interrupts off for that on a live radio is
	 * how you turn a diagnostic into a fault.
	 */
	key = irq_lock();
	for (uint8_t i = 0; i < BSF_V45_THREAD__COUNT; ++i) {
		struct bsf_v45_thread_snapshot *t = &bsf_v45_core.thread[i];
		k_tid_t th = v45_thread[i];

		memset(t, 0, sizeof(*t));
		if (th == NULL) {
			continue;
		}
		t->tid = (uint32_t)(uintptr_t)th;
		t->pended_on = (uint32_t)(uintptr_t)th->base.pended_on;
		t->psp = (uint32_t)th->callee_saved.psp;
		t->stack_start = (uint32_t)th->stack_info.start;
		t->stack_size = (uint32_t)th->stack_info.size;
		t->thread_state = th->base.thread_state;
		t->prio = (int8_t)th->base.prio;
		t->found = 1u;
	}
	irq_unlock(key);

	for (uint8_t i = 0; i < BSF_V45_THREAD__COUNT; ++i) {
		struct bsf_v45_thread_snapshot *t = &bsf_v45_core.thread[i];
		size_t unused = 0u;

		if (t->found == 0u) {
			continue;
		}
		if (k_thread_stack_space_get(v45_thread[i], &unused) == 0) {
			t->stack_unused = (uint32_t)unused;
		}
	}

	/* Channel seq belongs with the thread that owns the channel. */
	bsf_v45_core.thread[BSF_V45_THREAD_MPSL_RX].last_channel_seq =
		bsf_v45_ch[BSF_V45_CH_MPSL_RX].seq;
	bsf_v45_core.thread[BSF_V45_THREAD_BT_RX].last_channel_seq =
		bsf_v45_ch[BSF_V45_CH_BT_RX].seq;
	bsf_v45_core.thread[BSF_V45_THREAD_SYS_WQ].last_channel_seq =
		bsf_v45_ch[BSF_V45_CH_TX_WORK].seq;
	bsf_v45_core.thread[BSF_V45_THREAD_NOTIFY].last_channel_seq =
		bsf_v45_ch[BSF_V45_CH_APP_NOTIFY].seq;
}

static void v45_snapshot_channels(uint32_t now_cycles)
{
	for (uint8_t i = 0; i < BSF_V45_CH__COUNT; ++i) {
		const struct bsf_v45_channel_state *c = &bsf_v45_ch[i];
		struct bsf_v45_channel_summary *s = &bsf_v45_core.channel[i];

		s->stage = c->stage;
		s->seq = c->seq;
		s->arg0 = c->arg0;
		s->arg1 = c->arg1;
		s->stage_age_ms = k_cyc_to_ms_near32(now_cycles - c->stage_cycles);
		s->enter_total = c->enter_total;
		s->exit_total = c->exit_total;
		s->last_enter_ms = c->last_enter_ms;
		s->last_exit_ms = c->last_exit_ms;
		s->writer_tid = c->writer_tid;
		s->writer_mismatch_count = c->writer_mismatch_count;
		s->first_offending_tid = c->first_offending_tid;
		s->trace_head = c->trace_head;
	}
}

static void v45_snapshot_counters(void)
{
	const atomic_t *src = (const atomic_t *)&bsf_v45_cnt;
	size_t n = sizeof(bsf_v45_cnt) / sizeof(atomic_t);

	if (n > ARRAY_SIZE(bsf_v45_core.counters)) {
		n = ARRAY_SIZE(bsf_v45_core.counters);
	}
	for (size_t i = 0; i < n; ++i) {
		bsf_v45_core.counters[i] = (uint32_t)atomic_get(&src[i]);
	}
	bsf_v45_core.tx_pending_depth =
		(int32_t)atomic_get(&bsf_v45_cnt.tx_pending_added) -
		(int32_t)atomic_get(&bsf_v45_cnt.tx_pending_removed);
	bsf_v45_core.tx_complete_depth =
		(int32_t)atomic_get(&bsf_v45_cnt.tx_complete_added) -
		(int32_t)atomic_get(&bsf_v45_cnt.tx_complete_drained);
}

/*
 * The whole capture. Runs on the system workqueue.
 *
 * Nothing here allocates, logs on the hot path, or takes a mutex, and nothing
 * walks a live kernel list -- conn->tx_pending has three unlocked mutation
 * contexts (CONTEXT_AUDIT item 5) and is represented by shadow atomics instead.
 */
static void v45_capture(const struct bsf_v45_decision *d,
			const struct bsf_v45_env *env, uint32_t now_ms)
{
	uint32_t now_cycles;
	uint32_t seq = bsf_v45_core.corpse_seq + 1u;

	/* 1. Freeze every channel FIRST. Everything below reads a still image. */
	bsf_v45_frozen = 1u;
	__DMB();
	now_cycles = k_cycle_get_32();

	/* 2. Freeze the trajectory ring and record where the suspicion began. */
	if (v45_ring != NULL && v45_ring_lock != NULL) {
		k_spinlock_key_t key = k_spin_lock(v45_ring_lock);

		(void)bsf_stall_ring_freeze(v45_ring, BSF_RING_FREEZE_ALARM,
					    now_ms);
		k_spin_unlock(v45_ring_lock, key);
	}

	memset(&bsf_v45_core, 0, offsetof(bsf_v45_core_t, valid));
	bsf_v45_core.magic = BSF_V45_CORPSE_MAGIC;
	bsf_v45_core.schema = BSF_V45_SCHEMA;
	bsf_v45_core.corpse_seq = seq;

	bsf_v45_core.fw_marker_hash = env->fw_marker_hash;
	bsf_v45_core.node_identity = env->node_identity;
	bsf_v45_core.uptime_ms = now_ms;
	bsf_v45_core.boot_reset_reason = env->boot_reset_reason;
	bsf_v45_core.epoch = env->epoch;
	bsf_v45_core.trigger_cause = d->cause;
	bsf_v45_core.trigger_count = (uint16_t)v45_det.trigger_count;
	bsf_v45_core.notify_exit_age_ms = d->notify_exit_age_ms;
	bsf_v45_core.ncp_packet_age_ms = d->ncp_packet_age_ms;
	bsf_v45_core.suspect_start_ms = v45_det.suspect_start_ms;
	bsf_v45_core.suspect_ring_index = v45_det.suspect_ring_index;
	bsf_v45_core.connected_at_ms = env->connected_at_ms;
	bsf_v45_core.connected = env->connected ? 1u : 0u;
	bsf_v45_core.data_subscribed = env->data_subscribed ? 1u : 0u;
	bsf_v45_core.telemetry_subscribed = env->telemetry_subscribed ? 1u : 0u;
	bsf_v45_core.ota_active = v45_ota_active_now(now_ms) ? 1u : 0u;

	/* 3. Threads and their wait objects. */
	v45_snapshot_threads();

	/*
	 * 4. Pools, buffer ownership, connection shadow state.
	 *
	 * Captured into ALIGNED LOCALS first. The CORE is `__packed`, so
	 * &bsf_v45_core.pools is an under-aligned pointer, and handing one to
	 * another translation unit is undefined behaviour -- on Cortex-M it is
	 * also a real unaligned access. v44 hit exactly this and solved it the
	 * same way; the compiler warns about it, and the warning is right.
	 */
	{
		struct bsf_v45_pool_snapshot pools;
		struct bsf_v45_conn_snapshot conn;
		struct bsf_v45_waitobj_table waitobj;

		bsf_v45_capture_pools(&pools);
		bsf_v45_capture_conn(&conn);
		bsf_v45_capture_waitobjs(&waitobj);
		memcpy(&bsf_v45_core.pools, &pools, sizeof(pools));
		memcpy(&bsf_v45_core.conn, &conn, sizeof(conn));
		memcpy(&bsf_v45_core.waitobj, &waitobj, sizeof(waitobj));
		bsf_v45_core.tx_complete_busy = conn.tx_complete_busy;
	}

	/* 5. Progress counters and channel summaries. */
	v45_snapshot_channels(now_cycles);
	v45_snapshot_counters();
	bsf_v45_core.wdt_feed_count = env->wdt_feed_count;
	bsf_v45_core.producer_seq = env->producer_seq;
	bsf_v45_core.publisher_count = env->publisher_count;
	bsf_v45_core.notify_timeout_drop_total = env->notify_timeout_drop_total;

	bsf_v45_core.reboot_taken = 0u;
	bsf_v45_core.reboot_owner = 0u;
	bsf_v45_core.flash_slot = 0xffu;
#if defined(BSF_CORPSE_FLASH_ENABLED) && (BSF_CORPSE_FLASH_ENABLED == 1)
	bsf_v45_core.flash_enabled = 1u;
#else
	bsf_v45_core.flash_enabled = 0u;
#endif

	/* 6. CRCs, then the valid flags -- banks first, CORE last. */
	for (uint8_t b = 0; b < BSF_V45_BANK__COUNT; ++b) {
		if (b == BSF_V45_BANK_RING && v45_ring == NULL) {
			memset(&bsf_v45_bank[b], 0, sizeof(bsf_v45_bank[b]));
			continue;
		}
		v45_seal_bank(b, seq);
	}
	bsf_v45_core.length = (uint16_t)(offsetof(bsf_v45_core_t, valid) -
					 offsetof(bsf_v45_core_t, fw_marker_hash));
	bsf_v45_core.crc32 = crc32_ieee(
		(const uint8_t *)&bsf_v45_core.fw_marker_hash, bsf_v45_core.length);
	__DMB();
	bsf_v45_core.valid = BSF_V45_CORPSE_MAGIC;   /* LAST */
	__DMB();

	v45_corpse_present = true;
}

bool bsf_v45_core_validate(void)
{
	uint16_t want = (uint16_t)(offsetof(bsf_v45_core_t, valid) -
				   offsetof(bsf_v45_core_t, fw_marker_hash));

	if (bsf_v45_core.magic != BSF_V45_CORPSE_MAGIC ||
	    bsf_v45_core.valid != BSF_V45_CORPSE_MAGIC) {
		return false;
	}
	if (bsf_v45_core.schema != BSF_V45_SCHEMA ||
	    bsf_v45_core.length != want) {
		return false;
	}
	return bsf_v45_core.crc32 ==
	       crc32_ieee((const uint8_t *)&bsf_v45_core.fw_marker_hash, want);
}

/* ================================================================== */
/* Flash persistence -- section 9                                      */
/* ================================================================== */

/*
 * NEVER at capture time. The wedged thread may BE mpsl_work_q.thread, and
 * CONTEXT_AUDIT item 10 proves the flash driver takes an MPSL timeslot on every
 * write, at every point the application can run -- MPSL is initialised at
 * PRE_KERNEL_1, so the brief's "radio off before bt_enable(), no sync needed"
 * is simply false. A capture-time write could therefore wait on a timeslot
 * serviced from the very thread that is stuck.
 *
 * After the cold reboot that thread does not exist, `bt_enable()` has not run,
 * nothing competes for the radio, and the call is on the `main` thread where
 * blocking is legal and bounded (FLASH_TIMEOUT_MS, then an error). A failed
 * persist degrades to "the corpse stayed in .noinit". It cannot hang the boot.
 */
#if defined(BSF_CORPSE_FLASH_ENABLED) && (BSF_CORPSE_FLASH_ENABLED == 1)

#define V45_FLASH_ID          FIXED_PARTITION_ID(bsf_corpse_partition)
#define V45_FLASH_SLOT_BYTES  0x2000u

static size_t v45_flash_pack(uint8_t *dst, size_t cap)
{
	size_t off = 0;

	if (cap < sizeof(bsf_v45_core_t)) {
		return 0;
	}
	memcpy(dst, &bsf_v45_core, sizeof(bsf_v45_core_t));
	off += sizeof(bsf_v45_core_t);

	for (uint8_t b = 0; b < BSF_V45_BANK__COUNT; ++b) {
		uint32_t keep = (b == BSF_V45_BANK_RING) ?
			BSF_V45_FLASH_RING_KEEP : BSF_V45_FLASH_TRACE_KEEP;
		uint32_t esz = (b == BSF_V45_BANK_RING) ?
			sizeof(bsf_stall_ring_entry_t) :
			sizeof(struct bsf_v45_trace_entry);
		uint32_t total = v45_bank_payload_len(b) / esz;
		uint32_t n = MIN(keep, total);
		bsf_v45_bank_header_t h = bsf_v45_bank[b];
		const uint8_t *src = v45_bank_payload(b);
		uint32_t first = (total > n) ? (total - n) : 0u;

		if (off + sizeof(h) + (size_t)n * esz > cap) {
			return 0;
		}
		/* The header must describe what is actually carried, not what
		 * the .noinit bank holds -- a truncation the decoder cannot see
		 * is a silent cap, and there are none of those here. */
		h.length = n * esz;
		h.entries = (uint16_t)n;
		h.crc32 = crc32_ieee(&src[(size_t)first * esz], n * esz);
		memcpy(&dst[off], &h, sizeof(h));
		off += sizeof(h);
		memcpy(&dst[off], &src[(size_t)first * esz], (size_t)n * esz);
		off += (size_t)n * esz;
	}
	return off;
}

int bsf_v45_flash_persist(uint8_t slot)
{
	static uint8_t staging[V45_FLASH_SLOT_BYTES];
	const struct flash_area *fa;
	bsf_v45_flash_header_t hdr;
	size_t body;
	int rc;

	if (slot >= BSF_V45_FLASH_SLOTS) {
		return -EINVAL;
	}
	body = v45_flash_pack(&staging[sizeof(hdr)],
			      sizeof(staging) - sizeof(hdr));
	if (body == 0u) {
		return -ENOSPC;
	}
	memset(&hdr, 0, sizeof(hdr));
	hdr.magic = BSF_V45_FLASH_MAGIC;
	hdr.schema = BSF_V45_SCHEMA;
	hdr.slot = slot;
	hdr.length = (uint32_t)body;
	hdr.crc32 = crc32_ieee(&staging[sizeof(hdr)], body);
	hdr.corpse_seq = bsf_v45_core.corpse_seq;
	hdr.write_uptime_ms = k_uptime_get_32();
	hdr.trace_keep = BSF_V45_FLASH_TRACE_KEEP;
	hdr.ring_keep = BSF_V45_FLASH_RING_KEEP;
	hdr.collected = 0u;
	hdr.valid = BSF_V45_FLASH_MAGIC;
	memcpy(staging, &hdr, sizeof(hdr));

	rc = flash_area_open(V45_FLASH_ID, &fa);
	if (rc != 0) {
		return rc;
	}
	rc = flash_area_erase(fa, (off_t)slot * V45_FLASH_SLOT_BYTES,
			      V45_FLASH_SLOT_BYTES);
	if (rc == 0) {
		rc = flash_area_write(fa, (off_t)slot * V45_FLASH_SLOT_BYTES,
				      staging, sizeof(hdr) + body);
	}
	flash_area_close(fa);
	return rc;
}

/*
 * Called from main() BEFORE bt_enable(). See the argument above.
 * A brownout DURING this write leaves a CRC-invalid partial, which the decoder
 * rejects. That is accepted and documented rather than defended against: there
 * is no way to make a single flash write atomic against power loss, and a
 * rejected partial is strictly better than a plausible one.
 */
void bsf_v45_flash_persist_pending(void)
{
	int rc;

	if (!bsf_v45_core_validate()) {
		return;
	}
	if (bsf_v45_core.flash_slot != 0xffu) {
		return;   /* already persisted */
	}
	rc = bsf_v45_flash_persist(v45_flash_slot_next);
	if (rc == 0) {
		bsf_v45_core.flash_slot = v45_flash_slot_next;
		v45_flash_slot_next = (uint8_t)((v45_flash_slot_next + 1u) %
						BSF_V45_FLASH_SLOTS);
		LOG_WRN("V45 corpse persisted to flash slot=%u seq=%u",
			bsf_v45_core.flash_slot, bsf_v45_core.corpse_seq);
	} else {
		LOG_ERR("V45 flash persist failed rc=%d -- corpse retained in .noinit",
			rc);
	}
}

#else  /* BSF_CORPSE_FLASH_ENABLED == 0 */

/*
 * Default. CONTEXT_AUDIT item 11: the deployed partition map tiles the whole
 * 1 MiB with zero bytes free, and the only clean carve requires rebuilding and
 * SWD-reflashing MCUboot on every board -- which Stage C, being OTA-only,
 * cannot do. The implementation above is complete and compiles; it is simply
 * not reachable until that campaign happens.
 */
int bsf_v45_flash_persist(uint8_t slot) { ARG_UNUSED(slot); return -ENOTSUP; }
void bsf_v45_flash_persist_pending(void) { }

#endif /* BSF_CORPSE_FLASH_ENABLED */

/* ================================================================== */
/* Export image -- a flat byte stream the host reassembles              */
/* ================================================================== */

struct v45_region { const uint8_t *base; uint32_t len; };

static uint8_t v45_region_count(struct v45_region *r, size_t cap)
{
	uint8_t n = 0;

	if (cap < 1u + 2u * BSF_V45_BANK__COUNT) {
		return 0;
	}
	r[n++] = (struct v45_region){ (const uint8_t *)&bsf_v45_core,
				      (uint32_t)sizeof(bsf_v45_core_t) };
	for (uint8_t b = 0; b < BSF_V45_BANK__COUNT; ++b) {
		if (bsf_v45_bank[b].valid != BSF_V45_BANK_MAGIC) {
			continue;
		}
		r[n++] = (struct v45_region){ (const uint8_t *)&bsf_v45_bank[b],
					      (uint32_t)sizeof(bsf_v45_bank_header_t) };
		r[n++] = (struct v45_region){ v45_bank_payload(b),
					      bsf_v45_bank[b].length };
	}
	return n;
}

uint32_t bsf_v45_image_len(void)
{
	struct v45_region r[1u + 2u * BSF_V45_BANK__COUNT];
	uint8_t n = v45_region_count(r, ARRAY_SIZE(r));
	uint32_t total = 0;

	for (uint8_t i = 0; i < n; ++i) {
		total += r[i].len;
	}
	return total;
}

/*
 * Pure read. Re-reading a page on a frozen corpse is byte-identical, so
 * retrieval is idempotent and restartable: the host may ask for any page, in
 * any order, any number of times, and nothing on the board advances. The same
 * property the trajectory-ring pages already have, for the same reason.
 */
int bsf_v45_image_read(uint32_t off, uint8_t *dst, uint32_t len)
{
	struct v45_region r[1u + 2u * BSF_V45_BANK__COUNT];
	uint8_t n = v45_region_count(r, ARRAY_SIZE(r));
	uint32_t cursor = 0;
	uint32_t written = 0;

	if (!v45_corpse_present) {
		return -ENOENT;
	}
	for (uint8_t i = 0; i < n && written < len; ++i) {
		uint32_t start = cursor;
		uint32_t end = cursor + r[i].len;

		cursor = end;
		if (off + written >= end) {
			continue;
		}
		{
			uint32_t in_region = (off + written) - start;
			uint32_t take = MIN(len - written, r[i].len - in_region);

			memcpy(&dst[written], &r[i].base[in_region], take);
			written += take;
		}
	}
	if (written < len) {
		memset(&dst[written], 0, len - written);
	}
	return (int)written;
}

bool bsf_v45_present(void) { return v45_corpse_present; }
uint32_t bsf_v45_seq(void) { return bsf_v45_core.corpse_seq; }
uint16_t bsf_v45_cause(void) { return bsf_v45_core.trigger_cause; }
uint32_t bsf_v45_core_len(void) { return (uint32_t)sizeof(bsf_v45_core_t); }

/*
 * ACK-clear. Only after the host has verified CRCs and written its evidence
 * files: an unverified clear is how a corpse gets lost twice.
 */
bool bsf_v45_ack(uint32_t seq)
{
	if (!v45_corpse_present || seq != bsf_v45_core.corpse_seq) {
		return false;
	}
	bsf_v45_core.valid = 0u;
	bsf_v45_core.magic = 0u;
	for (uint8_t b = 0; b < BSF_V45_BANK__COUNT; ++b) {
		bsf_v45_bank[b].valid = 0u;
		bsf_v45_bank[b].magic = 0u;
	}
	v45_corpse_present = false;
	bsf_v45_frozen = 0u;     /* the channels resume */
	return true;
}

/* ================================================================== */
/* The detector, on the system workqueue                               */
/* ================================================================== */

static void v45_reboot_work_handler(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(v45_reboot_work, v45_reboot_work_handler);

static void v45_reboot_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);
	LOG_ERR("V45 WEDGE self-reset (cold) -- corpse seq=%u retained",
		bsf_v45_core.corpse_seq);
	sys_reboot(SYS_REBOOT_COLD);
}

static void v45_monitor_handler(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(v45_monitor_work, v45_monitor_handler);

static void v45_monitor_handler(struct k_work *work)
{
	struct bsf_v45_env env;
	struct bsf_v45_inputs in;
	struct bsf_v45_decision d;
	uint32_t now_ms = k_uptime_get_32();

	ARG_UNUSED(work);

	/* Re-arm FIRST and unconditionally. A detector that can stop
	 * rescheduling itself is a detector that fails silently -- the same
	 * defect telemetry_work_handler() deliberately does not have. */
	k_work_reschedule(&v45_monitor_work, K_MSEC(BSF_V45_MONITOR_TICK_MS));

	if (v45_corpse_present) {
		return;   /* frozen; nothing to measure until it is collected */
	}

	bsf_v45_env_get(&env);
	in = (struct bsf_v45_inputs){
		.connected = env.connected,
		.data_subscribed = env.data_subscribed,
		.ota_active = v45_ota_active_now(now_ms),
		.epoch = env.epoch,
		.now_ms = now_ms,
		.connected_at_ms = env.connected_at_ms,
		.producer_seq = env.producer_seq,
		.notify_exit_total =
			(uint32_t)atomic_get(&bsf_v45_cnt.notify_exit_total),
		.ncp_packet_total =
			(uint32_t)atomic_get(&bsf_v45_cnt.ncp_packet_total),
		.notify_exits_this_epoch = env.notify_exits_this_epoch,
		.forced = atomic_cas(&v45_force_trigger, 1, 0),
	};

	d = bsf_v45_detector_step(&v45_det, &in);

	if (d.mark_suspect && v45_ring != NULL) {
		v45_det.suspect_ring_index = v45_ring->count;
	}
	if (!d.capture) {
		return;
	}

	LOG_ERR("V45 WEDGE cause=%u notify_exit_age=%u ncp_age=%u suspect_ms=%u",
		d.cause, d.notify_exit_age_ms, d.ncp_packet_age_ms,
		v45_det.suspect_start_ms);

	v45_capture(&d, &env, now_ms);

	if (!d.reboot) {
		/*
		 * Second trigger in this power cycle: captured to the other
		 * slot, and the board deliberately STAYS UP WEDGED. No boot
		 * loops, ever -- that is a hard rule, not a preference.
		 */
		LOG_ERR("V45 WEDGE second trigger this power cycle -- no reboot, staying up");
		(void)bsf_v45_flash_persist(1u);
		return;
	}
	if (v45_budget_take != NULL && !v45_budget_take(BSF_V45_REBOOT_OWNER)) {
		LOG_ERR("V45 WEDGE reboot budget already spent -- corpse retained");
		return;
	}
	bsf_v45_core.reboot_taken = 1u;
	bsf_v45_core.reboot_owner = BSF_V45_REBOOT_OWNER;

	/*
	 * Jitter as a DELAYED WORK ITEM, not k_sleep(). Sleeping here would park
	 * the system workqueue for up to 4 s, and telemetry_work_handler() --
	 * the watchdog feed -- is on that same queue. Rescheduling keeps the
	 * queue running and reaches the same place.
	 */
	k_work_reschedule(&v45_reboot_work,
			  K_MSEC(bsf_v45_reboot_jitter_ms(env.node_identity)));
}

void bsf_v45_force(void)
{
	atomic_set(&v45_force_trigger, 1);
}

void bsf_v45_connection_epoch_changed(uint32_t epoch, uint32_t now_ms)
{
	bsf_v45_detector_reset(&v45_det, epoch, now_ms);
}

void bsf_v45_init(struct bsf_stall_ring *ring, struct k_spinlock *ring_lock,
		  bool (*budget_take)(uint32_t owner))
{
	struct bsf_v45_env env;

	v45_ring = ring;
	v45_ring_lock = ring_lock;
	v45_budget_take = budget_take;

	/*
	 * A retained corpse from before the reset is the normal case here, not
	 * an error: that is the whole point of the cold reboot. Validate it and
	 * keep it for collection; anything that fails validation is cold-boot
	 * garbage in `.noinit` and is cleared rather than reinterpreted.
	 */
	if (bsf_v45_core_validate()) {
		v45_corpse_present = true;
		bsf_v45_frozen = 1u;
		LOG_ERR("V45 CORPSE RECOVERED seq=%u cause=%u uptime_ms=%u flash_slot=%u",
			bsf_v45_core.corpse_seq, bsf_v45_core.trigger_cause,
			bsf_v45_core.uptime_ms, bsf_v45_core.flash_slot);
	} else {
		memset(&bsf_v45_core, 0, sizeof(bsf_v45_core));
		memset(bsf_v45_bank, 0, sizeof(bsf_v45_bank));
		memset(bsf_v45_ch, 0, sizeof(bsf_v45_ch));
		v45_corpse_present = false;
		bsf_v45_frozen = 0u;
	}

	memset(&bsf_v45_cnt, 0, sizeof(bsf_v45_cnt));
	v45_find_threads();

	bsf_v45_env_get(&env);
	bsf_v45_detector_reset(&v45_det, env.epoch, k_uptime_get_32());
	k_work_reschedule(&v45_monitor_work, K_MSEC(BSF_V45_MONITOR_TICK_MS));

	LOG_INF("V45 detector armed-capable tick=%u freeze=%u ch=%u core=%u image=%u",
		BSF_V45_MONITOR_TICK_MS, BSF_V45_FREEZE_MS,
		(unsigned int)BSF_V45_CH__COUNT,
		(unsigned int)sizeof(bsf_v45_core_t),
		(unsigned int)bsf_v45_image_len());
}
