#!/usr/bin/env python3
"""Compact legacy full-book production candidates after reviewing the mapping."""
import argparse,json
from astro_translate.candidate_storage import migrate_candidates
from astro_translate.production import writer_lock

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--book',required=True);parser.add_argument('--skip-mismatch',action='store_true',help='leave candidates whose render differs untouched instead of aborting')
    args=parser.parse_args()
    with writer_lock(args.book):result=migrate_candidates(args.book,skip_mismatch=args.skip_mismatch)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
