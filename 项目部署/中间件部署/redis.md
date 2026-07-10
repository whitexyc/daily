# Redis 部署

## 启动容器

```bash
docker run -d --name redis \
  -p 6379:6379 \
  -e REDIS_PASSWORD=qcyz \
  -v redis-data:/bitnami/redis/data \
  bitnami/redis:latest
```

## 开放防火墙端口

```bash
firewall-cmd --permanent --add-port=6379/tcp
```
