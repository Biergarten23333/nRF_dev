#include <assert.h>
#include <stdio.h>

#include "../src/boot_confirm_policy.h"

int main(void)
{
	struct bsf_boot_confirm_policy policy;

	bsf_boot_confirm_policy_init(&policy, true);
	assert(!policy.required);
	assert(!bsf_boot_confirm_policy_prepare(&policy, true, true, 1u));

	bsf_boot_confirm_policy_init(&policy, false);
	assert(policy.required);
	assert(!bsf_boot_confirm_policy_prepare(&policy, false, true, 1u));
	assert(!bsf_boot_confirm_policy_prepare(&policy, true, false, 1u));
	assert(!bsf_boot_confirm_policy_prepare(&policy, true, true, 0u));
	assert(bsf_boot_confirm_policy_prepare(&policy, true, true, 0x12345678u));
	assert(!bsf_boot_confirm_policy_commit(&policy, 0x12345679u));
	assert(!bsf_boot_confirm_policy_may_confirm(&policy, true, true));
	assert(bsf_boot_confirm_policy_commit(&policy, 0x12345678u));
	assert(!bsf_boot_confirm_policy_may_confirm(&policy, false, true));
	assert(!bsf_boot_confirm_policy_may_confirm(&policy, true, false));
	assert(bsf_boot_confirm_policy_may_confirm(&policy, true, true));

	puts("boot confirm policy tests passed");
	return 0;
}
