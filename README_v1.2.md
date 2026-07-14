# NVR AI Platform v1.2 — README

## v1.2 新增功能：reolink_aio API 集成

### 概述

v1.2 集成了 **reolink_aio** — Reolink 官方授权的 Python SDK，实现对 NVR 设备的直接控制与状态查询。

此前平台只能通过 RTSP 拉流和 FTP 文件被动接收数据。现在可以通过 HTTP API 直接：
- 查询设备信息（型号/固件/通道数/序列号）
- 查询各通道 AI 检测状态（人形/车辆/宠物）
- 查询端口状态（RTSP/ONVIF/RTMP）
- 查询硬盘信息

### 安装要求

```bash
# 需要 Python 3.11+
python --version  # 确认 >= 3.11

# 安装依赖
pip install reolink-aio>=0.21.0
```

### 使用方式

1. 启动平台 → 顶部导航栏 → "🧪 测试工具" → "NVR 设备"
2. 输入 NVR 的 IP 地址、用户名、密码
3. 点击 "连接 NVR"
4. 查看设备信息、AI 检测状态

### API 返回示例

```
=== 设备信息 ===
型号: NVR-REOCYP
型号代码: RP-N64
固件版本: v3.6.5.560_26062451
总通道数: 64 (实际接入: 19 个摄像头)
RTSP: 端口 554
ONVIF: 端口 8000
硬盘: 4 块 / 共 7296 GB

=== AI 检测状态 ===
Ch00: 人形=否 车辆=否 宠物=否
Ch01: 人形=否 车辆=否 宠物=否
...
```

### 架构说明

```
Reolink NVR
    │
    ├── RTSP (端口 554)        → OpenCV 拉流 → 实时预览 (保留)
    ├── FTP (端口 21)          → watchdog 监控 → 文件核验 (保留)
    └── HTTP API (端口 443)    → reolink_aio SDK → 设备控制 (新增)
         ├── get_host_data()   设备信息
         ├── get_states()      AI 检测状态
         ├── ai_detected(c, type)  按类型查询
         └── set_ir_lights()   IR 控制
```

### 跨平台兼容性说明

v1.2 已完成 Linux 兼容适配：

- 移除所有 `os.name == 'nt'` 判断，统一使用 `platform.system()`
- `os.startfile()` 替换为跨平台 3 路分发（Windows `startfile` / macOS `open` / Linux `xdg-open`）
- ffmpeg/ffprobe 搜索路径覆盖 Linux `/usr/bin`, `/usr/local/bin`, macOS `/opt/homebrew/bin`
- 所有新增组件使用纯 Python + 跨平台库（PyQt5 + reolink_aio + watchdog）

在 Linux 上运行：
```bash
# 1. 安装系统依赖
sudo apt install ffmpeg python3-pip python3-pyqt5

# 2. 创建虚拟环境 (Python 3.11+)
python3.11 -m venv .venv
source .venv/bin/activate

# 3. 安装 Python 依赖
pip install -r requirements.txt

# 4. 运行
python run.py
```

### 修改文件 (v1.2)

| 文件 | 改动 |
|------|------|
| `core/reolink_device.py` | **新建** — reolink_aio Host 同步封装 |
| `gui/nvr_device_widget.py` | **新建** — NVR 设备信息面板 GUI |
| `gui/main_window.py` | 测试工具页加 "NVR 设备" 子页 |
| `config/settings.py` | 新增 NVR_HOST/USERNAME/PASSWORD |
| `requirements.txt` | 新增 reolink-aio>=0.21.0 |
