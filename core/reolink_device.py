"""
reolink_device.py — Reolink NVR 设备 API 封装

基于 reolink_aio (官方授权 Python SDK) 的同步封装层。
reolink_aio 是异步库，本模块用 asyncio.run() 包装为同步接口，
供 PyQt5 主线程/后台线程直接调用。

支持的 API:
- get_host_data()    设备型号/固件/通道数/MAC
- get_states()       AI 检测状态/IR/灯等
- ai_detected(ch)    通道 AI 检测状态
- set_ir_lights()    IR 灯开关
- reboot()           设备重启
- check_new_firmware()  固件更新检查

依赖: pip install reolink-aio (需 Python 3.11+)
"""

import asyncio
import threading
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger("reolink_device")


# ═══════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════

@dataclass
class ReolinkDeviceInfo:
    """NVR 设备信息快照"""
    connected: bool = False
    model_name: str = ""              # NVR-REOCYP
    model_number: str = ""            # RP-N64
    is_nvr: bool = False
    num_channels: int = 0
    num_cameras: int = 0              # 实际接入的摄像头数
    firmware_version: str = ""
    hardware_version: str = ""
    mac_address: str = ""
    manufacturer: str = "Reolink"
    uid: str = ""
    serial: str = ""

    # 端口
    rtsp_enabled: bool = False
    rtsp_port: int = 554
    onvif_enabled: bool = False
    onvif_port: int = 8000
    rtmp_enabled: bool = False
    rtmp_port: int = 1935
    https_port: int = 443

    # 存储
    hdd_count: int = 0
    hdd_list: List[int] = field(default_factory=list)

    # 用户
    user_level: str = ""
    is_admin: bool = False

    # HDD 容量 (GB)
    hdd_total_gb: float = 0.0

    # 固件更新
    firmware_update_available: bool = False

    # 通道列表
    channels: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "model_name": self.model_name,
            "model_number": self.model_number,
            "is_nvr": self.is_nvr,
            "num_channels": self.num_channels,
            "num_cameras": self.num_cameras,
            "firmware_version": self.firmware_version,
            "hardware_version": self.hardware_version,
            "mac_address": self.mac_address,
            "manufacturer": self.manufacturer,
            "hdd_count": self.hdd_count,
            "rtsp_enabled": self.rtsp_enabled,
            "rtsp_port": self.rtsp_port,
            "onvif_enabled": self.onvif_enabled,
            "onvif_port": self.onvif_port,
            "rtmp_enabled": self.rtmp_enabled,
            "rtmp_port": self.rtmp_port,
            "is_admin": self.is_admin,
            "user_level": self.user_level,
            "firmware_update_available": self.firmware_update_available,
            "hdd_total_gb": round(self.hdd_total_gb, 1),
            "channels": self.channels,
            "channel_count": len(self.channels),
        }


@dataclass
class ReolinkAIState:
    """单通道 AI 检测状态"""
    channel: int
    ai_enabled: bool = False
    person_detected: bool = False
    vehicle_detected: bool = False
    pet_detected: bool = False
    motion_detected: bool = False


# ═══════════════════════════════════════════════════════════
# 设备封装
# ═══════════════════════════════════════════════════════════

