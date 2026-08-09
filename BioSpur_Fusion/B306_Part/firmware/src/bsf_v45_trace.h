/*
 * bsf_v45_trace.h -- four context-correct trace channels (v45).
 *
 * Shared between the application and PATCHED copies of the NCS v2.8.0
 * Bluetooth host and SoftDevice Controller HCI driver. Placed on the global
 * Zephyr include path by the application's CMakeLists, so a patched SDK file
 * can `#include <bsf_v45_trace.h>` with no relative path.
 *
 * WHY FOUR CHANNELS AND NOT ONE
 * -----------------------------
 * v44 had ONE global {stage, seq, cycles, arg}. The context audit (item 8)
 * proved at least three distinct threads write it: `bt_conn_tx_notify()` is
 * called from the BT RX workqueue, from MPSL Work (via the inline NCP handler)
 * and from the system workqueue, and `bt_att_chan_create_pdu()` is written by
 * the notify worker and the BT RX workqueue. Two of those are precisely the
 * paths v45 exists to read. A channel with more than one writer cannot answer
 * "where is THIS thread parked" -- it answers "where was the last thread to
 * write, whichever that was", which is how v44 came to be structurally blind.
 *
 * So: one channel per thread, and the thread identity is CHECKED on every
 * write. A foreign write is counted, the offending TID is latched once, and
 * the write is DROPPED -- the invariant is enforced, not merely observed. A
 * contaminated channel that keeps accepting writes is exactly the failure v44
 * shipped; a channel that refuses them stays readable and tells you it was
 * attacked.
 *
 * COST CONTRACT (brief section 1, law 7)
 * --------------------------------------
 * A marker may: write RAM, use aligned 32-bit atomics, compare k_current_get().
 * A marker may NOT: log, allocate, sleep, take a mutex, submit work, touch
 * flash. Every field is a single aligned store; `seq` is published LAST, so a
 * reader that sees a new `seq` is guaranteed to see the payload that belongs
 * to it (law 8).
 *
 * NEUTRALISATION
 * --------------
 * Everything is gated on CONFIG_BSF_V45_TRACE (declared in the application's
 * own Kconfig, default n) AND on this header being reachable. An unrelated
 * project built against the same shared SDK sees neither, so every macro
 * collapses to `((void)0)` and the patched files compile unchanged.
 */
#ifndef BSF_V45_TRACE_H_
#define BSF_V45_TRACE_H_

#include <stdint.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ */
/* Channels                                                            */
/* ------------------------------------------------------------------ */

enum bsf_v45_channel {
	BSF_V45_CH_MPSL_RX    = 0,  /* mpsl_work_q.thread, "MPSL Work"      */
	BSF_V45_CH_BT_RX      = 1,  /* bt_workq.thread, "BT RX WQ"          */
	BSF_V45_CH_TX_WORK    = 2,  /* k_sys_work_q.thread, "sysworkq"      */
	BSF_V45_CH_APP_NOTIFY = 3,  /* notify_worker_thread_id              */
	BSF_V45_CH__COUNT     = 4
};

/*
 * Stage IDs. ONE global enum across all four channels so the decoder maps by
 * value alone and a stage can never be ambiguous between channels.
 *
 * APPEND ONLY. Never renumber: a decoded corpse carries the numeric value, and
 * renumbering silently reinterprets every corpse ever taken. This is the same
 * rule bsf_bt_stage.h states, and it is why v44's enums are still intact.
 */
enum bsf_v45_stage {
	BSF_V45_STAGE_IDLE                  = 0,

