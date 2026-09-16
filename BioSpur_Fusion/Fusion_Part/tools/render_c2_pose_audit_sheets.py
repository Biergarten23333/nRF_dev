"""Build legible contact sheets from the actual three-view pose QA plots."""
import argparse
from pathlib import Path
from PIL import Image


def main():
    ap=argparse.ArgumentParser();ap.add_argument('directory',type=Path);args=ap.parse_args()
    files=sorted(args.directory.glob('*_[257][05].png'))
    # One action per row, three sampled times per row, every view retained.
    for page,start in enumerate(range(0,len(files),12)):
        group=files[start:start+12]
        sheet=Image.new('RGB',(3510,520*((len(group)+2)//3)), 'white')
        for j,path in enumerate(group):
            with Image.open(path) as im:sheet.paste(im.convert('RGB'),((j%3)*1170,(j//3)*520))
        sheet.save(args.directory/f'AUDIT_SHEET_{page+1}.png')


if __name__=='__main__':main()
