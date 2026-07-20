#!/usr/bin/env python3
"""Generate the image symbols consumed by the pinned BioSpur fast OTA core."""

import argparse
import hashlib
import struct
from pathlib import Path


def format_bytes(data: bytes) -> str:
    lines = []
    for offset in range(0, len(data), 12):
        chunk = data[offset : offset + 12]
        lines.append(", ".join(f"0x{value:02x}" for value in chunk))
    return ",\n    ".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("signed_bin", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--symbol-prefix", default="tag_ota_image")
    args = parser.parse_args()

    data = args.signed_bin.read_bytes()
    if len(data) < 32:
        raise SystemExit("signed image is too small to contain an MCUboot header")

    file_sha = hashlib.sha256(data).digest()
    _, _, header_size, protected_tlv_size, image_size, _, _, _ = (
        struct.unpack_from("<IIHHII8sI", data, 0)
    )
    image_hash_length = header_size + image_size + protected_tlv_size
    if image_hash_length > len(data):
        raise SystemExit(
            f"MCUboot hash length {image_hash_length} exceeds image size {len(data)}"
        )
    image_sha = hashlib.sha256(data[:image_hash_length]).digest()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    prefix = args.symbol_prefix
    args.output.write_text(
        "/* Auto-generated from the SHA-pinned signed OTA image. */\n"
        "#include <stdint.h>\n"
        "#include <stddef.h>\n\n"
        f"const uint8_t {prefix}[] = {{\n    "
        + format_bytes(data)
        + "\n};\n"
        f"const size_t {prefix}_len = sizeof({prefix});\n"
        f"const uint8_t {prefix}_sha256[32] = {{\n    "
        + ", ".join(f"0x{value:02x}" for value in file_sha)
        + "\n};\n"
        f"const uint8_t {prefix}_image_hash[32] = {{\n    "
        + ", ".join(f"0x{value:02x}" for value in image_sha)
        + "\n};\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
