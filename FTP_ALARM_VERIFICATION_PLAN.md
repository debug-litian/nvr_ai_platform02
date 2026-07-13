# NVR AI Platform — FTP 报警核验与功能测试报告 实施方案

## Context

Reolink NVR 通过 FTP 将报警截图/录像上传到 `D:\FTP_Upload`，需要本平台：
1. **AI 核验**：用 YOLO 交叉验证 NVR 报警是否准确（人形/机动车/宠物/画面变动）
2. **FTP 功能测试**：覆盖 Reolink FTP 设置页所有配置项，量化验证 NVR FTP 功能是否正常工作
3. **测试报告**：汇总展示 NVR FTP 配置测试结果 + AI 核验结果，清晰量化

## 文件命名格式（已确认）

```
NVR-REOCYP_00_20260713000552.jpg    → 通道00, 时间戳 2026-07-13 00:05:52, 类型来自子目录名
NVR-REOCYP_01_20260713000621.mp4    → 通道01, 时间戳 2026-07-13 00:06:21
NVR-REOCYP_14_20260713000616.jpg    → 通道14, 时间戳 2026-07-13 00:06:16
```

解析规则：`{NVR名称}_{通道号}_{YYYYMMDDHHMMSS}.{jpg|mp4}`，报警类型从 FTP 子目录名解析。

---

## Reolink FTP 设置项 — 完整测试覆盖清单

基于 Reolink NVR FTP 设置页面的所有配置项：

### 一、FTP 服务器连接

| 测试项 | 验证方式 | 量化指标 |
|--------|---------|---------|
| 服务器 IP/端口 | 文件成功到达目标目录 | 连接成功率 = 收到文件数/预期文件数 |
| 用户名/密码 | 同上 | ✅/❌ |
| 传输模式 (Auto/PORT/PASV) | 文件完整性校验 | 文件损坏数/总数 |
| FTPS 加密 | 检查 FileZilla 日志 TLS 握手 | ✅/❌ |
| 匿名登录 | 检查 FTP 日志用户名 | ✅/❌ |

### 二、文件类型与上传

| 测试项 | 验证方式 | 量化指标 |
|--------|---------|---------|
| 图片上传 (Picture) | 统计 .jpg 文件数 | 收到 JPG 数 |
| 视频上传 (Video) | 统计 .mp4 文件数 | 收到 MP4 数 |
| 图片+视频 (Both) | 同时有 .jpg + .mp4 | 按时间戳配对计数 |
| 图片分辨率 | 读取 JPG EXIF/尺寸 | 实际分辨率 vs 预期 |
| 视频分辨率 | 读取 MP4 cv2 属性 | 实际宽高 vs 预期 |
| 视频时长 | MP4 总帧数/fps | 实际时长 vs 预期录像长度 |
| 视频编码 | 检查 MP4 fourcc/codec | H.264/H.265 检测 |
| 文件大小合理性 | 文件大小范围统计 | 最小/最大/平均 MB |
| FTP Postpone 延迟 | 报警触发时间 vs 文件到达时间 | 实际延迟秒数 |

### 三、布防计划 (Schedule)

| 测试项 | 验证方式 | 量化指标 |
|--------|---------|---------|
| 定时上传 (Timer) | 按小时统计文件分布 | 每小时文件数柱状图 |
| 移动侦测 (Motion) | 文件时间戳 vs 布防时间段 | 布防外文件数/总数 |
| AI 检测类型 (人/车/动物) | YOLO 交叉验证 | AI 匹配率 |
| 画面变动 (Any Motion) | 绿线检测 + YOLO 空检测 | 误报率 |
| 周日~周六全覆盖 | 按星期统计文件分布 | 每天文件数 |
| 24 小时覆盖 | 按小时统计 | 每小时至少 1 个文件 |

### 四、通道覆盖

