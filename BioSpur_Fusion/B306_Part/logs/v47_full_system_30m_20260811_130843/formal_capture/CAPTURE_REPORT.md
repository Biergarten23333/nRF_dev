# v47 30-minute full-system capture

Result: **PASS**. The single formal run started at 2026-08-11T13:09:59.019+02:00 and stopped after 1800.000110 s with reason `PLANNED_DURATION_COMPLETE`. Formal transport error/drop deltas were {'raw_queue_drops': 0, 'decoded_queue_drops': 0, 'log_queue_drops': 0, 'frame_crc_decode_errors': 0, 'payload_decode_errors': 0, 'red_markers': 0, 'reader_exceptions': 0}; raw submitted and written bytes were both 109243079; decoded backlog at close was 0. The one serial-attach COBS fragment is explicitly pre-T0 and excluded from the formal window.

All ten nodes delivered continuous approximately 200 Hz IMU and 8.33 Hz Fusion UWB. IMU sequences have zero gaps, UWB sweep sequences have zero gaps, strict IMU tuples and all eight UWB slots validate, and raw replay matches the decoded log in order. Listener parse and serial errors are zero; 248 trailing bytes are shutdown-boundary fragments. Dynamic truth is `NOT_APPLICABLE_NO_TRUTH`. No prohibited hardware action or active formal diagnostic command occurred.

Sizes: raw 109243079 B; decoded text 326286014 B; Listener evidence 2352118503 B.
