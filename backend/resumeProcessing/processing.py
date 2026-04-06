import pdfplumber
from docx import Document
import io

def extractPdfText(file: bytes) -> str:
    text = []

    try:
        with pdfplumber.open(io.BytesIO(file)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text.append(page_text)
    except Exception:
        raise ValueError("Failed to extract text from PDF. Ensure the file is a valid PDF and not password-protected.")

    return "\n".join(text)

def extractDocxText(file: bytes) -> str:
    doc = Document(io.BytesIO(file))
    return "\n".join([paragraph.text for paragraph in doc.paragraphs])

def ingest(source_type, file: bytes) -> str:
    if source_type == "pdf":
        return extractPdfText(file)
    elif source_type == "docx":
        return extractDocxText(file)
    else:
        raise ValueError(f"Unsupported source type: {source_type}")