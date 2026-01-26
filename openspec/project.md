# Project Context

## Purpose

MindAtlas is a **personal knowledge and experience management system** that helps users record, connect, search, analyze, and summarize their knowledge and experiences.

**Core Goals**:
- Provide structured knowledge/experience recording capabilities (Entry)
- Support multi-dimensional classification (types, tags, relation networks)
- Enhance knowledge retrieval and analysis through AI
- Visualize knowledge graphs to discover connections between knowledge

**Design Principles**:
- Monolithic application first, avoid over-architecture
- Feature-first, rapid iteration
- AI-enhanced, not AI-dependent (core features work without AI)
- Data ownership, users have full control over their data

## Tech Stack

### Backend (Python)
- **Framework**: FastAPI
- **ORM**: SQLAlchemy
- **Database**: PostgreSQL 15+
- **Migration**: Alembic
- **Object Storage**: MinIO (S3-compatible)
- **AI/RAG**: LangChain, LightRAG, Neo4j (graph database)
- **Security**: Fernet symmetric encryption (API Key storage)

### Frontend (TypeScript)
- **Framework**: React 18
- **Build Tool**: Vite
- **State Management**: Zustand (global state) + TanStack Query (server state)
- **Styling**: Tailwind CSS
- **i18n**: react-i18next
- **UI Components**: Radix UI primitives
- **Visualization**: react-force-graph-2d (knowledge graph)

### Infrastructure
- **Container**: Docker + Docker Compose
- **Required Services**: PostgreSQL, MinIO, Neo4j (optional)

## Project Conventions

### Code Style

#### Backend (Python)

**File Header Convention**:
```python
from __future__ import annotations  # Required: enable postponed annotation evaluation

from typing import List, Optional   # Standard library
from uuid import UUID

from fastapi import APIRouter       # Third-party libraries
from sqlalchemy.orm import Session

from app.common.exceptions import ApiException  # Local modules
from app.entry.models import Entry
```

**Import Order** (separated by blank lines):
1. `from __future__ import annotations` (must be first line)
2. Standard library (`typing`, `uuid`, `datetime`, etc.)
3. Third-party libraries (`fastapi`, `sqlalchemy`, `pydantic`, etc.)
4. Local modules (`app.*`)

**Naming Conventions**:
| Type | Convention | Example |
|------|------------|---------|
| Module/File | snake_case | `entry_type.py` |
| Class | PascalCase | `EntryService`, `EntryResponse` |
| Function/Method | snake_case | `find_by_id()`, `create_entry()` |
| Variable | snake_case | `entry_id`, `tag_ids` |
| Constant | UPPER_SNAKE_CASE | `DEFAULT_PAGE_SIZE` |
| Private member | prefix `_` | `_validate_time_fields()` |

**Comment Convention**:
- **Language**: All code comments must be in English
- **Inline comments**: Brief explanations, on the same line or line above
- **Docstrings**: Use docstrings for public APIs, follow Google style
```python
def find_by_id(self, id: UUID) -> Entry:
    """Find an entry by its ID.

    Args:
        id: The UUID of the entry to find.

    Returns:
        The Entry object if found.

    Raises:
        ApiException: If entry not found (404).
    """
```

**Type Annotations**:
```python
# Function parameters and return values must have type annotations
def find_by_id(self, id: UUID) -> Entry:
    ...

# Use Optional for nullable types
def search(self, keyword: Optional[str] = None) -> List[Entry]:
    ...

# Use typing module for complex types
from typing import List, Optional, Dict
```

**Pydantic Schema Convention**:
```python
from app.common.schemas import CamelModel, OrmModel

# Request/Response models inherit from CamelModel (auto-converts to camelCase)
class EntryRequest(CamelModel):
    title: str = Field(..., min_length=1, max_length=255)
    type_id: UUID  # Python side: snake_case, JSON side: auto-converts to typeId

# ORM model responses inherit from OrmModel
class EntryResponse(OrmModel):
    id: UUID
    created_at: datetime  # Auto-converts to createdAt
```

**Service Class Pattern**:
```python
class EntryService:
    def __init__(self, db: Session):
        self.db = db

    def find_by_id(self, id: UUID) -> Entry:
        entry = self.db.query(Entry).filter(Entry.id == id).first()
        if not entry:
            raise ApiException(status_code=404, code=40400, message=f"Entry not found: {id}")
        return entry
```

**Router Definition Pattern**:
```python
router = APIRouter(prefix="/api/entries", tags=["entries"])

@router.get("/{id}", response_model=ApiResponse)
def get_entry(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = EntryService(db)
    entry = service.find_by_id(id)
    return ApiResponse.ok(EntryResponse.model_validate(entry).model_dump(by_alias=True))
```

**Exception Handling**:
```python
# Use ApiException to throw business exceptions
raise ApiException(status_code=404, code=40400, message="Entry not found")
raise ApiException(status_code=400, code=40001, message="Validation failed", details={"field": "error"})
```

#### Frontend (TypeScript)

**File Naming Convention**:
| Type | Convention | Example |
|------|------------|---------|
| Component file | PascalCase | `EntryCard.tsx`, `EntriesList.tsx` |
| Utility/API file | kebab-case | `api-client.ts`, `entries.ts` |
| Hook file | kebab-case | `use-entries.ts` |
| Type file | kebab-case | `types.ts`, `index.ts` |

**Import Order**:
```typescript
// 1. React related
import { useState, useEffect } from 'react'

// 2. Third-party libraries
import { useMutation, useQuery } from '@tanstack/react-query'

// 3. Path alias imports (@/*)
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'

// 4. Relative path imports
import { EntryCard } from './EntryCard'
```

