# MindAtlas Backend

Python FastAPI 后端服务，提供个人知识管理系统的 API。

## 技术栈

- **框架**: FastAPI
- **数据库**: PostgreSQL + SQLAlchemy
- **迁移**: Alembic
- **对象存储**: MinIO
- **配置**: pydantic-settings

## 项目结构

```
backend/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置管理
│   ├── database.py          # 数据库连接
│   ├── common/              # 公共模块
│   │   ├── storage.py       # MinIO 客户端
│   │   ├── responses.py     # API 响应格式
│   │   ├── exceptions.py    # 异常处理
│   │   └── models.py        # 基础模型
│   ├── entry/               # Entry 模块
│   ├── entry_type/          # 类型配置
│   ├── tag/                 # 标签管理
│   ├── relation/            # 关系管理
│   ├── attachment/          # 附件管理
│   ├── graph/               # 图谱接口
│   ├── stats/               # 统计接口
│   └── ai/                  # AI 接口
├── alembic/                 # 数据库迁移
├── alembic.ini
├── requirements.txt
└── .env.example
```

## 快速开始

### 1. 环境准备

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 配置数据库和 MinIO
```

### 3. 数据库迁移

```bash
alembic upgrade head
```

### 4. 启动服务

```bash
uvicorn app.main:app --reload --port 8000
```

访问 http://localhost:8000/docs 查看 API 文档。

### 5. 启动 LightRAG Worker（可选）

```bash
# 需要先配置 LIGHTRAG_ENABLED / LIGHTRAG_WORKER_ENABLED / NEO4J_* / AI_* 等环境变量
python -m app.lightrag.worker
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| DATABASE_URL | PostgreSQL 连接 | postgresql://postgres:postgres@localhost:5432/mindatlas |
| CORS_ORIGINS | CORS 允许域名 | http://localhost:3000,http://localhost:5173 |
| MINIO_ENDPOINT | MinIO 地址 | localhost:9000 |
| MINIO_ACCESS_KEY | MinIO 访问密钥 | - |
| MINIO_SECRET_KEY | MinIO 密钥 | - |
| MINIO_BUCKET | MinIO 桶名 | mindatlas |
| MINIO_SECURE | 是否使用 HTTPS | false |
| AI_API_KEY | AI 服务密钥（可选） | - |
| AI_BASE_URL | AI Base URL（OpenAI 兼容） | https://api.openai.com/v1 |
| AI_MODEL | LLM 模型名（OpenAI 兼容） | gpt-3.5-turbo |
| LIGHTRAG_ENABLED | LightRAG 总开关 | false |
| LIGHTRAG_WORKER_ENABLED | Outbox Worker 开关 | false |
| LIGHTRAG_WORKING_DIR | LightRAG 本地工作目录 | ./lightrag_storage |
| LIGHTRAG_WORKSPACE | LightRAG workspace（隔离数据） | - |
| LIGHTRAG_GRAPH_STORAGE | KG 存储实现 | Neo4JStorage |
| LIGHTRAG_LLM_MODEL | LightRAG 用的 LLM 模型名 | AI_MODEL |
| LIGHTRAG_EMBEDDING_MODEL | Embedding 模型名 | text-embedding-3-small |
| LIGHTRAG_EMBEDDING_DIM | Embedding 维度 | 1536 |
| NEO4J_URI | Neo4j 连接 | bolt://localhost:7687 |
| NEO4J_USER | Neo4j 用户名 | neo4j |
| NEO4J_PASSWORD | Neo4j 密码 | - |
| NEO4J_DATABASE | Neo4j 数据库 | neo4j |

## API 端点

### Entry
- `GET /api/entries` - 获取所有 Entry
- `GET /api/entries/{id}` - 获取单个 Entry
- `POST /api/entries` - 创建 Entry
- `PUT /api/entries/{id}` - 更新 Entry
- `DELETE /api/entries/{id}` - 删除 Entry
- `POST /api/entries/search` - 搜索 Entry

### Entry Type
- `GET /api/entry-types` - 获取所有类型
- `POST /api/entry-types` - 创建类型
- `PUT /api/entry-types/{id}` - 更新类型
- `DELETE /api/entry-types/{id}` - 删除类型

### Tag
- `GET /api/tags` - 获取所有标签
- `POST /api/tags` - 创建标签
- `DELETE /api/tags/{id}` - 删除标签

### Relation
- `GET /api/relations` - 获取所有关系
- `POST /api/relations` - 创建关系
- `DELETE /api/relations/{id}` - 删除关系

### Attachment
- `GET /api/attachments` - 获取所有附件
- `GET /api/attachments/entry/{entry_id}` - 获取 Entry 的附件
- `POST /api/attachments/entry/{entry_id}` - 上传附件
- `GET /api/attachments/{id}/download` - 下载附件
- `DELETE /api/attachments/{id}` - 删除附件

### Graph
- `GET /api/graph/data` - 获取图谱数据

### Stats
- `GET /api/stats/overview` - 获取统计概览

## 依赖服务

### PostgreSQL

```bash
docker run -d --name postgres -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=mindatlas \
  postgres:15
```

### MinIO

```bash
docker run -d --name minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"
```

MinIO 控制台: http://localhost:9001

## 数据库迁移

```bash
# 创建新迁移
alembic revision --autogenerate -m "description"

# 执行迁移
alembic upgrade head

# 回滚
alembic downgrade -1
```

## 单元测试

在 `backend/` 目录下运行：

```bash
python -m unittest discover -s tests -p "test_*.py"
```
