import sys
import zipfile
import xml.etree.ElementTree as ET


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def cell_text(cell, shared):
    if cell.get("t") == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//m:t", NS))
    value = cell.find("m:v", NS)
    if value is None:
        return ""
    if cell.get("t") == "s":
        return shared[int(value.text or "0")]
    return value.text or ""


def main():
    path = sys.argv[1]
    with zipfile.ZipFile(path) as archive:
        wb = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = wb.findall(".//m:sheet", NS)

        ws = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows = ws.findall(".//m:sheetData/m:row", NS)
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in item.findall(".//m:t", NS)))
        headers = [cell_text(cell, shared) for cell in rows[0].findall("m:c", NS)]
        inline_text = [
            "".join(t.text or "" for t in cell.findall(".//m:t", NS))
            for row in rows
            for cell in row.findall("m:c", NS)
            if cell.get("t") == "inlineStr"
        ]

    print("SHEETS=" + str(len(sheets)))
    print("SHEET_NAMES=" + ",".join(sheet.get("name", "") for sheet in sheets))
    print("DATA_ROWS=" + str(len(rows) - 1))
    print("HEADERS=" + "|".join(headers))
    print("QUESTION_MARKS=" + str(sum(text.count("?") for text in shared + inline_text)))


if __name__ == "__main__":
    main()
