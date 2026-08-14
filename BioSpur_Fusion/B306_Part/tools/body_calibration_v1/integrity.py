"""Lossless stream accounting and formal-window integrity gates."""
from __future__ import annotations
from dataclasses import dataclass

class IntegrityError(ValueError): pass

@dataclass
class StreamAudit:
    raw_bytes_read: int = 0
    raw_bytes_written: int = 0
    decoder_consumed: int = 0
    boundary_prefix: int = 0
    boundary_suffix: int = 0
    sequence_gaps: int = 0
    duplicates: int = 0
    timestamp_reversals: int = 0
    queue_drops: int = 0
    opens: int = 1
    raw_files: int = 1

    def validate(self):
        if self.opens != 1 or self.raw_files != 1: raise IntegrityError("not one-open/one-raw lifecycle")
        if self.raw_bytes_read != self.raw_bytes_written: raise IntegrityError("raw read/write accounting mismatch")
        if self.decoder_consumed + self.boundary_prefix + self.boundary_suffix != self.raw_bytes_written:
            raise IntegrityError("decoder byte accounting does not close")
        bad = self.sequence_gaps + self.duplicates + self.timestamp_reversals + self.queue_drops
        if bad: raise IntegrityError("formal window is not lossless")
        return True

class LiveCatchup:
    def __init__(self, required=5): self.required=required; self.stable=0
    def update(self, *, decoded_depth, raw_depth, source_age_delta_ms, gaps=0):
        ok = decoded_depth == 0 and raw_depth <= 1 and abs(source_age_delta_ms) <= 2 and gaps == 0
        self.stable = self.stable + 1 if ok else 0
        return self.stable >= self.required
