/*
 * biospur_link.h — DWM1001C(nRF52832) → B306(nRF52840) UART contract
 *
 * VERSION 2. Supersedes v1 entirely. Install verbatim on BOTH sides.
 *
 * Changes from v1:
 *   - BSL_EXPECTED_FRAME_LEN was wrong (77; actual 78). Now derived and
 *     correct.
 *   - tag_id (uint8_t) split into identity_code + logical_tag_id. They are
 *     genuinely different objects; see IDENTITY below.
 *   - Added t_round_us[] — measured, replacing a computed reply delay.
 *     See MOTION CORRECTION below.
 *
 * ---------------------------------------------------------------------------
 * DESIGN RULES — read before changing anything
 * ---------------------------------------------------------------------------
 *
 * 1. FIXED LENGTH. Every frame is exactly BSL_FRAME_LEN bytes, always, even
 *    when anchors drop out. Unused slots carry BSL_ANCHOR_NONE.
 *
 *    Why: the UART transmission time is then a CONSTANT, and a constant delay
 *    is absorbed entirely by the offset term of the B306 clock filter. A
 *    variable-length frame makes that delay data-dependent, which the filter
 *    cannot absorb and which does not average out.
 *
 * 2. EXPLICIT ANCHOR IDS. Array position carries no meaning. The frozen BLE
 *    record used implicit ordering (formatter emitted measurement order, host
 *    parser assumed increasing anchor ID); those coincide in steady state and
 *    diverge when an anchor drops. Never inherit that.
 *
 * 3. THE POLL TX TIMESTAMP IS THE ALIGNMENT ANCHOR. Broadcast Alt-SS-TWR gives
 *    all anchors one common outbound epoch. That instant — not any anchor
 *    response — is what IMU samples align to.
 *
 * 4. LITTLE ENDIAN throughout. Both MCUs are little-endian Cortex-M.
 *
 * ---------------------------------------------------------------------------
 * IDENTITY — two distinct objects, both required
 * ---------------------------------------------------------------------------
 *
 *   identity_code    the BSxxxx code. 16-bit, rendered as four uppercase hex
 *                    digits ("BS%04X"). Derived by XOR-folding
 *                    FICR.DEVICEID[0..1], overridable by a nonzero NVS
 *                    identity_code. Human/control-plane identity. NEVER
 *                    transmitted on air.
 *
 *   logical_tag_id   the on-air identity. The DW1000 short address is
 *                    BSL_TAG_ADDR_BASE + logical_tag_id. Defaults to the low
 *                    byte of identity_code at first boot, but the master can
 *                    reassign it independently — so it must be reported, not
 *                    inferred.
 *
 * Carrying only one of these loses information. Carrying identity_code alone
 * hides what was actually on air; carrying logical_tag_id alone hides which
 * physical device produced the data.
 *
 * The host should treat (identity_code, logical_tag_id) as the node key and
 * flag any change of either during a session.
 *
 * COLLISIONS: identity_code is a 16-bit fold, so distinct devices can collide
 * (~0.08% for ten nodes). The frozen firmware detects this nowhere, and a
 * collision can propagate into identical roster keys and identical TDMA slots
 * — an on-air collision, not merely ambiguous logging. The host MUST check
 * for duplicate identity_code at session start. The fix is the existing NVS
 * identity_code override.
 *
 * DO NOT use 0xFFFF as a "not found" sentinel for identity_code. The frozen
 * manufacturer-data parsers do, which makes a genuine BSFFFF unreachable. Use
 * the flags field or a separate presence bit.
 *
 * ---------------------------------------------------------------------------
 * MOTION CORRECTION — why t_round_us is measured, not computed
 * ---------------------------------------------------------------------------
 *
 * SS-TWR measures the MEAN of the distance at poll TX and the distance at
 * response RX:
 *
 *     range_k ~ [ d(t_poll) + d(t_resp_k) ] / 2
 *
 * Subtracting the anchor's reply delay removes its wait DURATION, not the tag
 * displacement during it. The residual bias is
 *
 *     dd_k = v_radial * (t_resp_k - t_poll) / 2
 *
 * which grows with TDMA slot index: ~0.6 mm per (m/s) at rank 0, ~4.1 mm per
 * (m/s) at rank 7. Negligible on a torso, 20-40 mm on a limb at running speed.
 * It is exactly zero when static, so no static calibration ever sees it. IMU
 * velocity makes it correctable:
 *
 *     range_corrected_k = range_mm[k] - (v . u_k) * t_round_us[k] / 2
 *
 * t_round_us[k] = (t_resp_k - t_poll), measured by the tag on its own DW1000
 * clock. It is reported rather than computed from guard_us + rank*spacing
 * because:
 *
 *   - the tag and the anchors hold SEPARATE build-time definitions of
 *     guard/spacing, so a heterogeneous build makes the tag report a schedule
 *     the responders did not execute — silently, and looking like noise;
 *   - a measured value also absorbs per-anchor deviations (late response,
 *     retry, rank reassignment) that no nominal schedule predicts.
 *
 * guard_us, spacing_us and rank[] are retained for diagnostics: a large
 * divergence between measured t_round_us[k] and the nominal
 * guard + rank[k]*spacing means an anchor is not behaving as scheduled.
 * Never substitute the nominal value into the correction.
 */

