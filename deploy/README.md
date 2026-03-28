# MindAtlas Docker 部署指南

本文档介绍如何使用 Docker Compose 部署 MindAtlas 系统。

## 前置要求

- Docker 20.10+
- Docker Compose 2.0+
- 至少 2GB 可用内存
- 至少 5GB 可用磁盘空间

## 快速开始

### 1. 进入部署目录

```bash
cd MindAtlas/deploy
```

### 2. 直接启动服务

```bash
docker compose up -d
```

首次启动会自动：
- 构建前后端镜像
- 创建 PostgreSQL 数据库
- 运行数据库迁移
- 创建 MinIO 存储桶

不需要预先拷贝或修改任何 env 文件。`docker-compose.yml` 已经内置了可运行的默认值。

### 3. 访问应用

| 服务 | 地址 |
|------|------|
| 前端应用 | http://localhost:3000 |
| MinIO 控制台 | http://localhost:9001 |
| Neo4j Browser（LightRAG） | http://localhost:7474 |

### 4. 如需覆盖默认值，再准备 `.env`

```bash
cp .env.example .env
```

复制后即使完全不修改，行为也与“不提供 `.env`”一致。只有在你想覆盖端口、密码或高级默认值时，才需要编辑它。

## `.env` 是做什么的

`deploy/.env` 是一个可选的 override 文件，不是 Docker 部署的前置条件。

- 不提供 `.env`：直接使用 `docker-compose.yml` 里的默认值启动
- 复制 `.env.example` 但不修改：行为与不提供 `.env` 相同
- 编辑 `.env`：按你的改动覆盖默认值

默认情况下：
- `DATABASE_URL`、`MINIO_ENDPOINT`、`NEO4J_URI` 等容器内地址由 `docker-compose.yml` 使用服务名（`db` / `minio` / `neo4j`）自动注入
- 对象存储、知识图谱、文档解析和后台调度器都会在 Compose 中自动启用
- 前端 Nginx 会反代 `/api/` 到后端（同源访问），一般不需要额外配置 CORS

## 服务架构

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Network                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │ frontend │  │ backend  │  │    db    │  │  minio  │ │
│  │ (nginx)  │──│ (uvicorn)│──│(postgres)│  │         │ │
│  │  :80     │  │  :8000   │  │  :5432   │  │ :9000/1 │ │
│  └──────────┘  └─────┬────┘  └──────────┘  └─────────┘ │
│                      │                                  │
│                 ┌────▼─────┐                            │
│                 │  neo4j   │                            │
│                 │:7474/7687│                            │
│                 └────┬─────┘                            │
│                      │                                  │
│                 ┌────▼─────┐                            │
│                 │  worker  │                            │
│                 │ (index)  │                            │
│                 └──────────┘                            │
└─────────────────────────────────────────────────────────┘
        │                                         │
        ▼                                         ▼
   http://localhost                    http://localhost:9001