	/* --- BSF_V45_CH_MPSL_RX : the inlet ------------------------------ */
	BSF_V45_MPSL_WORK_ENTER             = 1,
	BSF_V45_MPSL_WORK_EXIT              = 2,
	BSF_V45_MSG_GET_BEFORE              = 3,
	BSF_V45_MSG_GET_AFTER               = 4,   /* arg0 = msg_type|err<<16 */
	BSF_V45_EVT_ALLOC_BEFORE            = 5,   /* arg0 = evt<<8|pool_class */
	BSF_V45_EVT_ALLOC_AFTER             = 6,   /* arg1 = buf ptr           */
	BSF_V45_ACL_ALLOC_BEFORE            = 7,   /* arg0 = handle<<16|len    */
	BSF_V45_ACL_ALLOC_AFTER             = 8,   /* arg1 = buf ptr           */
	BSF_V45_RECV_FUNC_BEFORE            = 9,
	BSF_V45_RECV_FUNC_AFTER             = 10,
	/* --- BSF_V45_CH_MPSL_RX : the priority arm, same channel --------- */
	BSF_V45_PRIO_EVENT_ENTER            = 11,  /* arg0 = evt code          */
	BSF_V45_PRIO_EVENT_EXIT             = 12,
	BSF_V45_NCP_ENTER                   = 13,  /* arg0 = num_handles       */
	BSF_V45_NCP_EXIT                    = 14,  /* arg0 = packets credited  */
	BSF_V45_DISCONN_PRIO_ENTER          = 15,
	BSF_V45_DISCONN_PRIO_EXIT           = 16,
	BSF_V45_CMD_COMPLETE_ENTER          = 17,
	BSF_V45_CMD_COMPLETE_EXIT           = 18,
	BSF_V45_CMD_STATUS_ENTER            = 19,
	BSF_V45_CMD_STATUS_EXIT             = 20,

	/* --- BSF_V45_CH_BT_RX -------------------------------------------- */
	BSF_V45_RX_WORK_ENTER               = 21,  /* arg0 = buf type          */
	BSF_V45_RX_WORK_EXIT                = 22,
	BSF_V45_RX_ACL_ENTER                = 23,
	BSF_V45_RX_ACL_EXIT                 = 24,
	BSF_V45_RX_NORMAL_EVENT_ENTER       = 25,  /* arg0 = evt code          */
	BSF_V45_RX_NORMAL_EVENT_EXIT        = 26,
	BSF_V45_DISCONN_NORMAL_ENTER        = 27,
	BSF_V45_DISCONN_NORMAL_EXIT         = 28,
	/*
	 * arg0 of TX_NOTIFY_WAIT_ENTER is the ADDRESS of the k_work_sync object
	 * this thread is about to flush on. Section 4 exists to equate a later
	 * `pended_on` with it: that is what turns "the BT RX WQ is blocked" into
	 * "the BT RX WQ is blocked inside k_work_flush of tx_complete_work",
	 * which is a different verdict.
	 */
	BSF_V45_TX_NOTIFY_WAIT_ENTER        = 29,
	BSF_V45_TX_NOTIFY_WAIT_EXIT         = 30,

	/* --- BSF_V45_CH_TX_WORK ------------------------------------------ */
	BSF_V45_TX_WORK_ENTER               = 31,
	BSF_V45_TX_WORK_EXIT                = 32,
	BSF_V45_TX_NOTIFY_PROC_ENTER        = 33,
	BSF_V45_TX_NOTIFY_PROC_EXIT         = 34,  /* arg0 = entries drained   */
	BSF_V45_TX_CB_BEFORE                = 35,
	BSF_V45_TX_CB_AFTER                 = 36,

	/* --- BSF_V45_CH_APP_NOTIFY --------------------------------------- */
	BSF_V45_NOTIFY_ENTER                = 37,  /* arg0=stream<<16|len      */
	BSF_V45_NOTIFY_EXIT                 = 38,  /* arg0=rc, arg1=duration_us*/

	/* --- R4: the fatal-disconnect path, BSF_V45_CH_TX_WORK ------------
	 *
	 * conn.c's tx_processor() runs on the system workqueue
	 * (DATAFLOW_MAP.md section 2), which is the context CH_TX_WORK owns, so
	 * these two are single-writer by construction like everything else in
	 * that channel.
	 *
	 * SEND_FAIL is the ORIGIN and FATAL_DISCONNECT is the CONSEQUENCE, and
	 * they are separate stages on purpose: "the link was torn down because a
	 * send failed" and "the send failed because X" are different facts, and
	 * only the second one is actionable.
	 */
	BSF_V45_TX_SEND_FAIL                = 39,  /* arg0=err, arg1=site id   */
	BSF_V45_TX_FATAL_DISCONNECT         = 40,  /* arg0=err, arg1=uptime_ms */