| 测试项 | 验证方式 | 量化指标 |
|--------|---------|---------|
| 通道覆盖率 | 统计所有出现过的通道号 | 已覆盖通道/总通道 |
| 通道级报警类型 | 每个通道收到的报警类型 | 通道×类型矩阵 |

---

## 新增文件清单

### 1. `core/alarm_types.py` — 报警类型映射（新建）

```python
# 报警类型 → FTP 子目录 → COCO class IDs
ALARM_TYPE_MAP = {
    "human":   {"dir": "human",   "coco_ids": [0],           "label_cn": "人形"},
    "vehicle": {"dir": "vehicle", "coco_ids": [2, 3, 5, 7],  "label_cn": "机动车"},
    "pet":     {"dir": "pet",     "coco_ids": [15, 16],      "label_cn": "宠物"},
    "motion":  {"dir": "motion",  "coco_ids": [],            "label_cn": "画面变动"},
}

COCO_CN_NAMES = {
    0: "人", 1: "自行车", 2: "汽车", 3: "摩托车",
    5: "公交车", 7: "卡车", 15: "猫", 16: "狗",
}
```

### 2. `core/ftp_filename_parser.py` — 文件名解析器（新建）

解析 `NVR-REOCYP_01_20260713000614.jpg` 格式，从子目录名推断报警类型。

### 3. `core/ftp_monitor.py` — FTP 目录监控线程（新建）

- QThread + watchdog 监听 `D:\FTP_Upload` 及其子目录
- 新文件到达等 2 秒（等写完毕），解析后发出 `file_detected` 信号
- 信号：`file_detected(dict)`, `monitor_error(str)`, `monitor_status(str)`

### 4. `core/alarm_verifier.py` — 报警核验引擎（新建）

核心类，**非 QThread**，纯同步逻辑：

```
AlarmVerifier.verify(record) → VerificationResult:
  1. JPG → 加载图片 → YOLO 检测一次
  2. MP4 → 采样帧（1fps）→ YOLO 逐帧检测
  3. 对比检测结果 vs 期望 COCO class
  4. 检测绿线/花屏（画面变动类）
  5. 文件属性校验（分辨率、时长、编码）
  6. 配置合规检查（布防时间、报警开关）
```

### 5. `core/verification_worker.py` — 核验工作线程（新建）

- QThread，内部持有 AlarmVerifier + 共享 YoloDetector
- 队列接收 → 串行处理 → 信号返回结果

### 6. `core/ftp_test_reporter.py` — FTP 功能测试报告引擎（新建）

**这是新增的核心模块**，生成量化测试报告：

```python
@dataclass
class FTPTestReport:
    """FTP 功能测试报告"""
    # 基础统计
    total_files: int
    jpg_count: int
    mp4_count: int
    channel_count: int           # 覆盖通道数
    total_channels: int          # NVR 总通道数
    time_range: tuple            # (最早, 最晚) 文件时间

    # FTP 连接测试
    ftp_connection_ok: bool      # 是否收到过文件
    ftp_transfer_success_rate: float  # 传输成功率

    # 文件类型测试
    has_picture: bool            # 收到 JPG
    has_video: bool              # 收到 MP4

    # 图像/视频参数
    jpg_resolutions: dict        # {通道: [(宽,高),...]}  实际分辨率
    mp4_resolutions: dict        # {通道: [(宽,高),...]}
    mp4_durations: list[float]   # 所有视频时长

    # AI 核验汇总
    total_alarms: int            # 总报警数
    false_alarm_count: int       # 误报数
    false_alarm_rate: float      # 误报率 %
    match_count: int             # AI 匹配数
    match_rate: float            # AI 匹配率 %
    alarm_type_stats: dict       # {类型: {total, match, false, rate%}}

    # 绿线/花屏检测
    green_line_count: int        # 绿线问题文件数

    # 布防计划校验
    schedule_violations: int     # 布防外报警数
    schedule_coverage: dict      # {星期几: 文件数}
    hourly_coverage: dict        # {小时: 文件数}

    # 通道覆盖
    channel_coverage: dict       # {通道: {类型: 文件数}}
    channel_coverage_rate: float # 通道覆盖率 %

    # 总体评分
    overall_score: float         # 0-100 综合评分
```

