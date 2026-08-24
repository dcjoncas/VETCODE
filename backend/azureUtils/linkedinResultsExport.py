from io import BytesIO
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZIP_DEFLATED, ZipFile


_HEADERS = [
    "Person ID",
    "Name",
    "Title",
    "Email",
    "Phone",
    "Location",
    "Source",
    "LinkedIn Enriched",
    "Enrichment Likelihood",
    "Match Score",
    "Match Band",
    "Matched JD Signals",
    "Missing JD Signals",
    "LinkedIn URL",
    "Updated",
]


def _column_name(index: int) -> str:
    value = index
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _text_cell(reference: str, value, style: int = 0) -> str:
    style_attribute = f' s="{style}"' if style else ""
    text = escape(str(value or ""))
    return f'<c r="{reference}" t="inlineStr"{style_attribute}><is><t xml:space="preserve">{text}</t></is></c>'


def _number_cell(reference: str, value, style: int = 0) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text_cell(reference, "", style)
    rendered = str(int(number)) if number.is_integer() else str(round(number, 2))
    style_attribute = f' s="{style}"' if style else ""
    return f'<c r="{reference}"{style_attribute}><v>{rendered}</v></c>'


def _joined(values) -> str:
    if not isinstance(values, list):
        return ""
    return "; ".join(str(value).strip() for value in values if str(value or "").strip())


def _safe_linkedin_url(value) -> str:
    url = str(value or "").strip()
    lowered = url.lower()
    if lowered.startswith(("https://linkedin.com/", "https://www.linkedin.com/", "http://linkedin.com/", "http://www.linkedin.com/")):
        return url
    return ""


def build_linkedin_results_xlsx(rows: list[dict], domain: str) -> bytes:
    clean_domain = str(domain or "dev").strip().lower()
    title = f"{clean_domain.title()} LinkedIn-Enriched TEMP Profiles"
    sheet_rows = []
    sheet_rows.append(
        f'<row r="1" ht="30" customHeight="1">{_text_cell("A1", title, 1)}</row>'
    )
    header_cells = "".join(
        _text_cell(f"{_column_name(index)}2", header, 2)
        for index, header in enumerate(_HEADERS, start=1)
    )
    sheet_rows.append(f'<row r="2" ht="24" customHeight="1">{header_cells}</row>')

    hyperlink_elements = []
    hyperlink_relationships = []
    for row_index, profile in enumerate(rows or [], start=3):
        linkedin_url = _safe_linkedin_url(profile.get("profileUrl"))
        values = [
            profile.get("personid"),
            profile.get("name"),
            profile.get("title"),
            profile.get("email"),
            profile.get("phone"),
            profile.get("location"),
            profile.get("source"),
            "Yes" if profile.get("linkedInEnriched") else "No",
            profile.get("enrichmentLikelihood"),
            profile.get("matchScore"),
            profile.get("matchBand"),
            _joined(profile.get("matchMatched")),
            _joined(profile.get("matchMissing")),
            linkedin_url,
            profile.get("updated"),
        ]
        cells = []
        for column_index, value in enumerate(values, start=1):
            reference = f"{_column_name(column_index)}{row_index}"
            if column_index in {9, 10}:
                cells.append(_number_cell(reference, value, 4 if column_index == 10 else 0))
            elif column_index == 14 and linkedin_url:
                cells.append(_text_cell(reference, linkedin_url, 3))
            else:
                cells.append(_text_cell(reference, value))
        sheet_rows.append(f'<row r="{row_index}" ht="21" customHeight="1">{"".join(cells)}</row>')
        if linkedin_url:
            relationship_id = f"rId{len(hyperlink_relationships) + 1}"
            hyperlink_elements.append(f'<hyperlink ref="N{row_index}" r:id="{relationship_id}"/>')
            hyperlink_relationships.append(
                f'<Relationship Id="{relationship_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target={quoteattr(linkedin_url)} TargetMode="External"/>'
            )

    last_row = max(2, len(rows or []) + 2)
    hyperlinks_xml = f'<hyperlinks>{"".join(hyperlink_elements)}</hyperlinks>' if hyperlink_elements else ""
    sheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="2" topLeftCell="A3" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>
    <col min="1" max="1" width="12" customWidth="1"/><col min="2" max="2" width="24" customWidth="1"/>
    <col min="3" max="3" width="30" customWidth="1"/><col min="4" max="5" width="25" customWidth="1"/>
    <col min="6" max="7" width="28" customWidth="1"/><col min="8" max="11" width="20" customWidth="1"/>
    <col min="12" max="13" width="42" customWidth="1"/><col min="14" max="14" width="48" customWidth="1"/>
    <col min="15" max="15" width="24" customWidth="1"/>
  </cols>
  <sheetData>{"".join(sheet_rows)}</sheetData>
  <autoFilter ref="A2:O{last_row}"/>
  <mergeCells count="1"><mergeCell ref="A1:O1"/></mergeCells>
  {hyperlinks_xml}
</worksheet>'''

    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1"><numFmt numFmtId="164" formatCode="0&quot;%&quot;"/></numFmts>
  <fonts count="4">
    <font><sz val="11"/><name val="Aptos"/></font>
    <font><b/><sz val="16"/><color rgb="FFFFFFFF"/><name val="Aptos Display"/></font>
    <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Aptos"/></font>
    <font><u/><sz val="11"/><color rgb="FF0563C1"/><name val="Aptos"/></font>
  </fonts>
  <fills count="4">
    <fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF1F2937"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFEC4899"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2"><border/><border><bottom style="thin"><color rgb="FFD1D5DB"/></bottom></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="5">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="3" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''

    workbook_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <bookViews><workbookView xWindow="0" yWindow="0" windowWidth="24000" windowHeight="12000"/></bookViews>
  <sheets><sheet name="LinkedIn Results" sheetId="1" r:id="rId1"/></sheets>
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
        if hyperlink_relationships:
            archive.writestr(
                "xl/worksheets/_rels/sheet1.xml.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(hyperlink_relationships)
                + "</Relationships>",
            )
    return output.getvalue()
