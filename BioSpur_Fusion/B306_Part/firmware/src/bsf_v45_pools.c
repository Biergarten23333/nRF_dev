/*
 * bsf_v45_pools.c -- pool and buffer-ownership instrumentation (section 6).
 *
 * WHY THIS IS IN THE APPLICATION AND NOT IN A PATCHED buf.c
 * ---------------------------------------------------------
 * Every net_buf pool is a STRUCT_SECTION_ITERABLE that carries its own `name`,
 * `buf_count`, `avail_count`, `free` and `__bufs` -- all in the public
 * <zephyr/net_buf.h>. So `sync_evt_pool`, `hci_rx_pool`, `att_pool`,
 * `acl_tx_pool`, `hci_cmd_pool`, `fragments` and `discardable_pool` are all
 * reachable BY NAME from here, complete with their per-buffer arrays. Patching
 * the host's buf.c would have bought nothing and added a file to keep in step.
 *
 * The one thing the SDK genuinely must provide is the allocation hook, because
 * design law 4 requires the low-water mark to be folded in at every successful
 * allocation rather than sampled -- and the only place that can happen is
 * inside net_buf_alloc_len(). That is a five-line __weak hook; the strong
 * definition is here.
 *
 * WHAT THE OWNER FIELDS ARE FOR
 * -----------------------------
 * `avail == 0` says a pool is empty. It does not say who is holding it, and
 * "who" is the entire question for the singleton sync_evt_pool: a corpse that
 * shows the inlet parked on that pool with ref == 1 and last_owner == PRIO_NCP
 * is a different verdict from the same corpse with last_owner ==
 * DRIVER_EVT_ALLOC. Ownership is recorded only at the sparse (~20/s) sync-event
 * and inbound-ACL sites, so it costs a store on a path that already does far
 * more work than that.
 */
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/net_buf.h>
#include <zephyr/sys/atomic.h>

#include "bsf_v45_trace.h"

/*
 * Low-water storage, indexed by iteration order over the net_buf_pool section.
 * Kept here rather than in struct net_buf_pool: adding a field to that struct
 * would change its layout for every consumer of this shared SDK, which is the
 * kind of blast radius a diagnostic must never have.
 */
#define BSF_V45_POOL_MAX 12u

static uint16_t v45_true_min_avail[BSF_V45_POOL_MAX];
static uint32_t v45_alloc_attempts[BSF_V45_POOL_MAX];
static uint32_t v45_alloc_successes[BSF_V45_POOL_MAX];
static bool     v45_pools_seeded;

/* Owner tracking. */
static uint8_t  v45_sync_evt_owner;
static uint8_t  v45_sync_evt_evt_code;

struct v45_rx_owner { uint32_t ptr; uint8_t owner; uint16_t code; };
static struct v45_rx_owner v45_rx_owner[BSF_V45_HCI_RX_ENTRIES];

static int v45_pool_index(const struct net_buf_pool *pool)
{
	int i = 0;

	STRUCT_SECTION_FOREACH(net_buf_pool, p) {
		if (p == pool) {
			return i;
		}
		if (++i >= (int)BSF_V45_POOL_MAX) {
			break;
		}
	}
	return -1;
}

static struct net_buf_pool *v45_pool_by_name(const char *name)
{
	STRUCT_SECTION_FOREACH(net_buf_pool, p) {
		if (p->name != NULL && strcmp(p->name, name) == 0) {
			return p;
		}
	}
	return NULL;
}

static void v45_seed_pools(void)
{
	int i = 0;

	STRUCT_SECTION_FOREACH(net_buf_pool, p) {
		if (i >= (int)BSF_V45_POOL_MAX) {
			break;
		}
		v45_true_min_avail[i] = (uint16_t)p->buf_count;
		i++;
	}
	v45_pools_seeded = true;
}

/*
 * THE law-4 hook. Called from zephyr/lib/net_buf/buf.c after every successful
 * allocation, with the post-decrement availability.
 *
 * Contract: this runs on whichever thread allocated -- MPSL Work, BT RX WQ, the
 * notify worker, the system workqueue. It therefore does the absolute minimum:
 * one comparison and, rarely, one store. No atomic RMW loop, no lock, no log.
 * A torn read of a uint16 cannot happen on Cortex-M, and the worst a race can
 * do is lose one update of a monotone minimum, which is a bounded and stated
 * error rather than a corrupted value.
 */
void bsf_v45_net_buf_alloc_hook(struct net_buf_pool *pool, uint16_t avail)
{
	int idx;

	if (!v45_pools_seeded) {
		v45_seed_pools();
	}
	idx = v45_pool_index(pool);
	if (idx < 0) {
		return;
	}
	v45_alloc_successes[idx]++;
	if (avail < v45_true_min_avail[idx]) {
		v45_true_min_avail[idx] = avail;
	}
}

