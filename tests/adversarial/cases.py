"""Independent adversarial scenarios for Suraksha (written blind to detector internals).

Each Scenario is a sequence of requests; expectation applies to the LAST request.
  must_flag  -> status != CLEAR
  must_clear -> status == CLEAR
  either     -> ambiguous in the real world; reported, not scored
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from suraksha.models import Document, DocType, FinancingRequest
from suraksha.store.memory import MemoryStore

# --------------------------------------------------------------------------- registry
_COMPANIES = [
    # (id, name, reg_no, address, phone)
    ("RT01", "Al Noor Trading FZE", "REG-1001", "ADDR01", "+971-4-1110001"),
    ("RT02", "Gulf Meridian Commodities LLC", "REG-1002", "ADDR02", "+971-4-2220002"),
    ("RT03", "Falcon Bay Metals DMCC", "REG-1003", "ADDR03", "+971-4-3330003"),  # shares UBO P01 with RT01
    ("RT04", "Zenith Offshore Supplies Ltd", "REG-1004", "ADDR04", "+44-20-4440004"),  # 4 hops to P01
    ("RT05", "Zenith Mid Holdings One", "REG-1005", "ADDR05", "+44-20-5550005"),
    ("RT06", "Zenith Mid Holdings Two", "REG-1006", "ADDR06", "+44-20-6660006"),
    ("RT07", "Zenith Mid Holdings Three", "REG-1007", "ADDR07", "+44-20-7770007"),
    ("RT08", "Harbor Sugar Co", "REG-1008", "ADDR08", "+91-22-8880008"),  # nominee director P09
    ("RT09", "Deccan Edible Oils Pvt Ltd", "REG-1009", "ADDR09", "+91-22-9990009"),  # nominee P09
    ("RT10", "Kaveri Textiles Pvt Ltd", "REG-1010", "ADDR10", "+91-22-1010010"),  # nominee P09
    ("RT11", "Nordic Grain Traders AS", "REG-1011", "ADDR11", "+47-22-1111011"),
    ("RT12", "Sahara Holding Group WLL", "REG-1012", "ADDR12", "+973-1-2020012"),  # holding
    ("RT13", "Sahara Steel Trading WLL", "REG-1013", "ADDR13", "+973-1-1313013"),  # sub of RT12
    ("RT14", "Sahara Rice Imports WLL", "REG-1014", "ADDR14", "+973-1-1414014"),  # sub of RT12
    ("RT15", "Bosphorus Metals AS", "REG-1015", "ADDR15", "+90-212-1515015"),  # shares phone with RT02
    ("RT16", "Muscat Bulk Carriers SAOC", "REG-1016", "ADDR16", "+968-2-1616016"),
]
_PERSONS = [
    ("P01", "Khalid Al Mansoori"), ("P02", "Rashid bin Saeed Al Maktoum"), ("P03", "Priya Menon"),
    ("P04", "Anders Lindqvist"), ("P05", "Fatima Al-Zahra Hussain"), ("P06", "Omar Farouk"),
    ("P07", "Sunil Verma"), ("P08", "Yusuf Demir"), ("P09", "Corporate Nominee Services Ltd"),
    ("P10", "Hassan Al Khalifa"), ("P11", "Mehmet Kaya"), ("P12", "Salim Al Busaidi"),
]
_ROLES = [
    ("RT01", "P01", "UBO", 100.0), ("RT01", "P01", "DIRECTOR", None),
    ("RT02", "P02", "UBO", 100.0), ("RT02", "P02", "DIRECTOR", None),
    ("RT03", "P01", "UBO", 60.0), ("RT03", "P03", "DIRECTOR", None),
    ("RT07", "P01", "SHAREHOLDER", 100.0), ("RT04", "P06", "DIRECTOR", None),
    ("RT05", "P07", "DIRECTOR", None), ("RT06", "P07", "DIRECTOR", None),
    ("RT08", "P09", "DIRECTOR", None), ("RT09", "P09", "DIRECTOR", None), ("RT10", "P09", "DIRECTOR", None),
    ("RT08", "P03", "UBO", 100.0), ("RT09", "P04", "UBO", 100.0), ("RT10", "P05", "UBO", 100.0),
    ("RT11", "P04", "DIRECTOR", None),
    ("RT12", "P10", "UBO", 100.0), ("RT13", "P10", "DIRECTOR", None), ("RT14", "P10", "DIRECTOR", None),
    ("RT15", "P11", "UBO", 100.0), ("RT16", "P12", "UBO", 100.0),
]
_CORP_OWNERS = [
    ("RT07", "RT06", 100.0), ("RT06", "RT05", 100.0), ("RT05", "RT04", 100.0),  # P01 -> RT07 -> RT06 -> RT05 -> RT04
    ("RT12", "RT13", 100.0), ("RT12", "RT14", 100.0),
]
_CITIES = [("Dubai", "AE")] * 3 + [("London", "GB")] * 4 + [("Mumbai", "IN")] * 3 + [("Oslo", "NO")] \
    + [("Manama", "BH")] * 3 + [("Istanbul", "TR"), ("Muscat", "OM")]
_ADDRS = [(f"ADDR{i:02d}", f"Unit {i}, Business Bay Tower {i}", c, k) for i, (c, k) in enumerate(_CITIES, start=1)]
_CLAUSES = [
    ("TF-3.1", "3", "Collateral uniqueness", "Cargo documents must not be pledged to more than one lender.",
     ["duplicate_financing", "collateral"]),
    ("TF-4.2", "4", "Related party", "Related-party links to prior pledgors must be escalated.", ["related_party"]),
    ("AML-7.4", "7", "STR filing", "File an STR within 7 days of suspicion.", ["str_filing"]),
    ("TF-5.1", "5", "Hold", "Hold disbursement pending officer review.", ["hold"]),
]


def build_store() -> MemoryStore:
    s = MemoryStore()
    s.load_registry(
        companies=[dict(company_id=i, name=n, reg_no=r, address_id=a, phone=p,
                        incorporated=date(2015, 1, 1), country="AE") for i, n, r, a, p in _COMPANIES],
        persons=[dict(person_id=i, name=n, id_hash=f"h-{i}") for i, n in _PERSONS],
        roles=[dict(company_id=c, person_id=p, role=r, pct=pc) for c, p, r, pc in _ROLES],
        corp_owners=[dict(owner_company_id=o, owned_company_id=w, pct=pc) for o, w, pc in _CORP_OWNERS],
        addresses=[dict(address_id=i, line=ln, city=c, country=k) for i, ln, c, k in _ADDRS],
        transactions=[
            dict(txn_id="T1", company_id="RT01", bank_id="BANK_A", txn_date=date(2026, 3, 1), amount=1.0e6,
                 currency="USD", counterparty="RT03", txn_type="TRANSFER"),
            dict(txn_id="T2", company_id="RT03", bank_id="BANK_B", txn_date=date(2026, 3, 5), amount=9.0e5,
                 currency="USD", counterparty="RT01", txn_type="TRANSFER"),
        ],
        policy_clauses=[dict(clause_id=a, section=b, title=c, text=d, tags=e) for a, b, c, d, e in _CLAUSES],
    )
    s.companies["RT15"]["phone"] = s.companies["RT02"]["phone"]  # only link between RT15 and RT02
    return s


# --------------------------------------------------------------------------- document builder
BASE = dict(
    bl="BL-ADX-77120", shipper="Sunrise Steel Exports Pte Ltd", consignee="Al Noor Trading FZE",
    vessel="Sea Falcon", voyage="066S", pol="Qingdao", pod="Jebel Ali",
    commodity="Hot Rolled Steel Coils", qty="5,000", unit="MT", date="2026-04-10",
    value="3,250,000", cur="USD", inv="INV-5501", lc="LC-8801", wr="WR-3301",
)


def make_docs(rid: str, borrower_name: str, omit: tuple = (), docs: tuple = ("BL", "INV", "LC", "WR"),
              labels: dict | None = None, **over) -> list[Document]:
    f = {**BASE, **over}
    L = {"bl": "B/L No", "vessel": "Vessel", "voyage": "Voyage No", **(labels or {})}
    bl = [
        "BILL OF LADING", f"{L['bl']}: {f['bl']}", f"Shipper: {f['shipper']}", f"Consignee: {f['consignee']}",
        f"{L['vessel']}: {f['vessel']}", f"{L['voyage']}: {f['voyage']}", f"Port of Loading: {f['pol']}",
        f"Port of Discharge: {f['pod']}", f"Description of Goods: {f['commodity']}",
        f"Quantity: {f['qty']} {f['unit']}", f"Shipped on Board: {f['date']}",
    ]
    inv = ["COMMERCIAL INVOICE", f"Invoice No: {f['inv']}", f"Seller: {f['shipper']}", f"Buyer: {f['consignee']}",
           f"Goods: {f['commodity']}", f"Quantity: {f['qty']} {f['unit']}", f"Total Value: {f['cur']} {f['value']}",
           f"B/L Ref: {f['bl']}"]
    lc = ["LETTER OF CREDIT", f"LC No: {f['lc']}", f"Applicant: {f['consignee']}", f"Beneficiary: {f['shipper']}",
          f"Amount: {f['cur']} {f['value']}", f"Goods: {f['commodity']}", f"Port of Loading: {f['pol']}",
          f"Port of Discharge: {f['pod']}", f"Latest Shipment Date: {f['date']}"]
    wr = ["WAREHOUSE RECEIPT", f"Receipt No: {f['wr']}", f"Depositor: {borrower_name}", f"Commodity: {f['commodity']}",
          f"Quantity: {f['qty']} {f['unit']}", f"Ex Vessel: {f['vessel']} / {f['voyage']}"]
    out = []
    for key, t, lines in (("BL", DocType.BILL_OF_LADING, bl), ("INV", DocType.INVOICE, inv),
                          ("LC", DocType.LC, lc), ("WR", DocType.WAREHOUSE_RECEIPT, wr)):
        if key not in docs:
            continue
        keep = [ln for ln in lines if not any(ln.startswith(o + ":") for o in omit)]
        out.append(Document(doc_id=f"{rid}-{key}", request_id=rid, doc_type=t, text="\n".join(keep)))
    return out


# --------------------------------------------------------------------------- scenario model
@dataclass
class Step:
    bank: str
    borrower: str
    over: dict = field(default_factory=dict)  # make_docs overrides
    amount: float = 3_250_000.0
    days: int = 0  # submission offset


@dataclass
class Scenario:
    name: str
    expect: str  # must_flag | must_clear | either
    steps: list
    doc: str

    def requests(self, store: MemoryStore) -> list[FinancingRequest]:
        t0 = datetime(2026, 4, 20, 10, 0)
        reqs = []
        for i, st in enumerate(self.steps):
            rid = f"{self.name}-{i}"
            name = store.companies[st.borrower]["name"]
            over = dict(st.over)
            consignee = over.pop("consignee", name)
            reqs.append(FinancingRequest(
                request_id=rid, bank_id=st.bank, borrower_id=st.borrower, amount=st.amount, currency="USD",
                submitted_at=t0 + timedelta(days=st.days, hours=i),
                documents=make_docs(rid, name, consignee=consignee, **over)))
        return reqs


SCENARIOS: list[Scenario] = []


def sc(name: str, expect: str, steps: list, doc: str) -> None:
    SCENARIOS.append(Scenario(name, expect, steps, doc.strip()))


A, B, C = "BANK_A", "BANK_B", "BANK_C"

# ---- baselines
sc("original_pledge", "must_clear", [Step(A, "RT01")],
   "The very first pledge of a cargo. Nothing in the consortium; must never alert.")
sc("clean_unrelated_sequence", "must_clear",
   [Step(A, "RT01"),
    Step(B, "RT02", dict(bl="BL-OTH-1", vessel="Pacific Dawn", voyage="12N", commodity="Raw Sugar", qty="20,000",
                         pol="Santos", pod="Karachi")),
    Step(C, "RT11", dict(bl="BL-OTH-2", vessel="Nordic Star", voyage="03E", commodity="Wheat", qty="30,000",
                         pol="Odessa", pod="Alexandria"))],
   "Three banks, three genuinely different cargoes by unrelated parties. Honest book of business.")
sc("same_bank_represent", "must_clear", [Step(A, "RT01"), Step(A, "RT01", days=3)],
   "Trader re-presents the same documents to the SAME bank (resubmission after a typo fix). That bank already "
   "knows; not cross-bank duplicate financing.")

# ---- classic duplicates
sc("dup_exact_same_borrower_two_banks", "must_flag", [Step(A, "RT01"), Step(B, "RT01", days=2)],
   "Textbook: same borrower pledges identical B/L at two banks.")
sc("dup_exact_related_borrower", "must_flag", [Step(A, "RT01"), Step(B, "RT03", days=2)],
   "Identical documents presented by a sister company sharing the UBO (P01).")
sc("dup_exact_unrelated_borrower", "must_flag", [Step(A, "RT01"), Step(B, "RT02", days=2)],
   "Identical B/L, vessel, voyage presented by a company with no registry link. Weak link, but identical cargo "
   "evidence must not be CLEAR.")
sc("dup_three_bank_chain", "must_flag", [Step(A, "RT01"), Step(B, "RT03", days=2), Step(C, "RT01", days=4)],
   "Cargo goes to A, then B, then back via the original borrower to C. The last request has two prior pledges.")
sc("dup_shell_four_hops", "must_flag", [Step(A, "RT01"), Step(B, "RT04", dict(bl="BL-REISSUED-9"), days=2)],
   "Reissued B/L presented by a shell owned through a four-company chain ending at the original UBO "
   "(P01 -> RT07 -> RT06 -> RT05 -> RT04).")

# ---- text-noise evasions on same cargo
sc("dup_vessel_typo", "must_flag", [Step(A, "RT01"), Step(B, "RT03", dict(vessel="Sea Falkon"), days=2)],
   "Single-letter vessel typo/transliteration ('Falcon' vs 'Falkon') on otherwise identical docs.")
sc("dup_mv_prefix_case_spacing", "must_flag",
   [Step(A, "RT01"), Step(B, "RT03", dict(vessel="M/V  SEA   FALCON"), days=2)],
   "Prefix, upper-casing and extra whitespace in vessel name.")
sc("dup_voyage_leading_zero", "must_flag", [Step(A, "RT01"), Step(B, "RT03", dict(voyage="66S"), days=2)],
   "Voyage '066S' re-keyed as '66S'.")
sc("dup_voyage_v_prefix", "must_flag", [Step(A, "RT01"), Step(B, "RT03", dict(voyage="V.066S"), days=2)],
   "Voyage '066S' re-keyed as 'V.066S'.")
sc("dup_bl_spaces_dashes", "must_flag", [Step(A, "RT01"), Step(B, "RT03", dict(bl="BL ADX 77120"), days=2)],
   "B/L number punctuation changed from dashes to spaces; cargo fields identical.")
sc("dup_bl_lowercase_compact", "must_flag", [Step(A, "RT01"), Step(B, "RT03", dict(bl="bladx77120"), days=2)],
   "B/L lower-cased with separators stripped.")
sc("dup_qty_kg", "must_flag",
   [Step(A, "RT01"), Step(B, "RT03", dict(qty="5,000,000", unit="KG"), days=2)],
   "Same cargo expressed as 5,000,000 KG instead of 5,000 MT.")
sc("dup_qty_tonnes_unit", "must_flag", [Step(A, "RT01"), Step(B, "RT03", dict(unit="TONNES"), days=2)],
   "Unit written TONNES instead of MT.")
sc("dup_qty_rounded", "must_flag",
   [Step(A, "RT01", dict(qty="4,987.5")), Step(B, "RT03", dict(qty="5,000"), days=2)],
   "Quantity rounded up on re-pledge, same B/L.")
sc("dup_commodity_synonym", "must_flag", [Step(A, "RT01"), Step(B, "RT03", dict(commodity="HRC"), days=2)],
   "'HRC' vs 'Hot Rolled Steel Coils' -- same goods, different words; related borrower.")
sc("dup_commodity_wordorder", "must_flag",
   [Step(A, "RT01"), Step(B, "RT03", dict(commodity="Steel Coils, Hot-Rolled"), days=2)],
   "Commodity re-worded.")
sc("dup_date_ddmmyyyy", "must_flag", [Step(A, "RT01"), Step(B, "RT03", dict(date="10/04/2026"), days=2)],
   "Shipment date in DD/MM/YYYY.")
sc("dup_alias_labels", "must_flag",
   [Step(A, "RT01"),
    Step(B, "RT03", dict(labels={"bl": "Bill of Lading No.", "vessel": "Ocean Vessel", "voyage": "Voy. No"}), days=2)],
   "Alternative field labels used by another carrier's template.")
sc("dup_arabic_gcc_names", "must_flag",
   [Step(A, "RT01", dict(shipper="Shirkat Al-Nour Lil-Tijara", vessel="Al Bahriyah Star", voyage="W-14")),
    Step(B, "RT03", dict(shipper="شركة النور للتجارة",
                         vessel="AL BAHRIYAH STAR", voyage="W14"), days=2)],
   "Shipper rendered in Arabic script the second time, vessel case and voyage hyphen differ.")

# ---- structural evasions
sc("dup_via_wr_new_bl", "must_flag",
   [Step(A, "RT01"),
    Step(B, "RT03", dict(bl="BL-TOTALLY-NEW-1", inv="INV-9", lc="LC-9", date="2026-04-15"), days=2)],
   "Second bank gets a brand-new B/L number, new invoice/LC numbers and a different shipment date; the same "
   "vessel/voyage/commodity/qty remain (incl. the warehouse receipt).")
sc("dup_altered_commodity_same_bl", "must_flag",
   [Step(A, "RT01"), Step(B, "RT03", dict(commodity="Cold Rolled Steel Sheets"), days=2)],
   "Same B/L and vessel but commodity text altered to dodge the cargo hash.")
sc("dup_altered_qty_same_bl", "must_flag", [Step(A, "RT01"), Step(B, "RT03", dict(qty="9,500"), days=2)],
   "Same B/L number and vessel, quantity inflated nearly 2x at the second bank.")
sc("dup_inflated_invoice_value", "must_flag",
   [Step(A, "RT01"), Step(B, "RT03", dict(value="4,900,000"), days=2)],
   "Cargo identical but invoice value overstated for a bigger loan.")
sc("dup_split_halves_same_bl", "must_flag",
   [Step(A, "RT01", dict(qty="2,500", value="1,625,000")),
    Step(B, "RT03", dict(qty="2,500", value="1,625,000", commodity="HR Coils"), days=2)],
   "Two banks each financed 'half' under the same B/L number; the document trail is one cargo.")
sc("dup_split_halves_diff_bl", "either",
   [Step(A, "RT01", dict(qty="2,500", bl="BL-HALF-1")), Step(B, "RT03", dict(qty="2,500", bl="BL-HALF-2"), days=2)],
   "Two B/Ls for two halves of one vessel parcel by related companies. Could be a legit split shipment or a "
   "fraudulent split; genuinely ambiguous.")
sc("dup_late_representation", "must_flag", [Step(A, "RT01"), Step(B, "RT01", days=120)],
   "Same borrower re-pledges the identical cargo documents four months later at another bank (old loan still open).")
sc("dup_missing_voyage_in_second", "must_flag",
   [Step(A, "RT01"), Step(B, "RT03", dict(omit=("Voyage No",)), days=2)],
   "Second presentation omits the voyage line from the B/L. Same B/L number, vessel, commodity, quantity remain; "
   "an extraction gap must not equal CLEAR.")
sc("dup_only_bl_and_invoice", "must_flag",
   [Step(A, "RT01"), Step(B, "RT03", dict(docs=("BL", "INV")), days=2)],
   "Second bank is shown only B/L and invoice (no LC, no warehouse receipt).")

# ---- honest-trader decoys
sc("decoy_coloaded_diff_commodity", "must_clear",
   [Step(A, "RT01"),
    Step(B, "RT02", dict(bl="BL-CO-2", commodity="Basmati Rice", qty="5,000", shipper="Punjab Agro Exports"), days=2)],
   "Two unrelated shippers' cargo on the same vessel and voyage (different commodity, different B/L).")
sc("decoy_partial_shipments_same_vessel", "must_clear",
   [Step(A, "RT01"),
    Step(B, "RT02", dict(bl="BL-ADX-77121", qty="1,200", shipper="Other Steel Co"), days=2)],
   "Same commodity on same vessel, but clearly a different parcel (1,200 MT vs 5,000 MT), different B/L and "
   "unrelated shipper.")
sc("decoy_same_commodity_diff_voyage", "must_clear",
   [Step(A, "RT01"), Step(B, "RT02", dict(bl="BL-NEW-3", voyage="071N"), days=30)],
   "Same vessel returns next month on a new voyage with same steel qty for another trader.")
sc("decoy_same_qty_diff_vessel", "must_clear",
   [Step(A, "RT01"),
    Step(B, "RT02", dict(bl="BL-NEW-4", vessel="Ocean Harmony", voyage="22W", shipper="Other Steel Co"), days=5)],
   "Common 5,000 MT HRC parcel on a different vessel; steel traders hit standard lot sizes all the time.")
sc("decoy_similar_vessel_name", "must_clear",
   [Step(A, "RT01"),
    Step(B, "RT02", dict(bl="BL-NEW-5", vessel="Sea Falcon II", voyage="009E", commodity="Crude Palm Oil",
                         qty="12,000", pol="Belawan", pod="Mundra"), days=5)],
   "Sister ship 'Sea Falcon II' is a real different vessel with different cargo; a fuzzy vessel matcher must not "
   "collapse them.")
sc("decoy_nominee_director_many_companies", "must_clear",
   [Step(A, "RT08", dict(bl="BL-SUG-1", commodity="Raw Sugar", vessel="Pacific Dawn", voyage="12N", qty="20,000",
                         pol="Santos", pod="Mumbai")),
    Step(B, "RT09", dict(bl="BL-OIL-1", commodity="Refined Palm Oil", vessel="Golden Ray", voyage="88E", qty="8,000",
                         pol="Belawan", pod="Kandla"), days=3),
    Step(C, "RT10", dict(bl="BL-COT-1", commodity="Cotton Bales", vessel="Aegean Pride", voyage="41W", qty="3,000",
                         pol="Alexandria", pod="Nhava Sheva"), days=5)],
   "A corporate-nominee director sits on three unrelated Indian companies (common in India/GCC). Shared nominee "
   "alone, with different cargo, is not fraud.")
sc("decoy_nominee_director_same_vessel", "must_clear",
   [Step(A, "RT08", dict(bl="BL-SUG-1", commodity="Raw Sugar", vessel="Pacific Dawn", voyage="12N", qty="20,000",
                         pol="Santos", pod="Mumbai")),
    Step(B, "RT09", dict(bl="BL-OIL-1", commodity="Refined Palm Oil", vessel="Pacific Dawn", voyage="12N", qty="8,000",
                         pol="Santos", pod="Mumbai"), days=3)],
   "Same nominee director, and the vessel/voyage is shared (co-loaded), but cargo and B/L differ.")
sc("decoy_holding_group_diff_cargo", "must_clear",
   [Step(A, "RT13", dict(bl="BL-STL-1", vessel="Gulf Pearl", voyage="05S", commodity="Steel Rebar", qty="6,000",
                         pol="Dammam", pod="Bahrain")),
    Step(B, "RT14", dict(bl="BL-RICE-1", vessel="Indus Trader", voyage="17N", commodity="Basmati Rice", qty="4,000",
                         pol="Kandla", pod="Bahrain"), days=3)],
   "Sister subsidiaries of one holding group each financing their own, different cargo at different banks.")
sc("decoy_shared_phone_only", "must_clear",
   [Step(A, "RT02", dict(bl="BL-PH-1", vessel="Anatolia", voyage="10E", commodity="Copper Cathode", qty="1,000",
                         pol="Mersin", pod="Jebel Ali")),
    Step(B, "RT15", dict(bl="BL-PH-2", vessel="Bosphorus Spirit", voyage="30E", commodity="Zinc Ingots", qty="2,000",
                         pol="Istanbul", pod="Aqaba"), days=3)],
   "Two companies share a switchboard phone number but trade different goods. No cargo overlap.")
sc("decoy_same_borrower_second_cargo", "must_clear",
   [Step(A, "RT01"),
    Step(B, "RT01", dict(bl="BL-NEXT-1", vessel="Pacific Dawn", voyage="12N", commodity="Urea Fertilizer",
                         qty="8,000", pol="Bandar Abbas", pod="Mombasa"), days=10)],
   "A prolific trader legitimately financing a second, unrelated cargo at another bank.")
sc("decoy_wr_only_unique", "must_clear",
   [Step(A, "RT16", dict(docs=("WR",), vessel="Bulk Odyssey", voyage="02E", commodity="Wheat", qty="15,000"))],
   "Warehouse-receipt-only financing (no B/L), nothing else in the consortium.")

# ---- ambiguous
sc("hard_same_voyage_same_qty_unrelated", "either",
   [Step(A, "RT01"), Step(B, "RT02", dict(bl="BL-HARD-1", shipper="Other Steel Co"), days=2)],
   "Unrelated shippers, same vessel/voyage/commodity/qty, different B/L. Coincidence is possible but suspicious.")
sc("dup_missing_all_ids", "either",
   [Step(A, "RT01"), Step(B, "RT03", dict(omit=("B/L No", "Voyage No"), docs=("BL", "INV")), days=2)],
   "Second presentation is missing B/L number and voyage; only vessel/commodity/qty remain. Ambiguous.")
