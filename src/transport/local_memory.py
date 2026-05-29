"""
本地内存传输实现

基于内存的消息总线，模拟网络传输行为。
支持全局单例模式，多个客户端可共享同一个传输层实例。
"""

import logging
import threading
import time
from typing import Callable, Dict, Any, Optional, List, Set
from queue import Queue, Empty

from .interface import Transport
from src.models.message import Message, MessageStatus
from src.utils.logger import get_logger


# 全局传输层实例注册表（单例模式）
_global_transports: Dict[str, "LocalMemoryTransport"] = {}
_global_transports_lock = threading.RLock()


class LocalMemoryTransport(Transport):
    """
    本地内存传输实现

    在内存中维护消息路由表，模拟网络传输。
    支持多客户端注册和消息路由。
    支持全局单例模式（通过get_global_instance方法）。
    """

    @classmethod
    def get_global_instance(cls, transport_id: str = "default") -> "LocalMemoryTransport":
        """
        获取全局共享的传输层实例（单例模式）

        多个ChatService实例应使用此方法获取同一个传输层实例，
        以实现多用户间的消息互发。

        Args:
            transport_id: 传输标识符，不同的ID对应不同的全局实例

        Returns:
            LocalMemoryTransport: 全局共享的传输层实例
        """
        with _global_transports_lock:
            if transport_id not in _global_transports:
                _global_transports[transport_id] = cls(transport_id)
                _global_transports[transport_id].logger.info(
                    f"创建全局传输层实例: {transport_id}"
                )
            return _global_transports[transport_id]

    @classmethod
    def clear_global_instances(cls) -> None:
        """清除所有全局传输层实例（主要用于测试）"""
        with _global_transports_lock:
            for transport_id, transport in list(_global_transports.items()):
                transport.disconnect()
                del _global_transports[transport_id]

    def __init__(self, transport_id: str = "local_memory"):
        """
        初始化本地内存传输

        Args:
            transport_id: 传输标识符
        """
        self.transport_id = transport_id
        self.logger = get_logger(f"transport.{transport_id}")

        # 接收者注册表：receiver_id -> callback
        self._receivers: Dict[str, Callable[[Message], None]] = {}
        self._user_profiles: Dict[str, Dict[str, str]] = {}
        self._online_users_callbacks: List[Callable[[Dict[str, Dict[str, str]]], None]] = []
        self._groups: Dict[str, Set[str]] = {}
        self._pending_user_id = ""
        self._pending_display_name = ""
        self._pending_profile_extra: Dict[str, str] = {}

        # 消息队列（用于异步处理）
        self._message_queue: Queue = Queue()
        self._processing_thread: Optional[threading.Thread] = None
        self._running = False

        # 状态
        self._connected = False
        self._message_count = 0
        self._lock = threading.RLock()

        self.logger.info(f"初始化本地内存传输: {transport_id}")

    def send_message(self, message: Message) -> bool:
        """
        发送消息到指定接收者

        如果接收者已注册，将异步调用其回调函数。
        模拟网络延迟：立即放入队列异步处理。

        Args:
            message: 消息对象

        Returns:
            bool: 总是返回True（模拟成功发送）
        """
        with self._lock:
            self._message_count += 1

        # 记录发送日志，避免加密消息泄漏明文或完整密文。
        if message.encryption_version or (message.metadata or {}).get("encryption_version"):
            self.logger.info(
                f"发送加密消息 [{message.id[:8]}] from {message.sender_id} to {message.receiver_id}, "
                f"version={message.encryption_version or message.metadata.get('encryption_version')}"
            )
        else:
            self.logger.info(
                f"发送消息 [{message.id[:8]}] from {message.sender_id} to {message.receiver_id}: "
                f"payload_len={len(message.content or '')}"
            )

        # 将消息放入队列异步处理
        self._message_queue.put(Message.from_dict(message.to_dict()))

        # 返回成功（模拟立即发送成功）
        return True

    def register_receiver(self, receiver_id: str, callback: Callable[[Message], None]) -> None:
        """
        注册消息接收回调

        Args:
            receiver_id: 接收者ID
            callback: 消息到达时的回调函数
        """
        with self._lock:
            if receiver_id in self._receivers:
                self.logger.warning(f"接收者 {receiver_id} 已存在，将被覆盖")

            self._receivers[receiver_id] = callback
            self.logger.info(f"注册接收者: {receiver_id}")

    def unregister_receiver(self, receiver_id: str) -> None:
        """
        取消注册消息接收回调

        Args:
            receiver_id: 接收者ID
        """
        with self._lock:
            if receiver_id in self._receivers:
                del self._receivers[receiver_id]
                self.logger.info(f"取消注册接收者: {receiver_id}")
            else:
                self.logger.warning(f"尝试取消注册不存在的接收者: {receiver_id}")

    def connect(self) -> bool:
        """
        启动传输层（启动消息处理线程）

        Returns:
            bool: 是否成功启动
        """
        if self._connected:
            self.logger.warning("传输层已连接")
            return True

        try:
            self._running = True
            self._processing_thread = threading.Thread(
                target=self._process_messages,
                name=f"{self.transport_id}_processor",
                daemon=True
            )
            self._processing_thread.start()

            self._connected = True
            self.logger.info("本地内存传输已连接")
            return True

        except Exception as e:
            self.logger.error(f"连接失败: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        """
        停止传输层
        """
        if not self._connected:
            return

        self.logger.info("正在断开传输层连接...")
        self._running = False

        # 放入一个空消息来唤醒处理线程
        self._message_queue.put(None)

        if self._processing_thread and self._processing_thread.is_alive():
            self._processing_thread.join(timeout=2.0)

        self._connected = False
        self.logger.info("传输层已断开")

    def is_connected(self) -> bool:
        """
        检查是否已连接

        Returns:
            bool: 是否已连接
        """
        return self._connected

    def get_status(self) -> Dict[str, Any]:
        """
        获取传输层状态

        Returns:
            Dict[str, Any]: 状态信息
        """
        with self._lock:
            return {
                "transport_type": "memory",
                "transport_id": self.transport_id,
                "connected": self._connected,
                "receiver_count": len(self._receivers),
                "message_count": self._message_count,
                "queue_size": self._message_queue.qsize(),
                "processing_thread_alive": self._processing_thread.is_alive() if self._processing_thread else False,
                "online_users": sorted(self._user_profiles.keys()),
                "online_user_profiles": {
                    user_id: profile.copy()
                    for user_id, profile in self._user_profiles.items()
                },
                "groups": {
                    group_id: sorted(member_ids)
                    for group_id, member_ids in self._groups.items()
                },
            }

    def sync_groups(self, groups: List[Dict[str, Any]]) -> bool:
        """同步群组快照到内存路由表。"""
        with self._lock:
            for item in groups or []:
                group = item.get("group", {}) if isinstance(item, dict) else {}
                members = item.get("members", []) if isinstance(item, dict) else []
                group_id = str(group.get("id", "")).strip()
                if not group_id:
                    continue
                self._groups[group_id] = {
                    str(member.get("user_id", "")).strip()
                    for member in members
                    if isinstance(member, dict)
                    and str(member.get("status", "active")) == "active"
                    and str(member.get("user_id", "")).strip()
                }
        return True

    def send_group_update(
        self,
        group: Dict[str, Any],
        members: List[Dict[str, Any]],
        from_user: Optional[str] = None,
    ) -> bool:
        """广播群组/成员更新控制消息。"""
        group_id = str(group.get("id", "")).strip() if isinstance(group, dict) else ""
        if not group_id:
            return False
        active_member_ids = {
            str(member.get("user_id", "")).strip()
            for member in members
            if isinstance(member, dict)
            and str(member.get("status", "active")) == "active"
            and str(member.get("user_id", "")).strip()
        }
        notify_member_ids = {
            str(member.get("user_id", "")).strip()
            for member in members
            if isinstance(member, dict)
            and str(member.get("status", "active")) in {"active", "invited"}
            and str(member.get("user_id", "")).strip()
        }
        with self._lock:
            self._groups[group_id] = active_member_ids
            callbacks = [
                (member_id, self._receivers.get(member_id))
                for member_id in notify_member_ids
            ]

        control_msg = Message(
            content="",
            sender_id=from_user or str(group.get("creator_id", "")),
            receiver_id=group_id,
            conversation_id=group_id,
            status=MessageStatus.SENT,
            metadata={
                "control_type": "group_update",
                "inviter_id": from_user or str(group.get("creator_id", "")),
                "group": group,
                "members": members,
            },
        )
        for member_id, callback in callbacks:
            if not callback:
                continue
            try:
                msg = Message.from_dict(control_msg.to_dict())
                msg.receiver_id = member_id
                callback(msg)
            except Exception as e:
                self.logger.error(f"广播群更新失败 {group_id}->{member_id}: {e}", exc_info=True)
        return True

    def send_group_message(self, message: Message, members: Optional[List[str]] = None) -> bool:
        """按群成员 fanout 群消息，发送者本地不回环。"""
        group_id = message.receiver_id
        with self._lock:
            member_ids = set(members or []) or set(self._groups.get(group_id, set()))
            callbacks = [
                (member_id, self._receivers.get(member_id))
                for member_id in member_ids
                if member_id != message.sender_id
            ]

        for member_id, callback in callbacks:
            if not callback:
                self.logger.warning(f"群消息没有找到接收者: {group_id}->{member_id}")
                continue
            try:
                routed = Message.from_dict(message.to_dict())
                routed.receiver_id = group_id
                routed.conversation_id = group_id
                callback(routed)
                self.logger.info(f"路由群消息 [{message.id[:8]}] 到 {member_id} in {group_id}")
            except Exception as e:
                self.logger.error(f"路由群消息失败 {group_id}->{member_id}: {e}", exc_info=True)
        return True

    def set_user_profile(self, user_id: str, display_name: str = "", profile: Optional[Dict[str, str]] = None) -> None:
        """设置即将注册的本地用户 profile。"""
        self._pending_user_id = (user_id or "").strip()
        self._pending_display_name = (display_name or "").strip()
        self._pending_profile_extra = dict(profile or self._pending_profile_extra or {})

    def register_user(self, user_id: str) -> bool:
        """在内存传输中登记在线用户信息。"""
        exact_user_id = (user_id or self._pending_user_id or "").strip()
        if not exact_user_id:
            return False
        with self._lock:
            self._user_profiles[exact_user_id] = {
                "user_id": exact_user_id,
                "display_name": self._pending_display_name,
                **self._pending_profile_extra,
            }
        self._notify_online_users_changed()
        return True

    def update_display_name(self, display_name: str) -> bool:
        """更新当前 pending 用户的显示名。"""
        self._pending_display_name = (display_name or "").strip()
        if not self._pending_user_id:
            return False
        with self._lock:
            self._user_profiles[self._pending_user_id] = {
                "user_id": self._pending_user_id,
                "display_name": self._pending_display_name,
                **self._pending_profile_extra,
            }
        self._notify_online_users_changed()
        return True

    def set_online_users_callback(self, callback: Callable[[Dict[str, Dict[str, str]]], None]) -> None:
        """注册在线用户目录更新回调。"""
        with self._lock:
            if callback not in self._online_users_callbacks:
                self._online_users_callbacks.append(callback)

    def refresh_online_users(self, timeout: float = 0.0) -> Dict[str, Dict[str, str]]:
        """内存传输直接返回本地在线目录。"""
        return self.get_online_user_profiles()

    def get_online_users(self) -> list:
        """返回在线 user_id 列表。"""
        with self._lock:
            return sorted(self._user_profiles.keys())

    def get_online_user_profiles(self) -> Dict[str, Dict[str, str]]:
        """返回在线用户 profile。"""
        with self._lock:
            return {
                user_id: profile.copy()
                for user_id, profile in self._user_profiles.items()
            }

    def _notify_online_users_changed(self) -> None:
        """通知在线用户目录发生变化。"""
        profiles = self.get_online_user_profiles()
        with self._lock:
            callbacks = list(self._online_users_callbacks)
        for callback in callbacks:
            try:
                callback(profiles)
            except Exception as e:
                self.logger.error(f"在线用户目录回调失败: {e}", exc_info=True)

    def _process_messages(self) -> None:
        """
        消息处理线程函数

        从队列中取出消息并路由到对应的接收者。
        模拟网络延迟：每条消息处理间隔0.1-0.3秒。
        """
        self.logger.info("消息处理线程启动")

        while self._running:
            try:
                # 从队列获取消息，最多等待1秒
                message = self._message_queue.get(timeout=1.0)

                # None是停止信号
                if message is None:
                    self.logger.debug("收到停止信号")
                    break

                # 模拟轻量网络延迟，同时保持本地 smoke 测试稳定。
                time.sleep(0.01)

                # 路由消息
                self._route_message(message)

                # 标记任务完成
                self._message_queue.task_done()

            except Empty:
                # 队列为空，继续等待
                continue
            except Exception as e:
                self.logger.error(f"消息处理异常: {e}", exc_info=True)

        self.logger.info("消息处理线程退出")

    def _route_message(self, message: Message) -> None:
        """
        路由消息到接收者

        Args:
            message: 要路由的消息
        """
        receiver_id = message.receiver_id

        with self._lock:
            callback = self._receivers.get(receiver_id)

        if callback:
            try:
                # 模拟消息送达，更新状态
                if message.status == MessageStatus.SENDING:
                    message.status = MessageStatus.SENT

                self.logger.info(f"路由消息 [{message.id[:8]}] 到接收者 {receiver_id}")

                # 调用接收者回调
                callback(message)

                # 模拟送达回执（可选）
                # 这里可以触发on_message_delivered事件

            except Exception as e:
                self.logger.error(f"调用接收者回调失败: {e}", exc_info=True)
        else:
            self.logger.warning(f"消息 [{message.id[:8]}] 没有找到接收者: {receiver_id}")

    def __del__(self):
        """析构函数，确保资源清理"""
        self.disconnect()