	BSF_V45_STAGE__COUNT
};

/* Pool classes carried in EVT_ALLOC arg0, so the decoder needs no hash. */
#define BSF_V45_POOL_CLASS_SYNC_EVT     1u
#define BSF_V45_POOL_CLASS_DISCARDABLE  2u
#define BSF_V45_POOL_CLASS_HCI_RX       3u

/* ------------------------------------------------------------------ */
/* Per-channel storage                                                 */
/* ------------------------------------------------------------------ */

#define BSF_V45_TRACE_ENTRIES 128u   /* power of two; 128 * 16 B = 2048 B */

struct bsf_v45_trace_entry {
	uint32_t cycles;
	uint16_t stage;
	uint8_t  channel;
	uint8_t  flags;
	uint32_t arg0;
	uint32_t arg1;
};

/*
 * One of these per channel. Lives in `.noinit` so a capture freezes it IN
 * PLACE -- no copy, no second 8 KB of RAM, and nothing to get out of step with
 * the live data. `sys_reboot()`/NVIC_SystemReset retains it; a brownout does
 * not, which is what section 9 is about.
 */
struct bsf_v45_channel_state {
	/* --- published payload, written before `seq` (law 8) --- */
	uint32_t stage_cycles;
	uint32_t arg0;
	uint32_t arg1;
	uint16_t stage;
	uint16_t pad0;
	uint32_t seq;               /* monotonic; published LAST            */

	/* --- bookkeeping --- */
	uint32_t trace_head;        /* monotonic; index = head & mask       */
	uint32_t last_enter_ms;
	uint32_t last_exit_ms;
	uint32_t enter_total;
	uint32_t exit_total;

	/* --- single-writer enforcement --- */
	uint32_t writer_tid;            /* claimed by the first writer      */
	uint32_t writer_mismatch_count;
	uint32_t first_offending_tid;

	struct bsf_v45_trace_entry trace[BSF_V45_TRACE_ENTRIES];
};

/* Defined once, in bsf_v45_trace.c. Indexed by enum bsf_v45_channel. */
extern struct bsf_v45_channel_state bsf_v45_ch[BSF_V45_CH__COUNT];

/*
 * Freeze flag. Set by the capture routine BEFORE it reads anything, cleared
 * only by the collection ACK. While set, every marker returns immediately, so
 * the four channels and their traces are a consistent still image rather than
 * a set that keeps moving while it is being copied.
 *
 * Deliberately a plain volatile word, not an atomic: it is written by exactly
 * one context (the detector on the system workqueue) and read by all, and a
 * single aligned 32-bit load is atomic on Cortex-M.
 */
extern volatile uint32_t bsf_v45_frozen;

/* ------------------------------------------------------------------ */
/* Global atomics (law 6: counters are global atomics, any context;    */
/* stage machines are single-writer. Do not confuse the two.)          */
/* ------------------------------------------------------------------ */

struct bsf_v45_counters {
	/* MPSL Work inlet */
	atomic_t mpsl_work_enter;
	atomic_t mpsl_work_exit;
	atomic_t msg_get_ok;
	/* v46. Times the HCI RX path hit -ENOBUFS and RETAINED the fetched
	 * message instead of blocking on K_FOREVER. A non-zero value is the
	 * fix doing its job; the old code could only have deadlocked here. */
	atomic_t rx_retained;
	atomic_t evt_alloc_enter;
	atomic_t evt_alloc_exit;
	atomic_t acl_alloc_enter;
	atomic_t acl_alloc_exit;
	atomic_t recv_func_enter;
	atomic_t recv_func_exit;
	atomic_t ncp_event_count;
	/*
	 * THE COMPLETION WATERMARK. One per packet the controller confirmed on
	 * our connection handle, incremented in the per-packet body of
	 * hci_num_completed_packets() (hci_core.c:609-635). This is the only
	 * COMPLETION-stage counter that has ever existed in this system --
	 * COUNTER_SEMANTICS.md said so in bold and asked for it.
	 */
	atomic_t ncp_packet_total;
	atomic_t last_msg_type;
	atomic_t last_evt_code;
	atomic_t last_buf_ptr;

