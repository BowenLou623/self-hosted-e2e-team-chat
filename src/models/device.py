"""
设备数据模型

定义了设备（客户端实例）的基本信息，包含设备ID、类型、最后在线时间等。
为未来的多设备同步和端到端加密预留接口。
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


class DeviceType(Enum):
    """设备类型枚举"""
    DESKTOP = "desktop"    # 桌面客户端
    MOBILE = "mobile"      # 移动客户端（预留）
    WEB = "web"            # Web客户端（预留）


class TrustStatus(Enum):
    """信任状态枚举"""
    UNKNOWN = "unknown"    # 未验证，未配对
    TRUSTED = "trusted"    # 已信任
    BLOCKED = "blocked"    # 已阻止


@dataclass
class Device:
    """
    设备数据模型

    注意：为第二阶段扩展预留了以下字段：
    - public_key: 设备公钥（未来端到端加密使用）
    - fingerprint: 设备指纹（未来安全验证使用）
    """

    # 核心字段
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""  # 所属用户ID
    device_type: DeviceType = DeviceType.DESKTOP
    name: str = ""  # 设备名称（如"Alice的MacBook"）

    # 状态信息
    last_online: Optional[float] = None  # 最后在线时间
    is_online: bool = False  # 当前是否在线

    # 为端到端加密预留的字段
    public_key: Optional[str] = None  # 设备公钥
    fingerprint: Optional[str] = None  # 设备指纹（用于安全验证）

    # 信任状态
    trust_status: TrustStatus = TrustStatus.UNKNOWN

    # 扩展字段
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化存储"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "device_type": self.device_type.value,
            "name": self.name,
            "last_online": self.last_online,
            "is_online": self.is_online,
            "public_key": self.public_key,
            "fingerprint": self.fingerprint,
            "trust_status": self.trust_status.value,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Device":
        """从字典格式创建设备实例"""
        device = cls(
            id=data.get("id", str(uuid.uuid4())),
            user_id=data.get("user_id", ""),
            device_type=DeviceType(data.get("device_type", "desktop")),
            name=data.get("name", ""),
            last_online=data.get("last_online"),
            is_online=data.get("is_online", False),
            public_key=data.get("public_key"),
            fingerprint=data.get("fingerprint"),
            trust_status=TrustStatus(data.get("trust_status", "unknown")),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
        return device

    def update_online_status(self, is_online: bool) -> None:
        """更新设备在线状态"""
        self.is_online = is_online
        self.updated_at = time.time()
        if is_online:
            self.last_online = time.time()

    def __str__(self) -> str:
        """友好的字符串表示"""
        status = "online" if self.is_online else "offline"
        return f"Device[{self.id[:8]}]: {self.name} ({self.device_type.value}, {status})"
