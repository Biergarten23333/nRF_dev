/*
 * BT RX stage instrumentation -- v43 (batch v43_selfcapture_20260807).
 *
 * This header is shared between the application and a PATCHED copy of the
 * NCS v2.8.0 Bluetooth host (`subsys/bluetooth/host/conn.c`). It is placed on
 * the global Zephyr include path by the application's CMakeLists, so the
 * patched host file can `#include <bsf_bt_stage.h>` without a relative path.
 *
 * WHY THIS EXISTS
 * ---------------
 * The B306 wedges with its BLE link alive, its tag transmitting, and no UWB or
 * IMU export. J1 and the BT-RX wedge audit localised it: `disconnected()` is
 * dispatched from the SYSTEM workqueue, which is provably alive throughout the
 * failure (it feeds the watchdog and no board ever reset), yet the callback
 * never runs. So `bt_conn_set_state(DISCONNECTED)` never reached its final
 *
 *     k_work_reschedule(&conn->deferred_work, K_NO_WAIT);
 *
 * The three operations there are, in order:
 *
 *     bt_conn_tx_notify(conn, true)   <- the only one that can block
 *     bt_conn_reset_rx_state(conn)
 *     k_work_reschedule(...)          <- never reached
 *
 * and `bt_conn_recv()` calls the same `bt_conn_tx_notify(conn, true)` before
 * processing EVERY incoming ACL packet, so a wedge there explains the export
 * stall as well as the disconnect failure. One wedge point, all four symptoms.
 *
 * This instrumentation records which of those operations the BT RX workqueue
 * thread is sitting in, so a sealed board can answer the question itself.
 *
 * COST CONTRACT (brief section 5)
 * -------------------------------
 * On the normal path a transition may only write RAM. No LOG_*, no allocation,
 * no sleep, no mutex, no semaphore, no work submission, no flash. The marker is
 * a `static inline` over plain 32-bit stores, which are single-copy atomic on
 * Cortex-M. There is exactly ONE writer -- the BT RX workqueue thread -- and the
 * monitor thread is a pure reader, so no lock is required in either direction.
 * The instrumented build must behave like v41.
 */
#ifndef BSF_BT_STAGE_H_
#define BSF_BT_STAGE_H_

#include <stdint.h>
#include <zephyr/kernel.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Stage IDs. The order matters only for readability; the decoder maps by value,
 * so values must never be renumbered once an image has shipped.
 *
 * TX_NOTIFY_DIRECT is not in the brief's minimum list. It is the branch
 * `bt_conn_tx_notify()` takes when it is already running on the TX-notify
 * workqueue thread and therefore calls `tx_notify_process()` inline instead of
 * submitting and flushing. Instrumenting it separately is what stops that
 * (harmless, non-blocking) path from being mistaken for a flush wedge.
 */
enum bsf_bt_stage {
	BSF_BT_STAGE_IDLE                       = 0,
	BSF_BT_STAGE_CONN_RECV_ENTER            = 1,
	BSF_BT_STAGE_TX_NOTIFY_ENTER            = 2,
	BSF_BT_STAGE_TX_NOTIFY_BEFORE_SUBMIT    = 3,
	BSF_BT_STAGE_TX_NOTIFY_AFTER_SUBMIT     = 4,
	BSF_BT_STAGE_TX_NOTIFY_BEFORE_FLUSH     = 5,
	BSF_BT_STAGE_TX_NOTIFY_AFTER_FLUSH      = 6,
	BSF_BT_STAGE_TX_NOTIFY_EXIT             = 7,
	BSF_BT_STAGE_RESET_RX_BEFORE            = 8,
	BSF_BT_STAGE_RESET_RX_AFTER             = 9,
	BSF_BT_STAGE_DEFERRED_RESCHEDULE_BEFORE = 10,
	BSF_BT_STAGE_DEFERRED_RESCHEDULE_AFTER  = 11,
	BSF_BT_STAGE_CONN_RECV_EXIT             = 12,
	BSF_BT_STAGE_TX_NOTIFY_DIRECT           = 13,
	/*
	 * v44. APPENDED, never renumbered: decoded corpses carry the numeric
	 * value, so renumbering silently reinterprets every corpse ever taken.
	 */
	BSF_BT_STAGE_RX_WORK_ENTER              = 14,
	BSF_BT_STAGE_RX_WORK_EXIT               = 15,
	BSF_BT_STAGE_ACL_RECV_ENTER             = 16,
	BSF_BT_STAGE_ACL_RECV_EXIT              = 17,
	BSF_BT_STAGE_ATT_ALLOC_RESPONSE         = 18,
	BSF_BT_STAGE_ATT_ALLOC_FOREVER          = 19,
	BSF_BT_STAGE_ATT_ALLOC_DONE             = 20,
	BSF_BT_STAGE__COUNT
};

