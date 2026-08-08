/*
 * bsf_v45_corpse.h -- the v45 corpse: wire layout, banks, schema.
 *
 * This file is the CONTRACT. tools/bsf_v45_corpse_decode.py mirrors it field
 * for field, so field order, sizes and packing are not implementation details.
 *
 * SCHEMA DISCIPLINE
 * -----------------
 * BSF_V45_SCHEMA = 3. v43 shipped schema 1, v44 schema 2 (bsf_bt_stage.h /
 * main.c BSF_CORPSE_SCHEMA). Those enums, that struct and their decoder support
 * are UNTOUCHED and still decodable -- section "absolute prohibitions" requires
 * it, and the reason is the same one v44 recorded: two different layouts must
 * never claim the same schema, or an old corpse gets read with new offsets into
 * plausible-looking nonsense.
 *
 * WHY BANKS INSTEAD OF ONE BLOB
 * -----------------------------
 * The four channel traces and the 25.6 s trajectory ring together are ~29 KB.
 * They already live in `.noinit`, so a capture FREEZES THEM IN PLACE rather
 * than copying them -- no second 29 KB of RAM, and nothing that can drift out
 * of step with the live data. Each region therefore needs its own
 * {magic, schema, length, seq, CRC, valid} so a partially written or partially
 * corrupted set degrades bank by bank instead of all at once. The decoder
 * rejects any unknown schema/length combination outright.
 */
#ifndef BSF_V45_CORPSE_H
#define BSF_V45_CORPSE_H

#include <stdint.h>

#include "bsf_v45_trace.h"

