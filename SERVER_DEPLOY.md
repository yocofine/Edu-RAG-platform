# EduRAG 阿里云服务器部署版

本目录是 `edu-rag-platform` 的独立服务器部署副本。它不包含本地 `.env`、数据库、日志、
虚拟环境、前端依赖、构建产物和模型文件，不会覆盖本地开发项目。

## 1. 推荐服务器

- 操作系统：Alibaba Cloud Linux 3 或 Ubuntu 22.04/24.04，x86_64。
- 测试或关闭 MinerU：4 核 16 GB、100 GB ESSD。
- 启用 MinerU 的正式环境：建议 8 核 32 GB、200 GB ESSD。
- 安全组仅开放 22（限制管理员 IP）、80、443；不要开放 19530、3000、3306、9000、2379。

## 2. 上传目录

把本目录上传到服务器，例如：

```text
/opt/edu-rag-platform-server
```

服务器部署副本已经包含所需的 BGE-M3 模型：

```text
models/bge-m3/
```

上传整个 `edu-rag-platform-server` 目录时会一并上传，不需要再单独准备模型。
本地 `bge-reranker-large`、`bert-base-chinese` 和 `bert_query_classifier` 没有复制到部署版。
只有以后把 `RERANK_BACKEND` 改回 `local` 时，才需要额外上传 `models/bge-reranker-large/`。

## 3. 配置阿里云镜像加速

进入阿里云 ACR 控制台的“镜像工具 → 镜像加速器”，复制账号专属地址：

```bash
sudo mkdir -p /etc/docker
sudo cp docker/daemon.aliyun.json.example /etc/docker/daemon.json
sudo vi /etc/docker/daemon.json
sudo systemctl daemon-reload
sudo systemctl restart docker
```

`quay.io/coreos/etcd` 不属于 Docker Hub。如果服务器无法拉取，先同步到自己的 ACR，
再通过 `.env` 的 `ETCD_IMAGE` 指向 ACR 完整地址。其他镜像同样支持 `.env` 覆盖。

## 4. 生产环境变量

```bash
cp .env.example .env
vi .env
```

至少修改：

```env
JWT_SECRET=不少于32字符的随机字符串
MYSQL_PASSWORD=强密码
MYSQL_ROOT_PASSWORD=另一个强密码
MINIO_ROOT_USER=自定义用户名
MINIO_ROOT_PASSWORD=强密码
MINIO_ACCESS_KEY=与MINIO_ROOT_USER一致
MINIO_SECRET_KEY=与MINIO_ROOT_PASSWORD一致
DASHSCOPE_API_KEY=实际模型API密钥
RERANK_BACKEND=api
RERANK_API_KEY=实际重排API密钥
RERANK_API_FALLBACK_LOCAL=false
CORS_ORIGINS=https://你的域名
DOMAIN=你的域名
ACME_EMAIL=证书通知邮箱
COOKIE_SECURE=true
WEB_BIND=127.0.0.1:8080
MODEL_HOST_PATH=./models
MINERU_API_MAX_CONCURRENT_REQUESTS=1
```

不要将 `.env` 提交到 Git 或发送到聊天、工单和公开日志。

## 5. 启动

```bash
chmod +x deploy-server.sh
./deploy-server.sh
curl http://127.0.0.1:8080/health
```

默认不会启动 Attu。如需通过 SSH 隧道临时管理 Milvus：

```bash
docker compose --profile admin-tools up -d attu
```

Attu 只监听服务器 `127.0.0.1:3000`，不要在安全组中放行 3000。

## 6. HTTPS

复制 `docker/nginx-host-https.conf.example` 到宿主机 Nginx 配置目录，替换域名后通过
Certbot 申请证书。容器 Web 默认只监听 `127.0.0.1:8080`，公网流量统一从宿主机
Nginx 的 80/443 进入。

## 7. 数据与备份

MySQL、MinIO、Milvus、etcd 和 MinerU 缓存使用 Docker named volumes。生产服务器应：

1. 将 Docker 数据目录放到独立 ESSD 数据盘；
2. 为数据盘配置每日自动快照；
3. 定期执行 MySQL 逻辑备份，并验证 MinIO 原始文件可以恢复；
4. 升级或迁移前先备份 named volumes 和 `.env`。

## 8. 更新代码

覆盖代码前先备份数据，然后执行：

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 api web
```

代码更新通常不需要重新上传知识库文件。只有解析或切片逻辑改变且需要更新旧索引时，
才在“AI分析中心”执行“重新解析”。
