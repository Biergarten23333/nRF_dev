# Data Assumptions

## Anchor Layout

The expected BioSpur 8-anchor geometry is:

- `A B C D`: lower physical layer
- `E F G H`: upper physical layer
- `A B C D` are placed counter-clockwise
- `E F G H` are placed counter-clockwise
- vertical anchor pairs are `A-E`, `B-F`, `C-G`, `D-H`

Canonical anchor IDs:

```text
A=0 B=1 C=2 D=3 E=4 F=5 G=6 H=7
```

## Coordinate Convention

Native AutoPos layout JSON files often use a z-axis convention where the upper
physical layer has more-negative `z_mm` values. For physical layer checks in
native layouts, scripts treat physical height as approximately `-z_mm`.

The derived `layout_us_height.json` files are already transformed into a
height-like convention, so they are checked with physical height as `z_mm`.

## Current Use In Features

`scripts/extract_layout_features.py` computes:

- lower/upper layer mean z
- paired vertical gap for `A-E`, `B-F`, `C-G`, `D-H`
- paired XY offset
- lower/upper ring area
- lower/upper ring orientation
- expected layer/order flags

These are geometry features and sanity checks; raw capture files are never
modified.