	/* BT RX WQ */
	atomic_t rx_queue_put;      /* incremented on the MPSL side          */
	atomic_t rx_work_enter;
	atomic_t rx_work_exit;
	atomic_t rx_acl;
	atomic_t rx_event;
	atomic_t disconnect_normal;

	/* system WQ / completion consumer */
	atomic_t tx_work_submit;    /* incremented by the SUBMITTER's context */
	atomic_t tx_work_enter;
	atomic_t tx_work_exit;
	atomic_t tx_entries_drained;
	atomic_t tx_notify_runs;
	atomic_t tx_cb_calls;

	/* --- R4 ---------------------------------------------------------- */
	atomic_t conn_fatal_disconnects;  /* conn.c tx_processor tore the link */
	atomic_t send_fail_emsgsize;      /* send_buf: buf->len == 0           */
	atomic_t send_fail_eio;           /* send_buf: bt_buf_has_view         */
	atomic_t send_fail_enomem;        /* send_buf: no controller buf / tx  */
	atomic_t notify_notconn_max;      /* longest -ENOTCONN streak seen     */

	/*
	 * Shadow counters for conn->tx_pending / tx_complete (law 5). The audit
	 * found three UNLOCKED mutation contexts for tx_pending, so walking it
	 * from a corpse routine is a data race by construction. These are
	 * maintained at the real mutation sites instead; depth = added - removed.
	 */
	atomic_t tx_pending_added;
	atomic_t tx_pending_removed;
	atomic_t tx_complete_added;
	atomic_t tx_complete_drained;

	/* app notify path */
	atomic_t notify_enter_total;
	atomic_t notify_exit_total;  /* the OTHER watermark of section 5 */
};

extern struct bsf_v45_counters bsf_v45_cnt;

/*
 * R4/A2 -- WHICH LINE RELEASED THE CONNECTION.
 *
 * bt_conn_set_state() is reached from several threads, so marking inside it
 * would break the single-writer rule the channels depend on. The call SITES are
 * marked instead, and they record into plain atomics/stores rather than a trace
 * channel, which is context-safe from any thread.
 *
 * Site ids are assigned in bsf_v45_conn_sites.h and must be append-only: a
 * corpse decoded with renumbered sites names the wrong line, which is worse
 * than naming none.
 */
/* 32, not the 23 currently used: headroom so an SDK upgrade that adds a
 * call site does not force a schema bump. */
#define BSF_V45_CONN_SITE__MAX 32u

struct bsf_v45_conn_release {
	uint32_t uptime_ms;   /* when the LAST transition happened            */
	uint16_t total;       /* transitions recorded, saturating             */
	uint8_t  site;        /* BSF_V45_CONN_SITE_*                          */
	uint8_t  old_state;
	uint8_t  new_state;
	uint8_t  pad[3];
};

extern struct bsf_v45_conn_release bsf_v45_conn_rel;
extern uint8_t bsf_v45_conn_site_count[BSF_V45_CONN_SITE__MAX];

/* One word stored plus one saturating counter. No logging, any context. */
static inline void bsf_v45_conn_state_note(uint8_t site, uint8_t old_state,
					   uint8_t new_state)
{
	if (site < BSF_V45_CONN_SITE__MAX) {
		if (bsf_v45_conn_site_count[site] != 0xffu) {
			bsf_v45_conn_site_count[site]++;
		}
	}
	bsf_v45_conn_rel.site = site;
	bsf_v45_conn_rel.old_state = old_state;
	bsf_v45_conn_rel.new_state = new_state;
	bsf_v45_conn_rel.uptime_ms = k_uptime_get_32();
	if (bsf_v45_conn_rel.total != 0xffffu) {
		bsf_v45_conn_rel.total++;
	}
}

