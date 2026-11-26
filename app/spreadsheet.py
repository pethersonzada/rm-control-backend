import base64
import binascii
import io
import re
import unicodedata
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from time import monotonic
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException
from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel
from openpyxl.utils.exceptions import InvalidFileException

MAX_FILE = 21_000_000
MAX_UNPACKED = 180_000_000
MAX_ROWS = 100_000
MAX_COLUMNS = 120
MAX_WARNINGS = 100
DURATION_LIMIT = 60

REQUIRED = {"DATA RECEBIMENTO", "LOJA", "PLU", "N° CHAMADO", "TIPO DA DIVERGENCIA", "QTD DIVERGENTE"}
SUPPORTED = REQUIRED | {
    "VALOR DA DIVERGENCIA", "DATA RETORNO CD", "CD ORIGEM", "N° BDV", "SITUACAO DO CHAMADO",
    "VOLUME", "DESCRICAO PRODUTO", "CATEGORIA",
}


def normalize(value):
    text = unicodedata.normalize("NFKD", str(value if value is not None else ""))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).upper().split())


def identifier(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value if value is not None else "").strip()


def day(value, epoch):
    if value is None or value == "":
        return None
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        value = from_excel(value, epoch)
    if isinstance(value, (datetime, date)):
        return value.strftime("%d/%m/%Y")
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), pattern).strftime("%d/%m/%Y")
        except ValueError:
            continue
    raise ValueError("data inválida")


def amount(value):
    if isinstance(value, str):
        value = value.strip()
        if "," in value:
            if not re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+),[0-9]+", value):
                raise ValueError("número inválido")
            value = value.replace(".", "").replace(",", ".")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("número inválido")
    return str(result)


def parse_workbook(content_base64, sheet=None, skip_invalid=False):
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Arquivo XLSX inválido.") from error
    return parse_file(io.BytesIO(data), sheet, skip_invalid)


def validate_archive(file):
    file.seek(0, io.SEEK_END)
    if not 0 < file.tell() <= MAX_FILE:
        raise ValueError("O arquivo deve ter até 21 MB.")
    file.seek(0)
    with zipfile.ZipFile(file) as archive:
        parts = archive.infolist()
        names = {part.filename for part in parts}
        if len(parts) > 400 or len(names) != len(parts) or "[Content_Types].xml" not in names:
            raise ValueError("Estrutura XLSX inválida ou excessiva.")
        if sum(part.file_size for part in parts) > MAX_UNPACKED:
            raise ValueError("Planilha excede o limite descompactado.")
        for part in parts:
            if part.flag_bits & 1 or "vbaProject" in part.filename:
                raise ValueError("Planilhas criptografadas ou com macros são recusadas.")
            if part.filename == "xl/sharedStrings.xml" and part.file_size > 32_000_000:
                raise ValueError("A tabela de textos da planilha excede o limite.")
    file.seek(0)


def select_sheet(workbook, name):
    if name and name not in workbook.sheetnames:
        raise ValueError("Aba não encontrada.")
    candidates = [workbook[name]] if name else workbook.worksheets
    if len(candidates) > 32:
        raise ValueError("Informe o nome da aba para um arquivo com muitas abas.")
    compatible = []
    for worksheet in candidates:
        for index, row in enumerate(worksheet.iter_rows(max_row=30, max_col=MAX_COLUMNS, values_only=True), 1):
            headers = {}
            duplicates = set()
            for column, cell in enumerate(row):
                label = normalize(cell)
                if label in SUPPORTED:
                    if label in headers:
                        duplicates.add(label)
                    headers[label] = column
            if REQUIRED <= headers.keys():
                if duplicates:
                    raise ValueError("Cabeçalhos repetidos na aba selecionada.")
                compatible.append((worksheet, index, headers))
                break
    if not compatible:
        raise ValueError("Nenhuma aba possui os cabeçalhos de consolidação esperados.")
    if len(compatible) > 1:
        raise ValueError("Há mais de uma aba compatível. Informe o nome da aba.")
    return compatible[0]


