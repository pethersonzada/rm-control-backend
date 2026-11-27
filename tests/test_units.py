from dataclasses import replace
from decimal import Decimal
from app import security
from app.services.memberships import bootstrap
from conftest import headers
from test_api import RECORD


def test_verified_package_conversion(workspace,monkeypatch):
    client,engine=workspace
    project="rm-control-webapp"
    client.app.state.settings=replace(client.app.state.settings,firebase_project_id=project,allow_legacy_token=True)
    monkeypatch.setattr(security,"verify_uid",lambda token,project:token.split(".")[1])
    bootstrap(engine,project,"alpha","owner",{"2"})
    auth={"Authorization":"Bearer x.owner.x"}
    assert client.post("/imports/confirm",headers=headers(),json=[RECORD]).status_code==200
    body={"destination":"2","items":[{"code":"100","quantity":"-2","package":"CX-12"}]}
    result=client.post("/inventory/reconcile",headers=auth,json=body).json()["items"][0]
    assert result["inventory_normalized"] is None
    product={"code":"100","description":"Fictício","reference":"Catálogo QA","active":True}
    assert client.put("/admin/catalog",headers=auth,json=product).status_code==200
    unit={"code":"100","package":"CX-12","base_unit":"UN","factor":"12","reference":"Embalagem QA","active":True,"revision":0}
    assert client.put("/admin/units",headers=headers(),json=unit).status_code==403
    assert client.put("/admin/units",headers=auth,json=unit).status_code==200
    assert client.put("/admin/units",headers=auth,json=unit).status_code==409
    assert client.put("/admin/units",headers=auth,json={**unit,"package":"CX-24","base_unit":"KG"}).status_code==422
    result=client.post("/inventory/reconcile",headers=auth,json=body).json()["items"][0]
    assert Decimal(result["inventory_normalized"]["quantity"])==Decimal("-24")
    assert Decimal(result["pending"][0]["normalized"]["quantity"])==Decimal("12")
    assert result["inventory_normalized"]["unit"]=="UN"
    assert client.put("/admin/units",headers=auth,json={**unit,"active":False,"revision":1}).status_code==200
    assert client.post("/inventory/reconcile",headers=auth,json=body).json()["items"][0]["inventory_normalized"] is None
