#ifndef BIOSPUR_BOOT_CONFIRM_POLICY_H_
#define BIOSPUR_BOOT_CONFIRM_POLICY_H_

#include <stdbool.h>
#include <stdint.h>

struct bsf_boot_confirm_policy {
	uint32_t token;
	bool required;
	bool prepared;
	bool committed;
};

static inline void bsf_boot_confirm_policy_init(
	struct bsf_boot_confirm_policy *policy, bool image_confirmed)
{
	*policy = (struct bsf_boot_confirm_policy) {
		.required = !image_confirmed,
	};
}

static inline bool bsf_boot_confirm_policy_prepare(
	struct bsf_boot_confirm_policy *policy, bool connected, bool subscribed,
	uint32_t token)
{
	if (!policy->required || !connected || !subscribed || token == 0u) {
		return false;
	}
	policy->token = token;
	policy->prepared = true;
	policy->committed = false;
	return true;
}

static inline bool bsf_boot_confirm_policy_commit(
	struct bsf_boot_confirm_policy *policy, uint32_t token)
{
	if (!policy->required || !policy->prepared || token != policy->token) {
		return false;
	}
	policy->committed = true;
	return true;
}

static inline bool bsf_boot_confirm_policy_may_confirm(
	const struct bsf_boot_confirm_policy *policy, bool connected,
	bool subscribed)
{
	return policy->required && policy->prepared && policy->committed &&
		connected && subscribed;
}

#endif /* BIOSPUR_BOOT_CONFIRM_POLICY_H_ */
