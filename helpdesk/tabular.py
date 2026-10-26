"""
Read an uploaded CSV or Excel file into a plain list of rows. Shared by the organisation
import and the asset import so both behave identically with whatever Excel produces
(UTF-8 with a BOM, Windows-1252, ';' or tab delimiters, numeric cells, ...).
"""
import csv
import io

MAX_BYTES = 2 * 1024 * 1024


class ImportProblem(Exception):
    """A problem with the uploaded file, worded for the person uploading it."""


def norm(value):
    """Cell -> tidy text: collapse whitespace; whole numbers lose Excel's trailing '.0'."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return ' '.join(str(value).split())


def _decode(raw):
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1')


def _table_from_csv(raw):
    text = _decode(raw)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel                 # Excel in some regions saves with ';' — the sniffer handles it
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


def _table_from_xlsx(raw):
    try:
        import openpyxl
    except ImportError:
        raise ImportProblem('Excel files need the optional "openpyxl" package (pip install openpyxl). '
                            'Alternatively use File > Save As > CSV in Excel and upload that.')
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        # Cells are returned as Python values (dates stay dates, numbers stay numbers)
        return [[('' if c is None else c) for c in row] for row in wb.worksheets[0].iter_rows(values_only=True)]
    except Exception:
        raise ImportProblem('That Excel file could not be read. Try saving it again, or save as CSV.')


def read_table(filename, raw, max_bytes=MAX_BYTES):
    """Return the first sheet / the CSV as a list of rows (lists of raw cell values)."""
    if not raw:
        raise ImportProblem('The file is empty.')
    if len(raw) > max_bytes:
        raise ImportProblem(f'The file is too large (limit {max_bytes // (1024 * 1024)} MB).')
    is_xlsx = raw[:4] == b'PK\x03\x04' or (filename or '').lower().endswith(('.xlsx', '.xlsm'))
    return _table_from_xlsx(raw) if is_xlsx else _table_from_csv(raw)


def numbered_rows(table):
    """[(real row number in the file, row)] for rows that have something in them."""
    return [(n, r) for n, r in enumerate(table, start=1) if any(norm(c) for c in r)]
