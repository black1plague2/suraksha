"""Static checks on sql/*.sql (no Snowflake needed)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql"
SQL_FILES = sorted(SQL_DIR.glob("*.sql"))
REPO_URL = "https://github.com/black1plague2/suraksha.git"


def split_statements(text: str) -> list[str]:
    """Split on ';' outside '...' strings, $$ blocks and -- / /* */ comments (comments dropped)."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif text.startswith("$$", i):
            j = text.find("$$", i + 2)
            assert j >= 0, "unterminated $$ block"
            buf.append(text[i:j + 2])
            i = j + 2
        elif c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and text[j + 1:j + 2] == "'":
                    j += 2
                elif text[j] == "'":
                    break
                else:
                    j += 1
            assert j < n, "unterminated string literal"
            buf.append(text[i:j + 1])
            i = j + 1
        elif c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
        else:
            buf.append(c)
            i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def test_expected_files_present():
    names = {p.name for p in SQL_FILES}
    for n in ("06_git_repo.sql", "07_load_synth_proc.sql", "08_run_pipeline_proc.sql", "09_streamlit.sql",
              "11_run_pipeline_batch.sql"):
        assert n in names


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_file_parses_into_statements(path):
    stmts = split_statements(path.read_text(encoding="utf-8"))
    assert stmts, "no statements"
    for s in stmts:
        assert re.match(r"^[A-Za-z(]", s), f"odd statement start: {s[:40]!r}"


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_no_hardcoded_secrets(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9]{20,}", text)
    assert not re.search(r"(?i)\b(password|passwd|token|secret|api_key)\s*=\s*'[^']+'", text)
    assert not re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY", text)
    assert not re.search(r"https://[^/\s:@']+:[^/\s@']+@", text), "credentials embedded in a URL"


def test_06_repo_definition():
    text = (SQL_DIR / "06_git_repo.sql").read_text(encoding="utf-8")
    assert f"ORIGIN = '{REPO_URL}'" in text
    assert "CREATE OR REPLACE GIT REPOSITORY SURAKSHA.CORE.SURAKSHA_REPO" in text
    assert "API_INTEGRATION = github_api" in text
    assert "GIT_CREDENTIALS = github_pat" in text
    assert "FETCH" in text
    # database/schema bootstrap must precede the repo object
    assert text.index("CREATE DATABASE IF NOT EXISTS SURAKSHA") < text.index("CREATE OR REPLACE GIT REPOSITORY")


def test_06_deploy_order_and_files_exist():
    raw = (SQL_DIR / "06_git_repo.sql").read_text(encoding="utf-8")
    text = chr(10).join(l for l in raw.splitlines() if not l.lstrip().startswith("--"))
    refs = re.findall(r"EXECUTE IMMEDIATE FROM @SURAKSHA\.CORE\.SURAKSHA_REPO/branches/main/(sql/[\w.]+\.sql)", text)
    assert [Path(r).name[:2] for r in refs] == ["00", "01", "02", "03", "04", "05", "07", "08", "09", "10", "11"]
    for r in refs:
        assert (ROOT / r).is_file(), r


STAGE_REF = re.compile(r"@SURAKSHA\.CORE\.SURAKSHA_REPO/branches/main/([\w./-]*)")


@pytest.mark.parametrize("name", ["07_load_synth_proc.sql", "08_run_pipeline_proc.sql", "09_streamlit.sql"])
def test_repo_paths_exist(name):
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("--"))
    for rel in STAGE_REF.findall(code):
        if not rel or rel.endswith("/"):
            assert (ROOT / rel).is_dir() if rel else True, rel
        else:
            assert (ROOT / rel).exists(), rel


def test_09_streamlit_main_file_and_env():
    text = (SQL_DIR / "09_streamlit.sql").read_text(encoding="utf-8")
    m = re.search(r"MAIN_FILE\s*=\s*'([^']+)'", text)
    assert m and (ROOT / m.group(1)).is_file()
    assert "QUERY_WAREHOUSE" in text
    shim = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    assert "app" in shim and "src" in shim


@pytest.mark.parametrize("name", ["07_load_synth_proc.sql", "08_run_pipeline_proc.sql"])
def test_proc_python_bodies_compile_and_import_real_modules(name):
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    bodies = re.findall(r"\$\$(.*?)\$\$", text, re.S)
    assert len(bodies) == 1
    compile(bodies[0], name, "exec")
    for mod in re.findall(r"from (suraksha[\w.]*) import", bodies[0]):
        rel = Path("src", *mod.split("."))
        assert (ROOT / rel).with_suffix(".py").is_file() or (ROOT / rel / "__init__.py").is_file(), mod
    assert re.search(r"HANDLER = 'run'", text) and "def run(session, seed)" in bodies[0]