/* ------------------------------------------------------------------ */
/* The marker                                                          */
/* ------------------------------------------------------------------ */

#if defined(CONFIG_BSF_V45_TRACE) && (CONFIG_BSF_V45_TRACE == 1)

/*
 * Single-writer marker.
 *
 * Order of operations, and why:
 *   1. bail if frozen           -- a capture in progress must see a still image
 *   2. claim or check the TID   -- law 2, enforced rather than observed
 *   3. write the ring slot      -- payload first
 *   4. publish stage/arg/cycles -- payload before seq
 *   5. publish seq LAST         -- law 8
 *
 * `writer_tid` is claimed by whichever thread writes the channel first. That
 * is correct by construction here: each channel's first write happens at the
 * one call site on the one thread that owns it. A second thread arriving later
 * is exactly the contamination this check exists to catch.
 */
static inline void bsf_v45_mark(uint8_t channel, uint16_t stage,
				uint32_t arg0, uint32_t arg1)
{
	struct bsf_v45_channel_state *c;
	struct bsf_v45_trace_entry *slot;
	uint32_t self;
	uint32_t now;
	uint32_t head;

	if (bsf_v45_frozen != 0u) {
		return;
	}
	if (channel >= (uint8_t)BSF_V45_CH__COUNT) {
		return;
	}
	c = &bsf_v45_ch[channel];
	self = (uint32_t)(uintptr_t)k_current_get();

	if (c->writer_tid == 0u) {
		c->writer_tid = self;
	} else if (c->writer_tid != self) {
		/* Count it, latch the first offender, drop the write. Never log,
		 * never reset: this is the hot path. */
		c->writer_mismatch_count++;
		if (c->first_offending_tid == 0u) {
			c->first_offending_tid = self;
		}
		return;
	}

	now = k_cycle_get_32();
	head = c->trace_head;
	slot = &c->trace[head & (BSF_V45_TRACE_ENTRIES - 1u)];
	slot->cycles = now;
	slot->stage = stage;
	slot->channel = channel;
	slot->flags = 0u;
	slot->arg0 = arg0;
	slot->arg1 = arg1;
	c->trace_head = head + 1u;

	c->stage_cycles = now;
	c->arg0 = arg0;
	c->arg1 = arg1;
	c->stage = stage;
	__DMB();
	c->seq++;            /* published last */
}

/*
 * ENTER/EXIT variants also stamp a millisecond uptime and bump the per-channel
 * totals, so "this channel entered N times and exited N-1 times, N-1 ms ago"
 * is answerable without decoding the ring.
 */
static inline void bsf_v45_mark_enter(uint8_t channel, uint16_t stage,
				      uint32_t arg0, uint32_t arg1)
{
	if (bsf_v45_frozen == 0u && channel < (uint8_t)BSF_V45_CH__COUNT) {
		bsf_v45_ch[channel].last_enter_ms = k_uptime_get_32();
		bsf_v45_ch[channel].enter_total++;
	}
	bsf_v45_mark(channel, stage, arg0, arg1);
}

static inline void bsf_v45_mark_exit(uint8_t channel, uint16_t stage,
				     uint32_t arg0, uint32_t arg1)
{
	if (bsf_v45_frozen == 0u && channel < (uint8_t)BSF_V45_CH__COUNT) {
		bsf_v45_ch[channel].last_exit_ms = k_uptime_get_32();
		bsf_v45_ch[channel].exit_total++;
	}
	bsf_v45_mark(channel, stage, arg0, arg1);
}

