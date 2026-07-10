FROM nginx:alpine

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 删除默认配置，复制自定义配置
RUN rm /etc/nginx/nginx.conf
COPY nginx.conf /etc/nginx/nginx.conf

# 复制前端静态文件（构建后的 dist）
COPY dist /usr/share/nginx/html/dist

# 暴露端口
EXPOSE 80

# 默认启动 nginx（前台运行）
CMD ["nginx", "-g", "daemon off;"]
