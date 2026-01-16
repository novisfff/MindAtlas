# MindAtlas

Personal knowledge and experience management system - Record, connect, search, analyze, and summarize your knowledge and experiences.

[中文文档](README.zh-CN.md)

## Features

- **Entry Management** - Create, edit, and search knowledge/experience records with Markdown support
- **Type System** - Customize Entry types (Knowledge, Project, Competition, etc.) with icons and colors
- **Tag Management** - Flexible tagging system for multi-dimensional categorization
- **Relation Network** - Build connections between Entries with various relation types
- **Attachment Storage** - File attachment management powered by MinIO
- **Knowledge Graph** - Visualize knowledge connections in an interactive graph
- **AI Assistant** - LangChain-based intelligent assistant with tool calling and skill execution
- **Internationalization** - Support for Chinese and English interfaces

## Tech Stack

### Backend
- **Framework**: FastAPI
- **Database**: PostgreSQL + SQLAlchemy
- **Migration**: Alembic
- **Object Storage**: MinIO
- **AI**: LangChain + OpenAI-compatible API

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
│   │   ├── ai/             # AI features
│   │   ├── assistant/      # AI assistant
│   │   ├── assistant_config/ # Assistant tools/skills config
│   │   ├── graph/          # Graph API
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
# Edit .env to configure database and MinIO

# Run database migrations
alembic upgrade head

# Start the server
uvicorn app.main:app --reload --port 8000
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
docker compose up -d
```

See [deploy/README.md](deploy/README.md) for detailed deployment instructions.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://postgres:postgres@localhost:5432/mindatlas` |
| `MINIO_ENDPOINT` | MinIO address | `localhost:9000` |
| `MINIO_ACCESS_KEY` | MinIO access key | - |
| `MINIO_SECRET_KEY` | MinIO secret key | - |
| `MINIO_BUCKET` | MinIO bucket name | `mindatlas` |
| `AI_PROVIDER_FERNET_KEY` | API Key encryption key | - |

## Documentation

- [User Manual](docs/user-manual.md) - Comprehensive user guide

## License

MIT License
