from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.error import URLError
from urllib.request import Request, urlopen

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai_registry.runtime import resolve_openai_compat_config
from app.common.time import utcnow
from app.entry.models import Entry, TimeMode
from app.report.models import MonthlyReport, WeeklyReport
from app.report.schemas import (
    MonthlyReportListResponse,
    MonthlyReportResponse,
    WeeklyReportListResponse,
    WeeklyReportResponse,
)

logger = logging.getLogger(__name__)

MAX_RETRY_ATTEMPTS = 3
RETRY_DELAYS = [60, 300, 900]


@dataclass(frozen=True)
class _OpenAiConfig:
    api_key: str
    base_url: str
    model: str


class WeeklyReportService:
    def __init__(self, db: Session):
        self.db = db

    def get_latest(self) -> WeeklyReport | None:
        return (
            self.db.query(WeeklyReport)
            .order_by(WeeklyReport.week_start.desc())
            .first()
        )

    def list_reports(self, page: int = 0, size: int = 10) -> WeeklyReportListResponse:
        total = self.db.query(func.count(WeeklyReport.id)).scalar() or 0
        items = (
            self.db.query(WeeklyReport)
            .order_by(WeeklyReport.week_start.desc())
            .offset(page * size)
            .limit(size)
            .all()
        )
        return WeeklyReportListResponse(
            items=[WeeklyReportResponse.model_validate(r) for r in items],
            total=total,
            page=page,
            size=size,
        )

    def get_or_create_for_week(self, week_start: date) -> WeeklyReport:
        week_end = week_start + timedelta(days=6)
        report = self.db.query(WeeklyReport).filter_by(week_start=week_start).first()
        if report:
            return report

        entry_count = self._count_entries_for_week(week_start, week_end)
        report = WeeklyReport(
            week_start=week_start,
            week_end=week_end,
            entry_count=entry_count,
            status="pending",
        )
        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)
        return report

    def _count_entries_for_week(self, start: date, end: date) -> int:
        point_count = (
            self.db.query(func.count(Entry.id))
            .filter(
                Entry.time_mode == TimeMode.POINT,
                func.date(Entry.time_at) >= start,
                func.date(Entry.time_at) <= end,
            )
            .scalar() or 0
        )
        range_count = (
            self.db.query(func.count(Entry.id))
            .filter(
                Entry.time_mode == TimeMode.RANGE,
                func.date(Entry.time_from) >= start,
                func.date(Entry.time_from) <= end,
            )
            .scalar() or 0
        )
        return point_count + range_count

    @staticmethod
    def get_last_monday() -> date:
        today = date.today()
        days_since_monday = today.weekday()
        if days_since_monday == 0:
            days_since_monday = 7
        return today - timedelta(days=days_since_monday)

    def generate_report(self, report: WeeklyReport) -> WeeklyReport:
        """Generate AI content for a weekly report."""
        cfg = self._get_ai_config()
        if not cfg:
            report.status = "failed"
            report.last_error = "No AI provider configured"
            self.db.commit()
            return report

        report.status = "generating"
        report.attempts += 1
        self.db.commit()

        entries = self._get_entries_for_week(report.week_start, report.week_end)
        prompt = self._build_prompt(report.week_start, report.week_end, entries)

        try:
            raw = self._call_openai(cfg, prompt)
            content = self._parse_response(raw)
            report.content = content
            report.status = "completed"
            report.generated_at = utcnow()
            report.last_error = None
        except Exception as e:
            logger.exception("Failed to generate weekly report")
            report.status = "failed"
            report.last_error = str(e)

        self.db.commit()
        return report

    def _get_ai_config(self) -> _OpenAiConfig | None:
        try:
            cfg = resolve_openai_compat_config(self.db, component="assistant", model_type="llm")
        except Exception:
            return None
        if not cfg:
            return None
        return _OpenAiConfig(api_key=cfg.api_key, base_url=cfg.base_url, model=cfg.model)

    def _get_entries_for_week(self, start: date, end: date) -> list[Entry]:
        point_entries = (
            self.db.query(Entry)
            .filter(
                Entry.time_mode == TimeMode.POINT,
                func.date(Entry.time_at) >= start,
                func.date(Entry.time_at) <= end,
            )
            .all()
        )
        range_entries = (
            self.db.query(Entry)
            .filter(
                Entry.time_mode == TimeMode.RANGE,
                func.date(Entry.time_from) >= start,
                func.date(Entry.time_from) <= end,
            )
            .all()
        )
        return point_entries + range_entries

    def _build_prompt(self, week_start: date, week_end: date, entries: list[Entry]) -> str:
        if not entries:
            return self._build_empty_prompt(week_start, week_end)

        context_parts = []
        for entry in entries:
            time_str = self._format_entry_time(entry)
            tags_str = ", ".join(t.name for t in entry.tags) or "无标签"
            context_parts.append(
                f"- [{entry.type.name}] {entry.title} ({time_str})\n"
                f"  标签: {tags_str}\n"
                f"  摘要: {entry.summary or '无摘要'}"
            )
        entries_context = "\n".join(context_parts)

        return f"""你是 MindAtlas 的智能助手，负责生成用户的周报。

【时间范围】
{week_start} 至 {week_end}

【本周记录】
{entries_context}

【任务】
请基于以上记录，生成一份简洁的周报，包含以下三个部分：

1. **活动总结** (summary): 概括本周的主要活动和成果，100-200字
2. **行动建议** (suggestions): 基于记录内容给出 2-3 条具体建议
3. **趋势分析** (trends): 关注领域的变化，50-100字

【输出格式】
请以 JSON 格式输出：
{{"summary": "...", "suggestions": ["建议1", "建议2"], "trends": "..."}}
"""

    def _build_empty_prompt(self, week_start: date, week_end: date) -> str:
        return f"""本周（{week_start} 至 {week_end}）没有记录。

请生成一份简短的周报：
- summary: 简短说明本周无记录
- suggestions: 给出 1-2 条鼓励性建议
- trends: 可以留空

以 JSON 格式输出：{{"summary": "...", "suggestions": [...], "trends": ""}}
"""

    def _format_entry_time(self, entry: Entry) -> str:
        if entry.time_mode == TimeMode.POINT and entry.time_at:
            return entry.time_at.strftime("%Y-%m-%d")
        if entry.time_mode == TimeMode.RANGE and entry.time_from:
            return entry.time_from.strftime("%Y-%m-%d")
        return ""

    def _call_openai(self, cfg: _OpenAiConfig, prompt: str) -> str | None:
        base = cfg.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        url = base + "/chat/completions"

        body = {
            "model": cfg.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
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

        with urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8")

    def _parse_response(self, raw: str | None) -> dict:
        if not raw:
            return {"summary": None, "suggestions": [], "trends": None}

        payload = json.loads(raw)
        content = (
            payload.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        # Extract JSON from content
        result = self._extract_json(content)
        if not isinstance(result, dict):
            return {"summary": None, "suggestions": [], "trends": None}

        return {
            "summary": result.get("summary"),
            "suggestions": result.get("suggestions", []),
            "trends": result.get("trends"),
        }

    def _extract_json(self, text: str) -> dict | None:
        import re
        # Try to find JSON in markdown code block
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        # Try direct parse
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return None


class MonthlyReportService(WeeklyReportService):
    """Monthly report service - mirrors WeeklyReport patterns with input trimming."""

    MAX_ENTRIES_IN_PROMPT = 120
    MAX_PROMPT_CHARS = 14000
    MAX_TAGS_PER_ENTRY = 10
    MAX_SUMMARY_CHARS = 200

    def get_latest(self) -> MonthlyReport | None:
        return (
            self.db.query(MonthlyReport)
            .order_by(MonthlyReport.month_start.desc())
            .first()
        )

    def list_reports(self, page: int = 0, size: int = 10) -> MonthlyReportListResponse:
        total = self.db.query(func.count(MonthlyReport.id)).scalar() or 0
        items = (
            self.db.query(MonthlyReport)
            .order_by(MonthlyReport.month_start.desc())
            .offset(page * size)
            .limit(size)
            .all()
        )
        return MonthlyReportListResponse(
            items=[MonthlyReportResponse.model_validate(r) for r in items],
            total=total,
            page=page,
            size=size,
        )

    @staticmethod
    def get_last_month_start() -> date:
        """Get the first day of the previous month."""
        today = date.today()
        first_day_this_month = today.replace(day=1)
        last_day_last_month = first_day_this_month - timedelta(days=1)
        return last_day_last_month.replace(day=1)

    @staticmethod
    def _month_end_for(month_start: date) -> date:
        if month_start.day != 1:
            month_start = month_start.replace(day=1)
        if month_start.month == 12:
            next_month_start = date(month_start.year + 1, 1, 1)
        else:
            next_month_start = date(month_start.year, month_start.month + 1, 1)
        return next_month_start - timedelta(days=1)

    def get_or_create_for_month(self, month_start: date) -> MonthlyReport:
        month_end = self._month_end_for(month_start)
        report = self.db.query(MonthlyReport).filter_by(month_start=month_start).first()
        if report:
            return report

        entry_count = self._count_entries_for_month(month_start, month_end)
        report = MonthlyReport(
            month_start=month_start,
            month_end=month_end,
            entry_count=entry_count,
            status="pending",
        )
        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)
        return report

    def _count_entries_for_month(self, start: date, end: date) -> int:
        point_count = (
            self.db.query(func.count(Entry.id))
            .filter(
                Entry.time_mode == TimeMode.POINT,
                func.date(Entry.time_at) >= start,
                func.date(Entry.time_at) <= end,
            )
            .scalar() or 0
        )
        range_count = (
            self.db.query(func.count(Entry.id))
            .filter(
                Entry.time_mode == TimeMode.RANGE,
                func.date(Entry.time_from) >= start,
                func.date(Entry.time_from) <= end,
            )
            .scalar() or 0
        )
        return point_count + range_count

    def generate_report(self, report: MonthlyReport) -> MonthlyReport:
        """Generate AI content for a monthly report."""
        cfg = self._get_ai_config()
        if not cfg:
            report.status = "failed"
            report.last_error = "No AI provider configured"
            self.db.commit()
            return report

        report.status = "generating"
        report.attempts += 1
        self.db.commit()

        entries = self._get_entries_for_month(report.month_start, report.month_end)
        prompt = self._build_monthly_prompt(
            report.month_start, report.month_end, report.entry_count, entries
        )

        try:
            raw = self._call_openai(cfg, prompt)
            content = self._parse_response(raw)
            # Validate content has meaningful data
            if not content or not content.get("summary"):
                report.status = "failed"
                report.last_error = "AI returned empty or invalid content"
            else:
                report.content = content
                report.status = "completed"
                report.generated_at = utcnow()
                report.last_error = None
        except Exception as e:
            logger.exception("Failed to generate monthly report")
            report.status = "failed"
            report.last_error = str(e)

        self.db.commit()
        return report

    def _get_entries_for_month(self, start: date, end: date) -> list[Entry]:
        max_entries = self.MAX_ENTRIES_IN_PROMPT * 2
        point_entries = (
            self.db.query(Entry)
            .filter(
                Entry.time_mode == TimeMode.POINT,
                func.date(Entry.time_at) >= start,
                func.date(Entry.time_at) <= end,
            )
            .order_by(Entry.time_at.desc())
            .limit(max_entries)
            .all()
        )
        range_entries = (
            self.db.query(Entry)
            .filter(
                Entry.time_mode == TimeMode.RANGE,
                func.date(Entry.time_from) >= start,
                func.date(Entry.time_from) <= end,
            )
            .order_by(Entry.time_from.desc())
            .limit(max_entries)
            .all()
        )
        merged = point_entries + range_entries
        merged.sort(
            key=lambda e: (e.time_at or e.time_from or datetime.min),
            reverse=True,
        )
        return merged[:max_entries]

    @staticmethod
    def _clip(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 1)].rstrip() + "…"

    def _build_entries_context(self, entries: list[Entry]) -> str:
        trimmed = entries[: self.MAX_ENTRIES_IN_PROMPT]
        parts: list[str] = []
        for entry in trimmed:
            time_str = self._format_entry_time(entry)
            tag_names = [t.name for t in (entry.tags or [])]
            shown_tags = tag_names[: self.MAX_TAGS_PER_ENTRY]
            tags_str = ", ".join(shown_tags) if shown_tags else "无标签"
            if len(tag_names) > len(shown_tags):
                tags_str += f" 等{len(tag_names) - len(shown_tags)}个"

            summary = entry.summary or "无摘要"
            summary = self._clip(summary, self.MAX_SUMMARY_CHARS)

            parts.append(
                f"- [{entry.type.name}] {entry.title} ({time_str})\n"
                f"  标签: {tags_str}\n"
                f"  摘要: {summary}"
            )
        body = "\n".join(parts).strip()
        return self._clip(body, self.MAX_PROMPT_CHARS)

    def _build_monthly_prompt(
        self, month_start: date, month_end: date, entry_count: int, entries: list[Entry]
    ) -> str:
        if not entries:
            return self._build_empty_monthly_prompt(month_start, month_end)

        entries_context = self._build_entries_context(entries)

        prompt = f"""你是 MindAtlas 的智能助手，负责生成用户的月报。

【时间范围】
{month_start} 至 {month_end}

【数据概览】
- 本月记录条数：{entry_count}
- 提示：下方仅提供部分记录作为上下文

【本月记录（节选）】
{entries_context}

【任务】
请基于以上记录，生成一份简洁的月报，包含以下三个部分：

1. **月度总结** (summary): 概括本月的主要活动、进展与成果，150-300字
2. **下月建议** (suggestions): 给出 3-5 条可执行的建议
3. **趋势与反思** (trends): 提炼 2-3 个趋势/反思点，80-150字

【输出格式】
只输出 JSON：{{"summary": "...", "suggestions": ["建议1", "建议2"], "trends": "..."}}
"""
        return self._clip(prompt, self.MAX_PROMPT_CHARS)

    def _build_empty_monthly_prompt(self, month_start: date, month_end: date) -> str:
        return f"""本月（{month_start} 至 {month_end}）没有记录。

请生成一份简短的月报：
- summary: 简短说明本月无记录
- suggestions: 给出 1-3 条鼓励性建议
- trends: 可以留空

只输出 JSON：{{"summary": "...", "suggestions": [...], "trends": ""}}
"""
