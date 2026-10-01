ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}
ENV MINERU_MODEL_SOURCE=modelscope MINERU_API_MAX_CONCURRENT_REQUESTS=1
RUN sed -i 's|http://deb.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g; s|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*
# MinerU 本地容器仍然需要 torch/torchvision（CPU wheel），这里保留；
# 注意 api 镜像已完全移除 torch，两者互不影响。
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.7.1 torchvision==0.22.1
RUN pip install --no-cache-dir --index-url https://mirrors.aliyun.com/pypi/simple/ "mineru[pipeline]>=3.4,<3.5"
# MinerU 的 pytorch_paddle OCR 模块 import six，但上游没声明该依赖，这里显式补上
RUN pip install --no-cache-dir --index-url https://mirrors.aliyun.com/pypi/simple/ six
# 并发解析上限独立成行放在末尾，避免调整该值时让前面的依赖层失效
ENV MINERU_API_MAX_CONCURRENT_REQUESTS=5
VOLUME ["/root/.cache", "/data/output"]
CMD ["mineru-api", "--host", "0.0.0.0", "--port", "8000"]
