"""Fail-closed frozen inputs and fleet/readiness gates."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

EXPECTED_NODES = frozenset({
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
    "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35",
})
CENTRAL = "BSF31CC"
MASTER = "dk-fusion-imu-relay-v36"
MARKER = "b306-imu-relay-v47"
FWID = "f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed"
ACTIVE_SHA = "90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98"
LAYOUT_SHA = "20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1"
ANCHORS = tuple("ABCDEFGH")


class ContractError(ValueError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@dataclass(frozen=True)
class CalibrationContract:
    layout: Path
    geometry_manifest: Path
    slots: Path

    def validate(self) -> dict:
        if self.layout.name != "V4IO_LAYOUT.json" or "/V4IO/" in self.layout.as_posix():
            raise ContractError("reflected/intermediate geometry is forbidden")
        if sha256(self.layout) != LAYOUT_SHA:
            raise ContractError("canonical V4-io layout SHA mismatch")
        manifest = json.loads(self.geometry_manifest.read_text())
        if not manifest.get("geometry_capture_bound") or manifest.get("delay_convention") != "DELAY_CONVENTION_PASS":
            raise ContractError("geometry is not capture-bound with the frozen delay convention")
        identities = manifest.get("anchor_identity", {})
        if tuple(identities[str(i)]["label"] for i in range(8)) != ANCHORS:
            raise ContractError("canonical Anchor A-H identity mismatch")
        if not self.slots.exists():
            raise ContractError("authoritative ten-slot topology file is missing")
        topology = json.loads(self.slots.read_text())
        slots = topology.get("slots", [])
        if topology.get("schema") != "biospur-ten-node-body-slots-v1" or len(slots) != 10:
            raise ContractError("slot topology must be authoritative schema v1 with exactly ten slots")
        names = [s.get("name") for s in slots]
        if len(set(names)) != 10 or any(not x for x in names):
            raise ContractError("slot names must be ten unique non-empty values")
        central_slots = [s["name"] for s in slots if s.get("central") is True]
        if len(central_slots) != 1:
            raise ContractError("topology must designate exactly one central slot")
        edges = topology.get("joints", [])
        if not edges or any(e.get("a") not in names or e.get("b") not in names for e in edges):
            raise ContractError("topology joint graph is absent or references unknown slots")
        return {"layout_sha256": LAYOUT_SHA, "anchors": identities,
                "delay_convention": manifest["delay_convention"], "slots": names,
                "central_slot": central_slots[0], "topology_sha256": sha256(self.slots)}


def validate_readiness(observation: dict) -> None:
    peers = observation.get("peers", [])
    names = [p.get("name") for p in peers]
    failures = []
    if observation.get("master") != MASTER: failures.append("master")
    if observation.get("central") != CENTRAL: failures.append("central")
    if len(names) != 10 or set(names) != EXPECTED_NODES: failures.append("membership")
    if len(names) != len(set(names)): failures.append("duplicate")
    for peer in peers:
        if not (peer.get("connected") and peer.get("subscribed")): failures.append("link")
        if peer.get("marker") != MARKER or peer.get("fwid") != FWID: failures.append("identity")
        if peer.get("active_sha") != ACTIVE_SHA or peer.get("confirmed") != 1: failures.append("image")
    if tuple(observation.get("anchors", ())) != ANCHORS: failures.append("anchors")
    if not observation.get("listeners_ok"): failures.append("listeners")
    if failures: raise ContractError("readiness rejected: " + ",".join(sorted(set(failures))))