**Component Definition Convention**:
```typescript
// Props interface defined before component
interface EntryCardProps {
  entry: Entry
  onClick?: (entry: Entry) => void
}

// Use function declaration + named export
export function EntryCard({ entry, onClick }: EntryCardProps) {
  // Component logic
}
```

**TanStack Query Pattern**:
```typescript
// queries.ts - Query Key factory pattern
export const entriesKeys = {
  all: ['entries'] as const,
  lists: () => [...entriesKeys.all, 'list'] as const,
  list: (params?: ListEntriesParams) => [...entriesKeys.lists(), params] as const,
  detail: (id: string) => [...entriesKeys.all, 'detail', id] as const,
}

// Query Hook
export function useEntryQuery(id?: string) {
  return useQuery({
    queryKey: id ? entriesKeys.detail(id) : entriesKeys.detail('__missing__'),
    queryFn: () => getEntry(id as string),
    enabled: Boolean(id),
  })
}

// Mutation Hook (with cache update)
export function useCreateEntryMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: EntryUpsertRequest) => createEntry(payload),
    onSuccess: (entry) => {
      queryClient.setQueryData(entriesKeys.detail(entry.id), entry)
      queryClient.invalidateQueries({ queryKey: entriesKeys.lists() })
    },
  })
}
```

**API Call Pattern**:
```typescript
// api/entries.ts
import { apiClient } from '@/lib/api/client'

export async function getEntry(id: string): Promise<Entry> {
  return apiClient.get<Entry>(`/api/entries/${encodeURIComponent(id)}`)
}

export async function createEntry(payload: EntryUpsertRequest): Promise<Entry> {
  return apiClient.post<Entry>('/api/entries', { body: payload })
}
```

**Styling Convention (Tailwind CSS)**:
```typescript
import { cn } from '@/lib/utils'

// Use cn() to merge class names, supports conditional classes
<div className={cn(
  'flex items-center rounded-lg border',
  isActive && 'bg-primary text-primary-foreground',
  className
)}>
```

**Path Alias**:
- `@/*` maps to `./src/*`
- Example: `import { cn } from '@/lib/utils'`

**TypeScript Configuration**:
- `strict: true` enables strict mode
- Use `interface` for object types
- Use `type` for union types or type aliases

### Architecture Patterns

**Backend Module Structure**:
```
app/{module}/
├── models.py      # SQLAlchemy ORM models
├── schemas.py     # Pydantic request/response models
├── service.py     # Business logic layer
└── router.py      # FastAPI route definitions
```

**Frontend Feature-based Structure**:
```
features/{module}/
├── api/           # API call functions
├── components/    # Module-specific components
├── pages/         # Page components
├── queries.ts     # TanStack Query hooks
└── index.ts       # Exports
```

**API Response Format** (unified):
```json
{
  "code": 0,
  "message": "OK",
  "data": { ... }
}
```

### Testing Strategy

- **Current Status**: Primarily unit tests, low coverage, to be improved incrementally
- **Backend**: Uses unittest, mainly covers service layer and utility functions
- **Test Location**: `backend/tests/`
- **Run Tests**: `cd backend && python -m pytest tests/`

### Git Workflow

**Branch Strategy**: GitHub Flow + Trunk-based
- `main`: Main branch, always deployable
- `feature/*`: Feature branches, merged to main when complete
- Short-lived branches, frequent merges

**Commit Convention**: Conventional Commits
```
<type>(<scope>): <description>

Types: feat, fix, docs, style, refactor, test, chore
Example: feat(assistant): add citation system for knowledge base search
```

## Domain Context

### Core Entities

**Entry**: Core entity of the system, represents a knowledge/experience record
- Supports Markdown content
- Configurable types (EntryType): Knowledge, Project, Competition, etc.
- Time modes: NONE / POINT / RANGE
- Supports tags, relations, attachments

**EntryType**: Defines Entry classification
- Contains display properties like icon and color
- User-customizable

**Tag**: Multi-dimensional classification tags
- Supports hierarchical structure

**Relation**: Connections between Entries
- Types: belongs_to, uses, related_to, etc.
- Supports bidirectional relations

**Attachment**: File attachments
- Stored in MinIO

### AI Features

**AI Provider**: AI service provider configuration
- Supports OpenAI-compatible APIs
- API Keys encrypted with Fernet

**AI Assistant**: Intelligent assistant
- Built on LangChain
- Supports Tool Calling and Skill execution
- Integrates LightRAG for knowledge base retrieval

**LightRAG**: Knowledge base RAG system
- Supports hybrid/local/global retrieval modes
- Integrates rerank model for improved retrieval quality

## Important Constraints

### Technical Constraints
- Python 3.11+ required
- Node.js 18+ required
- PostgreSQL 15+ required
- `AI_PROVIDER_FERNET_KEY` must be configured to use AI features

### Security Constraints
- API Keys must be encrypted, no plaintext storage
- Frontend must not store sensitive information
- File uploads must validate type and size

### Design Constraints
- Maintain monolithic architecture, avoid microservices
- Prioritize personal use scenarios
- Core features must not depend on external AI services

## External Dependencies

### Required Services
| Service | Purpose | Default Port |
|---------|---------|--------------|
| PostgreSQL | Primary database | 5432 |
| MinIO | Object storage (attachments) | 9000/9001 |

### Optional Services
| Service | Purpose | Default Port |
|---------|---------|--------------|
| Neo4j | Graph database (LightRAG) | 7474/7687 |

### External APIs
- OpenAI-compatible API (AI features)
- Supports self-hosted LLMs (e.g., Ollama)
