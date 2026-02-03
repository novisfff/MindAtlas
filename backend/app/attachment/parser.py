"""Document parser using Docling for text extraction."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ParseError(Exception):
    """Raised when document parsing fails."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg"}


def parse_document(file_path: str, content_type: str, *, max_pages: int = 500) -> str:
    """Parse document and extract text using Docling.

    Args:
        file_path: Path to the file to parse
        content_type: MIME type of the file
        max_pages: Maximum pages for PDF files

    Returns:
        Extracted text content

    Raises:
        ParseError: If parsing fails
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ParseError(f"Unsupported file type: {ext}", retryable=False)

    try:
        from docling.document_converter import DocumentConverter
    except ImportError as e:
        raise ParseError("Docling not installed", retryable=False) from e

    try:
        converter = DocumentConverter()
        result = converter.convert(file_path)
        text = result.document.export_to_markdown()
        return text.strip() if text else ""
    except Exception as e:
        logger.exception("Document parsing failed: %s", file_path)
        raise ParseError(f"Parsing failed: {e}", retryable=True) from e
