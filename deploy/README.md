# MindAtlas Docker 部署指南

本文档介绍如何使用 Docker Compose 部署 MindAtlas 系统。

## 前置要求

- Docker 20.10+
- Docker Compose 2.0+
- 至少 2GB 可用内存
- 至少 5GB 可用磁盘空间

## 快速开始

### 1. 克隆代码

```bash
git clone <repository-url>
cd MindAtlas
```

### 2. 配置环境变量

```bash
cd deploy
cp .env.example .env
```

根据需要编辑 `.env` 文件，修改数据库密码、MinIO 密钥等配置。

### 3. 启动服务

```bash
docker compose up -d
```

首次启动会自动：
- 构建前后端镜像
- 创建 PostgreSQL 数据库
- 运行数据库迁移
- 创建 MinIO 存储桶

### 4. 访问应用

| 服务 | 地址 |
|------|------|
| 前端应用 | http://localhost:3000 |
| MinIO 控制台 | http://localhost:9001 |

## 服务架构

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Network                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │ frontend │  │ backend  │  │    db    │  │  minio  │ │
│  │ (nginx)  │──│ (uvicorn)│──│(postgres)│  │         │ │
│  │  :80     │  │  :8000   │  │  :5432   │  │ :9000/1 │ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │
└─────────────────────────────────────────────────────────┘
        │                                         │
        ▼                                         ▼
   http://localhost                    http://localhost:9001
```

## 环境变量说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `POSTGRES_USER` | 数据库用户名 | postgres |
| `POSTGRES_PASSWORD` | 数据库密码 | postgres |
| `POSTGRES_DB` | 数据库名称 | mindatlas |
| `MINIO_ACCESS_KEY` | MinIO 访问密钥 | minioadmin |
| `MINIO_SECRET_KEY` | MinIO 密钥 | minioadmin |
| `MINIO_BUCKET` | MinIO 桶名称 | mindatlas |
| `FRONTEND_PORT` | 前端访问端口 | 3000 |

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

1. **修改默认密码**: 务必修改 `.env` 中的数据库和 MinIO 密码
2. **配置 HTTPS**: 在 Nginx 前添加反向代理或使用 Let's Encrypt
3. **定期备份**: 备份 `postgres_data` 和 `minio_data` 卷
4. **监控日志**: 配置日志收集和监控告警
5. **资源限制**: 在 docker-compose.yml 中添加 `deploy.resources` 限制
