import cv2
import numpy as np
import onnxruntime as ort
from minio import Minio
import requests
import subprocess as sp
import time
import os
import json
import threading
import queue
import random
from datetime import datetime

# =========================== 配置区 ===========================
FLV_URL = "http://10.168.89.202:8001/live/stream.flv"
ONNX_MODEL_PATH = "/data/yolo/models/yolo11s.onnx"
BASE_SAVE_DIR = "/data/yolo/yolo_predict_chicken"   #文件保存目录 可以挂载到宿主机持久化
JAVA_CALLBACK_URL = os.getenv("JAVA_BACKEND_URL", "http://java-backend:8080") + "/api/anomaly/callback"
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "10.168.89.202:9000")
MINIO_ACCESS_KEY = "admin"
MINIO_SECRET_KEY = "admin@123"
MINIO_BUCKET = "qcyz"

# 类别与保存策略
CLASS_NAMES = ['Healthy', 'Sick', 'Unclear', 'Dead']
CLASS_NAMES_LOWER = ['healthy', 'sick', 'unclear', 'dead']  # 目录用小写
ANOMALY_CLASS_NAMES = ['sick', 'unclear', 'dead']           # 异常类别（小写，用于MinIO上传和回调）
SAVE_RULES = {
    'dead':    {'change_detection': True, 'min_cooldown': 10},
    'sick':    {'change_detection': True, 'min_cooldown': 20},
    'unclear': {'change_detection': True, 'min_cooldown': 30},
    'healthy': {'change_detection': False, 'save_interval': 120},
}



# 推理参数
CONFIDENCE_THRESHOLD = 0.5
IOU_THRESHOLD = 0.45
INFERENCE_INTERVAL = 5          # 每5帧推理一次（跳帧），但时间间隔控制已实现，此参数可保留或忽略

# FFmpeg 路径
FFMPEG_BIN = "ffmpeg"

# =========================== 初始化 ===========================
print("加载 ONNX 模型...")
session = ort.InferenceSession(ONNX_MODEL_PATH, providers=['CPUExecutionProvider'])
input_name = session.get_inputs()[0].name
output_name = session.get_outputs()[0].name
print(f"模型输入: {input_name}, 输出: {output_name}")

# MinIO 客户端
minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)
if not minio_client.bucket_exists(MINIO_BUCKET):
    minio_client.make_bucket(MINIO_BUCKET)
    print(f"创建 MinIO bucket: {MINIO_BUCKET}")

# 每个类别的上次保存状态（用于变化检测和冷却）
last_state = {}  # {class_name: {'boxes':[...], 'scores':[...], 'class_ids':[...], 'time': 0, 'max_score': 0.0}}
pipe_lock = threading.Lock()  # 保护管道重建

# =========================== 辅助函数 ===========================
def letterbox(img, new_shape=(640, 640), color=(114, 114, 114)):
    shape = img.shape[:2]
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = (new_shape[1] - new_unpad[0]) / 2, (new_shape[0] - new_unpad[1]) / 2
    if shape[::-1] != new_unpad:
        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return img, r, (dw, dh)

