import os
os.environ["DATABASE_URL"] = "sqlite://"
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import app.main as main

def test_api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    main.Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setenv("RM_ACCESS_TOKEN", "synthetic-test-token")
    client = TestClient(main.app)
    assert client.get("/occurrences").status_code == 401
    headers = {"X-RM-Token":"synthetic-test-token"}
    record = {"code":"90001","origin":"001-EXEMPLO","destination":"002-EXEMPLO","load":"123","reason":"FALTA","status":"AGU. ANALISE","items":[{"code":"100","description":"FICTÍCIO","quantity":"1","package":"CX-12","total":"120.10"}]}
    assert client.post("/imports/confirm",json=[record],headers=headers).status_code == 200
    record["items"] = []
    assert client.post("/imports/confirm",json=[record],headers=headers).status_code == 200
    data = client.get("/occurrences",headers=headers).json()
    assert len(data) == 1 and data[0]["items"][0]["total"] == "120.10"
    assert client.get("/summary",headers=headers).json()["net_reported"] == "-120.10"
    result = client.post("/inventory/reconcile",json={"destination":"2","items":[{"code":"100","quantity":"1"}]},headers=headers)
    assert result.status_code == 200 and len(result.json()["items"][0]["pending"]) == 1
