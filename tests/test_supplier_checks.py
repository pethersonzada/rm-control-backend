from io import BytesIO

from openpyxl import load_workbook
from conftest import headers


def test_completed_checks_scope_retry_correction_report(workspace):
    client, _ = workspace
    body = {"store":"2","supplier_code":"00042","supplier_name":"Fornecedor sintético","checker":"Conferente QA",
            "checked_at":"2026-01-08T22:45:00-03:00","notes":"=1+1","reference":"NF 001"}
    auth = headers()
    key = {**auth,"Idempotency-Key":"supplier-1"}
    assert client.post("/supplier-checks",headers=headers("viewer"),json=body).status_code==403
    assert client.post("/supplier-checks",headers=key,json={**body,"store":"9"}).status_code==403
    assert client.post("/supplier-checks",headers=key,json={**body,"checked_at":"2099-01-01T00:00:00Z"}).status_code==422
    response=client.post("/supplier-checks",headers=key,json=body)
    assert response.status_code==200,response.text
    row=response.json()
    assert client.post("/supplier-checks",headers=key,json=body).json()["id"]==row["id"]
    assert client.post("/supplier-checks",headers=key,json={**body,"reference":"NF 002"}).status_code==409
    second=client.post("/supplier-checks",headers={**auth,"Idempotency-Key":"supplier-2"},json=body).json()
    assert second["id"]!=row["id"]
    assert client.get("/supplier-checks",headers=headers("other")).json()["records"]==[]
    assert client.get("/supplier-checks?store=3",headers=headers("viewer")).status_code==403
    assert client.get("/supplier-checks?from_date=2026-01-08&to_date=2026-01-08",headers=auth).json()["completed"]==2
    assert client.get("/supplier-checks?from_date=2026-01-09",headers=auth).json()["completed"]==0
    path=f"/supplier-checks/{row['id']}"
    correction={**body,"checker":"Conferente corrigido","revision":1,"change_reason":"Correção sintética"}
    assert client.put(path,headers=auth,json=correction).json()["revision"]==2
    assert client.put(path,headers=auth,json=correction).status_code==409
    assert client.put(path,headers=headers("other"),json=correction).status_code==404
    cancelled={**correction,"revision":2,"active":False}
    assert not client.put(path,headers=auth,json=cancelled).json()["active"]
    assert client.get("/supplier-checks",headers=auth).json()["completed"]==1
    page=client.get("/supplier-checks?include_cancelled=true",headers=auth).json()
    assert page["cancelled"]==1
    events=client.get(path+"/history",headers=auth).json()["events"]
    assert [event["action"] for event in events]==["cancel","edit","create"]
    assert client.get(path+"/history",headers=headers("other")).status_code==404
    report=client.get("/reports/fornecedores.xlsx",headers=auth)
    assert report.status_code==200
    book=load_workbook(BytesIO(report.content),read_only=True)
    try:
        rows=list(book["Conferências"].iter_rows())
        assert len(rows)==2 and rows[1][1].value=="00042"
        assert rows[1][6].data_type=="s" and rows[1][6].value=="=1+1"
        assert list(book["Por conferente"].values)[1][2]==1
    finally:
        book.close()
