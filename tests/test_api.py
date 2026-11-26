from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import AuditEvent
from conftest import headers

RECORD = {
    "code": "90001", "origin": "001-EXEMPLO", "destination": "002-EXEMPLO", "load": "123",
    "reason": "FALTA", "included_at": "09/01/2026 10:00:00", "received_at": "08/01/2026",
    "status": "AGU. ANALISE", "items": [
        {"code": "100", "description": "FICTÍCIO", "quantity": "1", "package": "CX-12", "total": "120.10"},
    ],
}


def test_disabled_even_with_database_url(monkeypatch):
    monkeypatch.delenv("RM_MODE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("VERCEL", "1")
    app = create_app()
    client = TestClient(app)
    assert app.state.engine is None
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code == 503
    assert client.get("/occurrences").status_code == 503
    assert client.post("/imports/preview").status_code == 503
    monkeypatch.setenv("RM_MODE", "local")
    assert not create_app().state.settings.enabled


def test_auth_scope_and_pagination(workspace):
    client, _ = workspace
    assert client.get("/ready").status_code == 200
    assert client.get("/occurrences").status_code == 401
    assert client.get("/me", headers=headers()).json()["stores"] == ["2", "3"]
    assert client.post("/imports/confirm", json=[RECORD], headers=headers("viewer")).status_code == 403
    assert client.post("/imports/confirm", json=[RECORD], headers=headers("manager")).status_code == 403
    assert client.post("/imports/confirm", json=[RECORD], headers=headers()).status_code == 200
    another = {**RECORD, "destination": "003-EXEMPLO"}
    assert client.post("/imports/confirm", json=[another], headers=headers()).status_code == 200
    assert client.get("/occurrences", headers=headers("other")).json()["records"] == []
    assert client.get("/occurrences?store=3", headers=headers("viewer")).status_code == 403
    assert len(client.get("/occurrences", headers=headers("viewer")).json()["records"]) == 1
    response = client.get("/occurrences", params={"limit": 1, "search": "100"}, headers=headers())
    assert response.status_code == 200
    page = response.json()
    assert len(page["records"]) == 1
    after = client.get("/occurrences", params={"cursor": page["next_cursor"]}, headers=headers()).json()
    assert after["next_cursor"] is None
    assert after["records"][0]["destination"] == "003-EXEMPLO"
    for date_field, size in [("included", 0), ("received", 2)]:
        response = client.get("/occurrences", params={"date_field": date_field, "to_date": "2026-01-08"}, headers=headers())
        assert response.status_code == 200
        assert len(response.json()["records"]) == size
    assert client.get("/occurrences?from_date=broken", headers=headers()).status_code == 422
    assert client.post("/imports/confirm", json=[{**RECORD, "code": "10"}], headers=headers()).status_code == 200
    ordered = client.get("/occurrences?limit=1&order=code_asc", headers=headers()).json()
    assert ordered["records"][0]["code"] == "10"
    remaining = client.get("/occurrences", params={"cursor": ordered["next_cursor"], "order": "code_asc"}, headers=headers()).json()
    assert [row["code"] for row in remaining["records"]] == ["90001", "90001"]


def test_import_update_audit_and_rollback(workspace):
    client, engine = workspace
    key = {**headers(), "Idempotency-Key": "sintetico"}
    imported = client.post("/imports/confirm", json=[RECORD], headers=key)
    assert imported.json() == {"saved": 1, "replayed": False}
    assert client.post("/imports/confirm", json=[RECORD], headers=key).json()["replayed"]
    input_data = {"status": "APROVADO", "expected_status": "AGU. ANALISE"}
    path = "/occurrences/90001/status?store=2"
    assert client.patch(path, json=input_data, headers=headers("other")).status_code == 404
    assert client.patch(path, json=input_data, headers=headers()).json()["status"] == "APROVADO"
    assert client.patch(path, json=input_data, headers=headers()).status_code == 409
    assert client.post("/imports/confirm", json=[RECORD], headers=key).json()["replayed"]
    partial = {**RECORD, "items": [], "status": "DESCONHECIDO"}
    assert client.post("/imports/confirm", json=[partial], headers=key).status_code == 409
    assert client.post("/imports/confirm", json=[partial], headers=headers()).status_code == 200
    different_source = {**RECORD, "source": "spreadsheet"}
    batch = [{**RECORD, "code": "90000"}, different_source]
    assert client.post("/imports/confirm", json=batch, headers=headers()).status_code == 409
    records = client.get("/occurrences", headers=headers()).json()["records"]
    assert len(records) == 1 and records[0]["status"] == "APROVADO"
    assert records[0]["items"][0]["total"].rstrip("0") == "120.1"
    assert client.get("/summary", headers=headers()).json()["net_reported"].rstrip("0") == "-120.1"
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(AuditEvent)) == 2


def test_concurrent_import_same_scope(workspace):
    client, engine = workspace
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _: client.post("/imports/confirm", json=[RECORD], headers={**headers(), "Idempotency-Key": "race"}),
            range(2),
        ))
    assert sorted(response.json()["replayed"] for response in results) == [False, True]
    assert len(client.get("/occurrences", headers=headers()).json()["records"]) == 1
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_inventory_preserves_packages_and_signs(workspace):
    client, _ = workspace
    records = deepcopy(RECORD)
    records["items"].append({"code": "100", "description": "OUTRO", "quantity": "0.5", "package": "UN-1", "total": "-0.02"})
    assert client.post("/imports/confirm", json=[records], headers=headers()).status_code == 200
    summary = client.get("/summary", headers=headers()).json()
    assert summary["net_reported"] is None
    assert summary["totals_by_reason"]["FALTA"] == "120.08"
    body = {"destination": "2", "items": [{"code": "100", "quantity": "-1"}]}
    response = client.post("/inventory/reconcile", json=body, headers=headers("viewer"))
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["inventory_quantity"] == "-1"
    assert {row["package"] for row in item["pending"]} == {"CX-12", "UN-1"}
    assert client.post("/inventory/reconcile", json=body, headers=headers("manager")).status_code == 403


def test_limits_and_redaction(workspace):
    client, _ = workspace
    assert client.post("/imports/preview", content=b"{}", headers={
        **headers(), "Content-Length": "4100001", "Content-Type": "application/json",
    }).status_code == 413
    response = client.post("/imports/preview", json={"text": {"private": "synthetic-input-marker"}}, headers=headers())
    assert response.status_code == 422 and "synthetic-input-marker" not in response.text
    limiter = client.app.state.limiter
    with limiter.lock:
        limiter.windows.clear()
    for _ in range(60):
        assert client.get("/me").status_code == 401
    assert client.get("/me").status_code == 429
