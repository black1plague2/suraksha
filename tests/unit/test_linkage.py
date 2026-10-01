from suraksha.agents.linkage import answer, linked_entities
from suraksha.store.memory import MemoryStore


def make_store():
    st = MemoryStore()
    spec = [("Alpha Steel Pvt Ltd", "A1"), ("Beta Metals Pvt Ltd", "A2"),
            ("Holdco Pvt Ltd", "A3"), ("Island Ltd", "A4")]
    comps = [{"company_id": f"C000{i}", "name": n, "reg_no": f"R{i}", "address_id": a, "phone": f"+91-{i}",
              "incorporated": None, "country": "IN"} for i, (n, a) in enumerate(spec, start=1)]
    st.load_registry(
        companies=comps,
        persons=[{"person_id": "P1", "name": "Ravi Shah", "id_hash": "x"}],
        roles=[{"company_id": "C0001", "person_id": "P1", "role": "DIRECTOR", "pct": None},
               {"company_id": "C0002", "person_id": "P1", "role": "DIRECTOR", "pct": None}],
        corp_owners=[{"owner_company_id": "C0003", "owned_company_id": "C0001", "pct": 51.0}],
        addresses=[], transactions=[], policy_clauses=[])
    return st


def test_linked_entities():
    rows = linked_entities("C0001", make_store(), hops=2)
    ids = [r["company_id"] for r in rows]
    assert set(ids) == {"C0002", "C0003"}
    assert all(r["via"] and r["via"][0].citation for r in rows)


def test_answer_linked():
    r = answer("Who else is linked to Alpha Steel?", make_store())
    assert r["intent"] == "linked_entities"
    assert "Beta Metals" in r["answer_text"] and r["citations"]


def test_answer_path_by_id_and_name():
    r = answer("What is the path between C0003 and beta metals?", make_store())
    assert r["intent"] == "path" and r["rows"] and r["citations"]
    assert "hop" in r["answer_text"]


def test_answer_path_none():
    r = answer("link between Alpha Steel and Island Ltd", make_store())
    assert r["intent"] == "path" and r["rows"] == [] and "No link" in r["answer_text"]


def test_answer_roles():
    r = answer("Who are the directors of Alpha Steel Pvt Ltd?", make_store())
    assert r["intent"] == "roles"
    assert "Ravi Shah" in r["answer_text"] and "Holdco" in r["answer_text"] and r["citations"]


def test_answer_unknown():
    r = answer("what is the weather", make_store())
    assert r["intent"] == "unknown" and r["answer_text"]
