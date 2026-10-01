"""Snowflake-backed Store (production / hackathon demo).

Implements the `suraksha.store.base.Store` protocol over the tables created by sql/*.sql.

Rules followed here:
  * every VALUE reaches Snowflake as a bind parameter (%s, connector pyformat style); SQL text
    only ever contains constants, whitelisted identifiers (bank schemas, procedure names) and
    generated placeholder lists
  * VARIANT/ARRAY columns are written with PARSE_JSON(%s) inside INSERT ... SELECT / MERGE
  * the audit path is INSERT + SELECT only; this module never issues UPDATE/DELETE on AUDIT_LOG
  * `snowflake.connector` is imported lazily so the package imports without it installed

Connection (see `connect_from_env`): SNOWFLAKE_CONNECTION_NAME (entry in ~/.snowflake/connections.toml)
or SNOWFLAKE_ACCOUNT + SNOWFLAKE_USER + (SNOWFLAKE_PASSWORD | SNOWFLAKE_PRIVATE_KEY_PATH), plus
optional SNOWFLAKE_ROLE / SNOWFLAKE_WAREHOUSE / SNOWFLAKE_DATABASE.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import Any

from suraksha.log import get_logger
from suraksha.models import (
    AuditRecord,
    Case,
    CaseStatus,
    Citation,
    CitationKind,
    ConsortiumEntry,
    DocType,
    Document,
    ExtractedFields,
    FinancingRequest,
    ReportSection,
    ReportSentence,
    STRDraft,
    to_dict,
)

log = get_logger(__name__)

DB = "SURAKSHA"
BANKS = ("BANK_A", "BANK_B", "BANK_C")

# identifiers below are constants / whitelisted, never derived from user data
T_COMPANIES = f"{DB}.REGISTRY.COMPANIES"
T_PERSONS = f"{DB}.REGISTRY.PERSONS"
T_ROLES = f"{DB}.REGISTRY.ROLES"
T_CORP_OWNERS = f"{DB}.REGISTRY.CORP_OWNERS"
T_ADDRESSES = f"{DB}.REGISTRY.ADDRESSES"
T_POLICY = f"{DB}.CORE.POLICY_CLAUSES"
T_FIELDS = f"{DB}.CORE.REQUEST_FIELDS"
T_REPORTS = f"{DB}.CORE.REPORTS"
T_CASES = f"{DB}.CORE.CASES"
T_AUDIT = f"{DB}.CORE.AUDIT_LOG"
V_LEDGER = f"{DB}.CONSORTIUM.V_SHARED_LEDGER"
PLEDGE_PROCS = {b: f"{DB}.CONSORTIUM.SP_PLEDGE_{b}" for b in BANKS}

_COMPANY_COLS = "company_id, name, reg_no, address_id, phone, incorporated, country"
_TXN_COLS = "txn_id, company_id, bank_id, txn_date, amount, currency, counterparty, txn_type"
_ENTRY_COLS = "entry_id, bank_id, keys, qty_band, borrower_token, pledged_at"
_CASE_COLS = "case_id, request_id, report_id, status, hold_recommended, decided_by, decided_at, reason"
_AUDIT_COLS = "seq, at, actor, action, subject_id, payload, prev_hash, entry_hash"


# ----------------------------------------------------------------------------- connection
def connect_from_env(connection_name: str | None = None) -> Any:
    """Open a snowflake.connector connection from env vars or a named connection."""
    try:
        import snowflake.connector  # type: ignore
    except ImportError as e:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "snowflake-connector-python is not installed. `pip install snowflake-connector-python` "
            "(and `cryptography` for key-pair auth)."
        ) from e

    name = connection_name or os.getenv("SNOWFLAKE_CONNECTION_NAME")
    if name:
        log.info("snowflake_connect", extra={"ctx": {"mode": "named", "connection_name": name}})
        return snowflake.connector.connect(connection_name=name)

    account, user = os.getenv("SNOWFLAKE_ACCOUNT"), os.getenv("SNOWFLAKE_USER")
    if not account or not user:
        raise RuntimeError(
            "Snowflake not configured: set SNOWFLAKE_CONNECTION_NAME, or SNOWFLAKE_ACCOUNT + "
            "SNOWFLAKE_USER + (SNOWFLAKE_PASSWORD | SNOWFLAKE_PRIVATE_KEY_PATH)."
        )
    kwargs: dict[str, Any] = {
        "account": account,
        "user": user,
        "role": os.getenv("SNOWFLAKE_ROLE", "SURAKSHA_APP"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "SURAKSHA_WH"),
        "database": os.getenv("SNOWFLAKE_DATABASE", DB),
    }
    key_path = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
    if key_path:
        kwargs["private_key"] = _load_private_key(key_path)
    elif os.getenv("SNOWFLAKE_PASSWORD"):
        kwargs["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    else:
        raise RuntimeError("Set SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY_PATH.")
    log.info("snowflake_connect", extra={"ctx": {"mode": "env", "account": account, "role": kwargs["role"]}})
    return snowflake.connector.connect(**kwargs)


def _load_private_key(path: str) -> bytes:
    from cryptography.hazmat.primitives import serialization  # type: ignore

    with open(path, "rb") as f:
        key = serialization.load_pem_private_key(
            f.read(), password=os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "").encode() or None
        )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


# ----------------------------------------------------------------------------- helpers
def _ts(dt: datetime | None) -> str | None:
    """datetime -> ISO string for TO_TIMESTAMP_NTZ(%s). Aware datetimes are normalised to naive UTC."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(sep=" ")


