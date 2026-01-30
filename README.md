# MindAtlas

Personal knowledge and experience management system - Record, connect, search, analyze, and summarize your knowledge and experiences.

[中文文档](README.zh-CN.md)

## Overview

MindAtlas is a self-hosted knowledge & experience management system that helps you turn scattered notes into a structured, connected “atlas” — with AI-assisted writing and (optional) RAG querying.

It centers around **Entries** (Markdown content + optional time), and lets you organize them with **types**, **tags**, and explicit **relations**. On top of that, MindAtlas includes:

- **AI content assistant**: generate summaries, refine Markdown, and suggest tags for your entries
- **AI chat assistant**: streaming chat with tool calling and configurable skills
- **AI registry**: manage OpenAI-compatible credentials/models and bind them to components (assistant / LightRAG)
- **LightRAG + Neo4j (optional)**: index your content for RAG-style querying, graph exploration, and relation recommendations

## Use Cases

- Build a “second brain” for learning notes, projects, research, and life events
- Track experiences on a timeline (point-in-time or time range)
- Connect people/skills/projects/ideas with typed relations and visualize the network
- Store and retrieve attachments in S3-compatible storage (MinIO)
- Use AI to summarize/refine entries and suggest tags (optional)
- Ask natural-language questions with streaming answers (optional; best with LightRAG enabled)

## Core Concepts

- **Entry**: a record with title, Markdown content, optional time, and summary
- **Entry Type**: category configuration (icon/color) for consistent organization
- **Tag**: flexible multi-dimensional labels
- **Relation**: typed links between Entries (the basis of the “system graph”)
- **Attachment**: files associated with Entries (stored in MinIO)
- **Graph**: interactive visualization of explicit relations (and optional LightRAG graph)
- **AI Registry**: credentials/models + component bindings (assistant / LightRAG)
- **Assistant Skills/Tools**: configurable capabilities used by the chat assistant

## Features

- **Entry Management** - Create, edit, and search knowledge/experience records with Markdown support
- **Type System** - Customize Entry types (Knowledge, Project, Competition, etc.) with icons and colors
- **Tag Management** - Flexible tagging system for multi-dimensional categorization
- **Relation Network** - Build connections between Entries with various relation types
- **Attachment Storage** - File attachment management powered by MinIO
- **Knowledge Graph** - Visualize knowledge connections in an interactive graph
- **LightRAG (Optional)** - Knowledge graph indexing + RAG query powered by LightRAG + Neo4j (with a background worker)
- **AI Content Generation** - Summaries, refined Markdown, and tag suggestions for Entries
- **AI Assistant** - LangChain-based intelligent assistant with tool calling and skill execution
- **Internationalization** - Support for Chinese and English interfaces

## Tech Stack

### Backend
- **Framework**: FastAPI
- **Database**: PostgreSQL + SQLAlchemy
- **Migration**: Alembic
- **Object Storage**: MinIO
- **AI**: LangChain + OpenAI-compatible API
- **Graph DB (Optional)**: Neo4j (for LightRAG)
- **RAG (Optional)**: LightRAG (lightrag-hku)

### Frontend
- **Framework**: React 18 + TypeScript
- **Build Tool**: Vite
- **State Management**: Zustand + TanStack Query
- **Styling**: Tailwind CSS
- **i18n**: react-i18next

## Project Structure

```
MindAtlas/
├── backend/                 # Python FastAPI backend
│   ├── app/
│   │   ├── entry/          # Entry module
│   │   ├── entry_type/     # Type configuration
│   │   ├── tag/            # Tag management
│   │   ├── relation/       # Relation management
│   │   ├── attachment/     # Attachment management
│   │   ├── ai_provider/    # AI provider configuration
│   │   ├── ai_registry/    # AI key/model registry
│   │   ├── ai/             # AI features
│   │   ├── assistant/      # AI assistant
│   │   ├── assistant_config/ # Assistant tools/skills config
│   │   ├── graph/          # Graph API
│   │   ├── lightrag/       # LightRAG (optional)
│   │   └── stats/          # Statistics API
│   ├── alembic/            # Database migrations
│   └── requirements.txt
├── frontend/               # React frontend
│   ├── src/
│   │   ├── features/       # Feature modules
│   │   ├── components/     # Shared components
│   │   ├── stores/         # State management
│   │   └── locales/        # i18n files
│   └── package.json
└── deploy/                 # Docker deployment config
```

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL 15+
- MinIO (or S3-compatible object storage)
- Neo4j 5+ (optional; required when LightRAG is enabled)

### 1. Clone the Repository

```bash
git clone https://github.com/novisfff/MindAtlas
cd MindAtlas
```

### 2. Start Backend

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env to configure database/MinIO, and optionally AI/LightRAG/Neo4j

# Run database migrations
alembic upgrade head

# Start the server
uvicorn app.main:app --reload --port 8000
```

### 2.1 (Optional) Start LightRAG Worker

If you enable `LIGHTRAG_ENABLED=true`, start the background worker in another terminal:

```bash
python -m app.lightrag.worker
```

### 3. Start Frontend

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

Visit http://localhost:3000 to use the application.

## Docker Deployment

The project provides a complete Docker Compose configuration for one-click deployment:

```bash
cd deploy
cp .env.example .env
cp backend.env.example backend.env
docker compose up -d
```

See [deploy/README.md](deploy/README.md) for detailed deployment instructions (including Neo4j + LightRAG worker).

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://postgres:postgres@localhost:5432/mindatlas` |
| `MINIO_ENDPOINT` | MinIO address | `localhost:9000` |
| `MINIO_ACCESS_KEY` | MinIO access key | - |
| `MINIO_SECRET_KEY` | MinIO secret key | - |
| `MINIO_BUCKET` | MinIO bucket name | `mindatlas` |
| `AI_API_KEY` | OpenAI-compatible API key (optional; required for LightRAG) | - |
| `AI_PROVIDER_FERNET_KEY` | API Key encryption key (for DB-stored keys) | - |
| `LIGHTRAG_ENABLED` | Enable LightRAG | `false` |
| `NEO4J_URI` | Neo4j URI (required when LightRAG enabled) | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4j username | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j password | - |

For the full list, see `backend/.env.example`.

## Documentation

- [User Manual](docs/user-manual.md) - Comprehensive user guide

## License

MIT License
