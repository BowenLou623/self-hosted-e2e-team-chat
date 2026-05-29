"""
传输层接口定义

定义了消息传输的抽象接口，支持本地和网络传输的统一定义。
第一阶段实现LocalMemoryTransport，第二阶段可替换为NetworkTransport。
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional, Dict, Any, List
from src.models.message import Message


class Transport(ABC):
    """
    消息传输抽象基类

    定义了消息发送、接收、连接、断开等基本操作。
    所有具体的传输实现（本地内存、局域网、互联网）都应继承此接口。
    """

    @abstractmethod
    def send_message(self, message: Message) -> bool:
        """
        发送消息

        Args:
            message: 要发送的消息对象

        Returns:
            bool: 是否成功发送（不代表对方已接收）
        """
        pass

    @abstractmethod
    def register_receiver(self, receiver_id: str, callback: Callable[[Message], None]) -> None:
        """
        注册消息接收回调

        Args:
            receiver_id: 接收者ID（通常是用户ID或设备ID）
            callback: 当消息到达时调用的回调函数
        """
        pass

    @abstractmethod
    def unregister_receiver(self, receiver_id: str) -> None:
        """
        取消注册消息接收回调

        Args:
            receiver_id: 接收者ID
        """
        pass

    @abstractmethod
    def connect(self) -> bool:
        """
        连接传输层

        Returns:
            bool: 是否连接成功
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """
        断开传输层连接
        """
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """
        检查是否已连接

        Returns:
            bool: 是否已连接
        """
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """
        获取传输层状态

        Returns:
            Dict[str, Any]: 状态信息字典
        """
        pass

    def sync_groups(self, groups: List[Dict[str, Any]]) -> bool:
        """
        同步本地群组快照到传输层。

        第三阶段默认实现为空操作，具体传输层可覆盖。
        """
        return True

    def send_group_update(
        self,
        group: Dict[str, Any],
        members: List[Dict[str, Any]],
        from_user: Optional[str] = None,
    ) -> bool:
        """
        通知传输层群组或成员发生变化。

        第三阶段默认实现为空操作，具体传输层可覆盖。
        """
        return True

    def send_group_message(self, message: Message, members: Optional[List[str]] = None) -> bool:
        """
        发送群消息。

        默认回退到 send_message，兼容尚未扩展的传输实现。
        """
        return self.send_message(message)


class MessageCallback(ABC):
    """
    消息回调接口（可选）

    提供更结构化的消息处理接口，可作为register_receiver的替代方案。
    """

    @abstractmethod
    def on_message_received(self, message: Message) -> None:
        """
        当消息到达时调用

        Args:
            message: 接收到的消息
        """
        pass

    @abstractmethod
    def on_message_delivered(self, message_id: str) -> None:
        """
        当消息被对方接收时调用（送达回执）

        Args:
            message_id: 消息ID
        """
        pass

    @abstractmethod
    def on_message_failed(self, message_id: str, reason: str) -> None:
        """
        当消息发送失败时调用

        Args:
            message_id: 消息ID
            reason: 失败原因
        """
        pass
