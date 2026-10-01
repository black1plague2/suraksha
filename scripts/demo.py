"""Narrated end-to-end demo for judges.

Usage: python scripts/demo.py [--seed 42] [--scenario dup_exact_shell] [--approve "Priya Nair"]

Generates synth data, processes requests silently until the first request of the chosen scenario,
then prints step by step with short headers.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Suppress JSON logs during demo
os.environ.setdefault("SURAKSHA_LOG_LEVEL", "WARNING")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from suraksha.agents.report import render_markdown  # noqa: E402
from suraksha.models import Decision  # noqa: E402
from suraksha.pipeline import Suraksha  # noqa: E402
from suraksha.store.memory import MemoryStore  # noqa: E402
from suraksha.synth import generate, load_into  # noqa: E402


def fmt_hash_prefix(h: str, n: int = 8) -> str:
    """Show first n chars of hash."""
    return h[:n] + "..." if len(h) > n else h


def main(args: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Narrated demo for judges")
    ap.add_argument("--seed", type=int, default=42, help="Random seed")
    ap.add_argument("--scenario", default="dup_exact_shell", help="Scenario to demo")
    ap.add_argument("--approve", default="", help="Officer name to approve (e.g. 'Priya Nair')")
    a = ap.parse_args(args)

    # Generate synth data
    print(f"Generating synthetic dataset (seed={a.seed})...")
    ds = generate(seed=a.seed)
    store = MemoryStore()
    load_into(store, ds)
    app = Suraksha(store)
    t0 = time.perf_counter()

    # Find first request of the chosen scenario
    target_rid = None
    for rid, scn in ds.scenarios.items():
        if scn == a.scenario:
            target_rid = rid
            break

    if not target_rid:
        print(f"ERROR: scenario {a.scenario!r} not found in dataset", file=sys.stderr)
        return 1

    print(f"\nFound scenario {a.scenario!r} at request {target_rid}")
    print(f"Processing {ds.requests.index(next(r for r in ds.requests if r.request_id == target_rid)) + 1} "
          f"requests silently...")

    # Process all requests up to target; demo only the target
    demo_req = None
    for req in ds.requests:
        res = app.process(req)
        if req.request_id == target_rid:
            demo_req = (req, res)
            break

    if not demo_req:
        print(f"ERROR: could not find request {target_rid}", file=sys.stderr)
        return 1

    req, res = demo_req
    print(f"\n{'=' * 70}")
    print(f"DEMO: {a.scenario} (request {req.request_id})")
    print(f"{'=' * 70}\n")

    # 1. Intake
    print("1. INTAKE")
    print(f"   Bank: {req.bank_id}")
    print(f"   Borrower: {req.borrower_id}")
    print(f"   Amount: {req.currency} {req.amount:,.2f}")
    if res.fields:
        print(f"   Extracted B/L: {res.fields.bl_number or 'n/a'}")
        print(f"   Vessel: {res.fields.vessel or 'n/a'}, Voyage: {res.fields.voyage or 'n/a'}")
        print(f"   Commodity: {res.fields.commodity or 'n/a'}")
        print(f"   Quantity: {res.fields.quantity or 'n/a'} {res.fields.quantity_unit or ''}")
    print()

    # 2. Fingerprint & Consortium match
    if res.matches:
        match = res.matches[0]
        print("2. FINGERPRINT & CONSORTIUM MATCH")
        print(f"   Match type: {match.match_type.value}")
        print(f"   Similarity: {match.similarity:.2f}")
        print(f"   Matched keys: {', '.join(match.matched_keys)}")
        print(f"   Hash prefixes:")
        for key, h in match.entry.keys.items():
            print(f"     {key}: {fmt_hash_prefix(h)}")
        print(f"   Matched against: {match.entry.entry_id} (pledged by {match.entry.bank_id})")
        print()
    else:
        print("2. FINGERPRINT & CONSORTIUM MATCH")
        print("   No matches found - case cleared")
        print()
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"\nTotal elapsed: {elapsed:.1f}ms")
        print("Audit chain: VALID" if app.audit.verify() else "Audit chain: INVALID")
        return 0

    # 3. Investigation
    if res.investigation:
        inv = res.investigation
        print("3. INVESTIGATION")
        if inv.counterparty_company_id:
            print(f"   Counterparty: {inv.counterparty_company_id}")
        if inv.paths:
            print(f"   Ownership paths: {len(inv.paths)}")
            for i, path in enumerate(inv.paths[:2], 1):
                edge_str = " -> ".join(
                    f"{e.src.split(':')[1]} ({e.relation}) {e.dst.split(':')[1]}"
                    for e in path.edges
                )
                print(f"     Path {i}: {edge_str}")
        if inv.shared_ubos:
            print(f"   Shared UBOs: {', '.join(inv.shared_ubos[:3])}")
        if inv.shared_directors:
            print(f"   Shared directors: {', '.join(inv.shared_directors[:3])}")
        if inv.shared_addresses:
            print(f"   Shared addresses: {len(inv.shared_addresses)}")
        print()

    # 4. Confidence
    if res.confidence:
        conf = res.confidence
        print("4. CONFIDENCE SCORING")
        print(f"   Score: {conf.score:.2f} / 1.00")
        print(f"   Band: {conf.band.value}")
        print(f"   Evidence rules:")
        for ev in conf.evidence:
            print(f"     {ev.rule_id} (weight {ev.weight:.2f}): {ev.description}")
        print()

    # 5. STR
    if res.report:
        draft = res.report
        print("5. SUSPICIOUS TRANSACTION REPORT")
        print(f"   Report ID: {draft.report_id}")
        print(f"   Status: {draft.recommended_action}")
        print(f"   Sections: {len(draft.sections)}")
        for sec in draft.sections[:2]:
            print(f"     - {sec.part}: {sec.title} ({len(sec.sentences)} sentences)")
        print()

    # 6. Approval
    if res.case and a.approve:
        print("6. APPROVAL")
        print(f"   Case ID: {res.case.case_id}")
        print(f"   Officer: {a.approve}")
        case = app.decide(res.case.case_id, Decision.APPROVE, f"officer:{a.approve}",
                          "Confirmed duplicate pledge")
        print(f"   Decision: APPROVE")
        print(f"   New status: {case.status.value}")
        print()

    # 7. Audit trail
    print("7. AUDIT TRAIL (last 5 entries)")
    records = list(app.store.list_audit())
    for rec in records[-5:]:
        print(f"   [{rec.seq}] {rec.at.isoformat()} {rec.actor}: {rec.action}")
    print()

    elapsed = (time.perf_counter() - t0) * 1000
    print(f"Total elapsed: {elapsed:.1f}ms")
    print("Audit chain: VALID" if app.audit.verify() else "Audit chain: INVALID")
    return 0


if __name__ == "__main__":
    sys.exit(main())
