from dataclasses import replace
from app import security
from app.services.memberships import bootstrap
from conftest import headers
from test_api import RECORD


def test_reviewed_policy_and_finance(workspace,monkeypatch):
    client,engine=workspace
    project="rm-control-webapp"
    client.app.state.settings=replace(client.app.state.settings,firebase_project_id=project,allow_legacy_token=True)
    monkeypatch.setattr(security,"verify_uid",lambda token,project: token.split(".")[1])
    bootstrap(engine,project,"alpha","owner",{"2"})
    auth={"Authorization":"Bearer x.owner.x"}
    assert client.post("/imports/confirm",headers=headers(),json=[RECORD]).status_code==200
    assert client.get("/summary",headers=auth).json()["approved"] is None
    assert client.get("/admin/policy",headers=auth).json()["enabled"] is False
    rule={"enabled":True,"days":3,"start_field":"received_at","timezone":"America/Recife","holidays":["2026-01-12"],"reference":"Regra sintética conferida","revision":0}
    assert client.put("/admin/policy",headers=headers(),json=rule).status_code==403
    assert client.put("/admin/policy",headers=auth,json=rule).json()["revision"]==1
    assert client.put("/admin/policy",headers=auth,json=rule).status_code==409
    assert client.get("/summary",headers=auth).json()["deadline_counts"] is not None
    path="/occurrences/"+RECORD["code"]+"/finance?store=2"
    review={"approved":"10.10","recovered":"3.20","pending":None,"reference":"Comprovante QA","revision":0}
    assert client.put(path,headers=headers(),json=review).status_code==403
    assert client.put(path,headers=auth,json=review).status_code==200
    assert client.put(path,headers=auth,json=review).status_code==409
    summary=client.get("/summary",headers=auth).json()
    from decimal import Decimal
    assert Decimal(summary["approved"])==Decimal("10.10")
    assert Decimal(summary["recovered"])==Decimal("3.20")
    assert summary["pending"] is None
    assert summary["financial_counts"]=={"approved":1,"recovered":1,"pending":0}
    assert client.get(path,headers=headers("other")).status_code==404