class ReolinkDevice:
    """
    Reolink NVR 设备控制封装 (同步接口)。

    用法:
        device = ReolinkDevice("192.168.124.2", "admin", "password")
        info = device.connect()          # → ReolinkDeviceInfo
        states = device.get_ai_states()   # → List[ReolinkAIState]
        device.disconnect()

    reolink_aio 连接测试已验证:
        - Model: NVR-REOCYP (RP-N64)
        - Channels: 64, Cameras: 19
        - Firmware: v3.6.5.560_26062451
        - RTSP: 554, ONVIF: 8000, RTMP: 1935
    """

    _HAS_REOLINK_AIO: bool = False

    def __init__(
        self,
        host: str = "192.168.124.2",
        username: str = "admin",
        password: str = "",
    ):
        self._host = host
        self._username = username
        self._password = password
        self._api_host = None           # reolink_aio Host 实例
        self._connected = False
        self._lock = threading.Lock()   # 保护 _api_host 访问

    # ── 连接管理 ──────────────────────────────────────

    def connect(self) -> ReolinkDeviceInfo:
        """连接 NVR 并获取设备信息。线程安全。"""
        if not self._check_module():
            return ReolinkDeviceInfo()

        try:
            import threading
            result = {}

            def _connect_in_thread():
                try:
                    import asyncio as _asyncio
                    result['info'] = _asyncio.run(self._connect_async())
                except Exception as e:
                    result['error'] = e

            t = threading.Thread(target=_connect_in_thread, daemon=True)
            t.start()
            t.join(timeout=60)

            if 'error' in result:
                raise result['error']
            return result.get('info', ReolinkDeviceInfo())
        except Exception as e:
            logger.exception("NVR 连接失败: %s", e)
            return ReolinkDeviceInfo(connected=False)

    def disconnect(self):
        """断开连接"""
        if self._api_host is not None:
            try:
                self._run_async(self._api_host.logout())
            except Exception:
                pass
            self._api_host = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ── 设备查询 ──────────────────────────────────────

    def get_device_info(self) -> ReolinkDeviceInfo:
        """刷新设备信息（需先 connect）"""
        if not self._connected:
            return ReolinkDeviceInfo()

        try:
            return self._extract_device_info(self._api_host)
        except Exception:
            return ReolinkDeviceInfo(connected=self._connected)

    def get_ai_states(self, channels: Optional[List[int]] = None) -> List[ReolinkAIState]:
        """查询各通道 AI 检测状态"""
        if not self._connected:
            return []

        try:
            if channels is None:
                channels = list(getattr(self._api_host, 'channels', []))
                if not channels:
                    channels = list(range(self._api_host.num_channels))

            results = []
            for ch in list(channels)[:32]:
                # reolink_aio v0.21.x: ai_detected(channel, object_type)
                person = self._api_host.ai_detected(ch, "people")
                vehicle = self._api_host.ai_detected(ch, "vehicle")
                pet = self._api_host.ai_detected(ch, "animal")
                results.append(ReolinkAIState(
                    channel=ch,
                    ai_enabled=person or vehicle or pet,
                    person_detected=person,
                    vehicle_detected=vehicle,
                    pet_detected=pet,
                    motion_detected=False,
                ))
            return results
        except Exception as e:
            logger.exception("AI 状态查询失败: %s", e)
            return []

    def ai_detected(self, channel: int = 0) -> bool:
        """查询指定通道是否有 AI 检测结果"""
        if not self._connected:
            return False
        try:
            return self._api_host.ai_detected(channel)
        except Exception:
            return False

    # ── 设备控制 ──────────────────────────────────────

    def set_ir_lights(self, channel: int, enabled: bool) -> bool:
        """开关红外灯"""
        if not self._connected:
            return False
        try:
            self._run_async(self._api_host.set_ir_lights(channel, bool(enabled)))
            return True
        except Exception:
            return False

    def reboot(self) -> bool:
        """重启 NVR"""
        if not self._connected:
            return False
        try:
            self._run_async(self._api_host.reboot())
            return True
        except Exception:
            return False

    def check_firmware_update(self) -> bool:
        """检查是否有固件更新"""
        if not self._connected:
            return False
        try:
            self._run_async(self._api_host.check_new_firmware())
            return self._api_host.sw_version_update_required
        except Exception:
            return False

    # ── 内部方法 ──────────────────────────────────────

    @staticmethod
    def _check_module() -> bool:
        """检查 reolink_aio 是否可用"""
        if ReolinkDevice._HAS_REOLINK_AIO:
            return True
        try:
            from reolink_aio.api import Host  # noqa: F811
            ReolinkDevice._HAS_REOLINK_AIO = True
            return True
        except ImportError:
            logger.warning(
                "reolink-aio 未安装。NVR API 不可用。"
                "请执行: pip install reolink-aio (需 Python 3.11+)"
            )
            return False

    @staticmethod
    def _run_async(coro):
        """在任意线程中安全运行 async 协程（始终使用新线程+新loop）"""
        import concurrent.futures
        # 始终在新线程中创建全新的 event loop，避免 "Event loop is closed" 问题
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=60)

    async def _connect_async(self):
        """异步连接"""
        from reolink_aio.api import Host

        self._api_host = Host(self._host, self._username, self._password)
        await self._api_host.get_host_data()
        await self._api_host.get_states()
        self._connected = self._api_host.session_active
        logger.info("NVR 连接成功: %s (%s)", self._api_host.nvr_name, self._api_host.model)
        return self._extract_device_info(self._api_host)

    async def _get_ai_states_async(self, channels=None):
        """异步查询 AI 状态"""
        await self._api_host.get_states()

        if channels is None:
            channels = list(self._api_host.channels) if hasattr(self._api_host, 'channels') else list(range(self._api_host.num_channels))

        results = []
        for ch in list(channels)[:32]:  # 最多 32 路，避免过长
            ai = self._api_host.ai_detected(ch)
            # reolink_aio 的 ai_detected 返回 True/False，不区分类型
            results.append(ReolinkAIState(
                channel=ch,
                ai_enabled=bool(ai),
                person_detected=bool(ai),       # 简化：AI 检测到则各类型均标记
                vehicle_detected=bool(ai),
                pet_detected=bool(ai),
                motion_detected=False,           # motion 需单独 API
            ))

        return results

    def _extract_device_info(self, api_host) -> ReolinkDeviceInfo:
        """从 reolink_aio Host 提取设备信息"""
        info = ReolinkDeviceInfo(connected=True)

        info.model_name = getattr(api_host, 'nvr_name', '')
        info.model_number = getattr(api_host, 'model', '')
        info.is_nvr = getattr(api_host, 'is_nvr', False)
        info.num_channels = getattr(api_host, 'num_channels', 0)
        info.num_cameras = getattr(api_host, 'num_cameras', 0)
        info.firmware_version = getattr(api_host, 'sw_version', '')
        info.hardware_version = getattr(api_host, 'hardware_version', '')
        info.mac_address = getattr(api_host, 'mac_address', '')
        info.user_level = getattr(api_host, 'user_level', '')
        info.is_admin = getattr(api_host, 'is_admin', False)
        info.uid = str(getattr(api_host, 'uid', ''))

        info.rtsp_enabled = getattr(api_host, 'rtsp_enabled', False)
        info.rtsp_port = getattr(api_host, 'rtsp_port', 554)
        info.onvif_enabled = getattr(api_host, 'onvif_enabled', False)
        info.onvif_port = getattr(api_host, 'onvif_port', 8000)
        info.rtmp_enabled = getattr(api_host, 'rtmp_enabled', False)
        info.rtmp_port = getattr(api_host, 'rtmp_port', 1935)
        info.https_port = getattr(api_host, 'port', 443)

        # HDD 信息
        hdd_list = getattr(api_host, 'hdd_list', [])
        info.hdd_count = len(hdd_list)
        info.hdd_list = list(hdd_list)

        # HDD 总容量
        try:
            hdd_info = getattr(api_host, 'hdd_info', None)
            if hdd_info is None:
                hdd_info = []
            total_mb = sum(h.get('capacity', 0) for h in hdd_info)
            info.hdd_total_gb = total_mb / 1024.0  # 容量单位是 MB
        except Exception:
            info.hdd_total_gb = 0.0

        # 固件更新
        info.firmware_update_available = getattr(api_host, 'sw_version_update_required', False)

        # 通道列表
        info.channels = list(getattr(api_host, 'channels', []))

        # 串号
        try:
            serial_method = getattr(api_host, 'serial', None)
            if callable(serial_method):
                info.serial = str(serial_method())
            else:
                info.serial = str(serial_method or '')
        except Exception:
            info.serial = ''

        logger.info("设备信息: %s %s [%d通道/%d摄像头] FW=%s",
                    info.model_name, info.model_number,
                    info.num_channels, info.num_cameras,
                    info.firmware_version)

        return info
