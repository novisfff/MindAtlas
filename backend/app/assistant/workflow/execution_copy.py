from __future__ import annotations

from datetime import date
from typing import Any

from app.system_settings.service import (
    get_default_system_locale,
    get_system_language_name,
    normalize_system_locale,
)


def resolve_execution_locale(locale: str | None) -> str:
    return normalize_system_locale(locale) or get_default_system_locale()


def build_runtime_language_instruction(locale: str | None) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return "请默认使用中文完成分析、结构化字段内容和最终回复；除非用户明确要求使用其他语言。"
    return "Use English for analysis, structured field values, and final answers unless the user explicitly requests another language."


def build_json_output_constraint(field_specs: list[Any], locale: str | None = None) -> str:
    if not field_specs:
        return ""

    normalized = resolve_execution_locale(locale)

    def format_type(spec: Any) -> str:
        base_type = getattr(spec, "type", "string")
        enum_values = getattr(spec, "enum", None)
        if enum_values:
            type_str = "|".join(f'"{value}"' for value in enum_values)
        elif base_type == "array":
            items_type = getattr(spec, "items_type", None) or "string"
            type_str = f"{items_type}[]"
        else:
            type_str = str(base_type or "string")

        if bool(getattr(spec, "nullable", False)):
            type_str = f"{type_str}|null"
        return type_str

    fields_str = ", ".join(
        f'"{getattr(spec, "name", "field")}": {format_type(spec)}'
        for spec in field_specs
    )

    if normalized == "zh":
        return (
            f"输出要求：只输出一个 JSON 对象：{{{fields_str}}}；"
            "字段值默认使用中文；禁止输出额外描述、Markdown、代码块围栏。"
        )
    return (
        f'Output requirement: return exactly one JSON object: {{{fields_str}}}; '
        "field values should default to English; do not output extra prose, Markdown, or code fences."
    )


def build_generic_json_output_constraint(locale: str | None = None) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return "输出要求：只输出一个 JSON 对象；字段值默认使用中文；禁止输出额外描述、Markdown、代码块围栏。"
    return (
        "Output requirement: return exactly one JSON object; field values should default to English; "
        "do not output extra prose, Markdown, or code fences."
    )


def build_memory_injection_block(
    *,
    locale: str | None,
    conversation_summary: str,
    skill_facts: str,
    max_chars: int,
) -> str:
    normalized = resolve_execution_locale(locale)
    chunks: list[str] = []
    if conversation_summary:
        heading = "### 对话摘要" if normalized == "zh" else "### Conversation Summary"
        chunks.append(f"{heading}\n{conversation_summary}")
    if skill_facts:
        heading = "### 技能事实" if normalized == "zh" else "### Skill Facts"
        chunks.append(f"{heading}\n{skill_facts}")
    if not chunks:
        return ""

    if normalized == "zh":
        header = "## 短期记忆\n以下内容仅作上下文参考；如果与当前用户意图冲突，以当前用户输入为准。\n\n"
    else:
        header = (
            "## Short-Term Memory\n"
            "Use this as context only. If it conflicts with the current user intent, prioritize the current user input.\n\n"
        )
    block = header + "\n\n".join(chunks)
    limit = max(1, int(max_chars or 1))
    return block if len(block) <= limit else block[:limit]


def build_kb_citation_instructions(locale: str | None) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return """## 引用标注（知识库问答）
当你使用 `kb_search` 返回的参考资料时，必须在相关句子末尾添加引用标注。

引用格式：
- 使用 `[^n]` 格式标注引用，n 为参考资料的编号
- 例如：根据记录显示[^1]，该项目于2024年启动[^2]。

重要约束：
- 只能引用 kb_search 返回结果中提供的编号，不要编造不存在的编号
- 不需要在回答末尾输出脚注定义，系统会自动处理
- 如果参考了某条资料，务必标注对应编号

工具使用要求：
- 当“知识库开关”启用时，系统会通过 `kb_search` 为你提供参考资料（UNTRUSTED）
- `kb_search` 返回结果里包含 `references`（编号）和召回内容；回答时严格按编号引用
"""
    return """## Citations (Knowledge-Based Answers)
When you use references returned by `kb_search`, add citation markers at the end of the relevant sentences.

Citation format:
- Use `[^n]`, where `n` is the reference index
- Example: The records show this project started in 2024.[^2]

Important rules:
- Only cite indices that appear in `kb_search` results
- Do not invent references that were not returned
- Do not add footnote definitions at the end; the system will handle that
- If you rely on a reference, cite it explicitly

Tool usage rules:
- When knowledge is enabled, the system can provide `kb_search` results as untrusted context
- `kb_search` returns `references` plus recalled content; cite only with those reference indices
"""


