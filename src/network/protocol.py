"""
网络协议定义

定义客户端与Hub之间的最小JSON协议。
目前仅支持注册和心跳，为后续消息路由预留扩展。
"""

import json
import time
from enum import Enum
from typing import Dict, Any, Optional


class MessageType(str, Enum):
    """消息类型枚举"""
    REGISTER = "register"
    HEARTBEAT = "heartbeat"
    MESSAGE = "message"
    ONLINE_USERS = "online_users"
    GROUP_SYNC = "group_sync"
    GROUP_UPDATE = "group_update"
    GROUP_MESSAGE = "group_message"
    # ACK = "ack"


class ProtocolMessage:
    """协议消息基类"""
    
    def __init__(self, msg_type: MessageType, data: Optional[Dict[str, Any]] = None):
        self.type = msg_type
        self.data = data or {}
        self.timestamp = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "type": self.type.value,
            "data": self.data,
            "timestamp": self.timestamp
        }
    
    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict())

    def to_wire(self) -> str:
        """转换为带分帧符的网络传输文本。"""
        return frame_json(self.to_json()).decode("utf-8")

    def to_bytes(self) -> bytes:
        """转换为带分帧符的网络传输字节。"""
        return frame_json(self.to_json())
    
    @classmethod
    def from_json(cls, json_str: str) -> "ProtocolMessage":
        """从JSON字符串解析"""
        try:
            obj = json.loads(json_str)
            return cls.from_dict(obj)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ValueError(f"无效的协议消息: {e}")

    @classmethod
    def from_dict(cls, obj: Dict[str, Any]) -> "ProtocolMessage":
        """从协议字典解析。"""
        try:
            msg_type = MessageType(obj["type"])
            data = obj.get("data", {})
            msg = cls(msg_type, data)
            msg.timestamp = obj.get("timestamp", time.time())
            return msg
        except (KeyError, ValueError, TypeError) as e:
            raise ValueError(f"无效的协议消息: {e}")

    @classmethod
    def from_wire(cls, frame: Any) -> "ProtocolMessage":
        """从一条完整网络帧解析协议消息。"""
        if isinstance(frame, bytes):
            frame_text = frame.decode("utf-8")
        else:
            frame_text = str(frame)
        return cls.from_json(frame_text.rstrip(FRAME_DELIMITER))
    
    @classmethod
    def create_register(
        cls,
        user_id: str,
        display_name: Optional[str] = None,
        profile: Optional[Dict[str, Any]] = None,
    ) -> "ProtocolMessage":
        """创建注册消息"""
        data = {"user_id": user_id}
        if display_name is not None:
            data["display_name"] = display_name
        if profile:
            data.update(profile)
        return cls(MessageType.REGISTER, data)
    
    @classmethod
    def create_heartbeat(cls, user_id: str, device_id: str = "") -> "ProtocolMessage":
        """创建心跳消息"""
        data = {"user_id": user_id}
        if device_id:
            data["device_id"] = device_id
        return cls(MessageType.HEARTBEAT, data)
    
    def get_user_id(self) -> Optional[str]:
        """从消息数据中提取用户ID"""
        return self.data.get("user_id")
    
    @classmethod
    def create_online_users_request(cls) -> "ProtocolMessage":
        """创建在线用户列表请求。"""
        return cls(MessageType.ONLINE_USERS, {"request": True})

    @classmethod
    def create_online_users_response(cls, users: list) -> "ProtocolMessage":
        """创建在线用户列表响应。"""
        return cls(MessageType.ONLINE_USERS, {"users": users})

    @classmethod
    def create_group_sync(cls, user_id: str, groups: list) -> "ProtocolMessage":
        """创建群组快照同步消息。"""
        return cls(MessageType.GROUP_SYNC, {
            "user_id": user_id,
            "groups": groups,
        })

    @classmethod
    def create_group_update(cls, from_user: str, group: Dict[str, Any], members: list) -> "ProtocolMessage":
        """创建群组/成员更新消息。"""
        return cls(MessageType.GROUP_UPDATE, {
            "from": from_user,
            "group": group,
            "members": members,
        })

    @classmethod
    def create_group_message(
        cls,
        from_user: str,
        group_id: str,
        content: str,
        message_id: Optional[str] = None,
        from_display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ProtocolMessage":
        """创建群聊消息。"""
        data = {
            "from": from_user,
            "group_id": group_id,
            "content": content,
            "metadata": metadata or {},
        }
        if message_id:
            data["message_id"] = message_id
        if from_display_name is not None:
            data["from_display_name"] = from_display_name
        return cls(MessageType.GROUP_MESSAGE, data)

    @classmethod
    def create_message(
        cls,
        from_user: str,
        to_user: str,
        content: str,
        message_id: Optional[str] = None,
        from_display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ProtocolMessage":
        """创建消息"""
        data = {
            "from": from_user,
            "to": to_user,
            "content": content
        }
        if message_id:
            data["message_id"] = message_id
        if from_display_name is not None:
            data["from_display_name"] = from_display_name
        if metadata is not None:
            data["metadata"] = metadata
        return cls(MessageType.MESSAGE, data)
    
    def get_message_from(self) -> Optional[str]:
        """从消息数据中提取发送者ID（仅对MESSAGE类型有效）"""
        return self.data.get("from")
    
    def get_message_to(self) -> Optional[str]:
        """从消息数据中提取接收者ID（仅对MESSAGE类型有效）"""
        return self.data.get("to")
    
    def get_message_content(self) -> Optional[str]:
        """从消息数据中提取消息内容（仅对MESSAGE类型有效）"""
        return self.data.get("content")

    def get_message_metadata(self) -> Dict[str, Any]:
        """从消息数据中提取 metadata（仅对 MESSAGE/GROUP_MESSAGE 类型有效）。"""
        metadata = self.data.get("metadata", {})
        return metadata if isinstance(metadata, dict) else {}
    
    def get_message_id(self) -> Optional[str]:
        """从消息数据中提取消息ID（仅对MESSAGE类型有效）"""
        return self.data.get("message_id")

    def get_message_from_display_name(self) -> str:
        """从消息数据中提取发送者显示名称（仅对MESSAGE类型有效）"""
        return self.data.get("from_display_name", "")

    def get_group_id(self) -> Optional[str]:
        """从群消息数据中提取 group_id。"""
        return self.data.get("group_id")


# 协议常量
MAX_MESSAGE_SIZE = 65536  # 最大消息大小（64KB）
HEARTBEAT_INTERVAL = 30  # 心跳间隔（秒）
HEARTBEAT_TIMEOUT = HEARTBEAT_INTERVAL * 3  # 心跳超时（3倍间隔）
FRAME_DELIMITER = "\n"  # 每条协议消息一行，避免 TCP 粘包/半包导致 JSON 解析失败


def frame_json(json_str: str) -> bytes:
    """把单条 JSON 协议消息编码成一帧。"""
    return (json_str.rstrip(FRAME_DELIMITER) + FRAME_DELIMITER).encode("utf-8")


class ProtocolFrameBuffer:
    """
    TCP 协议流缓冲区。

    优先解析换行分帧的新协议；同时兼容旧版没有换行分隔的单个/连续 JSON。
    """

    def __init__(self, max_buffer_size: int = MAX_MESSAGE_SIZE):
        self.max_buffer_size = max_buffer_size
        self._buffer = ""
        self._decoder = json.JSONDecoder()

    def feed(self, data: Any) -> None:
        """追加网络字节/文本。"""
        if isinstance(data, bytes):
            chunk = data.decode("utf-8")
        else:
            chunk = str(data)
        self._buffer += chunk
        if len(self._buffer.encode("utf-8")) > self.max_buffer_size:
            raise ValueError(f"协议缓冲区超过大小限制: {len(self._buffer.encode('utf-8'))}")

    def pop_message(self) -> Optional[ProtocolMessage]:
        """弹出一条完整协议消息；如果还不完整则返回 None。"""
        self._buffer = self._buffer.lstrip()
        if not self._buffer:
            return None

        newline_index = self._buffer.find(FRAME_DELIMITER)
        if newline_index >= 0:
            frame = self._buffer[:newline_index]
            self._buffer = self._buffer[newline_index + len(FRAME_DELIMITER):]
            if not frame.strip():
                return self.pop_message()
            return ProtocolMessage.from_json(frame)

        try:
            obj, end_index = self._decoder.raw_decode(self._buffer)
        except json.JSONDecodeError:
            return None

        self._buffer = self._buffer[end_index:]
        return ProtocolMessage.from_dict(obj)

    def clear(self) -> None:
        """清空当前缓冲区。"""
        self._buffer = ""