/*
 * TERMINAL stages -- v44 makes this PROVABLE instead of enumerated.
 *
 * `rx_work_handler()` in hci_core.c is the SINGLE entry point for everything
 * the BT RX workqueue thread ever does: ACL, HCI events and ISO all dispatch
 * from its one switch. v44 brackets that switch, so:
 *
 *     the BT RX WQ is quiescent  <=>  it is not inside rx_work_handler()
 *
 * That is a complete statement, not a list someone has to keep correct. Every
 * blocking primitive reachable from this thread -- the ATT allocations at
 * att.c:729/1191/1399/3059, the L2CAP signalling alloc at l2cap.c:464, and the
 * HCI command paths at hci_core.c:311/429/4721 -- is inside the bracket.
 *
 * WHY THE PREVIOUS LIST FAILED, in one line: TX_NOTIFY_EXIT was terminal, and
 * `bt_conn_recv()` calls tx_notify at its HEAD and then descends into
 * bt_acl_recv() with no further mark. The monitor read "quiescent" while the
 * thread sat in ATT, and a real wedge on BSF6C53 went uncaptured.
 *
 * The root error was classifying a stage BY NAME rather than by which call
 * sites can leave it as the last mark. Note the trap that catches: v44's own
 * audit found a THIRD caller of bt_conn_tx_notify() --
 * hci_num_completed_packets() at hci_core.c:608, on this same thread -- where
 * TX_NOTIFY_EXIT genuinely IS the last mark. Simply reclassifying it, as the
 * obvious fix suggested, would have false-positive-rebooted the whole fleet
 * within 5 s of any pause in traffic. It is safe now ONLY because
 * RX_WORK_EXIT always follows it.
 */
#define BSF_BT_STAGE_IS_QUIESCENT(s)                                          \
	((s) == BSF_BT_STAGE_IDLE ||                                          \
	 (s) == BSF_BT_STAGE_RX_WORK_EXIT)

/* Event codes, carried alongside the stage in the flight recorder. */
#define BSF_BT_EVENT_STAGE       0u   /* ordinary stage transition          */
#define BSF_BT_EVENT_DISCONNECT  1u   /* HCI disconnect state transition    */
#define BSF_BT_EVENT_MONITOR     2u   /* monitor observation                */

#define BSF_BT_TRACE_ENTRIES 128u     /* power of two; 128 * 12 B = 1536 B  */

struct bsf_bt_trace_entry {
	uint32_t cycles;
	uint16_t stage;
	uint16_t event;
	uint32_t arg;
};

/* Defined once, in bsf_bt_stage.c. */
extern struct bsf_bt_trace_entry bsf_bt_trace[BSF_BT_TRACE_ENTRIES];
extern volatile uint32_t bsf_bt_trace_head;   /* monotonic; index = head & mask */
extern volatile uint32_t bsf_bt_stage_seq;    /* monotonic transition counter   */
extern volatile uint32_t bsf_bt_stage_cycles; /* k_cycle_get_32() at entry      */
extern volatile uint32_t bsf_bt_stage_arg;
extern volatile uint16_t bsf_bt_stage_id;
extern volatile uint32_t bsf_bt_rx_thread;    /* &bt_workq.thread, published once */

