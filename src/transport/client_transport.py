"""
客户端网络传输实现

实现NetworkTransport接口，连接Hub服务器，支持用户注册、心跳维持。
"""

import asyncio
import json
import logging
import threading
import time
from typing import Callable, Dict, Any, Optional, List
from queue import Queue, Empty

from .interface import Transport
from src.models.message import Message, MessageStatus, MessageType as ModelMessageType
from src.models.conversation import Conversation
from src.utils.logger import get_logger
from src.network.protocol import ProtocolMessage, MessageType, HEARTBEAT_INTERVAL, frame_json, ProtocolFrameBuffer


class ClientTransport(Transport):
    """
    客户端网络传输实现
    
    连接Hub服务器，实现消息发送和接收。
    支持用户注册、心跳维持、在线用户列表获取。
    """
    
    def __init__(self, server_address: str = "localhost:8080"):
        """
        初始化客户端传输
        
        Args:
            server_address: Hub服务器地址（格式：host:port）
        """
        self.server_address = server_address
        self.logger = get_logger(f"client_transport.{server_address}")
        
        # 连接状态
        self._connected = False
        self._connecting = False
        self._registered = False
        self._has_registered_once = False
        self._user_id: Optional[str] = None
        self._display_name = ""
        self._profile_extra: Dict[str, str] = {}
        
        # 接收者注册表
        self._receivers: Dict[str, Callable[[Message], None]] = {}
        
        # 异步事件循环和线程
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._network_thread: Optional[threading.Thread] = None
        self._running = False
        
        # 消息队列（用于从网络线程到主线程的消息传递）
        self._incoming_queue: Queue = Queue()
        self._outgoing_queue: Queue = Queue()
        
        # 连接对象
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        
        # 心跳任务
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        # 连接状态回调
        self._connection_state_callback: Optional[Callable[[str], None]] = None
        self._online_users_callback: Optional[Callable[[Dict[str, Dict[str, str]]], None]] = None
        
        # 在线用户列表缓存
        self._online_users: List[str] = []
        self._online_user_profiles: Dict[str, Dict[str, str]] = {}
        self._online_users_updated = 0.0
        self._online_users_event = threading.Event()
        self._group_snapshots: List[Dict[str, Any]] = []
        
        # 重连相关
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10  # 最大重连尝试次数，0表示无限重试
        self._reconnect_interval = 3.0  # 重连间隔（秒）
        self._reconnect_backoff_factor = 1.5  # 退避因子
        
        self.logger.info(f"客户端传输初始化完成，服务器地址: {server_address}")
    
    # ==================== Transport接口实现 ====================
    
    def send_message(self, message: Message) -> bool:
        """
        发送消息（网络传输）

        Args:
            message: 消息对象

        Returns:
            bool: 是否成功发送到网络队列
        """
        if not self._connected or not self._registered:
            self.logger.error(f"无法发送消息：连接状态 connected={self._connected}, registered={self._registered}")
            return False
        
        # 验证发送者身份（可选）
        if self._user_id and message.sender_id != self._user_id:
            self.logger.warning(f"消息发送者ID不匹配：消息声明 {message.sender_id}，当前用户 {self._user_id}")
            # 仍然发送，但记录警告
        
        if message.encryption_version:
            self.logger.info(
                f"发送加密消息 [{message.id[:8]}] from {message.sender_id} to {message.receiver_id}, "
                f"密文长度: {len(message.content)}"
            )
        else:
            self.logger.info(
                f"发送消息 [{message.id[:8]}] from {message.sender_id} to {message.receiver_id}, "
                f"payload_len={len(message.content or '')}"
            )
        
        # 创建协议消息（包含消息ID以便接收方使用相同ID）
        protocol_msg = ProtocolMessage.create_message(
            from_user=message.sender_id,
            to_user=message.receiver_id,
            content=message.content,
            message_id=message.id,
            from_display_name=self._display_name,
            metadata=message.metadata,
        )
        
        # 发送到网络线程
        if self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._async_send_message(protocol_msg.to_json()),
                    self._loop
                )
                return True
            except Exception as e:
                self.logger.error(f"提交消息到网络线程失败: {e}")
                return False
        else:
            self.logger.error("网络线程未运行")
            return False

    def sync_groups(self, groups: List[Dict[str, Any]]) -> bool:
        """同步本地群组快照到 Hub。"""
        self._group_snapshots = list(groups or [])
        if not self._connected or not self._registered or not self._user_id:
            return True
        if self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._async_send_group_sync(),
                    self._loop,
                )
                return True
            except Exception as e:
                self.logger.error(f"提交群组同步失败: {e}")
                return False
        return False

    def send_group_update(
        self,
        group: Dict[str, Any],
        members: List[Dict[str, Any]],
        from_user: Optional[str] = None,
    ) -> bool:
        """发送群组/成员更新到 Hub。"""
        if not self._connected or not self._registered or not self._user_id:
            self.logger.error("无法发送群更新：尚未连接或注册")
            return False
        protocol_msg = ProtocolMessage.create_group_update(
            from_user=from_user or self._user_id,
            group=group,
            members=members,
        )
        if self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._async_send_message(protocol_msg.to_json()),
                    self._loop,
                )
                return True
            except Exception as e:
                self.logger.error(f"提交群更新失败: {e}")
                return False
        return False

    def send_group_message(self, message: Message, members: Optional[List[str]] = None) -> bool:
        """发送群消息到 Hub，由 Hub fanout 给成员。"""
        if not self._connected or not self._registered:
            self.logger.error(f"无法发送群消息：连接状态 connected={self._connected}, registered={self._registered}")
            return False
        if self._user_id and message.sender_id != self._user_id:
            self.logger.warning(f"群消息发送者ID不匹配：消息声明 {message.sender_id}，当前用户 {self._user_id}")

        protocol_msg = ProtocolMessage.create_group_message(
            from_user=message.sender_id,
            group_id=message.receiver_id,
            content=message.content,
            message_id=message.id,
            from_display_name=self._display_name,
            metadata=message.metadata,
        )
        if self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._async_send_message(protocol_msg.to_json()),
                    self._loop,
                )
                return True
            except Exception as e:
                self.logger.error(f"提交群消息失败: {e}")
                return False
        self.logger.error("网络线程未运行")
        return False
    
    def register_receiver(self, receiver_id: str, callback: Callable[[Message], None]) -> None:
        """注册消息接收回调"""
        self.logger.info(f"注册接收者: {receiver_id}")
        self._receivers[receiver_id] = callback
    
    def unregister_receiver(self, receiver_id: str) -> None:
        """取消注册消息接收回调"""
        self.logger.info(f"取消注册接收者: {receiver_id}")
        self._receivers.pop(receiver_id, None)
    
    def connect(self) -> bool:
        """
        连接Hub服务器
        
        启动网络线程和异步事件循环。
        
        Returns:
            bool: 是否连接成功
        """
        if self._connected:
            self.logger.warning("传输层已连接")
            return True
        
        if self._connecting:
            self.logger.warning("正在连接中...")
            return False

        # 兼容早期 smoke tests：它们会 patch `_connect_websocket` 来模拟连接。
        legacy_result = self._connect_websocket()
        if legacy_result is None:
            self._connected = True
            self._connecting = False
            return True
        
        self._connecting = True
        
        try:
            # 启动网络线程
            self._running = True
            self._network_thread = threading.Thread(
                target=self._network_thread_func,
                name=f"network_{self.server_address}",
                daemon=True
            )
            self._network_thread.start()
            
            # 等待连接建立（最多5秒）
            for _ in range(50):  # 50 * 0.1 = 5秒
                if self._connected:
                    break
                time.sleep(0.1)
            
            if not self._connected:
                self.logger.error("连接超时")
                self._connecting = False
                return False
            
            self.logger.info("Hub服务器连接成功")
            return True
            
        except Exception as e:
            self.logger.error(f"连接失败: {e}", exc_info=True)
            self._connecting = False
            return False

    def _connect_websocket(self):
        """Legacy no-op hook kept for old transport smoke tests."""
        return False

    def _on_connected(self) -> None:
        self._connected = True
        self._connecting = False
        if self._connection_state_callback:
            self._connection_state_callback("connected")

    def _on_disconnected(self) -> None:
        self._connected = False
        self._connecting = False
        self._registered = False
        if self._connection_state_callback:
            self._connection_state_callback("disconnected")

    def _on_connecting(self) -> None:
        self._connecting = True
        if self._connection_state_callback:
            self._connection_state_callback("connecting")
    
    def disconnect(self) -> None:
        """断开连接"""
        if not self._connected and not self._connecting:
            return
        
        self.logger.info("正在断开传输层连接...")
        self._running = False
        
        # 停止网络线程
        if self._network_thread and self._network_thread.is_alive():
            # 向网络线程发送停止信号
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._async_stop(), self._loop)
            
            # 等待线程结束（最多3秒）
            self._network_thread.join(timeout=3.0)
        
        self._connected = False
        self._connecting = False
        self._registered = False
        self.logger.info("传输层已断开")
    
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected
    
    def get_status(self) -> Dict[str, Any]:
        """获取传输层状态"""
        return {
            "transport_type": "network",
            "connected": self._connected,
            "connecting": self._connecting,
            "registered": self._registered,
            "server_address": self.server_address,
            "user_id": self._user_id,
            "receiver_count": len(self._receivers),
            "online_users": self._online_users.copy(),
            "online_user_profiles": {
                user_id: profile.copy()
                for user_id, profile in self._online_user_profiles.items()
            },
            "device_id": self._profile_extra.get("device_id", ""),
            "device_name": self._profile_extra.get("device_name", ""),
            "online_users_updated": self._online_users_updated,
        }
    
    # ==================== NetworkTransport扩展接口 ====================
    
    def set_server_address(self, address: str) -> None:
        """
        设置服务器地址（Hub地址）
        
        只能在未连接时调用。
        
        Args:
            address: 服务器地址（格式：host:port）
        """
        if self._connected or self._connecting:
            self.logger.error("无法在连接状态下更改服务器地址")
            return
        
        self.server_address = address
        self.logger.info(f"服务器地址已更新: {address}")
    
    def get_online_users(self) -> List[str]:
        """
        获取在线用户列表
        
        返回缓存的在线用户列表。实际在线列表需要从Hub获取，
        当前阶段仅返回已注册用户的列表（模拟）。
        
        Returns:
            List[str]: 在线用户ID列表
        """
        # TODO: Milestone 2实现从Hub获取在线用户列表
        # 当前阶段返回缓存列表
        return self._online_users.copy()

    def get_online_user_profiles(self) -> Dict[str, Dict[str, Any]]:
        """返回缓存的在线用户 profile，键为真实 user_id。"""
        return {
            user_id: profile.copy()
            for user_id, profile in self._online_user_profiles.items()
        }

    def refresh_online_users(self, timeout: float = 2.0) -> Dict[str, Dict[str, str]]:
        """向 Hub 请求最新在线用户目录。"""
        if not self._connected or not self._loop or not self._loop.is_running():
            return self.get_online_user_profiles()

        self._online_users_event.clear()
        request = ProtocolMessage.create_online_users_request()
        try:
            asyncio.run_coroutine_threadsafe(
                self._async_send_message(request.to_json()),
                self._loop,
            )
            self._online_users_event.wait(timeout=timeout)
        except Exception as e:
            self.logger.error(f"刷新在线用户目录失败: {e}")
        return self.get_online_user_profiles()

    def set_user_profile(self, user_id: str, display_name: str = "", profile: Optional[Dict[str, str]] = None) -> None:
        """设置当前客户端 profile，注册与消息发送时会带上 display_name。"""
        self._user_id = (user_id or "").strip() or self._user_id
        self._display_name = (display_name or "").strip()
        self._profile_extra = dict(profile or self._profile_extra or {})

    def update_display_name(self, display_name: str) -> bool:
        """更新当前用户显示名，并同步到 Hub 在线目录。"""
        self._display_name = (display_name or "").strip()
        if not self._user_id:
            return False
        if self._connected and self._loop and self._loop.is_running():
            register_msg = ProtocolMessage.create_register(self._user_id, self._display_name, self._profile_extra)
            try:
                asyncio.run_coroutine_threadsafe(
                    self._async_send_message(register_msg.to_json()),
                    self._loop,
                )
            except Exception as e:
                self.logger.error(f"同步显示名到Hub失败: {e}")
                return False
        return True
    
    def get_connection_quality(self) -> Dict[str, Any]:
        """
        获取连接质量指标
        
        Returns:
            Dict[str, Any]: 连接质量信息
        """
        # TODO: 实现真实的连接质量测量
        return {
            "latency_ms": 0,
            "packet_loss": 0.0,
            "last_heartbeat": time.time() if self._connected else 0,
            "connected_duration": time.time() - self._get_connection_start_time() if self._connected else 0,
            "message_count": 0,  # TODO: 统计消息数量
        }
    
    def set_connection_state_callback(self, callback: Callable[[str], None]) -> None:
        """
        设置连接状态变更回调
        
        Args:
            callback: 回调函数，参数为状态字符串
                    可能的状态："connecting", "connected", "disconnected", "registered"
        """
        self._connection_state_callback = callback

    def set_online_users_callback(self, callback: Callable[[Dict[str, Dict[str, str]]], None]) -> None:
        """设置在线用户目录更新回调。"""
        self._online_users_callback = callback
    
    def register_user(self, user_id: str) -> bool:
        """
        注册用户到Hub
        
        必须在连接成功后调用。
        
        Args:
            user_id: 用户ID
            
        Returns:
            bool: 是否注册成功
        """
        if not self._connected:
            self.logger.error("未连接到Hub，无法注册用户")
            return False
        
        if self._registered:
            self.logger.warning(f"用户 {self._user_id} 已注册，忽略重复注册")
            return True
        
        self.logger.info(f"正在注册用户: {user_id}")
        self._user_id = user_id
        
        # 通过网络线程发送注册消息
        if self._loop and self._loop.is_running():
            # 创建注册消息
            register_msg = ProtocolMessage.create_register(user_id, self._display_name, self._profile_extra)
            
            # 发送到网络线程
            asyncio.run_coroutine_threadsafe(
                self._async_send_message(register_msg.to_json()),
                self._loop
            )
            
            # 等待注册响应（最多10秒）
            for _ in range(100):
                if self._registered:
                    break
                time.sleep(0.1)
            
            if self._registered:
                self.logger.info(f"用户注册成功: {user_id}")
                self._has_registered_once = True
                
                # 更新在线用户列表（至少添加自己；注册响应可能已带完整目录）
                self._apply_online_user_profiles({
                    user_id: {
                        "user_id": user_id,
                        "display_name": self._display_name,
                        **self._profile_extra,
                    }
                }, merge=True)
                
                # 触发回调
                if self._connection_state_callback:
                    self._connection_state_callback("registered")
                
                return True
            else:
                self.logger.error(f"用户注册失败: {user_id}")
                return False
        
        return False
    
    # ==================== 内部方法 ====================
    
    def _network_thread_func(self):
        """网络线程函数"""
        self.logger.info("网络线程启动")
        
        try:
            # 创建新的事件循环
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            
            # 运行异步主函数
            self._loop.run_until_complete(self._async_main())
            
        except Exception as e:
            self.logger.error(f"网络线程异常: {e}", exc_info=True)
        
        finally:
            self.logger.info("网络线程退出")
    
    async def _async_main(self):
        """异步主函数（带自动重连）"""
        while self._running:
            try:
                # 连接服务器
                await self._async_connect()
                
                # 重置重连尝试计数
                self._reconnect_attempts = 0
                
                # 启动心跳任务
                self._heartbeat_task = asyncio.create_task(self._async_heartbeat())
                frame_buffer = ProtocolFrameBuffer()
                
                # 主循环：读取消息
                while self._running:
                    try:
                        # 读取消息
                        if self._reader:
                            data = await self._reader.read(4096)
                            if not data:
                                self.logger.warning("服务器断开连接")
                                break

                            # 处理消息
                            frame_buffer.feed(data)
                            while True:
                                message = frame_buffer.pop_message()
                                if message is None:
                                    break
                                await self._async_process_incoming(message)
                        
                        # 处理待发送消息（当前阶段暂无）
                        # await self._async_process_outgoing()
                        
                        # 短暂休眠，避免CPU占用过高
                        await asyncio.sleep(0.01)
                        
                    except asyncio.CancelledError:
                        break
                    except ConnectionError:
                        self.logger.error("连接错误")
                        break
                    except ValueError as e:
                        frame_buffer.clear()
                        self.logger.error(f"协议解析失败: {e}")
                        break
                    except Exception as e:
                        self.logger.error(f"处理消息时出错: {e}", exc_info=True)
                        await asyncio.sleep(1.0)  # 出错后等待1秒
                
                # 断开连接，准备重连
                await self._async_stop()
                
            except Exception as e:
                self.logger.error(f"连接或主循环异常: {e}", exc_info=True)
                await self._async_stop()
            
            # 检查是否继续重连
            if not self._running:
                break
                
            # 计算重连等待时间（指数退避）
            self._reconnect_attempts += 1
            if self._max_reconnect_attempts > 0 and self._reconnect_attempts > self._max_reconnect_attempts:
                self.logger.error(f"已达到最大重连尝试次数 ({self._max_reconnect_attempts})，停止重连")
                break
            
            wait_time = self._reconnect_interval * (self._reconnect_backoff_factor ** (self._reconnect_attempts - 1))
            self.logger.info(f"等待 {wait_time:.1f} 秒后尝试重连 (尝试 {self._reconnect_attempts}/{self._max_reconnect_attempts if self._max_reconnect_attempts > 0 else '无限'})")
            
            # 等待重连间隔
            try:
                await asyncio.sleep(wait_time)
            except asyncio.CancelledError:
                break
        
        self.logger.info("网络主循环退出")
    
    async def _async_connect(self):
        """异步连接服务器"""
        try:
            host, port_str = self.server_address.split(":")
            port = int(port_str)
            
            self.logger.info(f"正在连接服务器: {host}:{port}")
            
            # 触发连接状态回调
            if self._connection_state_callback:
                self._connection_state_callback("connecting")
            
            # 建立TCP连接
            self._reader, self._writer = await asyncio.open_connection(host, port)
            
            self._connected = True
            self._connecting = False
            
            self.logger.info(f"服务器连接成功: {self.server_address}")
            
            # 触发连接状态回调
            if self._connection_state_callback:
                self._connection_state_callback("connected")
            
            # 自动重新注册（如果之前已注册）
            await self._async_register_after_connect()
            
        except Exception as e:
            self.logger.error(f"连接服务器失败: {e}")
            self._connected = False
            self._connecting = False
            
            # 触发连接状态回调
            if self._connection_state_callback:
                self._connection_state_callback("disconnected")
            
            raise
    
    async def _async_register_after_connect(self):
        """连接成功后自动重新注册（如果之前已注册）"""
        if self._user_id and self._has_registered_once and not self._registered:
            self.logger.info(f"连接成功，自动重新注册用户: {self._user_id}")
            # 发送注册消息
            register_msg = ProtocolMessage.create_register(self._user_id, self._display_name, self._profile_extra)
            await self._async_send_message(register_msg.to_json())
            # 等待注册响应（异步，由_process_incoming处理）
            # 这里不等待，依赖后续心跳响应
     
    async def _async_stop(self):
        """异步停止"""
        # 取消心跳任务
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # 关闭连接
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        
        self._connected = False
        self._connecting = False
        self._registered = False
        
        # 触发连接状态回调
        if self._connection_state_callback:
            self._connection_state_callback("disconnected")
    
    async def _async_send_message(self, message: str):
        """异步发送消息"""
        if self._writer and not self._writer.is_closing():
            try:
                frame = frame_json(message)
                self.logger.debug("发送协议消息: bytes=%s", len(frame))
                self._writer.write(frame)
                await self._writer.drain()
                self.logger.debug("消息发送成功")
                return True
            except Exception as e:
                self.logger.error(f"发送消息失败: {e}")
        
        return False

    async def _async_send_group_sync(self):
        """异步发送群组快照。"""
        if not self._user_id:
            return False
        sync_msg = ProtocolMessage.create_group_sync(self._user_id, self._group_snapshots)
        return await self._async_send_message(sync_msg.to_json())
    
    async def _async_process_incoming(self, data: Any):
        """异步处理接收到的数据"""
        try:
            if isinstance(data, ProtocolMessage):
                message = data
                self.logger.debug(f"收到协议消息类型: {message.type}")
            else:
                data_len = len(data) if hasattr(data, "__len__") else 0
                self.logger.debug("收到原始协议数据: bytes=%s", data_len)
                message = ProtocolMessage.from_wire(data)
            self.logger.debug(f"解析消息类型: {message.type}")
            
            if message.type == MessageType.REGISTER:
                # 注册响应
                status = message.data.get("status")
                user_id = message.data.get("user_id")
                
                if status == "success" and user_id == self._user_id:
                    self._registered = True
                    users = message.data.get("users")
                    if isinstance(users, list):
                        self._apply_online_user_list(users)
                    if self._group_snapshots:
                        await self._async_send_group_sync()
                    if self._connection_state_callback:
                        self._connection_state_callback("registered")
                    self.logger.info(f"收到注册成功响应: {user_id}")
                else:
                    error_msg = message.data.get("error", "unknown")
                    self.logger.error(f"注册失败: {error_msg}")
            
            elif message.type == MessageType.HEARTBEAT:
                # 心跳响应
                user_id = message.data.get("user_id")
                if user_id:
                    self.logger.debug(f"收到心跳响应: {user_id}")
                else:
                    error_msg = message.data.get("error")
                    if error_msg == "not_registered":
                        self.logger.warning("Hub要求重新注册")
                        # TODO: 重新注册逻辑
            
            elif message.type == MessageType.MESSAGE:
                # 消息转发
                from_user = message.get_message_from()
                to_user = message.get_message_to()
                content = message.get_message_content()
                metadata = message.get_message_metadata()
                from_display_name = (message.get_message_from_display_name() or "").strip()
                
                if not from_user or not to_user or content is None:
                    self.logger.warning(f"消息字段缺失: from={from_user}, to={to_user}")
                    return
                
                # 验证接收者是否为当前用户
                if to_user != self._user_id:
                    self.logger.warning(f"消息目标用户不匹配: 预期={self._user_id}, 实际={to_user}")
                    # 仍然处理，因为可能有多用户客户端？
                
                # 创建消息对象
                # 生成会话ID
                conversation_id = Conversation.generate_id([from_user, to_user])
                
                # 提取消息ID（如果协议中包含）
                message_id = message.get_message_id()
                if not message_id:
                    # 如果没有提供消息ID，生成一个（向后兼容）
                    import uuid
                    message_id = str(uuid.uuid4())
                    self.logger.debug(f"协议消息未包含消息ID，生成新ID: {message_id[:8]}")
                
                # 检测消息是否加密（检查content是否为JSON并包含encryption_version字段）
                encryption_version = None
                encrypted_content = None
                
                try:
                    payload_metadata = metadata if metadata else json.loads(content)
                    if isinstance(payload_metadata, dict) and "encryption_version" in payload_metadata:
                        # 这是加密消息
                        encryption_version = payload_metadata.get("encryption_version")
                        encrypted_content = json.dumps(payload_metadata, ensure_ascii=False)
                        # 注意：content字段仍然保持JSON字符串，解密后会被替换
                        self.logger.debug(f"检测到加密消息，版本: {encryption_version}")
                except (json.JSONDecodeError, ValueError):
                    # 不是JSON或解析失败，视为明文
                    pass
                
                msg = Message(
                    id=message_id,
                    content=content,
                    sender_id=from_user,
                    receiver_id=to_user,
                    conversation_id=conversation_id,
                    status=MessageStatus.SENT,  # 假设已送达
                    timestamp=time.time(),
                    encryption_version=encryption_version,
                    encrypted_content=encrypted_content,
                    metadata=metadata,
                )
                setattr(msg, "sender_display_name", from_display_name)
                
                if encryption_version:
                    self.logger.info(f"收到加密消息 from {from_user}, 密文长度: {len(content)}")
                else:
                    self.logger.info(f"收到消息 from {from_user}, payload_len={len(content or '')}")
                
                # 调用注册的回调
                for receiver_id, callback in self._receivers.items():
                    try:
                        callback(msg)
                    except Exception as e:
                        self.logger.error(f"调用接收者回调失败 {receiver_id}: {e}")

            elif message.type == MessageType.GROUP_UPDATE:
                group = message.data.get("group", {})
                members = message.data.get("members", [])
                from_user = message.data.get("from", "")
                if not isinstance(group, dict) or not isinstance(members, list):
                    self.logger.warning("群更新字段格式无效")
                    return

                group_id = str(group.get("id", ""))
                control_msg = Message(
                    content="",
                    sender_id=from_user,
                    receiver_id=self._user_id or "",
                    conversation_id=group_id,
                    status=MessageStatus.SENT,
                    timestamp=time.time(),
                    metadata={
                        "control_type": "group_update",
                        "inviter_id": from_user,
                        "group": group,
                        "members": members,
                    },
                )
                for receiver_id, callback in self._receivers.items():
                    try:
                        callback(control_msg)
                    except Exception as e:
                        self.logger.error(f"调用群更新接收回调失败 {receiver_id}: {e}")

            elif message.type == MessageType.GROUP_MESSAGE:
                from_user = message.get_message_from()
                group_id = message.get_group_id()
                content = message.get_message_content()
                from_display_name = (message.get_message_from_display_name() or "").strip()
                metadata = message.data.get("metadata", {})

                if not from_user or not group_id or content is None:
                    self.logger.warning(f"群消息字段缺失: from={from_user}, group_id={group_id}")
                    return
                if not isinstance(metadata, dict):
                    metadata = {}
                encryption_version = metadata.get("encryption_version")
                encrypted_content = json.dumps(metadata, ensure_ascii=False) if encryption_version else None

                message_id = message.get_message_id()
                if not message_id:
                    import uuid
                    message_id = str(uuid.uuid4())

                msg = Message(
                    id=message_id,
                    content=content,
                    sender_id=from_user,
                    receiver_id=group_id,
                    conversation_id=group_id,
                    status=MessageStatus.SENT,
                    message_type=ModelMessageType.FILE if metadata.get("schema") == "file_event_v1" else ModelMessageType.TEXT,
                    timestamp=time.time(),
                    encryption_version=encryption_version,
                    encrypted_content=encrypted_content,
                    metadata={
                        **metadata,
                        "conversation_type": "group",
                        "group_id": group_id,
                        "encryption_scope": metadata.get("encryption_scope", "group_plain_v3"),
                        "sync_status": metadata.get("sync_status", "reserved"),
                    },
                )
                setattr(msg, "sender_display_name", from_display_name)
                for receiver_id, callback in self._receivers.items():
                    try:
                        callback(msg)
                    except Exception as e:
                        self.logger.error(f"调用群消息接收回调失败 {receiver_id}: {e}")

            elif message.type == MessageType.ONLINE_USERS:
                users = message.data.get("users", [])
                if isinstance(users, list):
                    self._apply_online_user_list(users)
                self._online_users_event.set()
            
            else:
                self.logger.warning(f"收到未知类型的消息: {message.type}")
        
        except ValueError as e:
            self.logger.error(f"解析协议消息失败: {e}")
        except Exception as e:
            self.logger.error(f"处理接收数据时出错: {e}", exc_info=True)
    
    async def _async_heartbeat(self):
        """异步心跳任务"""
        self.logger.info("心跳任务启动")
        
        while self._running and self._connected:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                
                if not self._running or not self._connected:
                    break
                
                if self._registered and self._user_id:
                    # 发送心跳
                    heartbeat_msg = ProtocolMessage.create_heartbeat(
                        self._user_id,
                        self._profile_extra.get("device_id", ""),
                    )
                    await self._async_send_message(heartbeat_msg.to_json())
                    self.logger.debug(f"发送心跳: {self._user_id}")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"心跳任务出错: {e}")
                await asyncio.sleep(1.0)
        
        self.logger.info("心跳任务退出")

    def _apply_online_user_list(self, users: List[Dict[str, Any]]) -> None:
        """用 Hub 返回的在线用户列表替换本地缓存。"""
        profiles: Dict[str, Dict[str, Any]] = {}
        for item in users:
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("user_id", "")).strip()
            if not user_id:
                continue
            device_profile = {
                "user_id": user_id,
                "display_name": str(item.get("display_name", "") or "").strip(),
                "device_id": str(item.get("device_id", "") or "").strip(),
                "device_name": str(item.get("device_name", "") or "").strip(),
                "device_public_key": str(item.get("device_public_key", "") or "").strip(),
                "device_fingerprint": str(item.get("device_fingerprint", "") or "").strip(),
                "client_version": str(item.get("client_version", "") or "").strip(),
            }
            existing = profiles.setdefault(user_id, {
                "user_id": user_id,
                "display_name": device_profile["display_name"],
                "devices": [],
            })
            existing.setdefault("devices", [])
            existing["devices"].append(device_profile)
            if not existing.get("device_id"):
                existing.update(device_profile)
        self._apply_online_user_profiles(profiles, merge=False)

    def _apply_online_user_profiles(self, profiles: Dict[str, Dict[str, Any]], merge: bool = False) -> None:
        """更新在线用户缓存。"""
        if merge:
            merged = self._online_user_profiles.copy()
            merged.update(profiles)
            self._online_user_profiles = merged
        else:
            self._online_user_profiles = profiles
        self._online_users = sorted(self._online_user_profiles.keys())
        self._online_users_updated = time.time()
        if self._online_users_callback:
            try:
                self._online_users_callback(self.get_online_user_profiles())
            except Exception as e:
                self.logger.error(f"在线用户目录回调失败: {e}", exc_info=True)
    
    def _get_connection_start_time(self) -> float:
        """获取连接开始时间（简单实现）"""
        # TODO: 记录实际的连接开始时间
        return time.time() - 60  # 假设连接了60秒
    
    def _process_incoming_messages(self):
        """处理接收队列中的消息（主线程调用）"""
        try:
            while not self._incoming_queue.empty():
                message = self._incoming_queue.get_nowait()
                
                # TODO: 处理接收到的消息（Milestone 2）
                self.logger.debug(f"收到消息: {message}")
                
                self._incoming_queue.task_done()
        
        except Empty:
            pass
        except Exception as e:
            self.logger.error(f"处理接收消息时出错: {e}")


# 兼容性别名
NetworkTransport = ClientTransport
