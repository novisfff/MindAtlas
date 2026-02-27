from __future__ import annotations

from app.assistant.workflow.validation.contracts import (
    _IF_ELSE_HANDLE_RE,
    _IF_ELSE_LEGACY_OPERATOR_MAP,
)
from app.assistant.workflow.validation.rules.common import cfg_get

def normalize_if_else_operator(raw: object) -> str:
    op = str(raw or "is").strip().lower()
    if not op:
        return "is"
    return _IF_ELSE_LEGACY_OPERATOR_MAP.get(op, op)

def normalize_if_else_handle(raw: object) -> str:
    handle = str(raw or "").strip()
    if handle == "default":
        return "else"
    return handle

def normalize_if_else_config(cfg: dict) -> dict[str, object]:
    else_handle = normalize_if_else_handle(cfg_get(cfg, "else_handle", "elseHandle", default="else"))
    if not else_handle or not _IF_ELSE_HANDLE_RE.fullmatch(else_handle):
        else_handle = "else"

    branches_raw = cfg_get(cfg, "branches", default=None)
    branches: list[dict[str, object]] = []

    if isinstance(branches_raw, list) and branches_raw:
        for idx, branch in enumerate(branches_raw, start=1):
            if not isinstance(branch, dict):
                continue
            branch_id = normalize_if_else_handle(branch.get("id"))
            if not branch_id or not _IF_ELSE_HANDLE_RE.fullmatch(branch_id):
                branch_id = f"if_{idx}"
            logic = str(branch.get("logic") or "and").strip().lower()
            if logic not in {"and", "or"}:
                logic = "and"
            label = str(branch.get("label") or ("IF" if idx == 1 else f"ELIF {idx - 1}")).strip() or ("IF" if idx == 1 else f"ELIF {idx - 1}")
            conds: list[dict[str, object]] = []
            conds_raw = branch.get("conditions")
            if isinstance(conds_raw, list):
                for cond_idx, cond in enumerate(conds_raw, start=1):
                    if not isinstance(cond, dict):
                        continue
                    conds.append(
                        {
                            "id": str(cond.get("id") or f"{branch_id}_cond_{cond_idx}").strip() or f"{branch_id}_cond_{cond_idx}",
                            "variable": str(cond.get("variable") or "").strip(),
                            "operator": normalize_if_else_operator(cond.get("operator")),
                            "value": None if cond.get("value") is None else str(cond.get("value")),
                        }
                    )
            branches.append(
                {
                    "id": branch_id,
                    "label": label,
                    "logic": logic,
                    "conditions": conds,
                }
            )

    if not branches:
        # legacy format: conditions[] where each condition carries handle
        grouped: dict[str, list[dict[str, object]]] = {}
        handle_order: list[str] = []
        legacy_conds = cfg_get(cfg, "conditions", default=[])
        if isinstance(legacy_conds, list):
            for idx, cond in enumerate(legacy_conds, start=1):
                if not isinstance(cond, dict):
                    continue
                handle = normalize_if_else_handle(cond.get("handle"))
                if not handle:
                    continue
                if handle in {"else"}:
                    continue
                if not _IF_ELSE_HANDLE_RE.fullmatch(handle):
                    continue
                if handle not in grouped:
                    grouped[handle] = []
                    handle_order.append(handle)
                grouped[handle].append(
                    {
                        "id": str(cond.get("id") or f"{handle}_cond_{idx}").strip() or f"{handle}_cond_{idx}",
                        "variable": str(cond.get("variable") or "").strip(),
                        "operator": normalize_if_else_operator(cond.get("operator")),
                        "value": None if cond.get("value") is None else str(cond.get("value")),
                    }
                )
        for branch_idx, handle in enumerate(handle_order, start=1):
            branches.append(
                {
                    "id": handle,
                    "label": "IF" if branch_idx == 1 else f"ELIF {branch_idx - 1}",
                    "logic": "and",
                    "conditions": grouped.get(handle, []),
                }
            )

    return {"branches": branches, "else_handle": else_handle}
