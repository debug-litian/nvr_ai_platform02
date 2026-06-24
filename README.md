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

如需我为 `README` 加入运行截图、示例配置或 CI/CD 发布说明，请告诉我。
