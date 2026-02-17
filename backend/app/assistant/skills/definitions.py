"""Skill 定义"""
from __future__ import annotations

from app.assistant.skills.base import (
    DEFAULT_SKILL_NAME,
    SkillDefinition,
    SkillKBConfig,
    WorkflowEdgeDefinition,
    WorkflowNodeDefinition,
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
    mode="langgraph",
    langgraph_pattern="agent_loop",
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
    mode="langgraph",
    langgraph_pattern="workflow_dag",
    workflow_nodes=[
        WorkflowNodeDefinition(node_id="start", node_type="start", label="Start", position_x=120, position_y=220),
        WorkflowNodeDefinition(
            node_id="tool_stats",
            node_type="tool",
            label="获取统计",
            position_x=460,
            position_y=220,
            config={"toolName": "get_statistics", "inputBindings": {}},
        ),
        WorkflowNodeDefinition(
            node_id="llm_output",
            node_type="llm",
            label="汇报统计",
            position_x=800,
            position_y=220,
            config={
                "systemPrompt": "汇报当前的记录总数、最近活动趋势等统计信息，输出简洁清晰。",
                "userInput": "{{tool_stats.result}}",
                "outputMode": "text",
            },
        ),
        WorkflowNodeDefinition(
            node_id="output_final",
            node_type="output",
            label="输出",
            position_x=1080,
            position_y=220,
            config={
                "outputMode": "text",
                "textTemplate": "{{llm_output.response}}",
            },
        ),
    ],
    workflow_edges=[
        WorkflowEdgeDefinition(edge_id="e_start_tool", source_node_id="start", target_node_id="tool_stats"),
        WorkflowEdgeDefinition(edge_id="e_tool_output", source_node_id="tool_stats", target_node_id="llm_output"),
        WorkflowEdgeDefinition(edge_id="e_llm_output_final", source_node_id="llm_output", target_node_id="output_final"),
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
    mode="langgraph",
    langgraph_pattern="workflow_dag",
    workflow_nodes=[
        WorkflowNodeDefinition(
            node_id="start", node_type="start", label="Start",
            position_x=80, position_y=320,
        ),
        WorkflowNodeDefinition(
            node_id="tool_types", node_type="tool", label="获取类型列表",
            position_x=320, position_y=200,
            config={"toolName": "list_entry_types", "inputBindings": {}},
        ),
        WorkflowNodeDefinition(
            node_id="tool_tags", node_type="tool", label="获取标签列表",
            position_x=320, position_y=440,
            config={"toolName": "list_tags", "inputBindings": {}},
        ),
        WorkflowNodeDefinition(
            node_id="llm_title", node_type="llm", label="生成标题",
            position_x=560, position_y=320,
            config={
                "systemPrompt": (
                    "你是 MindAtlas 的\u201c智能创建记录\u201d技能，正在做结构化入库前的字段生成。\n"
                    "当前任务：基于用户原始内容生成 title（标题），用于最终写入数据库。\n"
                    "规范：title 简洁准确，不超过 30 个字。"
                ),
                "outputMode": "structured",
                "outputFields": [{"name": "title"}],
            },
        ),
        WorkflowNodeDefinition(
            node_id="llm_summary", node_type="llm", label="生成摘要",
            position_x=800, position_y=320,
            config={
                "systemPrompt": (
                    "你是 MindAtlas 的\u201c智能创建记录\u201d技能，正在做结构化入库前的字段生成。\n"
                    "当前任务：基于用户原始内容生成 summary（摘要），用于最终写入数据库。\n"
                    "规范：summary 为 50-150 字的一段话概括核心内容。"
                ),
                "outputMode": "structured",
                "outputFields": [{"name": "summary"}],
            },
        ),
        WorkflowNodeDefinition(
            node_id="llm_content", node_type="llm", label="生成正文",
            position_x=1040, position_y=320,
            config={
                "systemPrompt": (
                    "你是 MindAtlas 的\u201c智能创建记录\u201d技能，正在做结构化入库前的字段生成。\n"
                    "当前任务：基于用户原始内容生成 content（正文），用于最终写入数据库。\n"
                    "规范：content 可用 Markdown；禁止一级标题（#）；不扩写/不编造用户未提供的事实细节。"
                ),
                "outputMode": "structured",
                "outputFields": [{"name": "content"}],
            },
        ),
        WorkflowNodeDefinition(
            node_id="llm_type", node_type="llm", label="选择类型",
            position_x=1280, position_y=320,
            config={
                "systemPrompt": (
                    "你是 MindAtlas 的\u201c智能创建记录\u201d技能，正在为入库选择类型。\n"
                    "当前任务：选择 type_code（类型编码），用于最终写入数据库。\n"
                    "约束：type_code 必须且只能从 {{tool_types.result}} 的 code 中选择"
                    "（JSON 数组，字段含 code/name）。无法判断时选择第一个可用 code。"
                ),
                "outputMode": "structured",
                "outputFields": [{"name": "type_code"}],
            },
        ),
        WorkflowNodeDefinition(
            node_id="llm_tags", node_type="llm", label="生成标签",
            position_x=1520, position_y=320,
            config={
                "systemPrompt": (
                    "你是 MindAtlas 的\u201c智能创建记录\u201d技能，正在为入库生成标签。\n"
                    "当前任务：生成 tags（标签名数组），用于最终写入数据库。\n"
                    "约束：优先复用 {{tool_tags.result}} 中的 name"
                    "（大小写不敏感匹配；输出尽量返回列表中的原始写法）；"
                    "最多新增 5 个新标签；宁缺毋滥，不要为了凑数而编造标签；"
                    "tags 元素为纯标签名字符串（不要带 # 前缀），去重。"
                ),
                "outputMode": "structured",
                "outputFields": [{"name": "tags", "type": "array", "itemsType": "string"}],
            },
        ),
        WorkflowNodeDefinition(
            node_id="llm_time", node_type="llm", label="识别时间",
            position_x=1760, position_y=320,
            config={
                "systemPrompt": (
                    "你是 MindAtlas 的\u201c智能创建记录\u201d技能，正在为入库识别时间字段。\n"
                    "当前任务：识别时间信息并输出 time_mode + 对应日期字段，用于最终写入数据库。\n"
                    "规则：\n"
                    "- 无明确时间信息：默认 time_mode=POINT 且 time_at=今天（YYYY-MM-DD）。\n"
                    "- POINT：填写 time_at（YYYY-MM-DD），time_from/time_to 为 null。\n"
                    "- RANGE：填写 time_from/time_to（YYYY-MM-DD，且起止都不为空，"
                    "且 time_from<=time_to），time_at 为 null。"
                ),
                "outputMode": "structured",
                "outputFields": [
                    {"name": "time_mode", "enum": ["POINT", "RANGE"]},
                    {"name": "time_at", "nullable": True},
                    {"name": "time_from", "nullable": True},
                    {"name": "time_to", "nullable": True},
                ],
            },
        ),
        WorkflowNodeDefinition(
            node_id="tool_create", node_type="tool", label="创建记录",
            position_x=2000, position_y=320,
            config={
                "toolName": "create_entry",
                "inputBindings": {
                    "title": "{{llm_title.title}}",
                    "summary": "{{llm_summary.summary}}",
                    "content": "{{llm_content.content}}",
                    "type_code": "{{llm_type.type_code}}",
                    "tags": "{{llm_tags.tags}}",
                    "time_mode": "{{llm_time.time_mode}}",
                    "time_at": "{{llm_time.time_at}}",
                    "time_from": "{{llm_time.time_from}}",
                    "time_to": "{{llm_time.time_to}}",
                },
            },
        ),
        WorkflowNodeDefinition(
            node_id="llm_output", node_type="llm", label="创建结果",
            position_x=2240, position_y=320,
            config={
                "systemPrompt": "告知用户记录已创建，展示标题、类型与时间信息，并给出可继续补充/修改的提示。",
                "userInput": "{{tool_create.result}}",
                "outputMode": "text",
            },
        ),
        WorkflowNodeDefinition(
            node_id="output_final", node_type="output", label="输出",
            position_x=2480, position_y=320,
            config={
                "outputMode": "text",
                "textTemplate": "{{llm_output.response}}",
            },
        ),
    ],
    workflow_edges=[
        # start fans out to both tool nodes in parallel
        WorkflowEdgeDefinition(edge_id="e_start_types", source_node_id="start", target_node_id="tool_types"),
        WorkflowEdgeDefinition(edge_id="e_start_tags", source_node_id="start", target_node_id="tool_tags"),
        # both tools feed into llm_title (aggregation point)
        WorkflowEdgeDefinition(edge_id="e_types_title", source_node_id="tool_types", target_node_id="llm_title"),
        WorkflowEdgeDefinition(edge_id="e_tags_title", source_node_id="tool_tags", target_node_id="llm_title"),
        # linear chain: title → summary → content → type → tags → time → create → output llm → output
        WorkflowEdgeDefinition(edge_id="e_title_summary", source_node_id="llm_title", target_node_id="llm_summary"),
        WorkflowEdgeDefinition(edge_id="e_summary_content", source_node_id="llm_summary", target_node_id="llm_content"),
        WorkflowEdgeDefinition(edge_id="e_content_type", source_node_id="llm_content", target_node_id="llm_type"),
        WorkflowEdgeDefinition(edge_id="e_type_tags", source_node_id="llm_type", target_node_id="llm_tags"),
        WorkflowEdgeDefinition(edge_id="e_tags_time", source_node_id="llm_tags", target_node_id="llm_time"),
        WorkflowEdgeDefinition(edge_id="e_time_create", source_node_id="llm_time", target_node_id="tool_create"),
        WorkflowEdgeDefinition(edge_id="e_create_output", source_node_id="tool_create", target_node_id="llm_output"),
        WorkflowEdgeDefinition(edge_id="e_output_final", source_node_id="llm_output", target_node_id="output_final"),
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
    mode="langgraph",
    langgraph_pattern="workflow_dag",
    workflow_nodes=[
        WorkflowNodeDefinition(
            node_id="start", node_type="start", label="Start",
            position_x=120, position_y=320,
        ),
        WorkflowNodeDefinition(
            node_id="llm_dates", node_type="llm", label="解析时间范围",
            position_x=440, position_y=320,
            config={
                "systemPrompt": (
                    "理解用户希望回顾/分析的时间范围，输出结构化检索参数。\n"
                    "规则：\n"
                    "- start_date/end_date 格式为 YYYY-MM-DD，且 start_date<=end_date。\n"
                    "- 用户未明确给出具体日期时，结合用户说法（如上周/本月/今年等）推断。"
                ),
                "outputMode": "structured",
                "outputFields": [
                    {"name": "start_date"},
                    {"name": "end_date"},
                ],
            },
        ),
        WorkflowNodeDefinition(
            node_id="tool_entries", node_type="tool", label="获取记录",
            position_x=760, position_y=220,
            config={
                "toolName": "get_entries_by_time_range",
                "inputBindings": {
                    "start_date": "{{llm_dates.start_date}}",
                    "end_date": "{{llm_dates.end_date}}",
                },
            },
        ),
        WorkflowNodeDefinition(
            node_id="tool_activity", node_type="tool", label="分析活动",
            position_x=760, position_y=420,
            config={
                "toolName": "analyze_activity",
                "inputBindings": {
                    "start_date": "{{llm_dates.start_date}}",
                    "end_date": "{{llm_dates.end_date}}",
                },
            },
        ),
        WorkflowNodeDefinition(
            node_id="llm_output", node_type="llm", label="生成报告",
            position_x=1080, position_y=320,
            config={
                "systemPrompt": "生成结构化的回顾报告，包含关键成就、活动分布和洞察。",
                "userInput": "{{tool_entries.result}}\n\n{{tool_activity.result}}",
                "outputMode": "text",
            },
        ),
        WorkflowNodeDefinition(
            node_id="output_final", node_type="output", label="输出",
            position_x=1320, position_y=320,
            config={
                "outputMode": "text",
                "textTemplate": "{{llm_output.response}}",
            },
        ),
    ],
    workflow_edges=[
        WorkflowEdgeDefinition(edge_id="e_start_dates", source_node_id="start", target_node_id="llm_dates"),
        # llm_dates fans out to both tool nodes in parallel
        WorkflowEdgeDefinition(edge_id="e_dates_entries", source_node_id="llm_dates", target_node_id="tool_entries"),
        WorkflowEdgeDefinition(edge_id="e_dates_activity", source_node_id="llm_dates", target_node_id="tool_activity"),
        # both tools feed into final llm output (aggregation point)
        WorkflowEdgeDefinition(edge_id="e_entries_output", source_node_id="tool_entries", target_node_id="llm_output"),
        WorkflowEdgeDefinition(edge_id="e_activity_output", source_node_id="tool_activity", target_node_id="llm_output"),
        WorkflowEdgeDefinition(edge_id="e_output_final", source_node_id="llm_output", target_node_id="output_final"),
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
