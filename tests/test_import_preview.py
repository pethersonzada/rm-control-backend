from app.parser import parse
from conftest import headers
from test_api import RECORD

TABLE = ("DATA RECEBIMENTO\tLOJA\tPLU\tN° CHAMADO\tTIPO DA DIVERGÊNCIA\tQTD DIVERGENTE\t"
         "VOLUME\tDESCRIÇÃO PRODUTO\tVALOR DA DIVERGÊNCIA\n"
         "08/01/2026\t002-EXEMPLO\t000100\t090001\tFALTA\t1,5\tCX-12\tFICTÍCIO\t-12,34\n"
         "08/01/2026\t003-EXEMPLO\t000100\t090001\tSOBRA\t2\tCX-12\tFICTÍCIO\t")

DETAIL = ("Editar\nLojas\n002-EXEMPLO\nCD Origem\n001-EXEMPLO\nNº Carga\n123\nNº BDV\n001\nMotivo\nFALTA\n"
          "COD. PRODUTO\tDES. PRODUTO\tEMBALAGEM\tQUANTIDADE\tVALOR UNITÁRIO\tVALOR TOTAL\n"
          "100\tFICTÍCIO\tCX-12\t1\tR$ 120,10\tR$ 120,10")


def test_tabular_clipboard():
    result = parse(TABLE)
    assert not result["blocked"]
    assert len(result["records"]) == 2
    assert result["records"][0]["code"] == "090001"
    assert result["records"][0]["items"][0]["code"] == "000100"
    assert result["records"][0]["items"][0]["quantity"] == "1.5"
    assert result["records"][0]["items"][0]["total"] == "-12.34"
    assert result["records"][1]["items"][0]["total"] is None
    bad = parse(TABLE + "\ninvalid\tbad")
    assert bad["blocked"] and bad["counts"]["invalid_rows"] == 1


def test_preview_classification_and_details(workspace):
    client, _ = workspace
    auth = headers()
    def preview(text, **extra):
        return client.post("/imports/preview", json={"text": text, **extra}, headers=auth)
    first = preview(TABLE)
    assert first.status_code == 200
    result = first.json()
    assert result["comparison"]["new"] == 2
    assert client.get("/occurrences", headers=auth).json()["records"] == []
    assert client.post("/imports/confirm", headers=auth, json=result["records"]).status_code == 200
    assert preview(TABLE).json()["comparison"]["unchanged"] == 2
    changed = preview(TABLE.replace("-12,34", "-13,34")).json()
    assert changed["comparison"]["updated"] == 1
    assert client.patch("/occurrences/090001/status?store=2", headers=auth, json={"status":"APROVADO","expected_status":"DESCONHECIDO"}).status_code == 200
    assert client.post("/imports/confirm", headers=auth, json=changed["records"]).status_code == 409
    assert client.post("/imports/confirm", headers=auth, json=[RECORD]).status_code == 200
    detail = preview(DETAIL, target_code="90001", target_store="2")
    assert detail.status_code == 200
    assert not detail.json()["blocked"]
    assert detail.json()["records"][0]["bdv"] == "001"
    assert detail.json()["comparison"]["updated"] == 1
    assert client.post("/imports/preview", headers=headers("viewer"), json={"text": TABLE}).status_code == 403
    assert client.post("/imports/confirm", headers=auth, json=[{**RECORD,"destination":"003-EXEMPLO"}]).status_code == 200
    assert preview(DETAIL,target_code="90001").status_code == 409
    assert preview(DETAIL,target_code="90001",target_store="9").status_code == 403
    assert preview(DETAIL.replace("123\nNº BDV","999\nNº BDV"),target_code="90001",target_store="2").json()["blocked"]
