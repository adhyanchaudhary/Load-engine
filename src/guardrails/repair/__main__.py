"""Operator-only baseline provisioning and safe owned cleanup recovery."""
import argparse
from guardrails.tools.repair import get_repair_manager


def main():
    parser=argparse.ArgumentParser(description='Approved offline repair baseline/recovery operations')
    parser.add_argument('action',choices=['benchmark-baseline','recover'])
    parser.add_argument('project_id',nargs='?')
    args=parser.parse_args();manager=get_repair_manager()
    if args.action=='recover':print({'remaining_managed_resources':manager.recover()})
    else:
        if not args.project_id:parser.error('project_id is required')
        print(manager.benchmark_baseline(args.project_id).model_dump_json(indent=2))


if __name__=='__main__':main()
