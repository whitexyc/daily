# SRS WebRTC (WHIP/WHEP) 推拉流完整搭建文档

## 一、服务端配置 (SRS Conf)
这是最终完整可用的 SRS 配置文件 (`rtmp2rtc.conf`)。**请直接复制并覆盖您宿主机上的这个文件**。

```nginx
# ============================================================================
# SRS 配置文件 (支持 HTTPS)
# 内网 IP：10.168.89.202
# ============================================================================

# ==================== 全局基本设置 ====================
listen              1935;
max_connections     1000;
daemon              off;
srs_log_tank        console;

# ==================== HTTP API (HTTP & HTTPS) ====================
# 注意：crossdomain on; 是让前端页面能从本地跨域调用 SRS API 的关键！
http_api {
    enabled         on;
    listen          1985;
    https {
        enabled     on;
        listen      1986;
        key         /usr/local/srs/conf/ssl/server.key;
        cert        /usr/local/srs/conf/ssl/server.crt;
    }
    crossdomain     on; 
}

# ==================== HTTP 静态服务器 (支持 HTTPS) ====================
http_server {
    enabled         on;
    listen          8080;           
    https {
        enabled     on;
        listen      8088;           
        key         /usr/local/srs/conf/ssl/server.key;   
        cert        /usr/local/srs/conf/ssl/server.crt;   
    }
    dir             ./objs/nginx/html;
}

# ==================== WebRTC 服务器配置 ====================
rtc_server {
    enabled         on;
    listen          8000;
    candidate       10.168.89.202:8003;  # 注意：告诉浏览器媒体流 UDP 走外部暴露的 8003 端口
    protocol        udp;
}

# ==================== 虚拟主机配置 ====================
vhost __defaultVhost__ {
    rtc {
        enabled     on;
        rtmp_to_rtc on;
        rtc_to_rtmp on;   # 必须开启：将 WebRTC 转为 RTMP/FLV
        nack        on;
        twcc        on;
    }
    
    # 必须开启：将 RTMP 转化为 HTTP-FLV 网络流（让 VLC 和网页能拉取 .flv）
    http_remux {
        enabled     on;
        mount       [vhost]/[app]/[stream].flv;
    }
}
```

## 二、Docker 运行命令
配置修改好后，请切换到配置文件所在的目录，执行以下 `docker run` 命令启动容器。

```bash
docker run -d \
  --name srs \
  -p 1935:1935 \
  -p 1985:1985 \
  -p 1986:1986 \
  -p 8001:8080 \
  -p 8002:8088 \
  -p 8003:8000/udp \
  -v $(pwd)/rtmp2rtc.conf:/usr/local/srs/conf/rtmp2rtc.conf \
  -v $(pwd)/srs_ssl:/usr/local/srs/conf/ssl \
  docker.1ms.run/ossrs/srs:latest \
  ./objs/srs -c /usr/local/srs/conf/rtmp2rtc.conf
```

*(注：请确保 `$(pwd)/srs_ssl` 目录下存放了正确的 `server.key` 和 `server.crt` 证书文件)*

### 开放防火墙端口

```bash
firewall-cmd --permanent --add-port={1935,1985,1986,8001,8002}/tcp
firewall-cmd --permanent --add-port=8003/udp
firewall-cmd --reload
```

查看容器日志：

```bash
docker logs -f srs
```
### 端口说明

