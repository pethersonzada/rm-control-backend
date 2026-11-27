from dataclasses import replace
from io import BytesIO

from openpyxl import load_workbook

from app import security
from app.services.memberships import bootstrap
from conftest import headers

PROJECT = "rm-control-webapp"


def test_native_tickets_export_and_scope(workspace):
    client, _ = workspace
    data = {"destination": "2", "origin": "CD fictício", "reason": "FALTA", "received_at": "2026-11-25",
            "external_code": "000123", "items": [
                {"code": "000100", "description": "=1+1", "quantity": "1.5", "package": "CX", "total": "12.34"},
                {"code": "000101", "description": "Produto fictício", "quantity": "2", "package": "UN", "total": None},
            ], "invoices": [{"number": "00042", "series": "1"}]}
    key = {**headers(), "Idempotency-Key": "ticket-test"}
    assert client.post("/tickets", headers=headers("viewer"), json=data).status_code == 403
    assert client.post("/tickets", headers=key, json={**data, "destination": "9"}).status_code == 403
    response = client.post("/tickets", headers=key, json=data)
    assert response.status_code == 200, response.text
    ticket = response.json()
    assert ticket["external_code"] == "000123" and ticket["revision"] == 1
    assert client.post("/tickets", headers=key, json=data).json()["code"] == ticket["code"]
    assert client.post("/tickets", headers=key, json={**data, "reason": "SOBRA"}).status_code == 409
    assert client.post("/tickets", headers={**headers(), "Idempotency-Key": "other-key"}, json=data).status_code == 409
    assert client.get("/occurrences", headers=headers("other")).json()["records"] == []
    path = "/tickets/" + ticket["code"]
    edit = {**data, "revision": 1, "reason": "AVARIA"}
    assert client.put(path, headers=headers(), json=edit).json()["revision"] == 2
    assert client.put(path, headers=headers(), json=edit).status_code == 409
    rows = client.get("/occurrences", headers=headers(), params={"search": "000123"}).json()["records"]
    assert len(rows) == 1 and rows[0]["reason"] == "AVARIA"
    history = client.get(f"/occurrences/{ticket['code']}/history?store=2", headers=headers()).json()
    assert [e["action"] for e in history["events"]] == ["ticket.edit", "ticket.create"]
    assert "reason" in history["events"][0]["details"]["fields"]
    assert client.get(f"/occurrences/{ticket['code']}/history?store=2", headers=headers("other")).status_code == 404
    report = client.get("/reports/chamados.xlsx", headers=headers(), params={"store": "2"})
    assert report.status_code == 200
    book = load_workbook(BytesIO(report.content), read_only=True)
    try:
        rows = list(book.active.iter_rows())
        assert len(rows) == 3
        assert rows[1][3].value == "000100"
        assert rows[1][4].value == "=1+1" and rows[1][4].data_type == "s"
        assert rows[1][9].value == 12.34 and rows[2][9].value is None
        assert rows[1][10].value == "000123"
    finally:
        book.close()
    assert client.get("/reports/chamados.xlsx", headers=headers("viewer"), params={"store": "3"}).status_code == 403


def test_stores_and_access_queue(workspace, monkeypatch):
    client, engine = workspace
    client.app.state.settings = replace(client.app.state.settings, firebase_project_id=PROJECT)
    monkeypatch.setattr(security, "verify_uid", lambda token, project: token.split(".")[1])
    def auth(uid):
        return {"Authorization": f"Bearer x.{uid}.x"}
    bootstrap(engine, PROJECT, "alpha", "owner", {"2"})
    owner = auth("owner")
    pending = auth("pending-user")
    assert client.get("/me", headers=pending).status_code == 403
    assert client.post("/access/request", headers=pending).json()["status"] == "pending"
    assert client.post("/access/request", headers=pending).json()["status"] == "pending"
    assert len(client.get("/admin/access-requests", headers=owner).json()) == 1
    store = {"code": "007", "name": "Loja sintética", "active": True}
    assert client.put("/admin/stores", headers=pending, json=store).status_code == 403
    assert client.put("/admin/stores", headers=owner, json=store).json()["code"] == "7"
    assert "7" in client.get("/me", headers=owner).json()["stores"]
    member = {"uid": "pending-user", "role": "importer", "stores": ["7"], "active": True}
    assert client.put("/admin/users", headers=owner, json=member).status_code == 200
    assert client.get("/admin/access-requests", headers=owner).json() == []
    assert client.get("/me", headers=pending).json()["stores"] == ["7"]
    assert client.put("/admin/stores", headers=pending, json=store).status_code == 403
    assert client.put("/admin/stores", headers=owner, json={**store, "active": False}).status_code == 200
    assert client.get("/me", headers=pending).status_code == 403
    assert "7" not in client.get("/me", headers=owner).json()["stores"]
    assert client.put("/admin/stores", headers=owner, json=store).status_code == 200
    assert client.get("/me", headers=pending).status_code == 200
    rejected = auth("another-user")
    assert client.post("/access/request", headers=rejected).status_code == 200
    assert client.post("/admin/access-requests/another-user/reject", headers=owner).status_code == 200
    assert client.post("/access/request", headers=rejected).json()["status"] == "rejected"
    assert client.get("/me", headers=rejected).status_code == 403
