## Context

### Phase 1 (已完成)
当前 `recommend_entry_relations` 采用两阶段流程：
1. Phase-1: `recall_sources()` 向量检索获取候选
2. Phase-2: `llm_only_answer()` LLM 推断关系类型

单阶段整合目标：一次 LLM 调用完成"候选选择 + 关系类型确定"。

### Phase 2 (新增)
LightRAG 响应结构（`only_need_context=False` 时）：
```json
{
  "data": {
    "entities": [{"entity_name": "...", "file_path": "<entryId>", ...}],
    "relationships": [{"src_id": "...", "file_path": "<entryId>", ...}],
    "chunks": [{"content": "...", "file_path": "<entryId>", ...}]
  },
  "llm_response": {"content": "<JSON>"}
}
```

**问题**：当前只使用 chunks，遗漏了 entities/relationships 中的候选 entryId。
**问题**：chunks 不返回 score，需要改用 LLM 推荐度。

## Goals / Non-Goals

**Goals (Phase 1):**
- 减少 API 调用次数（2→1）
- 简化代码链路
- 保持推荐质量和 ID 校验严格性

**Goals (Phase 2):**
- 整合 entities/relationships 中的候选 entryId
- 用 LLM 推荐度替代不可用的 chunk score
- 提供更丰富的推荐来源

**Non-Goals:**
- 不改变检索算法本身
- 不修改 RelationType 数据模型
- 不改变 API 响应格式（score 字段语义从"向量相似度"变为"LLM推荐度"）

## Decisions

### Decision 1: 单阶段整合

**选择**: 使用 `rag.query_llm` 一次调用同时获取 chunks（召回）+ llm_response（JSON）

**理由**:
- 一次调用完成召回+推理
- chunks 的 doc_id 作为后置白名单校验
- 参考 `_call_rag_graph_recall_sync`，但不设置 `only_need_context=True`

### Decision 2: 候选白名单嵌入方式

**选择**: 在 LLM prompt 中以表格形式列出候选 `doc_id | title | summary`

**理由**:
- 复用现有 `_build_entry_relation_recommendation_stage2_prompt` 逻辑
- 明确约束 LLM 只能从白名单中选择
- 提供足够上下文帮助 LLM 判断关系类型

### Decision 4: 整合知识图谱元素 (Phase 2)

**选择**: 从 entities 和 relationships 的 `file_path` 字段提取候选 entryId

**理由**:
- LightRAG 知识图谱中 `file_path` 存储的就是 entryId
- 参考 `_extract_graph_context` 方法的解析逻辑
- 扩大候选来源，提高推荐覆盖率

### Decision 5: LLM 推荐度替代 score (Phase 2)

**选择**: 让 LLM 在 JSON 输出中包含 `relevance` 字段 (0.0-1.0)

**理由**:
- `only_need_context=False` 时 chunks 不返回 score
- LLM 可以综合判断语义相关性、知识图谱关联强度
- 提供判断维度指令确保推荐度有意义

## Risks / Trade-offs

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| LLM 幻觉 ID | 中 | 低 | 白名单校验过滤 |
| 召回遗漏 | 低 | 中 | over-fetching + 可配置 top_k |
| JSON 解析失败 | 低 | 低 | 返回 top scored candidates (relation_type=None) |
| LLM 推荐度不准确 | 中 | 低 | 提供明确判断维度指令 |
| entities/relationships 数据量大 | 低 | 低 | 按 file_path 去重 |

## Migration Plan

1. 修改 `recommend_entry_relations` 内部实现
2. API 接口和响应格式保持不变
3. 无需数据迁移
4. 回滚：恢复两阶段代码即可

## Open Questions

- [x] ~~是否需要添加配置开关支持切换单阶段/两阶段模式？~~ → **已确认：不需要，直接替换为单阶段实现**
- [x] ~~relevance 缺失时如何处理？~~ → **已确认：drop 该项，不使用默认值**
- [x] ~~重复 targetEntryId 如何处理？~~ → **已确认：取 max(relevance)**
- [x] ~~fallback 降级策略？~~ → **已确认：返回空列表，不做弱推荐**

## Technical Implementation

### 核心调用方式

参考 `_call_rag_graph_recall_sync` 机制，新增 `_call_rag_relation_recommend_sync`:

```python
def _call_rag_relation_recommend_sync(
    *,
    prompt: str,
    mode: LightRagQueryMode,
    top_k: int,
    chunk_top_k: int,
    timeout_sec: float = 30.0,
) -> tuple[str, list[dict]]:
    """单阶段关系推荐：召回 + LLM JSON 响应"""

    param = QueryParam(
        mode=mode,
        top_k=top_k,
        chunk_top_k=chunk_top_k,
        stream=False,
        # 关键：不设置 only_need_context=True
        # 让 LLM 生成 JSON 响应
    )
    raw = rag.query_llm(prompt, param=param)

    # 提取 LLM 响应
    llm_answer = raw.get("llm_response", {}).get("content", "")
    chunks = _extract_query_llm_chunks(raw)

    return llm_answer, chunks
```

### 关键区别

| 方法 | only_need_context | LLM 响应 |
|------|-------------------|----------|
| `_call_rag_graph_recall_sync` | True | 无 |
| `_call_rag_relation_recommend_sync` | False (默认) | JSON |

---

## Phase 2 Technical Implementation

### 修改后的返回类型

