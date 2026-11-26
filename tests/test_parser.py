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


def test_detail_preserves_reported_total():
    text = "90001\t001-CD EXEMPLO\t002-LOJA EXEMPLO\t100001\tManual\nFALTA\t01/01/2026 10:00:00\nAGU. ANÁLISE\nEditar\nLojas\n002-LOJA EXEMPLO\nCD Origem\n001-CD EXEMPLO\nNº Carga\n100001\nNº BDV\n123\nMotivo\nFALTA\nCOD. PRODUTO\tDES. PRODUTO\tEMBALAGEM\tQUANTIDADE\tVALOR UNITÁRIO\tVALOR TOTAL\n100\tPRODUTO FICTÍCIO\tCX-12\t1\tR$ 10,01\tR$ 120,10"
    result = parse(text)
    record = result["records"][0]
    assert not result.get("blocked")
    assert record["bdv"] == "123"
    assert record["status"] == "AGU. ANALISE"
    assert record["items"][0]["total"] == "120.10"
    assert record["items"][0]["unit_value"] == "10.01"

def test_detail_wrong_target_blocked():
    text = "90001\t001-CD EXEMPLO\t002-LOJA EXEMPLO\t100001\tManual\nFALTA\nEditar\nLojas\n002-LOJA EXEMPLO\nCD Origem\n001-CD EXEMPLO\nNº Carga\n100002\nMotivo\nFALTA"
    assert parse(text)["blocked"]
