"""Bulk-load the synthetic registry / policy / transactions into Snowflake.

Usage:
    python scripts/load_synth_to_snowflake.py [--seed 42] [--truncate] [--connection NAME]

Requires sql/00..01 to have been run, and SNOWFLAKE_* env vars (or --connection).
Requests are NOT loaded here; the pipeline writes them as it processes them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from suraksha.log import get_logger  # noqa: E402

log = get_logger("load_synth")

TRUNCATE_TABLES = [
    "SURAKSHA.REGISTRY.ADDRESSES", "SURAKSHA.REGISTRY.COMPANIES", "SURAKSHA.REGISTRY.PERSONS",
    "SURAKSHA.REGISTRY.ROLES", "SURAKSHA.REGISTRY.CORP_OWNERS", "SURAKSHA.CORE.POLICY_CLAUSES",
    "SURAKSHA.BANK_A.TRANSACTIONS", "SURAKSHA.BANK_B.TRANSACTIONS", "SURAKSHA.BANK_C.TRANSACTIONS",
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--truncate", action="store_true", help="empty target tables first (idempotent reload)")
    ap.add_argument("--connection", default=None, help="named connection in ~/.snowflake/connections.toml")
    args = ap.parse_args(argv)

    from suraksha.store.snowflake import SnowflakeStore  # lazy: needs snowflake-connector
    from suraksha.synth.generator import generate  # lazy: written by another agent

    ds = generate(seed=args.seed)
    store = SnowflakeStore(connection_name=args.connection)
    try:
        if args.truncate:
            for t in TRUNCATE_TABLES:  # constants only
                store._exec(f"TRUNCATE TABLE IF EXISTS {t}")
            log.info("truncated", extra={"ctx": {"tables": len(TRUNCATE_TABLES)}})
        store.load_registry(
            ds.companies, ds.persons, ds.roles, ds.corp_owners, ds.addresses, ds.transactions, ds.policy_clauses
        )
        print(
            f"loaded: {len(ds.companies)} companies, {len(ds.persons)} persons, {len(ds.roles)} roles, "
            f"{len(ds.corp_owners)} corp_owners, {len(ds.addresses)} addresses, "
            f"{len(ds.transactions)} transactions, {len(ds.policy_clauses)} policy clauses"
        )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