```python
def _call_rag_relation_recommend_sync(
    *,
    prompt: str,
    mode: LightRagQueryMode,
    top_k: int,
    chunk_top_k: int,
    timeout_sec: float = 30.0,
) -> tuple[str, dict[str, Any]]:
    """返回: (llm_answer, graph_context)"""
    # graph_context = {
    #     "chunks": [...],
    #     "entities": [...],
    #     "relationships": [...]
    # }
```

### 候选 entryId 提取逻辑

```python
def _extract_candidate_entry_ids(graph_context: dict) -> set[UUID]:
    """从 chunks/entities/relationships 提取候选 entryId"""
    entry_ids: set[UUID] = set()

    # 1. 从 chunks 提取
    for chunk in graph_context.get("chunks") or []:
        file_path = chunk.get("file_path") or chunk.get("doc_id")
        if file_path:
            try:
                entry_ids.add(UUID(file_path))
            except: pass

    # 2. 从 entities 提取
    for ent in graph_context.get("entities") or []:
        entry_id = ent.get("entry_id")
        if entry_id:
            try:
                entry_ids.add(UUID(entry_id))
            except: pass

    # 3. 从 relationships 提取
    for rel in graph_context.get("relationships") or []:
        entry_id = rel.get("entry_id")
        if entry_id:
            try:
                entry_ids.add(UUID(entry_id))
            except: pass

    return entry_ids
```

### LLM 输出格式变更

**原格式**:
```json
{"relations":[{"targetEntryId":"UUID","relationType":"CODE"}]}
```

**新格式**:
```json
{"relations":[
  {"targetEntryId":"UUID","relationType":"CODE","relevance":0.85}
]}
```

### Prompt 设计规范 (Phase 2 更新)

#### Role 定义

```python
_ENTRY_RELATION_RECOMMENDATION_ROLE = """你是一个知识图谱关系推荐助手。

你的任务是：
1. 阅读用户提供的"当前记录内容"
2. 分析系统检索到的三类数据：chunks（文档片段）、entities（实体）、relationships（关系）
3. 从这些数据的 file_path 字段中识别候选 Entry ID
4. 推荐最相关的 Entry 并给出关系类型和推荐度评分

重要说明：
- targetEntryId 必须是数据中 file_path 字段的值（UUID格式）
- 只能推荐在检索结果中出现的 Entry，不要编造 ID
"""
```

#### Relevance 评分标准

```
relevance 评分标准（0.0-1.0，精确到小数点后两位）：

0.90-1.00 强相关
- 直接的概念依赖或包含关系
- 同一主题的核心组成部分
- 明确的因果或前置关系

0.70-0.89 高度相关
- 同一知识领域的重要关联
- 共享多个实体或概念
- 互补性知识内容

0.50-0.69 中等相关
- 存在间接关联
- 共享少量实体或概念
- 同一大类但不同子领域

0.30-0.49 弱相关
- 仅有边缘关联
- 可能有用但非必要的参考

低于 0.30 不推荐
```

#### Instructions 模板

```python
instructions = f"""
分析依据：
- chunks: 文档片段，包含实际内容
- entities: 知识图谱中的实体节点
- relationships: 实体之间的已有关系

输出要求：
- 返回纯 JSON，无 markdown 包裹
- 格式: {{"relations":[{{"targetEntryId":"<UUID>","relationType":"<CODE>","relevance":<0.0-1.0>}}]}}
- 最多 {limit} 条推荐
- relationType 必须是: {codes}
- targetEntryId 必须来自检索数据的 file_path 字段
- relevance 必须在 0.30-1.00 之间，低于 0.30 不要推荐
"""
```

---

## Constraints (Phase 2)

### Hard Constraints

| ID | 约束 | 验证方式 |
|----|------|----------|
| HC-1 | targetEntryId 必须来自 graph_context 的 file_path/doc_id | 白名单校验 |
| HC-2 | relevance 必须在 [0.0, 1.0] 范围内 | clamp + isfinite 检测 |
| HC-3 | relevance < 0.30 的项不输出 | 服务端过滤 |
| HC-4 | 重复 targetEntryId 取 max(relevance) | 去重逻辑 |
| HC-5 | targetEntryId == source_entry_id 必须过滤 | 服务端强制过滤 |
| HC-6 | 无效 UUID 必须 drop | try/except |

### Soft Constraints

| ID | 约束 | 说明 |
|----|------|------|
| SC-1 | relevance 精确到两位小数 | prompt 指令 |
| SC-2 | JSON 输出无 markdown 包裹 | prompt 指令 |
| SC-3 | 按 relevance 降序输出 | prompt 指令 |

---

## PBT Properties

### P1: 白名单不变性
```
INVARIANT: ∀ item ∈ output.items: item.target_entry_id ∈ candidate_ids
FALSIFICATION: 生成随机 UUID 不在 candidate_ids 中，验证被过滤
```

### P2: Relevance 范围不变性
```
INVARIANT: ∀ item ∈ output.items: 0.0 ≤ item.score ≤ 1.0
FALSIFICATION: 注入 relevance=-0.5, 1.5, NaN, Infinity，验证被 clamp 或 drop
```

### P3: 去重幂等性
```
INVARIANT: len(set(item.target_entry_id for item in output.items)) == len(output.items)
FALSIFICATION: 输入重复 targetEntryId，验证输出无重复
```

### P4: 自引用过滤
```
INVARIANT: ∀ item ∈ output.items: item.target_entry_id ≠ source_entry_id
FALSIFICATION: LLM 输出包含 source_entry_id，验证被过滤
```
