# MindAtlas

可自托管的个人知识与经历管理系统，用于记录、关联、搜索与回顾真正重要的信息。

[English](README.md)

## 概览

MindAtlas 旨在把零散的笔记、项目记录和生活经历沉淀为结构化的个人知识地图。系统以 **Entry** 为核心，支持 Markdown 内容、时间信息、标签、类型化关系、附件以及图谱连接。

## 产品预览

先看一下当前产品界面的整体形态：

<table>
  <tr>
    <td align="center" colspan="2">
      <img src=".github/assets/readme/dashboard-overview.png" alt="MindAtlas 仪表盘总览" />
      <br />
      <strong>仪表盘</strong>
      <br />
      总览、近期活动、日历与 AI 洞察。
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src=".github/assets/readme/knowledge-graph-view.png" alt="MindAtlas 知识图谱视图" />
      <br />
      <strong>知识图谱</strong>
      <br />
      探索记录之间的连接、聚类与上下文。
    </td>
    <td align="center" width="50%">
      <img src=".github/assets/readme/workflow-editor-canvas.png" alt="MindAtlas 工作流编辑画布" />
      <br />
      <strong>工作流编辑器</strong>
      <br />
      用可视化画布编排工作流与智能体。
    </td>
  </tr>
</table>

当前系统已经不只是基础记录工具，还包含：

- **结构化知识管理**：Entry 类型、标签、关系、附件、仪表盘、日历视图与图谱探索
- **首次初始化向导**：首次进入时配置语言、默认类型、AI 服务商与运行时能力模块
- **运行时系统设置**：初始化后继续管理存储、知识图谱、文档解析、自动化等模块
- **AI 注册表与助手配置**：统一管理服务商、模型、工具、技能、目标、工作流、Agent 与系统 AI 行为
- **可选的 LightRAG 知识图谱**：结合 Neo4j 做索引、RAG 查询与图谱辅助探索
- **基于 Docling 的文档解析**：处理上传文件，支持 OCR 和可选图片描述能力
- **定时 AI 报告**：支持周报、月报生成，并在仪表盘中查看结果
- **OpenClaw 集成**：内置可配置能力目录，并在仓库中提供对应插件包

## 技术栈

### 后端

- FastAPI
- PostgreSQL + SQLAlchemy
- Alembic
- MinIO（S3 兼容对象存储）
- LangChain + OpenAI 兼容接口
- LightRAG + Neo4j（可选）
- APScheduler 后台调度

### 前端

- React 18 + TypeScript
- Vite
- Zustand + TanStack Query
- Tailwind CSS
- react-i18next

## 项目结构

```text
MindAtlas/
├── backend/                         # FastAPI API、后台 Worker、调度器、运行时配置
├── frontend/                        # React 应用、初始化流程、设置页、仪表盘
├── deploy/                          # Docker Compose 部署与覆盖配置
├── docs/                            # 用户手册与辅助文档
└── integrations/openclaw-mindatlas/ # OpenClaw 插件包
```

## 快速开始

### 环境要求

- Docker 20.10+
- Docker Compose 2.0+
- Python 3.11+
- Node.js 18+
- PostgreSQL 15+
- MinIO 或其他兼容 S3 的对象存储
- 如果启用 LightRAG，需要 Neo4j 5+

### 1. 克隆仓库

```bash
git clone https://github.com/novisfff/MindAtlas
cd MindAtlas
```

### 2. Docker Compose 启动（推荐）

```bash
cd deploy
docker compose up -d
```

Docker 部署默认即可零配置启动。如果你想覆盖端口、密码或其他运行时默认值，再将 `deploy/.env.example` 复制为 `deploy/.env` 并按需修改。

启动后可访问：

- 应用：`http://localhost:3000`
- MinIO 控制台：`http://localhost:9001`
- Neo4j Browser：`http://localhost:7474`

### 3. 本地开发方式

#### 后端

```bash
cd backend

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install --require-hashes -r requirements/api-worker.lock

cp .env.example .env
# 按需编辑 .env，配置数据库、对象存储，以及可选的 AI / LightRAG 参数

alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

API 文档地址为 `http://localhost:8000/docs`。

#### 可选 Worker

如果启用了 LightRAG：

```bash
cd backend
source .venv/bin/activate
python -m app.lightrag.worker
```

如果需要附件文档解析能力：

```bash
cd backend
source .venv/bin/activate
pip install --require-hashes -r requirements/parse-worker.lock
python -m app.attachment.worker
```

关于 Docling 依赖说明和更完整的后端启动信息，请参考 [`backend/README.md`](backend/README.md)。

#### 前端

```bash
cd frontend
npm install
npm run dev
```

打开 `http://localhost:3000`。

首次进入系统时，MindAtlas 会引导你完成初始化，并配置存储、知识图谱、文档解析、自动化等运行时模块。

## 配置说明

- Docker 部署默认读取 [`deploy/docker-compose.yml`](deploy/docker-compose.yml) 的内置值，并支持通过 [`deploy/.env.example`](deploy/.env.example) 做可选覆盖。
- 手动开发运行时，环境变量以 [`backend/.env.example`](backend/.env.example) 为准。
- 初始化完成后，可在 `Settings -> System Setup` 中管理运行时能力配置。
- 自动化设置当前用于控制后台调度器，负责周报和月报等 AI 定时任务。

## 后续阅读

- [`deploy/README.md`](deploy/README.md)：Docker 部署指南
- [`backend/README.md`](backend/README.md)：后端启动、Worker 与环境变量说明
- [`docs/user-manual.md`](docs/user-manual.md)：英文用户手册
- [`docs/user-manual.zh-CN.md`](docs/user-manual.zh-CN.md)：中文用户手册
- [`integrations/openclaw-mindatlas/README.md`](integrations/openclaw-mindatlas/README.md)：OpenClaw 插件包与集成说明

## 许可证

MIT License
