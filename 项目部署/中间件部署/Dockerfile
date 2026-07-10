# 直接使用官方 PostgreSQL 16 基础镜像
FROM postgres:16

# 1. 换为国内阿里云镜像源
# RUN rm -f /etc/apt/sources.list.d/*.sources && \
#     echo "deb http://mirrors.aliyun.com/debian trixie main contrib non-free" > /etc/apt/sources.list && \
#     echo "deb http://mirrors.aliyun.com/debian trixie-updates main contrib non-free" >> /etc/apt/sources.list && \
#     echo "deb http://mirrors.aliyun.com/debian-security trixie-security main contrib non-free" >> /etc/apt/sources.list

# 2. 更新源，安装编译工具 + pgvector 扩展 + 【flex 和 bison】
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    postgresql-server-dev-16 \
    make g++ \
    flex \
    bison \
    postgresql-16-pgvector \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 3. 复制您刚才下载的 AGE 1.6.0 压缩包
COPY apache-age-1.6.0-src.tar.gz  /tmp/age.tar.gz

# 4. 解压、编译并安装 AGE 1.6.0
RUN cd /tmp \
    && tar -xzf age.tar.gz \
    && cd apache-age-* \
    && make \
    && make install \
    && rm -rf /tmp/age.tar.gz /tmp/apache-age-*