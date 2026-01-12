"""Entry 相关工具函数"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import UUID

from langchain_core.tools import tool

from app.ai_provider.crypto import decrypt_api_key
from app.ai_provider.models import AiProvider
from app.entry.models import Entry, TimeMode
from app.entry_type.models import EntryType
from app.tag.models import Tag


def _get_db():
    """获取数据库会话 - 由 agent 注入"""
    from app.assistant.tools._context import get_current_db
    return get_current_db()


@dataclass(frozen=True)
class _OpenAiConfig:
    api_key: str
    base_url: str
    model: str


def _get_ai_config(db) -> _OpenAiConfig | None:
    """获取激活的 AI Provider 配置"""
    provider = (
        db.query(AiProvider)
        .filter(AiProvider.is_active.is_(True))
        .first()
    )
    if not provider:
        return None
    try:
        api_key = decrypt_api_key(provider.api_key_encrypted)
    except Exception:
        return None
    return _OpenAiConfig(
        api_key=api_key,
        base_url=provider.base_url,
        model=provider.model,
    )


def _build_api_url(base_url: str, endpoint: str) -> str:
    """构建 API URL"""
    base = (base_url or "").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base + endpoint


def _build_create_entry_prompt(
    *,
    raw_content: str,
    enabled_types: list[EntryType],
    existing_tags: list[str],
    default_type_code: str,
    current_date: str,
) -> str:
    """构建智能创建记录的中文 Prompt"""
    types_text = "\n".join(
        [
            f"- {((t.code or '').strip())}: {((t.name or '').strip())}"
            for t in enabled_types
            if (t.code or "").strip()
        ]
    )
    tags_text = ", ".join([t for t in existing_tags if isinstance(t, str) and t.strip()])
    allowed_codes = ", ".join(
        [f'"{((t.code or "").strip())}"' for t in enabled_types if (t.code or "").strip()]
    )

    return f"""你是 MindAtlas 的"智能创建记录"助手。你的任务是把用户提供的原始内容整理成一条可保存的记录。

【当前日期】
{current_date}

【目标】
1) 生成合适的标题 title（简洁、准确，不超过 30 个字）
2) 生成正文 content（可用 Markdown，但禁止出现一级标题：禁止任何以"# "开头的行；如需标题请从"## "开始）
3) 选择记录类型 type_code（必须且只能从下方"可用类型列表"的 code 中选择）
4) 生成标签 tags（优先复用"可用标签列表"，大小写不敏感匹配；最多允许新增 5 个新标签）
5) 识别时间信息并设置 time_mode 和对应的日期字段

【时间模式规则】
根据用户内容中的时间信息，设置以下字段：
- time_mode: 时间模式，必须是以下之一：
  - "NONE": 无时间信息（默认）
  - "POINT": 单一时间点（如"今天"、"2024-01-15"、"昨天下午"）
  - "RANGE": 时间范围（如"这周"、"上个月"、"1月1日到1月15日"）
- time_at: 当 time_mode="POINT" 时，填写具体日期（格式：YYYY-MM-DD）
- time_from / time_to: 当 time_mode="RANGE" 时，填写起止日期（格式：YYYY-MM-DD）

时间识别示例：
- "今天学了 Python" → time_mode="POINT", time_at="{current_date}"
- "昨天开会" → time_mode="POINT", time_at="昨天对应的日期"
- "这周完成了项目" → time_mode="RANGE", time_from="本周一", time_to="本周日"
- "上个月的总结" → time_mode="RANGE", time_from="上月1日", time_to="上月最后一天"
- "学习笔记"（无时间信息）→ time_mode="NONE"

【可用类型列表（仅允许从这些 code 中选择）】
{types_text}

【可用标签列表（优先复用；大小写不敏感匹配；输出时尽量返回列表中的原始写法）】
{tags_text}

【强约束】
- 只输出一个 JSON 对象，不要输出任何 Markdown、解释文字、代码块围栏。
- JSON schema: {{"title": string, "content": string, "type_code": string, "tags": string[], "time_mode": string, "time_at": string|null, "time_from": string|null, "time_to": string|null}}
- type_code 必须是以下之一：[{allowed_codes}]
- time_mode 必须是以下之一：["NONE", "POINT", "RANGE"]
- 日期格式统一使用 YYYY-MM-DD
- 当无法判断类型时，使用默认类型："{(default_type_code or '').strip()}"
- tags 中每个元素是纯标签名字符串（不要带 # 前缀），去重后输出。
- 不要在 content 中编造用户未提供的信息；可以整理结构、修正错别字、增强可读性，但不要扩写事实细节。

