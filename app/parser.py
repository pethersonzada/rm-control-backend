import re
import unicodedata
from decimal import Decimal, InvalidOperation
from datetime import datetime

HEADER = re.compile(r"^(\d+)\t(\d+[-\s][^\t]+)\t(\d+[-\s][^\t]+)\t(\d+)\t(.*)$")
DATE = re.compile(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}")
STATUSES = ("AGU. RECONFERENCIA", "AGU. ANALISE", "APROVADO", "REPROVADO", "CANCELADO", "PENDENTE", "EM ANALISE", "FORA DO PRAZO")
PACK = re.compile(r"^[A-ZÀ-Ú]+-\d+(?:[,.]\d+)?$", re.I)

def number(value):
    value = value.strip().removeprefix("R$").strip()
    if not re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)(?:,[0-9]+)?", value):
        raise ValueError("número inválido")
    return Decimal(value.replace(".", "").replace(",", "."))

def parse_list(text: str):
    records, warnings = [], []
    blocked = False
    item_keys = set()
    seen_records = set()
    ignored = 0
    current = None
    in_products = False
    metadata = []
    def finish():
        if current is None:
            return
        combined = " ".join(metadata)
        normalized = "".join(c for c in unicodedata.normalize("NFKD", combined.upper()) if not unicodedata.combining(c))
        nonlocal blocked
        dates = []
        for candidate in DATE.findall(combined):
            try:
                datetime.strptime(candidate, "%d/%m/%Y %H:%M:%S")
                dates.append(candidate)
            except ValueError:
                warnings.append(f"Chamado {current['code']}: data inválida.")
                blocked = True
        current["included_at"] = dates[0] if dates else None
        current["approved_at"] = dates[1] if len(dates) > 1 else None
        current["is_test"] = bool(re.search(r"\bTESTE\b.*\bNAO ENVIAR\b", normalized))
        current["status"] = next((s for s in STATUSES if re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", normalized)), "DESCONHECIDO")
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
            key = (code, destination)
            if key in seen_records:
                warnings.append(f"Chamado {code}: duplicado na listagem.")
                blocked = True
            seen_records.add(key)
            item_keys = set()
            metadata = [rest]
            in_products = False
            continue
        if current is None or not line:
            continue
        cols = line.split("\t")
        if len(cols) >= 4 and cols[0].isdigit() and PACK.fullmatch(cols[3].strip()):
            try:
                qty = number(cols[2])
                if not qty.is_finite():
                    raise ValueError()
                item = dict(code=cols[0], description=cols[1], quantity=str(qty), package=cols[3], total=None)
                if (item["code"], item["package"]) in item_keys:
                    warnings.append(f"Chamado {current['code']}: item repetido; revise antes de salvar.")
                    blocked = True
                item_keys.add((item["code"], item["package"]))
                current["items"].append(item)
            except (InvalidOperation, ValueError):
                warnings.append(f"Chamado {current['code']}: quantidade inválida.")
                blocked = True
            in_products = True
        elif line in {"CÓDIGO", "PRODUTO", "DESCRIÇÃO", "QUANTIDDE", "INFORMADA", "EMBALAGEM", "OBSERVAÇÃO"}:
            in_products = True
        elif not in_products:
            metadata.append(line)
            if not current["reason"]:
                reason = line.split("\t")[0].strip()
                if reason and not DATE.search(reason) and reason.upper() != "MANUAL":
                    current["reason"] = reason
        elif cols[0].isdigit() or len(cols) >= 3:
            ignored += 1
            blocked = True
    finish()
    if ignored:
        warnings.append(f"{ignored} linhas de produtos fora do formato esperado.")
    if not records:
        warnings.append("Nenhum chamado reconhecido. Confira o formato copiado.")
    return {"records": records, "warnings": warnings[:100], "blocked": blocked}


def parse(text: str, target_code: str | None = None):
    result = parse_list(text)
    records, warnings = result["records"], result["warnings"]
    split = re.split(r"(?m)^\s*Editar\s*$", text, maxsplit=1)
    if len(split) == 1:
        return result
    detail = split[1].replace("\r", "")
    lines = [line.strip() for line in detail.splitlines()]
    def normalized(label):
        return "".join(c for c in unicodedata.normalize("NFKD", label.upper()) if not unicodedata.combining(c))

    def field(label):
        for i, line in enumerate(lines[:-1]):
            if normalized(line) == normalized(label):
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
    detailed = set()
    observed = {(item["code"], item["package"]): item for item in record["items"]}
    malformed = 0
    for line in lines:
        if normalized(line).startswith("COD. PRODUTO\t"):
            mode = "products"
            record["detail_imported"] = True
            continue
        if normalized(line).startswith("NUMERO NF\t"):
            mode = "invoices"
            continue
        cols = line.split("\t")
        if not cols[0].isdigit():
            if mode and len(cols) >= 2:
                malformed += 1
            continue
        if mode == "products" and len(cols) >= 6 and PACK.fullmatch(cols[2]):
            try:
                quantity, unit, total = map(number, (cols[3], cols[4], cols[5]))
                if any(not n.is_finite() for n in (quantity, unit, total)):
                    raise ValueError()
            except (InvalidOperation, ValueError):
                warnings.append(f"Chamado {record['code']}: valor ou quantidade inválido no item {cols[0]}.")
                result["blocked"] = True
                continue
            item = dict(code=cols[0], description=cols[1], package=cols[2], quantity=str(quantity), unit_value=str(unit), total=str(total))
            key = (item["code"], item["package"])
            if key in detailed:
                warnings.append(f"Chamado {record['code']}: item detalhado duplicado.")
                result["blocked"] = True
                continue
            detailed.add(key)
            if key in observed:
                observed[key].update(item)
            else:
                record["items"].append(item)
        elif mode == "invoices" and len(cols) >= 2 and cols[1].isdigit():
            invoice = {"number": cols[0], "series": cols[1]}
            invoices = record.setdefault("invoices", [])
            if invoice not in invoices:
                invoices.append(invoice)
        elif mode:
            malformed += 1
    if malformed:
        warnings.append(f"{malformed} linhas de detalhes fora do formato esperado.")
        result["blocked"] = True
    if not mode:
        warnings.append("Nenhuma tabela de detalhes reconhecida.")
        result["blocked"] = True
    if record.get("detail_imported") and not detailed:
        warnings.append("Nenhum preço detalhado reconhecido.")
        result["blocked"] = True
    return result
