from __future__ import annotations


BASE_IMPORTS = (
    "BioSpur_Fusion",
    "biospur_fusion",
    "biospur_fusion.heading_anchor_audit_v2.core",
    "biospur_fusion.heading_anchor_audit_v2.heading_gauge",
    "biospur_fusion.heading_anchor_audit_v2.pipeline",
    "tools.fusion_v2.phase3r26c_r2_q2.command_dispatch",
)

FROZEN_TEST = "BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r26c_r2/test_frozen_contract.py"
SELF_TEST = "BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r26c_r2/test_harness_self.py"
R1_TESTS = (
    "BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r26c_r1/test_typed_boundary_r1.py",
    "BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r26c_r1/test_command_bound_regressions.py",
)
AUTHORIZED_TEST_GROUPS = (
    (
        FROZEN_TEST,
        SELF_TEST,
        *R1_TESTS,
        "BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r26c_r1/test_environment_skip_companion.py",
    ),
)


COMMANDS = {
    "q7_root_cause": {
        "command": "q7_root_cause", "exit": "zero", "collect": (),
        "imports": (
            "tools.fusion_v2.phase3r26c_r2_q2.q7_float_audit",
            "tools.fusion_v2.phase3r26c_r2_q2.float_semantic_equivalence",
        ),
    },
    "float_comparator_controls": {
        "command": "float_comparator_controls", "exit": "zero", "collect": (),
        "imports": (
            "tools.fusion_v2.phase3r26c_r2_q2.q7_float_audit",
            "tools.fusion_v2.phase3r26c_r2_q2.float_semantic_equivalence",
        ),
    },
    "environment_replication": {
        "command": "environment_replication", "exit": "zero", "collect": (),
        "imports": (
            "tools.fusion_v2.phase3r26c_r2_q2.q7_float_audit",
            "tools.fusion_v2.phase3r26c_r2_q2.float_semantic_equivalence",
            "tools.fusion_v2.phase3r26c_r2_q2.isolated_rerun",
        ),
    },
    "harness_lint": {"command": "harness_lint", "exit": "zero", "collect": ()},
    "harness_self_tests": {"command": "harness_self_tests", "exit": "zero", "collect": (SELF_TEST,)},
    "harness_mutation_tests": {
        "command": "harness_mutation_tests", "exit": "zero", "collect": (),
        "imports": ("tools.fusion_v2.phase3r26c_r2.harness_mutation_selftest",),
        "runner_preflight": "synthetic_mutant",
    },
    "frozen_red": {
        "command": "frozen_contract", "exit": "frozen_red", "collect": (FROZEN_TEST,),
        "source_mode": "clean_base_capsule",
    },
    "frozen_green": {"command": "frozen_contract", "exit": "zero", "collect": (FROZEN_TEST,)},
    "consumer_closure": {
        "command": "consumer_closure", "exit": "zero", "collect": (),
        "imports": ("tools.fusion_v2.phase3r26c_r2_q2.closure_checks",),
    },
    "h_boundary_closure": {
        "command": "h_boundary_closure", "exit": "zero", "collect": (),
        "imports": ("tools.fusion_v2.phase3r26c_r2_q2.closure_checks",),
    },
    "formal_schema_closure": {
        "command": "formal_schema_closure", "exit": "zero", "collect": (),
        "imports": ("tools.fusion_v2.phase3r26c_r2_q2.closure_checks",),
    },
    "r2_mutation_runner": {
        "command": "r2_mutation_runner", "exit": "zero", "collect": (FROZEN_TEST,),
        "imports": ("tools.fusion_v2.phase3r26c_r2_q2.mutation_runner",),
        "runner_preflight": "r2_structural_mutants",
    },
    "r1_mutation_replay": {
        "command": "r1_mutation_replay", "exit": "zero", "collect": R1_TESTS,
        "imports": (
            "tools.fusion_v2.phase3r26c_r2_q2.mutation_runner",
            "tools.fusion_v2.phase3r26c_r1.mutants",
            "tools.fusion_v2.phase3r26c_r1.mutation_probe",
        ),
        "runner_preflight": "r1_replay",
    },
    "negative_controls": {
        "command": "negative_controls", "exit": "negative_controls", "collect": (),
        "imports": (
            "tools.fusion_v2.phase3r26c_r2_q2.negative_controls",
            "tools.fusion_v2.phase3r26c_r2_q2.environment_gate",
        ),
    },
    "authorized_suite": {
        "command": "authorized_suite", "exit": "zero",
        "collect": tuple(path for group in AUTHORIZED_TEST_GROUPS for path in group),
        "imports": ("tools.fusion_v2.phase3r26c_r2_q2.authorized_suite",),
    },
    "deterministic_replay_a": {
        "command": "qualification_report_generation", "exit": "zero", "collect": (),
        "imports": ("tools.fusion_v2.phase3r26c_r2_q2.report_builder",),
    },
    "deterministic_replay_b": {
        "command": "qualification_report_generation", "exit": "zero", "collect": (),
        "imports": ("tools.fusion_v2.phase3r26c_r2_q2.report_builder",),
    },
    "isolated_rerun": {
        "command": "isolated_rerun", "exit": "zero", "collect": (FROZEN_TEST,),
        "imports": (
            "tools.fusion_v2.phase3r26c_r2_q2.isolated_rerun",
            "tools.fusion_v2.phase3r26c_r2_q2.closure_checks",
        ),
        "runner_preflight": "isolated_capsule",
    },
}


def spec_for(label: str) -> dict:
    row = dict(COMMANDS[label])
    row["imports"] = tuple(dict.fromkeys((*BASE_IMPORTS, *row.get("imports", ()))))
    return row