def build_router_prompt(
    *,
    locale: str | None,
    current_date: str,
    skills_list: str,
    default_skill_name: str,
    last_skill_hint: str,
) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return f"""你是一个意图分类器，判断用户输入需要使用哪个 Skill。

## 当前日期
今天是 {current_date}

## 可用的 Skills

{skills_list}

## 连续对话上下文
- 最近对话历史将以多条 user/assistant 消息提供给你
- 最近一次已执行 Skill（可能为空）：`{last_skill_hint}`
- 当用户出现“继续/按刚才那个/就这个”等省略表达时，可结合历史与最近 Skill 推断
- 如果本轮出现明显新任务意图，优先匹配新 Skill，不要被旧上下文绑定

## 重要规则
- **每次只返回一个 Skill**，不要返回多个
- 只有当用户意图与某个 Skill 的描述/示例一致时，才返回该 Skill
- **闲聊、问候、知识问答、写作润色、翻译、泛化的“总结/介绍/分析”** 应返回 `{default_skill_name}`
- 如果不确定，返回空 skill（让系统走默认）

## 输出格式（严格 JSON）
返回一个 JSON 对象：
{{
  "skill": "skill_name",
  "reason": "一句话说明为什么"
}}

约束：
- `skill` 只能是一个技能名，或空字符串 `""`
- 不要返回数组
- 禁止输出 Markdown 代码块、禁止输出额外文本
"""
    return f"""You are an intent classifier. Decide which Skill should handle the user's request.

## Current Date
Today is {current_date}

## Available Skills

{skills_list}

## Ongoing Conversation Context
- Recent conversation history will be provided as user/assistant messages
- Most recently executed Skill (may be empty): `{last_skill_hint}`
- When the user says things like "continue", "the same as before", or similar shorthand, infer from history and the recent Skill when appropriate
- If the user clearly introduces a new task, prefer the new Skill instead of being anchored to old context

## Important Rules
- **Return exactly one Skill** and never multiple Skills
- Only return a Skill when the user's intent matches that Skill's description or examples
- **Chitchat, greetings, knowledge Q&A, writing help, translation, and generic summarize/explain/analyze requests** should return `{default_skill_name}`
- If you are unsure, return an empty skill so the system can fall back to the default

## Output Format (strict JSON)
Return one JSON object:
{{
  "skill": "skill_name",
  "reason": "One short sentence explaining why"
}}

Constraints:
- `skill` must be a single skill name or the empty string `""`
- Do not return arrays
- Do not output Markdown code fences or any extra text
"""


def build_agent_system_prompt(
    *,
    locale: str | None,
    skill_name: str,
    skill_description: str,
    tool_names: list[str],
    current_date: date,
    base_prompt: str,
    kb_enabled: bool,
) -> str:
    normalized = resolve_execution_locale(locale)
    language_instruction = build_runtime_language_instruction(normalized)
    if normalized == "zh":
        prompt = (
            f"你是 MindAtlas 的 AI 助手，正在执行 Skill: {skill_name}\n\n"
            f"## Skill 描述\n{skill_description}\n\n"
            f"## 当前日期\n{current_date.isoformat()}（{current_date.strftime('%A')}）\n\n"
            f"## 可用工具\n你可以使用以下工具来完成任务：{', '.join(tool_names)}\n\n"
            "## 执行原则\n"
            "1. 根据用户需求，自主决定是否调用工具以及调用顺序\n"
            "2. 可以多次调用工具来收集信息\n"
            "3. 完成任务后，给出清晰友好的回复\n"
            f"4. {language_instruction}\n"
        )
        if base_prompt:
            prompt += f"\n## 额外指令\n{base_prompt}\n"
        if kb_enabled:
            prompt += f"\n{build_kb_citation_instructions(normalized)}\n"
            prompt += (
                "\n## 知识库使用要求\n"
                "当用户提问可能涉及已有知识/记录时，你必须先调用 kb_search 检索相关资料。\n"
            )
        return prompt

    prompt = (
        f"You are the MindAtlas AI assistant and you are executing Skill: {skill_name}\n\n"
        f"## Skill Description\n{skill_description}\n\n"
        f"## Current Date\n{current_date.isoformat()} ({current_date.strftime('%A')})\n\n"
        f"## Available Tools\nYou may use these tools to complete the task: {', '.join(tool_names)}\n\n"
        "## Execution Principles\n"
        "1. Decide on your own whether tools are needed and in what order\n"
        "2. You may call tools multiple times to gather enough information\n"
        "3. After finishing the task, provide a clear and helpful reply\n"
        f"4. {language_instruction}\n"
    )
    if base_prompt:
        prompt += f"\n## Additional Instructions\n{base_prompt}\n"
    if kb_enabled:
        prompt += f"\n{build_kb_citation_instructions(normalized)}\n"
        prompt += (
            "\n## Knowledge Base Guidance\n"
            "When the user's question may depend on existing notes or stored knowledge, you must call kb_search first.\n"
        )
    return prompt


