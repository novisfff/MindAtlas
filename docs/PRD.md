# MindAtlas - Product Requirements Document

> Version: 1.0
> Last Updated: 2026-01-09

## 1. Overview

### 1.1 Background

Personal experiences, knowledge, projects, and achievements accumulate over time, but fragmented records lose their value. What truly matters is a **structured, interconnected, reviewable, and summarizable** personal knowledge system.

**Problems with existing tools** (notes, blogs, cloud storage):
- Information silos with no connections
- Difficult to review holistically
- Cannot auto-summarize personal experiences
- Cannot answer "What have I done?" or "What am I good at?"

### 1.2 Goals

Build a personal knowledge and experience management system that supports:
- **Recording** - Capture knowledge and experiences
- **Connecting** - Link related entries
- **Searching** - Find information quickly
- **Analyzing** - Understand patterns
- **Summarizing** - Generate insights

### 1.3 Design Principles

| Principle | Description |
|-----------|-------------|
| Simplicity | Monolithic architecture, avoid over-engineering |
| Function First | Features over technology; tech can evolve |
| AI Enhanced | AI augments but doesn't replace core functionality |
| Data Ownership | Data is exportable and portable |

---

## 2. Core Concepts

### 2.1 Entry (Unified Record Unit)

An **Entry** is the atomic unit of knowledge/experience in the system.

**Core Attributes:**

| Attribute | Description |
|-----------|-------------|
| `title` | Entry title |
| `content` | Rich text/Markdown description |
| `type` | Configurable entry type |
| `time_mode` | POINT / RANGE |
| `time_at` | Point in time (for POINT mode) |
| `time_from/to` | Time range (for RANGE mode) |
| `tags` | Manual + AI-generated tags |
| `relations` | Links to other entries |
| `attachments` | Associated files |

### 2.2 Entry Types (Configurable)

Entry types are **not fixed enums** - users can customize and extend them.

**Default Types:**

| Type | Description | Icon |
|------|-------------|------|
| Knowledge | Learning notes, concepts | book |
| Project | Personal/work projects | folder |
| Competition | Contests, hackathons | trophy |
| Experience | Life experiences | star |
| Achievement | Awards, certifications | award |
| Technology | Tech skills, tools | code |
| Document | Reference materials | file |

**Type Configuration:**
- Name and description
- Graph display style (color/icon)
- Include in graph visualization
- Include in AI summaries
- Default tag templates

### 2.3 Relations

Entries can be connected through typed relationships.

**Default Relation Types:**

| Type | Description | Example |
|------|-------------|---------|
| belongs_to | Part of | "Feature X belongs_to Project Y" |
| uses | Utilizes | "Project uses Technology" |
| participates | Involved in | "I participated in Competition" |
| related_to | General association | "Entry A related_to Entry B" |
| derived_from | Based on | "Project B derived_from Project A" |

---

## 3. Functional Requirements

### 3.1 Entry Management

#### 3.1.1 CRUD Operations
- Create entries with all attributes
- Edit existing entries
- Delete entries (with cascade cleanup)
- List entries with pagination

#### 3.1.2 Search & Filter
- Keyword search (title + content)
- Filter by type
- Filter by tags
- Filter by time range
- Pagination support

#### 3.1.3 AI Enhancement (Optional)
- Auto-generate summaries
- Auto-suggest tags
- Auto-identify related entries
- Auto-suggest entry type

### 3.2 Type Configuration

| Feature | Description |
|---------|-------------|
| CRUD | Create/Read/Update/Delete types |
| Enable/Disable | Toggle type availability |
| Display Config | Color, icon, graph settings |
| Migration | Move entries from type A to B |
| Protection | Cannot delete types in use |

### 3.3 Attachment Management

#### 3.3.1 File Upload
- Support images, documents, archives
- Multiple attachments per entry
- Store in MinIO object storage
- Metadata tracking (size, type, name)

