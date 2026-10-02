"""Static content + document text rendering for the synthetic data generator.

Document templates follow docs/CONTRACTS.md section A exactly (labels verbatim,
aliases only where listed). Noise variants are controlled by `Noise`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from suraksha.models import DocType, Document

BANKS = ["BANK_A", "BANK_B", "BANK_C"]

# name, usd_price_per_mt (lo, hi), qty_mt (lo, hi)
COMMODITIES: list[tuple[str, tuple[int, int], tuple[int, int]]] = [
    ("Hot Rolled Steel Coils", (600, 750), (1500, 8000)),
    ("Cold Rolled Steel Coils", (700, 850), (1000, 6000)),
    ("Copper Cathodes", (8500, 9800), (300, 2500)),
    ("Aluminium Ingots", (2300, 2800), (500, 4000)),
    ("Crude Palm Oil", (850, 1050), (2000, 9000)),
    ("Nickel Cathodes", (15000, 18000), (200, 1500)),
    ("White Refined Sugar", (380, 480), (3000, 9000)),
    ("Granular Urea", (330, 450), (3000, 9000)),
    ("Thermal Coal", (90, 140), (4000, 9500)),
    ("Non-Basmati Rice", (380, 480), (2000, 8000)),
]

VESSELS = [
    "MSC Aurora", "Maersk Kolkata", "CMA CGM Tigris", "Ever Gentle", "OOCL Mumbai",
    "Hapag Valparaiso", "ONE Harmony", "Cosco Shanghai Star", "Yang Ming Unity", "Evergreen Triton",
    "Gulf Pearl", "Al Mirfa Express", "Jebel Ali Carrier", "Sea Falcon", "Nordic Bellatrix",
    "Pacific Tern", "Star Hydra", "Ocean Meridian", "Chennai Express", "Mundra Spirit",
    "Bahri Dammam", "Emirates Dana", "Navios Horizon", "Pioneer Jade", "Atlantic Sovereign",
    "Kota Lambang", "Wan Hai 325", "Sinar Bali", "Tasman Orchid", "Seaspan Ganges",
    "Zim Mumbai", "Pegasus Trader", "Golden Marlin", "Orient Aspire", "Bulk Sapphire",
    "Vishva Sagar", "Desert Rose", "Lotus Voyager", "Coral Dawn", "Blue Cormorant",
]

# (pol, pod)
PORT_PAIRS = [
    ("Mundra", "Jebel Ali"), ("JNPT", "Dammam"), ("Chennai", "Singapore"), ("Mundra", "Singapore"),
    ("Qingdao", "Mundra"), ("Qingdao", "JNPT"), ("Singapore", "Chennai"), ("Jebel Ali", "JNPT"),
    ("Dammam", "Mundra"), ("Jebel Ali", "Chennai"), ("Singapore", "Qingdao"), ("JNPT", "Jebel Ali"),
    ("Chennai", "Dammam"), ("Singapore", "Mundra"), ("Qingdao", "Chennai"),
]

CARRIER_PREFIXES = ["MAEU", "MSCU", "HLCU", "COSU", "ONEY", "CMDU", "EGLV", "OOLU"]

# --- people / company name pools
IN_FIRST = ["Rajesh", "Anil", "Sunita", "Vikram", "Priya", "Arjun", "Meera", "Sanjay", "Kavita", "Rohit",
            "Deepak", "Neha", "Amit", "Pooja", "Suresh", "Anjali", "Manoj", "Divya", "Karan", "Lakshmi",
            "Harish", "Ritu", "Naveen", "Shalini", "Prakash", "Geeta", "Vivek", "Nisha", "Ashok", "Swati"]
IN_LAST = ["Sharma", "Mehta", "Iyer", "Reddy", "Agarwal", "Patel", "Nair", "Kapoor", "Singh", "Gupta",
           "Joshi", "Menon", "Bansal", "Shah", "Desai", "Chopra", "Banerjee", "Rao", "Malhotra", "Pillai",
           "Khanna", "Bhatia", "Saxena", "Trivedi", "Kulkarni", "Verma", "Jain", "Thakur", "Naidu", "Sethi"]
GCC_FIRST = ["Khalid", "Fatima", "Omar", "Aisha", "Yusuf", "Mariam", "Hassan", "Layla", "Ibrahim", "Noor",
             "Saeed", "Huda", "Tariq", "Salma", "Rashid", "Amina", "Faisal", "Zainab", "Majid", "Hana"]
GCC_LAST = ["Al Mansoori", "Al Hashimi", "Al Suwaidi", "Al Qasimi", "Al Dhaheri", "Al Otaibi", "Al Harbi",
            "Al Thani", "Al Kuwari", "Al Balushi", "Al Falasi", "Al Zahrani", "Al Qahtani", "Al Maktoum",
            "Bin Rashid", "Bin Saeed", "Al Nuaimi", "Al Shamsi"]

NAME_STEMS = ["Shree", "Sagar", "Jai", "Vardhaman", "Kaveri", "Ganga", "Mahalaxmi", "Surya", "Tirupati", "Himalaya",
              "Arihant", "Bharat", "Deccan", "Konark", "Narmada", "Orient", "Pioneer", "Sapphire", "Trident", "Vasudha",
              "Crescent", "Falcon", "Horizon", "Meridian", "Zenith", "Platinum", "Emerald", "Anchor", "Atlas", "Nexus",
              "Gulf Star", "Al Noor", "Al Waha", "Al Safa", "Desert Rose", "Pearl Coast", "Golden Dune", "Marina", "Najm", "Rimal"]
NAME_SECTORS = ["Metals", "Steel", "Commodities", "Agro", "Traders", "Exports", "Impex", "Global Trading", "Industries",
                "Minerals", "Overseas", "Enterprises", "Logistics", "Foods", "Chemicals", "Alloys", "Resources", "Ventures"]
IN_SUFFIX = ["Pvt Ltd", "Private Limited", "Ltd", "LLP"]
AE_SUFFIX = ["FZE", "FZ-LLC", "LLC", "General Trading LLC"]
SA_SUFFIX = ["Trading Co", "Co. Ltd", "Est."]
QA_SUFFIX = ["W.L.L", "Trading W.L.L"]

BUYER_NAMES = [
    "Gulf Metals Distribution LLC", "Rotterdam Commodities BV", "Al Rawabi Foods Co", "Pacific Rim Traders Pte Ltd",
    "Sinopec Trade Hub Ltd", "Hamburg Industrial Supply GmbH", "Emirates Bulk Importers LLC", "Lagos Agro Trade Ltd",
    "Dhaka Steel Works Ltd", "Colombo Foods Importers", "Jeddah Industrial Metals Co", "Doha Refining Supplies WLL",
    "Busan Alloy Corp", "Istanbul Metal Sanayi AS", "Mombasa Grain Merchants Ltd", "Karachi Edible Oils Pvt Ltd",
    "Ho Chi Minh Steel JSC", "Muscat Construction Materials LLC", "Antwerp Metals NV", "Durban Bulk Terminals Pty Ltd",
    "Manila Sugar Refiners Inc", "Tianjin Port Resources Co", "Kuwait Fertilizer Imports KSC", "Port Klang Commodities Sdn Bhd",
]

CITIES: dict[str, list[tuple[str, str]]] = {
    # country -> [(city, state/emirate code)]
    "IN": [("Mumbai", "MH"), ("Pune", "MH"), ("Chennai", "TN"), ("New Delhi", "DL"), ("Ahmedabad", "GJ"),
           ("Surat", "GJ"), ("Kolkata", "WB"), ("Bengaluru", "KA"), ("Hyderabad", "TG"), ("Kochi", "KL")],
    "AE": [("Dubai", "DXB"), ("Sharjah", "SHJ"), ("Abu Dhabi", "AUH"), ("Jebel Ali", "DXB")],
    "SA": [("Dammam", "DMM"), ("Jeddah", "JED"), ("Riyadh", "RUH")],
    "QA": [("Doha", "DOH")],
}
STREETS = ["MG Road", "Nehru Place", "Industrial Estate Phase 2", "Dalal Street", "Mount Road", "Ring Road", "Link Road",
           "Business Bay Tower", "Sheikh Zayed Road", "Al Quoz Industrial Area", "Corniche Road", "Port Road", "Trade Centre Complex",
           "Okhla Industrial Area", "SEEPZ Zone", "Naroda GIDC", "Hosur Road", "Banjara Hills", "Marine Drive", "Al Khobar Street"]

# --- policy clauses
POLICY_CLAUSES: list[dict] = [
    {"clause_id": "TF-3.1", "section": "TF-3", "title": "Single-pledge principle",
     "text": "A single shipment of goods may be pledged as collateral to only one financier at a time. Documents of title presented to more than one lender for the same cargo constitute duplicate financing.",
     "tags": ["duplicate_financing", "collateral"]},
    {"clause_id": "TF-3.2", "section": "TF-3", "title": "Consortium duplicate check",
     "text": "Before disbursement against a bill of lading or warehouse receipt, the bank must run the consortium fingerprint check and may not release funds while an unresolved match exists.",
     "tags": ["duplicate_financing", "hold"]},
    {"clause_id": "TF-4.1", "section": "TF-4", "title": "Collateral verification",
     "text": "Quantity, vessel, voyage and commodity on the bill of lading must reconcile with the invoice, letter of credit and warehouse receipt before limits are drawn.",
     "tags": ["collateral"]},
    {"clause_id": "TF-4.2", "section": "TF-4", "title": "Document re-issuance",
     "text": "Where a bill of lading number differs but vessel, voyage, commodity and quantity band are unchanged, the transaction must be treated as a suspected re-issued document pending carrier confirmation.",
     "tags": ["duplicate_financing", "collateral", "hold"]},
    {"clause_id": "TF-5.3", "section": "TF-5", "title": "Related-party borrowers",
     "text": "Borrowers sharing a beneficial owner, director, registered address, telephone number or holding company are related parties; related-party exposures against the same cargo must be escalated.",
     "tags": ["related_party", "duplicate_financing"]},
    {"clause_id": "TF-5.4", "section": "TF-5", "title": "Common ownership look-through",
     "text": "Ownership must be looked through up to four hops, including holding companies, to identify the ultimate beneficial owner of each borrower.",
     "tags": ["related_party"]},
    {"clause_id": "TF-6.1", "section": "TF-6", "title": "Disbursement hold",
     "text": "On a high-confidence duplicate-financing indicator, disbursement must be placed on hold pending review by the compliance officer.",
     "tags": ["hold", "duplicate_financing"]},
    {"clause_id": "AML-7.1", "section": "AML-7", "title": "Round-tripping indicators",
     "text": "Funds moving between related entities in near-identical amounts within a short period, with no trade rationale, are an indicator of round-tripping and layering.",
     "tags": ["related_party", "str_filing"]},
    {"clause_id": "AML-7.4", "section": "AML-7", "title": "STR filing obligation",
     "text": "A suspicious transaction report must be filed with FIU-IND within seven working days of the principal officer concluding that a transaction is suspicious.",
     "tags": ["str_filing"]},
    {"clause_id": "AML-7.5", "section": "AML-7", "title": "Tipping-off prohibition",
     "text": "Staff must not disclose to the customer that an STR is being considered or has been filed.",
     "tags": ["str_filing"]},
    {"clause_id": "AML-8.2", "section": "AML-8", "title": "Human approval of STRs",
     "text": "Automated systems may draft, but not file, a suspicious transaction report. A named officer must approve or reject each draft and the decision must be recorded in the audit log.",
     "tags": ["str_filing", "hold"]},
    {"clause_id": "AML-8.5", "section": "AML-8", "title": "Evidence retention",
     "text": "All supporting documents, registry extracts and consortium match records relied on in an STR must be retained for five years in tamper-evident form.",
     "tags": ["str_filing", "collateral"]},
]


# --- rendering
@dataclass
class Noise:
    aliases: bool = False
    mv_prefix: str = ""          # "", "M/V ", "MV "
    date_fmt: str = "iso"        # "iso" | "dmy"
    qty_commas: bool = False
    qty_unit: str = "MT"         # MT | TONNES | KG
    case: str = "none"           # none | upper | lower
    extra_spaces: bool = False
    value_commas: bool = True


@dataclass
class DocData:
    bl: str
    shipper: str
    consignee: str
    vessel: str
    voyage: str
    pol: str
    pod: str
    commodity: str
    qty_mt: float
    currency: str
    value: int
    ship_date: date
    inv: str
    lc: str
    wr: str
    depositor: str


def make_noise(rng: random.Random, level: str) -> Noise:
    """level: 'none' | 'mild' (~20% of ordinary requests) | 'heavy' (dup_format_noise)."""
    if level == "none":
        return Noise()
    if level == "mild":
        return Noise(
            aliases=rng.random() < 0.6,
            mv_prefix=rng.choice(["", "M/V ", "MV "]),
            date_fmt=rng.choice(["iso", "dmy"]),
            qty_commas=rng.random() < 0.6,
            qty_unit=rng.choice(["MT", "TONNES"]),
        )
    return Noise(
        aliases=rng.random() < 0.5,
        mv_prefix=rng.choice(["M/V ", "MV "]),
        date_fmt="dmy",
        qty_commas=True,
        qty_unit="KG",
        case=rng.choice(["upper", "lower"]),
        extra_spaces=True,
    )


def _num(x: float, commas: bool) -> str:
    if float(x).is_integer():
        return f"{int(x):,}" if commas else str(int(x))
    return (f"{x:,.2f}" if commas else f"{x:.2f}").rstrip("0").rstrip(".")


def _txt(s: str, n: Noise) -> str:
    if n.case == "upper":
        s = s.upper()
    elif n.case == "lower":
        s = s.lower()
    if n.extra_spaces:
        s = s.replace(" ", "  ")
    return s


def _date(d: date, n: Noise) -> str:
    return d.strftime("%d/%m/%Y") if n.date_fmt == "dmy" else d.isoformat()


def _qty(data: DocData, n: Noise, qty_mt: float | None = None) -> str:
    base = data.qty_mt if qty_mt is None else qty_mt
    q = base * 1000 if n.qty_unit == "KG" else base
    return f"{_num(q, n.qty_commas)} {n.qty_unit}"


def render_documents(request_id: str, d: DocData, n: Noise, rng: random.Random,
                     ov: dict | None = None) -> list[Document]:
    """`ov` (optional, new-rules scenarios only) makes ONE document disagree with the others:
    inv_qty_mt / wr_qty_mt (quantity on the invoice / warehouse receipt, in MT), lc_value (LC amount)."""
    ov = ov or {}
    pick = (lambda opts: rng.choice(opts)) if n.aliases else (lambda opts: opts[0])
    value = _num(d.value, n.value_commas)
    goods = _txt(d.commodity, n)
    vessel = n.mv_prefix + _txt(d.vessel, n)
    pol, pod = _txt(d.pol, n), _txt(d.pod, n)
    qty = _qty(d, n)
    inv_qty = _qty(d, n, ov.get("inv_qty_mt"))
    wr_qty = _qty(d, n, ov.get("wr_qty_mt"))
    lc_value = _num(ov["lc_value"], n.value_commas) if "lc_value" in ov else value
    sdate = _date(d.ship_date, n)
    lc_date = _date(d.ship_date + timedelta(days=21), n)

    bl = "\n".join([
        "BILL OF LADING",
        f"{pick(['B/L No', 'B/L Number', 'Bill of Lading No.'])}: {d.bl}",
        f"Shipper: {d.shipper}",
        f"Consignee: {d.consignee}",
        f"{pick(['Vessel', 'Ocean Vessel'])}: {vessel}",
        f"{pick(['Voyage No', 'Voy. No', 'Voyage'])}: {d.voyage}",
        f"Port of Loading: {pol}",
        f"Port of Discharge: {pod}",
        f"Description of Goods: {goods}",
        f"Quantity: {qty}",
        f"Shipped on Board: {sdate}",
    ])
    inv = "\n".join([
        "COMMERCIAL INVOICE",
        f"Invoice No: {d.inv}",
        f"Seller: {d.shipper}",
        f"Buyer: {d.consignee}",
        f"Goods: {goods}",
        f"Quantity: {inv_qty}",
        f"Total Value: {d.currency} {value}",
        f"B/L Ref: {d.bl}",
    ])
    lc = "\n".join([
        "LETTER OF CREDIT",
        f"LC No: {d.lc}",
        f"Applicant: {d.consignee}",
        f"Beneficiary: {d.shipper}",
        f"Amount: {d.currency} {lc_value}",
        f"Goods: {goods}",
        f"Port of Loading: {pol}",
        f"Port of Discharge: {pod}",
        f"Latest Shipment Date: {lc_date}",
    ])
    wr = "\n".join([
        "WAREHOUSE RECEIPT",
        f"Receipt No: {d.wr}",
        f"Depositor: {d.depositor}",
        f"Commodity: {goods}",
        f"Quantity: {wr_qty}",
        f"Ex Vessel: {_txt(d.vessel, n)} / {d.voyage}",
    ])
    return [
        Document(f"{request_id}-BL", request_id, DocType.BILL_OF_LADING, bl),
        Document(f"{request_id}-INVOICE", request_id, DocType.INVOICE, inv),
        Document(f"{request_id}-LC", request_id, DocType.LC, lc),
        Document(f"{request_id}-WR", request_id, DocType.WAREHOUSE_RECEIPT, wr),
    ]