```

## 环境变量说明

### `.env`（可选覆盖项，不是必需文件）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `POSTGRES_USER` | 数据库用户名 | postgres |
| `POSTGRES_PASSWORD` | 数据库密码 | postgres |
| `POSTGRES_DB` | 数据库名称 | mindatlas |
| `MINIO_ACCESS_KEY` | MinIO 访问密钥 | minioadmin |
| `MINIO_SECRET_KEY` | MinIO 密钥 | minioadmin |
| `MINIO_BUCKET` | MinIO 桶名称 | mindatlas |
| `NEO4J_USER` | Neo4j 用户名 | neo4j |
| `NEO4J_PASSWORD` | Neo4j 密码 | password |
| `NEO4J_HTTP_PORT` | Neo4j HTTP 端口 | 7474 |
| `NEO4J_BOLT_PORT` | Neo4j Bolt 端口 | 7687 |
| `FRONTEND_PORT` | 前端访问端口 | 3000 |
| `AI_PROVIDER_FERNET_KEY` | 可选覆盖 AI Key 的 DB 加密密钥 | Compose 内置默认值 |
| `LIGHTRAG_ENABLED` | 可选覆盖知识图谱总开关 | true |
| `LIGHTRAG_WORKER_ENABLED` | 可选覆盖索引 Worker 开关 | true |
| `DOCLING_WORKER_ENABLED` | 可选覆盖附件解析 Worker 开关 | true |
| `SCHEDULER_ENABLED` | 可选覆盖后台调度器开关 | true |
| `LIGHTRAG_EMBEDDING_MODEL` | 可选覆盖默认 Embedding 模型名 | `text-embedding-3-small` |

如果你不需要覆盖这些值，可以完全忽略 `.env`。

补充说明：
- `parse-worker` 镜像默认按 CPU-only 链路安装 PyTorch + Docling，不会再为纯 CPU 部署额外拉取一整套 CUDA/NVIDIA 运行时轮子。
- `parse-worker` 现在走独立的最小依赖清单，不会再顺手安装整套后端 API、LangChain、LightRAG 依赖，构建时间和解析复杂度都会更低。
- 如果你确实需要改 PyTorch 版本，编辑 [backend/Dockerfile](/Users/zyf/IdeaProjects/MindAtlas/backend/Dockerfile) 里的 `TORCH_CPU_VERSION` 即可。

## 常用命令

### 查看服务状态

```bash
docker compose ps
```

### 查看日志

```bash
# 查看所有服务日志
docker compose logs -f

# 查看特定服务日志
docker compose logs -f backend
docker compose logs -f frontend
```

### 重启服务

```bash
# 重启所有服务
docker compose restart

# 重启特定服务
docker compose restart backend
```

### 停止服务

```bash
docker compose down
```

### 停止并删除数据

```bash
docker compose down -v
```

### 重新构建镜像

```bash
docker compose build --no-cache
docker compose up -d
```

## 常见问题排查

### 1. 数据库连接失败

**症状**: 后端启动失败，日志显示数据库连接错误

**解决方案**:
```bash
# 检查数据库服务状态
docker compose ps db

# 查看数据库日志
docker compose logs db

# 手动测试连接
docker compose exec db psql -U postgres -d mindatlas -c "SELECT 1"
```

### 2. MinIO 桶创建失败

**症状**: minio-init 容器退出码非 0

**解决方案**:
```bash
# 查看初始化日志
docker compose logs minio-init

# 手动创建桶
docker compose exec minio mc alias set local http://localhost:9000 minioadmin minioadmin
docker compose exec minio mc mb --ignore-existing local/mindatlas
```

### 3. 前端无法访问后端 API

**症状**: 浏览器控制台显示 502 Bad Gateway

**解决方案**:
```bash
# 检查后端健康状态
docker compose exec frontend curl http://backend:8000/health

# 查看后端日志
docker compose logs backend
```

### 4. 端口被占用

**症状**: 启动时提示端口已被使用

**解决方案**:
```bash
# 查看占用端口的进程
lsof -i :80
lsof -i :9001

# 修改 docker-compose.yml 中的端口映射
# 例如: "8080:80" 替代 "80:80"
```

### 5. 镜像构建失败

**症状**: npm 或 pip 安装依赖超时

**解决方案**:
```bash
# 使用国内镜像源重新构建
docker compose build --build-arg NPM_REGISTRY=https://registry.npmmirror.com
```

## 生产环境建议

1. **修改默认密码**: 启动后建议尽快通过 `.env` 覆盖数据库、MinIO 和 Neo4j 的默认密码
2. **配置 HTTPS**: 在 Nginx 前添加反向代理或使用 Let's Encrypt
3. **定期备份**: 备份 `postgres_data` 和 `minio_data` 卷
4. **监控日志**: 配置日志收集和监控告警
5. **覆盖默认密钥**: 生产环境建议通过 `.env` 覆盖 `AI_PROVIDER_FERNET_KEY`
6. **资源限制**: 在 docker-compose.yml 中添加 `deploy.resources` 限制
