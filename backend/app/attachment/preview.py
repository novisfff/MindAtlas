"""Attachment preview utilities."""

from pathlib import Path

# Extensions allowed for inline preview (images and PDF)
# Note: SVG is excluded by default due to XSS risks (can contain scripts)
INLINE_PREVIEW_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    # ".svg",  # Disabled: SVG can contain scripts, use download instead
}

# Text extensions that can be read directly as markdown
TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}

# Extension to canonical MIME type mapping
EXTENSION_TO_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}

# Maximum file size for text preview (2MB)
MAX_TEXT_PREVIEW_SIZE = 2 * 1024 * 1024


def get_file_extension(filename: str) -> str:
    """Get lowercase file extension from filename."""
    return Path(filename).suffix.lower()


def is_inline_previewable(filename: str) -> bool:
    """Check if file can be previewed inline (images, PDF)."""
    ext = get_file_extension(filename)
    return ext in INLINE_PREVIEW_EXTENSIONS


def is_text_file(filename: str) -> bool:
    """Check if file is a text file that can be read directly."""
    ext = get_file_extension(filename)
    return ext in TEXT_EXTENSIONS


def get_canonical_mime_type(filename: str, fallback: str = "application/octet-stream") -> str:
    """Get canonical MIME type based on file extension."""
    ext = get_file_extension(filename)
    return EXTENSION_TO_MIME.get(ext, fallback)
