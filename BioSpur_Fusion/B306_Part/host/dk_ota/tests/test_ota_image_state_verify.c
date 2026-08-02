#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "../src/ota_image_state_verify.h"

static const uint8_t hash_a[4] = {0x11, 0x22, 0x33, 0x44};

int main(void)
{
	struct bsf_ota_image_state state;
	/* {"images":[{"hash":h'11223344',"active":true,
	 *             "confirmed":true},{"hash":h'00000000',...}]} */
	const uint8_t definite[] = {
		0xa1, 0x66, 'i','m','a','g','e','s', 0x82,
		0xa3, 0x64, 'h','a','s','h', 0x44, 0x11,0x22,0x33,0x44,
		0x66, 'a','c','t','i','v','e', 0xf5,
		0x69, 'c','o','n','f','i','r','m','e','d', 0xf5,
		0xa3, 0x64, 'h','a','s','h', 0x44, 0,0,0,0,
		0x66, 'a','c','t','i','v','e', 0xf4,
		0x69, 'c','o','n','f','i','r','m','e','d', 0xf4,
	};
	const uint8_t split_flags[] = {
		0xa1, 0x66, 'i','m','a','g','e','s', 0x82,
		0xa3, 0x64, 'h','a','s','h', 0x44, 0x11,0x22,0x33,0x44,
		0x66, 'a','c','t','i','v','e', 0xf5,
		0x69, 'c','o','n','f','i','r','m','e','d', 0xf4,
		0xa3, 0x64, 'h','a','s','h', 0x44, 0,0,0,0,
		0x66, 'a','c','t','i','v','e', 0xf4,
		0x69, 'c','o','n','f','i','r','m','e','d', 0xf5,
	};
	const uint8_t indefinite[] = {
		0xbf, 0x66, 'i','m','a','g','e','s', 0x9f,
		0xbf, 0x64, 'h','a','s','h', 0x44, 0x11,0x22,0x33,0x44,
		0x66, 'a','c','t','i','v','e', 0xf5,
		0x69, 'c','o','n','f','i','r','m','e','d', 0xf5, 0xff,
		0xff, 0xff,
	};
	/* Old active image plus expected payload in secondary, pending. */
	const uint8_t secondary_pending[] = {
		0xa1, 0x66, 'i','m','a','g','e','s', 0x82,
		0xa4, 0x64, 's','l','o','t', 0x00,
		0x64, 'h','a','s','h', 0x44, 0,0,0,0,
		0x66, 'a','c','t','i','v','e', 0xf5,
		0x67, 'p','e','n','d','i','n','g', 0xf4,
		0xa5, 0x64, 's','l','o','t', 0x01,
		0x64, 'h','a','s','h', 0x44, 0x11,0x22,0x33,0x44,
		0x66, 'a','c','t','i','v','e', 0xf4,
		0x69, 'c','o','n','f','i','r','m','e','d', 0xf4,
		0x67, 'p','e','n','d','i','n','g', 0xf5,
	};
	const uint8_t active_unconfirmed[] = {
		0xa1, 0x66, 'i','m','a','g','e','s', 0x81,
		0xa4, 0x64, 's','l','o','t', 0x00,
		0x64, 'h','a','s','h', 0x44, 0x11,0x22,0x33,0x44,
		0x66, 'a','c','t','i','v','e', 0xf5,
		0x69, 'c','o','n','f','i','r','m','e','d', 0xf4,
	};
	const uint8_t old_erased[] = {
		0xa1, 0x66, 'i','m','a','g','e','s', 0x81,
		0xa3, 0x64, 's','l','o','t', 0x00,
		0x64, 'h','a','s','h', 0x44, 0,0,0,0,
		0x66, 'a','c','t','i','v','e', 0xf5,
	};

	assert(bsf_ota_image_state_verified(definite, sizeof(definite),
					    hash_a, sizeof(hash_a)));
	assert(!bsf_ota_image_state_verified(split_flags, sizeof(split_flags),
					     hash_a, sizeof(hash_a)));
	assert(bsf_ota_image_state_verified(indefinite, sizeof(indefinite),
					    hash_a, sizeof(hash_a)));
	assert(!bsf_ota_image_state_verified(definite, 20,
					     hash_a, sizeof(hash_a)));
	assert(bsf_ota_image_state_inspect(
		definite, sizeof(definite), hash_a, sizeof(hash_a), &state));
	assert(bsf_ota_image_state_branch(&state) ==
	       BSF_OTA_IMAGE_ACTIVE_CONFIRMED);
	assert(bsf_ota_image_state_inspect(
		active_unconfirmed, sizeof(active_unconfirmed),
		hash_a, sizeof(hash_a), &state));
	assert(bsf_ota_image_state_branch(&state) ==
	       BSF_OTA_IMAGE_ACTIVE_UNCONFIRMED);
	assert(bsf_ota_image_state_inspect(
		secondary_pending, sizeof(secondary_pending),
		hash_a, sizeof(hash_a), &state));
	assert(state.secondary_present);
	assert(bsf_ota_image_state_branch(&state) ==
	       BSF_OTA_IMAGE_SECONDARY_PENDING);
	assert(bsf_ota_image_state_inspect(
		old_erased, sizeof(old_erased), hash_a, sizeof(hash_a), &state));
	assert(!state.secondary_present);
	assert(bsf_ota_image_state_branch(&state) ==
	       BSF_OTA_IMAGE_OLD_NO_USABLE_PENDING);
	puts("OTA_IMAGE_STATE_VERIFY_PASS");
	return 0;
}
