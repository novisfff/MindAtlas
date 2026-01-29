"""Skill 定义"""
from __future__ import annotations

from app.assistant.skills.base import (
    DEFAULT_SKILL_NAME,
    OutputFieldSpec,
    SkillDefinition,
    SkillKBConfig,
    SkillStep,
)


# ==================== 默认 Skill（Fallback） ====================

GENERAL_CHAT = SkillDefinition(
    name=DEFAULT_SKILL_NAME,
    description=(
        "默认兜底对话（未匹配到任何 Skill 时使用）：支持知识问答/总结/写作/翻译等；"
        "可结合知识库检索结果回答；默认不执行写入/创建操作"
    ),
    intent_examples=[],
    tools=[
        "get_statistics",
        "list_entry_types",
        "list_tags",
    ],
    mode="agent",
    system_prompt="你是 MindAtlas 的 AI 助手，友好地回复用户，可以按需调用工具。MindAtlas 是一款个人知识与经历管理系统，旨在帮助用户系统性地记录、关联、回顾和总结个人的知识积累与人生经历。",
    kb=SkillKBConfig(enabled=True),
)


# ==================== 原子 Skills ====================

QUICK_STATS = SkillDefinition(
    name="quick_stats",
    description=(
        "快速统计（仅统计 MindAtlas 系统内数据，如我的记录/条目/标签/类型/仪表盘概况；"
        "不用于对外部组织/项目/人物的情况总结）"
    ),
    intent_examples=[
        "统计一下我在 MindAtlas 里有多少条记录",
        "看下我的仪表盘数据/数据概况",
        "我有多少个标签、多少种类型？",
        "现在的记录总数是多少",
    ],
    tools=["get_statistics"],
    steps=[
        SkillStep(
            type="tool",
            tool_name="get_statistics",
        ),
        SkillStep(
            type="summary",
            instruction="汇报当前的记录总数、最近活动趋势等统计信息。",
        ),
    ],
)

# ==================== 复合 Skills ====================

SMART_CAPTURE = SkillDefinition(
    name="smart_capture",
    description=(
        "智能创建记录（将用户内容写入/保存为 MindAtlas 的一条记录；"
        "在用户要求创建/新增/添加/记录/保存/入库时或用户直接提供内容时使用）"
    ),
    intent_examples=[
        "帮我记录一下今天学到的 Python 技巧",
        "把下面内容保存为一条笔记：……",
        "创建一个新任务：下周一交报告",
        "记笔记：React 19 的新特性包括...",
        "我今天学习了React 19的特性",
    ],
    tools=["list_entry_types", "list_tags", "create_entry"],
    steps=[
        SkillStep(
            type="tool",
            tool_name="list_entry_types",
            args_from="json",
            args_template="{}",
        ),
        SkillStep(
            type="tool",
            tool_name="list_tags",
            args_from="json",
            args_template="{}",
        ),
        SkillStep(
            type="analysis",
            instruction=(
                "你是 MindAtlas 的“智能创建记录”技能，正在做结构化入库前的字段生成。\n"
                "当前任务：基于用户原始内容生成 title（标题），用于最终写入数据库。\n"
                "规范：title 简洁准确，不超过 30 个字。"
            ),
            output_mode="json",
            output_fields=[OutputFieldSpec(name="title")],
        ),
        SkillStep(
            type="analysis",
            instruction=(
                "你是 MindAtlas 的“智能创建记录”技能，正在做结构化入库前的字段生成。\n"
                "当前任务：基于用户原始内容生成 summary（摘要），用于最终写入数据库。\n"
                "规范：summary 为 50-150 字的一段话概括核心内容。"
            ),
            output_mode="json",
            output_fields=[OutputFieldSpec(name="summary")],
        ),
        SkillStep(
            type="analysis",
            instruction=(
                "你是 MindAtlas 的“智能创建记录”技能，正在做结构化入库前的字段生成。\n"
                "当前任务：基于用户原始内容生成 content（正文），用于最终写入数据库。\n"
                "规范：content 可用 Markdown；禁止一级标题（#）；不扩写/不编造用户未提供的事实细节。"
            ),
            output_mode="json",
            output_fields=[OutputFieldSpec(name="content")],
        ),
        SkillStep(
            type="analysis",
            instruction=(
                "你是 MindAtlas 的“智能创建记录”技能，正在为入库选择类型。\n"
                "当前任务：选择 type_code（类型编码），用于最终写入数据库。\n"
                "约束：type_code 必须且只能从 {{step_1_result_raw}} 的 code 中选择（JSON 数组，字段含 code/name）。无法判断时选择第一个可用 code。"
            ),
            output_mode="json",
            output_fields=[OutputFieldSpec(name="type_code")],
        ),
        SkillStep(
            type="analysis",
            instruction=(
                "你是 MindAtlas 的“智能创建记录”技能，正在为入库生成标签。\n"
                "当前任务：生成 tags（标签名数组），用于最终写入数据库。\n"
                "约束：优先复用 {{step_2_result_raw}} 中的 name（大小写不敏感匹配；输出尽量返回列表中的原始写法）；最多新增 5 个新标签；宁缺毋滥，不要为了凑数而编造标签；tags 元素为纯标签名字符串（不要带 # 前缀），去重。"
            ),
            output_mode="json",
            output_fields=[OutputFieldSpec(name="tags", type="array", items_type="string")],
        ),
        SkillStep(
            type="analysis",
            instruction=(
                "你是 MindAtlas 的“智能创建记录”技能，正在为入库识别时间字段。\n"
                "当前任务：识别时间信息并输出 time_mode + 对应日期字段，用于最终写入数据库。\n"
                "规则：\n"
                "- 无明确时间信息：默认 time_mode=POINT 且 time_at=今天（YYYY-MM-DD）。\n"
                "- POINT：填写 time_at（YYYY-MM-DD），time_from/time_to 为 null。\n"
                "- RANGE：填写 time_from/time_to（YYYY-MM-DD，且起止都不为空，且 time_from<=time_to），time_at 为 null。"
            ),
            output_mode="json",
            output_fields=[
                OutputFieldSpec(name="time_mode", enum=["POINT", "RANGE"]),
                OutputFieldSpec(name="time_at", nullable=True),
                OutputFieldSpec(name="time_from", nullable=True),
                OutputFieldSpec(name="time_to", nullable=True),
            ],
        ),
        SkillStep(
            type="tool",
            tool_name="create_entry",
            args_from="json",
            args_template=(
                "{"
                "\"title\": {{step_3_title}}, "
                "\"summary\": {{step_4_summary}}, "
                "\"content\": {{step_5_content}}, "
                "\"type_code\": {{step_6_type_code}}, "
                "\"tags\": {{step_7_tags}}, "
                "\"time_mode\": {{step_8_time_mode}}, "
                "\"time_at\": {{step_8_time_at}}, "
                "\"time_from\": {{step_8_time_from}}, "
                "\"time_to\": {{step_8_time_to}}"
                "}"
            ),
        ),
        SkillStep(
            type="summary",
            instruction="告知用户记录已创建，展示标题、类型与时间信息，并给出需要的话可继续补充/修改的提示。",
        ),
    ],
)

