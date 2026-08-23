"""Reproducibly generate the committed XLSX seed using only the standard library."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT / "scenarios" / "supplier-counter-offer" / "workspace" / "internal" / "cost_model.xlsx"
)


def inline_cell(reference: str, value: str) -> str:
    return f'<c r="{reference}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'


def numeric_cell(reference: str, value: str) -> str:
    return f'<c r="{reference}"><v>{value}</v></c>'


@dataclass(frozen=True)
class Formula:
    expression: str
    cached_value: str


def formula_cell(reference: str, value: Formula) -> str:
    return (
        f'<c r="{reference}"><f>{escape(value.expression)}</f>'
        f"<v>{escape(value.cached_value)}</v></c>"
    )


rows = [
    ["Metric", "Atlas", "Beacon", "Unit", "Planning basis"],
    ["Q4 order quantity", "12000", "12000", "units", "Demand plan"],
    [
        "Net downstream unit revenue",
        "36.00",
        "36.00",
        "USD/unit",
        "Approved Q4 commercial plan",
    ],
    [
        "Minimum operating margin",
        "0.275",
        "0.275",
        "fraction (27.5%)",
        "Finance threshold",
    ],
    [
        "Non-component fulfillment cost",
        "3.60",
        "3.60",
        "USD/unit",
        "Handling, inspection, and downstream fulfillment",
    ],
    [
        "Maximum landed component cost",
        Formula("B3*(1-B4)-B5", "22.50"),
        Formula("C3*(1-C4)-C5", "22.50"),
        "USD/unit",
        "Revenue less required margin and non-component costs",
    ],
    [
        "Supplier-specific inbound logistics",
        "0.40",
        "0.70",
        "USD/unit",
        "Lane estimate from logistics planning",
    ],
    [
        "Recommended opening counter",
        Formula("B6-B7", "22.10"),
        Formula("C6-C7", "21.80"),
        "USD/unit",
        "Maximum landed component cost less inbound logistics",
    ],
    [
        "Approval ceiling",
        "23.10",
        "22.80",
        "USD/unit",
        "Escalate before agreeing above this invoice price",
    ],
]

sheet_rows = []
for row_number, row in enumerate(rows, start=1):
    cells = []
    for column_number, value in enumerate(row, start=1):
        column = chr(64 + column_number)
        reference = f"{column}{row_number}"
        if isinstance(value, Formula):
            cells.append(formula_cell(reference, value))
        elif row_number > 1 and column in {"B", "C"} and value.replace(".", "", 1).isdigit():
            cells.append(numeric_cell(reference, value))
        else:
            cells.append(inline_cell(reference, str(value)))
    sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')

content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Q4 Economics" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
worksheet = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:E{len(rows)}"/><sheetData>{"".join(sheet_rows)}</sheetData>
</worksheet>"""

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("[Content_Types].xml", content_types)
    archive.writestr("_rels/.rels", root_rels)
    archive.writestr("xl/workbook.xml", workbook)
    archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
    archive.writestr("xl/worksheets/sheet1.xml", worksheet)

print(OUTPUT)
