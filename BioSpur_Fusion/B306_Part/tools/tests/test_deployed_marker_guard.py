#!/usr/bin/env python3

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


def invoke(script: Path, elf: Path, artifact: Path, manifest: Path):
    return subprocess.run(
        [
            "python3",
            str(script),
            "--elf",
            str(elf),
            "--artifact",
            str(artifact),
            "--manifest",
            str(manifest),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    script = Path(__file__).resolve().parents[1] / "check_deployed_marker.py"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        elf = root / "zephyr.elf"
        artifact = root / "zephyr.signed.bin"
        manifest = root / "deployed_markers.json"
        artifact.write_bytes(b"same bytes")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

        elf.write_bytes(b"\x00b306-new-v20\x00")
        manifest.write_text(json.dumps({"markers": {}}))
        assert invoke(script, elf, artifact, manifest).returncode == 0

        elf.write_bytes(b"\x00b306-old-v18\x00")
        manifest.write_text(
            json.dumps(
                {"markers": {"b306-old-v18": {"signed_sha256": [digest]}}}
            )
        )
        assert invoke(script, elf, artifact, manifest).returncode == 0

        artifact.write_bytes(b"different bytes")
        rejected = invoke(script, elf, artifact, manifest)
        assert rejected.returncode != 0
        assert "MARKER_GUARD_FAIL" in rejected.stderr

        manifest.write_text(
            json.dumps(
                {
                    "markers": {
                        "b306-old-v18": {
                            "retired": True,
                            "signed_sha256": [hashlib.sha256(
                                artifact.read_bytes()
                            ).hexdigest()],
                        }
                    }
                }
            )
        )
        retired = invoke(script, elf, artifact, manifest)
        assert retired.returncode != 0
        assert "status=retired" in retired.stderr

    print("DEPLOYED_MARKER_GUARD_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

