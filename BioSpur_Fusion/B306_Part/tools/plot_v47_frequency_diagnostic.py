#!/usr/bin/env python3
import argparse, csv, json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

NODES=('BSF3C79','BSFC2CC','BSF44AD','BSF6C53','BSF8BC4','BSF1120','BSF31CC','BSFAA61','BSFB165','BSFEC35')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('run',type=Path);a=ap.parse_args()
    rows=json.loads((a.run/'SMOKE_MINUTE_STATUS.json').read_text())['minutes']
    with (a.run/'FREQUENCY_PER_MINUTE.csv').open('w',newline='') as fh:
        w=csv.writer(fh);w.writerow(('minute','node','fusion_uwb_hz','fusion_imu_hz','listener_source_hz'))
        for row in rows:
            for n in NODES:w.writerow((row['minute'],n,row['fusion'][n]['uwb_hz'],row['fusion'][n]['imu_hz'],row['listener'][n]['source_hz']))
    fig,axes=plt.subplots(2,1,figsize=(13,10),sharex=True,constrained_layout=True)
    minutes=[r['minute'] for r in rows]
    for n in NODES:
        style={'linewidth':3,'color':'#d62728','zorder':5} if n=='BSF6C53' else {'linewidth':1.4,'alpha':.85}
        axes[0].plot(minutes,[r['fusion'][n]['uwb_hz'] for r in rows],marker='o',label=n,**style)
        axes[1].plot(minutes,[r['listener'][n]['source_hz'] for r in rows],marker='o',label=n,**style)
    for ax in axes:
        ax.axhspan(8.0,8.6,color='#2ca02c',alpha=.10);ax.grid(True,alpha=.25);ax.set_ylabel('UWB frequency (Hz)')
    axes[0].set_title('Fusion-delivered UWB frequency per one-minute window')
    axes[1].set_title('Five-Listener deduplicated Tag source frequency')
    axes[1].set_xlabel('Minute after T0');axes[1].set_xticks(minutes)
    axes[0].legend(ncol=5,fontsize=8,loc='lower left')
    fig.savefig(a.run/'UWB_FREQUENCY_10MIN.png',dpi=180)

if __name__=='__main__':main()
