"""Snowflake Cortex AISQL helpers: AI_EXTRACT (document intake) and AI_COMPLETE (narrative).

Both take an open snowflake connection (see store.snowflake.connect_from_env) and need the
SNOWFLAKE.CORTEX_USER database role. Mirrors sql/05_cortex.sql.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

from suraksha.log import get_logger
from suraksha.models import Citation, CitationKind, ExtractedFields

log = get_logger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-5"

# ExtractedFields key -> natural-language question for AI_EXTRACT (static text, never user data)
EXTRACT_QUESTIONS: dict[str, str] = {
    "bl_number": "What is the Bill of Lading number (B/L No)?",
    "vessel": "What is the name of the vessel (ocean vessel)?",
    "voyage": "What is the voyage number?",
    "port_of_loading": "What is the port of loading?",
    "port_of_discharge": "What is the port of discharge?",
    "commodity": "What is the description of the goods / commodity?",
    "quantity": "What is the numeric quantity of goods, without the unit?",
    "quantity_unit": "What is the unit of the quantity (MT, TONNES or KG)?",
    "value": "What is the total invoice or LC value, as a number without currency?",
    "currency": "What is the currency code of the value (INR, USD, ...)?",
    "shipment_date": "What is the shipped-on-board date, in YYYY-MM-DD format?",
    "shipper": "Who is the shipper / seller / beneficiary?",
    "consignee": "Who is the consignee / buyer / applicant?",
}


def _response_format_sql() -> str:
    """Static Snowflake object literal {'k': 'question', ...} (all constants from this module)."""
    items = ", ".join(
        "'" + k.replace("'", "''") + "': '" + q.replace("'", "''") + "'" for k, q in EXTRACT_QUESTIONS.items()
    )
    return "{" + items + "}"


def _split_stage_path(stage_path: str) -> tuple[str, str]:
    """'@DB.SCHEMA.STAGE/dir/file.pdf' -> ('@DB.SCHEMA.STAGE', 'dir/file.pdf')."""
    if not stage_path.startswith("@") or "/" not in stage_path:
        raise ValueError("stage_path must look like @DB.SCHEMA.STAGE/path/to/file.pdf")
    stage, rel = stage_path.split("/", 1)
    return stage, rel


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    return float(m.group(0).replace(",", "")) if m else None


def _to_date(v: Any) -> date | None:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, list):  # AI_EXTRACT may return arrays for multi-valued answers
        v = v[0] if v else None
        if v is None:
            return None
    s = str(v).strip()
    return s or None


def _run(conn: Any, sql: str, params: tuple) -> Any:
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:  # connector errors: add actionable context
        raise RuntimeError(
            f"Cortex call failed ({type(e).__name__}: {e}). Check SNOWFLAKE.CORTEX_USER grant, model/region "
            "availability (CORTEX_ENABLED_CROSS_REGION) and the stage path."
        ) from e
    finally:
        cur.close()


def _require(conn: Any) -> None:
    if conn is None:
        raise RuntimeError(
            "Cortex is not configured: pass an open Snowflake connection "
            "(suraksha.store.snowflake.connect_from_env())."
        )


def cortex_extract(conn: Any, stage_path: str, request_id: str | None = None) -> ExtractedFields:
    """Run AI_EXTRACT on a staged document and map the answer onto ExtractedFields."""
    _require(conn)
    stage, rel = _split_stage_path(stage_path)
    raw = _run(
        conn,
        f"SELECT AI_EXTRACT(file => TO_FILE(%s, %s), responseFormat => {_response_format_sql()}) AS r",
        (stage, rel),
    )
    obj = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    if not obj or obj.get("error"):
        raise RuntimeError(f"AI_EXTRACT returned an error for {stage_path}: {obj and obj.get('error')}")
    resp: dict[str, Any] = obj.get("response") or {}

    rid = request_id or PurePosixPath(rel).stem
    fields = ExtractedFields(
        request_id=rid,
        bl_number=_str(resp.get("bl_number")),
        vessel=_str(resp.get("vessel")),
        voyage=_str(resp.get("voyage")),
        port_of_loading=_str(resp.get("port_of_loading")),
        port_of_discharge=_str(resp.get("port_of_discharge")),
        commodity=_str(resp.get("commodity")),
        quantity=_to_float(resp.get("quantity")),
        quantity_unit=(_str(resp.get("quantity_unit")) or "").upper() or None,
        value=_to_float(resp.get("value")),
        currency=(_str(resp.get("currency")) or "").upper() or None,
        shipment_date=_to_date(resp.get("shipment_date")),
        shipper=_str(resp.get("shipper")),
        consignee=_str(resp.get("consignee")),
    )
    for name in EXTRACT_QUESTIONS:
        val = getattr(fields, name)
        if val is not None:
            fields.sources[name] = Citation(
                kind=CitationKind.DOCUMENT, ref=PurePosixPath(rel).name, page=None, snippet=f"AI_EXTRACT:{name}"
            )
    log.info("cortex_extract", extra={"ctx": {"stage_path": stage_path, "filled": len(fields.sources)}})
    return fields


def cortex_complete(conn: Any, prompt: str, model: str = DEFAULT_MODEL) -> str:
    """AI_COMPLETE(model, prompt) -> text. Output is a DRAFT: validate citations downstream."""
    _require(conn)
    out = _run(conn, "SELECT AI_COMPLETE(%s, %s)", (model, prompt))
    if out is None:
        raise RuntimeError("AI_COMPLETE returned no result")
    text = str(out)
    log.info("cortex_complete", extra={"ctx": {"model": model, "prompt_chars": len(prompt), "out_chars": len(text)}})
    return text
