"""Minimal .xlsx reader using only the standard library (an .xlsx file is a zip of XML).

Returns each sheet as a list of rows keyed by the header row. Values are strings exactly as
stored: shared and inline strings, numbers ("40.0"), booleans ("TRUE"/"FALSE"), and formula
results ("#N/A"). Formatting, formulas and dates-as-dates are not interpreted.
"""

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"m": _MAIN, "rel": _PKG_REL}


class WorkbookError(ValueError):
    """The file is not a readable .xlsx workbook, or a sheet is missing."""


def _column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref)
    if letters is None:
        raise WorkbookError(f"unexpected cell reference {cell_ref!r}")
    index = 0
    for char in letters.group(0):
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _text(element: ET.Element) -> str:
    return "".join(t.text or "" for t in element.iter(f"{{{_MAIN}}}t"))


def read_sheets(path: Path) -> dict[str, list[dict[str, str]]]:
    """{sheet name: [row as {header: value}]}; rows with no value at all are skipped."""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise WorkbookError(f"{path.name} is not a readable .xlsx file: {exc}") from exc
    with archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [_text(item) for item in root.findall("m:si", _NS)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            r.get("Id"): r.get("Target", "") for r in relations.findall("rel:Relationship", _NS)
        }

        sheets: dict[str, list[dict[str, str]]] = {}
        for sheet in workbook.iter(f"{{{_MAIN}}}sheet"):
            target = targets.get(sheet.get(f"{{{_DOC_REL}}}id"), "").lstrip("/")
            target = target if target.startswith("xl/") else f"xl/{target}"
            if target not in names:
                continue
            grid = _read_grid(ET.fromstring(archive.read(target)), shared)
            header = [h.strip() for h in grid[0]] if grid else []
            rows = []
            for values in grid[1:]:
                padded = values + [""] * (len(header) - len(values))
                if any(v.strip() for v in padded):
                    rows.append({h: padded[i] for i, h in enumerate(header) if h})
            sheets[sheet.get("name", "")] = rows
    return sheets


def _read_grid(root: ET.Element, shared: list[str]) -> list[list[str]]:
    grid: list[list[str]] = []
    for row in root.iter(f"{{{_MAIN}}}row"):
        cells: dict[int, str] = {}
        for cell in row.findall("m:c", _NS):
            kind = cell.get("t")
            value = cell.find("m:v", _NS)
            raw = value.text if value is not None and value.text is not None else ""
            if kind == "s" and raw:
                text = shared[int(raw)]
            elif kind == "inlineStr":
                text = _text(cell)
            elif kind == "b":
                text = "TRUE" if raw == "1" else "FALSE"
            else:
                text = raw
            cells[_column_index(cell.get("r", "A"))] = text
        if cells:
            grid.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return grid
