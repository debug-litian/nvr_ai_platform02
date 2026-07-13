# NVR AI Platform Developer Guide

## 1. 项目目标

本项目实现一个基于 RTSP 的 NVR 智能分析平台，目标包括：
- 实时视频预览
- 绿线 / 花屏异常检测
- YOLOv8 目标检测
- CLIP + FAISS 文字/图像检索
- PyQt5 GUI 前端
- ffmpeg 解码与回放支持

## 2. 代码架构

### 2.1 关键目录
- `config/`
  - `settings.py`：全局参数、阈值、路径、开关。
- `core/`
  - `stream_capture.py`：RTSP 拉流、OpenCV/ffmpeg 解码、重连、坏帧过滤。
  - `frame_extractor.py`：从采集源抽取帧并回调。
  - `feature_extractor.py`：CLIP 特征提取。
  - `index_builder.py`：FAISS 索引构建。
  - `searcher.py`：CLIP 文本/图像检索。
- `detectors/`
  - `green_line_detector.py`：绿线与花屏检测。
  - `yolo_detector.py`：YOLOv8 检测。
  - `false_positive_filter.py`：误报过滤。
- `gui/`
  - `main_window.py`：主窗口、控件、定时与结果展示。
  - `video_widget.py`：视频显示与 overlay。
- `utils/`
  - `video_player.py`：ffplay 播放器启动与系统回退。
  - `logger.py`：日志辅助。

## 3. 本次改动总结

### 3.1 关键功能与变更点
- `core/stream_capture.py`
  - 新增 `FFMPEG_PROC` 模式：使用独立 ffmpeg 子进程解码 RTSP 视频流。
  - 将 ffmpeg stderr 输出写入 `logs/ffmpeg_<timestamp>.log`，便于离线排查。
  - 记录 ffmpeg 进程 PID，并对进程启动失败做异常处理。
  - 新增可选 ffmpeg 硬件加速配置：`USE_FFMPEG_HWACCEL`、`FFMPEG_HWACCEL`、`FFMPEG_HWACCEL_DEVICE`。
  - 保留 OpenCV capture fallback 逻辑，支持 `CAP_FFMPEG` 与 `CAP_ANY`。
  - 坏帧检测与重连机制：通过灰度标准差过滤低质量帧。

- `core/realtime_indexer.py`
  - 新增实时抽帧索引模块：对直播流采样帧并构建 CLIP 向量向量缓存。
  - 支持实时文搜图搜索并返回前 Top K 相似条目。

- `core/index_builder.py`
  - 新增 `build_from_video` 功能：从本地视频文件抽帧构建 FAISS 索引与映射表。

- `gui/video_widget.py`
  - 增加 overlay 信息显示：`Backend`、`FPS`、`RecvFPS`、`CPU%`、`Status`。
  - 让调试时能够直观看到当前解码后端与性能状态。

- `gui/main_window.py`
  - 新增搜索源选择：`历史索引` / `实时抽帧`。
  - 新增视频索引构建按钮：支持从本地视频文件抽帧构建索引。
  - 新增播放时长自定义输入和搜索结果双击验证回放。
  - 修复缺失的 `import time`。
  - 条件导入 `psutil`，用于采样当前 Python 进程 CPU 使用率。
  - 实现 `RecvFPS` 统计：表示从采集线程实际到达 UI 的帧率。
  - 使用处理队列与单线程后台处理，避免每帧创建线程。
  - 在 overlay 上显示 CPU 与接收帧率。

- `config/settings.py`
  - 添加 `LOG_DIR`，用于保存 ffmpeg stderr 日志文件。
  - 新增实时索引缓存与视频索引抽帧配置。

- `requirements.txt`
  - 增加 `psutil==5.9.5`，用于运行时 CPU 监控。

## 4. 重要关键词
`ffmpeg stderr log`、`FFMPEG_PROC`、`USE_FFMPEG_DECODE`、`LOG_DIR`、`psutil`、`CPU% overlay`、`RecvFPS`、`import time`、`main_window.py`、`core/stream_capture.py`、`gui/video_widget.py`

## 5. 调试与验证方法

### 5.1 验证 ffmpeg 子进程
- 运行程序后，检查 `logs/` 是否生成 `ffmpeg_*.log`。
- 通过日志确认 `ffmpeg` 是否只有 stderr 输出，是否存在 `not found` 或 `Invalid data` 等错误。

### 5.2 验证 UI 状态信息
- 在视频界面 overlay 中确认：
  - `Backend: FFMPEG_PROC`
  - `FPS`：当前摄像头 / 解码帧率
  - `RecvFPS`：实际到达框架的帧率
  - `CPU`：当前 Python 进程 CPU 使用率
  - `Status`：是否已连接

### 5.3 验证依赖安装
- 安装依赖命令：
```powershell
pip install -r requirements.txt
```

## 6. 进一步优化建议
- 将解码与检测解耦：保持画面显示优先，降低检测频率。
- 若可用 GPU，优先使用 `cuda` 执行 YOLO / CLIP 推理。- 已实现处理队列与单线程后台处理，避免每帧创建线程。- 引入队列丢弃策略：当检测队列积压时丢弃旧帧，减少延迟。
- 为 ffmpeg 解码添加硬件加速参数（如 `-hwaccel cuda`、`-hwaccel qsv`、`-hwaccel d3d11va`）。
- 加入更完整的运行日志：`frame_received`、`frame_dropped`、`reconnect` 等事件。

## 7. 代码提交与仓库状态
- 执行了本次变更并推送到 GitHub，因为 `main_window.py` 已修复 `import time`，并且逻辑调整已提交。

## 8. 推荐的开发流程
1. 修改 `config/settings.py` RTSP 地址与解码开关。
2. 运行 `python run.py` 启动 GUI。
3. 观察 overlay 与 `logs/ffmpeg_*.log`。
4. 根据问题再进一步调优 `stream_capture.py` 或检测模块。

---

