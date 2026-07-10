# QCYZ 部署文档

## 目录结构

```text
/data/
├── docker-compose.yml
├── java/
│   ├── Dockerfile
│   ├── application.yml              # Spring Boot 配置文件（挂载）
│   ├── campus-enterprise-platform-1.0.0.jar
│   └── logs/                        # 日志目录（挂载）
├── nginx/
│   ├── Dockerfile
│   ├── certs/                       # SSL 证书（挂载，只读）
│   ├── dist/                        # 前端构建产物（可选挂载）
│   └── nginx.conf                   # Nginx 配置（可选挂载）
├── python-yolo/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── yolo_ffmpeg1.py              # 长连接脚本
│   ├── yolo_predict/                # 推理结果（持久化）
│   └── models/
│       └── yolo11s.onnx             # YOLO 模型文件
└── python-xgb/
    ├── Dockerfile
    ├── requirements.txt
    ├── predict_client.py            # 定时预测脚本
    ├── xgb_predict/                 # 预测结果（持久化）
    └── models/
        ├── xgb_model.joblib
        └── label_encoder.joblib
```

## Java 后端

### Dockerfile

```dockerfile
# 基础镜像 - JDK 21
FROM eclipse-temurin:21-jre

# 作者
MAINTAINER qcyz

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 工作目录（按你要求 /data）
WORKDIR /data/java

# 暴露端口
EXPOSE 8080

# 复制 jar 包
COPY qcyz-1.0.0.jar /data/java/app.jar

# COPY application.yml /data/java/application.yml

# 启动命令（指定外部配置文件位置，如果你挂载的话）
ENTRYPOINT ["java", "-Dspring.config.location=/data/java/application.yml", "-jar", "/data/java/app.jar"]
```

## Python YOLO 服务

### Dockerfile

```dockerfile
FROM python:3.9-slim

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
# ===== 替换为阿里云镜像源（加速 apt-get） =====
RUN rm -rf /etc/apt/sources.list.d/* && \

    echo "deb http://mirrors.aliyun.com/debian/ bookworm main contrib non-free" > /etc/apt/sources.list && \

    echo "deb http://mirrors.aliyun.com/debian/ bookworm-updates main contrib non-free" >> /etc/apt/sources.list && \

    echo "deb http://mirrors.aliyun.com/debian-security bookworm-security main contrib non-free" >> /etc/apt/sources.list
# 安装系统依赖（ffmpeg 和 OpenCV 依赖）
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 工作目录
WORKDIR /data/yolo

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制脚本和模型
COPY yolo_ffmpeg1.py /data/yolo/
COPY models/yolo11s.onnx /data/yolo/models/

# 创建保存目录（持久化数据可挂载）
RUN mkdir -p /data/yolo_predict_chicken

# 启动（-u 禁用缓冲，便于查看日志）
CMD ["python", "-u", "yolo_ffmpeg1.py"]
```

### requirements.txt

```text
opencv-python
numpy
onnxruntime
minio
requests
```

## Python XGB 服务

### Dockerfile

```dockerfile
FROM python:3.9-slim

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
# 换源
RUN rm -rf /etc/apt/sources.list.d/* && \

    echo "deb http://mirrors.aliyun.com/debian/ bookworm main contrib non-free" > /etc/apt/sources.list && \

    echo "deb http://mirrors.aliyun.com/debian/ bookworm-updates main contrib non-free" >> /etc/apt/sources.list && \

    echo "deb http://mirrors.aliyun.com/debian-security bookworm-security main contrib non-free" >> /etc/apt/sources.list
# 安装 matplotlib 需要的中文字体
RUN apt-get update && apt-get install -y \
    fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /data/xgb

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY predict_client.py /data/xgb/
COPY models/xgb_model.joblib /data/xgb/models/
COPY models/label_encoder.joblib /data/xgb/models/

RUN mkdir -p /data/xgb/predict_results

CMD ["python", "-u", "predict_client.py"]
```

### requirements.txt

```text
numpy
pandas
scikit-learn
xgboost
joblib
minio
requests
matplotlib
```

## Docker Compose

```yaml
version: '3.8'

services:
  # ========== Java 后端（JDK 21） ==========
  java-backend:
    build:
      context: ./java
      dockerfile: Dockerfile
    container_name: java-backend
    ports:
      - "8080:8080"
    environment:
      - TZ=Asia/Shanghai
    volumes:
      # 挂载配置文件（如果内置在镜像中则可省略）
      - ./java/application.yml:/data/java/application.yml:ro
      # 挂载日志目录（如果有）
      - ./java/logs:/data/java/logs
    networks:
      - chicken-net
    restart: unless-stopped
  # ========== nginx 前端 ==========
  nginx:
    build:
      context: ./nginx
      dockerfile: Dockerfile
    container_name: nginx
    ports:
      - "80:80"
      - "443:443"                     # 新增 HTTPS 端口
    environment:
      - TZ=Asia/Shanghai
    volumes:
      # 挂载证书目录（只读）
      - ./nginx/certs:/etc/nginx/certs:ro
      # 如需动态更新前端或 nginx 配置，可取消下面注释
      # - ./nginx/dist:/usr/share/nginx/html/dist:ro
      # - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    networks:
      - chicken-net
    depends_on:
      - java-backend
    restart: unless-stopped

  # ========== YOLO 微服务（长连接） ==========
  python-yolo:
    build:
      context: ./python-yolo
      dockerfile: Dockerfile
    container_name: python-yolo
    restart: always
    environment:
      - TZ=Asia/Shanghai
      - JAVA_BACKEND_URL=http://java-backend:8080
      - MINIO_ENDPOINT=10.168.89.202:9000
      - MINIO_ACCESS_KEY=admin
      - MINIO_SECRET_KEY=admin@123
      - FLV_URL=http://10.168.89.202:8001/live/stream.flv   # 根据实际修改
    volumes:
      # 将容器内的 /data/yolo/predict_chicken 挂载到宿主机 ./data/python-yolo/yolo_predict
      - ./python-yolo/yolo_predict:/data/yolo/predict_chicken
    networks:
      - chicken-net
    depends_on:
      - java-backend

  # ========== XGB 微服务（定时） ==========
  python-xgb:
    build:
      context: ./python-xgb
      dockerfile: Dockerfile
    container_name: python-xgb
    restart: always
    environment:
      - TZ=Asia/Shanghai
      - JAVA_BACKEND_URL=http://java-backend:8080
      - MINIO_ENDPOINT=10.168.89.202:9000
      - MINIO_ACCESS_KEY=admin
      - MINIO_SECRET_KEY=admin@123
      # 可覆盖预测间隔（单位秒），如果脚本支持
      # - PREDICT_INTERVAL=60
    volumes:
      # 结果目录持久化
      - ./python-xgb/xgb_predict:/data/xgb/predict_results
    networks:
      - chicken-net
    depends_on:
      - java-backend

networks:
  chicken-net:
    driver: bridge
```

## 六、启动和测试

cd /data
docker compose up -d
docker compose stop 
docker compose start
# 查看日志
docker compose logs -f python-yolo
docker compose logs -f python-xgb

如果一切正常，YOLO 会拉流并开始推理，XGB 会定时执行预测（默认间隔在脚本中定义）。

docker stop java-backend python-yolo python-xgb   # 先停止
docker rm java-backend python-yolo python-xgb     # 再删除