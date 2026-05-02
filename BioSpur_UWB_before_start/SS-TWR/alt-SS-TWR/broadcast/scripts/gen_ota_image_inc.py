#!/usr/bin/env python3
import argparse
import hashlib
import struct
from pathlib import Path


def format_bytes(data: bytes) -> str:
    lines = []
    for i in range(0, len(data), 12):
        chunk = data[i:i + 12]
        lines.append(", ".join(f"0x{b:02x}" for b in chunk))
    return ",\n    ".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ota_image.inc from a signed binary")
    parser.add_argument("signed_bin", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--symbol-prefix", default="tag_ota_image")
    args = parser.parse_args()

    data = args.signed_bin.read_bytes()
    file_sha = hashlib.sha256(data).digest()

    if len(data) < 32:
        raise SystemExit("Signed image is too small to contain an MCUboot header")

    _, _, hdr_size, protect_tlv_size, img_size, _, _, _ = struct.unpack_from(
        "<IIHHII8sI", data, 0
    )
    image_hash_len = hdr_size + img_size + protect_tlv_size
    if image_hash_len > len(data):
        raise SystemExit(
            f"MCUboot hash length {image_hash_len} exceeds signed image size {len(data)}"
        )
    image_sha = hashlib.sha256(data[:image_hash_len]).digest()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        f.write("/* Auto-generated from signed OTA image. */\n")
        f.write("#include <stdint.h>\n")
        f.write("#include <stddef.h>\n\n")
        f.write(f"const uint8_t {args.symbol_prefix}[] = {{\n    ")
        f.write(format_bytes(data))
        f.write("\n};\n")
        f.write(f"const size_t {args.symbol_prefix}_len = sizeof({args.symbol_prefix});\n")
        f.write(f"const uint8_t {args.symbol_prefix}_sha256[32] = {{\n    ")
        f.write(", ".join(f"0x{b:02x}" for b in file_sha))
        f.write("\n};\n")
        f.write(f"const uint8_t {args.symbol_prefix}_image_hash[32] = {{\n    ")
        f.write(", ".join(f"0x{b:02x}" for b in image_sha))
        f.write("\n};\n")

    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
