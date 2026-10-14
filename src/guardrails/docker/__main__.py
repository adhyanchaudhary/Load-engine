"""Operator cleanup command. No load tests, image builds or pulls."""
import argparse
import sys

from guardrails.docker.manager import SandboxManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up expired owned sandboxes")
    parser.add_argument("action", choices=["cleanup"])
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    result = SandboxManager.from_environment().cleanup_expired(args.limit)
    print(result.model_dump_json(indent=2))
    sys.exit(1 if result.errors or result.remaining_expired else 0)


if __name__ == "__main__":
    main()
