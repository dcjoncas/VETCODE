from io import BytesIO
from urllib.parse import urlparse
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZIP_DEFLATED, ZipFile


_HEADERS = [
    "Search Order",
    "Current Rank",
    "Candidate",
    "Title",
    "Location",
    "Email",
    "Phone",
    "Match Score",
    "Match Band",
    "Matched JD Signals",
    "Missing JD Signals",
    "Source",
    "Evidence Sources",
    "Source Profile",
    "TEMP Profile",
    "TEMP Profile ID",
]


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _text_cell(reference: str, value, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    text = escape(str(value or ""))
    return f'<c r="{reference}" t="inlineStr"{style_attr}><is><t xml:space="preserve">{text}</t></is></c>'


def _number_cell(reference: str, value, style: int = 0) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text_cell(reference, "", style)
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{reference}"{style_attr}><v>{round(number, 2)}</v></c>'


def _joined(values) -> str:
    if not isinstance(values, list):
        return str(values or "")
    return "; ".join(str(value).strip() for value in values if str(value or "").strip())


def _safe_http_url(value) -> str:
    url = str(value or "").strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def build_ranked_search_xlsx(search: dict, rows: list[dict]) -> bytes:
    metadata = search.get("metadata") if isinstance(search.get("metadata"), dict) else {}
    query_name = str(metadata.get("queryName") or "Saved_Search_QRY")
    title = f"Ranked sourcing report - {query_name}"
    sheet_rows = [f'<row r="1" ht="30" customHeight="1">{_text_cell("A1", title, 1)}</row>']
    summary = [
        ("Saved query", query_name),
        ("JD", metadata.get("jdName") or "Not Found"),
        ("Client", metadata.get("clientName") or "Not Found"),
        ("Source", metadata.get("source") or "External"),
        ("Created", metadata.get("createdAt") or ""),
    ]
    for row_index, (label, value) in enumerate(summary, start=2):
        sheet_rows.append(
            f'<row r="{row_index}">{_text_cell(f"A{row_index}", label, 5)}{_text_cell(f"B{row_index}", value)}</row>'
        )

    header_row = 8
    header_cells = "".join(
        _text_cell(f"{_column_name(index)}{header_row}", header, 2)
        for index, header in enumerate(_HEADERS, start=1)
    )
    sheet_rows.append(f'<row r="{header_row}" ht="28" customHeight="1">{header_cells}</row>')

    hyperlinks = []
    relationships = []
    for row_index, row in enumerate(rows or [], start=header_row + 1):
        source_url = _safe_http_url(row.get("sourceProfileUrl"))
        temp_url = _safe_http_url(row.get("tempProfileUrl"))
        values = [
            row.get("searchOrder"),
            row.get("rank"),
            row.get("name"),
            row.get("title"),
            row.get("location"),
            row.get("email"),
            row.get("phone"),
            row.get("matchScore"),
            row.get("matchBand"),
            _joined(row.get("matched")),
            _joined(row.get("missing")),
            row.get("source"),
            _joined(row.get("evidenceSources")),
            source_url,
            temp_url,
            row.get("tempProfileId"),
        ]
        cells = []
        for column_index, value in enumerate(values, start=1):
            ref = f"{_column_name(column_index)}{row_index}"
            if column_index in {1, 2}:
                cells.append(_number_cell(ref, value))
            elif column_index == 8:
                cells.append(_number_cell(ref, value, 4))
            elif column_index in {14, 15} and value:
                cells.append(_text_cell(ref, value, 3))
            else:
                cells.append(_text_cell(ref, value))
        sheet_rows.append(f'<row r="{row_index}" ht="22" customHeight="1">{"".join(cells)}</row>')
        for column, url in (("N", source_url), ("O", temp_url)):
            if not url:
                continue
            rel_id = f"rId{len(relationships) + 1}"
            hyperlinks.append(f'<hyperlink ref="{column}{row_index}" r:id="{rel_id}"/>')
            relationships.append(
                f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target={quoteattr(url)} TargetMode="External"/>'
            )

    last_row = max(header_row, header_row + len(rows or []))
    hyperlinks_xml = f'<hyperlinks>{"".join(hyperlinks)}</hyperlinks>' if hyperlinks else ""
    sheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="8" topLeftCell="A9" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>
    <col min="1" max="2" width="14" customWidth="1"/><col min="3" max="3" width="28" customWidth="1"/>
    <col min="4" max="5" width="30" customWidth="1"/><col min="6" max="7" width="25" customWidth="1"/>
    <col min="8" max="9" width="18" customWidth="1"/><col min="10" max="11" width="42" customWidth="1"/>
    <col min="12" max="13" width="30" customWidth="1"/><col min="14" max="15" width="48" customWidth="1"/>
    <col min="16" max="16" width="18" customWidth="1"/>
  </cols>
  <sheetData>{"".join(sheet_rows)}</sheetData>
  <mergeCells count="1"><mergeCell ref="A1:P1"/></mergeCells>
  <autoFilter ref="A8:P{last_row}"/>
  {hyperlinks_xml}
</worksheet>'''

    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1"><numFmt numFmtId="164" formatCode="0.0&quot;%&quot;"/></numFmts>
  <fonts count="5">
    <font><sz val="11"/><name val="Aptos"/></font>
    <font><b/><color rgb="FFFFFFFF"/><sz val="16"/><name val="Aptos Display"/></font>
    <font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font>
    <font><u/><color rgb="FF0563C1"/><sz val="11"/><name val="Aptos"/></font>
    <font><b/><sz val="11"/><name val="Aptos"/></font>
  </fonts>
  <fills count="4">
    <fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF1F2937"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFEC4899"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2"><border/><border><bottom style="thin"><color rgb="FFD1D5DB"/></bottom></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="6">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="3" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
    workbook_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <bookViews><workbookView xWindow="0" yWindow="0" windowWidth="24000" windowHeight="12000"/></bookViews>
  <sheets><sheet name="Ranked Sourcing Report" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        if relationships:
            archive.writestr(
                "xl/worksheets/_rels/sheet1.xml.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(relationships)
                + "</Relationships>",
            )
    return output.getvalue()
