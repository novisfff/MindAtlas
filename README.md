# MindAtlas

个人知识与经历管理系统 - 支持记录、关联、搜索、分析、总结个人知识与经历。

## 功能特性

- **Entry 管理** - 统一的知识/经历记录单元
- **类型配置** - 可自定义的 Entry 类型（知识、项目、比赛、经历等）
- **标签系统** - 灵活的标签分类
- **关系图谱** - Entry 之间的关联关系可视化
- **附件管理** - MinIO 对象存储
- **时间轴** - 按时间展示记录
- **AI 增强** - 自动摘要、标签建议（开发中）

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python FastAPI |
| 数据库 | PostgreSQL |
| 对象存储 | MinIO |
| 前端 | React 18 + TypeScript + Vite |
| 样式 | Tailwind CSS |
| 状态管理 | Zustand + TanStack Query |

## 项目结构

```
MindAtlas/
├── docs/
│   └── PRD.md              # 产品需求文档
├── backend/                # Python FastAPI 后端
│   ├── app/                # 应用代码
│   ├── alembic/            # 数据库迁移
│   └── requirements.txt
└── frontend/               # React 前端
    └── src/
        ├── components/     # 公共组件
        └── features/       # 业务模块
```

## 快速开始

### 1. 启动依赖服务

```bash
# PostgreSQL
docker run -d --name postgres -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=mindatlas \
  postgres:15

# MinIO
docker run -d --name minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"
```

### 2. 启动后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

### 4. 访问应用

- 前端: http://localhost:5173
- 后端 API 文档: http://localhost:8000/docs
- MinIO 控制台: http://localhost:9001

## 文档

- [产品需求文档](docs/PRD.md)
- [后端说明](backend/README.md)

