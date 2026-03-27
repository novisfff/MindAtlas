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
from app.system_settings.runtime_config_service import (
    resolve_runtime_document_parsing_config,
    resolve_runtime_storage_config,
)

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


def _configure_ocr(
    pipeline_options,
    RapidOcrOptions,
    *,
    enabled: bool,
    force_full_page_ocr: bool,
    langs: str,
    det_model_path: str,
    rec_model_path: str,
    cls_model_path: str,
    modelscope_enabled: bool,
    modelscope_repo_id: str,
) -> None:
    """Configure OCR options on the pipeline."""
    if not enabled:
        pipeline_options.do_ocr = False
        return

    pipeline_options.do_ocr = True
    if not _rapidocr_is_available():
        logger.warning("RapidOCR not available; using Docling default OCR")
        return

    parsed_langs = _parse_csv_list(langs) or ["english", "chinese"]
    det, rec, cls = _resolve_rapidocr_model_paths(
        det_model_path=det_model_path,
        rec_model_path=rec_model_path,
        cls_model_path=cls_model_path,
        modelscope_enabled=bool(modelscope_enabled),
        modelscope_repo_id=modelscope_repo_id,
    )
    det_exists = bool(det and os.path.exists(det))
    rec_exists = bool(rec and os.path.exists(rec))
    cls_exists = bool(cls and os.path.exists(cls))

    if det_exists and rec_exists and cls_exists:
        try:
            pipeline_options.ocr_options = RapidOcrOptions(
                det_model_path=det, rec_model_path=rec, cls_model_path=cls,
                lang=parsed_langs, force_full_page_ocr=bool(force_full_page_ocr),
            )
            logger.info("Docling OCR enabled with RapidOCR (custom models)")
            return
        except Exception as exc:
            logger.warning(
                "RapidOcrOptions does not support custom model paths; falling back to defaults (%s)", exc,
            )
    elif det or rec or cls:
        logger.warning(
            "RapidOCR model paths provided but invalid; falling back to RapidOCR defaults. "
            "det=%s (exists=%s) rec=%s (exists=%s) cls=%s (exists=%s)",
            det, det_exists, rec, rec_exists, cls, cls_exists,
        )

    pipeline_options.ocr_options = RapidOcrOptions(
        lang=parsed_langs, force_full_page_ocr=bool(force_full_page_ocr),
    )
    logger.info("Docling OCR enabled with RapidOCR")


def _configure_picture_description(
    pipeline_options,
    PictureDescriptionApiOptions,
    *,
    enabled: bool,
    url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout_sec: float,
    concurrency: int,
    params_json: str,
) -> None:
    """Configure picture description options on the pipeline."""
    if not enabled:
        pipeline_options.do_picture_description = False
        return

    resolved_url = _normalize_chat_completions_url(url)
    resolved_key = (api_key or "").strip()
    resolved_model = (model or "").strip()

    if not resolved_url or not resolved_key or not resolved_model:
        logger.warning(
            "Picture description enabled but missing config; disabling. "
            "Required: DOCLING_PICTURE_DESCRIPTION_URL/API_KEY/MODEL"
        )
        pipeline_options.do_picture_description = False
        pipeline_options.generate_picture_images = False
        return

    try:
        pipeline_options.generate_picture_images = True
        pipeline_options.do_picture_description = True
        pipeline_options.enable_remote_services = True

        headers = {"Authorization": f"Bearer {resolved_key}"}
        params: dict[str, Any] = {}
        extra_params_raw = (params_json or "").strip()
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
        params["model"] = resolved_model

        pipeline_options.picture_description_options = PictureDescriptionApiOptions(
            url=resolved_url, headers=headers, params=params,
            prompt=(prompt or "").strip() or "Describe this image.",
            timeout=max(1.0, float(timeout_sec)),
            concurrency=max(1, int(concurrency)),
            provenance="openai_compat_api",
        )
        logger.info("Docling picture description enabled (model: %s)", resolved_model)
    except Exception as e:
        logger.warning("Failed to configure picture description: %s; disabling", e)
        pipeline_options.do_picture_description = False
        pipeline_options.generate_picture_images = False
        pipeline_options.enable_remote_services = False


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

    _configure_ocr(
        pipeline_options, RapidOcrOptions,
        enabled=ocr_enabled,
        force_full_page_ocr=ocr_force_full_page_ocr,
        langs=ocr_langs,
        det_model_path=ocr_det_model_path,
        rec_model_path=ocr_rec_model_path,
        cls_model_path=ocr_cls_model_path,
        modelscope_enabled=ocr_modelscope_enabled,
        modelscope_repo_id=ocr_modelscope_repo_id,
    )
    _configure_picture_description(
        pipeline_options, PictureDescriptionApiOptions,
        enabled=picture_description_enabled,
        url=picture_description_url,
        api_key=picture_description_api_key,
        model=picture_description_model,
        prompt=picture_description_prompt,
        timeout_sec=picture_description_timeout_sec,
        concurrency=picture_description_concurrency,
        params_json=picture_description_params_json,
    )

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
    document_config = resolve_runtime_document_parsing_config()
    storage_config = resolve_runtime_storage_config()
    resolved_max_pages = int(max_pages) if max_pages is not None else int(storage_config.max_pdf_pages)
    resolved_max_file_size = int(storage_config.max_file_size_mb) * 1024 * 1024

    try:
        converter = _get_docling_converter(
            ocr_enabled=bool(document_config.ocr_enabled),
            ocr_force_full_page_ocr=bool(settings.docling_ocr_force_full_page_ocr),
            ocr_langs=str(document_config.ocr_langs or ""),
            ocr_det_model_path=str(settings.docling_ocr_det_model_path or ""),
            ocr_rec_model_path=str(settings.docling_ocr_rec_model_path or ""),
            ocr_cls_model_path=str(settings.docling_ocr_cls_model_path or ""),
            ocr_modelscope_enabled=bool(settings.docling_ocr_modelscope_enabled),
            ocr_modelscope_repo_id=str(settings.docling_ocr_modelscope_repo_id or ""),
            picture_description_enabled=bool(document_config.picture_description_enabled),
            picture_description_url=str(document_config.picture_description_url or ""),
            picture_description_api_key=str(document_config.picture_description_api_key or ""),
            picture_description_model=str(document_config.picture_description_model or ""),
            picture_description_prompt=str(document_config.picture_description_prompt or ""),
            picture_description_timeout_sec=float(document_config.picture_description_timeout_sec),
            picture_description_concurrency=int(settings.docling_picture_description_concurrency),
            picture_description_params_json=str(document_config.picture_description_params_json or ""),
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
