"""Document parser using Docling for text extraction."""
from __future__ import annotations

import importlib
import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


class ParseError(Exception):
    """Raised when document parsing fails."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg"}


def _parse_csv_list(value: str) -> list[str]:
    """Parse comma-separated string into list."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize_chat_completions_url(value: str) -> str:
    """Normalize URL to OpenAI-compatible chat completions endpoint."""
    url = (value or "").strip()
    if not url:
        return ""
    if "/chat/completions" in url:
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    if url.endswith("/v1/"):
        return f"{url}chat/completions"
    if url.endswith("/"):
        return f"{url}v1/chat/completions"
    return f"{url}/v1/chat/completions"


def _rapidocr_is_available() -> bool:
    """Check if RapidOCR is installed."""
    try:
        importlib.import_module("rapidocr_onnxruntime")
        return True
    except Exception:
        return False


@lru_cache(maxsize=4)
def _download_rapidocr_modelscope_repo(repo_id: str) -> str:
    try:
        from modelscope import snapshot_download
    except Exception as exc:
        logger.warning("ModelScope not available; cannot download RapidOCR models (%s)", exc)
        return ""

    try:
        return snapshot_download(repo_id=repo_id)
    except Exception as exc:
        logger.warning("Failed to download RapidOCR models from ModelScope (repo_id=%s): %s", repo_id, exc)
        return ""


def _resolve_rapidocr_model_paths(
    *,
    det_model_path: str,
    rec_model_path: str,
    cls_model_path: str,
    modelscope_enabled: bool,
    modelscope_repo_id: str,
) -> tuple[str, str, str]:
    det = (det_model_path or "").strip()
    rec = (rec_model_path or "").strip()
    cls = (cls_model_path or "").strip()

    if det or rec or cls:
        return det, rec, cls

    if not modelscope_enabled:
        return "", "", ""

    repo_id = (modelscope_repo_id or "").strip() or "RapidAI/RapidOCR"
    download_path = _download_rapidocr_modelscope_repo(repo_id)
    if not download_path:
        return "", "", ""

    return (
        os.path.join(download_path, "onnx", "PP-OCRv5", "det", "ch_PP-OCRv5_server_det.onnx"),
        os.path.join(download_path, "onnx", "PP-OCRv5", "rec", "ch_PP-OCRv5_rec_server_infer.onnx"),
        os.path.join(download_path, "onnx", "PP-OCRv4", "cls", "ch_ppocr_mobile_v2.0_cls_infer.onnx"),
    )


@lru_cache(maxsize=4)
def _get_docling_converter(
    *,
    ocr_enabled: bool,
    ocr_force_full_page_ocr: bool,
    ocr_langs: str,
    ocr_det_model_path: str,
    ocr_rec_model_path: str,
    ocr_cls_model_path: str,
    ocr_modelscope_enabled: bool,
    ocr_modelscope_repo_id: str,
    picture_description_enabled: bool,
    picture_description_url: str,
    picture_description_api_key: str,
    picture_description_model: str,
    picture_description_prompt: str,
    picture_description_timeout_sec: float,
    picture_description_concurrency: int,
    picture_description_params_json: str,
):
    """Build and cache DocumentConverter with pipeline options."""
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            PictureDescriptionApiOptions,
            RapidOcrOptions,
        )
        from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
    except ImportError as exc:
        raise ParseError("Docling not installed", retryable=False) from exc

    pipeline_options = PdfPipelineOptions()

    # OCR configuration (RapidOCR, CPU optimized)
    if ocr_enabled:
        pipeline_options.do_ocr = True
        if _rapidocr_is_available():
            langs = _parse_csv_list(ocr_langs) or ["english", "chinese"]
            det, rec, cls = _resolve_rapidocr_model_paths(
                det_model_path=ocr_det_model_path,
                rec_model_path=ocr_rec_model_path,
                cls_model_path=ocr_cls_model_path,
                modelscope_enabled=bool(ocr_modelscope_enabled),
                modelscope_repo_id=ocr_modelscope_repo_id,
            )
            det_exists = bool(det and os.path.exists(det))
            rec_exists = bool(rec and os.path.exists(rec))
            cls_exists = bool(cls and os.path.exists(cls))

            if det_exists and rec_exists and cls_exists:
                try:
                    pipeline_options.ocr_options = RapidOcrOptions(
                        det_model_path=det,
                        rec_model_path=rec,
                        cls_model_path=cls,
                        lang=langs,
                        force_full_page_ocr=bool(ocr_force_full_page_ocr),
                    )
                    logger.info("Docling OCR enabled with RapidOCR (custom models)")
                except Exception as exc:
                    logger.warning(
                        "RapidOcrOptions does not support custom model paths; falling back to defaults (%s)",
                        exc,
                    )
                    pipeline_options.ocr_options = RapidOcrOptions(
                        lang=langs,
                        force_full_page_ocr=bool(ocr_force_full_page_ocr),
                    )
            elif det or rec or cls:
                logger.warning(
                    "RapidOCR model paths provided but invalid; falling back to RapidOCR defaults. "
                    "det=%s (exists=%s) rec=%s (exists=%s) cls=%s (exists=%s)",
                    det,
                    det_exists,
                    rec,
                    rec_exists,
                    cls,
                    cls_exists,
                )
                pipeline_options.ocr_options = RapidOcrOptions(
                    lang=langs,
                    force_full_page_ocr=bool(ocr_force_full_page_ocr),
                )
            else:
                pipeline_options.ocr_options = RapidOcrOptions(
                    lang=langs,
                    force_full_page_ocr=bool(ocr_force_full_page_ocr),
                )
                logger.info("Docling OCR enabled with RapidOCR")
        else:
            logger.warning("RapidOCR not available; using Docling default OCR")
    else:
        pipeline_options.do_ocr = False

    # Picture description configuration (remote VLM)
    if picture_description_enabled:
        url = _normalize_chat_completions_url(picture_description_url)
        api_key = (picture_description_api_key or "").strip()
        model = (picture_description_model or "").strip()

        if not url or not api_key or not model:
            logger.warning(
                "Picture description enabled but missing config; disabling. "
                "Required: DOCLING_PICTURE_DESCRIPTION_URL/API_KEY/MODEL"
            )
            pipeline_options.do_picture_description = False
            pipeline_options.generate_picture_images = False
        else:
            try:
                pipeline_options.generate_picture_images = True
                pipeline_options.do_picture_description = True
                pipeline_options.enable_remote_services = True

                headers = {"Authorization": f"Bearer {api_key}"}
                # Merge extra params first, then force model to prevent override
                params: dict[str, Any] = {}
                extra_params_raw = (picture_description_params_json or "").strip()
                if extra_params_raw:
                    try:
                        extra_params = json.loads(extra_params_raw)
                        if isinstance(extra_params, dict):
                            if "model" in extra_params:
                                logger.warning("DOCLING_PICTURE_DESCRIPTION_PARAMS_JSON contains 'model'; ignoring")
                                extra_params.pop("model", None)
                            params.update(extra_params)
                    except Exception:
                        logger.warning("Invalid DOCLING_PICTURE_DESCRIPTION_PARAMS_JSON; ignoring")
                # Force model after merge to prevent override
                params["model"] = model

                pipeline_options.picture_description_options = PictureDescriptionApiOptions(
                    url=url,
                    headers=headers,
                    params=params,
                    prompt=(picture_description_prompt or "").strip() or "Describe this image.",
                    timeout=max(1.0, float(picture_description_timeout_sec)),
                    concurrency=max(1, int(picture_description_concurrency)),
                    provenance="openai_compat_api",
                )
                logger.info("Docling picture description enabled (model: %s)", model)
            except Exception as e:
                logger.warning("Failed to configure picture description: %s; disabling", e)
                pipeline_options.do_picture_description = False
                pipeline_options.generate_picture_images = False
                pipeline_options.enable_remote_services = False
    else:
        pipeline_options.do_picture_description = False

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
    )


