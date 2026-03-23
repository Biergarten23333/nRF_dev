# BioSpur UWB UI

Standalone Flutter UI shell for the BioSpur UWB workflows.

## Layout

- `lib/main.dart`: app entry point
- `lib/app.dart`: top-level app shell with tabs
- `lib/features/`: tab pages for dashboard, live view, sessions, 3D view, and autopositioning

## Start

Run the helper script:

```bash
./run.sh
```

Or run Flutter directly:

```bash
flutter run
```

For desktop:

```bash
flutter run -d linux
```

## Purpose

This UI is intentionally separated from the firmware tree. It should help with:

- live device status
- direct serial live feed without requiring log recording
- session control
- autopositioning workflow
- log review
