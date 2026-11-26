import re
from decimal import Decimal, InvalidOperation

HEADER = re.compile(r"^(\d+)\t(\d+[-\s][^\t]+)\t(\d+[-\s][^\t]+)\t(\d+)\t(.*)$")
DATE = re.compile(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}")
STATUSES = ("AGU. ANALISE", "APROVADO", "REPROVADO", "CANCELADO", "PENDENTE", "EM ANALISE", "FORA DO PRAZO")
PACK = re.compile(r"^[A-ZÀ-Ú]+-\d+(?:[,.]\d+)?$", re.I)

def number(value):
    return Decimal(value.strip().replace("R$", "").strip().replace(".", "").replace(",", "."))

def parse(text: str):
    records, warnings = [], []
    current = None
    in_products = False
    metadata = []
    def finish():
        if current is None:
            return
        combined = " ".join(metadata)
        date = DATE.search(combined)
        current["included_at"] = date.group() if date else None
        current["status"] = next((s for s in STATUSES if re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", combined.upper())), "DESCONHECIDO")
        if current["status"] == "DESCONHECIDO":
            warnings.append(f"Chamado {current['code']}: status não reconhecido.")
        if not current["items"]:
            warnings.append(f"Chamado {current['code']}: nenhum item visível; itens anteriores serão preservados.")
        records.append(current)
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if line in {"Editar", "×"}:
            break
        match = HEADER.match(line)
        if match:
            finish()
            code, origin, destination, load, rest = match.groups()
            current = dict(code=code, origin=origin, destination=destination, load=load, reason="", included_at=None, status="DESCONHECIDO", items=[])
            metadata = [rest]
            in_products = False
            continue
        if current is None or not line:
            continue
        cols = line.split("\t")
        if len(cols) >= 4 and cols[0].isdigit() and PACK.fullmatch(cols[3].strip()):
            try:
                qty = number(cols[2])
                if not qty.is_finite() or qty < 0:
                    raise ValueError()
                item = dict(code=cols[0], description=cols[1], quantity=str(qty), package=cols[3], total=None)
                if any((i["code"], i["package"]) == (item["code"], item["package"]) for i in current["items"]):
                    warnings.append(f"Chamado {current['code']}: item repetido; revise antes de salvar.")
                current["items"].append(item)
            except (InvalidOperation, ValueError):
                warnings.append(f"Chamado {current['code']}: quantidade inválida.")
            in_products = True
        elif line in {"CÓDIGO", "PRODUTO", "DESCRIÇÃO", "QUANTIDDE", "INFORMADA", "EMBALAGEM", "OBSERVAÇÃO"}:
            in_products = True
        elif not in_products:
            metadata.append(line)
            if not current["reason"]:
                reason = line.split("\t")[0].strip()
                if reason and not DATE.search(reason) and reason.upper() != "MANUAL":
                    current["reason"] = reason
    finish()
    if not records:
        warnings.append("Nenhum chamado reconhecido. Confira o formato copiado.")
    return {"records": records, "warnings": warnings}