def _is_non_retryable_error(message: str) -> bool:
    """Check if error indicates a non-retryable condition."""
    msg = (message or "").lower()
    return any(
        needle in msg
        for needle in ("max_num_pages", "max_file_size", "file too large", "too large", "page_range")
    )


def parse_document(file_path: str, content_type: str, *, max_pages: int | None = None) -> str:
    """Parse document and extract text using Docling.

    Args:
        file_path: Path to the file to parse
        content_type: MIME type of the file
        max_pages: Maximum pages for PDF files (defaults to settings)

    Returns:
        Extracted text content

    Raises:
        ParseError: If parsing fails
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ParseError(f"Unsupported file type: {ext}", retryable=False)

    settings = get_settings()
    resolved_max_pages = int(max_pages) if max_pages is not None else int(settings.docling_max_pdf_pages)
    resolved_max_file_size = int(settings.docling_max_file_size_mb) * 1024 * 1024

    try:
        converter = _get_docling_converter(
            ocr_enabled=bool(settings.docling_ocr_enabled),
            ocr_force_full_page_ocr=bool(settings.docling_ocr_force_full_page_ocr),
            ocr_langs=str(settings.docling_ocr_langs or ""),
            ocr_det_model_path=str(settings.docling_ocr_det_model_path or ""),
            ocr_rec_model_path=str(settings.docling_ocr_rec_model_path or ""),
            ocr_cls_model_path=str(settings.docling_ocr_cls_model_path or ""),
            ocr_modelscope_enabled=bool(settings.docling_ocr_modelscope_enabled),
            ocr_modelscope_repo_id=str(settings.docling_ocr_modelscope_repo_id or ""),
            picture_description_enabled=bool(settings.docling_picture_description_enabled),
            picture_description_url=str(settings.docling_picture_description_url or ""),
            picture_description_api_key=str(settings.docling_picture_description_api_key or ""),
            picture_description_model=str(settings.docling_picture_description_model or ""),
            picture_description_prompt=str(settings.docling_picture_description_prompt or ""),
            picture_description_timeout_sec=float(settings.docling_picture_description_timeout_sec),
            picture_description_concurrency=int(settings.docling_picture_description_concurrency),
            picture_description_params_json=str(settings.docling_picture_description_params_json or ""),
        )

        result = converter.convert(
            file_path,
            max_num_pages=resolved_max_pages,
            max_file_size=resolved_max_file_size,
        )
        text = result.document.export_to_markdown()
        return text.strip() if text else ""
    except Exception as e:
        retryable = not _is_non_retryable_error(str(e))
        logger.exception("Document parsing failed: %s", file_path)
        raise ParseError(f"Parsing failed: {e}", retryable=retryable) from e
