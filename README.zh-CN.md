# MindAtlas

个人知识与经历管理系统 - 记录、关联、搜索、分析、总结你的知识与经历。

[English](README.md)

## 功能特性

- **Entry 管理** - 创建、编辑、搜索知识/经历记录，支持 Markdown 内容
- **类型系统** - 自定义 Entry 类型（知识、项目、竞赛等），配置图标和颜色
- **标签管理** - 灵活的标签分类系统
- **关系网络** - 建立 Entry 之间的关联关系，支持多种关系类型
- **附件存储** - 基于 MinIO 的文件附件管理
- **知识图谱** - 可视化展示知识关联网络
- **AI 助手** - 基于 LangChain 的智能助手，支持工具调用和技能执行
- **国际化** - 支持中文和英文界面切换

## 技术栈

### 后端
- **框架**: FastAPI
- **数据库**: PostgreSQL + SQLAlchemy
- **迁移**: Alembic
- **对象存储**: MinIO
- **AI**: LangChain + OpenAI 兼容接口

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
│   │   ├── ai/             # AI 功能接口
│   │   ├── assistant/      # AI 助手
│   │   ├── assistant_config/ # 助手工具/技能配置
│   │   ├── graph/          # 图谱接口
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
# 编辑 .env 配置数据库和 MinIO

# 数据库迁移
alembic upgrade head

# 启动服务
uvicorn app.main:app --reload --port 8000
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

详细部署说明请参考 [deploy/README.md](deploy/README.md)。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DATABASE_URL` | PostgreSQL 连接串 | `postgresql://postgres:postgres@localhost:5432/mindatlas` |
| `MINIO_ENDPOINT` | MinIO 地址 | `localhost:9000` |
| `MINIO_ACCESS_KEY` | MinIO 访问密钥 | - |
| `MINIO_SECRET_KEY` | MinIO 密钥 | - |
| `MINIO_BUCKET` | MinIO 桶名 | `mindatlas` |
| `AI_PROVIDER_FERNET_KEY` | API Key 加密密钥 | - |

## 文档

- [用户操作手册](docs/user-manual.zh-CN.md) - 完整用户指南

## 许可证

MIT License
