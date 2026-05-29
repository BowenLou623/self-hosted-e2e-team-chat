"""
用户数据模型

定义了用户的基本信息，包含用户ID、名称、状态、设备等信息。
为未来的扩展预留了加密密钥相关字段。
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional


class UserStatus(Enum):
    """用户在线状态枚举"""
    OFFLINE = "offline"    # 离线
    ONLINE = "online"      # 在线
    AWAY = "away"          # 离开
    BUSY = "busy"          # 忙碌
    INVISIBLE = "invisible" # 隐身（预留）


@dataclass
class User:
    """
    用户数据模型

    注意：为第二阶段扩展预留了以下字段：
    - public_key: 用户公钥（未来端到端加密使用）
    - device_keys: 设备密钥映射
    
    重要：user_id是系统主身份，首次生成后永久不变，用于登录、消息路由、联系人关系、信任判断、加密身份。
    display_name是用户显示名，登录后可设置，后续可修改，仅用于UI显示。
    如果 display_name 为空，UI 应直接回退到完整 user_id。
    """

    # 核心字段
    user_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    display_name: str = ""  # 显示名称（可修改）
    original_username: str = ""  # 兼容性字段，保留旧的username（未来可移除）

    # 状态信息
    status: UserStatus = UserStatus.OFFLINE
    last_seen: Optional[float] = None  # 最后在线时间
    avatar_url: Optional[str] = None  # 头像URL

    # 为端到端加密预留的字段
    public_key: Optional[str] = None  # 用户公钥（未来用于加密）
    device_keys: Dict[str, str] = field(default_factory=dict)  # 设备ID -> 设备公钥

    # 设备列表（预留，未来支持多设备）
    device_ids: List[str] = field(default_factory=list)

    # 扩展字段
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化存储"""
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "original_username": self.original_username,  # 兼容性字段
            "status": self.status.value,
            "last_seen": self.last_seen,
            "avatar_url": self.avatar_url,
            "public_key": self.public_key,
            "device_keys": self.device_keys,
            "device_ids": self.device_ids,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "User":
        """从字典格式创建用户实例"""
        # 兼容性处理：旧数据可能包含id和username字段
        user_id = data.get("user_id") or data.get("id", str(uuid.uuid4()))
        display_name = data.get("display_name", "")
        original_username = data.get("original_username") or data.get("username", "")
        
        user = cls(
            user_id=user_id,
            display_name=display_name,
            original_username=original_username,
            status=UserStatus(data.get("status", "offline")),
            last_seen=data.get("last_seen"),
            avatar_url=data.get("avatar_url"),
            public_key=data.get("public_key"),
            device_keys=data.get("device_keys", {}),
            device_ids=data.get("device_ids", []),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
        return user

    def update_status(self, status: UserStatus) -> None:
        """更新用户状态"""
        self.status = status
        self.updated_at = time.time()
        if status == UserStatus.ONLINE:
            self.last_seen = time.time()

    def get_display_name(self) -> str:
        """获取显示名称，优先使用 display_name，否则回退到完整 user_id。"""
        normalized_display_name = (self.display_name or "").strip()
        if normalized_display_name:
            return normalized_display_name
        return self.user_id if self.user_id else "未知用户"

    @property
    def id(self) -> str:
        """兼容性属性：返回user_id（旧代码可能使用.id访问）"""
        return self.user_id
    
    @property
    def username(self) -> str:
        """兼容性属性：返回display_name（旧代码可能使用.username访问）"""
        return self.display_name

    def __str__(self) -> str:
        """友好的字符串表示"""
        return f"User[{self.user_id[:8]}]: {self.get_display_name()} ({self.status.value})"
