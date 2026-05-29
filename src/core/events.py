"""
事件系统定义

定义了应用内的事件类型和事件总线接口。
用于模块间的解耦通信。
"""

from typing import Dict, List, Callable, Any, Optional
from enum import Enum
import logging

from src.utils.logger import get_logger


class EventType(Enum):
    """事件类型枚举"""
    # 消息相关事件
    MESSAGE_SENT = "message_sent"              # 消息已发送
    MESSAGE_RECEIVED = "message_received"      # 消息已接收
    MESSAGE_DELIVERED = "message_delivered"    # 消息已送达（对方已接收）
    MESSAGE_READ = "message_read"              # 消息已读
    MESSAGE_FAILED = "message_failed"          # 消息发送失败

    # 会话相关事件
    CONVERSATION_SELECTED = "conversation_selected"  # 会话被选中
    CONVERSATION_UPDATED = "conversation_updated"    # 会话更新
    CONVERSATION_CREATED = "conversation_created"    # 会话创建
    GROUP_CREATED = "group_created"                  # 群组创建
    GROUP_UPDATED = "group_updated"                  # 群组/成员更新
    GROUP_INVITE_RECEIVED = "group_invite_received"  # 收到群组邀请

    # 用户相关事件
    USER_STATUS_CHANGED = "user_status_changed"      # 用户状态变化
    USER_UPDATED = "user_updated"                    # 用户信息更新

    # 系统事件
    CONNECTING = "connecting"                        # 连接中
    CONNECTED = "connected"                          # 连接建立
    DISCONNECTED = "disconnected"                    # 连接断开
    ERROR = "error"                                  # 错误事件
    
    # 配对与信任事件
    PAIRING_REQUEST = "pairing_request"              # 配对请求
    PAIRING_COMPLETED = "pairing_completed"          # 配对完成
    CONTACT_AUTH_REQUIRED = "contact_auth_required"  # 联系人授权请求


class Event:
    """
    事件对象

    包含事件类型、数据、时间戳等信息。
    """

    def __init__(self, event_type: EventType, data: Dict[str, Any] = None, source: str = None):
        """
        初始化事件

        Args:
            event_type: 事件类型
            data: 事件数据
            source: 事件源标识
        """
        import time
        self.event_type = event_type
        self.data = data or {}
        self.source = source
        self.timestamp = time.time()
        self.handled = False

    def __str__(self) -> str:
        """友好的字符串表示"""
        return f"Event[{self.event_type.value}] from {self.source or 'unknown'} at {self.timestamp}"

    def mark_handled(self) -> None:
        """标记事件已处理"""
        self.handled = True


class EventBus:
    """
    事件总线

    提供事件的发布和订阅功能。
    单例模式，全局共享。
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._subscribers: Dict[EventType, List[Callable[[Event], None]]] = {}
        self._logger = get_logger("event_bus")
        self._initialized = True
        self._logger.info("事件总线初始化完成")

    def subscribe(self, event_type: EventType, callback: Callable[[Event], None]) -> None:
        """
        订阅事件

        Args:
            event_type: 事件类型
            callback: 事件处理回调函数
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []

        self._subscribers[event_type].append(callback)
        self._logger.debug(f"订阅事件: {event_type.value}")

    def unsubscribe(self, event_type: EventType, callback: Callable[[Event], None]) -> None:
        """
        取消订阅事件

        Args:
            event_type: 事件类型
            callback: 要移除的回调函数
        """
        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(callback)
                self._logger.debug(f"取消订阅事件: {event_type.value}")
            except ValueError:
                self._logger.warning(f"尝试取消订阅未注册的回调: {event_type.value}")

    def publish(self, event: Event) -> None:
        """
        发布事件

        Args:
            event: 事件对象
        """
        event_type = event.event_type
        self._logger.debug(f"发布事件: {event}")

        if event_type in self._subscribers:
            # 复制列表，防止在迭代过程中修改
            callbacks = self._subscribers[event_type][:]

            for callback in callbacks:
                try:
                    callback(event)
                except Exception as e:
                    self._logger.error(f"事件处理异常: {e}", exc_info=True)

    def publish_simple(self, event_type: EventType, data: Dict[str, Any] = None, source: str = None) -> None:
        """
        简化版发布事件

        Args:
            event_type: 事件类型
            data: 事件数据
            source: 事件源
        """
        event = Event(event_type, data, source)
        self.publish(event)

    def clear_subscribers(self, event_type: Optional[EventType] = None) -> None:
        """
        清除订阅者

        Args:
            event_type: 事件类型，如果为None则清除所有
        """
        if event_type is None:
            self._subscribers.clear()
            self._logger.info("清除所有事件订阅者")
        elif event_type in self._subscribers:
            self._subscribers[event_type].clear()
            self._logger.info(f"清除事件订阅者: {event_type.value}")


# 全局事件总线实例
event_bus = EventBus()


# 便捷函数
def subscribe(event_type: EventType, callback: Callable[[Event], None]) -> None:
    """订阅事件（便捷函数）"""
    event_bus.subscribe(event_type, callback)


def unsubscribe(event_type: EventType, callback: Callable[[Event], None]) -> None:
    """取消订阅事件（便捷函数）"""
    event_bus.unsubscribe(event_type, callback)


def publish(event: Event) -> None:
    """发布事件（便捷函数）"""
    event_bus.publish(event)


def publish_simple(event_type: EventType, data: Dict[str, Any] = None, source: str = None) -> None:
    """简化版发布事件（便捷函数）"""
    event_bus.publish_simple(event_type, data, source)
