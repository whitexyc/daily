"""
鸡舍健康预测客户端 - 小白版
========================
这个脚本会自动运行，每过一段时间做一次：

步骤1：从 Java 后端获取传感器数据（温度、湿度、氨气等）
步骤2：用训练好的模型预测每只鸡的健康状态
步骤3：生成鸡舍风险热力图和状态分布图
步骤4：把图片上传到 MinIO（图片服务器）
步骤5：把图片地址和统计结果回传给 Java 后端
步骤6：根据实际数据自动计算环境舒适/异常阈值，推送给 Java

你只需要改下面"配置区"里的 JAVA_API_URL 和 JAVA_CALLBACK_URL 两个地址就行。

Java 后端需要提供 3 个接口：
  GET  /api/sensor-data       返回传感器数据
  POST /api/xgb/callback      接收预测结果
  POST /api/xgb/threshold     接收环境阈值
"""

import os
import json
import time
import joblib
import requests
import numpy as np
from datetime import datetime
from minio import Minio
import matplotlib
matplotlib.use('Agg')
# matplotlib 中文字体配置
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun', 'Noto Sans SC']
matplotlib.rcParams['axes.unicode_minus'] = False

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# ═══════════════════════════════════════════════
# 配置区（你只需要改这里！！！）
# ═══════════════════════════════════════════════

# Java 后端的地址，改成你的实际地址
# 从 Java 拉取传感器数据的接口
# JAVA_API_URL = "http://localhost:8080/api/sensor-data"
# # 把预测结果回传给 Java 的接口
# JAVA_CALLBACK_URL = "http://localhost:8080/api/xgb/callback"
# # 把环境阈值推送给 Java 的接口
# JAVA_THRESHOLD_URL = "http://localhost:8080/api/xgb/threshold"
# Java 地址从环境变量读取
JAVA_BACKEND_HOST = os.getenv("JAVA_BACKEND_URL", "http://java-backend:8080")
JAVA_API_URL = JAVA_BACKEND_HOST + "/api/sensor-data"
JAVA_CALLBACK_URL = JAVA_BACKEND_HOST + "/api/xgb/callback"
JAVA_THRESHOLD_URL = JAVA_BACKEND_HOST + "/api/xgb/threshold"
# MinIO 图片服务器的地址和账号
MINIO_ENDPOINT = "10.168.89.202:9000"
MINIO_ACCESS_KEY = "admin"
MINIO_SECRET_KEY = "admin@123"
MINIO_BUCKET = "qcyz"
XGB_BASE_PATH = "xgb_predict"

# 隔多久预测一次（单位：秒）
# 生产环境 = 86400（24小时一次）
# 测试环境 = 60（每分钟一次，看效果用）
PREDICT_INTERVAL = 86400

# 鸡舍尺寸（长x宽，单位：米）
SIZE_X = 10.0
SIZE_Y = 8.0

# ═══════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))      # 现在 BASE_DIR = /data/xgb
MODELS_DIR = os.path.join(BASE_DIR, 'models')              # /data/xgb/models
RESULTS_DIR = os.path.join(BASE_DIR, 'predict_results')    # /data/xgb/predict_results
os.makedirs(RESULTS_DIR, exist_ok=True)

model = joblib.load(os.path.join(MODELS_DIR, 'xgb_model.joblib'))
label_info = joblib.load(os.path.join(MODELS_DIR, 'label_encoder.joblib'))
class_names = label_info['classes']         # ['healthy','sick','dead','unclear']
chinese_labels = label_info['chinese_labels']

minio_client = Minio(
    MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY, secure=False
)
if not minio_client.bucket_exists(MINIO_BUCKET):
    minio_client.make_bucket(MINIO_BUCKET)

CATEGORY_KEYS = ['healthy', 'sick', 'dead', 'unclear']
CATEGORY_COLORS = {'healthy': '#2ecc71', 'sick': '#f39c12', 'dead': '#e74c3c', 'unclear': '#95a5a6'}
CATEGORY_CN = {'healthy': '健康', 'sick': '病鸡', 'dead': '死鸡', 'unclear': '需人工检测'}

