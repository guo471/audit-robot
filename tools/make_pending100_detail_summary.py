import csv
import re
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


YES = "\u662f"
REVIEW = "\u8f6c\u4eba\u5de5"
PASS = "\u901a\u8fc7"
COLS = [
    "\u5e8f\u53f7",
    "\u8ba2\u5355\u53f7",
    "\u5ba1\u6838\u7ed3\u679c",
    "\u672c\u5355\u8017\u65f6\u79d2",
    "\u5e73\u5747\u8017\u65f6\u79d2",
    "\u8f6c\u4eba\u5de5\u539f\u56e0",
]
REASON_MAP = {
    "SN_MISMATCH": "\u7cfb\u7edfSN\u4e0e\u7167\u7247\u4e2dSN\u4e0d\u4e00\u81f4",
    "SN_NOT_FOUND": "\u6fc0\u6d3b/SN\u7167\u7247\u672a\u62cd\u5230\u5b8c\u6574\u53ef\u7528SN",
    "PRODUCT_PHOTO_INVALID": "\u5546\u54c1\u7167\u7247\u4e0d\u7b26\u5408\u8981\u6c42",
    "PRODUCT_TYPE_MISMATCH": "\u7167\u7247\u5546\u54c1\u54c1\u7c7b\u4e0e\u9875\u9762\u5546\u54c1\u7c7b\u578b\u4e0d\u4e00\u81f4",
    "UNBOXING_PHOTO_INVALID": "\u62c6\u5c01/\u5b89\u88c5\u7167\u7247\u4e0d\u7b26\u5408\u8981\u6c42",
    "ACTIVATION_PHOTO_INVALID": "\u6fc0\u6d3b/SN\u8bc1\u636e\u94fe\u4e0d\u8db3",
    "IMAGE_STRONG_RISK": "\u56fe\u7247\u7591\u4f3c\u62fc\u63a5\u6216\u5904\u7406\uff0c\u9700\u4eba\u5de5\u590d\u6838",
    "DUPLICATE_IMAGE_EVIDENCE": "\u4e0d\u540c\u5206\u7ec4\u56fe\u7247\u91cd\u590d\uff0c\u8bc1\u636e\u4e0d\u8db3",
    "MODEL_UNCERTAIN": "\u6a21\u578b\u8bc6\u522b\u4e0d\u7a33\u5b9a\u6216\u8d85\u65f6\uff0c\u9700\u4eba\u5de5\u590d\u6838",
    "ADDRESS_TOO_COARSE": "\u5bb6\u7535\u6536\u8d27\u5730\u5740\u4e0d\u591f\u7cbe\u786e",
}


def as_float(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def clean_reason(code, cn, raw, is_manual):
    if not is_manual:
        return ""
    base = REASON_MAP.get(code, "\u9700\u4eba\u5de5\u590d\u6838")
    text = (cn or raw or "").strip()
    if code == "ADDRESS_TOO_COARSE":
        return base
    if "?" in text or re.search(r"[\u7029\u7eee\u5254]", text):
        return base
    if text.startswith(base + "\uff1a"):
        text = text[len(base) + 1 :]
    if not text or text == base:
        return base
    return base + "\uff1a" + text


def col_name(index):
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def cell(ref, value, style=""):
    style_attr = f' s="{style}"' if style else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = escape(str(value or ""))
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{text}</t></is></c>'


def sheet_xml(rows):
    widths = (
        '<cols>'
        '<col min="1" max="1" width="8" customWidth="1"/>'
        '<col min="2" max="2" width="28" customWidth="1"/>'
        '<col min="3" max="3" width="12" customWidth="1"/>'
        '<col min="4" max="5" width="14" customWidth="1"/>'
        '<col min="6" max="6" width="86" customWidth="1"/>'
        '</cols>'
    )
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        widths,
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>',
        "<sheetData>",
    ]
    for row_index, values in enumerate(rows, 1):
        out.append(f'<row r="{row_index}">')
        for col_index, value in enumerate(values, 1):
            style = "1" if row_index == 1 else ""
            out.append(cell(f"{col_name(col_index)}{row_index}", value, style))
        out.append("</row>")
    out.append(f'</sheetData><autoFilter ref="A1:F{len(rows)}"/></worksheet>')
    return "".join(out)


def write_xlsx(path, rows):
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="detail" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml(rows))
        archive.writestr("xl/styles.xml", styles)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: make_pending100_detail_summary.py <source_csv> <output_xlsx>")

    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    raw_rows = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for values in reader:
            values += [""] * (24 - len(values))
            raw_rows.append(values)

    total_elapsed = sum(as_float(row[5]) for row in raw_rows)
    average_elapsed = round(total_elapsed / len(raw_rows), 2) if raw_rows else 0
    rows = [COLS]
    for index, values in enumerate(raw_rows, 1):
        is_manual = values[1] == YES
        result = REVIEW if is_manual else PASS
        reason = clean_reason(values[2], values[3], values[4], is_manual)
        rows.append([index, values[0], result, round(as_float(values[5]), 2), average_elapsed, reason])

    output.parent.mkdir(parents=True, exist_ok=True)
    write_xlsx(output, rows)
    question_marks = sum(str(value).count("?") for row in rows for value in row)
    manual_count = sum(1 for row in rows[1:] if row[2] == REVIEW)
    print(f"OUT={output}")
    print(f"ROWS={len(rows) - 1}")
    print(f"PASS={len(rows) - 1 - manual_count}")
    print(f"MANUAL={manual_count}")
    print(f"AVG_SECONDS={average_elapsed}")
    print(f"QUESTION_MARKS={question_marks}")


if __name__ == "__main__":
    main()
