"""Display-only heading normalization and fourth overlay of frozen replay data."""
import argparse,json,hashlib
from pathlib import Path
from biospur_fusion.c2_sparse_nodes.viewer import write_viewer

def main(source,output):
    if source.resolve()==output.resolve():raise ValueError('preserve original frozen viewer')
    text=source.read_text();payload,_=json.JSONDecoder().raw_decode(text.split('const D=',1)[1])
    if len(payload.get('panels',[]))!=3:raise ValueError('three source panels required')
    payload['heading_overlay']=True
    payload['status_text']='统一展示航向：按各自骨盆左右轴绕竖直方向旋转整个人物，骨盆原点重合；只用于比较，不修正算法。动作重建仍未通过完整验收。'
    payload['model_text']+=' 统一航向仅消除整身水平转角；不匹配肩线，不修改肢体姿态、骨长、时间或原始结果。取消勾选可查看未对齐航向。'
    write_viewer(output,payload)
    output.with_suffix('.manifest.json').write_text(json.dumps(dict(source=str(source.resolve()),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),display_only=True,algorithm_outputs_modified=False,heading_reference='current five-node pelvis lateral azimuth',overlay_sources=['five','ten']),indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();main(a.source,a.output)
