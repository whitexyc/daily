# PostgreSQL Docker 部署

## 快速启动,不包含向量功能

```bash
docker run -d --name postgres \
  -e POSTGRES_PASSWORD=qcyz \
  -p 5432:5432 \
  bitnami/postgresql:latest
```

## 开放防火墙端口

```bash
firewall-cmd --permanent --add-port=5432/tcp
```

## 自定义镜像（含 pgvector + Apache AGE）

### 构建并启动

```bash
docker compose -p postgres_age build --no-cache
docker compose -p postgres_age up -d
```

### Dockerfile

```dockerfile
# 直接使用官方 PostgreSQL 16 基础镜像
FROM postgres:16

# 1. 换为国内阿里云镜像源 （本地用魔法，我换了各种源都没用，最后打开魔法注释掉下面的源，就完成了）
RUN rm -f /etc/apt/sources.list.d/*.sources && \
    echo "deb http://mirrors.aliyun.com/debian trixie main contrib non-free" > /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian trixie-updates main contrib non-free" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security trixie-security main contrib non-free" >> /etc/apt/sources.list

# 2. 更新源，安装编译工具 + pgvector 扩展 + flex 和 bison
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    postgresql-server-dev-16 \
    make g++ \
    flex \
    bison \
    postgresql-16-pgvector \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 3. 复制 AGE 1.6.0 压缩包（从https://dlcdn.apache.org/age/PG16/1.6.0/apache-age-1.6.0-src.tar.gz下载）
COPY apache-age-1.6.0-src.tar.gz /tmp/age.tar.gz

# 4. 解压、编译并安装 AGE 1.6.0
RUN cd /tmp \
    && tar -xzf age.tar.gz \
    && cd apache-age-* \
    && make \
    && make install \
    && rm -rf /tmp/age.tar.gz /tmp/apache-age-*
```

### docker-compose.yml

```yaml
version: '3.8'

services:
  postgres:
    build: .
    container_name: postgres_ext
    environment:
      - POSTGRES_PASSWORD=qcyz
      - POSTGRES_USER=postgres
      - POSTGRES_DB=postgres
    ports:
      - "5432:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
    # 必须加上下面的 command，让 Apache AGE 在启动时就加载
    command:
      - -c
      - shared_preload_libraries=age

volumes:
  pg_data:
```