#define BSF_V45_MARK(ch, st)                 bsf_v45_mark((ch), (st), 0u, 0u)
#define BSF_V45_MARK_A(ch, st, a)            bsf_v45_mark((ch), (st), (uint32_t)(a), 0u)
#define BSF_V45_MARK_A2(ch, st, a, b)        bsf_v45_mark((ch), (st), (uint32_t)(a), (uint32_t)(b))
#define BSF_V45_ENTER(ch, st, a)             bsf_v45_mark_enter((ch), (st), (uint32_t)(a), 0u)
#define BSF_V45_ENTER2(ch, st, a, b)         bsf_v45_mark_enter((ch), (st), (uint32_t)(a), (uint32_t)(b))
#define BSF_V45_EXIT(ch, st, a)              bsf_v45_mark_exit((ch), (st), (uint32_t)(a), 0u)
#define BSF_V45_EXIT2(ch, st, a, b)          bsf_v45_mark_exit((ch), (st), (uint32_t)(a), (uint32_t)(b))
#define BSF_V45_INC(field)                   atomic_inc(&bsf_v45_cnt.field)
#define BSF_V45_ADD(field, n)                atomic_add(&bsf_v45_cnt.field, (atomic_val_t)(n))
#define BSF_V45_SET(field, v)                atomic_set(&bsf_v45_cnt.field, (atomic_val_t)(v))

#else  /* instrumentation neutralised for every other SDK consumer */

#define BSF_V45_MARK(ch, st)                 ((void)0)
#define BSF_V45_MARK_A(ch, st, a)            ((void)0)
#define BSF_V45_MARK_A2(ch, st, a, b)        ((void)0)
#define BSF_V45_ENTER(ch, st, a)             ((void)0)
#define BSF_V45_ENTER2(ch, st, a, b)         ((void)0)
#define BSF_V45_EXIT(ch, st, a)              ((void)0)
#define BSF_V45_EXIT2(ch, st, a, b)          ((void)0)
#define BSF_V45_INC(field)                   ((void)0)
#define BSF_V45_ADD(field, n)                ((void)0)
#define BSF_V45_SET(field, v)                ((void)0)

#endif /* CONFIG_BSF_V45_TRACE */

/* ------------------------------------------------------------------ */
/* Pool ownership -- section 6                                         */
/* ------------------------------------------------------------------ */

/*
 * Who last took the singleton sync_evt buffer. Updated only at the sparse
 * (~20/s) sync-event alloc / handler / free sites, so it costs nothing.
 * IGNORE A STALE OWNER WHEN ref == 0 -- the field is not cleared on free,
 * deliberately: "who held it last" is worth more than "nobody holds it".
 */
#define BSF_V45_OWNER_FREE_OR_UNKNOWN   0u
#define BSF_V45_OWNER_DRIVER_EVT_ALLOC  1u
#define BSF_V45_OWNER_PRIO_NCP          2u
#define BSF_V45_OWNER_PRIO_CMD_COMPLETE 3u
#define BSF_V45_OWNER_PRIO_CMD_STATUS   4u
#define BSF_V45_OWNER_PRIO_DISCONNECT   5u
/* hci_rx_pool per-buffer owners */
#define BSF_V45_OWNER_DRIVER            6u
#define BSF_V45_OWNER_PRIO_HANDLER      7u
#define BSF_V45_OWNER_RX_QUEUE          8u
#define BSF_V45_OWNER_BT_RX_ACTIVE      9u
#define BSF_V45_OWNER_CONN_RX_REASSY    10u
#define BSF_V45_OWNER_INJECTED          11u  /* fault-injection hook only */

struct bsf_v45_buf_entry {
	uint32_t ptr;
	uint16_t len;
	uint8_t  ref;
	uint8_t  owner;
	uint16_t code;      /* event code, or ACL handle */
	uint16_t reserved;
};

#define BSF_V45_HCI_RX_ENTRIES 10u

struct bsf_v45_pool_summary {
	uint32_t name_hash;
	uint16_t avail;
	uint16_t buf_count;
	uint16_t true_min_avail;    /* folded in at EVERY successful alloc */
	uint16_t pad;
	uint32_t alloc_attempts;
	uint32_t alloc_successes;
	uint32_t releases;
};

