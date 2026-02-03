# Proposal: Attachment Knowledge Graph Integration

## Context

### User Need
用户希望将上传的附件（PDF、Office 文档、图片）解析后加载到知识图谱中，以便通过 LightRAG 进行语义检索和关联分析。

### Background
- MindAtlas 已有完整的附件上传功能（MinIO 存储）
- 已有 LightRAG 知识图谱实现
- 已有 Outbox Pattern + Worker 独立进程的异步任务模式

### Discovered Constraints

#### Hard Constraints (技术限制)
1. **HC-1**: 必须复用现有 Outbox Pattern 异步任务模式
2. **HC-2**: 文档解析服务必须作为独立 Python 进程运行
3. **HC-3**: 解析内容必须关联到所属 Entry 进行索引（非独立文档节点）
4. **HC-4**: 必须使用 Docling 库进行文档解析

#### Soft Constraints (约定/偏好)
1. **SC-1**: 遵循现有模块结构：`models.py → schemas.py → service.py → router.py`
2. **SC-2**: 前端遵循 Feature-based 架构
3. **SC-3**: API 响应遵循统一格式 `{code, message, data}`
4. **SC-4**: 使用 TanStack Query 进行数据请求

#### Dependencies (跨模块依赖)
1. **DEP-1**: 依赖 `app/attachment/` 模块获取文件信息
2. **DEP-2**: 依赖 `app/lightrag/` 模块进行知识图谱索引
3. **DEP-3**: 依赖 MinIO 存储读取文件内容
4. **DEP-4**: 依赖 `app/entry/` 模块获取 Entry 信息

---

## Requirements

### R1: 文件上传时选择是否解析到知识图谱

**Scenario**: 用户上传附件时，可以选择是否将该文件解析并加载到知识图谱

**Acceptance Criteria**:
- [ ] 上传 API 接受 `indexToKnowledgeGraph: boolean` 参数
- [ ] 前端 FileUpload 组件显示开关/复选框供用户选择
- [ ] 支持批量上传，每个文件独立选择是否解析
- [ ] 选择解析时，创建解析任务记录

### R2: 支持多种文件格式解析

**Scenario**: 系统能够解析 PDF、Office 文档和图片文件

**Acceptance Criteria**:
- [ ] 支持 PDF 文件解析
- [ ] 支持 Word (.docx) 文件解析
- [ ] 支持 Excel (.xlsx) 文件解析
- [ ] 支持 PowerPoint (.pptx) 文件解析
- [ ] 支持图片 OCR 识别（PNG, JPG, JPEG）
- [ ] 不支持的格式返回明确错误

### R3: 异步任务处理

**Scenario**: 文档解析作为异步任务在独立进程中执行

**Acceptance Criteria**:
- [ ] 创建 `AttachmentParseOutbox` 表存储解析任务
- [ ] 独立 Worker 进程轮询并处理任务
- [ ] Worker 使用 `FOR UPDATE SKIP LOCKED` 实现并发安全
- [ ] 支持配置 `poll_interval_ms`, `batch_size`, `max_attempts`

### R4: 解析状态追踪与反馈

**Scenario**: 用户可以查看附件的解析状态

**Acceptance Criteria**:
- [ ] Attachment 模型增加 `parse_status` 字段 (pending/processing/completed/failed)
- [ ] 前端附件列表显示解析状态图标/标签
- [ ] 提供 API 查询附件解析状态
- [ ] 前端定期轮询更新状态

### R5: 错误处理与重试机制

**Scenario**: 解析失败时自动重试，超过次数后标记失败并支持手动重试

**Acceptance Criteria**:
- [ ] 自动重试最多 3 次（可配置）
- [ ] 重试使用指数退避策略
- [ ] 超过重试次数标记为 `failed` 状态
- [ ] 记录失败原因到 `last_error` 字段
- [ ] 提供手动重试 API
- [ ] 前端显示重试按钮（仅失败状态）

### R6: 知识图谱索引

**Scenario**: 解析后的内容与所属 Entry 关联并索引到 LightRAG

**Acceptance Criteria**:
- [ ] 解析内容追加到 Entry 的索引文本中
- [ ] 触发 Entry 的 LightRAG 重新索引
- [ ] 删除附件时从索引中移除对应内容
- [ ] 索引文本包含文件名元数据便于溯源

---

## Success Criteria

1. **功能完整性**: 用户可以上传文件并选择解析到知识图谱，解析完成后可通过 LightRAG 检索到文件内容
2. **状态可见性**: 用户可以在前端看到每个附件的解析状态（pending/processing/completed/failed）
3. **错误恢复**: 解析失败的文件可以手动重试
4. **性能隔离**: 文档解析不影响主应用响应时间

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Docling 依赖较重（需要 PyTorch） | 部署复杂度增加 | 独立进程/容器部署，不影响主应用 |
| 大文件解析耗时长 | 用户等待体验差 | 异步处理 + 状态轮询反馈 |
| OCR 识别准确率不稳定 | 索引质量下降 | 记录原始文件路径便于人工校验 |
| 并发解析资源竞争 | Worker 性能下降 | 配置 batch_size 限制并发数 |

---

## Confirmed Decisions

| Decision Point | Choice | Rationale |
|----------------|--------|-----------|
| Worker 部署方式 | 独立 Python 进程 (parse-worker) | 隔离 Docling 重依赖，独立资源配置 |
| 前端交互模式 | 全局开关 + 立即上传 | 实现简单，MVP 阶段足够 |
| 解析文本存储 | PostgreSQL TEXT 字段 | 事务一致性好，文件限制下文本量可控 |
| 文件大小限制 | 100MB / 500页 | 平衡功能与资源消耗 |
| 轮询间隔 | 3 秒 | 平衡实时性与服务器压力 |
| 自动重试次数 | 3 次 | 足够处理瞬态错误 |

---

## Out of Scope

- 实时 WebSocket 进度推送（使用轮询替代）
- 视频/音频文件解析
- 自定义解析规则配置
- 解析结果人工编辑
