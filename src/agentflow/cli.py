"""Tiny CLI: inspect and verify the audit log, list pending approvals."""
from __future__ import annotations

import argparse
import sys

from .audit import AuditLog
from .store import Store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentflow")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check the audit chain is intact")
    v.add_argument("log")
    t = sub.add_parser("tail", help="print the last audit events")
    t.add_argument("log")
    t.add_argument("-n", type=int, default=10)
    q = sub.add_parser("pending", help="list approvals awaiting a human")
    q.add_argument("db")
    args = p.parse_args(argv)

    if args.cmd == "verify":
        ok, bad = AuditLog(args.log).verify()
        print("audit chain OK" if ok else f"TAMPERED at record {bad}")
        return 0 if ok else 2
    if args.cmd == "tail":
        for e in AuditLog(args.log).entries()[-args.n:]:
            print(e["ts"], e["event"], {k: v for k, v in e.items() if k not in ("ts", "event", "prev", "hash")})
        return 0
    for run_id, step, reason in Store(args.db).pending():
        print(f"{run_id}  {step:<20} {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