| 宿主机端口    | 容器内部端口 | 协议      | 作用                        | 通俗解释                                                                                                             |
| -------- | ------ | ------- | ------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **1935** | 1935   | TCP     | **RTMP 推流/拉流**            | 这是用来接收视频源（比如用 FFmpeg 或 OBS 推流）的"入口"。您推流时填写的地址是 `rtmp://10.168.89.202:1935/live/stream`。                          |
| **1985** | 1985   | TCP     | **HTTP API（管理接口）**        | 用来查询 SRS 的运行状态，比如当前有没有流、有多少人在看。您可以通过 `http://10.168.89.202:1985/api/v1/streams` 查看。一般调试时用，普通观众用不到。               |
| **1986** | 1986   | TCP     | **HTTPS API（信令接口）**       | 用于 WebRTC 的"握手"通信（WHEP/WHIP 协议）。当您在浏览器中播放 WebRTC 时，页面会通过这个端口交换信令数据。例如 WHEP 播放地址中的 `api=1986` 就指向这里。              |
| **8001** | 8080   | TCP     | **HTTP 静态服务（FLV 播放）**     | 提供 **FLV 格式** 的视频流，供网页上的 Flash 或 flv.js 播放器拉取。您可以用 `http://10.168.89.202:8001/live/stream.flv` 来播放。              |
| **8002** | 8088   | TCP     | **HTTPS 静态服务（WHEP 播放页面）** | 提供 **WebRTC 播放器页面**（带 HTTPS 加密）。您在浏览器中打开 `https://10.168.89.202:8002/players/whep.html` 即可看到 WHEP 播放界面。          |
| **8003** | 8000   | **UDP** | **WebRTC 媒体传输**           | 这是 **最关键也最容易忽略** 的端口！WebRTC 播放时，视频和音频数据是通过 **UDP** 传输的，而 TCP 只用来交换信令。您必须确保防火墙放行 **UDP 8003**，否则画面会黑屏（DTLS 握手失败）。 |

## 三、前端代码 (原生 JavaScript 拉流)
这是一个完整的单页 HTML 文件。**您不需要把它部署到服务器，直接保存到本地电脑即可**。

⚠️ **极重要：** 由于浏览器安全策略，**此 HTML 不能直接双击打开（`file://` 协议会被拦截）**。您必须用 IDE（如 VS Code 的 Live Server 插件）运行，或在当前文件夹下开启一个简单的本地服务器（例如 `python3 -m http.server 8000`），然后通过 `http://127.0.0.1:8000` 访问。

复制以下完整代码保存为 `index.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>原生 JS 拉流代码 (WHEP)</title>
    <style>
        body { font-family: sans-serif; padding: 20px; }
        video { width: 80%; max-width: 800px; background: #000; display: block; margin-top: 15px; }
        .box { margin-top: 15px; border: 1px solid #ccc; padding: 10px; background: #f9f9f9; color: #333; font-family: monospace; height: 150px; overflow-y: auto; }
    </style>
</head>
<body>
    <h2>SRS WebRTC (WHEP) 拉流</h2>
    <div>
        <button onclick="startPlay()" id="playBtn">开始拉流</button>
        <button onclick="stopPlay()" id="stopBtn" disabled>停止拉流</button>
    </div>

    <!-- 播放器 -->
    <video id="videoPlayer" autoplay muted playsinline controls></video>
    
    <div id="consoleLog" class="box">等待操作...</div>

    <script>
        // -------------------- 配置项 --------------------
        // 请将此处替换为您的 SRS HTTPS API 地址（端口1986）
        const SRS_API_URL = 'https://10.168.89.202:1986/rtc/v1/whep/?app=live&stream=livestream';
        // ------------------------------------------------

        let pc = null;
        const v = document.getElementById('videoPlayer');
        const logEl = document.getElementById('consoleLog');

        function log(msg) {
            const time = new Date().toLocaleTimeString();
            logEl.innerHTML += `<div>[${time}] ${msg}</div>`;
            logEl.scrollTop = logEl.scrollHeight;
        }

        async function startPlay() {
            document.getElementById('playBtn').disabled = true;
            log('正在发起 WebRTC 请求...');

            pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });

            // 监听远程流推送
            pc.ontrack = (event) => {
                log('✅ 成功获取视频流，正在播放...');
                v.srcObject = event.streams[0];
                v.play().catch(e => log('自动播放被阻止: ' + e));
                document.getElementById('stopBtn').disabled = false;
            };

            pc.oniceconnectionstatechange = () => {
                if (pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'failed') {
                    log('⚠️ 连接已断开！');
                    stopPlay();
                }
            };

            try {
                // 创建 Offer
                const offer = await pc.createOffer({ offerToReceiveAudio: true, offerToReceiveVideo: true });
                await pc.setLocalDescription(offer);
                log('发送 SDP Offer 给 SRS...');

                // 发送请求
                const resp = await fetch(SRS_API_URL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/sdp' },
                    body: offer.sdp
                });

                if (!resp.ok) throw new Error(`SRS 响应错误 (${resp.status})`);

                // 接收 Answer
                const answerSdp = await resp.text();
                await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
                log('✅ 连接建立成功！');

            } catch (e) {
                log('❌ 拉流失败: ' + e.message);
                stopPlay();
            }
        }

        function stopPlay() {
            if (pc) { pc.close(); pc = null; }
            v.srcObject = null;
            document.getElementById('playBtn').disabled = false;
            document.getElementById('stopBtn').disabled = true;
            log('🛑 已停止拉流');
        }
    </script>
</body>
</html>
```