def _d(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _json(obj: Any) -> str:
    return json.dumps(obj, default=str, sort_keys=True)


def _variant(v: Any) -> Any:
    """VARIANT/ARRAY cells come back from the connector as JSON text."""
    if isinstance(v, (str, bytes)):
        return json.loads(v)
    return v


def _bank_schema(bank_id: str) -> str:
    if bank_id not in BANKS:
        raise ValueError(f"unknown bank_id {bank_id!r}; expected one of {BANKS}")
    return f"{DB}.{bank_id}"


def _placeholders(n: int) -> str:
    return ", ".join(["%s"] * n)


def _entry(row: dict[str, Any]) -> ConsortiumEntry:
    return ConsortiumEntry(
        entry_id=row["entry_id"],
        bank_id=row["bank_id"],
        keys=dict(_variant(row["keys"]) or {}),
        qty_band=int(row["qty_band"]) if row["qty_band"] is not None else None,
        borrower_token=row["borrower_token"],
        pledged_at=row["pledged_at"],
    )


def _case(row: dict[str, Any]) -> Case:
    return Case(
        case_id=row["case_id"],
        request_id=row["request_id"],
        report_id=row["report_id"],
        status=CaseStatus(row["status"]),
        hold_recommended=bool(row["hold_recommended"]),
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        reason=row["reason"],
    )


def _citation_from_dict(d: dict[str, Any]) -> Citation:
    return Citation(kind=CitationKind(d["kind"]), ref=d["ref"], page=d.get("page"), snippet=d.get("snippet"))


class SnowflakeStore:
    """Store implementation over Snowflake. Pass an open connection, or let it connect from env."""

    def __init__(self, conn: Any | None = None, connection_name: str | None = None) -> None:
        self._conn = conn if conn is not None else connect_from_env(connection_name)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ low level
    def _exec(self, sql: str, params: tuple | list | None = None) -> None:
        log.debug("sf_exec", extra={"ctx": {"sql": sql.split("\n", 1)[0][:80], "n_params": len(params or ())}})
        cur = self._conn.cursor()
        try:
            cur.execute(sql, tuple(params) if params else None)
        finally:
            cur.close()

    def _query(self, sql: str, params: tuple | list | None = None) -> list[dict[str, Any]]:
        log.debug("sf_query", extra={"ctx": {"sql": sql.split("\n", 1)[0][:80], "n_params": len(params or ())}})
        cur = self._conn.cursor()
        try:
            cur.execute(sql, tuple(params) if params else None)
            cols = [d[0].lower() for d in cur.description or []]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()

    def _one(self, sql: str, params: tuple | list | None = None) -> dict[str, Any] | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------ consortium
    def add_consortium_entry(self, entry: ConsortiumEntry) -> None:
        proc = PLEDGE_PROCS.get(entry.bank_id)
        if proc is None:
            raise ValueError(f"unknown bank_id {entry.bank_id!r}; expected one of {BANKS}")
        # bank id is fixed inside the procedure: a bank role cannot write another bank's rows
        self._exec(
            f"CALL {proc}(%s, %s, %s, %s, TO_TIMESTAMP_NTZ(%s))",
            (entry.entry_id, _json(entry.keys), entry.qty_band, entry.borrower_token, _ts(entry.pledged_at)),
        )
        log.info("consortium_pledge", extra={"ctx": {"entry_id": entry.entry_id, "bank_id": entry.bank_id}})

    def find_consortium_by_keys(self, keys: dict[str, str], exclude_bank: str) -> list[ConsortiumEntry]:
        if not keys:
            return []
        cond = " OR ".join(["GET(keys, %s)::STRING = %s"] * len(keys))
        params: list[Any] = [exclude_bank]
        for k, v in keys.items():
            params += [k, v]
        rows = self._query(
            f"SELECT {_ENTRY_COLS} FROM {V_LEDGER} WHERE bank_id <> %s AND ({cond}) ORDER BY pledged_at, entry_id",
            params,
        )
        return [_entry(r) for r in rows]

    def find_consortium_by_band(self, key_name: str, values: list[str], exclude_bank: str) -> list[ConsortiumEntry]:
        if not values:
            return []
        rows = self._query(
            f"SELECT {_ENTRY_COLS} FROM {V_LEDGER} WHERE bank_id <> %s AND GET(keys, %s)::STRING IN "
            f"({_placeholders(len(values))}) ORDER BY pledged_at, entry_id",
            [exclude_bank, key_name, *values],
        )
        return [_entry(r) for r in rows]

    def list_consortium(self) -> list[ConsortiumEntry]:
        return [_entry(r) for r in self._query(f"SELECT {_ENTRY_COLS} FROM {V_LEDGER} ORDER BY pledged_at, entry_id")]

    # ------------------------------------------------------------------ registry
    def get_company(self, company_id: str) -> dict[str, Any] | None:
        return self._one(f"SELECT {_COMPANY_COLS} FROM {T_COMPANIES} WHERE company_id = %s", (company_id,))

    def list_companies(self) -> list[dict[str, Any]]:
        return self._query(f"SELECT {_COMPANY_COLS} FROM {T_COMPANIES} ORDER BY company_id")

    def get_person(self, person_id: str) -> dict[str, Any] | None:
        return self._one(f"SELECT person_id, name, id_hash FROM {T_PERSONS} WHERE person_id = %s", (person_id,))

    def roles_for_company(self, company_id: str) -> list[dict[str, Any]]:
        return self._query(
            f"SELECT company_id, person_id, role, pct FROM {T_ROLES} WHERE company_id = %s ORDER BY person_id, role",
            (company_id,),
        )

    def roles_for_person(self, person_id: str) -> list[dict[str, Any]]:
        return self._query(
            f"SELECT company_id, person_id, role, pct FROM {T_ROLES} WHERE person_id = %s ORDER BY company_id, role",
            (person_id,),
        )

    def corp_owners_of(self, company_id: str) -> list[dict[str, Any]]:
        return self._query(
            f"SELECT owner_company_id, owned_company_id, pct FROM {T_CORP_OWNERS} WHERE owned_company_id = %s",
            (company_id,),
        )

    def corp_owned_by(self, company_id: str) -> list[dict[str, Any]]:
        return self._query(
            f"SELECT owner_company_id, owned_company_id, pct FROM {T_CORP_OWNERS} WHERE owner_company_id = %s",
            (company_id,),
        )

    def get_address(self, address_id: str) -> dict[str, Any] | None:
        return self._one(f"SELECT address_id, line, city, country FROM {T_ADDRESSES} WHERE address_id = %s", (address_id,))

    # ------------------------------------------------------------------ bank private
    def transactions_for(self, company_id: str, bank_id: str | None = None) -> list[dict[str, Any]]:
        if bank_id is not None:
            if bank_id not in BANKS:
                return []
            return self._query(
                f"SELECT {_TXN_COLS} FROM {_bank_schema(bank_id)}.TRANSACTIONS WHERE company_id = %s "
                "ORDER BY txn_date, txn_id",
                (company_id,),
            )
        union = " UNION ALL ".join(
            f"SELECT {_TXN_COLS} FROM {_bank_schema(b)}.TRANSACTIONS WHERE company_id = %s" for b in BANKS
        )
        return self._query(f"{union} ORDER BY txn_date, txn_id", [company_id] * len(BANKS))

    def policy_clauses(self, tags: list[str] | None = None) -> list[dict[str, Any]]:
        cols = "clause_id, section, title, text, tags"
        if not tags:
            rows = self._query(f"SELECT {cols} FROM {T_POLICY} ORDER BY clause_id")
        else:
            rows = self._query(
                f"SELECT {cols} FROM {T_POLICY} WHERE ARRAYS_OVERLAP(tags, PARSE_JSON(%s)::ARRAY) ORDER BY clause_id",
                (json.dumps(list(tags)),),
            )
        for r in rows:
            r["tags"] = list(_variant(r["tags"]) or [])
        return rows

    # ------------------------------------------------------------------ workflow
    def save_request(self, req: FinancingRequest) -> None:
        table = f"{_bank_schema(req.bank_id)}.REQUESTS"
        docs = [
            {"doc_id": d.doc_id, "request_id": d.request_id, "doc_type": d.doc_type.value, "text": d.text, "pages": d.pages}
            for d in req.documents
        ]
        self._exec(
            f"MERGE INTO {table} t USING (SELECT %s AS request_id, %s AS bank_id, %s AS borrower_id, "
            "%s AS amount, %s AS currency, TO_TIMESTAMP_NTZ(%s) AS submitted_at, PARSE_JSON(%s) AS documents) s "
            "ON t.request_id = s.request_id "
            "WHEN MATCHED THEN UPDATE SET bank_id = s.bank_id, borrower_id = s.borrower_id, amount = s.amount, "
            "currency = s.currency, submitted_at = s.submitted_at, documents = s.documents "
            "WHEN NOT MATCHED THEN INSERT (request_id, bank_id, borrower_id, amount, currency, submitted_at, documents) "
            "VALUES (s.request_id, s.bank_id, s.borrower_id, s.amount, s.currency, s.submitted_at, s.documents)",
            (req.request_id, req.bank_id, req.borrower_id, req.amount, req.currency, _ts(req.submitted_at), _json(docs)),
        )

    def get_request(self, request_id: str) -> FinancingRequest | None:
        cols = "request_id, bank_id, borrower_id, amount, currency, submitted_at, documents"
        union = " UNION ALL ".join(
            f"SELECT {cols} FROM {_bank_schema(b)}.REQUESTS WHERE request_id = %s" for b in BANKS
        )
        row = self._one(f"{union} LIMIT 1", [request_id] * len(BANKS))
        if row is None:
            return None
        docs = [
            Document(
                doc_id=d["doc_id"],
                request_id=d["request_id"],
                doc_type=DocType(d["doc_type"]),
                text=d["text"],
                pages=int(d.get("pages", 1)),
            )
            for d in (_variant(row["documents"]) or [])
        ]
        return FinancingRequest(
            request_id=row["request_id"],
            bank_id=row["bank_id"],
            borrower_id=row["borrower_id"],
            amount=row["amount"],
            currency=row["currency"],
            submitted_at=row["submitted_at"],
            documents=docs,
        )

    def save_fields(self, fields: ExtractedFields) -> None:
        sources = {
            k: {"kind": c.kind.value, "ref": c.ref, "page": c.page, "snippet": c.snippet}
            for k, c in fields.sources.items()
        }
        self._exec(
            f"MERGE INTO {T_FIELDS} t USING (SELECT %s AS request_id, %s AS bl_number, %s AS vessel, %s AS voyage, "
            "%s AS port_of_loading, %s AS port_of_discharge, %s AS commodity, %s AS quantity, %s AS quantity_unit, "
            "%s AS value, %s AS currency, TO_DATE(%s) AS shipment_date, %s AS shipper, %s AS consignee, "
            "PARSE_JSON(%s) AS sources) s ON t.request_id = s.request_id "
            "WHEN MATCHED THEN UPDATE SET bl_number = s.bl_number, vessel = s.vessel, voyage = s.voyage, "
            "port_of_loading = s.port_of_loading, port_of_discharge = s.port_of_discharge, commodity = s.commodity, "
            "quantity = s.quantity, quantity_unit = s.quantity_unit, value = s.value, currency = s.currency, "
            "shipment_date = s.shipment_date, shipper = s.shipper, consignee = s.consignee, sources = s.sources "
            "WHEN NOT MATCHED THEN INSERT (request_id, bl_number, vessel, voyage, port_of_loading, port_of_discharge, "
            "commodity, quantity, quantity_unit, value, currency, shipment_date, shipper, consignee, sources) "
            "VALUES (s.request_id, s.bl_number, s.vessel, s.voyage, s.port_of_loading, s.port_of_discharge, "
            "s.commodity, s.quantity, s.quantity_unit, s.value, s.currency, s.shipment_date, s.shipper, s.consignee, "
            "s.sources)",
            (
                fields.request_id, fields.bl_number, fields.vessel, fields.voyage, fields.port_of_loading,
                fields.port_of_discharge, fields.commodity, fields.quantity, fields.quantity_unit, fields.value,
                fields.currency, _d(fields.shipment_date), fields.shipper, fields.consignee, _json(sources),
            ),
        )

    def get_fields(self, request_id: str) -> ExtractedFields | None:
        """Extra (not in the Store protocol): read back saved extraction."""
        row = self._one(
            "SELECT request_id, bl_number, vessel, voyage, port_of_loading, port_of_discharge, commodity, quantity, "
            f"quantity_unit, value, currency, shipment_date, shipper, consignee, sources FROM {T_FIELDS} "
            "WHERE request_id = %s",
            (request_id,),
        )
        if row is None:
            return None
        src = {k: _citation_from_dict(v) for k, v in (_variant(row.pop("sources")) or {}).items()}
        return ExtractedFields(sources=src, **row)

    def save_report(self, report: STRDraft) -> None:
        sections = to_dict(report)["sections"]
        self._exec(
            f"MERGE INTO {T_REPORTS} t USING (SELECT %s AS report_id, %s AS request_id, %s AS reporting_bank_id, "
            "TO_TIMESTAMP_NTZ(%s) AS created_at, %s AS recommended_action, PARSE_JSON(%s) AS sections) s "
            "ON t.report_id = s.report_id "
            "WHEN MATCHED THEN UPDATE SET request_id = s.request_id, reporting_bank_id = s.reporting_bank_id, "
            "created_at = s.created_at, recommended_action = s.recommended_action, sections = s.sections "
            "WHEN NOT MATCHED THEN INSERT (report_id, request_id, reporting_bank_id, created_at, recommended_action, "
            "sections) VALUES (s.report_id, s.request_id, s.reporting_bank_id, s.created_at, s.recommended_action, "
            "s.sections)",
            (report.report_id, report.request_id, report.reporting_bank_id, _ts(report.created_at),
             report.recommended_action, _json(sections)),
        )

    def get_report(self, report_id: str) -> STRDraft | None:
        row = self._one(
            "SELECT report_id, request_id, reporting_bank_id, created_at, recommended_action, sections "
            f"FROM {T_REPORTS} WHERE report_id = %s",
            (report_id,),
        )
        if row is None:
            return None
        sections = [
            ReportSection(
                part=s["part"],
                title=s["title"],
                sentences=[
                    ReportSentence(text=x["text"], citations=[_citation_from_dict(c) for c in x["citations"]])
                    for x in s["sentences"]
                ],
            )
            for s in (_variant(row["sections"]) or [])
        ]
        return STRDraft(
            report_id=row["report_id"],
            request_id=row["request_id"],
            reporting_bank_id=row["reporting_bank_id"],
            created_at=row["created_at"],
            sections=sections,
            recommended_action=row["recommended_action"],
        )

    def save_case(self, case: Case) -> None:
        self._exec(
            f"MERGE INTO {T_CASES} t USING (SELECT %s AS case_id, %s AS request_id, %s AS report_id, %s AS status, "
            "%s AS hold_recommended, %s AS decided_by, TO_TIMESTAMP_NTZ(%s) AS decided_at, %s AS reason) s "
            "ON t.case_id = s.case_id "
            "WHEN MATCHED THEN UPDATE SET request_id = s.request_id, report_id = s.report_id, status = s.status, "
            "hold_recommended = s.hold_recommended, decided_by = s.decided_by, decided_at = s.decided_at, "
            "reason = s.reason "
            "WHEN NOT MATCHED THEN INSERT (case_id, request_id, report_id, status, hold_recommended, decided_by, "
            "decided_at, reason) VALUES (s.case_id, s.request_id, s.report_id, s.status, s.hold_recommended, "
            "s.decided_by, s.decided_at, s.reason)",
            (case.case_id, case.request_id, case.report_id, case.status.value, case.hold_recommended,
             case.decided_by, _ts(case.decided_at), case.reason),
        )

    def get_case(self, case_id: str) -> Case | None:
        row = self._one(f"SELECT {_CASE_COLS} FROM {T_CASES} WHERE case_id = %s", (case_id,))
        return _case(row) if row else None

    def list_cases(self) -> list[Case]:
        return [_case(r) for r in self._query(f"SELECT {_CASE_COLS} FROM {T_CASES} ORDER BY case_id")]

    # ------------------------------------------------------------------ audit (INSERT + SELECT only)
    def append_audit(self, record: AuditRecord) -> None:
        last = self.last_audit()
        if last is not None and record.seq != last.seq + 1:
            raise ValueError("audit seq must be contiguous")
        self._exec(
            f"INSERT INTO {T_AUDIT} (seq, at, actor, action, subject_id, payload, prev_hash, entry_hash) "
            "SELECT %s, TO_TIMESTAMP_NTZ(%s), %s, %s, %s, PARSE_JSON(%s), %s, %s",
            (record.seq, _ts(record.at), record.actor, record.action, record.subject_id,
             _json(record.payload), record.prev_hash, record.entry_hash),
        )

    @staticmethod
    def _audit(row: dict[str, Any]) -> AuditRecord:
        return AuditRecord(
            seq=int(row["seq"]),
            at=row["at"],
            actor=row["actor"],
            action=row["action"],
            subject_id=row["subject_id"],
            payload=dict(_variant(row["payload"]) or {}),
            prev_hash=row["prev_hash"],
            entry_hash=row["entry_hash"],
        )

    def list_audit(self, subject_id: str | None = None) -> list[AuditRecord]:
        if subject_id is None:
            rows = self._query(f"SELECT {_AUDIT_COLS} FROM {T_AUDIT} ORDER BY seq")
        else:
            rows = self._query(f"SELECT {_AUDIT_COLS} FROM {T_AUDIT} WHERE subject_id = %s ORDER BY seq", (subject_id,))
        return [self._audit(r) for r in rows]

    def last_audit(self) -> AuditRecord | None:
        row = self._one(f"SELECT {_AUDIT_COLS} FROM {T_AUDIT} ORDER BY seq DESC LIMIT 1")
        return self._audit(row) if row else None

    # ------------------------------------------------------------------ bulk registry load
    def load_registry(
        self,
        companies: list[dict[str, Any]],
        persons: list[dict[str, Any]],
        roles: list[dict[str, Any]],
        corp_owners: list[dict[str, Any]],
        addresses: list[dict[str, Any]],
        transactions: list[dict[str, Any]],
        policy_clauses: list[dict[str, Any]],
    ) -> None:
        """Same signature as MemoryStore.load_registry (used by synth.load_into). Uses executemany."""
        def many(sql: str, rows: list[tuple]) -> None:
            if not rows:
                return
            cur = self._conn.cursor()
            try:
                cur.executemany(sql, rows)
            finally:
                cur.close()

        many(f"INSERT INTO {T_ADDRESSES} (address_id, line, city, country) VALUES (%s, %s, %s, %s)",
             [(a["address_id"], a.get("line"), a.get("city"), a.get("country")) for a in addresses])
        many(f"INSERT INTO {T_COMPANIES} ({_COMPANY_COLS}) VALUES (%s, %s, %s, %s, %s, %s, %s)",
             [(c["company_id"], c.get("name"), c.get("reg_no"), c.get("address_id"), c.get("phone"),
               _d(c.get("incorporated")) if isinstance(c.get("incorporated"), date) else c.get("incorporated"),
               c.get("country")) for c in companies])
        many(f"INSERT INTO {T_PERSONS} (person_id, name, id_hash) VALUES (%s, %s, %s)",
             [(p["person_id"], p.get("name"), p.get("id_hash")) for p in persons])
        many(f"INSERT INTO {T_ROLES} (company_id, person_id, role, pct) VALUES (%s, %s, %s, %s)",
             [(r["company_id"], r["person_id"], r["role"], r.get("pct")) for r in roles])
        many(f"INSERT INTO {T_CORP_OWNERS} (owner_company_id, owned_company_id, pct) VALUES (%s, %s, %s)",
             [(o["owner_company_id"], o["owned_company_id"], o.get("pct")) for o in corp_owners])
        for bank in BANKS:
            many(
                f"INSERT INTO {_bank_schema(bank)}.TRANSACTIONS ({_TXN_COLS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [(t["txn_id"], t["company_id"], t["bank_id"],
                  _d(t["txn_date"]) if isinstance(t.get("txn_date"), date) else t.get("txn_date"),
                  t.get("amount"), t.get("currency"), t.get("counterparty"), t.get("txn_type"))
                 for t in transactions if t["bank_id"] == bank],
            )
        # ARRAY column: INSERT ... SELECT with PARSE_JSON (VALUES cannot call functions)
        many(
            f"INSERT INTO {T_POLICY} (clause_id, section, title, text, tags) "
            "SELECT %s, %s, %s, %s, PARSE_JSON(%s)",
            [(c["clause_id"], c.get("section"), c.get("title"), c.get("text"), json.dumps(list(c.get("tags", []))))
             for c in policy_clauses],
        )
        log.info("registry_loaded", extra={"ctx": {"companies": len(companies), "transactions": len(transactions)}})
