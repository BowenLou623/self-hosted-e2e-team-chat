"""
联系人数据模型

定义了联系人的数据结构和授权状态。
联系人代表其他用户，与当前用户有特定的授权关系。
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, Union

from .user import User, UserStatus


class ContactAuthStatus(Enum):
    """联系人授权状态枚举"""
    UNKNOWN = "unknown"                 # 已知联系人，但尚未进入授权流程
    PENDING_INCOMING = "pending_incoming"  # 收到对方消息，待我授权
    TRUSTED = "trusted"                 # 已授权，可正常聊天
    REJECTED = "rejected"               # 已拒绝该联系人


_LEGACY_CONTACT_AUTH_STATUS_MAP = {
    "pending_outgoing": ContactAuthStatus.UNKNOWN,
    "blocked": ContactAuthStatus.REJECTED,
}


def normalize_contact_auth_status(
    value: Union["ContactAuthStatus", str, None]
) -> ContactAuthStatus:
    """将历史状态和值兜底收敛到 M2-C 最小状态机。"""
    if isinstance(value, ContactAuthStatus):
        return value

    normalized_value = str(value or ContactAuthStatus.UNKNOWN.value).strip().lower()
    normalized_value = _LEGACY_CONTACT_AUTH_STATUS_MAP.get(normalized_value, normalized_value)

    try:
        return ContactAuthStatus(normalized_value)
    except ValueError:
        return ContactAuthStatus.UNKNOWN


@dataclass
class Contact(User):
    """
    联系人数据模型，扩展User类
    
    添加了授权状态和联系人特定字段。
    """
    
    # 授权状态
    auth_status: ContactAuthStatus = ContactAuthStatus.UNKNOWN
    
    # 联系人特定字段
    alias: str = ""  # 兼容旧字段，M2-B 起不再作为主显示来源
    added_at: float = field(default_factory=time.time)  # 添加时间
    last_interaction: Optional[float] = None  # 最后交互时间
    
    # 待授权消息计数
    pending_message_count: int = 0

    @property
    def contact_id(self) -> str:
        """联系人唯一键，对应对方 user_id。"""
        return self.user_id

    @contact_id.setter
    def contact_id(self, value: str) -> None:
        self.user_id = value

    @property
    def trust_status(self) -> ContactAuthStatus:
        """M2-B 规范名称，兼容旧的 auth_status 字段。"""
        return self.auth_status

    @trust_status.setter
    def trust_status(self, value: ContactAuthStatus) -> None:
        self.auth_status = normalize_contact_auth_status(value)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化存储"""
        base_dict = super().to_dict()
        base_dict.update({
            "contact_id": self.contact_id,
            "auth_status": self.auth_status.value,
            "trust_status": self.trust_status.value,
            "alias": self.alias,
            "added_at": self.added_at,
            "last_interaction": self.last_interaction,
            "pending_message_count": self.pending_message_count
        })
        return base_dict
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Contact":
        """从字典格式创建联系人实例"""
        # 先创建User部分
        normalized_data = data.copy()
        normalized_data["user_id"] = data.get("contact_id") or data.get("user_id") or data.get("id", "")
        user = super().from_dict(normalized_data)

        trust_status = normalize_contact_auth_status(
            data.get("trust_status") or data.get("auth_status", "unknown")
        )
        
        # 创建Contact实例
        contact = cls(
            user_id=user.user_id,
            display_name=user.display_name,
            original_username=user.original_username,
            status=user.status,
            last_seen=user.last_seen,
            avatar_url=user.avatar_url,
            public_key=user.public_key,
            device_keys=user.device_keys,
            device_ids=user.device_ids,
            metadata=user.metadata,
            created_at=user.created_at,
            updated_at=user.updated_at,
            auth_status=trust_status,
            alias=data.get("alias", ""),
            added_at=data.get("added_at", time.time()),
            last_interaction=data.get("last_interaction"),
            pending_message_count=data.get("pending_message_count", 0)
        )
        return contact
    
    @classmethod
    def from_user(cls, user: User, auth_status: ContactAuthStatus = ContactAuthStatus.UNKNOWN) -> "Contact":
        """从User对象创建Contact对象"""
        contact = cls(
            user_id=user.user_id,
            display_name=user.display_name,
            original_username=user.original_username,
            status=user.status,
            last_seen=user.last_seen,
            avatar_url=user.avatar_url,
            public_key=user.public_key,
            device_keys=user.device_keys,
            device_ids=user.device_ids,
            metadata=user.metadata,
            created_at=user.created_at,
            updated_at=user.updated_at,
            auth_status=auth_status,
            added_at=time.time()
        )
        return contact
    
    def get_display_name(self) -> str:
        """获取联系人主显示名，优先使用 display_name，否则回退到 contact_id。"""
        return super().get_display_name()

    def get_list_display_name(self) -> str:
        """联系人列表显示：优先名称，必要时附带 contact_id。"""
        primary_name = self.get_display_name()
        normalized_display_name = (self.display_name or "").strip()
        if normalized_display_name and normalized_display_name != self.contact_id:
            return f"{primary_name} ({self.contact_id})"
        return primary_name
    
    def update_auth_status(self, auth_status: ContactAuthStatus) -> None:
        """更新授权状态"""
        self.auth_status = normalize_contact_auth_status(auth_status)
        self.updated_at = time.time()
        
        # 状态已经终结或放行后，不再保留待授权计数
        if self.auth_status in (ContactAuthStatus.TRUSTED, ContactAuthStatus.REJECTED):
            self.pending_message_count = 0
    
    def increment_pending_count(self) -> int:
        """增加待授权消息计数"""
        self.pending_message_count += 1
        self.updated_at = time.time()
        return self.pending_message_count
    
    def clear_pending_count(self) -> None:
        """清空待授权消息计数"""
        self.pending_message_count = 0
        self.updated_at = time.time()
    
    def update_last_interaction(self) -> None:
        """更新最后交互时间"""
        self.last_interaction = time.time()
        self.updated_at = time.time()
    
    def __str__(self) -> str:
        """友好的字符串表示"""
        base_str = super().__str__()
        return f"{base_str} [{self.auth_status.value}]"