## 四、完整调试使用流程（建议按以下顺序操作）
1. **启动容器**：在 Linux 上运行 `docker restart srs`。
2. **信任证书**：在浏览器中单独打开 `https://10.168.89.202:1986/`，点击"高级 -> 继续前往"，信任该自签名证书（**这一步不做，所有 HTTPS API 请求都会被浏览器拦截**）。
3. **开启推流**：打开 `https://10.168.89.202:8002/players/whip.html?api=1986`，选择 `Screen` 并点击 `Publish`，分享屏幕。
4. **本地拉流验证**：使用 VS Code 的 `Live Server` 打开上面第三步的 `index.html`，点击 **"开始拉流"**，您将能立即以极低延迟看到自己的屏幕画面。

## 五、Web 端推拉流快速操作

### 1. 首次准备：信任证书

浏览器打开 `https://10.168.89.202:1986/`，点击"高级 → 继续前往"，信任自签名证书。

### 2. 推流

1. 打开 `https://10.168.89.202:8002/players/whip.html?api=1986`
2. 选择 **WHIP** → 选择 **Screen** → 点击 **Publish**

![[推流.png]]

### 3. 拉流

#### HTTP-FLV 方式

1. 打开 `http://10.168.89.202:8001/players/srs_player.html`
2. 选择 **LivePlayer**，输入 URL `http://10.168.89.202:8001/live/livestream.flv`，点击 **Play**

![[拉流flv.png]]

#### WebRTC (WHEP) 方式

1. 打开 `https://10.168.89.202:8002/players/whep.html?api=1986`
2. 输入 URL `https://10.168.89.202:1986/rtc/v1/whep/?app=live&stream=livestream`
3. 仅勾选 **Video Only**，点击 **Play**

![[WHIP.png]]

### 4. FFmpeg 推流（摄像头）

#### 查看设备

```bash
ffmpeg -list_devices true -f dshow -i dummy
```

![[获取设备摄像头名称.png]]

#### 推流命令

```bash
ffmpeg -f dshow -framerate 30 -video_size 640x480 \
  -i video="ACER HD User Facing" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
  -f flv rtmp://10.168.89.202:1935/live/livestream/1
```

#### 参数说明

| 参数 | 说明 |
|------|------|
| `-f dshow` | 输入格式为 DirectShow（Windows 捕获视频） |
| `-framerate 30` | 帧率 30 fps |
| `-video_size 640x480` | 分辨率 640×480 |
| `-i video="ACER HD User Facing"` | 摄像头设备名称（通过查询获得） |
| `-c:v libx264` | 视频编码为 H.264 |
| `-preset ultrafast` | 最快编码速度 |
| `-pix_fmt yuv420p` | 像素格式 yuv420p |
| `-f flv` | 输出 FLV 格式 |

![[ffmpeg推流.png]]

### 5. 浏览器播放验证
（摄像头已挡住）
- **FLV 播放**：`http://10.168.89.202:8001/live/livestream.flv`

![[查看.flv.png|1088]]

- **WebRTC 播放**：`https://10.168.89.202:8002/players/whep.html?api=1986`

![[查看WEHP.png]]


## 🎉 完整流程总结

### 一、环境准备

#### 1. 开放防火墙端口（系统防火墙 + 阿里云安全组）
```bash
# 系统防火墙（firewalld）
firewall-cmd --permanent --add-port=80/tcp
firewall-cmd --permanent --add-port=1935/tcp
firewall-cmd --permanent --add-port={1985,1986,8001,8002}/tcp
firewall-cmd --permanent --add-port=8003/udp
firewall-cmd --reload
```
**同时需要在阿里云安全组入方向放行以上端口。**

