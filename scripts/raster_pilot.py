#!/usr/bin/env python3
"""Run the audited Route C cover or the separately authorized Mercury appendix."""
import argparse
import json
from pathlib import Path
from astro_translate.raster_pilot import build_cover,build_mercury_annex
from astro_translate.core import Blocked
from astro_translate.llm import QuotaPaused


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--book',type=Path,required=True)
    parser.add_argument('--kind',choices=['cover','mercury-annex'],required=True)
    parser.add_argument('--config',type=Path,default=Path('config/pipeline.toml'))
    args=parser.parse_args()
    try:
        report=(build_cover if args.kind=='cover' else build_mercury_annex)(args.book,args.config)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    except QuotaPaused as exc:
        print(str(exc));return 75
    except Blocked as exc:
        print(str(exc));return 2

if __name__=='__main__':raise SystemExit(main())
