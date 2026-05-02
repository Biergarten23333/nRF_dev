#!/usr/bin/env python3
import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path


ANCHORS = ("A", "B", "C", "D", "E", "F", "G", "H")


def pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def load_existing_distances(path: Path) -> dict[tuple[str, str], int]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for k, v in raw.get("distances", {}).items():
        a, b = k.split("-")
        out[pair_key(a, b)] = int(v)
    return out


def robust_mm(values: list[float]) -> tuple[float, float]:
    med = float(statistics.median(values))
    abs_dev = [abs(v - med) for v in values]
    mad = float(statistics.median(abs_dev)) if abs_dev else 0.0
    return med, mad


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a fresh inter-anchor matrix JSON from pairwise ranging samples."
    )
    parser.add_argument(
        "--pairs-csv",
        required=True,
        help="CSV with columns: a,b,dist_mm[,quality]. One row per pair sample.",
    )
    parser.add_argument(
        "--output",
        default="data/inter_anchor_matrix_ah.json",
        help="Output matrix JSON path.",
    )
    parser.add_argument(
        "--existing-matrix",
        default=None,
        help="Optional old matrix for fallback on missing pairs (fallbacks are marked in notes).",
    )
    parser.add_argument(
        "--min-samples-per-pair",
        type=int,
        default=8,
        help="Minimum number of samples required for a pair to be considered fresh.",
    )
    parser.add_argument(
        "--max-mad-mm",
        type=float,
        default=180.0,
        help="Pairs with MAD above this are flagged as noisy in notes.",
    )
    args = parser.parse_args()

    pairs_csv = Path(args.pairs_csv).resolve()
    output = Path(args.output).resolve()
    existing = load_existing_distances(Path(args.existing_matrix).resolve()) if args.existing_matrix else {}

    samples: dict[tuple[str, str], list[float]] = defaultdict(list)
    with pairs_csv.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            a = row["a"].strip().upper()
            b = row["b"].strip().upper()
            if a == b:
                continue
            if a not in ANCHORS or b not in ANCHORS:
                continue
            d = float(row["dist_mm"])
            samples[pair_key(a, b)].append(d)

    distances_out = {}
    pair_stats = {}
    notes = [
        "Fresh inter-anchor matrix generated from pairwise sample CSV.",
        "Lower anchors were physically moved; previous matrix should not be treated as current truth.",
    ]
    noisy_pairs = []
    fallback_pairs = []

    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            pk = pair_key(a, b)
            key = f"{a}-{b}"
            vals = samples.get(pk, [])
            if len(vals) >= args.min_samples_per_pair:
                med, mad = robust_mm(vals)
                distances_out[key] = int(round(med))
                pair_stats[key] = {
                    "sample_count": len(vals),
                    "median_mm": med,
                    "mad_mm": mad,
                    "source": "fresh",
                }
                if mad > args.max_mad_mm:
                    noisy_pairs.append(key)
            elif pk in existing:
                distances_out[key] = int(existing[pk])
                pair_stats[key] = {
                    "sample_count": len(vals),
                    "median_mm": None,
                    "mad_mm": None,
                    "source": "fallback_existing",
                }
                fallback_pairs.append(key)
            else:
                pair_stats[key] = {
                    "sample_count": len(vals),
                    "median_mm": None,
                    "mad_mm": None,
                    "source": "missing",
                }

    if noisy_pairs:
        notes.append(f"Noisy pairs (MAD > {args.max_mad_mm} mm): {', '.join(sorted(noisy_pairs))}")
    if fallback_pairs:
        notes.append(f"Fallback-to-existing pairs: {', '.join(sorted(fallback_pairs))}")

    out = {
        "units": "mm",
        "anchors": list(ANCHORS),
        "distances": distances_out,
        "pair_stats": pair_stats,
        "source": {
            "pairs_csv": str(pairs_csv),
            "existing_matrix": str(Path(args.existing_matrix).resolve()) if args.existing_matrix else None,
            "min_samples_per_pair": args.min_samples_per_pair,
            "max_mad_mm": args.max_mad_mm,
        },
        "notes": notes,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(f"wrote: {output}")
    print(f"fresh pairs: {sum(1 for s in pair_stats.values() if s['source'] == 'fresh')}")
    print(f"fallback pairs: {sum(1 for s in pair_stats.values() if s['source'] == 'fallback_existing')}")
    print(f"missing pairs: {sum(1 for s in pair_stats.values() if s['source'] == 'missing')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