void bsf_v45_sync_evt_set_owner(uint8_t owner, uint8_t evt_code)
{
	v45_sync_evt_owner = owner;
	v45_sync_evt_evt_code = evt_code;
}

void bsf_v45_hci_rx_set_owner(const void *buf, uint8_t owner, uint16_t code)
{
	uint32_t p = (uint32_t)(uintptr_t)buf;

	if (buf == NULL) {
		return;
	}
	for (uint8_t i = 0; i < BSF_V45_HCI_RX_ENTRIES; ++i) {
		if (v45_rx_owner[i].ptr == p || v45_rx_owner[i].ptr == 0u) {
			v45_rx_owner[i].ptr = p;
			v45_rx_owner[i].owner = owner;
			v45_rx_owner[i].code = code;
			return;
		}
	}
}

static uint8_t v45_rx_owner_of(const struct net_buf *b, uint16_t *code)
{
	uint32_t p = (uint32_t)(uintptr_t)b;

	for (uint8_t i = 0; i < BSF_V45_HCI_RX_ENTRIES; ++i) {
		if (v45_rx_owner[i].ptr == p) {
			*code = v45_rx_owner[i].code;
			return v45_rx_owner[i].owner;
		}
	}
	*code = 0u;
	return BSF_V45_OWNER_FREE_OR_UNKNOWN;
}

static void v45_fill_summary(struct bsf_v45_pool_summary *s, const char *name)
{
	struct net_buf_pool *p = v45_pool_by_name(name);
	int idx;

	memset(s, 0, sizeof(*s));
	if (p == NULL) {
		return;
	}
	idx = v45_pool_index(p);
	s->name_hash = 0u;   /* filled by the caller from the same table */
	s->avail = (uint16_t)atomic_get(&p->avail_count);
	s->buf_count = (uint16_t)p->buf_count;
	if (idx >= 0) {
		s->true_min_avail = v45_pools_seeded ?
			v45_true_min_avail[idx] : (uint16_t)p->buf_count;
		s->alloc_attempts = v45_alloc_attempts[idx];
		s->alloc_successes = v45_alloc_successes[idx];
		s->releases = v45_alloc_successes[idx] >=
			      (uint32_t)(p->buf_count - s->avail) ?
			v45_alloc_successes[idx] -
				(uint32_t)(p->buf_count - s->avail) : 0u;
	}
}

/* FNV-1a/32, identical to main.c's pool_name_hash() and to the decoder's. */
static uint32_t v45_fnv1a(const char *s)
{
	uint32_t h = 2166136261u;

	while (*s != '\0') {
		h ^= (uint8_t)(*s++);
		h *= 16777619u;
	}
	return h;
}

static void v45_named(struct bsf_v45_pool_summary *s, const char *name)
{
	v45_fill_summary(s, name);
	s->name_hash = v45_fnv1a(name);
}

void bsf_v45_capture_pools(struct bsf_v45_pool_snapshot *out)
{
	struct net_buf_pool *sync_evt;
	struct net_buf_pool *hci_rx;

	if (out == NULL) {
		return;
	}
	memset(out, 0, sizeof(*out));

	v45_named(&out->sync_evt, "sync_evt_pool");
	v45_named(&out->hci_rx, "hci_rx_pool");
	v45_named(&out->att, "att_pool");
	v45_named(&out->acl_tx, "acl_tx_pool");
	v45_named(&out->hci_cmd, "hci_cmd_pool");
	v45_named(&out->fragments, "fragments");

	/*
	 * The singleton, in full. CONTEXT_AUDIT item 3 proved this pool has
	 * EXACTLY one buffer and that Number Of Completed Packets, Command
	 * Complete and Command Status all route through it. If the inlet is
	 * parked anywhere, this is the first place to look.
	 */
	sync_evt = v45_pool_by_name("sync_evt_pool");
	if (sync_evt != NULL && sync_evt->buf_count > 0) {
		const struct net_buf *b = &sync_evt->__bufs[0];

		out->sync_evt_buf.ptr = (uint32_t)(uintptr_t)b;
		out->sync_evt_buf.ref = b->ref;
		out->sync_evt_buf.len = b->len;
		out->sync_evt_buf.owner = v45_sync_evt_owner;
		out->sync_evt_buf.code = v45_sync_evt_evt_code;
	}
	/*
	 * The owner field is deliberately NOT cleared on free. "Who held it
	 * last" is worth more than "nobody holds it" -- but a stale owner must
	 * be IGNORED when ref == 0, and the decoder is told so rather than
	 * being left to work it out.
	 */
	out->sync_evt_last_owner = v45_sync_evt_owner;
	out->sync_evt_last_evt_code = v45_sync_evt_evt_code;

	hci_rx = v45_pool_by_name("hci_rx_pool");
	if (hci_rx != NULL) {
		uint8_t n = (uint8_t)MIN((uint32_t)hci_rx->buf_count,
					 (uint32_t)BSF_V45_HCI_RX_ENTRIES);

		for (uint8_t i = 0; i < n; ++i) {
			const struct net_buf *b = &hci_rx->__bufs[i];
			uint16_t code = 0u;

			out->hci_rx_buf[i].ptr = (uint32_t)(uintptr_t)b;
			out->hci_rx_buf[i].ref = b->ref;
			out->hci_rx_buf[i].len = b->len;
			out->hci_rx_buf[i].owner = v45_rx_owner_of(b, &code);
			out->hci_rx_buf[i].code = code;
		}
		out->hci_rx_entries = n;
	}
}

