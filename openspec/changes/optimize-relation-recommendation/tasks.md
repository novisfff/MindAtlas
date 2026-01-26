# Tasks: 优化 Relation 推荐机制

## Phase 1: 单阶段整合 (已完成)

### 1. 新增底层调用函数

- [x] 1.1 新增 `_call_rag_relation_recommend_sync` 函数
  - 参考 `_call_rag_graph_recall_sync` 机制
  - **关键**: `only_need_context=False`（默认），让 LLM 响应 JSON
  - 返回: `tuple[str, list[dict]]` → (llm_answer, chunks)

### 2. 新增 Service 方法

- [x] 2.1 新增 `LightRagService.relation_recommend_with_llm` 异步方法
  - 封装底层调用，处理并发/超时/异常
  - 返回: (llm_json_str, chunks)

### 3. 重构 recommend_entry_relations

- [x] 3.1 修改为单阶段流程
  - 构建带关系推荐指令的 prompt
  - 一次调用获取 chunks + llm_response
  - 用 chunks 的 doc_id 作为白名单校验 LLM 输出

### 4. 校验与兜底

- [x] 4.1 保留白名单校验（targetEntryId 必须在 chunks doc_id 中）
- [x] 4.2 JSON 解析失败时返回 top scored candidates（relation_type=None）

---

## Phase 2: 整合知识图谱元素 + LLM 推荐度 (单阶段)

### 6. 修改 Prompt

- [x] 6.1 重写 `_ENTRY_RELATION_RECOMMENDATION_ROLE`
  - 说明 targetEntryId 来源是 file_path 字段
  - 要求结合 chunks/entities/relationships 分析
  - **约束**: 不得编造 UUID，只能使用检索结果中的 file_path
- [x] 6.2 重写 `_build_entry_relation_recommendation_prompt`
  - 新增 relevance 字段要求 (0.30-1.00)
  - 添加严格的打分标准（见 design.md）
  - **约束**: relevance < 0.30 不输出
- [x] 6.3 更新 JSON 输出格式
  - `{"relations":[{"targetEntryId":"UUID","relationType":"CODE","relevance":0.85}]}`
  - **约束**: 纯 JSON，无 markdown 包裹

### 7. 扩展数据提取

- [x] 7.1 修改 `_call_rag_relation_recommend_sync` 返回类型
  - 从 `tuple[str, list[dict]]` 改为 `tuple[str, dict[str, Any]]`
  - 使用 `_extract_graph_context` 提取完整上下文
  - **约束**: graph_context 必须包含 chunks/entities/relationships 三个 key
- [x] 7.2 新增 `_extract_candidate_entry_ids` 辅助函数
  - 从 chunks 提取: `chunk['doc_id']` 或 `chunk['file_path']`
  - 从 entities 提取: `entity['entry_id']`
  - 从 relationships 提取: `relationship['entry_id']`
  - **约束**: 无效 UUID 必须 try/except drop
- [x] 7.3 修改 `relation_recommend_with_llm` 返回类型
  - 同步修改为 `tuple[str, dict[str, Any]]`

### 8. 解析与校验

- [x] 8.1 新增 `_parse_relation_recommendation_payload` 函数
  - 解析 targetEntryId, relationType, relevance 三个字段
  - **约束**: relevance 必须 clamp 到 [0.0, 1.0]
  - **约束**: NaN/Infinity 必须 drop (math.isfinite)
  - **约束**: 重复 targetEntryId 取 max(relevance)
- [x] 8.2 白名单校验
  - targetEntryId 必须在 candidate_ids 中
  - targetEntryId != source_entry_id
- [x] 8.3 更新 `recommend_entry_relations`
  - 使用 relevance 作为 score
  - 应用 min_score 阈值过滤

### 9. 测试

- [x] 9.1 更新 `_extract_candidate_entry_ids` 测试
  - 测试从 chunks/entities/relationships 提取
  - 测试无效 UUID drop
- [x] 9.2 更新 `_parse_relation_recommendation_payload` 测试
  - 测试 relevance 解析 (数值/字符串/缺失/NaN)
  - 测试重复 targetEntryId 去重
  - 测试白名单校验
- [x] 9.3 集成测试
  - 测试完整流程 graph_context → 候选提取 → LLM 解析 → 输出
