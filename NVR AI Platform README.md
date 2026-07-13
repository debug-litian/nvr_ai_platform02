# NVR AI Platform

轻量级 NVR 智能分析平台骨架，集成实时流预览、异常检测、AI 目标检测与基于 CLIP+FAISS 的文字检索能力。

功能简介
- 实时 RTSP 拉流与预览（OpenCV）
- 花屏/绿线异常检测（基于 HSV 与霍夫直线检测）
- AI 目标检测（YOLOv8）与误报过滤
- CLIP 特征提取 + FAISS 向量索引，支持文字检索回溯视频帧
- PyQt5 简易 GUI：预览、搜索、告警面板

安装说明
1. 克隆仓库：
```
git clone https://github.com/debug-litian/nvr_ai_platform.git
cd nvr_ai_platform
```
2. 创建并激活虚拟环境（Windows 示例）：
```
python -m venv .venv
.venv\\Scripts\\activate
```
3. 安装依赖：
```
pip install -r requirements.txt
```
注意：`torch`/`faiss-cpu` 在不同平台上可能需要特定版本或二进制，若安装失败请参考对应项目官方安装说明。

运行方法
```
python run.py
```
主程序会启动 PyQt GUI，使用 `config/settings.py` 中的 `RTSP_URL` 配置默认摄像头流地址。

RTSP 地址配置说明
- RTSP 示例格式：`rtsp://admin:password@192.168.1.100:554/Preview_01_main`
- 请将 `config/settings.py` 中的 `RTSP_URL` 修改为真实设备地址，或在运行时程序里扩展为界面输入。

目录结构（简略）
- `config/`：全局配置
- `core/`：拉流、抽帧、特征提取、索引与检索核心
- `detectors/`：花屏/绿线检测、YOLO 检测、误报过滤
- `gui/`：PyQt 界面组件
- `data/`：本地视频、索引与映射等数据

## 开发者说明

### FTP 报警核验模块（v2.0 新增）

本平台支持通过 FTP 接收 Reolink NVR 报警文件（截图/录像），自动进行 AI 核验与功能测试：

- **FTP 目录监控**：使用 watchdog 监听 `D:\FTP_Upload` 目录，自动检测新上传的报警文件
- **AI 核验**：YOLOv8 交叉验证 NVR 报警类型（人形/机动车/宠物/画面变动），判断是否为误报
- **绿线/花屏检测**：自动识别因视频信号干扰导致的画面异常误报
- **NVR 配置校验**：验证 FTP 文件是否符合 NVR 布防计划、报警开关、视频参数等配置
- **双 Tab 报警面板**：报警明细（逐条核验结果表格，颜色标记）+ FTP 功能测试报告（聚合统计仪表盘）
- **测试报告导出**：支持导出 CSV 和 HTML 格式的量化测试报告，覆盖 FTP 连接、文件类型、AI 匹配率、通道覆盖率等维度
- **YOLO 线程安全**：预览画框与 FTP 核验共享 YOLO 模型，通过 `threading.Lock` 保护

### FTP 报警核验模块 — 关键组件
- `core/alarm_types.py`：Reolink 报警类型 → YOLO COCO class ID 映射（单一数据源）
- `core/ftp_filename_parser.py`：解析 Reolink FTP 文件名 `NVR-REOCYP_{通道}_{时间戳}.{jpg|mp4}`
- `core/ftp_monitor.py`：QThread + watchdog 监听 FTP 目录，文件到达后延时解析
- `core/alarm_verifier.py`：核验引擎 — YOLO 检测 + 绿线检测 + 文件属性读取 + 配置合规检查
- `core/verification_worker.py`：QThread 工作线程，队列接收 → 串行核验 → 信号返回
- `core/ftp_test_reporter.py`：聚合测试报告引擎，支持 CSV/HTML 导出
- `config/nvr_profile.json`：NVR 预期配置（通道布防计划、报警开关、视频参数）
- `gui/alert_panel.py`：双 Tab 面板（报警明细表格 + FTP 测试报告仪表盘）

### FTP 报警核验 — 使用方式
1. 在 Reolink NVR 中配置 FTP 上传，报警类型按子目录（human/vehicle/pet/motion）分类
2. 在 FileZilla Server 中创建 FTP 用户，指向本机 `D:\FTP_Upload` 目录
3. 启动本平台，点击 **”开始 FTP 监控”** 按钮
4. NVR 报警文件到达 → 自动核验 → 报警明细（Tab 1）显示核验结果，测试报告（Tab 2）生成聚合统计
5. 点击 **”导出 HTML 报告”** 或 **”导出 CSV 报告”** 保存测试结果