报告类提供方法：
- `from_records(records: list) → FTPTestReport` — 从核验结果列表聚合生成
- `to_dict() → dict` — 转字典供 UI 展示
- `export_csv(path)` — 导出 CSV
- `export_html(path)` — 导出 HTML 测试报告

### 7. `config/nvr_profile.json` — NVR 预期配置（新建）

包含每个通道的：布防计划（星期+时间段）、报警类型开关、预期分辨率/时长

---

## 修改文件清单

### 8. `gui/alert_panel.py` — 完全重写为测试报告面板

分为两个 Tab 页：

**Tab 1 — "报警明细"**（每条 FTP 文件的逐行核验结果）：

| 时间 | 通道 | 文件名 | 文件类型 | NVR类型 | AI检测结果 | 置信度 | 误报 | 绿线 | 文件参数 |
|------|------|--------|---------|---------|-----------|--------|------|------|---------|
| 00:05:52 | 01 | xxx.jpg | JPG | human | person | 0.92 | ✅ | — | 4608×1728 |
| 00:06:21 | 01 | xxx.mp4 | MP4 | human | person(0.89), car(0.45) | 0.89 | ✅ | — | 2560×1440, 8s |
| 00:06:14 | 09 | xxx.jpg | JPG | motion | (无目标) | — | ⚠️ | ❌绿线 | 4512×2512 |

行颜色：🔴红色=误报, 🟡黄色=绿线/花屏, ⚪灰色=画面变动(无YOLO), 🟢绿色=正常

每行可展开查看该帧的 YOLO 检测框预览图。

**Tab 2 — "FTP 功能测试报告"**（聚合统计仪表盘）：

```
┌─────────────────────────────────────────────────────┐
│ 📊 NVR FTP 功能测试报告          总分: 87/100       │
├─────────────────────────────────────────────────────┤
│                                                     │
│  📁 文件统计                                        │
│  ├─ 总文件数: 32    JPG: 20    MP4: 12             │
│  ├─ 覆盖通道: 16/16 (100%)                         │
│  ├─ 时间范围: 2026-07-13 00:05 ~ 00:06             │
│  └─ 文件大小: 最小 0.1MB / 最大 40MB / 平均 8.2MB │
│                                                     │
│  🔌 FTP 连接测试                                    │
│  ├─ 连接状态: ✅ 正常                               │
│  ├─ 传输成功率: 100% (32/32)                       │
│  └─ 传输模式: PASV (被动模式)                       │
│                                                     │
│  🖼️ 图片上传测试                                    │
│  ├─ 图片上传: ✅ 正常 (20张)                        │
│  ├─ 分辨率分布: 2560×1920(8), 4608×1728(5), ...    │
│  └─ 上传间隔: 平均 3.2s (预期 ≥2s) ✅              │
│                                                     │
│  🎬 视频上传测试                                    │
│  ├─ 视频上传: ✅ 正常 (12段)                        │
│  ├─ 分辨率: 2560×1440(8), 3840×2160(4)             │
│  ├─ 平均时长: 8.5s (预期 30s) ⚠️                   │
│  └─ 编码格式: H.264 ✅                              │
│                                                     │
│  🤖 AI 核验汇总                                     │
│  ├─ 总报警: 32   误报: 5   误报率: 15.6%           │
│  ├─ 人形: 10次, 匹配 9次, 匹配率 90.0% ✅          │
│  ├─ 机动车: 8次, 匹配 7次, 匹配率 87.5% ✅         │
│  ├─ 宠物: 4次, 匹配 2次, 匹配率 50.0% ⚠️          │
│  ├─ 画面变动: 10次, 绿线问题 3次 (30%)             │
│  └─ AI 综合匹配率: 81.3%                            │
│                                                     │
│  📅 布防计划校验                                     │
│  ├─ 布防外报警: 2 次 ❌                             │
│  ├─ 周日~周六: [3,5,4,6,5,4,5] (文件数)           │
│  └─ 24h覆盖: 00:00-01:00 ✅, 01:00-02:00 ✅, ...  │
│                                                     │
│  📡 通道覆盖                                        │
│  ├─ 通道覆盖率: 100% (16/16)                        │
│  └─ [通道×类型矩阵热力图]                           │
│                                                     │
│  [📥 导出 CSV] [📄 导出 HTML 报告]                  │
└─────────────────────────────────────────────────────┘
```

