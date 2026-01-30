# MindAtlas

个人知识与经历管理系统 - 记录、关联、搜索、分析、总结你的知识与经历。

[English](README.md)

## 系统介绍

MindAtlas 是一个可自托管的个人知识与经历管理系统，目标是把零散的信息沉淀为结构化、可连接、可检索的“知识地图”——并提供 AI 辅助写作与（可选）RAG 查询能力。

系统以 **Entry**（Markdown 内容 + 可选时间信息）为核心，通过 **类型**、**标签** 与显式 **关系** 来组织内容。在此基础上，MindAtlas 还提供：

- **AI 内容助手**：为 Entry 生成摘要、整理 Markdown、推荐标签
- **AI 对话助手**：支持流式对话（SSE）、工具调用与可配置技能
- **AI 注册表**：管理 OpenAI 兼容的凭据/模型，并绑定到不同组件（assistant / LightRAG）
- **LightRAG + Neo4j（可选）**：对内容做索引，用于 RAG 查询、图谱浏览与关系推荐

## 适用场景

- 构建个人“第二大脑”：学习笔记、项目记录、研究资料、生活经历沉淀
- 按时间维度管理经历（时间点 / 时间区间）
- 用关系把人物/技能/项目/想法串起来，并以图谱方式可视化
- 通过 MinIO（S3 兼容）存储与管理附件
- 使用 AI 生成摘要/内容整理/标签建议（可选）
- 以自然语言提问并获得流式回答（可选；启用 LightRAG 效果更佳）

## 核心概念

- **Entry**：一条记录，包含标题、Markdown 内容、可选时间、摘要
- **Entry 类型**：Entry 的分类配置（图标/颜色等），便于统一管理
- **标签**：多维度的灵活标注
- **关系**：Entry 之间的有类型连接（构成“系统图谱”）
- **附件**：与 Entry 关联的文件（存储在 MinIO）
- **图谱**：显式关系的交互式可视化（以及可选的 LightRAG 图谱）
- **AI 注册表**：凭据/模型管理 + 组件绑定（assistant / LightRAG）
- **助手技能/工具**：对话助手可配置的能力集合

## 功能特性

- **Entry 管理** - 创建、编辑、搜索知识/经历记录，支持 Markdown 内容
- **类型系统** - 自定义 Entry 类型（知识、项目、竞赛等），配置图标和颜色
- **标签管理** - 灵活的标签分类系统
- **关系网络** - 建立 Entry 之间的关联关系，支持多种关系类型
- **附件存储** - 基于 MinIO 的文件附件管理
- **知识图谱** - 可视化展示知识关联网络
- **LightRAG（可选）** - 基于 LightRAG + Neo4j 的知识图谱索引与 RAG 查询（含后台 Worker）
- **AI 内容生成** - 为 Entry 生成摘要、内容整理与标签建议
- **AI 助手** - 基于 LangChain 的智能助手，支持工具调用和技能执行
- **国际化** - 支持中文和英文界面切换

## 技术栈

### 后端
- **框架**: FastAPI
- **数据库**: PostgreSQL + SQLAlchemy
- **迁移**: Alembic
- **对象存储**: MinIO
- **AI**: LangChain + OpenAI 兼容接口
- **图数据库（可选）**: Neo4j（用于 LightRAG）
- **RAG（可选）**: LightRAG（lightrag-hku）

### 前端
- **框架**: React 18 + TypeScript
- **构建**: Vite
- **状态管理**: Zustand + TanStack Query
- **样式**: Tailwind CSS
- **国际化**: react-i18next

## 项目结构

```
MindAtlas/
├── backend/                 # Python FastAPI 后端
│   ├── app/
│   │   ├── entry/          # Entry 模块
│   │   ├── entry_type/     # 类型配置
│   │   ├── tag/            # 标签管理
│   │   ├── relation/       # 关系管理
│   │   ├── attachment/     # 附件管理
│   │   ├── ai_provider/    # AI 服务商配置
│   │   ├── ai_registry/    # AI Key/模型注册表
│   │   ├── ai/             # AI 功能接口
│   │   ├── assistant/      # AI 助手
│   │   ├── assistant_config/ # 助手工具/技能配置
│   │   ├── graph/          # 图谱接口
│   │   ├── lightrag/       # LightRAG（可选）
│   │   └── stats/          # 统计接口
│   ├── alembic/            # 数据库迁移
│   └── requirements.txt
├── frontend/               # React 前端
│   ├── src/
│   │   ├── features/       # 功能模块
│   │   ├── components/     # 公共组件
│   │   ├── stores/         # 状态管理
│   │   └── locales/        # 国际化文件
│   └── package.json
└── deploy/                 # Docker 部署配置
```

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- PostgreSQL 15+
- MinIO (或兼容 S3 的对象存储)
- Neo4j 5+（可选；启用 LightRAG 时需要）

### 1. 克隆项目

```bash
git clone https://github.com/novisfff/MindAtlas
cd MindAtlas
```

### 2. 启动后端

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 配置数据库/MinIO，并可选配置 AI/LightRAG/Neo4j

# 数据库迁移
alembic upgrade head

# 启动服务
uvicorn app.main:app --reload --port 8000
```

### 2.1（可选）启动 LightRAG Worker

当你设置 `LIGHTRAG_ENABLED=true` 时，建议在另一个终端启动后台 Worker：

```bash
python -m app.lightrag.worker
```

### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

访问 http://localhost:3000 使用应用。

## Docker 部署

项目提供完整的 Docker Compose 配置，一键部署所有服务：

```bash
cd deploy
cp .env.example .env
cp backend.env.example backend.env
docker compose up -d
```

详细部署说明请参考 [deploy/README.md](deploy/README.md)（包含 Neo4j + LightRAG Worker）。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DATABASE_URL` | PostgreSQL 连接串 | `postgresql://postgres:postgres@localhost:5432/mindatlas` |
| `MINIO_ENDPOINT` | MinIO 地址 | `localhost:9000` |
| `MINIO_ACCESS_KEY` | MinIO 访问密钥 | - |
| `MINIO_SECRET_KEY` | MinIO 密钥 | - |
| `MINIO_BUCKET` | MinIO 桶名 | `mindatlas` |
| `AI_API_KEY` | OpenAI 兼容接口的 API Key（可选；LightRAG 需要） | - |
| `AI_PROVIDER_FERNET_KEY` | API Key 加密密钥（用于 DB 存储的 Key） | - |
| `LIGHTRAG_ENABLED` | 是否启用 LightRAG | `false` |
| `NEO4J_URI` | Neo4j 连接（启用 LightRAG 时需要） | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4j 用户名 | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j 密码 | - |

完整变量列表请参考 `backend/.env.example`。

## 文档

- [用户操作手册](docs/user-manual.zh-CN.md) - 完整用户指南

## 许可证

MIT License
