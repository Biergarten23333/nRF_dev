"""Execute only the frozen synthetic/contract test set authorized for Q4."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from command_specs import AUTHORIZED_TEST_GROUPS
from common import read_json, write_json


def _junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    cases = [case for suite in suites for case in suite.findall("testcase")]
    failures = [case.attrib.get("name", "") for case in cases if case.find("failure") is not None]
    errors = [case.attrib.get("name", "") for case in cases if case.find("error") is not None]
    skipped = [case.attrib.get("name", "") for case in cases if case.find("skipped") is not None]
    return {
        "tests": len(cases),
        "passes": len(cases) - len(failures) - len(errors) - len(skipped),
        "failures": failures,
        "errors": errors,
        "skipped_or_xfailed": skipped,
    }


def run_authorized_suite(root: Path, report: Path) -> None:
    targets = [path for group in AUTHORIZED_TEST_GROUPS for path in group]
    evidence = Path(os.environ["R26C_Q2_EVIDENCE_DIR"])
    junit = evidence / "authorized_suite.xml"
    completed = subprocess.run(
        [
            sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "--rootdir", str(root), "--import-mode=importlib",
            f"--junitxml={junit}", *targets,
        ],
        cwd=root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    (evidence / "authorized_pytest_stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (evidence / "authorized_pytest_stderr.txt").write_text(completed.stderr, encoding="utf-8")
    result = _junit(junit) if junit.exists() else {
        "tests": 0, "passes": 0, "failures": [], "errors": ["missing junit"], "skipped_or_xfailed": []
    }
    if (report / "COMMAND_ENVIRONMENT_MANIFEST.json").exists():
        frozen = read_json(report / "COMMAND_ENVIRONMENT_MANIFEST.json")["commands"]["authorized_suite"]
        expected_count = len(frozen["runtime_preflight"]["pytest"]["collected_nodeids"])
    else:
        expected_count = result["tests"]
    passed = (
        completed.returncode == 0
        and result["tests"] == expected_count
        and result["passes"] == expected_count
        and not result["failures"]
        and not result["errors"]
        and not result["skipped_or_xfailed"]
    )
    payload = {
        "schema": "biospur.phase3r26c_r2_q4.authorized_suite.v1",
        "status": "PASS" if passed else "FAIL",
        "targets": targets,
        "expected_collected_count": expected_count,
        "pytest_exit_code": completed.returncode,
        **result,
        "no_raw_session_solve": True,
        "scope": "frozen R2 harness, R1 regressions, and environment companion only",
    }
    target = report / "AUTHORIZED_SUITE_RESULT.json" if os.environ.get("R26C_Q2_FORMAL") == "1" else evidence / "AUTHORIZED_SUITE_RESULT.json"
    write_json(target, payload)
    if not passed:
        raise RuntimeError(f"authorized suite failed: {payload}")
