# BioSpur Fusion v1 reference

Clean-slate scientific reference implementation. This package decodes raw v47
records directly and does not import `biospur_fusion` or any historical solver.

Stage A:

```bash
python3 -m fusion_v1.cli audit \
  --capture ../logs/v47_ten_node_body_calibration_20260814_093601 \
  --output ../logs/fusion_v1_reference_20260816_$(date +%H%M%S)
```

The command verifies the raw hash, COBS envelope, CRC, payload lengths,
measurement timestamps and sequence counters. It writes manifests and a
gzip-compressed canonical observation table with raw-byte lineage.