#ifndef BIOSPUR_LINK_H
#define BIOSPUR_LINK_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BSL_MAGIC0          0xB5u
#define BSL_MAGIC1          0x9Cu
#define BSL_VERSION         2u

#define BSL_MAX_ANCHORS     8u
#define BSL_ANCHOR_NONE     0xFFu   /* slot not used this sweep */
#define BSL_RANGE_INVALID   0xFFFFu
#define BSL_TROUND_INVALID  0xFFFFu

#define BSL_TAG_ADDR_BASE   0xB100u /* short address = base + logical_tag_id */

#define BSL_BAUDRATE        460800u /* 8N1, no flow control */

/* ------------------------------------------------------------------------ */
/* Frame                                                                     */
/* ------------------------------------------------------------------------ */

typedef struct __attribute__((packed)) {
	uint8_t magic0;   /* BSL_MAGIC0 */
	uint8_t magic1;   /* BSL_MAGIC1 */
	uint8_t version;  /* BSL_VERSION */
	uint8_t len;      /* sizeof(bsl_uwb_t), constant; sanity check only */
} bsl_hdr_t;

typedef struct __attribute__((packed)) {
	/* --- sweep identity -------------------------------------------------- */
	uint32_t sweep;            /* monotonic, wraps; pairs frame with strobe  */
	uint8_t  poll_tx_ts[5];    /* DW1000 40-bit TX timestamp, broadcast poll */

	/* --- node identity (see IDENTITY) ------------------------------------ */
	uint16_t identity_code;    /* BSxxxx, hex. NOT an on-air value.          */
	uint8_t  logical_tag_id;   /* short address = BSL_TAG_ADDR_BASE + this   */

	/* --- nominal schedule, DIAGNOSTIC ONLY (see MOTION CORRECTION) ------- */
	uint16_t guard_us;         /* tag's build-time value, nominal 1200       */
	uint16_t spacing_us;       /* tag's build-time value, nominal 1000       */

	/* --- per anchor slot, all fixed length ------------------------------- */
	uint8_t  anchor_id  [BSL_MAX_ANCHORS]; /* BSL_ANCHOR_NONE if unused      */
	uint8_t  rank       [BSL_MAX_ANCHORS]; /* TDMA slot index, diagnostic    */
	uint16_t range_mm   [BSL_MAX_ANCHORS]; /* Per-sweep instantaneous range,
					       * CFO clock-offset corrected;
					       * NO smoothing/filtering applied.
					       * Smoothing belongs on the fusion
					       * host to preserve its noise model. */
	uint16_t t_round_us [BSL_MAX_ANCHORS]; /* MEASURED resp_rx - poll_tx     */
	uint8_t  quality    [BSL_MAX_ANCHORS]; /* 0..100                         */
	int16_t  cfo_ppm_q8 [BSL_MAX_ANCHORS]; /* CFO in ppm, Q8 fixed point     */

	uint8_t  valid_mask;       /* bit i set: range_mm[i] usable              */
	uint8_t  flags;            /* see BSL_FLAG_*                             */
} bsl_uwb_t;

#define BSL_FLAG_STROBE_SENT    (1u << 0) /* P0.26 strobe fired this sweep   */
#define BSL_FLAG_SWEEP_PARTIAL  (1u << 1) /* fewer responses than requested  */
#define BSL_FLAG_IDENTITY_NVS   (1u << 2) /* identity_code came from NVS
                                           * override, not the FICR fold     */

typedef struct __attribute__((packed)) {
	bsl_hdr_t hdr;
	bsl_uwb_t body;
	uint16_t  crc;    /* CRC-16/CCITT-FALSE over hdr+body, init 0xFFFF */
} bsl_frame_t;

#define BSL_FRAME_LEN  ((uint32_t)sizeof(bsl_frame_t))

/*
 * Derived, not hand-written — a hand-maintained constant drifted once already.
 * These asserts exist to catch one side editing the struct alone.
 */
