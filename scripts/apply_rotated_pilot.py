#!/usr/bin/env python3
"""Add audited rotated labels to an already composed native pilot page."""
import argparse
import json
from pathlib import Path
from astro_translate.rotated_pilot import apply_plan
from astro_translate.core import Blocked


def main():
    p=argparse.ArgumentParser();p.add_argument('--book',type=Path,required=True)
    p.add_argument('--page',type=int,choices=[63,69],required=True)
    p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--candidate-page',type=int)
    a=p.parse_args()
    try:
        r=apply_plan(a.book,a.page,a.candidate,candidate_page=a.candidate_page)
        print(json.dumps({'page':r['page'],'output':r['output'],'scale':r['group_scale'],'non_text_pixel_diff':r['non_text_pixel_diff']},indent=2))
        return 0
    except Blocked as exc:print(str(exc));return 2

if __name__=='__main__':raise SystemExit(main())