def parse_rows(workbook, worksheet, start, headers, skip_invalid, deadline):
    records, item_keys = {}, {}
    warnings = []
    rejected = set()
    counts = {"rows": 0, "invalid_rows": 0, "rows_without_value": 0}
    valid_rows = {}
    def warn(row, reason, code=None):
        counts["invalid_rows"] += 1
        if len(warnings) < MAX_WARNINGS:
            warnings.append(f"Linha {row}: {reason}.")
        if code:
            rejected.add(code)

    worksheet.reset_dimensions()
    iterator = worksheet.iter_rows(min_row=start + 1, max_col=max(headers.values()) + 1, values_only=True)
    for row_number, row in enumerate(iterator, start + 1):
        if row_number > MAX_ROWS + start:
            raise ValueError("Limite de 100 mil linhas excedido.")
        if row_number % 256 == 0 and monotonic() > deadline:
            raise ValueError("A planilha excedeu o tempo de processamento.")
        if all(cell is None or cell == "" for cell in row):
            continue
        counts["rows"] += 1

        def value(name):
            index = headers.get(name)
            return row[index] if index is not None else None

        code, plu = identifier(value("N° CHAMADO")), identifier(value("PLU"))
        if not re.fullmatch(r"[0-9]{1,40}", code) or not re.fullmatch(r"[0-9]{1,40}", plu):
            warn(row_number, "chamado ou PLU ausente ou inválido", code)
            continue
        destination = identifier(value("LOJA"))
        reason = normalize(value("TIPO DA DIVERGENCIA"))
        if not destination or not reason:
            warn(row_number, "loja ou tipo de divergência ausente", code)
            continue
        try:
            quantity = amount(value("QTD DIVERGENTE"))
            price = value("VALOR DA DIVERGENCIA")
            total = amount(price) if price is not None and price != "" else None
            received = day(value("DATA RECEBIMENTO"), workbook.epoch)
            returned = day(value("DATA RETORNO CD"), workbook.epoch)
        except (InvalidOperation, ValueError, TypeError, OverflowError):
            warn(row_number, "data, quantidade ou valor inválido ou sem valor em cache", code)
            continue
        package = identifier(value("VOLUME")) or "NÃO INFORMADO"
        item_id = (plu, package)
        metadata = dict(
            code=code, origin=identifier(value("CD ORIGEM")), destination=destination, load="",
            bdv=identifier(value("N° BDV")) or None, reason=reason, received_at=received, returned_at=returned,
            status=normalize(value("SITUACAO DO CHAMADO")) or "DESCONHECIDO", source="spreadsheet",
        )
        if code in records:
            if any(records[code][field] != content for field, content in metadata.items()):
                warn(row_number, "informações conflitantes no mesmo chamado", code)
                continue
            if item_id in item_keys[code]:
                warn(row_number, "PLU e embalagem repetidos", code)
                continue
        else:
            records[code] = {**metadata, "items": []}
            item_keys[code] = set()
        item_keys[code].add(item_id)
        valid_rows[code] = valid_rows.get(code, 0) + 1
        if total is None:
            counts["rows_without_value"] += 1
        records[code]["items"].append(dict(
            code=plu, description=identifier(value("DESCRICAO PRODUTO")), quantity=quantity, package=package,
            total=total, category=identifier(value("CATEGORIA")) or None,
        ))
    blocked = counts["invalid_rows"] > 0
    counts["omitted_rows"] = 0
    if skip_invalid and blocked:
        for code in rejected:
            records.pop(code, None)
        counts["omitted_rows"] = counts["invalid_rows"] + sum(valid_rows.get(code, 0) for code in rejected)
        warnings.append("Importação parcial: linhas inválidas e chamados afetados foram excluídos.")
        blocked = False
    if counts["invalid_rows"] > MAX_WARNINGS:
        warnings.append(f"{counts['invalid_rows'] - MAX_WARNINGS} rejeições adicionais.")
    if counts["rows_without_value"]:
        warnings.append(f"{counts['rows_without_value']} linhas sem valor monetário disponível. Esses valores ficam em branco.")
    if not records:
        warnings.append("Nenhum registro reconhecido.")
    return {"records": list(records.values()), "warnings": warnings, "blocked": blocked, "counts": counts,
            "sheet": worksheet.title}


def parse_file(file, sheet=None, skip_invalid=False):
    try:
        validate_archive(file)
        deadline = monotonic() + DURATION_LIMIT
        workbook = load_workbook(file, read_only=True, data_only=True, keep_links=False)
        try:
            return parse_rows(workbook, *select_sheet(workbook, sheet), skip_invalid, deadline)
        finally:
            workbook.close()
    except (zipfile.BadZipFile, OSError, KeyError, TypeError, ParseError, DefusedXmlException, InvalidFileException) as error:
        raise ValueError("Arquivo XLSX inválido.") from error