#### 3.3.2 File Processing (AI)
- Text extraction and summarization
- Keyword extraction
- Auto-link to relevant entries

### 3.4 Knowledge Graph

#### 3.4.1 Relation Management
- Entry ↔ Entry (many-to-many)
- Configurable relation types
- Bidirectional navigation

#### 3.4.2 Visualization
- Interactive graph display
- Node filtering by type
- Zoom and pan controls
- Click to navigate

#### 3.4.3 Advanced (V2)
- Multi-hop path analysis
- Recommendation engine
- Neo4j integration

### 3.5 Timeline & Statistics

- Chronological entry display
- Type distribution charts
- Tag cloud visualization
- Activity heatmap

### 3.6 Summary & Export

#### 3.6.1 AI Summaries
- By type: "Summarize all my competitions"
- By time: "What did I do in 2025?"
- By tag: "Summarize my Python projects"

#### 3.6.2 Export Formats
- Markdown
- JSON
- PDF (future)

---

## 4. Technical Architecture

### 4.1 Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python FastAPI |
| Database | PostgreSQL |
| Migrations | Alembic |
| Object Storage | MinIO |
| Frontend | React 18 + TypeScript |
| Build Tool | Vite |
| Styling | Tailwind CSS |
| State | Zustand |
| Data Fetching | TanStack Query |
| Routing | React Router |

### 4.2 Project Structure

```
MindAtlas/
├── docs/                    # Documentation
│   └── PRD.md              # This file
├── backend/                 # Python FastAPI
│   ├── app/
│   │   ├── common/         # Shared utilities
│   │   ├── entry/          # Entry module
│   │   ├── entry_type/     # Type config module
│   │   ├── tag/            # Tag module
│   │   ├── relation/       # Relation module
│   │   └── attachment/     # Attachment module
│   ├── alembic/            # DB migrations
│   └── requirements.txt
├── frontend/               # React frontend
│   └── src/
│       ├── components/     # Shared components
│       ├── features/       # Feature modules
│       ├── stores/         # Zustand stores
│       └── types/          # TypeScript types
└── CLAUDE.md              # AI context file
```

### 4.3 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/entries | List entries |
| GET | /api/entries/{id} | Get entry |
| POST | /api/entries | Create entry |
| PUT | /api/entries/{id} | Update entry |
| DELETE | /api/entries/{id} | Delete entry |
| POST | /api/entries/search | Search entries |
| GET | /api/entry-types | List types |
| POST | /api/entry-types | Create type |
| GET | /api/tags | List tags |
| POST | /api/attachments/entry/{id} | Upload file |
| GET | /api/attachments/{id}/download | Download file |
| GET | /api/relations | List relations |
| POST | /api/relations | Create relation |

---

## 5. Version Roadmap

### V1 - MVP (Current)

- [x] Entry CRUD
- [x] Entry Type configuration
- [x] Tag management
- [x] Attachment upload (MinIO)
- [x] Relation management
- [x] Basic search & filter
- [ ] Knowledge graph visualization
- [ ] Timeline view
- [ ] AI summary/tags

### V2 - Enhanced

- [ ] AI semantic search
- [ ] Auto relation discovery
- [ ] Interview summary mode
- [ ] Neo4j integration
- [ ] Export to PDF

---

## 6. Non-Functional Requirements

### 6.1 Performance
- Support 10,000+ entries
- Graph pagination (limit visible nodes)
- Lazy loading for large lists

### 6.2 Extensibility
- Entry types are user-configurable
- Relation types are extensible
- AI modules are replaceable

### 6.3 Security
- Input validation
- SQL injection prevention
- XSS protection
- Secure file uploads

---

## 7. Risks & Constraints

| Risk | Mitigation |
|------|------------|
| AI results unreliable | AI is advisory only, user has final control |
| Graph too large | Limit visible nodes, pagination |
| Over-automation | Keep manual control, AI enhances not replaces |
