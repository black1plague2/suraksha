"""Intake agent: deterministic, tolerant regex extraction over document text.

B/L is authoritative for bl_number / vessel / voyage / ports / commodity / quantity /
shipment_date; the invoice for value / currency (fallback LC). Missing fields fall back
to the other documents. Every filled field carries a DOCUMENT citation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from suraksha.log import get_logger
from suraksha.models import (
    Citation,
    CitationKind,
    Document,
    DocType,
    ExtractedFields,
    FinancingRequest,
)

log = get_logger(__name__)

# --------------------------------------------------------------------------- units
_UNIT_FACTORS = {
    "MT": 1.0, "MTS": 1.0,
    "TONNE": 1.0, "TONNES": 1.0, "TON": 1.0, "TONS": 1.0,
    "KG": 0.001, "KGS": 0.001, "KILOGRAM": 0.001, "KILOGRAMS": 0.001,
}


def normalize_quantity(qty: float, unit: str) -> tuple[float, str]:
    """Convert a quantity to metric tonnes. Unknown units are returned unchanged (upper-cased)."""
    u = (unit or "").strip().upper().replace(".", "")
    factor = _UNIT_FACTORS.get(u)
    if factor is None:
        return float(qty), u
    return round(float(qty) * factor, 6), "MT"


# --------------------------------------------------------------------------- label aliases
# doc type -> field -> label regex fragments (longest first)
_LABELS: dict[DocType, dict[str, list[str]]] = {
    DocType.BILL_OF_LADING: {
        "bl_number": [r"bill\s+of\s+lading\s+no\.?", r"bill\s+of\s+lading\s+number", r"b/l\s+number", r"b/l\s+no\.?", r"bl\s+no\.?"],
        "shipper": [r"shipper"],
        "consignee": [r"consignee"],
        "vessel": [r"ocean\s+vessel", r"vessel"],
        "voyage": [r"voyage\s+no\.?", r"voy\.?\s*no\.?", r"voyage"],
        "port_of_loading": [r"port\s+of\s+loading", r"pol"],
        "port_of_discharge": [r"port\s+of\s+discharge", r"pod"],
        "commodity": [r"description\s+of\s+goods", r"goods", r"commodity"],
        "quantity": [r"quantity", r"qty"],
        "shipment_date": [r"shipped\s+on\s+board", r"on\s+board\s+date"],
    },
    DocType.INVOICE: {
        "bl_number": [r"b/l\s+ref\.?", r"b/l\s+reference", r"bl\s+ref\.?"],
        "shipper": [r"seller"],
        "consignee": [r"buyer"],
        "commodity": [r"description\s+of\s+goods", r"goods", r"commodity"],
        "quantity": [r"quantity", r"qty"],
        "value": [r"total\s+value", r"invoice\s+value", r"total\s+amount"],
    },
    DocType.LC: {
        "consignee": [r"applicant"],
        "shipper": [r"beneficiary"],
        "value": [r"lc\s+amount", r"amount"],
        "commodity": [r"description\s+of\s+goods", r"goods", r"commodity"],
        "port_of_loading": [r"port\s+of\s+loading"],
        "port_of_discharge": [r"port\s+of\s+discharge"],
        "shipment_date": [r"latest\s+shipment\s+date", r"latest\s+date\s+of\s+shipment"],
    },
    DocType.WAREHOUSE_RECEIPT: {
        "commodity": [r"commodity", r"goods"],
        "quantity": [r"quantity", r"qty"],
        "ex_vessel": [r"ex[\s\-]*vessel"],
    },
}

# Field -> ordered doc types to consult (first hit wins).
_PRIORITY: dict[str, list[DocType]] = {
    "bl_number": [DocType.BILL_OF_LADING, DocType.INVOICE],
    "vessel": [DocType.BILL_OF_LADING],
    "voyage": [DocType.BILL_OF_LADING],
    "port_of_loading": [DocType.BILL_OF_LADING, DocType.LC],
    "port_of_discharge": [DocType.BILL_OF_LADING, DocType.LC],
    "commodity": [DocType.BILL_OF_LADING, DocType.INVOICE, DocType.LC, DocType.WAREHOUSE_RECEIPT],
    "quantity": [DocType.BILL_OF_LADING, DocType.INVOICE, DocType.WAREHOUSE_RECEIPT],
    "value": [DocType.INVOICE, DocType.LC],
    "shipment_date": [DocType.BILL_OF_LADING, DocType.LC],
    "shipper": [DocType.BILL_OF_LADING, DocType.INVOICE, DocType.LC],
    "consignee": [DocType.BILL_OF_LADING, DocType.INVOICE, DocType.LC],
}

_QTY_RE = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>M\.?T\.?S?|TONNES?|TONS?|KGS?|KILOGRAMS?)(?![A-Za-z])",
    re.I,
)
_MONEY_RE = re.compile(
    r"(?:(?P<cur1>[A-Za-z]{3})\s*(?P<num1>\d[\d,]*(?:\.\d+)?)|(?P<num2>\d[\d,]*(?:\.\d+)?)\s*(?P<cur2>[A-Za-z]{3}))(?![A-Za-z0-9])"
)
_DATE_ISO = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_DATE_DMY = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")


@dataclass
class _Hit:
    value: str
    doc_id: str
    page: int
    line: str


def _find(doc: Document, labels: list[str]) -> _Hit | None:
    """First 'Label: value' line on any page of `doc` matching one of `labels`."""
    pat = re.compile(
        r"^[ \t]*(?:" + "|".join(labels) + r")[ \t]*[:\-][ \t]*(?P<v>\S.*?)[ \t]*$", re.I | re.M
    )
    for pageno, page in enumerate(doc.text.split("\f"), start=1):
        m = pat.search(page)
        if m:
            return _Hit(value=m.group("v").strip(), doc_id=doc.doc_id, page=pageno, line=m.group(0).strip())
    return None


def _clean(v: str) -> str:
    return re.sub(r"\s+", " ", v).strip()


def _parse_num(s: str) -> float:
    return float(re.sub(r"[,\s]", "", s))


def _parse_date(s: str) -> date | None:
    try:
        m = _DATE_ISO.search(s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _DATE_DMY.search(s)
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return None


def _parse_money(s: str) -> tuple[float, str] | None:
    m = _MONEY_RE.search(s)
    if not m:
        return None
    if m.group("cur1"):
        return _parse_num(m.group("num1")), m.group("cur1").upper()
    return _parse_num(m.group("num2")), m.group("cur2").upper()


def extract(req: FinancingRequest) -> ExtractedFields:
    """Parse all documents of a request into ExtractedFields (with per-field citations)."""
    docs: dict[DocType, list[Document]] = {}
    for d in req.documents:
        docs.setdefault(d.doc_type, []).append(d)

    out = ExtractedFields(request_id=req.request_id)

    def hits(field: str) -> list[_Hit]:
        res: list[_Hit] = []
        for dt in _PRIORITY[field]:
            labels = _LABELS.get(dt, {}).get(field)
            if not labels:
                continue
            for d in docs.get(dt, []):
                h = _find(d, labels)
                if h:
                    res.append(h)
        return res

    def cite(field: str, h: _Hit) -> None:
        out.sources[field] = Citation(CitationKind.DOCUMENT, h.doc_id, page=h.page, snippet=h.line)

    for f in (
        "bl_number", "vessel", "voyage", "port_of_loading", "port_of_discharge",
        "commodity", "shipper", "consignee",
    ):
        for h in hits(f):
            setattr(out, f, _clean(h.value))
            cite(f, h)
            break

    # Warehouse receipt fallback: "Ex Vessel: <vessel> / <voyage>"
    if out.vessel is None or out.voyage is None:
        for d in docs.get(DocType.WAREHOUSE_RECEIPT, []):
            h = _find(d, _LABELS[DocType.WAREHOUSE_RECEIPT]["ex_vessel"])
            if h:
                parts = [p.strip() for p in re.split(r"\s*/\s*", _clean(h.value), maxsplit=1)]
                if out.vessel is None and parts[0]:
                    out.vessel = parts[0]
                    cite("vessel", h)
                if out.voyage is None and len(parts) > 1 and parts[1]:
                    out.voyage = parts[1]
                    cite("voyage", h)
                break

    for h in hits("quantity"):
        m = _QTY_RE.search(h.value)
        if not m:
            continue
        try:
            q, u = normalize_quantity(_parse_num(m.group("num")), m.group("unit"))
        except ValueError:
            continue
        out.quantity, out.quantity_unit = q, u
        cite("quantity", h)
        break

    for h in hits("value"):
        mv = _parse_money(h.value)
        if mv:
            out.value, out.currency = mv
            cite("value", h)
            cite("currency", h)
            break

    for h in hits("shipment_date"):
        d_ = _parse_date(h.value)
        if d_:
            out.shipment_date = d_
            cite("shipment_date", h)
            break

    missing = [
        f for f in (
            "bl_number", "vessel", "voyage", "port_of_loading", "port_of_discharge",
            "commodity", "quantity", "value", "currency", "shipment_date",
        )
        if getattr(out, f) is None
    ]
    log.info(
        "intake_extracted",
        extra={"ctx": {
            "request_id": req.request_id,
            "filled": sorted(out.sources),
            "missing": missing,
            "documents": [d.doc_id for d in req.documents],
        }},
    )
    if missing:
        log.warning("intake_missing_fields", extra={"ctx": {"request_id": req.request_id, "missing": missing}})
    return out
