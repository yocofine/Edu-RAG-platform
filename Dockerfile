ARG NODE_BASE_IMAGE=node:22-alpine
ARG PYTHON_BASE_IMAGE=python:3.12-slim
ARG NGINX_BASE_IMAGE=nginx:1.27-alpine

FROM ${NODE_BASE_IMAGE} AS frontend
WORKDIR /src
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
COPY tokens.css ./tokens.css
RUN npm run build

FROM ${PYTHON_BASE_IMAGE} AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Keep this layer identical to the previously published image so existing
# LibreOffice layers can be reused during incremental rebuilds.
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice curl && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/requirements.txt
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.7.1 torchvision==0.22.1
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir "cryptography>=46,<51"
RUN pip install --no-cache-dir tomli==2.2.1
COPY backend backend
COPY qa_core qa_core
COPY config config
COPY scenarios scenarios
COPY scripts scripts
COPY --from=frontend /src/dist frontend/dist
# Keep font installation after dependency/source layers so existing pip and
# LibreOffice layers remain reusable when only application code changes.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM ${NGINX_BASE_IMAGE} AS web
COPY --from=frontend /src/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
# The web image is Alpine-based, so use apk (the API image above is Debian-based
# and intentionally keeps its apt-get installation unchanged).
RUN apk add --no-cache \
    font-noto-cjk \
    fontconfig \
    && fc-cache -f -v