#### 2. 安装 acme.sh（国内网络使用 Gitee 源）
```bash
git clone https://gitee.com/neilpang/acme.sh.git
cd acme.sh
./acme.sh --install -m your_email@example.com
source ~/.bashrc
```

### 二、申请证书

#### 1. 停止占用 80 端口的服务
```bash
docker stop srs
```

#### 2. 申请 RSA 证书（公网 IP）
```bash
~/.acme.sh/acme.sh --issue --server letsencrypt \
  -d 47.99.145.131 \
  --certificate-profile shortlived \
  --days 3 \
  --standalone \
  --keylength 2048
```
证书生成在 `~/.acme.sh/47.99.145.131/` 目录下。

### 三、部署证书到 SRS

```bash
cp ~/.acme.sh/47.99.145.131/fullchain.cer /data/srs/srs_ssl/server.crt
cp ~/.acme.sh/47.99.145.131/47.99.145.131.key /data/srs/srs_ssl/server.key
docker start srs
```

### 四、验证 HTTPS

浏览器访问：
- `https://47.99.145.131:1986`
- `https://47.99.145.131:8002`

不再出现证书错误，SRS 日志无 `4045` 报错。

### 五、推流与播放

#### 推流（RTMP）
```bash
ffmpeg -f dshow -framerate 30 -video_size 640x480 -i video="摄像头名称" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
  -f flv rtmp://47.99.145.131:1935/live/livestream
```

#### 播放（WHEP）
- **播放 URL**：`https://47.99.145.131:1986/rtc/v1/whep/?app=live&stream=livestream`
- **播放器页面**：`https://47.99.145.131:8002/players/whep.html?api=1986`

**注意：推流和播放的流名必须一致！**

### 六、自动续期配置

#### 绑定续期 hook（证书更新时自动部署并重启 SRS）
```bash
~/.acme.sh/acme.sh --renew -d 47.99.145.131 \
  --renew-hook "cp ~/.acme.sh/47.99.145.131/fullchain.cer /data/srs/srs_ssl/server.crt && cp ~/.acme.sh/47.99.145.131/47.99.145.131.key /data/srs/srs_ssl/server.key && docker restart srs"
```

#### 确认 cron 任务（每天自动检查续期）
```bash
crontab -l | grep acme
```
应显示类似：
```
0 0 * * * "/root/.acme.sh"/acme.sh --cron --home "/root/.acme.sh" > /dev/null
```

## 📌 关键注意事项

| 事项           | 说明                                                                                                       |
| ------------ | -------------------------------------------------------------------------------------------------------- |
| **IP 证书有效期** | Let's Encrypt IP 证书最长 **6 天**，建议用 `--days 3`                                                             |
| **80 端口**    | 续期时必须空闲（SRS 不能占用），否则需改用 `--alpn`（443 端口）                                                                 |
| **流名一致性**    | 推流 RTMP 地址和播放 WHEP URL 中的 `stream` 参数必须相同                                                                |
| **阿里云安全组**   | 必须放行 `80/tcp`（申请证书）、`1935/tcp`（RTMP）、`1986/tcp`（HTTPS API）、`8002/tcp`（HTTPS 控制台）、`8003/udp`（WebRTC 媒体传输） |
| **证书路径**     | 宿主机：`/data/srs/srs_ssl/` → 容器内：`/usr/local/srs/conf/ssl/`                                                |



## 🔄 日常维护命令

| 操作 | 命令 |
|------|------|
| 手动强制续期（测试） | `~/.acme.sh/acme.sh --renew -d 47.99.145.131 --force` |
| 查看证书信息 | `~/.acme.sh/acme.sh --info -d 47.99.145.131` |
| 查看 SRS 日志 | `docker logs srs --tail 30` |
| 重启 SRS | `docker restart srs` |

**至此，整个 SRS + HTTPS 证书 + WebRTC 推拉流流程已全部打通！** 🚀