void bsf_v45_capture_waitobjs(struct bsf_v45_waitobj_table *out)
{
	static const struct { const char *name; size_t off; } map[] = {
		{ "att_pool",         offsetof(struct bsf_v45_waitobj_table, att_pool_free) },
		{ "acl_tx_pool",      offsetof(struct bsf_v45_waitobj_table, acl_tx_pool_free) },
		{ "fragments",        offsetof(struct bsf_v45_waitobj_table, fragments_free) },
		{ "hci_cmd_pool",     offsetof(struct bsf_v45_waitobj_table, hci_cmd_pool_free) },
		{ "hci_rx_pool",      offsetof(struct bsf_v45_waitobj_table, hci_rx_pool_free) },
		{ "sync_evt_pool",    offsetof(struct bsf_v45_waitobj_table, sync_evt_pool_free) },
		{ "discardable_pool", offsetof(struct bsf_v45_waitobj_table, discardable_pool_free) },
	};

	if (out == NULL) {
		return;
	}
	memset(out, 0, sizeof(*out));
	for (size_t i = 0; i < ARRAY_SIZE(map); ++i) {
		struct net_buf_pool *p = v45_pool_by_name(map[i].name);
		uint32_t addr = 0u;

		if (p != NULL) {
			/*
			 * net_buf blocks in k_lifo_get(&pool->free, timeout),
			 * and k_queue_get pends on &queue->wait_q. So this,
			 * exactly, is what a blocked thread's `pended_on` will
			 * equal -- computed, never guessed by the decoder.
			 */
			addr = (uint32_t)(uintptr_t)&p->free._queue.wait_q;
		}
		*(uint32_t *)((uint8_t *)out + map[i].off) = addr;
	}
	out->free_tx_queue = bsf_v45_free_tx_waitq();
}

/* ------------------------------------------------------------------ */
/* Fault injection -- section 12.3. Validation builds only.            */
/* ------------------------------------------------------------------ */

#if defined(BSF_V45_FAULT_INJECT) && (BSF_V45_FAULT_INJECT == 1)

static struct net_buf *v45_leaked_sync_evt;

/*
 * Take the ONE sync_evt buffer and never give it back.
 *
 * Expected consequence if candidate 1 is right: the full phenotype reproduces
 * -- Link Layer alive, all export dead within ~0.3 s, ATT accepted and never
 * answered, no re-advertise after a disconnect, watchdog still fed -- and the
 * corpse shows the receive thread pended_on = sync_evt_pool free wait_q,
 * avail 0, ref 1, with INJECTED as last owner.
 *
 * SCOPE, HONESTLY: this proves the starvation -> phenotype consequence chain.
 * It does NOT prove that real wedges begin this way. Those are different
 * claims and the run brief must not conflate them.
 */
int bsf_v45_sync_evt_leak(void)
{
	struct net_buf_pool *p = v45_pool_by_name("sync_evt_pool");

	if (p == NULL) {
		return -ENODEV;
	}
	if (v45_leaked_sync_evt != NULL) {
		return -EALREADY;
	}
	v45_leaked_sync_evt = net_buf_alloc(p, K_NO_WAIT);
	if (v45_leaked_sync_evt == NULL) {
		return -ENOMEM;
	}
	bsf_v45_sync_evt_set_owner(BSF_V45_OWNER_INJECTED, 0xffu);
	return 0;
}

int bsf_v45_sync_evt_release(void)
{
	if (v45_leaked_sync_evt == NULL) {
		return -EALREADY;
	}
	net_buf_unref(v45_leaked_sync_evt);
	v45_leaked_sync_evt = NULL;
	bsf_v45_sync_evt_set_owner(BSF_V45_OWNER_FREE_OR_UNKNOWN, 0u);
	return 0;
}

#else

int bsf_v45_sync_evt_leak(void) { return -ENOTSUP; }
int bsf_v45_sync_evt_release(void) { return -ENOTSUP; }

#endif /* BSF_V45_FAULT_INJECT */
