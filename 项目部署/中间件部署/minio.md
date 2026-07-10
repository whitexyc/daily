# MinIO 对象存储部署

## 启动容器

```bash
docker run -d \
  --name minio-aistor \
  --restart unless-stopped \
  -p 9000:9000 \
  -p 9001:9001 \
  -e "MINIO_ROOT_USER=admin" \
  -e "MINIO_ROOT_PASSWORD=admin@123" \
  -e "MINIO_LICENSE=eyJhbGciOiJFUzM4NCIsInR5cCI6IkpXVCJ9..." \
  -v /data/minio:/data \
  quay.io/minio/aistor/minio:RELEASE.2026-05-28T20-50-32Z \
  server /data --console-address ":9001"
```

> `MINIO_ROOT_PASSWORD` 密码需大于等于八位。

| 端口 | 用途 |
|------|------|
| `9000` | API 服务 |
| `9001` | Web 管理控制台 |

## 开放防火墙端口

```bash
firewall-cmd --permanent --add-port=9000/tcp
firewall-cmd --permanent --add-port=9001/tcp
```

## 访问

浏览器访问 `http://10.168.89.202:9001`，使用上面配置的账号密码登录。