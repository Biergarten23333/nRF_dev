/* v46 wedge recovery guard. See bsf_recovery.h for why it exists and why it
 * resets rather than disconnects. */
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/crc.h>
#include <zephyr/sys/reboot.h>

#include "bsf_recovery.h"
#include "bsf_v45.h"
#include "bsf_v45_detector.h"

LOG_MODULE_REGISTER(bsf_recovery, LOG_LEVEL_INF);

#define RECOVERY_MAGIC 0x52435634u   /* "RCV4" */

/*
 * The witness. `.noinit` because it must be readable AFTER the reset it
 * describes -- the same lifetime rule that has already cost this project
 * `bsf_v45_frozen` and `v45_flash_slot_next`, both of which were in `.bss` and
 * were therefore zeroed by exactly the event they were meant to record.
 *
 * CRC-protected because `.noinit` is uninitialised on a power-on and a
 * plausible-looking garbage witness is worse than no witness.
 */
struct bsf_recovery_witness {
	uint32_t magic;
	uint32_t resets;          /* cumulative across soft resets  */
	uint32_t uptime_ms;       /* at the moment of the trigger   */
	uint32_t epoch;
	uint32_t notify_ok_total;
	uint32_t notconn_streak;
	uint32_t frozen_ms;
	uint8_t  cause;
	uint8_t  streak;          /* consecutive guard resets       */
	uint8_t  latched;         /* gave up: streak limit reached  */
	uint8_t  pad;
	uint32_t crc32;           /* over everything above          */
};

__attribute__((section(".noinit")))
static struct bsf_recovery_witness bsf_rcv;

static uint32_t witness_crc(const struct bsf_recovery_witness *w)
{
	return crc32_ieee((const uint8_t *)w,
			  offsetof(struct bsf_recovery_witness, crc32));
}

static bool witness_valid(void)
{
	return bsf_rcv.magic == RECOVERY_MAGIC &&
	       bsf_rcv.crc32 == witness_crc(&bsf_rcv);
}

static void witness_seal(void)
{
	bsf_rcv.magic = RECOVERY_MAGIC;
	bsf_rcv.crc32 = witness_crc(&bsf_rcv);
}

static int bsf_recovery_early_init(void)
{
	if (!witness_valid()) {
		memset(&bsf_rcv, 0, sizeof(bsf_rcv));
		witness_seal();
	}
	return 0;
}
/* Before any thread can read it, and before the guard thread starts. */
SYS_INIT(bsf_recovery_early_init, PRE_KERNEL_1, 0);

void bsf_recovery_report(uint32_t *resets, uint8_t *cause, uint32_t *frozen_ms,
			 uint8_t *streak, uint8_t *latched)
{
	*resets = bsf_rcv.resets;
	*cause = bsf_rcv.cause;
	*frozen_ms = bsf_rcv.frozen_ms;
	*streak = bsf_rcv.streak;
	*latched = bsf_rcv.latched;
}

static void recover(uint8_t cause, const struct bsf_v45_env *env,
		    uint32_t now_ms, uint32_t frozen_ms)
{
	if (bsf_rcv.streak >= BSF_RECOVERY_MAX_STREAK) {
		if (!bsf_rcv.latched) {
			bsf_rcv.latched = 1u;
			witness_seal();
			LOG_ERR("V46 RECOVERY LATCHED after %u consecutive "
				"resets -- not resetting again, cause=%u",
				bsf_rcv.streak, cause);
		}
		return;
	}

	bsf_rcv.resets++;
	bsf_rcv.streak++;
	bsf_rcv.cause = cause;
	bsf_rcv.uptime_ms = now_ms;
	bsf_rcv.epoch = env->epoch;
	bsf_rcv.notify_ok_total = env->notify_ok_total;
	bsf_rcv.notconn_streak = env->notconn_streak;
	bsf_rcv.frozen_ms = frozen_ms;
	witness_seal();

	LOG_ERR("V46 RECOVERY cause=%u frozen_ms=%u epoch=%u notify_ok=%u "
		"streak=%u -- resetting", cause, frozen_ms, env->epoch,
		env->notify_ok_total, bsf_rcv.streak);

	/*
	 * Bounded, node-derived jitter. Ten boards that wedge together must not
	 * come back together and re-collide on the master's connection slots.
	 * Deterministic per node so a given board's behaviour is reproducible.
	 */
	/* Reuse the detector's node-derived jitter, clamped to this guard's own
	 * bound so the two mechanisms cannot drift apart. */
	k_sleep(K_MSEC(bsf_v45_reboot_jitter_ms(env->node_identity) %
		       (BSF_RECOVERY_JITTER_MS + 1u)));

	/*
	 * COLD, not WARM. The controller must come up in step with the host:
	 * wedge #2 had the host holding no conn object while the controller
	 * still owned the link, and only a full-chip reset puts the two back in
	 * agreement. A warm reboot has also been observed (anchor OTA work) to
	 * leave peers unable to reconnect.
	 */
	sys_reboot(SYS_REBOOT_COLD);
}

