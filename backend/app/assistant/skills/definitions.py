"""Skill 定义"""
from __future__ import annotations

from app.assistant.skills.base import DEFAULT_SKILL_NAME, SkillDefinition, SkillStep


# ==================== 默认 Skill（Fallback） ====================

GENERAL_CHAT = SkillDefinition(
    name=DEFAULT_SKILL_NAME,
    description="默认兜底对话（未匹配到任何 Skill 时使用）",
    intent_examples=[],
    tools=[
        "search_entries",
        "get_entry_detail",
        "create_entry",
        "get_statistics",
        "list_entry_types",
        "list_tags",
    ],
    mode="agent",
    system_prompt="你是 MindAtlas 的 AI 助手，友好地回复用户，可以按需调用工具。MindAtlas 是一款个人知识与经历管理系统，旨在帮助用户系统性地记录、关联、回顾和总结个人的知识积累与人生经历。",
)


# ==================== 原子 Skills ====================

SEARCH_ENTRIES = SkillDefinition(
    name="search_entries",
    description="搜索记录",
    intent_examples=[
        "找一下关于 React 的笔记",
        "有没有记录过 Docker 配置",
        "查询最近的会议记录",
        "搜索包含'架构'的文档",
    ],
    tools=["search_entries"],
    steps=[
        SkillStep(
            type="analysis",
            instruction="理解用户的搜索意图（主题/范围/过滤条件），给出你的执行计划：随后会调用搜索工具获取结果。",
        ),
        SkillStep(
            type="tool",
            tool_name="search_entries",
            args_from="context",
        ),
        SkillStep(
            type="summary",
            instruction="向用户展示搜索到的记录列表，简要概括结果数量和主要内容。",
        ),
    ],
)

GET_ENTRY_DETAIL = SkillDefinition(
    name="get_entry_detail",
    description="获取记录详情",
    intent_examples=[
        "查看这条记录的详情",
        "展开显示完整内容",
        "显示 ID 为 xxx 的记录",
        "详细看看这个",
    ],
    tools=["get_entry_detail"],
    steps=[
        SkillStep(
            type="analysis",
            instruction="理解用户想查看的记录对象与原因（例如通过ID/标题/上下文指代）。说明接下来会如何获取详情。",
        ),
        SkillStep(
            type="tool",
            tool_name="get_entry_detail",
            args_from="context",
        ),
        SkillStep(
            type="summary",
            instruction="展示记录的完整内容，包括标题、正文、标签和属性。",
        ),
    ],
)

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

LIST_TYPES = SkillDefinition(
    name="list_types",
    description="列出所有记录类型",
    intent_examples=[
        "有哪些记录类型",
        "系统支持哪些分类",
        "查看所有类型",
    ],
    tools=["list_entry_types"],
    steps=[
        SkillStep(
            type="tool",
            tool_name="list_entry_types",
        ),
        SkillStep(
            type="summary",
            instruction="列出所有可用的记录类型及其说明。",
        ),
    ],
)

LIST_TAGS = SkillDefinition(
    name="list_tags",
    description="列出所有标签",
    intent_examples=[
        "查看所有标签",
        "列出最常用的标签",
        "有哪些 tag",
    ],
    tools=["list_tags"],
    steps=[
        SkillStep(
            type="tool",
            tool_name="list_tags",
        ),
        SkillStep(
            type="summary",
            instruction="列出系统中的标签列表，可以按使用频率展示。",
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
            instruction="理解用户希望回顾的时间范围与目标，并说明计划：先查询该时间范围内的记录，再做统计/洞察分析。",
        ),
        SkillStep(
            type="tool",
            tool_name="get_entries_by_time_range",
            args_from="context",
        ),
        SkillStep(
            type="tool",
            tool_name="analyze_activity",
            args_from="context",
        ),
        SkillStep(
            type="summary",
            instruction="生成结构化的回顾报告，包含关键成就、活动分布和洞察。",
        ),
    ],
)

KNOWLEDGE_SYNTHESIS = SkillDefinition(
    name="knowledge_synthesis",
    description="知识综合与梳理",
    intent_examples=[
        "总结一下关于微服务架构的知识",
        "梳理最近关于 AI Agent 的笔记",
        "把 React 相关的记录整合一下",
    ],
    tools=["search_entries"],
    steps=[
        SkillStep(
            type="analysis",
            instruction="理解用户希望综合/梳理的主题，并说明计划：先搜索相关记录，再做归纳整理。",
        ),
        SkillStep(
            type="tool",
            tool_name="search_entries",
            args_from="context",
        ),
        SkillStep(
            type="summary",
            instruction="综合搜索到的记录，生成连贯的知识总结，标注引用来源。",
        ),
    ],
)


# ==================== 导出 ====================

SKILLS: list[SkillDefinition] = [
    SEARCH_ENTRIES,
    GET_ENTRY_DETAIL,
    QUICK_STATS,
    LIST_TYPES,
    LIST_TAGS,
    SMART_CAPTURE,
    PERIODIC_REVIEW,
    KNOWLEDGE_SYNTHESIS,
    GENERAL_CHAT,
]


def get_skill_by_name(name: str) -> SkillDefinition | None:
    """根据名称获取 Skill 定义"""
    for skill in SKILLS:
        if skill.name == name:
            return skill
    return None
