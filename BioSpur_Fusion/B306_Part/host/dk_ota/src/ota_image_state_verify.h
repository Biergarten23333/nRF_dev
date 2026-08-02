#ifndef BSF_OTA_IMAGE_STATE_VERIFY_H
#define BSF_OTA_IMAGE_STATE_VERIFY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

struct bsf_cbor_cursor {
	const uint8_t *p;
	const uint8_t *end;
};

static inline bool bsf_cbor_head(struct bsf_cbor_cursor *c, uint8_t *major,
				 uint64_t *value, bool *indefinite)
{
	uint8_t first;
	uint8_t ai;
	uint8_t bytes;
	uint64_t decoded = 0;

	if (c->p >= c->end) {
		return false;
	}
	first = *c->p++;
	*major = first >> 5;
	ai = first & 0x1fu;
	*indefinite = (ai == 31u);
	if (*indefinite) {
		*value = 0;
		return true;
	}
	if (ai < 24u) {
		*value = ai;
		return true;
	}
	if (ai == 24u) {
		bytes = 1;
	} else if (ai == 25u) {
		bytes = 2;
	} else if (ai == 26u) {
		bytes = 4;
	} else if (ai == 27u) {
		bytes = 8;
	} else {
		return false;
	}
	if ((size_t)(c->end - c->p) < bytes) {
		return false;
	}
	for (uint8_t i = 0; i < bytes; ++i) {
		decoded = (decoded << 8) | *c->p++;
	}
	*value = decoded;
	return true;
}

static inline bool bsf_cbor_skip(struct bsf_cbor_cursor *c)
{
	uint8_t major;
	uint64_t count;
	bool indefinite;

	if (!bsf_cbor_head(c, &major, &count, &indefinite)) {
		return false;
	}
	if (major == 2u || major == 3u) {
		if (indefinite || count > (uint64_t)(c->end - c->p)) {
			return false;
		}
		c->p += (size_t)count;
		return true;
	}
	if (major == 4u || major == 5u) {
		uint64_t items = major == 5u && !indefinite ? count * 2u : count;

		if (indefinite) {
			while (c->p < c->end && *c->p != 0xffu) {
				if (!bsf_cbor_skip(c)) {
					return false;
				}
			}
			if (c->p >= c->end) {
				return false;
			}
			++c->p;
			return true;
		}
		for (uint64_t i = 0; i < items; ++i) {
			if (!bsf_cbor_skip(c)) {
				return false;
			}
		}
	}
	return true;
}

static inline bool bsf_cbor_text(struct bsf_cbor_cursor *c,
				 const uint8_t **text, size_t *len)
{
	uint8_t major;
	uint64_t count;
	bool indefinite;

	if (!bsf_cbor_head(c, &major, &count, &indefinite) || major != 3u ||
	    indefinite || count > (uint64_t)(c->end - c->p)) {
		return false;
	}
	*text = c->p;
	*len = (size_t)count;
	c->p += *len;
	return true;
}

static inline bool bsf_cbor_key_is(const uint8_t *key, size_t key_len,
				   const char *literal)
{
	size_t len = strlen(literal);

	return key_len == len && memcmp(key, literal, len) == 0;
}

struct bsf_ota_image_state {
	bool parsed;
	bool expected_found;
	bool expected_active;
	bool expected_confirmed;
	bool expected_pending;
	bool expected_secondary;
	bool secondary_present;
};

enum bsf_ota_image_branch {
	BSF_OTA_IMAGE_INVALID = 0,
	BSF_OTA_IMAGE_ACTIVE_CONFIRMED,
	BSF_OTA_IMAGE_ACTIVE_UNCONFIRMED,
	BSF_OTA_IMAGE_SECONDARY_PENDING,
	BSF_OTA_IMAGE_OLD_NO_USABLE_PENDING,
};

static inline bool bsf_cbor_bool(struct bsf_cbor_cursor *c, bool *value)
{
	if (c->p >= c->end || (*c->p != 0xf4u && *c->p != 0xf5u)) {
		return false;
	}
	*value = *c->p++ == 0xf5u;
	return true;
}

static inline bool bsf_cbor_uint(struct bsf_cbor_cursor *c, uint64_t *value)
{
	uint8_t major;
	bool indefinite;

	return bsf_cbor_head(c, &major, value, &indefinite) &&
		major == 0u && !indefinite;
}