#ifdef __cplusplus
extern "C" {
#endif

#define BSF_V45_CORPSE_MAGIC   0x35345043u   /* 'CP45' little-endian        */
#define BSF_V45_BANK_MAGIC     0x354b4e42u   /* 'BNK5' little-endian        */
#define BSF_V45_SCHEMA         3u

/* Bank identifiers. APPEND ONLY. */
#define BSF_V45_BANK_MPSL_RX    0u
#define BSF_V45_BANK_BT_RX      1u
#define BSF_V45_BANK_TX_WORK    2u
#define BSF_V45_BANK_APP_NOTIFY 3u
#define BSF_V45_BANK_RING       4u
#define BSF_V45_BANK__COUNT     5u

/* Trigger causes mirror bsf_v45_detector.h BSF_V45_CAUSE_*. */

#define BSF_V45_THREAD_MPSL_RX   0u
#define BSF_V45_THREAD_BT_RX     1u
#define BSF_V45_THREAD_SYS_WQ    2u
#define BSF_V45_THREAD_NOTIFY    3u
#define BSF_V45_THREAD_PUBLISHER 4u
#define BSF_V45_THREAD__COUNT    5u

struct __packed bsf_v45_thread_snapshot {
	uint32_t tid;
	uint32_t pended_on;      /* _wait_q_t*, resolved to a NAME by the decoder */
	uint32_t psp;            /* callee_saved.psp                             */
	uint32_t stack_start;
	uint32_t stack_size;
	uint32_t stack_unused;
	uint32_t last_channel_seq;
	uint8_t  thread_state;
	int8_t   prio;
	uint8_t  found;
	uint8_t  pad;
};

struct __packed bsf_v45_channel_summary {
	uint32_t seq;
	uint32_t stage_age_ms;
	uint32_t arg0;
	uint32_t arg1;
	uint32_t enter_total;
	uint32_t exit_total;
	uint32_t last_enter_ms;
	uint32_t last_exit_ms;
	uint32_t writer_tid;
	uint32_t writer_mismatch_count;
	uint32_t first_offending_tid;
	uint32_t trace_head;
	uint16_t stage;
	uint16_t pad;
};

/*
 * CORE. Target <= 1 KB (brief section 8); the _Static_assert below is the
 * tripwire, exactly like v44's four-page export budget. Growth past it is a
 * deliberate act, not a side effect.
 */
typedef struct __packed {
	uint32_t magic;               /* BSF_V45_CORPSE_MAGIC                 */
	uint16_t schema;              /* BSF_V45_SCHEMA                       */
	uint16_t length;              /* crc_start .. end of struct           */
	uint32_t crc32;

	/* --- crc_start --- */
	uint32_t fw_marker_hash;
	uint32_t node_identity;
	uint32_t uptime_ms;
	uint32_t boot_reset_reason;
	uint32_t corpse_seq;
	uint32_t epoch;               /* connection incarnation               */
	uint16_t trigger_cause;       /* BSF_V45_CAUSE_*                      */
	uint16_t trigger_count;       /* this power cycle                     */

	/* the two watermark ages that decided it, plus the suspicion mark */
	uint32_t notify_exit_age_ms;
	uint32_t ncp_packet_age_ms;
	uint32_t suspect_start_ms;
	uint32_t suspect_ring_index;

	/* connection state at capture */
	uint32_t connected_at_ms;
	uint8_t  connected;
	uint8_t  data_subscribed;
	uint8_t  telemetry_subscribed;
	uint8_t  ota_active;

	/* budget / persistence bookkeeping */
	uint8_t  reboot_taken;
	uint8_t  reboot_owner;
	uint8_t  flash_slot;          /* 0xff = not persisted                 */
	uint8_t  flash_enabled;

	struct bsf_v45_channel_summary  channel[BSF_V45_CH__COUNT];
	struct bsf_v45_thread_snapshot  thread[BSF_V45_THREAD__COUNT];
	struct bsf_v45_waitobj_table    waitobj;
	struct bsf_v45_conn_snapshot    conn;
	struct bsf_v45_pool_snapshot    pools;

	/* every global atomic of section 3, in declaration order */
	uint32_t counters[32];

	/* shadow depths, precomputed so the decoder never subtracts wrong */
	int32_t  tx_pending_depth;
	int32_t  tx_complete_depth;

	/* liveness cross-checks that cost nothing */
	uint32_t wdt_feed_count;
	uint32_t producer_seq;
	uint32_t publisher_count;
	uint32_t notify_timeout_drop_total;
	int32_t  tx_complete_busy;
	/* --- crc_end --- */

	uint32_t valid;               /* BSF_V45_CORPSE_MAGIC, written LAST   */
} bsf_v45_core_t;

/*
 * Bank header. Written after the payload is frozen, `valid` last.
 * `entry_size` and `entries` let the decoder reject a bank produced by a
 * differently shaped build instead of reinterpreting it -- the same rule the
 * trajectory ring's geometry stamp already enforces.
 */
typedef struct __packed {
	uint32_t magic;               /* BSF_V45_BANK_MAGIC                   */
	uint16_t schema;              /* BSF_V45_SCHEMA                       */
	uint8_t  bank;                /* BSF_V45_BANK_*                       */
	uint8_t  entry_size;
	uint32_t length;              /* payload bytes following this header  */
	uint32_t crc32;               /* over the payload                     */
	uint32_t corpse_seq;          /* must match the CORE                  */
	uint16_t entries;
	uint16_t head;                /* ring head at freeze, for ordering    */
	uint32_t valid;               /* BSF_V45_BANK_MAGIC, written LAST     */
} bsf_v45_bank_header_t;

_Static_assert(sizeof(bsf_v45_core_t) <= 1400u,
	       "bsf_v45_core_t has outgrown its budget: shrink it, or raise the "
	       "bound deliberately and re-check the export page walk and the "
	       "flash slot size");

/*
 * WIRE-SIZE CONTRACT.
 *
 * tools/bsf_v45_corpse_decode.py models every one of these with an explicit
 * little-endian struct format, and tests/test_bsf_v45_decoder.py cross-checks
 * the model against the DWARF of the real ELF. These asserts are the third leg:
 * they fail at COMPILE time, on every build, including the ones where a
 * conditionally-compiled struct never gets a DWARF entry because nothing
 * references it. A decoder whose model has silently drifted produces plausible
 * nonsense, and this project has already paid for one of those.
 */
_Static_assert(sizeof(struct bsf_v45_channel_summary) == 52u, "wire size moved");
_Static_assert(sizeof(struct bsf_v45_thread_snapshot) == 32u, "wire size moved");
_Static_assert(sizeof(struct bsf_v45_waitobj_table) == 32u, "wire size moved");
_Static_assert(sizeof(struct bsf_v45_conn_snapshot) == 36u, "wire size moved");
_Static_assert(sizeof(struct bsf_v45_pool_summary) == 24u, "wire size moved");
_Static_assert(sizeof(struct bsf_v45_buf_entry) == 12u, "wire size moved");
_Static_assert(sizeof(struct bsf_v45_pool_snapshot) == 280u, "wire size moved");
_Static_assert(sizeof(struct bsf_v45_trace_entry) == 16u, "wire size moved");
_Static_assert(sizeof(bsf_v45_bank_header_t) == 28u, "wire size moved");
_Static_assert(sizeof(bsf_v45_core_t) == 944u,
	       "the CORE wire layout moved: update the decoder model in "
	       "tools/bsf_v45_corpse_decode.py and re-run its test");

/*
 * How much of each region the FLASH container carries. The `.noinit` banks are
 * complete; the flash slot is 8 KB and cannot be, so the truncation is declared
 * here and reported by the decoder rather than being silent.
 */
#define BSF_V45_FLASH_TRACE_KEEP 32u
#define BSF_V45_FLASH_RING_KEEP  48u

#define BSF_V45_FLASH_MAGIC      0x35465043u  /* 'CPF5'                      */
#define BSF_V45_FLASH_SLOTS      2u

typedef struct __packed {
	uint32_t magic;
	uint16_t schema;
	uint16_t slot;
	uint32_t length;              /* bytes after this header               */
	uint32_t crc32;               /* over those bytes                      */
	uint32_t corpse_seq;
	uint32_t write_uptime_ms;
	uint16_t trace_keep;
	uint16_t ring_keep;
	uint32_t collected;           /* 0 = awaiting collection               */
	uint32_t valid;               /* BSF_V45_FLASH_MAGIC, written LAST     */
} bsf_v45_flash_header_t;

_Static_assert(sizeof(bsf_v45_flash_header_t) == 36u, "wire size moved");

#ifdef __cplusplus
}
#endif

#endif /* BSF_V45_CORPSE_H */
