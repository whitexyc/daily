# RabbitMQ Docker 部署

## 拉取镜像

```bash
docker pull rabbitmq:3.8-management
```

## 启动容器

```bash
docker run \
  -e RABBITMQ_DEFAULT_USER=root \
  -e RABBITMQ_DEFAULT_PASS=123456 \
  -v mq-plugins:/plugins \
  --name mq \
  --hostname mq1 \
  -p 15672:15672 \
  -p 5672:5672 \
  -d \
  rabbitmq:3.8-management
```

| 端口      | 用途        |
| ------- | --------- |
| `15672` | Web 管理界面  |
| `5672`  | AMQP 协议端口 |

访问：`http://10.168.89.202:15672`

## 关于 --hostname

RabbitMQ 的节点名称（Node Name）默认是 `rabbit@<hostname>`。设置 `--hostname mq1` 后，容器内的 RabbitMQ 节点名就会是 `rabbit@mq1`。

如果不设置 `--hostname`，Docker 会随机生成一个容器 ID 作为 hostname（比如 `a1b2c3d4e5f6`），节点名就会变成 `rabbit@a1b2c3d4e5f6`。这种随机性会导致两个严重问题：

- **集群配置困难**：每次重启容器，hostname 都会变，你可能需要频繁更改集群配置。
- **数据丢失风险**：RabbitMQ 的状态（如队列、交换器）默认会以节点名作为标识的一部分。节点名一变，它就无法加载之前的数据，看起来就像数据丢失了一样。

## 安装延迟消息插件

1. 查看默认卷的位置：
   ```bash
   docker volume inspect mq-plugins
   ```

2. 获取 `.ez` 插件包，上传到插件目录：
   [rabbitmq-delayed-message-exchange 3.8.9](https://github.com/rabbitmq/rabbitmq-delayed-message-exchange/releases/tag/3.8.9)
3. ![[上传RabbitMq插件.png]]

4. 进入容器内部，启动插件：
   ```bash
   docker exec -it mq bash
   rabbitmq-plugins enable rabbitmq_delayed_message_exchange
   ```
![[进入RabbitMq内部启动插件.png]]
## Spring Boot 配置

```yaml
spring:
  rabbitmq:
    host: 10.168.89.202
    port: 5672
    username: root
    password: 123456
    virtual-host: /
```
