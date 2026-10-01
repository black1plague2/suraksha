"""Deterministic synthetic data generator (registry + financing requests + labels).

Only the standard library is used. Everything derives from `random.Random(seed)`
and a fixed base datetime, so the same seed always yields identical output.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any

from suraksha.log import get_logger
from suraksha.models import FinancingRequest
from suraksha.synth import templates as T

log = get_logger(__name__)

BASE_DT = datetime(2026, 6, 1)
QTY_BAND_MT = 50.0  # mirrors config.Settings.qty_band_mt default

DUP_SCENARIOS = [
    "dup_exact_same_borrower",
    "dup_exact_shell",
    "dup_reissued_bl",
    "dup_rounded_qty",
    "dup_format_noise",
    "dup_weak_unlinked",
]
DECOY_SCENARIOS = [
    "decoy_coloaded",
    "decoy_related_diff_cargo",
    "decoy_same_commodity_diff_voyage",
    "decoy_same_bank_refinance",
]
# link recipes between two borrowers (every recipe is <= 3 graph hops)
LINK_KINDS: list[tuple[str, ...]] = [
    ("ubo",), ("director",), ("address", "phone"), ("holding",), ("chain3",),
    ("ubo_holding",), ("director", "address"), ("ubo", "phone"),
]


@dataclass
class SynthDataset:
    companies: list[dict]
    persons: list[dict]
    roles: list[dict]
    corp_owners: list[dict]
    addresses: list[dict]
    transactions: list[dict]
    policy_clauses: list[dict]
    requests: list[FinancingRequest]
    labels: dict[str, bool]
    scenarios: dict[str, str]
    borrower_reg_no: dict[str, str]


def band(qty_mt: float) -> int:
    return round(qty_mt / QTY_BAND_MT)


# --------------------------------------------------------------------------- registry
class _Registry:
    def __init__(self, rng: random.Random, seed: int) -> None:
        self.rng, self.seed = rng, seed
        self.companies: list[dict] = []
        self.persons: list[dict] = []
        self.roles: list[dict] = []
        self.corp: list[dict] = []
        self.addresses: list[dict] = []
        self._names: set[str] = set()
        self._regs: set[str] = set()
        self._phones: set[str] = set()
        self._person_names: set[str] = set()

    # -- primitives
    def person(self, country: str) -> dict:
        r = self.rng
        gcc = country != "IN" and r.random() < 0.75
        for _ in range(50):
            if gcc:
                nm = f"{r.choice(T.GCC_FIRST)} {r.choice(T.GCC_LAST)}"
            else:
                nm = f"{r.choice(T.IN_FIRST)} {r.choice(T.IN_LAST)}"
            if nm not in self._person_names:
                break
        else:
            nm = f"{nm} {len(self.persons)}"
        self._person_names.add(nm)
        pid = f"P{len(self.persons) + 1:04d}"
        p = {"person_id": pid, "name": nm,
             "id_hash": hashlib.sha256(f"{self.seed}:person:{pid}".encode()).hexdigest()}
        self.persons.append(p)
        return p

    def address(self, country: str) -> dict:
        r = self.rng
        city, _ = r.choice(T.CITIES[country])
        line = f"{r.randint(1, 480)}, {r.choice(T.STREETS)}"
        a = {"address_id": f"A{len(self.addresses) + 1:04d}", "line": line, "city": city, "country": country}
        self.addresses.append(a)
        return a

    def _phone(self, country: str) -> str:
        r = self.rng
        while True:
            if country == "IN":
                ph = f"+91{r.choice('6789')}{r.randint(10**8, 10**9 - 1)}"
            elif country == "AE":
                ph = f"+9715{r.choice('0245689')}{r.randint(10**6, 10**7 - 1)}"
            elif country == "SA":
                ph = f"+9665{r.randint(10**7, 10**8 - 1)}"
            else:
                ph = f"+9743{r.randint(10**6, 10**7 - 1)}"
            if ph not in self._phones:
                self._phones.add(ph)
                return ph

    def _reg_no(self, country: str, city_code: str, year: int) -> str:
        r = self.rng
        while True:
            if country == "IN":
                reg = f"U{r.choice(['51909', '24109', '27109', '01111', '46900', '51100'])}{city_code}{year}{r.choice(['PTC', 'PTC', 'PLC'])}{r.randint(100000, 999999)}"
            elif country == "AE":
                reg = f"{r.choice(['DED', 'JAFZA', 'DMCC'])}-{r.randint(100000, 999999)}"
            elif country == "SA":
                reg = f"CR-{r.randint(10**9, 10**10 - 1)}"
            else:
                reg = f"QFC-{r.randint(10000, 99999)}"
            if reg not in self._regs:
                self._regs.add(reg)
                return reg

    def _name(self, country: str) -> str:
        r = self.rng
        suffixes = {"IN": T.IN_SUFFIX, "AE": T.AE_SUFFIX, "SA": T.SA_SUFFIX, "QA": T.QA_SUFFIX}[country]
        stems = T.NAME_STEMS[:30] if country == "IN" else T.NAME_STEMS[20:]
        for _ in range(200):
            nm = f"{r.choice(stems)} {r.choice(T.NAME_SECTORS)} {r.choice(suffixes)}"
            if nm not in self._names:
                self._names.add(nm)
                return nm
        nm = f"{nm} {len(self.companies)}"
        self._names.add(nm)
        return nm

    def role(self, cid: str, pid: str, role: str, pct: float | None) -> None:
        for x in self.roles:
            if x["company_id"] == cid and x["person_id"] == pid and x["role"] == role:
                return
        self.roles.append({"company_id": cid, "person_id": pid, "role": role, "pct": pct})

    def own(self, owner: dict, owned: dict, pct: float | None = None) -> None:
        pct = pct if pct is not None else float(self.rng.randint(51, 100))
        self.corp.append({"owner_company_id": owner["company_id"], "owned_company_id": owned["company_id"], "pct": pct})

    def country(self) -> str:
        return self.rng.choices(["IN", "AE", "SA", "QA"], weights=[62, 22, 10, 6])[0]

    def company(self, country: str | None = None, address_id: str | None = None, phone: str | None = None) -> dict:
        r = self.rng
        country = country or self.country()
        cid = f"C{len(self.companies) + 1:04d}"
        year = r.randint(2008, 2025)
        city, code = r.choice(T.CITIES[country])
        if address_id is None:
            addr = self.address(country)
            address_id = addr["address_id"]
        c = {
            "company_id": cid, "name": self._name(country),
            "reg_no": self._reg_no(country, code, year),
            "address_id": address_id, "phone": phone or self._phone(country),
            "incorporated": date(year, r.randint(1, 12), r.randint(1, 28)), "country": country,
        }
        self.companies.append(c)
        # officers: 1-3 directors, 1-2 shareholders (often the same people), one UBO
        directors = [self.person(country) for _ in range(r.randint(1, 3))]
        for p in directors:
            self.role(cid, p["person_id"], "DIRECTOR", None)
        holders = [directors[0]] if r.random() < 0.7 else [self.person(country)]
        if r.random() < 0.4:
            holders.append(directors[-1] if len(directors) > 1 and directors[-1] is not holders[0] else self.person(country))
        first = float(r.randint(40, 85))
        self.role(cid, holders[0]["person_id"], "SHAREHOLDER", first)
        if len(holders) > 1:
            self.role(cid, holders[1]["person_id"], "SHAREHOLDER", float(r.randint(5, int(100 - first))) if first < 95 else 5.0)
        self.role(cid, holders[0]["person_id"], "UBO", first)
        return c

    # -- links
    def make_linked(self, a: dict, kinds: tuple[str, ...]) -> dict:
        """Create a NEW company genuinely linked to `a` through the recipe `kinds`."""
        r = self.rng
        kw: dict[str, Any] = {}
        if "address" in kinds:
            kw["address_id"] = a["address_id"]
        if "phone" in kinds:
            kw["phone"] = a["phone"]
        same_country = bool(kw) or r.random() < 0.8
        b = self.company(country=a["country"] if same_country else None, **kw)
        if "ubo" in kinds:
            p = self.person(a["country"])
            self.role(a["company_id"], p["person_id"], "UBO", float(r.randint(30, 80)))
            self.role(b["company_id"], p["person_id"], "UBO", float(r.randint(30, 80)))
        if "director" in kinds:
            p = self.person(a["country"])
            self.role(a["company_id"], p["person_id"], "DIRECTOR", None)
            self.role(b["company_id"], p["person_id"], "DIRECTOR", None)
        if "holding" in kinds:
            h = self.company(country=a["country"])
            self.own(h, a)
            self.own(h, b)
        if "chain3" in kinds:
            h1, h2 = self.company(country=a["country"]), self.company(country=a["country"])
            self.own(h1, a)
            self.own(h2, h1)
            self.own(h2, b)
        if "ubo_holding" in kinds:
            p = self.person(a["country"])
            h = self.company(country=a["country"])
            self.role(a["company_id"], p["person_id"], "UBO", float(r.randint(30, 80)))
            self.role(h["company_id"], p["person_id"], "UBO", float(r.randint(51, 100)))
            self.own(h, b)
        return b

    def add_background(self, n: int) -> list[dict]:
        """Unrelated trading companies plus a few rare, legitimate overlaps."""
        r = self.rng
        pool = [self.company() for _ in range(n)]
        # ~5 small legitimate subsidiary groups (holding + 2 subs, same ownership)
        for _ in range(5):
            h = self.company(country="IN")
            for _ in range(2):
                self.own(h, self.company(country="IN"), float(r.randint(60, 100)))
        # a few shared business-centre addresses and nominee/CA directors
        for _ in range(3):
            c1, c2 = r.sample(pool, 2)
            c2["address_id"] = c1["address_id"]
        for _ in range(4):
            c1, c2 = r.sample(pool, 2)
            if c1["country"] == c2["country"]:
                p = self.person(c1["country"])
                self.role(c1["company_id"], p["person_id"], "DIRECTOR", None)
                self.role(c2["company_id"], p["person_id"], "DIRECTOR", None)
        # drop address rows orphaned by the sharing above
        used = {c["address_id"] for c in self.companies}
        self.addresses = [a for a in self.addresses if a["address_id"] in used]
        return pool


# --------------------------------------------------------------------------- cargo + drafts
@dataclass
class _Draft:
    seq: int
    at: datetime
    bank: str
    borrower: dict
    data: T.DocData
    noise: T.Noise
    scenario: str
    label: bool
    advance: float


class _Gen:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.reg = _Registry(self.rng, seed)
        self.drafts: list[_Draft] = []
        self._vv: set[tuple[str, str]] = set()
        self._ids: set[str] = set()

    # -- identifiers
    def _unique(self, make) -> str:
        while True:
            v = make()
            if v not in self._ids:
                self._ids.add(v)
                return v

    def new_bl(self) -> str:
        r = self.rng
        return self._unique(lambda: f"{r.choice(T.CARRIER_PREFIXES)}{r.randint(10**8, 10**9 - 1)}")

    def new_vv(self) -> tuple[str, str]:
        r = self.rng
        while True:
            vv = (r.choice(T.VESSELS), f"{r.randint(1, 99):03d}{r.choice('EWNS')}")
            if vv not in self._vv:
                self._vv.add(vv)
                return vv

    # -- quantities
    def qty(self, lo: int, hi: int, dmax: float = 15.0) -> float:
        r = self.rng
        k = r.randint(lo // 50, hi // 50)
        d = r.randint(-int(dmax * 2), int(dmax * 2)) / 2
        return 50 * k + d

    def cargo(self, shipper: dict, commodity: str | None = None, vv: tuple[str, str] | None = None,
              qty: float | None = None, dmax: float = 15.0) -> T.DocData:
        r = self.rng
        com = next((c for c in T.COMMODITIES if c[0] == commodity), None) or r.choice(T.COMMODITIES)
        vessel, voyage = vv or self.new_vv()
        q = qty if qty is not None else self.qty(com[2][0], com[2][1], dmax)
        pol, pod = r.choice(T.PORT_PAIRS)
        usd = q * r.uniform(*com[1])
        if shipper["country"] == "IN" and r.random() < 0.5:
            cur, value = "INR", int(usd * 83.5)
        else:
            cur, value = "USD", int(usd)
        ship = (BASE_DT + timedelta(days=r.randint(-25, 40))).date()
        n = r.randint(10000, 99999)
        return T.DocData(
            bl=self.new_bl(), shipper=shipper["name"], consignee=r.choice(T.BUYER_NAMES),
            vessel=vessel, voyage=voyage, pol=pol, pod=pod, commodity=com[0], qty_mt=q,
            currency=cur, value=value, ship_date=ship,
            inv=self._unique(lambda: f"INV/2026/{r.randint(10000, 99999)}"),
            lc=self._unique(lambda: f"LC{r.choice('IDP')}{r.randint(26000000, 26999999)}"),
            wr=self._unique(lambda: f"WR-{r.choice(['MUN', 'JNP', 'CHE', 'JEA', 'DAM'])}-{r.randint(100000, 999999)}"),
            depositor=shipper["name"],
        )

    def other_commodity(self, name: str) -> str:
        return self.rng.choice([c[0] for c in T.COMMODITIES if c[0] != name])

    # -- drafts
    def add(self, at: datetime, bank: str, borrower: dict, data: T.DocData, scenario: str, label: bool,
            noise_level: str | None = None) -> _Draft:
        r = self.rng
        level = noise_level or ("mild" if r.random() < 0.2 else "none")
        d = _Draft(len(self.drafts), at, bank, borrower, replace(data, depositor=borrower["name"]),
                   T.make_noise(r, level), scenario, label, r.uniform(0.7, 0.9))
        self.drafts.append(d)
        return d

    def rand_time(self, lo_days: int, hi_days: int) -> datetime:
        r = self.rng
        return BASE_DT + timedelta(days=r.randint(lo_days, hi_days), hours=r.randint(8, 18), minutes=r.randint(0, 59))

    def later(self, t: datetime, lo: int = 1, hi: int = 40) -> datetime:
        r = self.rng
        return t + timedelta(days=r.randint(lo, hi), hours=r.randint(0, 6), minutes=r.randint(1, 59))

    def fresh(self) -> dict:
        return self.reg.company()

    # -- scenario builders
    def clean(self, pool: list[dict]) -> None:
        b = self.rng.choice(pool)
        self.add(self.rand_time(0, 110), self.rng.choice(T.BANKS), b, self.cargo(b), "clean", False)

    def dup_case(self, scn: str) -> None:
        r = self.rng
        banks = r.sample(T.BANKS, 3)
        a = self.fresh()
        data = self.cargo(a, dmax=22.0 if scn == "dup_rounded_qty" else 15.0)
        if scn == "dup_rounded_qty" and data.qty_mt % 50 == 0:
            data = replace(data, qty_mt=data.qty_mt + 1.5)
        t0 = self.rand_time(0, 75)
        self.add(t0, banks[0], a, data, "dup_original_" + scn[4:], False)
        n_dups = 2 if r.random() < 0.35 else 1
        for j in range(n_dups):
            if scn == "dup_exact_same_borrower":
                b = a
            elif scn == "dup_weak_unlinked":
                b = self.fresh()
            else:
                b = self.reg.make_linked(a, r.choice(LINK_KINDS))
            noise_level = None
            if scn == "dup_exact_same_borrower" or scn == "dup_exact_shell":
                d2 = data
            elif scn == "dup_reissued_bl" or scn == "dup_weak_unlinked":
                d2 = replace(data, bl=self.new_bl(), inv=self._unique(lambda: f"INV/2026/{r.randint(10000, 99999)}"))
            elif scn == "dup_rounded_qty":
                q = data.qty_mt
                base50 = round(q / 50) * 50
                target = base50 if r.random() < 0.5 else round(q / 100) * 100
                if target == q:
                    target = base50 + 50 if base50 + 50 != q else base50 - 50
                # never move more than one band
                if abs(band(target) - band(q)) > 1:
                    target = base50
                d2 = replace(data, qty_mt=float(target))
            else:  # dup_format_noise
                d2 = data
                noise_level = "heavy"
            self.add(self.later(t0), banks[1 + j], b, d2, scn, True, noise_level)

    def decoy_case(self, scn: str, hard: bool = False) -> None:
        r = self.rng
        bx, by = r.sample(T.BANKS, 2)
        a = self.fresh()
        data = self.cargo(a, dmax=12.0 if hard else 15.0)
        t0 = self.rand_time(0, 75)
        self.add(t0, bx, a, data, "decoy_anchor", False)
        t1 = self.later(t0)
        if hard:
            c = self.fresh()
            delta = r.choice([-10, -6, -4, 4, 6, 10]) + 0.0
            d2 = self.cargo(c, commodity=data.commodity, vv=(data.vessel, data.voyage), qty=data.qty_mt + delta)
            d2 = replace(d2, pol=data.pol, pod=data.pod, ship_date=data.ship_date)
            self.add(t1, by, c, d2, "decoy_hard_same_voyage_same_qty", False)
        elif scn == "decoy_coloaded":
            c = self.fresh()
            d2 = self.cargo(c, commodity=self.other_commodity(data.commodity), vv=(data.vessel, data.voyage))
            d2 = replace(d2, pol=data.pol, pod=data.pod, ship_date=data.ship_date)
            self.add(t1, by, c, d2, scn, False)
        elif scn == "decoy_related_diff_cargo":
            b = self.reg.make_linked(a, r.choice([("director",), ("ubo",)]))
            d2 = self.cargo(b, commodity=self.other_commodity(data.commodity))
            self.add(t1, by, b, d2, scn, False)
        elif scn == "decoy_same_commodity_diff_voyage":
            c = self.fresh()
            d2 = self.cargo(c, commodity=data.commodity, qty=data.qty_mt)
            self.add(t1, by, c, d2, scn, False)
        else:  # decoy_same_bank_refinance: same cargo re-presented at the SAME bank
            self.add(t1, bx, a, data, scn, False)

    # -- transactions
    def transactions(self) -> list[dict]:
        r = self.rng
        rows: list[dict] = []
        banks_of: dict[str, list[str]] = {}
        for d in self.drafts:
            lst = banks_of.setdefault(d.borrower["company_id"], [])
            if d.bank not in lst:
                lst.append(d.bank)
        by_id = {c["company_id"]: c for c in self.reg.companies}
        counterparties = [x.replace("  ", " ") for x in T.BUYER_NAMES] + [c["name"] for c in self.reg.companies[:40]]
        start, end = date(2025, 9, 1), date(2026, 5, 30)
        span = (end - start).days

        def add(cid: str, bank: str, when: date, amount: float, cur: str, cp: str, typ: str) -> None:
            rows.append({"txn_id": "", "company_id": cid, "bank_id": bank, "txn_date": when,
                         "amount": amount, "currency": cur, "counterparty": cp, "txn_type": typ})

        for cid in sorted(banks_of):
            c = by_id[cid]
            cur = "INR" if c["country"] == "IN" else "USD"
            scale = 1_000_000 if cur == "INR" else 20_000
            for bank in banks_of[cid]:
                for _ in range(r.randint(14, 30)):
                    add(cid, bank, start + timedelta(days=r.randint(0, span)),
                        round(r.uniform(0.3, 25) * scale, 2), cur, r.choice(counterparties),
                        r.choice(["TT_RECEIPT", "TT_PAYMENT", "LC_PAYMENT", "TRADE_SETTLEMENT", "CHEQUE"]))
        # round-tripping between genuinely linked borrowers of duplicate cases
        done: set[tuple[str, str]] = set()
        for d in self.drafts:
            if not d.label or d.scenario in ("dup_exact_same_borrower", "dup_weak_unlinked"):
                continue
            o = next((x for x in self.drafts if x.scenario.startswith("dup_original_") and x.data.vessel == d.data.vessel
                      and x.data.voyage == d.data.voyage and x.data.commodity == d.data.commodity), None)
            if o is None or (o.borrower["company_id"], d.borrower["company_id"]) in done:
                continue
            done.add((o.borrower["company_id"], d.borrower["company_id"]))
            a, b = o.borrower, d.borrower
            cur = "INR" if a["country"] == "IN" else "USD"
            unit = 2_500_000 if cur == "INR" else 30_000
            for _ in range(r.randint(2, 4)):
                day = start + timedelta(days=r.randint(0, span - 6))
                amt = float(unit * r.randint(2, 12))
                add(a["company_id"], o.bank, day, amt, cur, b["name"], "TT_PAYMENT")
                add(b["company_id"], d.bank, day, amt, cur, a["name"], "TT_RECEIPT")
                back = day + timedelta(days=r.randint(1, 4))
                add(b["company_id"], d.bank, back, amt, cur, a["name"], "TT_PAYMENT")
                add(a["company_id"], o.bank, back, amt, cur, b["name"], "TT_RECEIPT")
        rows.sort(key=lambda x: (x["txn_date"], x["company_id"], x["bank_id"], x["amount"]))
        for i, row in enumerate(rows, 1):
            row["txn_id"] = f"TXN-{i:06d}"
        return rows


# --------------------------------------------------------------------------- public API
def generate(seed: int = 42, n_clean: int = 60, n_dup: int = 30, n_decoy: int = 20) -> SynthDataset:
    g = _Gen(seed)
    pool = g.reg.add_background(max(100, int(n_clean * 1.7)))

    for _ in range(n_clean):
        g.clean(pool)
    for i in range(n_dup):
        g.dup_case(DUP_SCENARIOS[i % len(DUP_SCENARIOS)])
    n_hard = min(2, n_decoy // 8)
    for i in range(n_decoy - n_hard):
        g.decoy_case(DECOY_SCENARIOS[i % len(DECOY_SCENARIOS)])
    for _ in range(n_hard):
        g.decoy_case("decoy_hard_same_voyage_same_qty", hard=True)

    order = sorted(g.drafts, key=lambda d: (d.at, d.seq))
    requests: list[FinancingRequest] = []
    labels: dict[str, bool] = {}
    scenarios: dict[str, str] = {}
    for i, d in enumerate(order, 1):
        rid = f"REQ-{i:05d}"
        rrng = random.Random(f"{seed}:{rid}")
        docs = T.render_documents(rid, d.data, d.noise, rrng)
        amount = round(d.data.value * d.advance, -2)
        requests.append(FinancingRequest(rid, d.bank, d.borrower["company_id"], float(amount),
                                         d.data.currency, d.at, docs))
        labels[rid] = d.label
        scenarios[rid] = d.scenario

    txns = g.transactions()
    reg = g.reg
    ds = SynthDataset(
        companies=reg.companies, persons=reg.persons, roles=reg.roles, corp_owners=reg.corp,
        addresses=reg.addresses, transactions=txns, policy_clauses=[dict(c) for c in T.POLICY_CLAUSES],
        requests=requests, labels=labels, scenarios=scenarios,
        borrower_reg_no={c["company_id"]: c["reg_no"] for c in reg.companies},
    )
    counts: dict[str, int] = {}
    for s in scenarios.values():
        counts[s] = counts.get(s, 0) + 1
    log.info("synth_generated", extra={"ctx": {"seed": seed, "requests": len(requests),
                                                "companies": len(reg.companies), "scenarios": counts}})
    return ds


def load_into(store, ds: SynthDataset) -> None:
    """Load the registry, transactions and policy clauses into a Store."""
    store.load_registry(ds.companies, ds.persons, ds.roles, ds.corp_owners, ds.addresses,
                        ds.transactions, ds.policy_clauses)
    log.info("synth_loaded", extra={"ctx": {"companies": len(ds.companies), "transactions": len(ds.transactions)}})
