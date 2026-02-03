# Tasks: Attachment Knowledge Graph Integration

## Phase 1: Database Schema

### T1.1: Create migration for attachment_parse_outbox table
**File**: `backend/alembic/versions/xxxx_add_attachment_parse_outbox.py`
**Actions**:
- Create `attachment_parse_outbox` table with columns: id, attachment_id, entry_id, status, attempts, available_at, locked_at, locked_by, last_error, created_at, updated_at
- Add index `idx_attachment_parse_outbox_pending` on (status, available_at)
- Add FK constraint to attachment(id) with ON DELETE CASCADE

### T1.2: Add columns to attachment table
**File**: `backend/alembic/versions/xxxx_add_attachment_parse_columns.py`
**Actions**:
- Add `index_to_knowledge_graph` BOOLEAN DEFAULT FALSE
- Add `parse_status` VARCHAR(20) DEFAULT NULL
- Add `parsed_text` TEXT
- Add `parsed_at` TIMESTAMP WITH TIME ZONE
- Add `parse_last_error` TEXT

---

## Phase 2: Backend Models & Schemas

### T2.1: Create AttachmentParseOutbox model
**File**: `backend/app/attachment/models.py`
**Actions**:
- Add `AttachmentParseOutbox` class mirroring `EntryIndexOutbox` structure
- Define status enum: pending, processing, succeeded, dead

### T2.2: Update Attachment model
**File**: `backend/app/attachment/models.py`
**Actions**:
- Add new columns to `Attachment` class
- Add relationship to `AttachmentParseOutbox`

### T2.3: Update attachment schemas
**File**: `backend/app/attachment/schemas.py`
**Actions**:
- Add `indexToKnowledgeGraph` to upload request
- Add `parseStatus`, `parsedAt`, `parseLastError` to response
- Add `AttachmentRetryRequest` schema

---

## Phase 3: Backend Services & APIs

### T3.1: Create AttachmentParseOutboxRepo
**File**: `backend/app/attachment/outbox_repo.py`
**Actions**:
- Implement `claim_batch()` with FOR UPDATE SKIP LOCKED
- Implement `mark_succeeded()`, `mark_retry()`, `mark_dead()`
- Mirror `backend/app/lightrag/outbox_repo.py` pattern

### T3.2: Update AttachmentService.upload()
**File**: `backend/app/attachment/service.py`
**Actions**:
- Accept `index_to_knowledge_graph` parameter
- Validate file type if indexing requested
- Create `AttachmentParseOutbox` record if indexing
- Set `attachment.parse_status = 'pending'`

### T3.3: Add retry_parse() method
**File**: `backend/app/attachment/service.py`
**Actions**:
- Validate current status is 'failed'
- Reset `parse_status` to 'pending'
- Create new outbox record or reset existing

### T3.4: Update attachment router
**File**: `backend/app/attachment/router.py`
**Actions**:
- Add `index_to_knowledge_graph` Form parameter to upload endpoint
- Add `POST /api/attachments/{id}/retry` endpoint
- Update response to include parse fields

---

## Phase 4: Docling Worker

### T4.1: Create worker configuration
**File**: `backend/app/config.py`
**Actions**:
- Add `docling_worker_enabled`, `docling_worker_poll_interval_ms`
- Add `docling_worker_batch_size`, `docling_worker_max_attempts`
- Add `docling_worker_lock_ttl_sec`
- Add `docling_max_file_size_mb`, `docling_max_pdf_pages`

### T4.2: Create Docling parser module
**File**: `backend/app/attachment/parser.py`
**Actions**:
- Implement `parse_document(file_path, content_type)` → str
- Handle PDF, DOCX, XLSX, PPTX, images
- Enforce page limit for PDFs
- Return extracted text or raise ParseError

### T4.3: Create parse worker
**File**: `backend/app/attachment/worker.py`
**Actions**:
- Implement `Worker` class mirroring `lightrag/worker.py`
- Download file from MinIO to temp path
- Call parser, store result in `attachment.parsed_text`
- Update `attachment.parse_status`
- Enqueue Entry reindex on success

### T4.4: Create worker entry point
**File**: `backend/app/attachment/worker.py`
**Actions**:
- Add `main()` function with signal handling
- Mirror `lightrag/worker.py:main()` pattern

---

## Phase 5: LightRAG Integration

### T5.1: Update document payload builder
**File**: `backend/app/lightrag/documents.py`
**Actions**:
- Modify `build_document_payload()` to include attachment texts
- Format: `--- Attachment: {filename} ---\n{parsed_text}`
- Query attachments with `parse_status='completed'`

### T5.2: Add reindex helper
**File**: `backend/app/attachment/service.py`
**Actions**:
- Add `_enqueue_entry_reindex(entry_id)` method
- Reuse `EntryService._coalesce_upsert_outbox()` logic

### T5.3: Trigger reindex on attachment delete
**File**: `backend/app/attachment/service.py`
**Actions**:
- In `delete()`, check if attachment had `parse_status='completed'`
- If yes, enqueue Entry reindex to remove attachment text

---

## Phase 6: Frontend Types & API

