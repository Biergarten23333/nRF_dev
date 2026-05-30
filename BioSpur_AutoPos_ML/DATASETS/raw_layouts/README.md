# Raw Layouts

Place layout files here when they are already identifiable as candidate anchor
layouts.

Expected future canonical shape:

```json
{
  "layout_id": "...",
  "source_run": "...",
  "solver_version": "...",
  "unit": "mm",
  "anchors": [
    {"anchor_id": 0, "x": 0.0, "y": 0.0, "z": 2500.0}
  ]
}
```

The exact schema will be versioned once ingestion starts.