PERIODIC_REVIEW = SkillDefinition(
    name="periodic_review",
    description=(
        "周期性回顾与分析（按时间范围回顾 MindAtlas 中我的记录并生成周报/月报/复盘；"
        "需要明确时间范围，如上周/本月/某日期区间）"
    ),
    intent_examples=[
        "回顾我上周在 MindAtlas 里的记录并生成周报",
        "复盘我本月的记录产出",
        "分析 2025-01-01 到 2025-01-31 的我的记录",
        "查看我这周的学习记录进度",
    ],
    tools=["get_entries_by_time_range", "analyze_activity"],
    steps=[
        SkillStep(
            type="analysis",
            instruction=(
                "理解用户希望回顾/分析的时间范围，输出结构化检索参数。\n"
                "规则：\n"
                "- start_date/end_date 格式为 YYYY-MM-DD，且 start_date<=end_date。\n"
                "- 用户未明确给出具体日期时，结合用户说法（如上周/本月/今年等）推断。"
            ),
            output_mode="json",
            output_fields=[
                OutputFieldSpec(name="start_date"),
                OutputFieldSpec(name="end_date"),
            ],
        ),
        SkillStep(
            type="tool",
            tool_name="get_entries_by_time_range",
            args_from="json",
            args_template="{\"start_date\": {{step_1_start_date}}, \"end_date\": {{step_1_end_date}}}",
        ),
        SkillStep(
            type="tool",
            tool_name="analyze_activity",
            args_from="json",
            args_template=(
                "{\"start_date\": {{step_1_start_date}}, "
                "\"end_date\": {{step_1_end_date}}}"
            ),
        ),
        SkillStep(
            type="summary",
            instruction="生成结构化的回顾报告，包含关键成就、活动分布和洞察。",
        ),
    ],
)


# ==================== 导出 ====================

SKILLS: list[SkillDefinition] = [
    QUICK_STATS,
    SMART_CAPTURE,
    PERIODIC_REVIEW,
    GENERAL_CHAT,
]


def get_skill_by_name(name: str) -> SkillDefinition | None:
    """根据名称获取 Skill 定义"""
    for skill in SKILLS:
        if skill.name == name:
            return skill
    return None
