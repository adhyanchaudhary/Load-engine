"""Explicit local index initialization/import. No Docker or application execution."""
import argparse
from pathlib import Path
import os
from guardrails.persistence.migrations import import_artifacts


def main():
    p=argparse.ArgumentParser(description='Idempotently import retained local JSON into performance.db')
    p.add_argument('action',choices=['import']);p.add_argument('--limit',type=int,default=5000);a=p.parse_args()
    root=Path(os.environ.get('GUARDRAILS_STATE_DIR',str(Path.home()/'.ai-performance-guardrails')))
    print(import_artifacts(root,a.limit))


if __name__=='__main__':main()
