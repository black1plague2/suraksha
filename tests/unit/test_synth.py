import re
from collections import Counter, deque

import pytest

from suraksha.store.memory import MemoryStore
from suraksha.synth import SynthDataset, generate, load_into


@pytest.fixture(scope="module")
def ds() -> SynthDataset:
    return generate(42)


def bl_of(req) -> str:
    return re.search(r"^(?:B/L No|B/L Number|Bill of Lading No\.): (\S+)$", req.documents[0].text, re.M).group(1)


def qty_mt(req) -> float:
    m = re.search(r"^Quantity: ([\d,\.]+) (MT|TONNES|KG)$", req.documents[0].text, re.M)
    q = float(m.group(1).replace(",", ""))
    return q / 1000 if m.group(2) == "KG" else q


def graph(ds):
    adj: dict[str, set[str]] = {}

    def edge(a, b):
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    for c in ds.companies:
        edge(f"company:{c['company_id']}", f"address:{c['address_id']}")
        edge(f"company:{c['company_id']}", f"phone:{c['phone']}")
    for r in ds.roles:
        edge(f"company:{r['company_id']}", f"person:{r['person_id']}")
    for o in ds.corp_owners:
        edge(f"company:{o['owner_company_id']}", f"company:{o['owned_company_id']}")
    return adj


def hops(adj, a: str, b: str, limit: int) -> int | None:
    start, goal = f"company:{a}", f"company:{b}"
    seen, q = {start: 0}, deque([start])
    while q:
        n = q.popleft()
        if n == goal:
            return seen[n]
        if seen[n] == limit:
            continue
        for m in adj.get(n, ()):
            if m not in seen:
                seen[m] = seen[n] + 1
                q.append(m)
    return None


def case_pairs(ds, dup_scn):
    """(original request, duplicate request) pairs for a dup scenario."""
    reqs = {r.request_id: r for r in ds.requests}
    orig_scn = "dup_original_" + dup_scn[4:]
    originals = [r for r in ds.requests if ds.scenarios[r.request_id] == orig_scn]
    out = []
    for d in (r for r in ds.requests if ds.scenarios[r.request_id] == dup_scn):
        key = (re.search(r"^Ex Vessel: (.+)$", d.documents[3].text, re.M).group(1).split(" / ")[1])
        vessel = re.search(r"^Ex Vessel: (.+?) / ", d.documents[3].text, re.M).group(1).strip().lower().replace("  ", " ")
        match = [o for o in originals
                 if re.search(r"^Ex Vessel: (.+)$", o.documents[3].text, re.M).group(1).split(" / ")[1] == key
                 and re.search(r"^Ex Vessel: (.+?) / ", o.documents[3].text, re.M).group(1).lower() == vessel]
        assert len(match) == 1
        out.append((match[0], d))
    assert reqs
    return out


def test_deterministic():
    a, b = generate(7), generate(7)
    assert [r.documents[0].text for r in a.requests] == [r.documents[0].text for r in b.requests]
    assert a.companies == b.companies and a.roles == b.roles and a.transactions == b.transactions
    assert a.labels == b.labels and a.scenarios == b.scenarios
    assert [r.submitted_at for r in a.requests] == [r.submitted_at for r in b.requests]
    c = generate(8)
    assert [r.documents[0].text for r in a.requests] != [r.documents[0].text for r in c.requests]


def test_counts_and_structure(ds):
    sc = Counter(ds.scenarios.values())
    assert sc["clean"] == 60
    dup_originals = sum(v for k, v in sc.items() if k.startswith("dup_original_"))
    assert dup_originals == 30
    for s in ["dup_exact_same_borrower", "dup_exact_shell", "dup_reissued_bl", "dup_rounded_qty",
              "dup_format_noise", "dup_weak_unlinked"]:
        assert sc["dup_original_" + s[4:]] == 5
        assert 5 <= sc[s] <= 10
    decoys = sum(v for k, v in sc.items() if k.startswith("decoy_") and k != "decoy_anchor")
    assert decoys == 20
    assert 0 < sc["decoy_hard_same_voyage_same_qty"] <= 2
    assert len(ds.companies) >= 150
    assert len(ds.policy_clauses) >= 11
    assert set(ds.labels) == set(ds.scenarios) == {r.request_id for r in ds.requests}
    for rid, scn in ds.scenarios.items():
        assert ds.labels[rid] is (scn.startswith("dup_") and not scn.startswith("dup_original_"))
    assert len({c["reg_no"] for c in ds.companies}) == len(ds.companies)
    assert ds.borrower_reg_no[ds.companies[0]["company_id"]] == ds.companies[0]["reg_no"]


