import pdfplumber
from docx import Document

def ingest_pdf(path: str) -> str:
    parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)

def ingest_docx(path: str) -> str:
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)

def ingest(source_type: str, path: str) -> str:
    st = (source_type or "").lower().strip()
    if st == "pdf":
        return ingest_pdf(path)
    if st == "docx":
        return ingest_docx(path)
    return ingest_docx(path)
