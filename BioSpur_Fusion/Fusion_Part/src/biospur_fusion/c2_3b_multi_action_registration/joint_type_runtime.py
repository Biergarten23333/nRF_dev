"""Fresh pinned IMT runtime and bounded subprocess ownership for the micro-stage."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Any


IMT_CHILD = r'''
import json
import os
import time
import numpy as np

import imt
from imt.wrappers._jpos import _jpos_solve

data=np.load(os.environ["BIOSPUR_IMT_INPUT"])
arrays={name:np.ascontiguousarray(data[name],dtype=np.float64) for name in ("acc1","gyr1","acc2","gyr2")}
np.random.seed(101)
started=time.monotonic()
solver=imt.Solver(
    [-1,0],
    [
        imt.methods.NoOpMethod(),
        imt.wrappers.JointPosition(
            imt.methods.NoOpMethod(),dof_is_1d=False,verbose=False,
            num_workers=0,buffer_size_T=10,stop_once_buffer_full=True,
        ),
    ],
    0.01,
    body_names=["parent","child"],
)
_,extras=solver.step({
    "parent":{"acc":arrays["acc1"],"gyr":arrays["gyr1"]},
    "child":{"acc":arrays["acc2"],"gyr":arrays["gyr2"]},
})
public_wall=time.monotonic()-started
public_parent=np.asarray(extras["child"]["joint-center-to-body1"],dtype=np.float64)
public_child=np.asarray(extras["child"]["joint-center-to-body2"],dtype=np.float64)
solver.close()

guesses=np.random.RandomState(101).normal(size=(3,6))*0.2
comparators=[]
for index,guess in enumerate(guesses):
    one=time.monotonic()
    parent,child,info=_jpos_solve(
        arrays["acc1"],arrays["gyr1"],arrays["acc2"],arrays["gyr2"],
        100.0,verbose=False,initial_guess=guess,
    )
    result=info["scipy_minimize_result"]
    comparators.append({
        "index":index,
        "initial_guess_m":guess.tolist(),
        "parent_m":np.asarray(parent,dtype=np.float64).tolist(),
        "child_m":np.asarray(child,dtype=np.float64).tolist(),
        "final_residual_m_s2":float(info["final residual m/s**2"]),
        "nit":int(result.nit),
        "nfev":int(result.nfev),
        "njev":None if getattr(result,"njev",None) is None else int(result.njev),
        "success":bool(result.success),
        "status":int(result.status),
        "message":str(result.message),
        "fun":float(result.fun),
        "wall_s":time.monotonic()-one,
    })

third_parent=np.asarray(comparators[2]["parent_m"],dtype=np.float64)
third_child=np.asarray(comparators[2]["child_m"],dtype=np.float64)
payload={
    "public_parent_m":public_parent.tolist(),
    "public_child_m":public_child.tolist(),
    "public_iteration_telemetry":None,
    "public_wall_s":public_wall,
    "comparators":comparators,
    "public_vs_third_parent_m":float(np.linalg.norm(public_parent-third_parent)),
    "public_vs_third_child_m":float(np.linalg.norm(public_child-third_child)),
    "array_shapes":{name:list(value.shape) for name,value in arrays.items()},
    "array_dtypes":{name:str(value.dtype) for name,value in arrays.items()},
    "array_c_contiguous":{name:bool(value.flags.c_contiguous) for name,value in arrays.items()},
    "public_calls":1,
    "internal_bfgs_calls":3,
    "private_comparator_bfgs_calls":3,
}
print(json.dumps(payload,sort_keys=True))
'''


IMPORT_CHILD = r'''
import hashlib
import importlib.metadata as md
import inspect
import json
import os
from pathlib import Path
import sys
import time

def digest(path):
    h=hashlib.sha256()
    with open(path,"rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()

started=time.monotonic()
import imt
import qmt
import_elapsed=time.monotonic()-started
imt_root=Path(os.environ["IMT_SOURCE_ROOT"]).resolve()
qmt_root=Path(os.environ["QMT_SOURCE_ROOT"]).resolve()
site=Path(os.environ["AUDIT_SITE_PACKAGES"]).resolve()
assert Path(imt.__file__).resolve().is_relative_to(imt_root)
assert Path(qmt.__file__).resolve().is_relative_to(qmt_root)
unique={}
module_rows=[]
for name,module in sorted(sys.modules.items()):
    filename=getattr(module,"__file__",None)
    if not filename:
        continue
    path=Path(filename).resolve()
    if path.is_relative_to(imt_root):
        normalized="imt_source/"+str(path.relative_to(imt_root))
    elif path.is_relative_to(qmt_root):
        normalized="qmt_source/"+str(path.relative_to(qmt_root))
    elif path.is_relative_to(site):
        normalized="site-packages/"+str(path.relative_to(site))
    else:
        continue
    if path.is_file():
        unique[normalized]={"path":normalized,"sha256":digest(path),"bytes":path.stat().st_size}
        module_rows.append({"module":name,"path":normalized})
distribution_rows=[]
for name in json.loads(os.environ["AUDIT_DISTRIBUTION_NAMES"]):
    dist=md.distribution(name)
    metadata_path=Path(dist._path)/"METADATA"
    license_rows=[]
    for entry in sorted(dist.files or [],key=str):
        upper=str(entry).upper()
        if any(token in upper for token in ("LICENSE","COPYING","NOTICE")):
            path=Path(dist.locate_file(entry))
            if path.is_file():
                license_rows.append({
                    "path":"site-packages/"+str(path.resolve().relative_to(site)),
                    "sha256":digest(path),"bytes":path.stat().st_size,
                })
    distribution_rows.append({
        "name":dist.metadata["Name"],"version":dist.version,
        "metadata_path":"site-packages/"+str(metadata_path.resolve().relative_to(site)),
        "metadata_sha256":digest(metadata_path),"license_files":license_rows,
    })
print(json.dumps({
    "import_elapsed_s":import_elapsed,
    "imt_file":"imt_source/"+str(Path(imt.__file__).resolve().relative_to(imt_root)),
    "qmt_file":"qmt_source/"+str(Path(qmt.__file__).resolve().relative_to(qmt_root)),
    "signatures":{
        "Solver":str(inspect.signature(imt.Solver)),
        "JointPosition":str(inspect.signature(imt.wrappers.JointPosition)),
        "NoOpMethod":str(inspect.signature(imt.methods.NoOpMethod)),
    },
    "imported_modules":module_rows,
    "imported_unique_files":[unique[key] for key in sorted(unique)],
    "distributions":distribution_rows,
    "source_pycache_count":sum(1 for root in (imt_root,qmt_root) for p in root.rglob("__pycache__")),
    "source_nonreadonly_mode_count":sum(
        1 for root in (imt_root,qmt_root) for p in root.rglob("*")
        if p.is_file() and (p.stat().st_mode & 0o222)
    ),
    "estimator_calls":0,
},sort_keys=True))
'''


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RuntimeBound(RuntimeError):
    """Raised when an approved runtime, stage, or disk boundary is reached."""


class PinnedImtRuntime:
    """Own one temporary, read-only IMT/QMT runtime for exactly three public calls."""

    def __init__(
        self,
        repo_root: Path,
        output_dir: Path,
        aggregate_started: float,
        aggregate_deadline: float,
        transient_limit_bytes: int,
    ) -> None:
        self.repo_root = repo_root
        self.output_dir = output_dir
        self.aggregate_started = aggregate_started
        self.aggregate_deadline = aggregate_deadline
        self.transient_limit_bytes = transient_limit_bytes
        self.temp = Path(tempfile.mkdtemp(prefix="biospur_c2_3b_jtf_"))
        self.python = self.temp / "venv/bin/python"
        self.source_paths: dict[str, Path] = {}
        self.disk_samples: list[dict[str, Any]] = []
        self._closed = False

    def _remaining(self, stage_deadline: float | None = None) -> float:
        deadline = self.aggregate_deadline
        if stage_deadline is not None:
            deadline = min(deadline, stage_deadline)
        return deadline - time.monotonic()

    @staticmethod
    def clean_environment() -> dict[str, str]:
        """Remove caller Python-path ownership before creating the pinned runtime."""

        env = os.environ.copy()
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(name, None)
        env.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        })
        return env

    def _run(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        stage_deadline: float | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], float]:
        remaining = self._remaining(stage_deadline)
        if remaining <= 0:
            raise RuntimeBound("RUNTIME_BOUND before subprocess")
        started = time.monotonic()
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            raise RuntimeBound(f"RUNTIME_BOUND: {' '.join(command[:3])}")
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), time.monotonic() - started

    @staticmethod
    def _allocated_bytes(path: Path) -> int:
        if not path.exists():
            return 0
        result = subprocess.run(
            ["du", "-s", "--block-size=1", str(path)],
            text=True,
            capture_output=True,
            check=True,
        )
        return int(result.stdout.split()[0])

    def sample_disk(self, stage: str) -> dict[str, Any]:
        temp_bytes = self._allocated_bytes(self.temp)
        result_bytes = self._allocated_bytes(self.output_dir)
        total = temp_bytes + result_bytes
        row = {
            "stage": stage,
            "temp_allocated_bytes": temp_bytes,
            "result_allocated_bytes": result_bytes,
            "run_owned_allocated_bytes": total,
            "limit_bytes": self.transient_limit_bytes,
            "pass": total <= self.transient_limit_bytes,
        }
        self.disk_samples.append(row)
        if not row["pass"]:
            raise RuntimeBound("TRANSIENT_DISK_BOUND")
        return row

    def environment(self) -> dict[str, str]:
        site = next((self.temp / "venv/lib").glob("python*/site-packages"))
        env = self.clean_environment()
        env.update({
            "IMT_SOURCE_ROOT": str(self.source_paths["imt-imt"]),
            "QMT_SOURCE_ROOT": str(self.source_paths["qmt"]),
            "AUDIT_SITE_PACKAGES": str(site),
            "PYTHONPATH": (
                str(self.source_paths["imt-imt"] / "src")
                + os.pathsep
                + str(self.source_paths["qmt"])
            ),
        })
        return env

    def bootstrap(self, closure: dict[str, Any], canonical_audit: dict[str, Any]) -> dict[str, Any]:
        stage_started = time.monotonic()
        stage_deadline = stage_started + 70.0
        venv, wall_venv = self._run(
            [closure["python"]["executable"], "-m", "venv", str(self.temp / "venv")],
            env=self.clean_environment(),
            stage_deadline=stage_deadline,
        )
        if venv.returncode:
            raise RuntimeBound(f"IMT_SOURCE_OR_API: venv: {venv.stderr[-1000:]}")
        self.sample_disk("venv")
        wheel_specs = [
            f'{row["name"]} @ {row["url"]}#sha256={row["sha256"]}'
            for row in closure["runtime_wheels"]
        ]
        installed, wall_install = self._run(
            [
                str(self.python), "-m", "pip", "install", "--disable-pip-version-check",
                "--no-cache-dir", "--no-deps", *wheel_specs,
            ],
            env=self.clean_environment(),
            stage_deadline=stage_deadline,
        )
        if installed.returncode:
            raise RuntimeBound(f"IMT_SOURCE_OR_API: install: {installed.stderr[-1000:]}")
        self.sample_disk("wheel_install")
        clone_rows: list[dict[str, Any]] = []
        clone_wall = 0.0
        for source in closure["source_trees"]:
            destination = self.temp / source["name"]
            cloned, elapsed = self._run(
                ["git", "clone", "--quiet", source["repository"], str(destination)],
                stage_deadline=stage_deadline,
            )
            clone_wall += elapsed
            if cloned.returncode:
                raise RuntimeBound(f"IMT_SOURCE_OR_API: clone {source['name']}: {cloned.stderr[-1000:]}")
            checked, elapsed = self._run(
                ["git", "-C", str(destination), "checkout", "--quiet", source["revision"]],
                stage_deadline=stage_deadline,
            )
            clone_wall += elapsed
            if checked.returncode:
                raise RuntimeBound(f"IMT_SOURCE_OR_API: checkout {source['name']}: {checked.stderr[-1000:]}")
            head, _ = self._run(
                ["git", "-C", str(destination), "rev-parse", "HEAD"],
                stage_deadline=stage_deadline,
            )
            if head.returncode or head.stdout.strip() != source["revision"]:
                raise RuntimeBound(f"IMT_SOURCE_OR_API: HEAD {source['name']}")
            self.source_paths[source["name"]] = destination
            clone_rows.append({"name": source["name"], "revision": head.stdout.strip()})
            for path in destination.rglob("*"):
                if path.is_file():
                    path.chmod(path.stat().st_mode & ~0o222)
            self.sample_disk(f"clone_{source['name']}")
        env = self.environment()
        env["AUDIT_DISTRIBUTION_NAMES"] = json.dumps(
            [row["name"] for row in closure["runtime_wheels"]]
        )
        imported, wall_import = self._run(
            [str(self.python), "-c", IMPORT_CHILD],
            env=env,
            stage_deadline=stage_deadline,
        )
        if imported.returncode:
            raise RuntimeBound(f"IMT_SOURCE_OR_API: import: {imported.stderr[-1000:]}")
        child = json.loads(imported.stdout)
        if child["signatures"] != closure["public_signatures"]:
            raise RuntimeBound("IMT_SOURCE_OR_API: public signature")
        expected_versions = {
            row["name"].lower(): row["version"] for row in closure["runtime_wheels"]
        }
        observed_versions = {
            row["name"].lower(): row["version"] for row in child["distributions"]
        }
        if observed_versions != expected_versions:
            raise RuntimeBound("IMT_SOURCE_OR_API: distribution versions")
        for key in ("imported_unique_files", "imported_modules"):
            if child[key] != canonical_audit[key]:
                raise RuntimeBound(f"IMT_SOURCE_OR_API: {key} closure")
        if child["distributions"] != canonical_audit["distributions"]:
            raise RuntimeBound("IMT_SOURCE_OR_API: distribution metadata/license closure")
        if child["source_pycache_count"] or child["source_nonreadonly_mode_count"]:
            raise RuntimeBound("IMT_SOURCE_OR_API: source mutation")
        self.sample_disk("public_import_guard")
        elapsed = time.monotonic() - stage_started
        if elapsed > 70.0:
            raise RuntimeBound("RTG00_RUNTIME_BOUND")
        return {
            "id": "RTG00_RUNTIME_AND_HASH_GUARD",
            "pass": True,
            "wall_s": {
                "venv": wall_venv,
                "wheel_install": wall_install,
                "source_clone_checkout": clone_wall,
                "public_import_and_hash": wall_import,
                "stage": elapsed,
            },
            "wall_limit_s": 70.0,
            "venv_exit": venv.returncode,
            "install_exit": installed.returncode,
            "install_stdout_sha256": _sha256(installed.stdout.encode()),
            "install_stderr_sha256": _sha256(installed.stderr.encode()),
            "source_revisions": clone_rows,
            "import_audit": child,
            "disk_samples": list(self.disk_samples),
            "estimator_calls": 0,
        }

    def call_imt(self, input_path: Path, stage_deadline: float) -> dict[str, Any]:
        env = self.environment()
        env["BIOSPUR_IMT_INPUT"] = str(input_path)
        result, wall = self._run(
            [str(self.python), "-c", IMT_CHILD],
            env=env,
            stage_deadline=stage_deadline,
        )
        if result.returncode:
            raise RuntimeBound(f"IMT_SOURCE_OR_API: estimator: {result.stderr[-2000:]}")
        payload = json.loads(result.stdout)
        payload["subprocess_wall_s"] = wall
        payload["stdout_sha256"] = _sha256(result.stdout.encode())
        payload["stderr_sha256"] = _sha256(result.stderr.encode())
        if payload["public_vs_third_parent_m"] > 1e-12 or payload["public_vs_third_child_m"] > 1e-12:
            raise RuntimeBound("IMT_SOURCE_OR_API: public/private mismatch")
        self.sample_disk("imt_call")
        return payload

    def close(self) -> None:
        if self._closed:
            return
        for root in self.source_paths.values():
            for path in root.rglob("*"):
                if path.is_file():
                    path.chmod(path.stat().st_mode | 0o200)
        shutil.rmtree(self.temp)
        self._closed = True