#define BSL_BODY_LEN_EXPECTED   90u
#define BSL_FRAME_LEN_EXPECTED  (4u + BSL_BODY_LEN_EXPECTED + 2u)  /* 96 */

_Static_assert(sizeof(bsl_hdr_t) == 4u,
	       "biospur_link: header size drifted");
_Static_assert(sizeof(bsl_uwb_t) == BSL_BODY_LEN_EXPECTED,
	       "biospur_link: body size drifted - both sides must be rebuilt");
_Static_assert(sizeof(bsl_frame_t) == BSL_FRAME_LEN_EXPECTED,
	       "biospur_link: frame size drifted - both sides must be rebuilt");
_Static_assert(offsetof(bsl_uwb_t, poll_tx_ts) == 4u,
	       "biospur_link: poll_tx_ts offset drifted");
_Static_assert(offsetof(bsl_uwb_t, identity_code) == 9u,
	       "biospur_link: identity_code offset drifted");
_Static_assert(offsetof(bsl_uwb_t, anchor_id) == 16u,
	       "biospur_link: anchor_id offset drifted");
_Static_assert(offsetof(bsl_uwb_t, range_mm) == 32u,
	       "biospur_link: range_mm offset drifted");
_Static_assert(offsetof(bsl_uwb_t, t_round_us) == 48u,
	       "biospur_link: t_round_us offset drifted");
_Static_assert(offsetof(bsl_uwb_t, valid_mask) == 88u,
	       "biospur_link: valid_mask offset drifted");
_Static_assert(offsetof(bsl_frame_t, body) == 4u,
	       "biospur_link: frame body offset drifted");
_Static_assert(offsetof(bsl_frame_t, crc) == 94u,
	       "biospur_link: frame CRC offset drifted");

/* ------------------------------------------------------------------------ */
/* Helpers                                                                   */
/* ------------------------------------------------------------------------ */

static inline uint64_t bsl_ts40_get(const uint8_t ts[5])
{
	return ((uint64_t)ts[0])
	     | ((uint64_t)ts[1] << 8)
	     | ((uint64_t)ts[2] << 16)
	     | ((uint64_t)ts[3] << 24)
	     | ((uint64_t)ts[4] << 32);
}

static inline void bsl_ts40_set(uint8_t ts[5], uint64_t v)
{
	for (int i = 0; i < 5; i++) {
		ts[i] = (uint8_t)(v >> (8 * i));
	}
}

/* On-air short address of the tag that produced this frame. */
static inline uint16_t bsl_short_addr(const bsl_uwb_t *u)
{
	return (uint16_t)(BSL_TAG_ADDR_BASE + u->logical_tag_id);
}

/* Nominal reply delay for slot i. DIAGNOSTIC ONLY — compare against the
 * measured t_round_us[i] to detect a responder off schedule. Never use this
 * in the motion correction; use t_round_us[i]. */
static inline uint32_t bsl_nominal_reply_us(const bsl_uwb_t *u, unsigned i)
{
	return (uint32_t)u->guard_us + (uint32_t)u->rank[i] * u->spacing_us;
}

/* Constant UART transmission time, microseconds. Constant by construction —
 * see DESIGN RULE 1. B306 subtracts this before feeding the clock filter. */
static inline uint32_t bsl_tx_time_us(void)
{
	return (BSL_FRAME_LEN * 10u * 1000000u) / BSL_BAUDRATE;
}

/*
 * BSxxxx identity fold, reproduced from the frozen tag firmware.
 *
 * Reproduce the ORIGINAL OPERATION SEQUENCE, not an algebraically simplified
 * form. (The high 16 bits of DEVICEID[0] cancel out — an accident of the
 * original expression, not an intent. Any "cleaner" rewrite risks diverging.)
 *
 * Identical on nRF52832 and nRF52840: FICR base 0x10000000, DEVICEID[0..1] at
 * +0x60, two read-only uint32 words on both parts.
 *
 * NOTE: this is the TAG algorithm. Unified anchors use a different one
 * (CRC-16/CCITT over a 16-byte device UUID). Do not cross them.
 */
static inline uint16_t bsl_identity_from_ficr(uint32_t deviceid0,
					      uint32_t deviceid1)
{
	uint32_t folded = deviceid0 ^ deviceid1
			^ (deviceid0 >> 16) ^ (deviceid1 << 1);
	return (uint16_t)(((folded >> 16) ^ folded) & 0xFFFFu);
}

#ifdef __cplusplus
}
#endif

#endif /* BIOSPUR_LINK_H */
