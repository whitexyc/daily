# 基础镜像 - JDK 21
FROM eclipse-temurin:21-jre

# 作者
MAINTAINER qcyz

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 容器内的工作目录
WORKDIR /data/java

# 暴露端口
EXPOSE 8080

# 复制 jar 包
COPY qcyz-1.0.0.jar /data/java/app.jar


# COPY application.yml /data/java/application.yml

# 启动命令（指定外部配置文件位置，如果你挂载的话）
ENTRYPOINT ["java", "-Dspring.config.location=/data/java/application.yml", "-jar", "/data/java/app.jar"]