def postprocess_yolo(output, orig_shape, conf_thres=0.5, iou_thres=0.45, ratio=1.0, dw=0.0, dh=0.0):
    preds = output[0].T
    scores = preds[:, 4:].max(axis=1)
    mask = scores > conf_thres
    preds = preds[mask]
    scores = scores[mask]
    if len(preds) == 0:
        return [], [], []
    boxes = preds[:, :4].copy()
    boxes[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    boxes[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    boxes[:, 2] = boxes[:, 0] + boxes[:, 2]
    boxes[:, 3] = boxes[:, 1] + boxes[:, 3]
    class_ids = preds[:, 4:].argmax(axis=1)
    indices = cv2.dnn.NMSBoxes(
        bboxes=boxes.tolist(),
        scores=scores.tolist(),
        score_threshold=conf_thres,
        nms_threshold=iou_thres
    )
    if len(indices) == 0:
        return [], [], []
    if isinstance(indices, tuple):
        indices = indices[0]
    elif isinstance(indices, np.ndarray):
        indices = indices.flatten()
    final_boxes = [boxes[i] for i in indices]
    final_scores = [scores[i] for i in indices]
    final_class_ids = [class_ids[i] for i in indices]
    # boxes are absolute pixel coordinates in the 640x640 letterbox space
    # Map back to original frame dimensions
    final_boxes = [[(x1-dw)/ratio, (y1-dh)/ratio, (x2-dw)/ratio, (y2-dh)/ratio] for (x1,y1,x2,y2) in final_boxes]
    return final_boxes, final_scores, final_class_ids

def iou(box1, box2):
    """计算两个框的交并比，box格式 [x1, y1, x2, y2]"""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0

def has_scene_changed(current_boxes, current_scores, last_state_entry, iou_threshold=0.5):
    """变化检测：比较当前检测和上次保存的状态，场景有明显变化则返回True"""
    prev_boxes = last_state_entry['boxes']
    prev_scores = last_state_entry['scores']

    # 1. 数量变化
    if len(current_boxes) != len(prev_boxes):
        return True

    # 2. 位置变化：计算当前框和上次框的IoU
    if len(current_boxes) > 0:
        matches = []
        for cbox in current_boxes:
            best = max(iou(cbox, pbox) for pbox in prev_boxes)
            matches.append(best)
        avg_iou = sum(matches) / len(matches)
        if avg_iou < iou_threshold:
            return True

    # 3. 置信度跃升（可能拍到更清晰的画面）
    if max(current_scores) - max(prev_scores) > 0.2:
        return True

    return False

def save_frame_locally(frame, boxes, scores, class_ids, class_name, timestamp_sec, frame_count):
    """保存帧到本地对应类别目录，并生成标签文件"""
    date_str = datetime.fromtimestamp(timestamp_sec).strftime("%Y-%m-%d")
    category_dir = os.path.join(BASE_SAVE_DIR, class_name.lower())
    images_dir = os.path.join(category_dir, "images", date_str)
    labels_dir = os.path.join(category_dir, "labels", date_str)
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(labels_dir, exist_ok=True)

    # 生成唯一文件名（时间戳+随机数）
    timestamp_ms = int(timestamp_sec * 1000)
    rand_suffix = random.randint(0, 999)
    filename = f"{timestamp_ms}_{rand_suffix}"
    img_path = os.path.join(images_dir, f"{filename}.jpg")
    label_path = os.path.join(labels_dir, f"{filename}.txt")

    # 保存图片
    cv2.imwrite(img_path, frame)
    # 保存标签（YOLO格式）
    h, w = frame.shape[:2]
    with open(label_path, 'w') as f:
        for box, cls_id in zip(boxes, class_ids):
            x1, y1, x2, y2 = box
            x_center = (x1 + x2) / 2.0 / w
            y_center = (y1 + y2) / 2.0 / h
            width = (x2 - x1) / w
            height = (y2 - y1) / h
            f.write(f"{cls_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}\n")
    print(f"[本地保存] {class_name} 帧 #{frame_count} -> {img_path}")
    return img_path, label_path

def upload_to_minio_and_notify(frame, boxes, scores, class_ids, class_name, timestamp_sec, frame_count):
    """上传图片+标签到MinIO，并回调Java（仅异常类别调用）"""
    timestamp_ms = int(timestamp_sec * 1000)
    date_str = datetime.fromtimestamp(timestamp_sec).strftime("%Y-%m-%d")
    local_img = f"temp_anomaly_{timestamp_ms}.jpg"
    local_label = f"temp_anomaly_{timestamp_ms}.txt"
    cv2.imwrite(local_img, frame)

    # 生成标签内容（YOLO格式）
    h, w = frame.shape[:2]
    label_text = ""
    for box, cls_id in zip(boxes, class_ids):
        x1, y1, x2, y2 = box
        x_center = (x1 + x2) / 2.0 / w
        y_center = (y1 + y2) / 2.0 / h
        width_b = (x2 - x1) / w
        height_b = (y2 - y1) / h
        label_text += f"{cls_id} {x_center:.6f} {y_center:.6f} {width_b:.6f} {height_b:.6f}\n"
    with open(local_label, 'w') as f:
        f.write(label_text)

    img_object = f"yolo_predict_chicken/{class_name}/images/{date_str}/{timestamp_ms}.jpg"
    label_object = f"yolo_predict_chicken/{class_name}/labels/{date_str}/{timestamp_ms}.txt"
    try:
        minio_client.fput_object(MINIO_BUCKET, img_object, local_img, content_type="image/jpeg")
        minio_client.fput_object(MINIO_BUCKET, label_object, local_label, content_type="text/plain")
        minio_url = f"http://{MINIO_ENDPOINT}/{MINIO_BUCKET}/{img_object}"
        print(f"[MinIO上传] {class_name} 帧 #{frame_count} -> img+label")
        # 异步回调Java
        payload = {
            "minioUrl": minio_url,
            "timestamp": timestamp_ms,
            "boxes": [[float(x) for x in box] for box in boxes],
            "scores": [float(s) for s in scores],
            "classIds": [int(c) for c in class_ids],
            "frameCount": frame_count,
            "resolution": f"{WIDTH}x{HEIGHT}"
        }
        threading.Thread(target=send_to_java, args=(payload,), daemon=True).start()
    except Exception as e:
        print(f"MinIO 上传失败: {e}")
    finally:
        for p in [local_img, local_label]:
            if os.path.exists(p):
                os.remove(p)

_java_fail_streak = 0
def send_to_java(payload, retry=3):
    """回调 Java，失败时指数退避重试，最长间隔 30 秒。"""
    global _java_fail_streak
    wait = 2
    max_wait = 30
    attempt = 0
    while attempt < retry or retry == -1:
        attempt += 1
        try:
            resp = requests.post(JAVA_CALLBACK_URL, json=payload, timeout=3)
            if resp.status_code == 200:
                if _java_fail_streak > 0:
                    print(f"Java 回调已恢复（之前失败 {_java_fail_streak} 次）")
                _java_fail_streak = 0
                return True
            else:
                print(f"Java 返回 {resp.status_code}，{wait}秒后重试 ({attempt}/{retry})")
        except Exception as e:
            print(f"Java 回调第 {attempt} 次失败，{wait}秒后重试...")
        time.sleep(wait)
        wait = min(wait * 1.5, max_wait)
        if retry != -1 and attempt >= retry:
            break
    _java_fail_streak += 1
    if _java_fail_streak % 5 == 0:
        print(f"Java 回调连续失败 {_java_fail_streak} 次")
    return False

def get_resolution_from_stream(url):
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        raise RuntimeError("无法打开视频流")
    ret, frame = cap.read()
    if not ret:
        cap.release()
        raise RuntimeError("无法读取视频帧")
    h, w = frame.shape[:2]
    cap.release()
    return w, h

# =========================== 获取分辨率 ===========================
print("正在获取视频分辨率...")
# WIDTH, HEIGHT = get_resolution_from_stream(FLV_URL)
WIDTH = 480
HEIGHT = 640
print(f"视频分辨率: {WIDTH} x {HEIGHT}")
FRAME_BYTES = WIDTH * HEIGHT * 3

# =========================== FFmpeg 管道 ===========================
command = [
    FFMPEG_BIN,
    '-reconnect', '1',
    '-reconnect_streamed', '1',
    '-reconnect_delay_max', '10',
    '-i', FLV_URL,
    '-analyzeduration', '0',
    '-probesize', '32',
    '-flags', 'low_delay',
    '-fflags', 'nobuffer',
    '-avioflags', 'direct',
    '-f', 'rawvideo',
    '-pix_fmt', 'bgr24',
    '-an',
    'pipe:1'
]
print("启动 FFmpeg 管道...")
pipe = sp.Popen(command, stdout=sp.PIPE, stderr=sp.PIPE, bufsize=10**8)

# =========================== 主循环 ===========================
frame_count = 0
last_infer_time = time.time()
last_heartbeat = time.time()
last_frame_count_heartbeat = 0
print("开始处理视频流...")

# 帧队列：后台线程读FFmpeg管道，主线程消费
frame_queue = queue.Queue(maxsize=10)

def start_reader(cmd):
    """启动一个新的管道和一个新的读取线程，返回 (pipe, thread)"""
    p = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE, bufsize=10**8)
    t = threading.Thread(target=pipe_reader, args=(p, cmd, frame_queue), daemon=True)
    t.start()
    return p, t

def pipe_reader(pipe_obj, cmd, frame_queue):
    """后台线程：持续从管道读取帧并放入队列"""
    global frame_count
    p = pipe_obj
    while True:
        try:
            raw = p.stdout.read(FRAME_BYTES)
            if len(raw) != FRAME_BYTES:
                print(f"管道数据异常，预期 {FRAME_BYTES} 字节，实际 {len(raw)} 字节，重建...")
                try: p.terminate()
                except: pass
                time.sleep(2)
                with pipe_lock:
                    p = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE, bufsize=10**8)
                continue
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((HEIGHT, WIDTH, 3)).copy()
            frame_count += 1
            try:
                frame_queue.put(frame, timeout=0.1)
            except queue.Full:
                try:
                    frame_queue.get_nowait()
                    frame_queue.put(frame, timeout=0.1)
                except (queue.Full, queue.Empty):
                    pass
        except (ValueError, OSError, AttributeError) as e:
            print(f"管道读取异常: {e}，重建中...")
            try: p.terminate()
            except: pass
            time.sleep(2)
            with pipe_lock:
                p = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE, bufsize=10**8)
        except Exception as e:
            print(f"管道读取异常: {e}")
            time.sleep(2)
            with pipe_lock:
                p = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE, bufsize=10**8)

