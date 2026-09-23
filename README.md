# EduRAG 教辅知识平台（服务器部署副本）

本目录面向阿里云 Linux 服务器部署。首次部署请先阅读 [SERVER_DEPLOY.md](SERVER_DEPLOY.md)，
不要从本地开发目录复制 `.env`、数据库、日志或缓存到服务器。

服务器副本已包含 `models/bge-m3`，运行时通过 `MODEL_HOST_PATH=./models` 只读挂载到
容器 `/app/models`；Reranker 通过 API 调用，不包含也不要求上传本地重排模型。

独立的公司内部教辅知识平台。前端保留原教辅知识库的视觉和八个业务模块，后端统一为
FastAPI；RAG 核心复用 KnowForge，支持大模型意图识别、真实文件查找、最近上传、
PDF 智能分流、MinerU、三级表格索引、版本管理和来源引用。

## 当前能力

- Vue 3 智能搜索页面和实时“最近上传”侧栏；
- 开放注册、首位注册用户自动成为管理员，后续用户默认为普通成员；
- 文件上传、同名版本链、SHA-256 去重、预览、下载、回收站和回滚；
- 大模型结构化意图识别，文件类意图只查询真实数据库记录；
- PDF 文本层直接提取，纯扫描页和图文复杂资料使用 MinerU CPU Pipeline；
- CSV/Excel 表级摘要、逐行和关联行组三层索引；
- TXT/Markdown/CSV 自动识别 UTF-8、UTF-16（带 BOM）和 GB18030/GBK，预览统一转为 UTF-8；
- 文件、话术和规则统一进入 BGE-M3 + Milvus BM25 混合索引；
- MySQL、MinIO、Milvus、MinerU、Nginx 一体化 Docker 部署。

## 本地界面开发

```powershell
cd frontend
npm install
npm run dev
```

业务后端轻量调试：

```powershell
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt
.venv\Scripts\uvicorn backend.app.main:app --reload --port 8000
```

默认使用本地 SQLite 和 `storage/`，可测试注册、页面及业务接口；完整 RAG 需要安装根目录
`requirements.txt` 并启动 MySQL、Milvus、MinIO 和 MinerU。

## Docker部署

复制配置并替换所有 `CHANGE_ME`、域名、DashScope Key 和 JWT 密钥：

```bash
cp .env.example .env
```

将模型放到：

```text
models/bge-m3/
models/bge-reranker-large/
```

构建并启动：

```bash
mkdir -p logs reports models
docker compose up -d --build
docker compose ps
curl http://127.0.0.1/health
```

### 阿里云镜像加速与 ACR

阿里云 Docker Hub 镜像加速器是账号专属地址。进入“容器镜像服务 ACR → 镜像工具 →
镜像加速器”复制地址，把 `docker/daemon.aliyun.json.example` 中的占位地址替换后保存为：

```text
/etc/docker/daemon.json
```

然后重启 Docker：

```bash
sudo systemctl daemon-reload
sudo systemctl restart docker
```

Docker Hub 上的 Node、Python、Nginx、MySQL、MinIO、Milvus 和 Attu 会自动使用该加速器，
Compose 与 Dockerfile 无需改成非官方公共路径。`quay.io/coreos/etcd` 不属于 Docker Hub；
生产环境可先把它以及其余依赖镜像同步到自己的 ACR 仓库，再在 `.env` 中覆盖完整地址：

```env
NODE_BASE_IMAGE=registry.cn-beijing.aliyuncs.com/你的命名空间/node:22-alpine
PYTHON_BASE_IMAGE=registry.cn-beijing.aliyuncs.com/你的命名空间/python:3.12-slim
NGINX_BASE_IMAGE=registry.cn-beijing.aliyuncs.com/你的命名空间/nginx:1.27-alpine
MYSQL_IMAGE=registry.cn-beijing.aliyuncs.com/你的命名空间/mysql:8.4
ETCD_IMAGE=registry.cn-beijing.aliyuncs.com/你的命名空间/etcd:v3.5.18
MINIO_IMAGE=registry.cn-beijing.aliyuncs.com/你的命名空间/minio:RELEASE.2025-04-22T22-12-26Z
MILVUS_IMAGE=registry.cn-beijing.aliyuncs.com/你的命名空间/milvus:v2.5.15
ATTU_IMAGE=registry.cn-beijing.aliyuncs.com/你的命名空间/attu:v2.5.12
```

这些地址必须对应你已经同步或推送成功的 ACR 仓库；仅替换域名不会自动复制上游镜像。

Milvus 可视化管理界面使用与 Milvus 2.5.x 兼容的 Attu 2.5.12，默认仅监听本机：

```text
http://127.0.0.1:3000
```

Attu 已通过 Docker 内网预配置连接 `milvus:19530`。若页面要求手动填写连接地址，仍应填写
`milvus:19530`，不要填写 `localhost:19530`；后者在 Attu 容器中指向 Attu 自己。

默认启动 MinerU：带图片或扫描的 PDF、以及 Office 文档都会走 MinerU 解析，不再进入“等待复核”。
首次启动会从 modelscope 下载 pipeline 模型，缓存保存在 mineru_cache 卷中；并发解析上限由
MINERU_API_MAX_CONCURRENT_REQUESTS 控制，默认 5。若临时不需要解析能力，可在 .env 里设置
MINERU_ENABLED=false，并用 docker compose stop mineru 停掉该服务。

如果文件曾因编码、字体或解析器问题生成了旧结果，重建镜像后可在“AI分析中心/入库任务”中点击
“重新解析”（已发布版本）或“重试”（失败、等待复核版本）；这会重新生成预览、解析文本和检索索引。
也可以重新上传文件。只有任务变为“已发布”的版本才会进入检索索引。

首次访问后注册的第一个账号是管理员。生产开放给其他用户注册前，应先完成管理员初始化。

### HTTPS

服务器 `.env` 设置 `WEB_BIND=127.0.0.1:8080`，安装宿主机 Nginx 与 Certbot，将
`docker/nginx-host-https.conf.example` 替换成真实域名后启用。容器Web仅监听本机8080，
公网流量统一经过宿主机HTTPS入口。

## 迁移旧系统

先执行演练：

```bash
python scripts/migrate_legacy.py \
  --sqlite ../教辅知识库前端开发/server/data.db \
  --uploads ../教辅知识库前端开发/server/uploads \
  --dry-run
```

确认统计后去掉 `--dry-run`，再顺序处理迁移文件：

```bash
python scripts/process_pending.py
```

迁移工具幂等保留账号、bcrypt密码摘要、板块、文件、话术、规则、日志和时间信息。

## 验证

```powershell
.venv\Scripts\python -m pytest tests\test_api_smoke.py tests\test_pdf_routing.py tests\test_edu_table_indexing.py -q
cd frontend
npm run build
```

## 解析原则

```text
文本层完整的PDF       -> PyMuPDF
纯文字扫描PDF         -> MinerU pipeline OCR
图片/公式/复杂表格PDF -> MinerU pipeline
CSV/Excel             -> 原生表格解析和三级索引
XMind 思维导图        -> 原生解析（content.json / content.xml，无预览）
DOC/PPT/XLS预览       -> LibreOffice PDF
```

所有文档内容均视为不可信证据，不能覆盖系统指令；文件ID、预览地址和下载地址只能由
后端数据库与权限校验产生。