def test_requests_sorted_ids_and_docs(ds):
    times = [r.submitted_at for r in ds.requests]
    assert times == sorted(times)
    assert [r.request_id for r in ds.requests] == [f"REQ-{i:05d}" for i in range(1, len(ds.requests) + 1)]
    labels = {
        "BL": ["BILL OF LADING", "Shipper:", "Consignee:", "Port of Loading:", "Port of Discharge:",
               "Description of Goods:", "Quantity:", "Shipped on Board:"],
        "INVOICE": ["COMMERCIAL INVOICE", "Invoice No:", "Seller:", "Buyer:", "Goods:", "Quantity:", "Total Value:", "B/L Ref:"],
        "LC": ["LETTER OF CREDIT", "LC No:", "Applicant:", "Beneficiary:", "Amount:", "Goods:", "Latest Shipment Date:"],
        "WR": ["WAREHOUSE RECEIPT", "Receipt No:", "Depositor:", "Commodity:", "Quantity:", "Ex Vessel:"],
    }
    company_names = {c["company_id"]: c["name"] for c in ds.companies}
    for r in ds.requests:
        assert [d.doc_type.value for d in r.documents] == ["BL", "INVOICE", "LC", "WR"]
        for d in r.documents:
            assert d.doc_id == f"{r.request_id}-{d.doc_type.value}"
            for lab in labels[d.doc_type.value]:
                assert lab in d.text, (d.doc_id, lab)
        bl_text = r.documents[0].text
        assert re.search(r"^(B/L No|B/L Number|Bill of Lading No\.): ", bl_text, re.M)
        assert re.search(r"^(Vessel|Ocean Vessel): ", bl_text, re.M)
        assert re.search(r"^(Voyage No|Voy\. No|Voyage): ", bl_text, re.M)
        assert f"B/L Ref: {bl_of(r)}" in r.documents[1].text
        assert f"Depositor: {company_names[r.borrower_id]}" in r.documents[3].text


def test_dup_borrowers_linked(ds):
    adj = graph(ds)
    for scn in ["dup_exact_shell", "dup_reissued_bl", "dup_rounded_qty", "dup_format_noise"]:
        pairs = case_pairs(ds, scn)
        assert pairs
        for o, d in pairs:
            assert o.borrower_id != d.borrower_id
            h = hops(adj, o.borrower_id, d.borrower_id, 3)
            assert h is not None and 1 <= h <= 3, (scn, o.request_id, d.request_id)
            assert d.bank_id != o.bank_id
            assert 0 < (d.submitted_at - o.submitted_at).days <= 41
    for o, d in case_pairs(ds, "dup_exact_same_borrower"):
        assert o.borrower_id == d.borrower_id and o.bank_id != d.bank_id


def test_weak_unlinked_not_linked(ds):
    adj = graph(ds)
    pairs = case_pairs(ds, "dup_weak_unlinked")
    assert pairs
    for o, d in pairs:
        assert o.borrower_id != d.borrower_id
        assert hops(adj, o.borrower_id, d.borrower_id, 4) is None
        assert bl_of(o) != bl_of(d)