### 9. `gui/main_window.py` — 集成修改

- FTP 监控控制栏（启动/停止 + 目录输入 + 状态灯）
- `alerts_list` → `alert_panel`（AlertPanel）
- 新增 Report 标签页到主窗口
- 布线：FTPMonitor → MainWindow → VerificationWorker → AlertPanel + Report
- 线程生命周期管理

### 10. `config/settings.py` — 新增配置

```python
FTP_UPLOAD_DIR = r"D:\FTP_Upload"
FTP_SUBDIRS = ["human", "vehicle", "pet", "motion"]
FTP_PROCESSING_DELAY_SEC = 2.0
ALARM_VIDEO_SAMPLE_FPS = 1.0
NVR_PROFILE_PATH = ROOT / "config" / "nvr_profile.json"
NVR_TOTAL_CHANNELS = 16
REPORT_EXPORT_DIR = ROOT / "reports"
```

### 11. `requirements.txt` — 加 `watchdog`

---

## 数据流

```
Reolink NVR
    │ FTP上传
    ▼
D:\FTP_Upload\{human,vehicle,pet,motion}\
    │
    │ watchdog 监听
    ▼
FTPMonitor (QThread) ──file_detected──→ MainWindow
                                           │
                              ┌────────────┼────────────┐
                              ▼            ▼            ▼
                        AlertPanel   VerificationWorker  FTPTestReporter
                        "处理中..."   (QThread)           (聚合统计)
                              │            │                │
                              │     AlarmVerifier.verify()  │
                              │       ├─ 文件名解析         │
                              │       ├─ YOLO 检测          │
                              │       ├─ 绿线检测           │
                              │       ├─ 文件属性读取       │
                              │       ├─ 类型匹配判断       │
                              │       └─ 配置合规检查       │
                              │            │                │
                              ▼   verification_complete     │
                        AlertPanel.update()                 │
                        (更新行 + 报告刷新) ←───────────────┘
```

---

## 实现阶段

| 阶段 | 内容 | 预计工作量 |
|------|------|-----------|
| **1** | `alarm_types.py` + `ftp_filename_parser.py` + `nvr_profile.json` + `settings.py` 扩展 | 小 |
| **2** | `ftp_monitor.py` + `watchdog` 依赖 | 中 |
| **3** | `alarm_verifier.py` + `verification_worker.py` | 中 |
| **4** | `ftp_test_reporter.py` — 测试报告引擎 | 中 |
| **5** | `alert_panel.py` 重写（报警明细 Tab + 测试报告 Tab） | 大 |
| **6** | `main_window.py` 集成全部 + 线程安全 | 中 |

阶段 2、3、4 可并行执行。

## 验证方式

1. 启动 FTP 监控 → 往 `D:\FTP_Upload\human\` 放有人物的 JPG → 报警明细出现绿色行，AI 匹配=✅
2. 往 `human\` 放纯风景 JPG → 报警明细出现红色行，误报=❌，测试报告误报率上升
3. 往 `motion\` 放绿线花屏 JPG → 黄色行，绿线=❌
4. 切到测试报告 Tab → 看到聚合统计数据、AI 匹配率、误报率、文件参数分布
5. 导出 HTML/CSV 测试报告 → 在浏览器打开确认格式
6. RTSP 预览画框不受影响