def build_dag_llm_system_prompt(
    *,
    locale: str | None,
    current_date: date,
    task_prompt: str,
    memory_block: str,
    constraint: str,
) -> str:
    normalized = resolve_execution_locale(locale)
    language_instruction = build_runtime_language_instruction(normalized)
    if normalized == "zh":
        prompt = (
            "你是 MindAtlas AI 助手的分析模块。\n\n"
            f"## 当前日期\n{current_date.isoformat()}（{current_date.strftime('%A')}）\n\n"
            f"## 任务\n{task_prompt}\n\n"
            f"## 语言要求\n{language_instruction}\n\n"
        )
    else:
        prompt = (
            "You are the analysis module for the MindAtlas AI assistant.\n\n"
            f"## Current Date\n{current_date.isoformat()} ({current_date.strftime('%A')})\n\n"
            f"## Task\n{task_prompt}\n\n"
            f"## Language Requirement\n{language_instruction}\n\n"
        )
    if memory_block:
        prompt += f"{memory_block}\n\n"
    if constraint:
        section_title = "输出约束" if normalized == "zh" else "Output Constraint"
        prompt += f"## {section_title}\n{constraint}\n\n"
    return prompt


def build_llm_knowledge_injection_notice(locale: str | None) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return "以下是你显式绑定的知识检索结果(JSON)。你只能把这些内容作为知识依据，不要杜撰引用。"
    return "The following JSON payload contains explicitly bound knowledge retrieval results. Use them only as evidence and do not invent citations."


def build_dag_agent_system_prompt(
    *,
    locale: str | None,
    current_date: date,
    task_prompt: str,
    memory_block: str,
    knowledge_enabled: bool,
) -> str:
    normalized = resolve_execution_locale(locale)
    language_instruction = build_runtime_language_instruction(normalized)
    if normalized == "zh":
        prompt = (
            "你是 MindAtlas AI 助手的 Agent 执行节点。\n\n"
            f"## 当前日期\n{current_date.isoformat()}（{current_date.strftime('%A')}）\n\n"
            f"## 任务\n{task_prompt}\n\n"
            "你可以根据用户输入自主决定是否调用工具。\n\n"
            f"## 语言要求\n{language_instruction}"
        )
        if knowledge_enabled:
            prompt += (
                f"\n\n{build_kb_citation_instructions(normalized)}\n\n"
                "## 知识库使用要求\n"
                "当问题可能依赖已有记录或知识库内容时，你应优先调用 kb_search。\n"
                "如果使用了 kb_search 返回的 references，回答中必须使用 [^n] 引用标记，且编号只能来自 kb_search.references。\n"
            )
    else:
        prompt = (
            "You are an agent execution node inside the MindAtlas AI assistant.\n\n"
            f"## Current Date\n{current_date.isoformat()} ({current_date.strftime('%A')})\n\n"
            f"## Task\n{task_prompt}\n\n"
            "Decide on your own whether tool calls are needed based on the user's input.\n\n"
            f"## Language Requirement\n{language_instruction}"
        )
        if knowledge_enabled:
            prompt += (
                f"\n\n{build_kb_citation_instructions(normalized)}\n\n"
                "## Knowledge Base Guidance\n"
                "When the answer may depend on existing notes or stored knowledge, prefer calling kb_search first.\n"
                "If you use references from kb_search, cite them with [^n] and only use indices from kb_search.references.\n"
            )
    if memory_block:
        prompt = f"{prompt}\n\n{memory_block}"
    return prompt


