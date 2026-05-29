"""
消息数据模型

定义了消息的核心数据结构，包含内容、发送者、接收者、时间戳、状态等信息。
为未来的扩展预留了加密相关字段。
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


class MessageStatus(Enum):
    """消息状态枚举"""
    SENDING = "sending"      # 发送中
    SENT = "sent"           # 已发送（对方可能未收到）
    DELIVERED = "delivered" # 已送达（对方已收到）
    READ = "read"           # 已读
    FAILED = "failed"       # 发送失败


class MessageType(Enum):
    """消息类型枚举"""
    TEXT = "text"           # 文本消息
    FILE = "file"           # 文件消息（预留）
    IMAGE = "image"         # 图片消息（预留）
    SYSTEM = "system"       # 系统消息（预留）


FILE_MESSAGE_METADATA_SCHEMA: Dict[str, Any] = {
    "schema": "file_event_v1",
    "event_type": "",
    "project_id": "",
    "file_id": "",
    "file_name": "",
    "size": 0,
    "mime_type": "",
    "sha256": "",
    "shared_folder_id": "",
    "syncthing_folder_id": "",
    "relative_path": "",
    "sync_status": "reserved",
    "origin_user_id": "",
    "event_time": 0.0,
}


class MessageAuthStatus(Enum):
    """消息授权状态枚举"""
    PENDING = "pending"      # 待授权消息
    TRUSTED = "trusted"      # 已授权消息
    REJECTED = "rejected"    # 被拒绝消息


@dataclass
class Message:
    """
    消息数据模型

    注意：为第二阶段扩展预留了以下字段：
    - encrypted_content: 加密后的内容（未来端到端加密使用）
    - encryption_version: 加密算法版本
    - signature: 消息签名（未来完整性验证使用）
    """

    # 核心字段
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str = ""  # 明文内容（第一阶段使用）
    sender_id: str = ""  # 发送者用户ID
    receiver_id: str = ""  # 接收者用户ID（个人聊天）或群ID（群聊）
    conversation_id: str = ""  # 所属会话ID

    # 状态和时间
    status: MessageStatus = MessageStatus.SENDING
    message_type: MessageType = MessageType.TEXT
    timestamp: float = field(default_factory=time.time)  # 发送时间戳
    delivered_at: Optional[float] = None  # 送达时间
    read_at: Optional[float] = None  # 已读时间

    # 为第二阶段预留的加密字段
    encrypted_content: Optional[str] = None  # 加密后的内容
    encryption_version: Optional[str] = None  # 加密算法版本
    signature: Optional[str] = None  # 消息签名

    # 授权状态（用于待授权消息流程）
    auth_status: MessageAuthStatus = MessageAuthStatus.TRUSTED  # 默认已授权

    # 扩展字段（预留）
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于序列化存储"""
        return {
            "id": self.id,
            "content": self.content,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "conversation_id": self.conversation_id,
            "status": self.status.value,
            "message_type": self.message_type.value,
            "timestamp": self.timestamp,
            "delivered_at": self.delivered_at,
            "read_at": self.read_at,
            "encrypted_content": self.encrypted_content,
            "encryption_version": self.encryption_version,
            "signature": self.signature,
            "auth_status": self.auth_status.value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """从字典格式创建消息实例"""
        msg = cls(
            id=data.get("id", str(uuid.uuid4())),
            content=data.get("content", ""),
            sender_id=data.get("sender_id", ""),
            receiver_id=data.get("receiver_id", ""),
            conversation_id=data.get("conversation_id", ""),
            status=MessageStatus(data.get("status", "sending")),
            message_type=MessageType(data.get("message_type", "text")),
            timestamp=data.get("timestamp", time.time()),
            delivered_at=data.get("delivered_at"),
            read_at=data.get("read_at"),
            encrypted_content=data.get("encrypted_content"),
            encryption_version=data.get("encryption_version"),
            signature=data.get("signature"),
            auth_status=MessageAuthStatus(data.get("auth_status", "trusted")),
            metadata=data.get("metadata", {}),
        )
        return msg

    def __str__(self) -> str:
        """友好的字符串表示"""
        return (
            f"Message[{self.id[:8]}]: payload_len={len(self.content or '')} "
            f"(from {self.sender_id[:8]} to {self.receiver_id[:8]})"
        )
