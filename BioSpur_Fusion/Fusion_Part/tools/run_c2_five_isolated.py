#!/usr/bin/env python3
"""Run five-node stages without mounting other recordings or pose references.

Linux bubblewrap supplies a fresh filesystem and network namespace. Only
runtime libraries, source, released assets, declared five-IMU caches, tape
measurements and the current output directory are visible. This tests data
independence; it does not certify motion accuracy or erase development history.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / 'logs/c2_five_node_inertial_rework_20260906_133807'
SURFACE = ROOT / 'config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json'
STAGES = {
    'navigation': ('diagnose_c2_five_navigation.py', [], 120),
    'navigation-control': ('diagnose_c2_five_navigation.py', ['--vqf-control'], 120),
    'arm-probe': ('diagnose_c2_arm_protocol.py', ['probe'], 120),
    'arm-profile': ('diagnose_c2_arm_protocol.py', ['profile'], 1200),
    'geometric': ('diagnose_c2_five_geometric_hinge.py', [], 180),
    'tracking': ('diagnose_c2_five_tracking.py', [], 180),
    'frames': ('audit_c2_five_frozen_frames.py', [], 120),
    'optimizer': ('diagnose_c2_five_optimizer.py', [], 180),
    'blocks': ('diagnose_c2_five_offsets.py', [], 180),
    'boundary': ('audit_c2_five_isolation.py', [], 120),
    'probe': ('run_c2_five_calibration.py', ['probe'], 120),
    'prior': ('run_c2_five_calibration.py', ['prior'], 600),
    'representative': ('check_c2_five_representative.py', [], 120),
    # Physical calibration already enforces a 900 s internal cap. Allow it
    # to finish or report that cap instead of killing it at an obsolete 600 s.
    'calibrate': ('run_c2_five_calibration.py', ['calibrate'], 1000),
    'transfer': ('audit_c2_five_physics.py', [], 120),
    'validate': ('run_c2_five_calibration.py', ['validate'], 600),
    'h': ('run_c2_five_holdout.py', ['--diagnostic-only'], 600),
    'shared-probe': ('run_c2_five_shared_calibration.py', ['probe'], 120),
    'shared-fit': ('run_c2_five_shared_calibration.py', ['fit'], 1200),
    'shared-c2-probe': ('run_c2_five_frozen_replay.py', ['--probe'], 90),
    'shared-c2-resource': ('run_c2_five_frozen_replay.py', ['--resource-probe'], 90),
    'shared-c2-replay': ('run_c2_five_frozen_replay.py', [], 700),
    'shared-h': ('run_c2_five_holdout.py', ['--calibration-kind', 'shared', '--diagnostic-only'], 780),
}


def descendant_memory(root_pid):
    """Observe this stage's process tree, including bubblewrap sessions."""
    processes = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path/'stat').read_text()
            fields = stat[stat.rfind(')')+2:].split()
            memory = (path/'status').read_text().split('VmRSS:')
            processes[int(path.name)] = (int(fields[1]), int(memory[1].split()[0])*1024 if len(memory)>1 else 0)
        except (OSError, ValueError, IndexError):
            continue
    descendants = {root_pid}
    while True:
        expanded = descendants | {pid for pid,(parent,_) in processes.items() if parent in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    return descendants, sum(processes.get(pid,(0,0))[1] for pid in descendants)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def mount_plan(out, stage):
    out = out.resolve()
    if out.parent != ROOT / 'logs' or not (out / 'TASK_CONTRACT.json').is_file():
        raise ValueError('isolated run needs its own logs directory and task contract')
    # Do not expose evaluation artifacts through the writable run directory.
    if any('REFERENCE' in p.name.upper() or 'ASSESSMENT' in p.name.upper()
           for p in out.iterdir()):
        raise ValueError('reference/evaluation files must stay outside isolated computation outputs')
    for path in out.rglob('*'):
        if path.is_symlink() and path.resolve() != ROOT / 'third_party/smpl/SMPL_MALE.pkl':
            raise ValueError('unexpected external symlink in isolated output: ' + str(path))
    runtime = [Path(p) for p in ('/usr', '/lib', '/lib64') if Path(p).exists()]
    packages = Path.home() / '.local/lib/python3.12/site-packages'
    source = [ROOT / p for p in ('src', 'tools', '.venv-v0',
                                'third_party/imucoco', 'third_party/smpl')]
    # Ingest imports the authoritative transport decoder even when this stage
    # consumes an already exported cache. Expose source only, never B306 logs.
    source.append(ROOT.parent / 'B306_Part/tools/fusion_host_binary.py')
    data = [SURFACE, INPUT / 'CALIBRATION_CONTINUOUS_INPUT.npz',
            INPUT / 'CALIBRATION_INPUT_AUDIT.json']
    if stage in ('h', 'shared-h'):
        data.append(INPUT / 'HOLDOUT_CONTINUOUS_INPUT.npz')
    if stage == 'shared-h':
        data.append(INPUT / 'HOLDOUT_INPUT_AUDIT.json')
    return runtime + [packages] + source, data, packages


def run(out, stage):
    out = out.resolve()
    readonly, data, packages = mount_plan(out, stage)
    executable = shutil.which('bwrap')
    if executable is None:
        raise RuntimeError('bubblewrap required; do not silently run without isolation')
    report_path = out / ('ISOLATED_' + stage.upper() + '.json')
    if report_path.exists():
        raise ValueError('preserve prior isolated stage evidence')
    script, arguments, limit = STAGES[stage]
    command = [executable, '--unshare-all', '--die-with-parent', '--new-session',
               '--clearenv', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
    for path in readonly + data:
        command += ['--ro-bind', str(path), str(path)]
    command += ['--bind', str(out), str(out), '--chdir', str(ROOT),
                '--setenv', 'HOME', '/tmp', '--setenv', 'PATH', '/usr/bin',
                '--setenv', 'USER', 'five-node-runtime',
                '--setenv', 'PYTHONPATH', str(ROOT / 'src') + ':' + str(packages),
                '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
                '--setenv', 'OPENBLAS_NUM_THREADS', '1', '--setenv', 'OMP_NUM_THREADS', '2',
                str(ROOT / '.venv-v0/bin/python'), str(ROOT / 'tools' / script),
                *arguments, '--out', str(out)]
    record = dict(stage=stage, status='running', command=command, wall_limit_s=limit,
                  started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  launcher_sha256=digest(Path(__file__)),
                  mounted_data_sha256={str(p): digest(p) for p in data},
                  network_namespace='unshared', source_and_assets_readonly=True,
                  other_run_directories_mounted=False, raw_ten_node_recording_mounted=False,
                  comparison_report_required=False,
                  qualification='filesystem-isolated execution, not pose-accuracy acceptance')
    rss_limit = (1_000_000_000 if stage.startswith('navigation') else
                 4*1024**3 if stage.startswith(('shared-','arm-')) else None)
    if rss_limit is not None:
        record.update(rss_limit_bytes=rss_limit, peak_rss_bytes=0)
    report_path.write_text(json.dumps(record, indent=2) + '\n')
    started = time.monotonic()
    with (out / ('ISOLATED_' + stage.upper() + '.log')).open('x') as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        if rss_limit is not None:
            while child.poll() is None:
                descendants, rss = descendant_memory(child.pid)
                record['peak_rss_bytes'] = max(record['peak_rss_bytes'], rss)
                if time.monotonic()-started > limit or rss > record['rss_limit_bytes']:
                    record['termination'] = 'wall_limit' if time.monotonic()-started > limit else 'rss_limit'
                    for pid in descendants:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    child.wait()
                    break
                time.sleep(.2)
            code = child.returncode
        else:
            try:
                code = child.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                code = 124
    record.update(status='completed' if code == 0 else 'failed', exit_code=code,
                  ended_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  wall_s=time.monotonic() - started,
                  inputs_unchanged=all(digest(Path(p)) == expected
                      for p, expected in record['mounted_data_sha256'].items()))
    report_path.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({k: record[k] for k in ('stage', 'status', 'wall_s', 'inputs_unchanged')}), flush=True)
    if code or not record['inputs_unchanged']:
        raise SystemExit(code or 1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=STAGES)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    run(args.out, args.stage)