print("=" * 55)
print("  鸡舍健康预测客户端 + 可视化 (含 alarm、moveStatus)")
print(f"  Java 数据接口: {JAVA_API_URL}")
print(f"  Java 回调接口: {JAVA_CALLBACK_URL}")
print(f"  Java 阈值接口: {JAVA_THRESHOLD_URL}")
print(f"  预测间隔: {PREDICT_INTERVAL} 秒")
print(f"  MinIO: {MINIO_ENDPOINT}/{MINIO_BUCKET}/{XGB_BASE_PATH}")
print("=" * 55)


def build_features(record):
    """把一条传感器数据转成模型能识别的 14 个数字（特征向量）。

    Java 传来的原始数据有 x, y, z, temp 等 10 个字段，
    但模型需要更多信息，所以额外计算：
    - dist_wall：到墙壁的距离（靠墙的鸡通风差，容易生病）
    - nh3_temp：氨气 x 温度（高温下氨气更毒）
    - co2_hum：二氧化碳 x 湿度
    - local_density：局部密度（固定为1）
    最终拼成 14 个数字送给模型。
    """
    x = float(record['x'])
    y = float(record['y'])
    z = float(record.get('z', 1.5))
    temp = float(record['temp'])
    hum = float(record['hum'])
    co2 = float(record['co2'])
    nh3 = float(record['nh3'])
    light = float(record['light'])
    if light < 0:
        light = 150.0  # 传感器无数据时使用默认值
    alarm = int(record.get('aiAlarm', record.get('alarm', 1)))
    move_status = int(record.get('moveStatus', 1))

    dist_wall = min(x, SIZE_X - x, y, SIZE_Y - y)
    nh3_temp = nh3 * temp
    co2_hum = co2 * hum
    local_density = 1

    return np.array([x, y, z, temp, hum, co2, nh3, light, alarm, move_status,
                     dist_wall, nh3_temp, co2_hum, local_density], dtype=np.float32)


