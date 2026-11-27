"""Command-line entry point for NEXUS coordination.

Agents are told to use these commands rather than edit coordination files
directly, because the commands are what enforce the lease. A command that
cannot get the lock exits non-zero with a reason, which is how a NEXUS run
learns to stop before it writes anything.

Exit codes are meaningful and stable, so shell wrappers and the prompt can
branch on them without parsing text:

- ``0`` — the operation succeeded.
- ``1`` — usage or environment error.
- ``2`` — the lease is held by another live agent; do not write.
- ``3`` — no task available (every candidate is already claimed).
"""

from __future__ import annotations

import argparse
import json
import sys

from .claims import ClaimRegistry
from .gitref import GitError, SubprocessGit
from .lease import LeaseHeldError, LeaseManager, default_holder

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_LEASE_HELD = 2
EXIT_NO_TASK = 3


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _cmd_lease(args: argparse.Namespace, manager: LeaseManager) -> int:
    if args.action == "status":
        live = manager.current()
        if live is None:
            _emit({"held": False})
            return EXIT_OK
        verdict = manager.holder_liveness(live.holder)
        _emit(
            {
                "held": True,
                "holder": live.holder,
                "expires_at": live.expires_at,
                "remaining_seconds": live.remaining(),
                "mine": live.holder == manager.holder,
                "holder_liveness": verdict.value,
            }
        )
        return EXIT_OK

    if args.action == "acquire":
        lease = manager.acquire()
        _emit(
            {
                "acquired": True,
                "holder": lease.holder,
                "ref": lease.ref,
                "sha": lease.sha,
                "expires_at": lease.expires_at,
                "remaining_seconds": lease.remaining(),
            }
        )
        return EXIT_OK

    # renew and release both operate on the caller's own lease, identified by
    # the live ref rather than by local state. The holder string must match, so
    # the caller must run from the same identity it acquired with. The default
    # identity is the terminal session id (see `default_holder`), which is
    # stable across the separate processes that make up one run; a pid would
    # change on every invocation and could never renew or release (R39).
    live = manager.current()
    if live is None:
        _emit({"error": "no lease held"})
        return EXIT_ERROR
    if live.holder != manager.holder:
        _emit({"error": "lease held by another agent", "holder": live.holder})
        return EXIT_LEASE_HELD
    if args.action == "renew":
        renewed = manager.renew(live)
        _emit(
            {
                "renewed": True,
                "ref": renewed.ref,
                "expires_at": renewed.expires_at,
                "remaining_seconds": renewed.remaining(),
            }
        )
        return EXIT_OK
    manager.release(live)
    _emit({"released": True})
    return EXIT_OK


def _cmd_claim(args: argparse.Namespace, registry: ClaimRegistry) -> int:
    if args.action == "status":
        if args.task_id:
            claim = registry.get(args.task_id)
            if claim is None:
                _emit({"claimed": False, "task_id": args.task_id})
            else:
                _emit(
                    {
                        "claimed": True,
                        "task_id": claim.task_id,
                        "holder": claim.holder,
                        "run_id": claim.run_id,
                        "age_seconds": claim.age(),
                        "stale": claim.is_stale(),
                        "mine": claim.holder == registry.holder,
                    }
                )
            return EXIT_OK
        _emit(
            {
                "claims": [
                    {
                        "task_id": c.task_id,
                        "holder": c.holder,
                        "run_id": c.run_id,
                        "age_seconds": c.age(),
                        "stale": c.is_stale(),
                    }
                    for c in registry.all_claims()
                ]
            }
        )
        return EXIT_OK

    if args.action == "release":
        if not args.task_id:
            _emit({"error": "--task-id is required to release"})
            return EXIT_ERROR
        claim = registry.get(args.task_id)
        if claim is None:
            _emit({"released": False, "reason": "not claimed"})
            return EXIT_OK
        if claim.holder != registry.holder:
            _emit({"error": "claim held by another agent", "holder": claim.holder})
            return EXIT_ERROR
        registry.release(claim)
        _emit({"released": True, "task_id": claim.task_id})
        return EXIT_OK

    # claim
    if args.task_ids:
        claim = registry.claim_first(args.task_ids)
    elif args.task_id:
        claim = registry.claim(args.task_id)
    else:
        _emit({"error": "provide --task-id or --from-file"})
        return EXIT_ERROR

    if claim is None:
        _emit({"claimed": False, "reason": "all candidates already claimed"})
        return EXIT_NO_TASK
    _emit(
        {
            "claimed": True,
            "task_id": claim.task_id,
            "holder": claim.holder,
            "run_id": claim.run_id,
            "ref": claim.ref,
            "sha": claim.sha,
        }
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexus_coord", description=__doc__)
    parser.add_argument("--holder", default=None, help="agent identity (default: host-s<session>)")
    parser.add_argument("--run-id", default="", help="run id recorded in claims")
    sub = parser.add_subparsers(dest="command", required=True)

    lease = sub.add_parser("lease", help="single-writer lease")
    lease.add_argument("action", choices=["acquire", "renew", "release", "status"])

    claim = sub.add_parser("claim", help="per-task claims")
    claim.add_argument("action", choices=["claim", "release", "status"])
    claim.add_argument("--task-id", default=None)
    claim.add_argument(
        "--from-file",
        default=None,
        help="file with one task id per line, highest priority first",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    holder = args.holder or default_holder()
    git = SubprocessGit()
    task_ids: list[str] = []
    if getattr(args, "from_file", None):
        try:
            with open(args.from_file, encoding="utf-8") as handle:
                task_ids = [line.strip() for line in handle if line.strip()]
        except OSError as exc:
            _emit({"error": f"cannot read --from-file: {exc}"})
            return EXIT_ERROR
    args.task_ids = task_ids

    try:
        if args.command == "lease":
            return _cmd_lease(args, LeaseManager(git, holder=holder))
        return _cmd_claim(args, ClaimRegistry(git, holder=holder, run_id=args.run_id))
    except LeaseHeldError as exc:
        _emit({"error": str(exc), "holder": exc.holder, "remaining_seconds": exc.remaining})
        return EXIT_LEASE_HELD
    except GitError as exc:
        _emit({"error": f"git: {exc}"})
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