def _code(name: str) -> str:
    raw = (SQL_DIR / name).read_text(encoding="utf-8")
    return "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("--"))


def test_09_warehouse_runtime_no_external_access():
    # Trial accounts reject EXTERNAL ACCESS INTEGRATION (live ITER-04 error), so the active statement must pin
    # the warehouse runtime and must not need PyPI egress; the container variant stays as a commented alternative.
    code = _code("09_streamlit.sql")
    assert "RUNTIME_NAME = 'SYSTEM$WAREHOUSE_RUNTIME'" in code
    assert "EXTERNAL_ACCESS_INTEGRATIONS" not in code
    assert "EXTERNAL ACCESS INTEGRATION" not in code
    assert "QUERY_WAREHOUSE = SURAKSHA_WH" in code
    assert "GRANT USAGE ON STREAMLIT SURAKSHA.CORE.SURAKSHA_APP TO ROLE SURAKSHA_APP" in code
    raw = (SQL_DIR / "09_streamlit.sql").read_text(encoding="utf-8")
    assert "SYSTEM$ST_CONTAINER_RUNTIME_PY3_11" in raw  # documented alternative
    assert (ROOT / "environment.yml").exists()


def test_load_synth_not_granted_to_app():
    code = _code("07_load_synth_proc.sql")
    assert not re.search(r"GRANT\s+USAGE\s+ON\s+PROCEDURE\s+SURAKSHA\.CORE\.LOAD_SYNTH[^;]*SURAKSHA_APP", code)


def test_cases_app_has_no_update_delete():
    for name in ("01_tables.sql", "10_decide_case.sql"):
        code = _code(name)
        for g in re.findall(r"GRANT\s+([^;]*?)\s+ON\s+TABLE\s+SURAKSHA\.CORE\.CASES\s+TO\s+ROLE\s+SURAKSHA_APP", code):
            assert not re.search(r"UPDATE|DELETE|ALL|OWNERSHIP", g), g
    assert re.search(r"REVOKE\s+UPDATE,\s*DELETE[^;]*ON\s+TABLE\s+SURAKSHA\.CORE\.CASES\s+FROM\s+ROLE\s+SURAKSHA_APP",
                     _code("10_decide_case.sql"))


def test_10_decide_case():
    text = (SQL_DIR / "10_decide_case.sql").read_text(encoding="utf-8")
    assert "EXECUTE AS OWNER" in text
    assert "GRANT USAGE ON PROCEDURE SURAKSHA.CORE.DECIDE_CASE(STRING, STRING, STRING, STRING) TO ROLE SURAKSHA_APP" in text
    body = re.findall(r"\$\$(.*?)\$\$", text, re.S)
    assert len(body) == 1
    compile(body[0], "10_decide_case.sql", "exec")
    for needle in ("system:", "PENDING_APPROVAL", "CASE_APPROVED", "CASE_FILED", "HOLD_RECOMMENDED",
                   "CASE_REJECTED", "FILED", "CLOSED", "sort_keys=True", 'separators=(",", ":")', "default=str"):
        assert needle in body[0], needle


def test_10_decide_case_hash_matches_approval_py():
    """Execute the proc's pure helpers and compare with agents/approval.py."""
    import sys
    from datetime import datetime

    sys.path.insert(0, str(ROOT / "src"))
    from suraksha.agents import approval

    text = (SQL_DIR / "10_decide_case.sql").read_text(encoding="utf-8")
    ns: dict = {}
    exec(compile(re.findall(r"\$\$(.*?)\$\$", text, re.S)[0], "proc", "exec"), ns)
    at = datetime(2026, 1, 2, 3, 4, 5, 678000)
    args = (7, at, "officer:Priya Nair", "CASE_APPROVED", "CASE-X", {"reason": "ok", "a": [1]})
    assert ns["_canonical"](*args) == approval._canonical(*args)
    assert ns["_hash"]("0" * 64, "abc") == approval._hash("0" * 64, "abc")
    assert ns["_officer"]("officer: Priya ") == "Priya"
    for bad in ("", "  ", "system:bot", "System:x"):
        with pytest.raises(ValueError):
            ns["_officer"](bad)