struct bsf_v45_pool_snapshot {
	struct bsf_v45_pool_summary sync_evt;
	struct bsf_v45_pool_summary hci_rx;
	struct bsf_v45_pool_summary att;
	struct bsf_v45_pool_summary acl_tx;
	struct bsf_v45_pool_summary hci_cmd;
	struct bsf_v45_pool_summary fragments;

	/* the one buffer of sync_evt_pool, in full */
	struct bsf_v45_buf_entry sync_evt_buf;
	uint8_t  sync_evt_last_owner;
	uint8_t  sync_evt_last_evt_code;
	uint8_t  hci_rx_entries;
	uint8_t  pad0;

	struct bsf_v45_buf_entry hci_rx_buf[BSF_V45_HCI_RX_ENTRIES];
};

/*
 * Implemented in the APPLICATION, not in the SDK.
 *
 * Every net_buf pool is a STRUCT_SECTION_ITERABLE carrying its own `name`,
 * `buf_count`, `avail_count`, `free` and `__bufs`, all in the public
 * <zephyr/net_buf.h>. So the whole snapshot is reachable from the app by name,
 * and buf.c needs no patch at all -- one fewer SDK file to keep in step, for
 * exactly zero loss of information.
 *
 * Pure reader; takes no lock and cannot block. Safe to call from the system
 * workqueue while another thread is parked inside an allocation on the very
 * pool being read -- which is the whole point.
 */
void bsf_v45_capture_pools(struct bsf_v45_pool_snapshot *out);

/* Owner hooks, called from the patched host/controller. */
void bsf_v45_sync_evt_set_owner(uint8_t owner, uint8_t evt_code);
void bsf_v45_hci_rx_set_owner(const void *buf, uint8_t owner, uint16_t code);

/*
 * Strong definition provided by the application; zephyr/lib/net_buf/buf.c
 * declares and defines a __weak no-op. Called after EVERY successful
 * allocation, which is what makes true_min_avail a real minimum (law 4).
 */
struct net_buf_pool;
void bsf_v45_net_buf_alloc_hook(struct net_buf_pool *pool, uint16_t avail);

/* Only `free_tx` needs the patched conn.c: it is a plain file-static k_fifo. */
uint32_t bsf_v45_free_tx_waitq(void);

/* Fault injection (section 12.3), compiled out of production. */
int  bsf_v45_sync_evt_leak(void);
int  bsf_v45_sync_evt_release(void);

/* ------------------------------------------------------------------ */
/* Connection shadow state -- section 6, from the patched conn.c       */
/* ------------------------------------------------------------------ */

struct bsf_v45_conn_snapshot {
	uint32_t conn_addr;
	uint32_t rx_ptr;
	uint16_t rx_len;
	uint16_t handle;
	uint8_t  state;
	uint8_t  err;
	uint8_t  role;
	uint8_t  valid;
	int32_t  tx_complete_busy;   /* k_work_busy_get(&conn->tx_complete_work) */
	int32_t  deferred_busy;
	uint32_t pkts_avail;
	uint32_t in_ll;
	/* wait-object addresses resolved inside the host, for section 4 */
	uint32_t tx_complete_work_addr;
};

void bsf_v45_capture_conn(struct bsf_v45_conn_snapshot *out);

/*
 * Wait-object addresses that only the host can resolve. Filled once at boot so
 * the decoder can print a NAME instead of a raw address when a thread's
 * `pended_on` matches one of them.
 */
struct bsf_v45_waitobj_table {
	uint32_t att_pool_free;
	uint32_t acl_tx_pool_free;
	uint32_t fragments_free;
	uint32_t hci_cmd_pool_free;
	uint32_t hci_rx_pool_free;
	uint32_t sync_evt_pool_free;
	uint32_t discardable_pool_free;
	uint32_t free_tx_queue;
};

void bsf_v45_capture_waitobjs(struct bsf_v45_waitobj_table *out);

#ifdef __cplusplus
}
#endif

#endif /* BSF_V45_TRACE_H_ */
