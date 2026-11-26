import base64
import binascii
import io
import unicodedata
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

def normalize(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).upper().split())

def identifier(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value or "").strip()

def day(value, epoch):
    if value is None:
        return None
    if isinstance(value, (float, int)):
        value = from_excel(value, epoch)
    if isinstance(value, (datetime, date)):
        return value.strftime("%d/%m/%Y")
    return str(value).strip() or None

def parse_workbook(content_base64, sheet=None, skip_invalid=False):
    try:
        data = base64.b64decode(content_base64, validate=True)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 250_000_000:
                raise ValueError("Planilha excede o limite descompactado.")
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except (binascii.Error, zipfile.BadZipFile, OSError, KeyError) as e:
        raise ValueError("Arquivo XLSX inválido.") from e
    try:
        if sheet and sheet not in workbook.sheetnames:
            raise ValueError("Aba não encontrada.")
        candidates = [workbook[sheet]] if sheet else workbook.worksheets
        required = {"DATA RECEBIMENTO", "LOJA", "PLU", "N° CHAMADO", "TIPO DA DIVERGENCIA", "QTD DIVERGENTE"}
        # NFKD normaliza o símbolo de número; todas as chaves usam a mesma função.
        required = {normalize(v) for v in required}
        selected = None
        for ws in candidates:
            for n, row in enumerate(ws.iter_rows(max_row=30, values_only=True), 1):
                headers = {normalize(v): i for i, v in enumerate(row) if isinstance(v, str)}
                if required <= headers.keys():
                    selected = (ws, n, headers)
                    break
            if selected:
                break
        if not selected:
            raise ValueError("Nenhuma aba possui os cabeçalhos de consolidação esperados.")
        ws, start, headers = selected
        records, warnings = {}, []
        blocked = False
        rejected = set()
        for n, row in enumerate(ws.iter_rows(min_row=start+1, max_col=max(headers.values())+1, values_only=True), start+1):
            if n > 100000:
                raise ValueError("Limite de 100 mil linhas excedido.")
            def val(name):
                i = headers.get(normalize(name))
                return row[i] if i is not None else None
            code, plu = identifier(val("N° CHAMADO")), identifier(val("PLU"))
            if not code or not plu:
                if code or plu:
                    warnings.append(f"Linha {n}: chamado ou PLU ausente.")
                    if code:
                        rejected.add(code)
                    blocked = True
                continue
            if not code.isdigit() or not plu.isdigit():
                warnings.append(f"Linha {n}: identificador inválido.")
                rejected.add(code)
                blocked = True
                continue
            reason = normalize(val("TIPO DA DIVERGÊNCIA"))
            try:
                qty = Decimal(str(val("QTD DIVERGENTE")))
                price = val("VALOR DA DIVERGÊNCIA")
                total = abs(Decimal(str(price))) if price is not None and price != "" else None
                if not qty.is_finite() or (total is not None and not total.is_finite()):
                    raise ValueError()
            except (InvalidOperation, ValueError, TypeError):
                warnings.append(f"Linha {n}: quantidade ou valor inválido.")
                rejected.add(code)
                blocked = True
                continue
            record = records.setdefault(code, dict(code=code, origin=identifier(val("CD ORIGEM")), destination=identifier(val("LOJA")), load="", bdv=identifier(val("N° BDV")) or None, reason=reason, received_at=day(val("DATA RECEBIMENTO"), workbook.epoch), returned_at=day(val("DATA RETORNO CD"), workbook.epoch), status=normalize(val("SITUAÇÃO DO CHAMADO")) or "DESCONHECIDO", source="spreadsheet", items=[]))
            if record["reason"] != reason or record["destination"] != identifier(val("LOJA")) or record["bdv"] != (identifier(val("N° BDV")) or None):
                warnings.append(f"Linha {n}: informações conflitantes no mesmo chamado.")
                rejected.add(code)
                blocked = True
                continue
            package = identifier(val("VOLUME")) or "NÃO INFORMADO"
            if any(i["code"] == plu and i["package"] == package for i in record["items"]):
                warnings.append(f"Linha {n}: PLU e embalagem repetidos; revise antes de importar.")
                rejected.add(code)
                blocked = True
                continue
            record["items"].append(dict(code=plu, description=identifier(val("DESCRIÇÃO PRODUTO")), quantity=str(abs(qty)), package=package, total=str(total) if total is not None else None, category=identifier(val("CATEGORIA")) or None))
        if skip_invalid and blocked:
            for code in rejected:
                records.pop(code, None)
            warnings.append("Importação parcial autorizada: chamados com conflito foram excluídos integralmente; linhas sem código ficaram de fora.")
            blocked = False
        if not records:
            warnings.append("Nenhum registro reconhecido.")
        return {"records": list(records.values()), "warnings": warnings, "blocked": blocked}
    finally:
        workbook.close()
