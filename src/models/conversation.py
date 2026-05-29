"""
会话数据模型

定义了会话（聊天对话）的数据结构，包含参与者、最后消息、未读计数等信息。
支持一对一聊天和群聊（预留）。
"""

import time
import uuid
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional


class ConversationType(Enum):
    """会话类型枚举"""
    DIRECT = "direct"      # 一对一聊天
    GROUP = "group"        # 群聊


@dataclass
class Conversation:
    """
    会话数据模型

    注意：为未来扩展预留了以下字段：
    - group_name: 群聊名称
    - group_avatar: 群聊头像
    - admins: 群管理员列表
    """

    # 核心字段
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    conversation_type: ConversationType = ConversationType.DIRECT
    participant_ids: List[str] = field(default_factory=list)  # 参与者用户ID列表

    # 显示相关字段
    display_name: Optional[str] = None  # 显示名称（如果为空则根据参与者生成）
    avatar_url: Optional[str] = None  # 头像URL

    # 状态字段
    last_message_id: Optional[str] = None  # 最后一条消息ID
    last_message_preview: Optional[str] = None  # 最后一条消息预览
    last_message_time: Optional[float] = None  # 最后一条消息时间
    unread_count: int = 0  # 未读消息数

    # 为群聊预留的字段
    group_name: Optional[str] = None  # 群聊名称
    group_avatar: Optional[str] = None  # 群聊头像URL
    admins: List[str] = field(default_factory=list)  # 管理员列表

    # 扩展字段
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化存储"""
        return {
            "id": self.id,
            "conversation_type": self.conversation_type.value,
            "participant_ids": self.participant_ids,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "last_message_id": self.last_message_id,
            "last_message_preview": self.last_message_preview,
            "last_message_time": self.last_message_time,
            "unread_count": self.unread_count,
            "group_name": self.group_name,
            "group_avatar": self.group_avatar,
            "admins": self.admins,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def generate_id(cls, participant_ids: List[str]) -> str:
        """
        生成确定性会话ID
        
        基于排序后的参与者ID生成确定性哈希ID，确保相同参与者总是得到相同会话ID。
        
        Args:
            participant_ids: 参与者ID列表
            
        Returns:
            str: 确定性会话ID
        """
        # 排序参与者ID以确保一致性
        sorted_ids = sorted(participant_ids)
        # 生成哈希
        hash_input = "|".join(sorted_ids).encode('utf-8')
        hash_hex = hashlib.sha256(hash_input).hexdigest()[:32]
        # 添加前缀以便识别
        return f"conv_{hash_hex}"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Conversation":
        """从字典格式创建会话实例"""
        conv = cls(
            id=data.get("id", str(uuid.uuid4())),
            conversation_type=cls._normalize_type(data.get("conversation_type", "direct")),
            participant_ids=data.get("participant_ids", []),
            display_name=data.get("display_name"),
            avatar_url=data.get("avatar_url"),
            last_message_id=data.get("last_message_id"),
            last_message_preview=data.get("last_message_preview"),
            last_message_time=data.get("last_message_time"),
            unread_count=data.get("unread_count", 0),
            group_name=data.get("group_name"),
            group_avatar=data.get("group_avatar"),
            admins=data.get("admins", []),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
        return conv

    def update_last_message(self, message_id: str, preview: str, timestamp: float) -> None:
        """更新最后一条消息信息"""
        self.last_message_id = message_id
        self.last_message_preview = preview
        self.last_message_time = timestamp
        self.updated_at = time.time()

    @staticmethod
    def _normalize_type(value: Any) -> ConversationType:
        """兼容历史数据和坏数据的会话类型解析。"""
        if isinstance(value, ConversationType):
            return value
        try:
            return ConversationType(str(value or ConversationType.DIRECT.value))
        except ValueError:
            return ConversationType.DIRECT

    def get_display_name(self) -> str:
        """获取显示名称，如果未设置则生成默认名称"""
        if self.display_name:
            return self.display_name

        # 根据参与者生成默认名称
        if self.conversation_type == ConversationType.DIRECT and len(self.participant_ids) >= 2:
            # 一对一聊天，显示对方名称（这里简化为显示ID）
            return f"Chat with {self.participant_ids[0][:8]}..."
        elif self.group_name:
            return self.group_name
        else:
            return f"Conversation {self.id[:8]}"

    def __str__(self) -> str:
        """友好的字符串表示"""
        return f"Conversation[{self.id[:8]}]: {self.get_display_name()} ({self.unread_count} unread)"
