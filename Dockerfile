ARG NODE_BASE_IMAGE=node:22-alpine
ARG PYTHON_BASE_IMAGE=python:3.12-slim
ARG NGINX_BASE_IMAGE=nginx:1.27-alpine

FROM ${NODE_BASE_IMAGE} AS frontend
WORKDIR /src
COPY frontend/package*.json ./
RUN npm config set registry https://registry.npmmirror.com && npm install
COPY frontend/ ./
COPY tokens.css ./tokens.css
RUN npm run build

FROM ${PYTHON_BASE_IMAGE} AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Use an Alibaba Cloud mirror for reliable builds from mainland China.
RUN sed -i 's|http://deb.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g; s|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get install -y --no-install-recommends libreoffice curl \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/requirements.txt
COPY requirements.txt requirements.txt
# torch / torchvision 已整体移除：向量化与重排统一走远端 API
# （EMBEDDING_BACKEND=api / RERANK_BACKEND=api），镜像里不再需要本地推理框架。
RUN pip install --no-cache-dir --index-url https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
RUN pip install --no-cache-dir --index-url https://mirrors.aliyun.com/pypi/simple/ "cryptography>=46,<51"
RUN pip install --no-cache-dir --index-url https://mirrors.aliyun.com/pypi/simple/ tomli==2.2.1
# Install runtime fonts before copying application source, so ordinary code
# updates can reuse this relatively large package layer.
RUN sed -i 's|http://deb.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g; s|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*
COPY backend backend
COPY qa_core qa_core
COPY config config
COPY scenarios scenarios
COPY scripts scripts
COPY --from=frontend /src/dist frontend/dist
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM ${NGINX_BASE_IMAGE} AS web
COPY --from=frontend /src/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
# The web image is Alpine-based, so use apk and switch to the Alibaba Cloud mirror
# (the API image above is Debian-based and uses mirrors.aliyun.com via sources.list).
RUN sed -i 's|https://dl-cdn.alpinelinux.org/alpine|https://mirrors.aliyun.com/alpine|g' /etc/apk/repositories \
    && apk add --no-cache \
    font-noto-cjk \
    fontconfig \
    && fc-cache -f -v
