FROM python:3.11-slim

# 设置工作目录（与宿主持平，避免硬编码路径问题）
WORKDIR /home/ubuntu/crypto-scanner

# 安装编译依赖（部分 Python 包需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir eventlet gunicorn

# 复制项目代码
COPY . .

# 创建必要的目录并赋予权限
RUN mkdir -p /var/www data output logs configs /tmp && \
    chmod -R 777 /var/www data output logs /tmp

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV MARKET_HOST=http://localhost:5000

EXPOSE 5000

ENTRYPOINT ["python3", "docker_entrypoint.py"]