### T6.1: Update Attachment type
**File**: `frontend/src/types/index.ts`
**Actions**:
- Add `indexToKnowledgeGraph?: boolean`
- Add `parseStatus?: 'pending' | 'processing' | 'completed' | 'failed'`
- Add `parsedAt?: string`
- Add `parseLastError?: string`

### T6.2: Update upload API
**File**: `frontend/src/features/attachments/api/attachments.ts`
**Actions**:
- Add `indexToKnowledgeGraph` parameter to `uploadAttachment()`
- Append to FormData

### T6.3: Add retry API
**File**: `frontend/src/features/attachments/api/attachments.ts`
**Actions**:
- Add `retryAttachmentParse(id: string)` function

---

## Phase 7: Frontend Components

### T7.1: Add index toggle to FileUpload
**File**: `frontend/src/features/attachments/components/FileUpload.tsx`
**Actions**:
- Add `indexToKnowledgeGraph` state (default: true)
- Add Switch/Checkbox above drop zone
- Pass value to `onUpload` callback

### T7.2: Update FileUpload props
**File**: `frontend/src/features/attachments/components/FileUpload.tsx`
**Actions**:
- Change `onUpload: (file: File) => void`
- To `onUpload: (file: File, indexToKg: boolean) => void`

### T7.3: Add status indicators to AttachmentList
**File**: `frontend/src/features/attachments/components/AttachmentList.tsx`
**Actions**:
- Import status icons (Loader2, CheckCircle, AlertCircle)
- Render icon based on `parseStatus`
- Show tooltip with `parseLastError` for failed items

### T7.4: Add retry button
**File**: `frontend/src/features/attachments/components/AttachmentList.tsx`
**Actions**:
- Add RefreshCw icon button for failed items
- Call `retryAttachmentParse` mutation on click
- Disable during mutation pending

---

## Phase 8: Frontend Queries & Polling

### T8.1: Update upload mutation
**File**: `frontend/src/features/attachments/queries.ts`
**Actions**:
- Update `useUploadAttachmentMutation` to accept `indexToKg` param
- Pass to `uploadAttachment()` API call

### T8.2: Add retry mutation
**File**: `frontend/src/features/attachments/queries.ts`
**Actions**:
- Add `useRetryAttachmentParseMutation()`
- Invalidate attachments query on success

### T8.3: Implement smart polling
**File**: `frontend/src/features/attachments/queries.ts`
**Actions**:
- Update `useEntryAttachmentsQuery` with `refetchInterval` function
- Poll every 3s if any attachment has pending/processing status
- Stop polling when all terminal states

---

## Phase 9: Deployment

### T9.1: Create Docling requirements file
**File**: `backend/requirements-docling.txt`
**Actions**:
- Add `docling` with version pin
- Add OCR dependencies (tesseract bindings if needed)

### T9.2: Add Dockerfile target for parse-worker
**File**: `backend/Dockerfile`
**Actions**:
- Add `parse-worker` target
- Install system deps (poppler, tesseract)
- Install requirements-docling.txt
- Set entrypoint to `python -m app.attachment.worker`

### T9.3: Add docker-compose service
**File**: `deploy/docker-compose.yml`
**Actions**:
- Add `parse-worker` service
- Use `parse-worker` target
- Configure environment variables
- Set resource limits

---

## Phase 10: Internationalization

### T10.1: Add English translations
**File**: `frontend/src/locales/en/translation.json`
**Actions**:
- Add `attachment.indexToKnowledgeGraph`: "Index to Knowledge Graph"
- Add `attachment.parseStatus.pending`: "Parsing..."
- Add `attachment.parseStatus.processing`: "Processing..."
- Add `attachment.parseStatus.completed`: "Indexed"
- Add `attachment.parseStatus.failed`: "Parse failed"
- Add `attachment.retry`: "Retry"

### T10.2: Add Chinese translations
**File**: `frontend/src/locales/zh/translation.json`
**Actions**:
- Add corresponding Chinese translations

---

## Phase 11: Testing

### T11.1: Backend unit tests
**File**: `backend/tests/test_attachment_parse.py`
**Actions**:
- Test upload with indexToKnowledgeGraph flag
- Test file type validation
- Test retry endpoint
- Test outbox creation/coalescing

### T11.2: Worker integration tests
**File**: `backend/tests/test_attachment_worker.py`
**Actions**:
- Test parse success flow
- Test retry on transient error
- Test max attempts → dead
- Test Entry reindex triggered

---

## Task Dependencies

```
T1.1 ─┬─► T2.1 ─► T3.1 ─► T4.3
      │
T1.2 ─┴─► T2.2 ─► T3.2 ─┬─► T5.1
                        │
                  T3.3 ─┤
                        │
                  T3.4 ─┴─► T6.2 ─► T7.1 ─► T8.1
                                    │
                              T6.3 ─► T7.4 ─► T8.2
                                    │
                              T6.1 ─► T7.3 ─► T8.3

T4.1 ─► T4.2 ─► T4.3 ─► T4.4

T9.1 ─► T9.2 ─► T9.3

T10.1, T10.2 (parallel, no deps)
T11.1, T11.2 (after all implementation)
```
