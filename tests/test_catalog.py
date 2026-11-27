from dataclasses import replace
from app import security
from app.services.memberships import bootstrap

PROJECT = "rm-control-webapp"


def test_catalog_reviewed_reference_scope(workspace, monkeypatch):
    client, engine = workspace
    client.app.state.settings = replace(client.app.state.settings, firebase_project_id=PROJECT)
    monkeypatch.setattr(security, "verify_uid", lambda token, project: token.split(".")[1])
    def auth(uid):
        return {"Authorization": f"Bearer x.{uid}.x"}
    bootstrap(engine, PROJECT, "alpha", "owner", {"2"})
    bootstrap(engine, PROJECT, "beta", "other-owner", {"2"})
    owner = auth("owner")
    data = {"code":"000123","description":"Produto fictício","reference":"Catálogo sintético validado","package":"CX","active":True}
    assert client.post("/admin/catalog/batch", headers=owner, json={"products":[data]}).json()["counts"]["new"] == 1
    assert client.get("/catalog", headers=owner).json()["products"] == []
    assert client.post("/admin/catalog/batch", headers=owner, json={"products":[data],"preview":False}).status_code == 200
    assert client.get("/catalog", headers=owner, params={"code":"000123"}).json()["products"][0]["description"] == "Produto fictício"
    assert client.get("/catalog", headers=auth("other-owner")).json()["products"] == []
    assert client.put("/admin/users", headers=owner, json={"uid":"operator","role":"importer","stores":["2"],"active":True}).status_code == 200
    assert client.put("/admin/catalog", headers=auth("operator"), json=data).status_code == 403
    assert client.get("/catalog", headers=auth("operator")).json()["products"][0]["code"] == "000123"
    assert client.put("/admin/catalog", headers=owner, json={**data,"active":False}).status_code == 200
    assert client.get("/catalog", headers=auth("operator")).json()["products"] == []
