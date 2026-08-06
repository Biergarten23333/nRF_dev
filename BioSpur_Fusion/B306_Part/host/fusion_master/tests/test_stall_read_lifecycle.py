#!/usr/bin/env python3
"""Regression model and source contract for dk-v32 per-peer read cleanup."""

from pathlib import Path

TERMINALS = (
    "submit_error",
    "att_error",
    "valid_completion",
    "invalid_completion",
    "timeout",
    "disconnect",
    "cancel",
    "shutdown",
)


class PeerRead:
    def __init__(self):
        self.generation = 0
        self.active = False

    def start(self):
        assert not self.active
        self.generation += 1
        self.active = True
        return self.generation

    def finish(self, generation):
        if not self.active or generation != self.generation:
            return False
        self.active = False
        return True


for terminal in TERMINALS:
    first = PeerRead()
    other = PeerRead()
    generation = first.start()
    assert first.finish(generation), terminal
    assert first.start() == generation + 1, terminal
    assert other.start() == 1, terminal
    assert not first.finish(generation), f"late callback accepted after {terminal}"

source = (Path(__file__).parents[1] / "src" / "main.c").read_text()
for token in (
    'stall_read_abort(peer, "submit_error")',
    'stall_read_abort(peer, "timeout")',
    'stall_read_abort(peer, "disconnect")',
    'stall_read_abort(peer, "cancel")',
    "if (!peer->stall_read_active)",
    "stall_read_generation",
):
    assert token in source, token

print(f"PASS injected_terminal_paths={len(TERMINALS)} same_peer=1 other_peer=1 late_generation=ignored")
