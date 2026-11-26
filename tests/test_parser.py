from app.parser import parse

def test_synthetic_list():
    text = "90001\t001-CD EXEMPLO\t002-LOJA EXEMPLO\t100001\tManual\nFALTA\t01/01/2026 10:00:00\noperador\nEXEMPLO\tCARRETA\tEXEMPLO\tAGU. ANALISE\nCÓDIGO\nPRODUTO\n100\tPRODUTO FICTÍCIO\t2\tCX-12"
    result = parse(text)
    record = result["records"][0]
    assert record["code"] == "90001"
    assert record["reason"] == "FALTA"
    assert record["status"] == "AGU. ANALISE"
    assert record["items"][0]["quantity"] == "2"
    assert result["warnings"] == []

def test_collapsed_items():
    result = parse("90002\t001-CD EXEMPLO\t002-LOJA EXEMPLO\t100002\tManual\nSOBRA\t01/01/2026 10:00:00\nAGU. ANALISE")
    assert result["records"][0]["items"] == []
    assert result["warnings"]

def test_no_data():
    assert parse("menu")["records"] == []
