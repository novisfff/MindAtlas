"""Skill 定义"""
from __future__ import annotations

from app.assistant.skills.base import DEFAULT_SKILL_NAME, SkillDefinition, SkillStep, SkillKBConfig


# ==================== 默认 Skill（Fallback） ====================

GENERAL_CHAT = SkillDefinition(
    name=DEFAULT_SKILL_NAME,
    description="默认兜底对话（未匹配到任何 Skill 时使用）",
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
    description="快速统计",
    intent_examples=[
        "最近有多少条记录",
        "查看数据统计",
        "统计一下现在的知识库概况",
        "看下仪表盘数据",
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
    description="智能创建记录",
    intent_examples=[
        "帮我记录一下今天学到的 Python 技巧",
        "创建一个新任务：下周一交报告",
        "记笔记：React 19 的新特性包括...",
        "添加一条关于健身的记录",
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
                "可用类型列表在 step_1_result_raw（JSON 数组，字段含 code/name），可用标签列表在 step_2_result_raw（JSON 数组，字段含 name）。\n"
                "输出要求：只输出一个 JSON 对象：{\"title\": string}；title 简洁准确，不超过 30 个字；禁止输出额外描述、Markdown、代码块围栏。"
            ),
            output_mode="json",
            output_fields=["title"],
        ),
        SkillStep(
            type="analysis",
            instruction=(
                "你是 MindAtlas 的“智能创建记录”技能，正在做结构化入库前的字段生成。\n"
                "当前任务：基于用户原始内容生成 summary（摘要），用于最终写入数据库。\n"
                "输出要求：只输出一个 JSON 对象：{\"summary\": string}；summary 为 50-150 字的一段话概括核心内容；禁止输出额外描述、Markdown、代码块围栏。"
            ),
            output_mode="json",
            output_fields=["summary"],
        ),
        SkillStep(
            type="analysis",
            instruction=(
                "你是 MindAtlas 的“智能创建记录”技能，正在做结构化入库前的字段生成。\n"
                "当前任务：基于用户原始内容生成 content（正文），用于最终写入数据库。\n"
                "输出要求：只输出一个 JSON 对象：{\"content\": string}；content 可用 Markdown；禁止一级标题（#）；不扩写/不编造用户未提供的事实细节；禁止输出额外描述、代码块围栏。"
            ),
            output_mode="json",
            output_fields=["content"],
        ),
        SkillStep(
            type="analysis",
            instruction=(
                "你是 MindAtlas 的“智能创建记录”技能，正在为入库选择类型。\n"
                "当前任务：选择 type_code（类型编码），用于最终写入数据库。\n"
                "约束：type_code 必须且只能从 step_1_result_raw 的 code 中选择（JSON 数组，字段含 code/name）。无法判断时选择第一个可用 code。\n"
                "输出要求：只输出一个 JSON 对象：{\"type_code\": string}；禁止输出额外描述、Markdown、代码块围栏。"
            ),
            output_mode="json",
            output_fields=["type_code"],
        ),
        SkillStep(
            type="analysis",
            instruction=(
                "你是 MindAtlas 的“智能创建记录”技能，正在为入库生成标签。\n"
                "当前任务：生成 tags（标签名数组），用于最终写入数据库。\n"
                "约束：优先复用 step_2_result_raw 中的 name（大小写不敏感匹配；输出尽量返回列表中的原始写法）；最多新增 5 个新标签；tags 元素为纯标签名字符串（不要带 # 前缀），去重。\n"
                "输出要求：只输出一个 JSON 对象：{\"tags\": string[]}；禁止输出额外描述、Markdown、代码块围栏。"
            ),
            output_mode="json",
            output_fields=["tags"],
        ),
        SkillStep(
            type="analysis",
            instruction=(
                "你是 MindAtlas 的“智能创建记录”技能，正在为入库识别时间字段。\n"
                "当前任务：识别时间信息并输出 time_mode + 对应日期字段，用于最终写入数据库。\n"
                "输出要求：只输出一个 JSON 对象："
                "{\"time_mode\": \"POINT\"|\"RANGE\", \"time_at\": string|null, \"time_from\": string|null, \"time_to\": string|null}。\n"
                "规则：\n"
                '- time_mode 必须是 \"POINT\" 或 \"RANGE\"。\n'
                "- 无明确时间信息：默认 time_mode=\"POINT\" 且 time_at=今天（YYYY-MM-DD）。\n"
                "- POINT：填写 time_at（YYYY-MM-DD），time_from/time_to 为 null。\n"
                "- RANGE：填写 time_from/time_to（YYYY-MM-DD，且起止都不为空，且 time_from<=time_to），time_at 为 null。\n"
                "禁止输出额外描述、Markdown、代码块围栏。"
            ),
            output_mode="json",
            output_fields=["time_mode", "time_at", "time_from", "time_to"],
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
    description="周期性回顾与分析",
    intent_examples=[
        "分析一下上周的记录",
        "回顾本月的工作产出",
        "查看这周的学习进度",
        "生成月度报告",
    ],
    tools=["get_entries_by_time_range", "analyze_activity"],
    steps=[
        SkillStep(
            type="analysis",
            instruction=(
                "理解用户希望回顾/分析的时间范围，输出结构化检索参数。\n"
                "输出要求：只输出一个 JSON 对象：{\"start_date\": string, \"end_date\": string}。\n"
                "规则：\n"
                "- start_date/end_date 格式为 YYYY-MM-DD，且 start_date<=end_date。\n"
                "- 用户未明确给出具体日期时，结合用户说法（如上周/本月/今年等）推断。\n"
                "禁止输出额外描述、Markdown、代码块围栏。"
            ),
            output_mode="json",
            output_fields=["start_date", "end_date"],
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