def fetch_data():
    """调用 Java 接口获取传感器数据。

    去请求 JAVA_API_URL 这个地址，拿到一批鸡的传感器数据。
    如果连不上 Java 或返回空数据，就跳过本轮预测。
    """
    try:
        resp = requests.get(JAVA_API_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            print(f"[警告] Java 返回的不是数组: {type(data)}")
            return None
        return data
    except requests.exceptions.ConnectionError:
        print(f"[错误] 无法连接 Java: {JAVA_API_URL}")
        return None
    except Exception as e:
        print(f"[错误] 获取数据异常: {e}")
        return None


def predict_all(data):
    """对每只鸡做预测，返回每只鸡的健康状态。

    处理流程：
    1. 调用 build_features() 把原始数据转成 14 个数字
    2. 调用模型算出 4 种状态的概率
    3. 取概率最高的作为预测结果

    返回结果包含：原始数据 + 预测标签 + 4项概率
    """
    results = []
    for item in data:
        try:
            feat = build_features(item)
            proba = model.predict_proba([feat])[0]
            pred_idx = int(model.predict([feat])[0])
            results.append({
                'x': float(item['x']), 'y': float(item['y']), 'z': float(item.get('z', 1.5)),
                'temp': float(item['temp']), 'hum': float(item['hum']),
                'co2': float(item['co2']), 'nh3': float(item['nh3']),
                'light': float(item['light']), 'alarm': int(item.get('aiAlarm', item.get('alarm', 1))),
                'moveStatus': int(item.get('moveStatus', 1)),
                'label_idx': pred_idx,
                'label_en': class_names[pred_idx],
                'label_cn': chinese_labels[pred_idx],
                'probs': [round(float(p), 4) for p in proba],
            })
        except Exception as e:
            print(f"[跳过] 一条数据处理失败: {e}")
            continue
    return results


import pandas as pd

def make_position_map(results, timestamp_str):
    """画一张鸡舍风险热力图。

    这张图有两层信息：
    1. 背景颜色 = 区域风险高低（红=高风险，绿=低风险）
       方法：把鸡舍分成 30x30 网格，对每格预测病死概率
    2. 彩色圆点 = 每只鸡的位置和健康状态
       绿色=健康  黄色=病鸡  红色=死鸡  灰色=待检测
    """
    GRID = 30
    xs = np.linspace(0, SIZE_X, GRID)
    ys = np.linspace(0, SIZE_Y, GRID)
    xx, yy = np.meshgrid(xs, ys)

    # 用实际数据的 median 填充非位置特征
    df = pd.DataFrame(results)
    z_med = float(df['z'].median()) if 'z' in df else 1.5
    temp_med = float(df['temp'].median()) if 'temp' in df else 25.0
    hum_med = float(df['hum'].median()) if 'hum' in df else 70.0
    co2_med = float(df['co2'].median()) if 'co2' in df else 800.0
    nh3_med = float(df['nh3'].median()) if 'nh3' in df else 10.0
    light_med = float(df['light'].median()) if 'light' in df else 150.0
    alarm_med = int(df['alarm'].mode().iloc[0]) if 'alarm' in df else 1
    move_med = int(df['moveStatus'].mode().iloc[0]) if 'moveStatus' in df else 1

    grid_feats = []
    for i in range(GRID):
        for j in range(GRID):
            dw = min(xx[i, j], SIZE_X - xx[i, j], yy[i, j], SIZE_Y - yy[i, j])
            grid_feats.append([
                xx[i, j], yy[i, j], z_med,
                temp_med, hum_med, co2_med, nh3_med, light_med,
                alarm_med, move_med,
                dw,
                nh3_med * temp_med,
                co2_med * hum_med,
                1,
            ])

    grid_X = np.array(grid_feats, dtype=np.float32)
    grid_prob = model.predict_proba(grid_X)
    risk = grid_prob[:, 2] + grid_prob[:, 1]  # P(dead) + P(sick)
    risk_grid = risk.reshape(GRID, GRID)

    # 绘图
    fig, ax = plt.subplots(figsize=(10, 7))
    c = ax.contourf(xx, yy, risk_grid, levels=20, cmap='RdYlGn_r', alpha=0.85)
    plt.colorbar(c, ax=ax, label='Risk Index (dead + sick probability)')

    ax.set_xlim(0, SIZE_X)
    ax.set_ylim(0, SIZE_Y)
    ax.set_aspect('equal')
    ax.set_xlabel('x (m)')
    ax.set_ylabel('y (m)')
    ax.set_title(f'Chicken House Risk Heatmap  ({timestamp_str})')

    # 叠加点位
    for cat_key in CATEGORY_KEYS:
        pts = [r for r in results if r['label_en'] == cat_key]
        if not pts:
            continue
        xs_pts = [p['x'] for p in pts]
        ys_pts = [p['y'] for p in pts]
        ax.scatter(xs_pts, ys_pts, c=CATEGORY_COLORS[cat_key], label=CATEGORY_CN[cat_key],
                   s=40, edgecolors='k', linewidth=0.5, alpha=0.85, zorder=5)
    ax.legend(loc='upper right', fontsize=11)

    # 统计文字
    counts = {cat: sum(1 for r in results if r['label_en'] == cat) for cat in CATEGORY_KEYS}
    alarm_n = sum(1 for r in results if r['alarm'] == 1)
    move_n = sum(1 for r in results if r['moveStatus'] == 1)
    text = f"Total: {len(results)}   "
    text += "  ".join(f"{CATEGORY_CN[c]}: {counts[c]}" for c in CATEGORY_KEYS)
    text += f"  |  Alarm: {alarm_n}N/{len(results)-alarm_n}A"
    text += f"  Move: {move_n}M/{len(results)-move_n}S"
    ax.text(0.5, -0.06, text, transform=ax.transAxes, ha='center', fontsize=10,
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    fig.tight_layout()
    local_path = os.path.join(RESULTS_DIR, f"position_map_{timestamp_str}.png")
    fig.savefig(local_path, dpi=150)
    plt.close(fig)
    return local_path, f"{XGB_BASE_PATH}/position_map/{timestamp_str[:10]}/position_map_{timestamp_str}.png"
def make_status_chart(results, timestamp_str):
    """画一张状态分布图（左右两半）。

    左半边：柱状图，显示各类鸡的数量
    右半边：表格，列出每只鸡的详细概率
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5),
                                    gridspec_kw={'width_ratios': [1, 1.5]})

    counts = {cat: sum(1 for r in results if r['label_en'] == cat) for cat in CATEGORY_KEYS}
    bars = ax1.bar(CATEGORY_KEYS, [counts[c] for c in CATEGORY_KEYS],
                   color=[CATEGORY_COLORS[c] for c in CATEGORY_KEYS], edgecolor='white')
    ax1.set_title(f'Health Status Distribution  ({timestamp_str})')
    ax1.set_ylabel('Count')
    ax1.set_xticklabels([CATEGORY_CN[c] for c in CATEGORY_KEYS])
    for bar, count in zip(bars, [counts[c] for c in CATEGORY_KEYS]):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                 str(count), ha='center', fontsize=11, fontweight='bold')

    ax2.axis('off')
    table_data = []
    col_labels = ['#', 'Position', 'Alarm', 'Move', 'Result', 'Healthy', 'Sick', 'Dead', 'Unclear']
    for i, r in enumerate(results[:25]):
        table_data.append([
            i + 1,
            f"({r['x']:.1f},{r['y']:.1f})",
            '正常' if r['alarm'] == 1 else '异常',
            '动' if r['moveStatus'] == 1 else '停',
            r['label_cn'],
            f"{r['probs'][0]:.0%}",
            f"{r['probs'][1]:.0%}",
            f"{r['probs'][2]:.0%}",
            f"{r['probs'][3]:.0%}",
        ])
    if len(results) > 25:
        table_data.append(['...', f'共 {len(results)} 条', '', '', '', '', '', '', ''])

    table = ax2.table(cellText=table_data, colLabels=col_labels,
                      cellLoc='center', loc='center',
                      colWidths=[0.08, 0.15, 0.08, 0.07, 0.1, 0.08, 0.08, 0.08, 0.08])
    table.auto_set_font_size(False)
    table.set_fontsize(7.5)
    table.scale(1, 1.3)
    ax2.set_title('Detailed Prediction Probabilities', fontsize=11)

    fig.tight_layout()
    local_path = os.path.join(RESULTS_DIR, f"status_chart_{timestamp_str}.png")
    fig.savefig(local_path, dpi=150)
    plt.close(fig)
    return local_path, f"{XGB_BASE_PATH}/status_chart/{timestamp_str[:10]}/status_chart_{timestamp_str}.png"


def upload_to_minio(local_path, object_path):
    """把本地图片传到 MinIO，返回浏览器可访问的地址。"""
    minio_client.fput_object(MINIO_BUCKET, object_path, local_path, content_type="image/png")
    return f"http://{MINIO_ENDPOINT}/{MINIO_BUCKET}/{object_path}"


def callback_to_java(image_urls, summary):
    """把预测结果（图片地址+统计）POST 回 Java 后端。"""
    payload = {
        'predictTime': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'imageUrls': image_urls,
        'summary': summary,
    }
    try:
        resp = requests.post(JAVA_CALLBACK_URL, json=payload, timeout=30)
        print(f"[回调] Java 返回 {resp.status_code}")
        return resp.ok
    except Exception as e:
        print(f"[回调失败] {e}")
        return False


def push_thresholds(results):
    """Calculate thresholds from sensor data and push to Java."""
    if not results:
        return

    # 提取各环境参数
    temps = [r['temp'] for r in results]
    hums = [r['hum'] for r in results]
    co2s = [r['co2'] for r in results]
    nh3s = [r['nh3'] for r in results]
    lights = [r['light'] for r in results]

    def pct(arr, p):
        return float(np.percentile(sorted(arr), p))

    def build_field(arr, unit, dual_sided=True):
        """Calculate comfort and abnormal ranges for a parameter."""
        low = pct(arr, 25)
        high = pct(arr, 75)
        extreme_low = pct(arr, 5)
        extreme_high = pct(arr, 95)
        return {
            "comfort": {"min": round(low, 1), "max": round(high, 1), "unit": unit},
            "abnormal": {"min": round(extreme_low, 1), "max": round(extreme_high, 1), "unit": unit},
        }

    fields = {
        "temperature": build_field(temps, "C"),
        "humidity": build_field(hums, "%"),
        "co2": build_field(co2s, "ppm"),
        "nh3": build_field(nh3s, "ppm"),
        "light": build_field(lights, "Lux"),
    }

    # 裁剪到合理物理范围
    ranges = {
        "temperature": (0, 60),
        "humidity": (0, 100),
        "co2": (300, 5000),
        "nh3": (0, 100),
        "light": (0, 1000),
    }
    for key, (lo, hi) in ranges.items():
        for typ in ["comfort", "abnormal"]:
            f = fields[key][typ]
            if "min" in f:
                f["min"] = max(lo, f["min"])
            if "max" in f:
                f["max"] = min(hi, f["max"])

    for typ in ["comfort", "abnormal"]:
        config = {key: fields[key][typ] for key in fields}
        payload = {
            "type": typ,
            "description": f"{typ} threshold (auto-calculated from {len(results)} samples)",
            "configData": config,
        }
        try:
            resp = requests.post(JAVA_THRESHOLD_URL, json=payload, timeout=30)
            print(f"  [threshold] {typ} -> {resp.status_code}")
        except Exception as e:
            print(f"  [threshold] {typ} failed: {e}")


def print_summary(results):
    """在控制台打印本次预测的结果摘要。"""
    counts = {cat: sum(1 for r in results if r['label_en'] == cat) for cat in CATEGORY_KEYS}
    alarm_n = sum(1 for r in results if r['alarm'] == 1)
    print(f"\n[CHART] 预测完成 - {len(results)} 条")
    print(f"   [OK] {CATEGORY_CN['healthy']}: {counts['healthy']}")
    print(f"    {CATEGORY_CN['sick']}: {counts['sick']}")
    print(f"    {CATEGORY_CN['dead']}: {counts['dead']}")
    print(f"    {CATEGORY_CN['unclear']}: {counts['unclear']}")
    print(f"   Alarm异常: {len(results)-alarm_n}  正常: {alarm_n}")
    move_n = sum(1 for r in results if r['moveStatus'] == 1)
    print(f"   Move停止: {len(results)-move_n}  移动: {move_n}")


# ═══════════════════════════════════════════════
# 主循环（脚本会一直循环执行下面的步骤）
# ═══════════════════════════════════════════════

def run_once():
    """执行一轮完整的预测流程。

    顺序：获取数据 -> 预测 -> 画图 -> 上传MinIO -> 推阈值 -> 回调Java
    """
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n[{timestamp_str}] 开始一轮预测...")

    data = fetch_data()
    if not data:
        print("[EMPTY] 无数据，跳过本轮")
        return

    results = predict_all(data)
    if not results:
        print("[EMPTY] 预测结果为空，跳过")
        return

    print_summary(results)

    print("[IMAGE] 生成图表...")
    position_local, position_obj = make_position_map(results, timestamp_str)
    chart_local, chart_obj = make_status_chart(results, timestamp_str)

    print("[CLOUD] 上传 MinIO...")
    position_url = upload_to_minio(position_local, position_obj)
    chart_url = upload_to_minio(chart_local, chart_obj)
    print(f"   [MAP] 点位图: {position_url}")
    print(f"   [CHART] 状态图: {chart_url}")

    counts = {cat: sum(1 for r in results if r['label_en'] == cat) for cat in CATEGORY_KEYS}
    alarm_n = sum(1 for r in results if r['alarm'] == 1)
    move_n = sum(1 for r in results if r['moveStatus'] == 1)
    image_urls = {
        'positionMap': position_url,
        'statusChart': chart_url,
    }
    summary = {
        'totalCount': len(results),
        'healthyCount': counts['healthy'],
        'sickCount': counts['sick'],
        'deadCount': counts['dead'],
        'unclearCount': counts['unclear'],
        'alarmNormalCount': alarm_n,
        'alarmAbnormalCount': len(results) - alarm_n,
        'moveStopCount': len(results) - move_n,
        'moveMoveCount': move_n,
    }
    push_thresholds(results)
    callback_to_java(image_urls, summary)
    return image_urls, summary


def main():
    """主函数：一直循环执行 run_once()，每次间隔 PREDICT_INTERVAL 秒。"""
    print("\n[START] 启动定时预测...")
    round_num = 0
    while True:
        round_num += 1
        print(f"\n{'=' * 50}")
        print(f"  第 {round_num} 轮")
        print(f"{'=' * 50}")
        run_once()
        print(f"\n[WAIT] 等待 {PREDICT_INTERVAL} 秒后下一轮...")
        time.sleep(PREDICT_INTERVAL)


# --- 程序入口 ---
# 当你直接运行这个 .py 文件时，从这里开始执行
if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:  # 按 Ctrl+C 可以停止程序
        print("\n 用户终止")
