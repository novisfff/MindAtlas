# Design: Attachment Knowledge Graph Integration

## Overview

将上传的附件（PDF、Office 文档、图片）解析后加载到 LightRAG 知识图谱中。

## Architecture Decision Records

### ADR-1: Worker 部署方式
**Decision**: 独立 Python 进程（parse-worker）
**Rationale**:
- Docling 依赖较重（PyTorch），需要隔离
- 不同资源配置（CPU/RAM 峰值）
- 不同故障特征（解析错误 vs 图存储错误）

### ADR-2: 解析文本存储
**Decision**: PostgreSQL TEXT 字段 (`attachment.parsed_text`)
**Rationale**: 简单直接，事务一致性好，100MB/500页限制下文本量可控

### ADR-3: 知识图谱索引方式
**Decision**: 追加到 Entry 索引文本，不创建独立文档节点
**Rationale**: 符合 HC-3 约束，保持 Entry 为核心实体

### ADR-4: 前端交互模式
**Decision**: 全局开关 + 立即上传
**Rationale**: 实现简单，用户体验流畅，MVP 阶段足够

---

## Technical Specifications

### 1. Database Schema

#### 1.1 New Table: `attachment_parse_outbox`
```sql
CREATE TABLE attachment_parse_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attachment_id UUID NOT NULL REFERENCES attachment(id) ON DELETE CASCADE,
    entry_id UUID NOT NULL,  -- denormalized for convenience
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMP WITH TIME ZONE,
    locked_by VARCHAR(64),
    last_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_attachment_parse_outbox_pending
    ON attachment_parse_outbox(status, available_at)
    WHERE status IN ('pending', 'processing');
```

#### 1.2 Attachment Table Changes
```sql
ALTER TABLE attachment ADD COLUMN index_to_knowledge_graph BOOLEAN DEFAULT FALSE;
ALTER TABLE attachment ADD COLUMN parse_status VARCHAR(20) DEFAULT NULL;
ALTER TABLE attachment ADD COLUMN parsed_text TEXT;
ALTER TABLE attachment ADD COLUMN parsed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE attachment ADD COLUMN parse_last_error TEXT;
```

### 2. Configuration Parameters

```python
# backend/.env
DOCLING_WORKER_ENABLED=true
DOCLING_WORKER_POLL_INTERVAL_MS=2000
DOCLING_WORKER_BATCH_SIZE=1
DOCLING_WORKER_MAX_ATTEMPTS=3
DOCLING_WORKER_LOCK_TTL_SEC=600
DOCLING_MAX_FILE_SIZE_MB=100
DOCLING_MAX_PDF_PAGES=500
```

### 3. API Endpoints

#### 3.1 Upload with Index Option
```
POST /api/attachments/entry/{entry_id}
Content-Type: multipart/form-data

file: <binary>
indexToKnowledgeGraph: true|false
```

#### 3.2 Get Attachment Status
```
GET /api/attachments/{id}
Response: { parseStatus, parsedAt, parseLastError, ... }
```

#### 3.3 Manual Retry
```
POST /api/attachments/{id}/retry
Response: { parseStatus: "pending" }
```

### 4. Supported File Types

| Extension | MIME Type | Parser |
|-----------|-----------|--------|
| .pdf | application/pdf | Docling PDF |
| .docx | application/vnd.openxmlformats-officedocument.wordprocessingml.document | Docling Office |
| .xlsx | application/vnd.openxmlformats-officedocument.spreadsheetml.sheet | Docling Office |
| .pptx | application/vnd.openxmlformats-officedocument.presentationml.presentation | Docling Office |
| .png, .jpg, .jpeg | image/* | Docling OCR |

### 5. Status State Machine

```
                    ┌─────────────┐
                    │   (none)    │  ← 未选择索引
                    └─────────────┘
                          │
                          │ upload with indexToKnowledgeGraph=true
                          ▼
                    ┌─────────────┐
                    │   pending   │
                    └─────────────┘
                          │
                          │ worker claims
                          ▼
                    ┌─────────────┐
              ┌────▶│ processing  │◀────┐
              │     └─────────────┘     │
              │           │             │
              │    success│    error    │
              │           ▼             │
              │     ┌───────────┐       │
              │     │ completed │       │
              │     └───────────┘       │
              │                         │
              │  retry < max    retry >= max
              │           │             │
              │           ▼             ▼
              │     ┌───────────┐ ┌──────────┐
              └─────│  (retry)  │ │  failed  │
                    └───────────┘ └──────────┘
                                       │
                                       │ manual retry
                                       ▼
                                 ┌─────────────┐
                                 │   pending   │
                                 └─────────────┘
```
