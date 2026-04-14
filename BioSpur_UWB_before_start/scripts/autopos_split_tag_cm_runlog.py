#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Split a tag CM run.log into train/test chunks by CM notify lines.")
    ap.add_argument("--run-log", required=True)
    ap.add_argument("--out-train", required=True)
    ap.add_argument("--out-test", required=True)
    ap.add_argument("--cm-train-lines", type=int, default=80)
    ap.add_argument("--cm-test-lines", type=int, default=30)
    ap.add_argument("--tag-name", default="BSF66F")
    args = ap.parse_args()

    run_log = Path(args.run_log)
    text = run_log.read_text(encoding="utf-8", errors="ignore")

    cm_lines = [ln for ln in text.splitlines(keepends=True) if f"{args.tag_name} notify: CM;" in ln]
    total = len(cm_lines)
    if total == 0:
        raise SystemExit("[error] no CM notify lines found")

    train_n = max(0, min(args.cm_train_lines, total))
    remain = total - train_n
    test_n = max(0, min(args.cm_test_lines, remain))

    train = "".join(cm_lines[:train_n])
    test = "".join(cm_lines[train_n : train_n + test_n])

    out_train = Path(args.out_train)
    out_test = Path(args.out_test)
    out_train.parent.mkdir(parents=True, exist_ok=True)
    out_test.parent.mkdir(parents=True, exist_ok=True)
    out_train.write_text(train, encoding="utf-8")
    out_test.write_text(test, encoding="utf-8")

    print(f"[ok] total_cm_lines={total} train={train_n} test={test_n} out_train={out_train} out_test={out_test}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