/*
 * Per-stage maximum observed dwell, in cycles, updated as each stage is LEFT.
 *
 * This is what justifies the monitor's 5 s threshold with a measured margin
 * instead of an assumed one (brief sections 7 and 11). Sampling the age from
 * the 1 Hz monitor could not do it: healthy stages last microseconds, so a 1 Hz
 * sampler would almost never observe one in flight. Measuring on the transition
 * itself catches every occurrence.
 */
extern volatile uint32_t bsf_bt_stage_max[BSF_BT_STAGE__COUNT];

/*
 * Single-writer marker.
 *
 * Store order is deliberate: the payload (cycles, arg, stage) is published
 * BEFORE the sequence counter, so a reader that observes a new `seq` is
 * guaranteed to see the stage that belongs to it. The monitor only ever tests
 * `seq` for change and `cycles` for age, so a torn read is impossible -- every
 * field is a single aligned 32- or 16-bit store.
 */
static inline void bsf_bt_stage_mark(uint16_t stage, uint16_t event, uint32_t arg)
{
	uint32_t now = k_cycle_get_32();
	uint32_t head = bsf_bt_trace_head;
	struct bsf_bt_trace_entry *slot =
		&bsf_bt_trace[head & (BSF_BT_TRACE_ENTRIES - 1u)];
	uint16_t prev = bsf_bt_stage_id;
	uint32_t dwell = now - bsf_bt_stage_cycles;

	/* Close out the stage being left before publishing the new one. */
	if (prev < BSF_BT_STAGE__COUNT && dwell > bsf_bt_stage_max[prev]) {
		bsf_bt_stage_max[prev] = dwell;
	}

	slot->cycles = now;
	slot->stage = stage;
	slot->event = event;
	slot->arg = arg;
	bsf_bt_trace_head = head + 1u;

	bsf_bt_stage_cycles = now;
	bsf_bt_stage_arg = arg;
	bsf_bt_stage_id = stage;
	bsf_bt_stage_seq++;
}

#define BSF_BT_STAGE(s)          bsf_bt_stage_mark((s), BSF_BT_EVENT_STAGE, 0u)
#define BSF_BT_STAGE_A(s, a)     bsf_bt_stage_mark((s), BSF_BT_EVENT_STAGE, (uint32_t)(a))
#define BSF_BT_EVENT(s, e, a)    bsf_bt_stage_mark((s), (e), (uint32_t)(a))

/*
 * Connection fields the corpse needs. Every one of these lives inside
 * `struct bt_conn`, which is private to the host (`conn_internal.h`), so the
 * capture function is implemented INSIDE the patched conn.c where the struct is
 * complete. The application never sees the layout -- it only sees this struct.
 *
 * Read against the actual v2.8.0 layout, never against current Zephyr main.
 */
struct bsf_bt_corpse_conn {
	uint32_t conn_addr;
	uint32_t rx_ptr;
	uint16_t rx_len;
	uint16_t handle;
	uint8_t  state;
	uint8_t  err;
	uint8_t  type;
	uint8_t  role;
	int32_t  tx_complete_busy;   /* k_work_busy_get(&conn->tx_complete_work) */
	int32_t  deferred_busy;      /* k_work_delayable_busy_get(&deferred_work) */
	uint16_t tx_pending;         /* entries on conn->tx_pending              */
	uint16_t tx_complete;        /* entries on conn->tx_complete             */
	uint32_t pkts_avail;         /* k_sem_count_get(bt_conn_get_pkts(conn))  */
	uint8_t  valid;              /* 0 = no connection object found           */
	uint8_t  reserved[3];
};

/* Implemented in the patched conn.c. Safe to call from a thread context. */
void bsf_bt_capture_conn(struct bsf_bt_corpse_conn *out);

#ifdef __cplusplus
}
#endif

#endif /* BSF_BT_STAGE_H_ */