pipe, reader_thread = start_reader(command)

while True:
    try:
        # 从队列取帧，超时3秒
        frame = frame_queue.get(timeout=3.0)
    except queue.Empty:
        now = time.time()
        if now - last_heartbeat > 30:
            elapsed = now - last_infer_time
            print(f"[心跳] 运行中... 总帧 {frame_count}, 上次推理 {elapsed:.0f}s 前")
            last_heartbeat = now
            # 如果超过 120s 没有新帧，杀掉旧管道让读线程重建
            if frame_count == last_frame_count_heartbeat:
                print("[警告] 120s 内无新帧，终止旧管道触发重建...")
                with pipe_lock:
                    try:
                        pipe.terminate()
                    except Exception:
                        pass
                # 读线程会检测到管道断开自动重建，无需主线程干预
            last_frame_count_heartbeat = frame_count
        continue

    try:
        # 时间间隔控制（每5秒推理一次）
        current_time = time.time()
        if current_time - last_infer_time < 5.0:
            continue
        last_infer_time = current_time
        last_heartbeat = current_time

        # ---------- YOLO 推理 ----------
        img, ratio_lb, (dw, dh) = letterbox(frame, (640, 640))
        img = img.astype(np.float32) / 255.0
        img = img[:, :, ::-1]  # BGR -> RGB（模型训练在RGB上）
        img = img.transpose(2, 0, 1)
        img = np.expand_dims(img, axis=0)
        outputs = session.run([output_name], {input_name: img})
        output = outputs[0]

        boxes, scores, class_ids = postprocess_yolo(
            output,
            (HEIGHT, WIDTH),
            conf_thres=CONFIDENCE_THRESHOLD,
            iou_thres=IOU_THRESHOLD,
            ratio=ratio_lb,
            dw=dw,
            dh=dh
        )

        # ---------- 调试：打印检测结果 ----------
        if boxes:
            class_names_detected = [CLASS_NAMES[c] for c in class_ids]
            print(f"[检测到] 帧 #{frame_count}, {len(boxes)} 个目标: {class_names_detected}, 最高分: {max(scores):.3f}")
        else:
            print(f"[无检测] 帧 #{frame_count}, 试试降低置信度阈值 (当前 {CONFIDENCE_THRESHOLD})")
            continue

        # ---------- 保存策略 ----------
        unique_classes = set(class_ids)
        current_time = time.time()
        need_save = False          # 本轮是否需要保存
        triggered_classes = []     # 触发保存的类别（可能一个画面多个类别同时触发）

        for cls_id in unique_classes:
            class_name = CLASS_NAMES_LOWER[cls_id]
            rule = SAVE_RULES[class_name]
            state = last_state.get(class_name)  # None 表示该类从未保存过

            if rule['change_detection']:
                # 异常类：首次检测立即存，冷却到期后必须存（变化可提前触发）
                if state is None:
                    need_save = True
                    triggered_classes.append(class_name)
                elif current_time - state['time'] >= rule['min_cooldown']:
                    # 冷却已过 → 必存（静态场景也能周期性捕获）
                    need_save = True
                    triggered_classes.append(class_name)
                elif has_scene_changed(boxes, scores, state):
                    # 冷却未过但场景明显变化 → 提前存（最短间隔 = cooldown/3）
                    min_gap = rule['min_cooldown'] / 3
                    if current_time - state['time'] >= min_gap:
                        need_save = True
                        triggered_classes.append(class_name)
            else:
                # 健康类：纯时间间隔，首次只设计时器不触发
                if state is None:
                    last_state[class_name] = {
                        'boxes': [], 'scores': [], 'class_ids': [],
                        'time': current_time, 'max_score': 0.0,
                    }
                elif current_time - state['time'] >= rule['save_interval']:
                    need_save = True
                    triggered_classes.append(class_name)

        if need_save:
            for class_name in triggered_classes:
                save_frame_locally(frame, boxes, scores, class_ids, class_name, current_time, frame_count)
                if class_name in ANOMALY_CLASS_NAMES:
                    threading.Thread(
                        target=upload_to_minio_and_notify,
                        args=(frame, boxes, scores, class_ids, class_name, current_time, frame_count),
                        daemon=True
                    ).start()
                # 更新该类的保存状态
                last_state[class_name] = {
                    'boxes': boxes,
                    'scores': scores,
                    'class_ids': class_ids,
                    'time': current_time,
                    'max_score': max(scores) if scores else 0,
                }
        else:
            # 找最先接近冷却期的类别显示跳过提示
            for cls_id in unique_classes:
                cn = CLASS_NAMES_LOWER[cls_id]
                rule = SAVE_RULES[cn]
                st = last_state.get(cn)
                if st and st.get('time', 0) > 0:
                    remain = rule.get('min_cooldown', rule.get('save_interval', 120)) - (current_time - st['time'])
                    if remain > 0:
                        print(f"[跳过] 帧 #{frame_count} {cn} 冷却剩余 {remain:.0f}s")
                        break

    except Exception as e:
        print(f"推理处理异常: {e}")
        continue