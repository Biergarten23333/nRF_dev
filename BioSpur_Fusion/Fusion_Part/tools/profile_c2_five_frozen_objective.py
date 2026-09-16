"""Bounded five-C2-only timing probe; no optimizer updates or replay output."""
import argparse
import ast
import json
import os
from pathlib import Path
import time

import numpy as np
import torch

from biospur_fusion.c2_five_calibration import anatomy
from biospur_fusion.c2_five_calibration.frozen_replay import continuous_inputs
from biospur_fusion.c2_five_calibration.shared_candidate import resolve_shared_candidate
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from biospur_fusion.c2_sparse_nodes.inputs import sha


def run(out):
    path=out/'OBJECTIVE_RUNTIME_PROFILE.json'
    if path.exists():raise ValueError('preserve prior runtime probe')
    contract=json.loads((out/'SHARED_REPLAY_CONTRACT.json').read_text())
    frozen=resolve_shared_candidate(out,diagnostic_only=True,compatibility=contract['compatibility'])
    data=continuous_inputs(out/'C2_PRIOR.npz')
    if len(data['time_s'])!=26426:raise ValueError('exact declared fullC2 size required')
    old=json.loads((out/'PRODUCER_SOURCE_SNAPSHOT.json').read_text())['src/biospur_fusion/c2_five_calibration/anatomy.py']['text']
    fn=next(n for n in ast.parse(old).body if isinstance(n,ast.FunctionDef) and n.name=='exp_rotation')
    namespace={'torch':torch};exec(compile(ast.Module(body=[fn],type_ignores=[]),'frozen-exp-rotation','exec'),namespace)
    methods={'matrix_exp':namespace['exp_rotation'],'rodrigues':anatomy.exp_rotation}
    original=anatomy.exp_rotation;records=[];constructors={};baseline={};started=time.monotonic()
    try:
        for threads in (1,2):
            torch.set_num_threads(threads);t=time.monotonic();objective=PoseObjective(**data,geometry=frozen['geometry'])
            constructors[str(threads)]=time.monotonic()-t
            levers=torch.tensor(frozen['levers'],dtype=torch.float64)
            perturbation=.01*torch.sin(torch.arange(objective.initial.numel(),dtype=torch.float64)).reshape_as(objective.initial)
            for case in ('initial','nonzero'):
                initial=objective.initial+(perturbation if case=='nonzero' else 0.)
                for repeat in range(3):
                    order=('matrix_exp','rodrigues') if repeat%2==0 else ('rodrigues','matrix_exp')
                    for name in order:
                        if time.monotonic()-started>75:raise TimeoutError('profile timing budget')
                        anatomy.exp_rotation=methods[name]
                        p=initial.detach().clone().requires_grad_();t=time.monotonic()
                        r,terms=objective.evaluate(p,levers);forward=time.monotonic()-t
                        terms['loss'].backward();total=time.monotonic()-t
                        grad=p.grad.detach().numpy().copy();loss=float(terms['loss'].detach())
                        if not np.isfinite(grad).all() or not np.isfinite(loss):raise ValueError('invalid timing gradient')
                        hard=float((r[:,[0,18,19,4,5]]-objective.observed).abs().max())
                        if case not in baseline:baseline[case]=(loss,grad)
                        error=float(np.max(abs(grad-baseline[case][1])))
                        if hard!=0. or abs(loss-baseline[case][0])>1e-10 or error>1e-9:
                            raise ValueError('runtime timing variants changed objective/gradient')
                        records.append(dict(threads=threads,case=case,repeat=repeat,warmup=repeat==0,method=name,
                            forward_s=forward,forward_backward_s=total,loss=loss,
                            gradient_max_abs_error=error,observed_max_abs_error=hard))
                        del r,terms,p,grad
                        print(json.dumps(records[-1]),flush=True)
    finally:
        anatomy.exp_rotation=original
    after=resolve_shared_candidate(out,diagnostic_only=True,compatibility=contract['compatibility'])
    if after['bindings']!=frozen['bindings'] or after['inference_sources']!=frozen['inference_sources']:
        raise ValueError('frozen source/input changed')
    result=dict(status='FROZEN_OBJECTIVE_TIMING_ONLY',optimizer_updates=0,frames=len(data['time_s']),
        source_sha256=sha(Path(__file__)),inference_sources=frozen['inference_sources'],frozen_inputs=frozen['bindings'],
        constructors_s=constructors,measurements=records,wall_s=time.monotonic()-started,
        torch_version=torch.__version__,cpu_count=os.cpu_count(),load_average=os.getloadavg(),
        interop_threads=torch.get_num_interop_threads(),H_data_opened=False,model_inference_executed=False,
        timing_excludes_import_disk_hash_and_comparison=True)
    path.write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();run(a.out.resolve())