def test_11_run_pipeline_batch_static():
    name = "11_run_pipeline_batch.sql"
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    code = _code(name)
    assert "CREATE OR REPLACE PROCEDURE SURAKSHA.CORE.RUN_PIPELINE_BATCH(SEED INT, RESET BOOLEAN)" in code
    assert "EXECUTE AS OWNER" in code and "EXECUTE AS CALLER" not in code
    assert "RUNTIME_VERSION = '3.12'" in code and 'PROC_VERSION = "iter05-batch-pkgcheck"' in code
    # granted to the admin role only (reset is destructive)
    grants = re.findall(r"GRANT\s+USAGE\s+ON\s+PROCEDURE\s+SURAKSHA\.CORE\.RUN_PIPELINE_BATCH[^;]*;", code)
    assert grants and all(g.rstrip(";").endswith("TO ROLE SURAKSHA_ADMIN") for g in grants)
    assert "SURAKSHA_APP" not in " ".join(grants)
    # no USE statements inside the owner's-rights body
    body = re.findall(r"\$\$(.*?)\$\$", text, re.S)
    assert len(body) == 1
    compile(body[0], name, "exec")
    assert not re.search(r"USE\s+(ROLE|DATABASE|SCHEMA|WAREHOUSE)", body[0], re.I)
    assert re.search(r"HANDLER = 'run'", text) and "def run(session, seed, reset)" in body[0]
    # git-first loading + module purge + qmark / inline-NULL shim, same as sql/08
    for needle in ("SURAKSHA_REPO/branches/main/src/", 'paramstyle = "qmark"', "inline_nulls", "del sys.modules[m]",
                   "statements=lambda: _STMTS[0]"):
        assert needle in body[0], needle
    # destructive statements exist only inside reset_demo_tables, which is only called under `if reset is True`
    deletes = [m.start() for m in re.finditer(r"DELETE\s+FROM|TRUNCATE|DROP\s", body[0], re.I)]
    fn_start = body[0].index("def reset_demo_tables")
    fn_end = body[0].index("def run(")
    assert len(deletes) == 1 and fn_start < deletes[0] < fn_end
    run_src = body[0][fn_end:]
    assert len(re.findall(r"reset_demo_tables\(", run_src)) == 1
    assert re.search(r"if reset is True:[^\n]*\n\s+reset_demo_tables\(conn\)", run_src)
    for t in ("BANK_A.REQUESTS", "BANK_B.REQUESTS", "BANK_C.REQUESTS", "REQUEST_FIELDS", "REPORTS", "CASES",
              "PIPELINE_RESULTS", "INVESTIGATION_FACTS", "CONSORTIUM.LEDGER", "AUDIT_LOG"):
        assert t in body[0], t
    assert "REGISTRY" not in body[0] and "TRANSACTIONS" not in body[0]  # registry / txns never reset
    for mod in re.findall(r"from (suraksha[\w.]*) import", body[0]):
        rel = Path("src", *mod.split("."))
        assert (ROOT / rel).with_suffix(".py").is_file() or (ROOT / rel / "__init__.py").is_file(), mod
    assert STAGE_REF.findall(code) and all((ROOT / r).exists() for r in STAGE_REF.findall(code) if r and not r.endswith("/"))


@pytest.mark.parametrize("fname", ["07_load_synth_proc.sql", "08_run_pipeline_proc.sql", "11_run_pipeline_batch.sql"])
def test_ensure_pkg_skips_stale_sources_and_reports_them(fname, tmp_path):
    # Live ITER-05: RUN_PIPELINE_BATCH silently loaded a stale package copy without store/batch.py.
    import re as _re
    src = (SQL_DIR / fname).read_text(encoding="utf-8")
    body = _re.findall(r"\$\$(.*?)\$\$", src, _re.S)[0]
    ns: dict = {}
    exec(compile(body, fname, "exec"), ns)
    ns["DST"] = str(tmp_path / "pkg")
    git, code = ns["STAGES"]
    full = ["src/suraksha/__init__.py"] + ["src/" + m for m in ns["REQUIRED_MODULES"]]

    class Row(dict):
        pass

    class Sess:
        def __init__(self, listing):
            self.listing = listing
            self.got = []
            self.file = self

        def sql(self, q):
            base = q[len("LIST "):-len("suraksha/")]
            names = self.listing.get(base)
            if isinstance(names, Exception):
                raise names
            return type("R", (), {"collect": lambda _s: [Row(name="stage/x/" + n) for n in names]})()

        def get(self, path, tgt):
            self.got.append(path)

    # git source fails (e.g. LIST under owner's rights), fallback copy current -> uses fallback
    s = Sess({git: RuntimeError("LIST not allowed"), code: full})
    assert ns["ensure_pkg"](s).startswith(code)
    # git source fails, fallback stale -> loud error naming both sources, nothing downloaded
    s = Sess({git: RuntimeError("LIST not allowed"), code: ["src/suraksha/__init__.py"]})
    with pytest.raises(RuntimeError) as e:
        ns["ensure_pkg"](s)
    assert "missing" in str(e.value) and "LIST not allowed" in str(e.value) and not s.got