def test_scenario_semantics(ds):
    for o, d in case_pairs(ds, "dup_exact_shell"):
        assert bl_of(o) == bl_of(d) and qty_mt(o) == qty_mt(d)
    for o, d in case_pairs(ds, "dup_reissued_bl"):
        assert bl_of(o) != bl_of(d) and qty_mt(o) == qty_mt(d)
    for o, d in case_pairs(ds, "dup_rounded_qty"):
        assert bl_of(o) == bl_of(d)
        qo, qd = qty_mt(o), qty_mt(d)
        assert qo != qd
        assert qo % 50 not in (25.0,)
        assert abs(round(qo / 50) - round(qd / 50)) <= 1
    noisy = case_pairs(ds, "dup_format_noise")
    for o, d in noisy:
        assert bl_of(o) == bl_of(d)
        assert abs(qty_mt(o) - qty_mt(d)) < 1e-6
        assert " KG" in d.documents[0].text
        assert re.search(r"Shipped on Board: \d\d/\d\d/\d{4}", d.documents[0].text)
        assert re.search(r"^(Vessel|Ocean Vessel): (M/V|MV) ", d.documents[0].text, re.M)


def test_decoys(ds):
    by_bl = {}
    for r in ds.requests:
        by_bl.setdefault(bl_of(r), []).append(r)
    refin = [r for r in ds.requests if ds.scenarios[r.request_id] == "decoy_same_bank_refinance"]
    assert refin
    for r in refin:
        anchors = [x for x in by_bl[bl_of(r)] if ds.scenarios[x.request_id] == "decoy_anchor"]
        assert len(anchors) == 1 and anchors[0].bank_id == r.bank_id
        assert anchors[0].borrower_id == r.borrower_id
    hard = [r for r in ds.requests if ds.scenarios[r.request_id] == "decoy_hard_same_voyage_same_qty"]
    assert 1 <= len(hard) <= 2
    assert not any(ds.labels[r.request_id] for r in ds.requests if ds.scenarios[r.request_id].startswith("decoy_"))


def test_registry_and_transactions(ds):
    cids = {c["company_id"] for c in ds.companies}
    pids = {p["person_id"] for p in ds.persons}
    aids = {a["address_id"] for a in ds.addresses}
    assert all(r["company_id"] in cids and r["person_id"] in pids for r in ds.roles)
    assert {r["role"] for r in ds.roles} == {"DIRECTOR", "SHAREHOLDER", "UBO"}
    assert all(c["address_id"] in aids for c in ds.companies)
    assert ds.corp_owners and all(o["owner_company_id"] in cids for o in ds.corp_owners)
    # shell clusters share an address with another company; most companies do not (noise stays rare)
    addr_counts = Counter(c["address_id"] for c in ds.companies)
    assert 0 < sum(1 for n in addr_counts.values() if n > 1) < len(ds.companies) * 0.15
    first_req = min(r.submitted_at for r in ds.requests)
    assert all(t["txn_date"] < first_req.date() for t in ds.transactions)
    per = Counter(t["company_id"] for t in ds.transactions)
    assert max(per.values()) >= 24 and min(per.values()) >= 12
    assert len({t["txn_id"] for t in ds.transactions}) == len(ds.transactions)
    ids = {c["clause_id"] for c in ds.policy_clauses}
    assert {"TF-3.1", "TF-4.2", "AML-7.4"} <= ids
    assert {"duplicate_financing", "collateral", "related_party", "str_filing", "hold"} <= {
        t for c in ds.policy_clauses for t in c["tags"]}


def test_round_trips_between_linked_borrowers(ds):
    names = {c["company_id"]: c["name"] for c in ds.companies}
    ids_by_name = {v: k for k, v in names.items()}
    adj = graph(ds)
    rt = [t for t in ds.transactions
          if t["counterparty"] in ids_by_name and t["txn_type"] == "TT_PAYMENT"
          and hops(adj, t["company_id"], ids_by_name[t["counterparty"]], 3) is not None]
    assert len(rt) >= 10


def test_load_into_memory_store(ds):
    s = MemoryStore()
    load_into(s, ds)
    assert len(s.list_companies()) == len(ds.companies)
    c = ds.companies[0]
    assert s.get_company(c["company_id"])["reg_no"] == c["reg_no"]
    assert s.roles_for_company(ds.roles[0]["company_id"])
    assert s.get_address(c["address_id"])
    assert s.policy_clauses(["str_filing"])
    b = ds.requests[0].borrower_id
    assert s.transactions_for(b)