static inline bool bsf_ota_image_map_inspect(
	struct bsf_cbor_cursor *c, const uint8_t *expected_hash,
	size_t expected_hash_len, struct bsf_ota_image_state *state)
{
	uint8_t major;
	uint64_t pairs;
	bool indefinite;
	bool hash_match = false;
	bool active = false;
	bool confirmed = false;
	bool pending = false;
	bool slot_known = false;
	uint64_t slot = 0u;

	if (!bsf_cbor_head(c, &major, &pairs, &indefinite) || major != 5u) {
		return false;
	}
	for (uint64_t i = 0; indefinite || i < pairs; ++i) {
		const uint8_t *key;
		size_t key_len;

		if (indefinite && c->p < c->end && *c->p == 0xffu) {
			++c->p;
			break;
		}
		if (!bsf_cbor_text(c, &key, &key_len)) {
			return false;
		}
		if (bsf_cbor_key_is(key, key_len, "hash")) {
			uint8_t value_major;
			uint64_t len;
			bool value_indefinite;

			if (!bsf_cbor_head(c, &value_major, &len,
					   &value_indefinite) || value_major != 2u ||
			    value_indefinite || len > (uint64_t)(c->end - c->p)) {
				return false;
			}
			hash_match = len == expected_hash_len &&
				memcmp(c->p, expected_hash, expected_hash_len) == 0;
			c->p += (size_t)len;
		} else if (bsf_cbor_key_is(key, key_len, "active") ||
			   bsf_cbor_key_is(key, key_len, "confirmed") ||
			   bsf_cbor_key_is(key, key_len, "pending")) {
			bool is_active = bsf_cbor_key_is(key, key_len, "active");
			bool is_confirmed =
				bsf_cbor_key_is(key, key_len, "confirmed");
			bool value;

			if (!bsf_cbor_bool(c, &value)) {
				return false;
			}
			if (is_active) {
				active = value;
			} else if (is_confirmed) {
				confirmed = value;
			} else {
				pending = value;
			}
		} else if (bsf_cbor_key_is(key, key_len, "slot")) {
			if (!bsf_cbor_uint(c, &slot)) {
				return false;
			}
			slot_known = true;
		} else if (!bsf_cbor_skip(c)) {
			return false;
		}
	}
	if (slot_known && slot == 1u) {
		state->secondary_present = true;
	}
	if (hash_match) {
		state->expected_found = true;
		state->expected_active = active;
		state->expected_confirmed = confirmed;
		state->expected_pending = pending;
		state->expected_secondary = slot_known && slot == 1u;
	}
	return true;
}

static inline bool bsf_ota_image_state_inspect(
	const uint8_t *payload, size_t payload_len,
	const uint8_t *expected_hash, size_t expected_hash_len,
	struct bsf_ota_image_state *state)
{
	struct bsf_cbor_cursor c = {payload, payload + payload_len};
	uint8_t major;
	uint64_t pairs;
	bool indefinite;
	bool images_found = false;

	memset(state, 0, sizeof(*state));
	if (!bsf_cbor_head(&c, &major, &pairs, &indefinite) || major != 5u) {
		return false;
	}
	for (uint64_t i = 0; indefinite || i < pairs; ++i) {
		const uint8_t *key;
		size_t key_len;

		if (indefinite && c.p < c.end && *c.p == 0xffu) {
			break;
		}
		if (!bsf_cbor_text(&c, &key, &key_len)) {
			return false;
		}
		if (!bsf_cbor_key_is(key, key_len, "images")) {
			if (!bsf_cbor_skip(&c)) {
				return false;
			}
			continue;
		}
		if (!bsf_cbor_head(&c, &major, &pairs, &indefinite) || major != 4u) {
			return false;
		}
		images_found = true;
		for (uint64_t image = 0; indefinite || image < pairs; ++image) {
			if (indefinite && c.p < c.end && *c.p == 0xffu) {
				++c.p;
				break;
			}
			if (!bsf_ota_image_map_inspect(&c, expected_hash,
						       expected_hash_len, state)) {
				return false;
			}
		}
		state->parsed = true;
		return true;
	}
	state->parsed = images_found;
	return images_found;
}

static inline enum bsf_ota_image_branch bsf_ota_image_state_branch(
	const struct bsf_ota_image_state *state)
{
	if (!state->parsed) {
		return BSF_OTA_IMAGE_INVALID;
	}
	if (state->expected_found && state->expected_active) {
		return state->expected_confirmed ?
			BSF_OTA_IMAGE_ACTIVE_CONFIRMED :
			BSF_OTA_IMAGE_ACTIVE_UNCONFIRMED;
	}
	if (state->expected_found && state->expected_secondary &&
	    state->expected_pending) {
		return BSF_OTA_IMAGE_SECONDARY_PENDING;
	}
	return BSF_OTA_IMAGE_OLD_NO_USABLE_PENDING;
}

/* Verify that one and the same MCUboot image map carries the expected hash,
 * active=true, and confirmed=true. Definite and indefinite maps/arrays are
 * accepted because both encodings are emitted by supported mcumgr versions. */
static inline bool bsf_ota_image_state_verified(
	const uint8_t *payload, size_t payload_len,
	const uint8_t *expected_hash, size_t expected_hash_len)
{
	struct bsf_ota_image_state state;

	return bsf_ota_image_state_inspect(payload, payload_len, expected_hash,
					   expected_hash_len, &state) &&
		bsf_ota_image_state_branch(&state) ==
			BSF_OTA_IMAGE_ACTIVE_CONFIRMED;
}

#endif /* BSF_OTA_IMAGE_STATE_VERIFY_H */