【用户原始内容】
{(raw_content or '').strip()}
"""


def _call_ai_for_entry_creation(cfg: _OpenAiConfig, prompt: str) -> str | None:
    """调用 AI 生成记录结构化数据"""
    url = _build_api_url(cfg.base_url, "/chat/completions")
    body = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    req = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {cfg.api_key}",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
    except URLError:
        return None
    except Exception:
        return None
    try:
        return data.decode("utf-8")
    except Exception:
        return None


def _parse_json_from_text(text: str) -> Any:
    """从文本中提取 JSON 对象"""
    value = (text or "").strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        pass
    start = value.find("{")
    end = value.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(value[start : end + 1])
    except Exception:
        return None


def _parse_ai_response(raw: str | None) -> dict[str, Any] | None:
    """解析 AI 响应，提取结构化数据"""
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        content = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
    except Exception:
        return None

    result = _parse_json_from_text(content)
    if not isinstance(result, dict):
        return None

    title = result.get("title")
    content_value = result.get("content")
    type_code = result.get("type_code") or result.get("typeCode")
    tags = result.get("tags")

    title_out = str(title).strip() if isinstance(title, str) and title.strip() else None
    content_out = str(content_value).strip() if isinstance(content_value, str) and content_value.strip() else None
    type_code_out = str(type_code).strip() if isinstance(type_code, str) and type_code.strip() else None

    tags_out: list[str] = []
    if isinstance(tags, list):
        for t in tags:
            v = str(t).strip()
            if v:
                tags_out.append(v)
    elif isinstance(tags, str) and tags.strip():
        parts = re.split(r"[,\n;，；]+", tags)
        for p in parts:
            v = (p or "").strip()
            if v:
                tags_out.append(v)

    # 解析时间字段
    time_mode = result.get("time_mode") or result.get("timeMode")
    time_at = result.get("time_at") or result.get("timeAt")
    time_from = result.get("time_from") or result.get("timeFrom")
    time_to = result.get("time_to") or result.get("timeTo")

    time_mode_out = str(time_mode).strip().upper() if isinstance(time_mode, str) and time_mode.strip() else "NONE"
    if time_mode_out not in ("NONE", "POINT", "RANGE"):
        time_mode_out = "NONE"

    time_at_out = str(time_at).strip() if isinstance(time_at, str) and time_at.strip() else None
    time_from_out = str(time_from).strip() if isinstance(time_from, str) and time_from.strip() else None
    time_to_out = str(time_to).strip() if isinstance(time_to, str) and time_to.strip() else None

    return {
        "title": title_out,
        "content": content_out,
        "type_code": type_code_out,
        "tags": tags_out,
        "time_mode": time_mode_out,
        "time_at": time_at_out,
        "time_from": time_from_out,
        "time_to": time_to_out,
    }


@tool
def search_entries(
    keyword: Optional[str] = None,
    type_code: Optional[str] = None,
    tag_names: Optional[list[str]] = None,
    limit: int = 10
) -> str:
    """搜索用户的记录（Entry）。

    Args:
        keyword: 搜索关键词，匹配标题和内容
        type_code: 记录类型编码，如 knowledge, project, competition
        tag_names: 标签名称列表（匹配任意一个标签）
        limit: 返回结果数量限制，默认10条

    Returns:
        匹配的记录列表（JSON数组），包含id、标题、类型、摘要等信息
    """
    db = _get_db()
    query = db.query(Entry)

    # limit 边界校验
    if limit < 1:
        limit = 10
    elif limit > 100:
        limit = 100

    if keyword:
        kw = f"%{keyword}%"
        query = query.filter(
            Entry.title.ilike(kw) | Entry.content.ilike(kw)
        )

    if type_code:
        query = query.join(EntryType).filter(EntryType.code == type_code)

    if tag_names:
        cleaned = [t.strip() for t in tag_names if isinstance(t, str) and t.strip()]
        if cleaned:
            query = query.join(Entry.tags).filter(Tag.name.in_(cleaned)).distinct()

    entries = query.order_by(Entry.updated_at.desc()).limit(limit).all()

    if not entries:
        return json.dumps([], ensure_ascii=False, indent=2)

    results = []
    for e in entries:
        results.append({
            "id": str(e.id),
            "title": e.title,
            "type": e.type.name if e.type else "未知",
            "summary": (e.summary or e.content or "")[:100],
            "tags": [t.name for t in e.tags],
        })

    return json.dumps(results, ensure_ascii=False, indent=2)


def _remove_level1_headings(md: str) -> str:
    """移除 Markdown 中的一级标题，转换为二级标题"""
    lines = (md or "").splitlines()
    if not lines:
        return md or ""
    out: list[str] = []
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if not in_code:
            line = re.sub(r"^(\s*)#\s+", r"\1## ", line)
        out.append(line)
    return "\n".join(out)


def _normalize_tags(db, tag_names: list[str], existing_tags: list[Tag] | None = None) -> list[Tag]:
    """标签归一化与复用（大小写不敏感匹配）

    Args:
        db: 数据库会话
        tag_names: AI 建议的标签名列表
        existing_tags: 已有标签列表（可选，避免重复查询）
    """
    if existing_tags is None:
        existing_tags = db.query(Tag).all()
    existing_by_lower: dict[str, Tag] = {}
    for t in existing_tags:
        n = (t.name or "").strip()
        if n:
            existing_by_lower[n.lower()] = t

    out: list[Tag] = []
    used_lower: set[str] = set()
    new_created = 0

    for raw in (tag_names or [])[:50]:
        name = (str(raw) if raw is not None else "").strip()
        if not name:
            continue
        name = re.sub(r"^[#]+", "", name).strip()
        if not name:
            continue

        key = name.lower()
        if key in used_lower:
            continue
        used_lower.add(key)

        hit = existing_by_lower.get(key)
        if hit:
            out.append(hit)
            continue

        if new_created >= 5:
            continue

        if len(name) > 128:
            name = name[:128].rstrip()
        if not name:
            continue

        tag = Tag(name=name)
        db.add(tag)
        existing_by_lower[key] = tag
        out.append(tag)
        new_created += 1

    return out


@tool
def get_entry_detail(entry_id: str) -> str:
    """获取记录的详细信息。

    Args:
        entry_id: 记录的 UUID

    Returns:
        记录的完整信息，包含内容、标签、关联等
    """
    db = _get_db()
    try:
        uid = UUID(entry_id)
    except ValueError:
        raise ValueError(f"无效的记录ID: {entry_id}")

    entry = db.query(Entry).filter(Entry.id == uid).first()
    if not entry:
        raise ValueError(f"未找到记录: {entry_id}")

    result = {
        "id": str(entry.id),
        "title": entry.title,
        "content": entry.content or "",
        "type": entry.type.name if entry.type else "未知",
        "type_code": entry.type.code if entry.type else "",
        "summary": entry.summary or "",
        "tags": [t.name for t in entry.tags],
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def create_entry(
    raw_content: str,
) -> str:
    """智能创建新的记录（只需提供原始内容，AI自动生成标题/内容/标签/类型）。

    Args:
        raw_content: 原始输入内容（可以是随手记录/片段/要点，支持 Markdown）

    Returns:
        创建成功的记录信息（JSON格式，包含id、标题、类型等）
    """
    db = _get_db()

    raw = (raw_content or "").strip()
    if not raw:
        raise ValueError("raw_content 不能为空")

    # 获取可用类型
    enabled_types = (
        db.query(EntryType)
        .filter(EntryType.enabled.is_(True))
        .all()
    )
    if not enabled_types:
        raise ValueError("没有可用的记录类型")

    # 选择默认类型
    def _pick_default_type(types: list) -> EntryType:
        preferred = ["knowledge", "project", "competition"]
        for code in preferred:
            for t in types:
                if (t.code or "").strip().lower() == code:
                    return t
        return types[0]

    # 清理标题
    def _clean_title(text: str) -> str:
        v = (text or "").strip()
        v = re.sub(r"^[#>\-\*\s]+", "", v).strip()
        if len(v) > 255:
            v = v[:255].rstrip()
        return v

    # 从文本提取首行作为临时标题
    def _first_non_empty_line(text: str) -> str:
        for line in (text or "").splitlines():
            v = line.strip()
            if v:
                return v
        return ""

    # 从 Markdown 提取标题
    def _infer_title_from_markdown(md: str) -> str:
        for line in (md or "").splitlines():
            m = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
            if m:
                return _clean_title(m.group(1))
        return ""

    default_type = _pick_default_type(enabled_types)
    provisional_title = _clean_title(_first_non_empty_line(raw)) or "未命名记录"

    # 预先查询所有标签（复用于 Prompt 和归一化）
    all_existing_tags = db.query(Tag).all()

    # 调用 AI 生成结构化数据
    cfg = _get_ai_config(db)
    ai_payload: dict[str, Any] | None = None
    if cfg:
        # 限制 Prompt 中的标签数量（最多 100 个），避免 token 膨胀
        tag_names = [t.name for t in all_existing_tags if (t.name or "").strip()][:100]
        prompt = _build_create_entry_prompt(
            raw_content=raw,
            enabled_types=enabled_types,
            existing_tags=tag_names,
            default_type_code=(default_type.code or "").strip(),
            current_date=date.today().isoformat(),
        )
        raw_ai = _call_ai_for_entry_creation(cfg, prompt)
        ai_payload = _parse_ai_response(raw_ai)

    # 提取 AI 结果
    ai_title = (ai_payload or {}).get("title") if isinstance(ai_payload, dict) else None
    ai_content = (ai_payload or {}).get("content") if isinstance(ai_payload, dict) else None
    ai_type_code = (ai_payload or {}).get("type_code") if isinstance(ai_payload, dict) else None
    ai_tags = (ai_payload or {}).get("tags") if isinstance(ai_payload, dict) else None
    ai_time_mode = (ai_payload or {}).get("time_mode") if isinstance(ai_payload, dict) else "NONE"
    ai_time_at = (ai_payload or {}).get("time_at") if isinstance(ai_payload, dict) else None
    ai_time_from = (ai_payload or {}).get("time_from") if isinstance(ai_payload, dict) else None
    ai_time_to = (ai_payload or {}).get("time_to") if isinstance(ai_payload, dict) else None

    # 确定最终内容
    content_candidate = (ai_content or "").strip() if isinstance(ai_content, str) else ""
    final_content = content_candidate or raw
    final_content = _remove_level1_headings(final_content).strip()

    # 确定最终标题
    title_candidate = (ai_title or "").strip() if isinstance(ai_title, str) else ""
    title_candidate = _clean_title(title_candidate)
    title_from_md = _infer_title_from_markdown(final_content)
    final_title = title_candidate or title_from_md or provisional_title or "未命名记录"

    # 确定类型（AI 选择 > 默认）
    enabled_type_by_code = {
        ((t.code or "").strip().lower()): t
        for t in enabled_types
        if (t.code or "").strip()
    }
    req_code = (str(ai_type_code).strip() if ai_type_code is not None else "").strip()
    chosen_type = enabled_type_by_code.get(req_code.lower()) if req_code else None
    if not chosen_type:
        chosen_type = default_type

    # 处理标签（使用归一化函数，复用已查询的标签列表）
    tag_objects: list[Tag] = []
    if isinstance(ai_tags, list):
        suggested = [str(t).strip() for t in ai_tags if str(t).strip()]
        tag_objects = _normalize_tags(db, suggested, all_existing_tags)
    elif isinstance(ai_tags, str) and ai_tags.strip():
        parts = re.split(r"[,\n;，；]+", ai_tags)
        suggested = [p.strip() for p in parts if p and p.strip()]
        tag_objects = _normalize_tags(db, suggested, all_existing_tags)

    # 处理时间字段
    def _parse_date(date_str: str | None):
        """解析日期字符串为 datetime"""
        if not date_str:
            return None
        from datetime import datetime
        try:
            return datetime.strptime(date_str.strip(), "%Y-%m-%d")
        except ValueError:
            return None

    final_time_mode = TimeMode.NONE
    final_time_at = None
    final_time_from = None
    final_time_to = None

    if ai_time_mode == "POINT" and ai_time_at:
        parsed = _parse_date(ai_time_at)
        if parsed:
            final_time_mode = TimeMode.POINT
            final_time_at = parsed
    elif ai_time_mode == "RANGE" and (ai_time_from or ai_time_to):
        from_parsed = _parse_date(ai_time_from)
        to_parsed = _parse_date(ai_time_to)
        if from_parsed or to_parsed:
            final_time_mode = TimeMode.RANGE
            final_time_from = from_parsed
            final_time_to = to_parsed

    # 创建记录
    entry = Entry(
        title=final_title,
        content=final_content,
        summary=(final_content or "")[:200] or None,
        type_id=chosen_type.id,
        time_mode=final_time_mode,
        time_at=final_time_at,
        time_from=final_time_from,
        time_to=final_time_to,
    )
    entry.tags = tag_objects
    db.add(entry)
    db.commit()
    db.refresh(entry)

    result = {
        "id": str(entry.id),
        "title": entry.title,
        "type": entry.type.name if entry.type else "",
        "type_code": entry.type.code if entry.type else "",
        "tags": [t.name for t in entry.tags],
        "time_mode": entry.time_mode.value if entry.time_mode else "NONE",
        "time_at": entry.time_at.strftime("%Y-%m-%d") if entry.time_at else None,
        "time_from": entry.time_from.strftime("%Y-%m-%d") if entry.time_from else None,
        "time_to": entry.time_to.strftime("%Y-%m-%d") if entry.time_to else None,
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
    }
    return json.dumps(result, ensure_ascii=False, indent=2)