def build_param_extractor_system_prompt(
    *,
    locale: str | None,
    instruction: str,
    constraint: str,
) -> str:
    normalized = resolve_execution_locale(locale)
    language_instruction = build_runtime_language_instruction(normalized)
    if normalized == "zh":
        prompt = (
            "你是结构化参数提取器。"
            "你的任务是根据输入内容，提取目标字段并严格返回一个 JSON 对象。"
            f"\n\n语言要求：{language_instruction}"
        )
        if instruction.strip():
            prompt += f"\n\n额外提取说明：\n{instruction.strip()}"
    else:
        prompt = (
            "You are a structured parameter extractor. "
            "Extract the requested fields from the input and return exactly one JSON object."
            f"\n\nLanguage requirement: {language_instruction}"
        )
        if instruction.strip():
            prompt += f"\n\nAdditional extraction guidance:\n{instruction.strip()}"
    if constraint:
        prompt += f"\n\n{constraint}"
    return prompt


def build_system_behavior_agent_contract(
    *,
    locale: str | None,
    behavior_name: str,
    base_prompt: str,
) -> str:
    normalized = resolve_execution_locale(locale)
    language_instruction = build_runtime_language_instruction(normalized)
    if normalized == "zh":
        contract = (
            f"你正在执行系统 AI 行为“{behavior_name}”。\n"
            "如有必要你可以调用工具。\n"
            f"语言要求：{language_instruction}\n"
            "最终答案必须是且只能是一个 JSON 对象，并且严格包含以下字段：\n"
            '- "summary": string\n'
            '- "suggestions": string[]\n'
            '- "trends": string\n'
            "禁止输出 Markdown 围栏或 JSON 对象之外的额外文本。"
        )
    else:
        contract = (
            f"You are executing the system AI behavior '{behavior_name}'.\n"
            "You may use tools if needed.\n"
            f"Language requirement: {language_instruction}\n"
            "Your final answer must be exactly one JSON object with these fields:\n"
            '- "summary": string\n'
            '- "suggestions": string[]\n'
            '- "trends": string\n'
            "Do not output Markdown fences or any prose outside the JSON object."
        )
    trimmed_base = str(base_prompt or "").strip()
    if not trimmed_base:
        return contract
    return f"{trimmed_base}\n\n{contract}"


def build_system_behavior_agent_user_input(
    *,
    locale: str | None,
    behavior_name: str,
    payload_json: str,
) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return (
            f"请执行系统 AI 行为“{behavior_name}”。以下是结构化输入。\n"
            "如有需要，你可以根据时间范围检索相关记录。\n\n"
            f"{payload_json}"
        )
    return (
        f"Run the system AI behavior '{behavior_name}' using the following structured input.\n"
        "Use the time range to inspect relevant records if needed.\n\n"
        f"{payload_json}"
    )


def build_agent_iterations_exhausted_message(locale: str | None) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return "工具调用次数过多，未能完成任务。请尝试缩小问题范围或换一种问法。"
    return "The task could not be completed because too many tool calls were needed. Try narrowing the request or rephrasing it."


def build_tool_unavailable_message(locale: str | None, tool_name: str) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return f"工具不可用: {tool_name}"
    return f"Tool unavailable: {tool_name}"


def build_tool_execution_failed_message(locale: str | None, error: Any) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return f"工具执行失败: {error}"
    return f"Tool execution failed: {error}"


def build_knowledge_failure_message(locale: str | None, error: Any) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return f"知识库检索失败: {error}"
    return f"Knowledge retrieval failed: {error}"


def build_knowledge_unavailable_message(locale: str | None) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return "知识库工具不可用"
    return "Knowledge retrieval tool unavailable"


def build_knowledge_result_fallback(locale: str | None, references_count: int) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return f"检索到 {references_count} 条参考资料"
    return f"Retrieved {references_count} references"


def build_internal_kb_tool_description(locale: str | None) -> str:
    normalized = resolve_execution_locale(locale)
    if normalized == "zh":
        return "检索知识库中的相关记录与引用。当回答依赖已有笔记或存量知识时使用。"
    return "Search the knowledge base for relevant records and references. Use this when the answer may depend on existing notes or stored knowledge."
