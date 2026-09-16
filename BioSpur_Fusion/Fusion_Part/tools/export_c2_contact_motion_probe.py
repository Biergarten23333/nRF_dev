"""Reuse the fixed-world synchronous viewer without display state correction."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import numpy as np
from build_c2_full_continuous_ab_viewer import build


def export(folder):
    result=json.loads((folder/'RESULT.json').read_text())
    duration=float(result['duration_s'])
    summary = dict(comparison_labels=['A：当前十节点融合基线','B：连续运动状态＋支撑腿约束'],
        viewer_policy=f'仅{duration:.2f}秒；状态{result["status"]}。短段修复候选，不是完整校准通过。B是因果运动学重建，不是原始滤波器后验；上肢不变，根与腿共同求解。显示层无插值/滤波。',
        scientific_pass=False, full_calibration_pass=False)
    (folder/'VIEW_SUMMARY.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    with np.load(folder/'VIEW.npz') as d:
        t = d['time_s']
        regions=json.loads(Path('logs/c2_support_position_guard_20260913/delivery/REGIONS.json').read_text())
        regions=[r for r in regions if r['start_s']<=t[-1] and r['end_s']>t[0]]
    (folder/'VIEW_REGIONS.json').write_text(json.dumps(regions,indent=2))
    output=folder/'CURRENT_VS_CONTACT_MOTION.html'
    build(folder/'VIEW.npz',folder/'VIEW_REGIONS.json',output,summary_path=folder/'VIEW_SUMMARY.json')
    html=output.read_text().replace('C2 全程连续 · Pure IMU / IMU+UWB',f'C2 滑移修复诊断 · {duration:.2f}s')
    html=html.replace('C2 · 全程连续 A/B',f'C2 · {duration:.2f}秒诊断 A/B')
    html=html.replace('同步的左右三维骨架；左纯 IMU，右 IMU+UWB',
                      '同步的左右三维骨架；左当前融合基线，右连续运动重建候选')
    output.write_text(html)
    scripts=re.findall(r'<script[^>]*>(.*?)</script>',output.read_text(),re.S)
    assert len(scripts)==1
    subprocess.run(['node','--check'],input=scripts[0],text=True,check=True,timeout=15)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.style.use('dark_background')
    with np.load(folder/'PROBE.npz') as d:
        t=d['time_s']-d['time_s'][0];names=d['joint_names'].tolist()
        fig,axes=plt.subplots(3,1,figsize=(12,8),sharex=True)
        for label,color in [('baseline','#ffad63'),('candidate','#3cddd5')]:
            root=d['roots_'+label]; pose=d['pose_'+label]
            axes[0].plot(t[1:],1000*np.linalg.norm(np.diff(root,axis=0),axis=1),c=color,label=label)
            for j,side in enumerate(('left','right')):
                foot=root+pose[:,names.index('ankle_'+side)]
                axes[j+1].plot(t[1:],1000*np.linalg.norm(np.diff(foot,axis=0),axis=1),c=color)
        for ax,title in zip(axes,['Root step','Left ankle step','Right ankle step']):
            ax.set_ylabel(title+' (mm)');ax.grid(alpha=.2)
        axes[0].legend();axes[-1].set_xlabel('Capture time (s), real 200 Hz samples')
        fig.tight_layout();fig.savefig(folder/'MOTION_STEPS.png',dpi=150);plt.close(fig)
    print(output.resolve())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    export(parser.parse_args().folder)
