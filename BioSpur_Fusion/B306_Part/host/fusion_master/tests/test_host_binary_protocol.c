#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../../include/host_binary_protocol.h"

int main(void)
{
	uint8_t input[BSF_HOST_FRAME_MAX_RAW];
	uint8_t encoded[BSF_HOST_FRAME_MAX_ENCODED];
	uint8_t decoded[BSF_HOST_FRAME_MAX_RAW];

	for (size_t i = 0; i < sizeof(input); ++i) {
		input[i] = (uint8_t)i;
	}
	for (size_t length = 0; length <= sizeof(input); ++length) {
		size_t encoded_length = bsf_host_cobs_encode(
			input, length, encoded, sizeof(encoded));
		size_t decoded_length;

		assert(encoded_length >= 2u);
		assert(encoded[encoded_length - 1u] == 0u);
		decoded_length = bsf_host_cobs_decode(
			encoded, encoded_length - 1u, decoded, sizeof(decoded));
		assert(decoded_length == length);
		assert(memcmp(input, decoded, length) == 0);
	}
	assert(bsf_host_crc16(
		(const uint8_t *)"123456789", 9u) == 0x29b1u);
	puts("host binary protocol tests passed");
	return 0;
}
