import re
from decimal import Decimal, InvalidOperation

HEADER = re.compile(r"^(\d+)\t(\d+[-\s][^\t]+)\t(\d+[-\s][^\t]+)\t(\d+)\t(.*)$")
DATE = re.compile(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}")
STATUSES = ("AGU. ANALISE", "APROVADO", "REPROVADO", "CANCELADO", "PENDENTE", "EM ANALISE", "FORA DO PRAZO")
PACK = re.compile(r"^[A-ZÀ-Ú]+-\d+(?:[,.]\d+)?$", re.I)

def number(value):
    return Decimal(value.strip().replace("R$", "").strip().replace(".", "").replace(",", "."))

def parse_list(text: str):
    records, warnings = [], []
    current = None
    in_products = False
    metadata = []
    def finish():
        if current is None:
            return
        combined = " ".join(metadata)
        dates = DATE.findall(combined)
        current["included_at"] = dates[0] if dates else None
        current["approved_at"] = dates[1] if len(dates) > 1 else None
        current["is_test"] = bool(re.search(r"\bTESTE\b.*\bNAO ENVIAR\b", combined.upper()))
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


def parse(text: str, target_code: str | None = None):
    result = parse_list(text)
    records, warnings = result["records"], result["warnings"]
    split = re.split(r"(?m)^\s*Editar\s*$", text, maxsplit=1)
    if len(split) == 1:
        return result
    detail = split[1].replace("\r", "")
    lines = [line.strip() for line in detail.splitlines()]
    def field(label):
        for i, line in enumerate(lines[:-1]):
            if line == label:
                return lines[i + 1]
        return None
    load, bdv = field("Nº Carga"), field("Nº BDV")
    origin, destination, reason = field("CD Origem"), field("Lojas"), field("Motivo")
    candidates = [r for r in records if r["load"] == load and r["origin"] == origin and r["destination"] == destination and r["reason"] == reason]
    if target_code:
        candidates = [r for r in candidates if r["code"] == target_code]
    if len(candidates) != 1:
        warnings.append("Detalhes não vinculados: selecione o código correto e confira carga, loja, CD e motivo.")
        result["blocked"] = True
        return result
    record = candidates[0]
    record["bdv"] = bdv if bdv and bdv.isdigit() else None
    mode = None
    for line in lines:
        if line.startswith("COD. PRODUTO\t"):
            mode = "products"
            record["detail_imported"] = True
            continue
        if line.startswith("NUMERO NF\t"):
            mode = "invoices"
            continue
        cols = line.split("\t")
        if not cols[0].isdigit():
            continue
        if mode == "products" and len(cols) >= 6 and PACK.fullmatch(cols[2]):
            try:
                quantity, unit, total = map(number, (cols[3], cols[4], cols[5]))
                if any(not n.is_finite() or n < 0 for n in (quantity, unit, total)):
                    raise ValueError()
            except (InvalidOperation, ValueError):
                warnings.append(f"Chamado {record['code']}: valor ou quantidade inválido no item {cols[0]}.")
                result["blocked"] = True
                continue
            item = dict(code=cols[0], description=cols[1], package=cols[2], quantity=str(quantity), unit_value=str(unit), total=str(total))
            matches = [i for i in record["items"] if (i["code"], i["package"]) == (item["code"], item["package"])]
            if len(matches) > 1 or (matches and matches[0].get("unit_value") is not None):
                warnings.append(f"Chamado {record['code']}: item detalhado duplicado.")
                result["blocked"] = True
                continue
            if matches:
                matches[0].update(item)
            else:
                record["items"].append(item)
        elif mode == "invoices" and len(cols) >= 2 and cols[1].isdigit():
            invoice = {"number": cols[0], "series": cols[1]}
            invoices = record.setdefault("invoices", [])
            if invoice not in invoices:
                invoices.append(invoice)
    if not mode:
        warnings.append("Nenhuma tabela de detalhes reconhecida.")
        result["blocked"] = True
    if mode == "products" and not any(i.get("unit_value") is not None for i in record["items"]):
        warnings.append("Nenhum preço detalhado reconhecido.")
        result["blocked"] = True
    return result
