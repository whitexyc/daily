FROM python:3.9-slim

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
# 删除可能存在的旧源配置，创建新的阿里云源
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