static void guard_thread(void *a, void *b, void *c)
{
	struct bsf_v45_env env;
	uint32_t last_notify_ok = 0u;
	uint32_t notify_ok_moved_ms = 0u;
	uint32_t healthy_since_ms = 0u;
	uint32_t last_epoch = 0u;
	bool seeded = false;

	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	if (witness_valid() && bsf_rcv.resets > 0u) {
		LOG_WRN("V46 recovery witness: resets=%u cause=%u frozen_ms=%u "
			"streak=%u latched=%u", bsf_rcv.resets, bsf_rcv.cause,
			bsf_rcv.frozen_ms, bsf_rcv.streak, bsf_rcv.latched);
	}

	while (true) {
		uint32_t now_ms;
		bool armed;

		k_sleep(K_MSEC(BSF_RECOVERY_TICK_MS));
		bsf_v45_env_get(&env);
		now_ms = k_uptime_get_32();

		/* A reconnect voids every dwell. */
		if (env.epoch != last_epoch) {
			last_epoch = env.epoch;
			seeded = false;
		}

		/* OTA suppression: bsf_v45_ota_mark() already gates the v45
		 * detector; the guard reads the same state through the env's
		 * subscription flags, which DFU clears. */
		armed = env.connected && env.data_subscribed &&
			(now_ms - env.connected_at_ms) >= BSF_RECOVERY_GRACE_MS;

		if (!armed) {
			seeded = false;
			continue;
		}

		if (!seeded) {
			last_notify_ok = env.notify_ok_total;
			notify_ok_moved_ms = now_ms;
			seeded = true;
			continue;
		}

		if (env.notify_ok_total != last_notify_ok) {
			last_notify_ok = env.notify_ok_total;
			notify_ok_moved_ms = now_ms;

			/*
			 * Healthy delivery clears the boot-loop streak, but
			 * only after a long stretch of it. A board that
			 * recovers, delivers for two seconds and wedges again
			 * is still in a loop.
			 */
			if (healthy_since_ms == 0u) {
				healthy_since_ms = now_ms;
			} else if (bsf_rcv.streak != 0u &&
				   (now_ms - healthy_since_ms) >=
					   BSF_RECOVERY_HEALTHY_MS) {
				LOG_INF("V46 recovery streak cleared after 30 min healthy");
				bsf_rcv.streak = 0u;
				bsf_rcv.latched = 0u;
				witness_seal();
			}
		} else {
			uint32_t frozen_ms = now_ms - notify_ok_moved_ms;

			healthy_since_ms = 0u;
			if (frozen_ms >= BSF_RECOVERY_FREEZE_MS) {
				recover(BSF_RECOVERY_CAUSE_NOTIFY_FROZEN, &env,
					now_ms, frozen_ms);
				seeded = false;
			}
		}

		/*
		 * Second arm, and a faster one. The node contradicting itself:
		 * the application believes it is connected while every notify
		 * returns -ENOTCONN. No dwell -- this is not a slow symptom,
		 * it is two halves of one node disagreeing.
		 */
		if (env.connected &&
		    env.notconn_streak >= BSF_RECOVERY_NOTCONN_STREAK) {
			recover(BSF_RECOVERY_CAUSE_NOTCONN, &env, now_ms,
				now_ms - notify_ok_moved_ms);
			seeded = false;
		}
	}
}

#define GUARD_STACK_SIZE 1024
#define GUARD_PRIORITY   7

K_THREAD_STACK_DEFINE(guard_stack, GUARD_STACK_SIZE);
static struct k_thread guard_thread_data;

void bsf_recovery_start(void)
{
	k_tid_t t = k_thread_create(&guard_thread_data, guard_stack,
				    K_THREAD_STACK_SIZEOF(guard_stack),
				    guard_thread, NULL, NULL, NULL,
				    GUARD_PRIORITY, 0, K_NO_WAIT);

	k_thread_name_set(t, "bsf_recovery");
	LOG_INF("V46 recovery guard started tick=%ums freeze=%ums notconn=%u",
		BSF_RECOVERY_TICK_MS, BSF_RECOVERY_FREEZE_MS,
		BSF_RECOVERY_NOTCONN_STREAK);
}
