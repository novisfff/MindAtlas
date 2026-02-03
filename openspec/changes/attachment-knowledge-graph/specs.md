# Specifications: Attachment Knowledge Graph Integration

## Requirements with PBT Properties

### R1: Upload with Index Option

**Requirement**: 上传 API 接受 `indexToKnowledgeGraph` 参数

**Invariants**:
- INV-1.1: `indexToKnowledgeGraph=true` → 必创建 `attachment_parse_outbox` 记录
- INV-1.2: `indexToKnowledgeGraph=false` → `attachment.parse_status` 保持 NULL
- INV-1.3: 上传成功 → `attachment` 记录必存在

**Falsification Strategy**:
```python
@given(index_flag=st.booleans())
def test_upload_creates_outbox_iff_index_true(index_flag):
    attachment = upload_file(file, index_to_kg=index_flag)
    outbox_exists = db.query(AttachmentParseOutbox).filter_by(
        attachment_id=attachment.id
    ).count() > 0
    assert outbox_exists == index_flag
```

---

### R2: File Type Validation

**Requirement**: 仅支持 PDF、Office、图片格式

**Invariants**:
- INV-2.1: 不支持的格式 → 返回 400 错误，不创建 outbox
- INV-2.2: 支持的格式 + `indexToKnowledgeGraph=true` → 创建 outbox

**Supported Extensions**: `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.png`, `.jpg`, `.jpeg`

**Falsification Strategy**:
```python
SUPPORTED = {'.pdf', '.docx', '.xlsx', '.pptx', '.png', '.jpg', '.jpeg'}

@given(ext=st.sampled_from(['.exe', '.zip', '.mp4', '.txt']))
def test_unsupported_format_rejected(ext):
    with pytest.raises(ApiException) as exc:
        upload_file(f"test{ext}", index_to_kg=True)
    assert exc.value.code == 40001  # unsupported format
```

---

### R3: File Size Limits

**Requirement**: 单文件最大 100MB，PDF 最多 500 页

**Invariants**:
- INV-3.1: `file_size > 100MB` → 返回 413 错误
- INV-3.2: `pdf_pages > 500` → 解析时标记 failed（非上传时）

**Falsification Strategy**:
```python
@given(size_mb=st.integers(min_value=101, max_value=200))
def test_oversized_file_rejected(size_mb):
    large_file = generate_file(size_mb * 1024 * 1024)
    with pytest.raises(ApiException) as exc:
        upload_file(large_file, index_to_kg=True)
    assert exc.value.status_code == 413
```

---

### R4: Outbox Processing (Idempotency)

**Requirement**: Worker 处理任务具有幂等性

**Invariants**:
- INV-4.1: 同一 attachment 最多一个 pending/processing outbox
- INV-4.2: 重复处理同一 attachment → 结果一致
- INV-4.3: `attempts >= max_attempts` → status 变为 `dead`

**Falsification Strategy**:
```python
def test_outbox_idempotency():
    attachment = upload_file(file, index_to_kg=True)
    # Simulate duplicate outbox creation
    create_outbox(attachment.id)
    create_outbox(attachment.id)
    count = db.query(AttachmentParseOutbox).filter(
        attachment_id=attachment.id,
        status.in_(['pending', 'processing'])
    ).count()
    assert count == 1  # coalesced
```

---

### R5: Entry Reindex on Parse Completion

**Requirement**: 解析完成后触发 Entry 重新索引

**Invariants**:
- INV-5.1: parse_status=completed → entry_index_outbox 有 upsert 记录
- INV-5.2: 附件删除 → entry_index_outbox 有 upsert 记录（移除附件文本）
- INV-5.3: Entry 索引文本包含 `--- Attachment: {filename} ---` 标记

**Falsification Strategy**:
```python
def test_parse_completion_triggers_reindex():
    attachment = upload_and_parse(file, index_to_kg=True)
    outbox = db.query(EntryIndexOutbox).filter(
        entry_id=attachment.entry_id,
        created_at > attachment.parsed_at
    ).first()
    assert outbox is not None
    assert outbox.op == 'upsert'
```

---

### R6: Status Polling

**Requirement**: 前端轮询状态更新

**Invariants**:
- INV-6.1: 有 pending/processing 附件 → 轮询间隔 3s
- INV-6.2: 所有附件 completed/failed/null → 停止轮询
- INV-6.3: 轮询返回最新 parse_status

---

### R7: Manual Retry

**Requirement**: 失败任务可手动重试

**Invariants**:
- INV-7.1: parse_status=failed → retry API 返回 200
- INV-7.2: parse_status!=failed → retry API 返回 400
- INV-7.3: retry 后 parse_status=pending, attempts 重置

**Falsification Strategy**:
```python
@given(status=st.sampled_from(['pending', 'processing', 'completed']))
def test_retry_only_allowed_for_failed(status):
    attachment = create_attachment_with_status(status)
    with pytest.raises(ApiException) as exc:
        retry_parse(attachment.id)
    assert exc.value.code == 40002  # invalid state