### 最近改动摘要 (v2.0)
- **新增 FTP 报警核验完整模块**：7 个新文件（alarm_types, ftp_filename_parser, ftp_monitor, alarm_verifier, verification_worker, ftp_test_reporter, nvr_profile.json）
- **重写 `gui/alert_panel.py`**：从简单 QListWidget 升级为双 Tab 面板（报警明细 + FTP 测试报告）
- **集成 `gui/main_window.py`**：新增 FTP 监控控制栏、FTPMonitor → VerificationWorker → AlertPanel 信号链路
- **YOLO 线程安全**：`detectors/yolo_detector.py` 增加 `threading.Lock`，FTP 核验与预览共享模型
- **新增 `watchdog` 依赖**：`requirements.txt` 增加 `watchdog==4.0.1`

### 最近改动摘要 (v1.x)
- 新增 `ffmpeg stderr` 日志保存机制，写入 `logs/ffmpeg_<timestamp>.log`。
- 增加 `FFMPEG_PROC` backend，支持独立 ffmpeg 子进程解码。
- 新增可选 ffmpeg 硬件加速配置：`USE_FFMPEG_HWACCEL`、`FFMPEG_HWACCEL`、`FFMPEG_HWACCEL_DEVICE`。
- 增加 GUI overlay：`Backend`、`FPS`、`RecvFPS`、`CPU%`、`Status`。
- 优化帧检测调度：使用处理队列和单独后台线程，避免每帧创建新线程。
- 新增实时抽帧文搜图索引：界面支持”历史索引”和”实时抽帧”两种搜索源。
- 新增视频索引构建按钮：可从本地视频文件抽帧构建 CLIP + FAISS 索引。
- 支持自定义验证播放时长与双击结果跳转回放。
- 修复 `main_window.py` 中缺失的 `import time`。
- 引入 `psutil` 进行 Python 进程 CPU 监控。

### 调试与验证
1. 启动后检查 `logs/` 下是否存在 `ffmpeg_*.log`。
2. 在视频 overlay 中确认 `Backend: FFMPEG_PROC`、`RecvFPS`、`CPU%`。
3. 若出现解码异常，查看 `ffmpeg` stderr 日志是否包含 `Invalid data`、`not found` 或 `reference` 警告。

### 进一步优化建议
- 将解码与检测解耦，避免检测拖慢实时展示。
- 若有 GPU，优先使用 CUDA 提速 YOLO/CLIP。
- 增加队列丢弃策略，避免检测堆积导致画面卡顿。
- 为 ffmpeg 解码添加硬件加速参数（如 `-hwaccel cuda` / `-hwaccel qsv`）。

如需我继续补充“开发环境搭建”、“常见问题排查”或“CI/CD 注意事项”，我可以继续扩展。

## 开发环境搭建
1. 克隆仓库：
```powershell
git clone https://github.com/debug-litian/nvr_ai_platform.git
cd nvr_ai_platform
```
2. 创建并激活虚拟环境（Windows）：
```powershell
python -m venv .venv
.venv\Scripts\activate
```
3. 安装依赖：
```powershell
pip install -r requirements.txt
```
4. 如需 GPU 支持，请根据系统与显卡安装 CUDA 版本的 PyTorch，并确保 `torch` 与 `ultralytics` 正常可用。

## 常见问题排查
- `ffmpeg binary not found in PATH`
  - 请确认系统已安装 ffmpeg，并且其可执行文件在 `PATH` 中。
- `Backend` 不是 `FFMPEG_PROC`
  - 说明当前使用的是 OpenCV 采集后端，可能是 ffmpeg 子进程启动失败。请检查 `logs/ffmpeg_*.log`。
- 界面卡顿或低帧率
  - 当前检测与模型推理可能成为瓶颈，可降低 `SAMPLE_FPS`，或关闭部分检测逻辑。
- `psutil` 导入失败
  - 请确保 `requirements.txt` 中已安装 `psutil`，可运行 `pip install psutil`。
- 搜索结果为空
  - 请确认已正确构建 CLIP index，或加载了有效的图片/视频检索数据。

## CI/CD 注意事项
- 依赖安装：
  - `torch` 和 `faiss-cpu` 在不同平台下可能需要不同版本或二进制包，CI 环境请预先测试对应 Python 版本。
- 运行测试：
  - 如果新增自动化测试，应设计“轻量级帧采集”与“模型加载”测试，避免在 CI 中拉取真实 RTSP 流。
- 资源限制：
  - PyQt GUI 组件不适合常规 CI 终端环境，建议将核心逻辑抽离成可测试模块。
- 日志与输出：
  - CI 运行时可将 `logs/` 输出路径映射到构建工件，便于排查 ffmpeg stderr 日志。

