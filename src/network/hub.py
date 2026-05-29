"""
Hub主程序

TCP服务器，作为客户端之间的中转节点。
支持客户端连接、用户注册、心跳维持。
"""

import asyncio
import logging
import argparse
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, List, Set, Any

from .connection_manager import ConnectionManager, ClientConnection
from .discovery import DEFAULT_DISCOVERY_PORT, HubDiscoveryInfo, HubDiscoveryService
from .hub_runtime import mark_runtime_stopped, write_runtime_marker
from .hub_storage import HubStorage
from .protocol import ProtocolMessage, MessageType, HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT, ProtocolFrameBuffer
from .temp_files import (
    DEFAULT_TEMP_FILE_MAX_BYTES,
    DEFAULT_TEMP_FILE_TTL_SECONDS,
    TempFileHTTPServer,
    TempFileStore,
)


class HubServer:
    """Hub服务器"""
    
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        hub_dir: str = "runtime/hub",
        hub_db_path: Optional[str] = None,
        hub_name: str = "Team Chat Hub",
        temp_file_dir: str = "runtime/hub/temp_files",
        temp_file_ttl_seconds: int = DEFAULT_TEMP_FILE_TTL_SECONDS,
        temp_file_max_bytes: int = DEFAULT_TEMP_FILE_MAX_BYTES,
        temp_file_port: Optional[int] = None,
        discovery_port: int = DEFAULT_DISCOVERY_PORT,
        enable_discovery: bool = True,
    ):
        """
        初始化Hub服务器
        
        Args:
            host: 监听主机
            port: 监听端口
        """
        self.host = host
        self.port = port
        self.hub_dir = Path(hub_dir or "runtime/hub")
        self.hub_name = (hub_name or "Team Chat Hub").strip()
        self.hub_id = self._load_or_create_hub_id()
        self.logger = logging.getLogger("hub")
        self._temp_file_port_requested = temp_file_port
        self.temp_file_port = int(temp_file_port or (0 if port == 0 else port + 1))
        self.temp_file_store = TempFileStore(
            temp_file_dir,
            ttl_seconds=temp_file_ttl_seconds,
            max_bytes=temp_file_max_bytes,
        )
        self.temp_file_http = TempFileHTTPServer(host, self.temp_file_port, self.temp_file_store)
        self.storage = HubStorage(str(self.hub_dir), db_path=hub_db_path)
        self.discovery_port = int(discovery_port or DEFAULT_DISCOVERY_PORT)
        self.enable_discovery = bool(enable_discovery)
        self.discovery_service: Optional[HubDiscoveryService] = None
        
        # 连接管理器
        self.connection_manager = ConnectionManager(heartbeat_timeout=HEARTBEAT_TIMEOUT)
        
        # 服务器对象
        self.server: Optional[asyncio.Server] = None
        
        # 运行标志
        self._running = False
        self._cleanup_task: Optional[asyncio.Task] = None
        
        # 离线消息存储（内存暂存）
        self._offline_messages: Dict[str, List[ProtocolMessage]] = {}
        self._max_offline_messages_per_user = 100
        
        # 第三阶段群聊内存路由表：可靠源仍是客户端本地 SQLite，Hub 重启后由 group_sync 恢复。
        self._groups: Dict[str, Dict[str, Any]] = {}
        self._group_members: Dict[str, Set[str]] = {}
        
        self.logger.info(f"Hub服务器初始化完成，地址: {host}:{port}")

    def _load_or_create_hub_id(self) -> str:
        self.hub_dir.mkdir(parents=True, exist_ok=True)
        path = self.hub_dir / "hub_id"
        if path.exists():
            loaded = path.read_text(encoding="utf-8").strip()
            if loaded:
                return loaded
        hub_id = "hub_" + uuid.uuid4().hex[:16]
        path.write_text(hub_id, encoding="utf-8")
        return hub_id
    
    async def start(self) -> bool:
        """
        启动服务器
        
        Returns:
            bool: 是否启动成功
        """
        try:
            self.server = await asyncio.start_server(
                self.handle_client,
                self.host,
                self.port
            )
            
            addr = self.server.sockets[0].getsockname()
            self.port = int(addr[1])
            if self._temp_file_port_requested is None and self.temp_file_port == 0:
                self.temp_file_http.port = 0
            self.logger.info(f"Hub服务器启动，监听地址: {addr[0]}:{addr[1]}")
            
            self._running = True
            self.temp_file_store.cleanup_expired()
            self.temp_file_http.start()
            self.temp_file_port = int(self.temp_file_http.port)
            self.logger.info(
                "Hub临时文件服务启动: http://%s:%s, dir=%s, ttl=%ss, max=%s bytes",
                self.host,
                self.temp_file_port,
                self.temp_file_store.root_dir,
                self.temp_file_store.ttl_seconds,
                self.temp_file_store.max_bytes,
            )
            if self.enable_discovery:
                self.discovery_service = HubDiscoveryService(
                    HubDiscoveryInfo(
                        hub_id=self.hub_id,
                        hub_name=self.hub_name,
                        host=self.host,
                        port=self.port,
                        temp_file_port=self.temp_file_port,
                        version="1.0-rc-phase11",
                        started_at=time.time(),
                        discovery_port=self.discovery_port,
                    )
                )
                self.discovery_service.start()
                self.logger.info("Hub UDP发现服务启动: port=%s", self.discovery_port)

            write_runtime_marker(
                self.hub_dir,
                hub_id=self.hub_id,
                host=self.host,
                port=self.port,
                temp_file_port=self.temp_file_port,
                discovery_port=self.discovery_port if self.enable_discovery else 0,
            )
            
            # 启动清理任务
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            
            return True
            
        except Exception as e:
            self.logger.error(f"服务器启动失败: {e}")
            return False
    
    async def stop(self):
        """停止服务器"""
        self.logger.info("正在停止Hub服务器...")
        self._running = False
        
        # 取消清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # 关闭服务器
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.logger.info("服务器已关闭")
        self.temp_file_http.stop()
        self.logger.info("临时文件服务已关闭")
        if self.discovery_service:
            self.discovery_service.stop()
            self.logger.info("Hub UDP发现服务已关闭")
        
        # 断开所有客户端连接
        try:
            all_connections = await self.connection_manager.get_all_connections()
            for conn in all_connections:
                try:
                    conn.writer.close()
                    await conn.writer.wait_closed()
                except Exception:
                    pass
            
            self.logger.info(f"已断开 {len(all_connections)} 个客户端连接")
        except Exception as e:
            self.logger.error(f"断开客户端连接时出错: {e}")
        try:
            self.storage.close()
        except Exception:
            pass
        try:
            mark_runtime_stopped(self.hub_dir)
        except Exception:
            pass
        
        self.logger.info("Hub服务器已停止")
    
    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """
        处理客户端连接
        
        Args:
            reader: 流读取器
            writer: 流写入器
        """
        client_addr = writer.get_extra_info('peername')
        self.logger.info(f"新客户端连接: {client_addr}")
        
        # 创建连接对象
        connection = ClientConnection(
            writer=writer,
            connected_at=time.time(),
            last_heartbeat=time.time()
        )
        frame_buffer = ProtocolFrameBuffer()
        
        try:
            while self._running:
                try:
                    # 读取数据
                    try:
                        data = await reader.read(4096)
                        if not data:
                            # 连接关闭
                            self.logger.info(f"客户端断开连接: {client_addr}")
                            break
                    except asyncio.CancelledError:
                        break
                    except ConnectionError:
                        self.logger.info(f"客户端连接异常断开: {client_addr}")
                        break
                    except Exception as e:
                        self.logger.error(f"读取数据时出错: {e}", exc_info=True)
                        break
                    
                    # 解析协议消息
                    try:
                        frame_buffer.feed(data)
                        while True:
                            message = frame_buffer.pop_message()
                            if message is None:
                                break
                            await self.process_message(message, connection, client_addr)
                    except ValueError as e:
                        self.logger.error(f"协议解析失败: {e}")
                        frame_buffer.clear()
                        # 发送错误响应
                        error_msg = ProtocolMessage(MessageType.HEARTBEAT, {"error": "invalid_message"})
                        writer.write(error_msg.to_bytes())
                        await writer.drain()
                        
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"处理客户端数据时出错: {e}", exc_info=True)
                    break
        
        except Exception as e:
            self.logger.error(f"处理客户端时发生未捕获异常: {e}", exc_info=True)
        
        finally:
            # 清理连接
            await self.cleanup_connection(connection, client_addr)
    
    def _offline_key(self, user_id: str, device_id: str = "") -> str:
        return f"{user_id}:{device_id or '*'}"

    def _message_target_device_id(self, message: ProtocolMessage) -> str:
        metadata = message.get_message_metadata()
        return str(
            metadata.get("recipient_device_id")
            or message.data.get("target_device_id")
            or message.data.get("recipient_device_id")
            or ""
        ).strip()

    def _safe_to_persist_offline(self, message: ProtocolMessage) -> bool:
        if message.type == MessageType.GROUP_UPDATE:
            return True
        metadata = message.get_message_metadata()
        if metadata.get("encryption_version") or metadata.get("ciphertext"):
            return True
        if message.type == MessageType.MESSAGE:
            content = message.get_message_content() or ""
            return "Encrypted message" in content
        return False

    async def _store_offline_message(
        self,
        user_id: str,
        message: ProtocolMessage,
        target_device_id: str = "",
    ) -> None:
        """
        存储离线消息
        
        Args:
            user_id: 目标用户ID
            message: 协议消息
        """
        if self._safe_to_persist_offline(message):
            try:
                queue_id = self.storage.enqueue_offline(
                    target_user_id=user_id,
                    target_device_id=target_device_id,
                    payload_json=message.to_json(),
                )
                self.logger.info(
                    "为离线用户存储密文/控制消息: user=%s, device=%s, queue_id=%s",
                    user_id,
                    target_device_id or "*",
                    queue_id,
                )
                return
            except Exception as e:
                self.logger.error("Hub离线密文队列写入失败: %s", e)

        offline_key = self._offline_key(user_id, target_device_id)
        if offline_key not in self._offline_messages:
            self._offline_messages[offline_key] = []
        
        queue = self._offline_messages[offline_key]
        # 检查队列长度限制
        if len(queue) >= self._max_offline_messages_per_user:
            self.logger.warning(f"用户 {user_id} 的内存离线队列已满 ({self._max_offline_messages_per_user} 条)，丢弃最早的消息")
            queue.pop(0)
        
        queue.append(message)
        self.logger.info(f"为用户 {user_id} 暂存内存离线消息，device={target_device_id or '*'}, 当前队列长度: {len(queue)}")
    
    async def _deliver_offline_messages(self, user_id: str, connection: ClientConnection, device_id: str = "") -> None:
        """
        向用户投递离线消息
        
        Args:
            user_id: 用户ID
            connection: 用户连接
        """
        persisted = self.storage.pending_offline(user_id, device_id)
        persisted_delivered = 0
        for item in persisted:
            try:
                message = ProtocolMessage.from_json(str(item.get("payload_json") or "{}"))
                connection.writer.write(message.to_bytes())
                await connection.writer.drain()
                self.storage.mark_offline_delivered(str(item.get("id") or ""))
                persisted_delivered += 1
            except Exception as e:
                self.logger.error(f"持久离线消息投递失败: {e}")
                break
        if persisted_delivered:
            self.logger.info(
                "向用户 %s/%s 投递持久离线消息 %s 条",
                user_id,
                device_id or "*",
                persisted_delivered,
            )

        offline_key = self._offline_key(user_id, device_id)
        fallback_key = self._offline_key(user_id, "")
        memory_messages = []
        for key in {fallback_key, offline_key}:
            memory_messages.extend(self._offline_messages.get(key, []))
        if not memory_messages:
            return
        
        queue = memory_messages
        if not queue:
            return
        
        self.logger.info(f"向用户 {user_id} 投递 {len(queue)} 条离线消息")
        
        delivered_count = 0
        for msg in queue:
            try:
                connection.writer.write(msg.to_bytes())
                await connection.writer.drain()
                delivered_count += 1
                self.logger.debug(f"离线消息投递成功: {user_id}")
            except Exception as e:
                self.logger.error(f"离线消息投递失败: {e}")
                # 如果投递失败，停止投递（连接可能已断开）
                break
        
        # 移除已投递的消息
        if delivered_count > 0:
            # 保留未投递的消息（如果有）
            if delivered_count < len(queue):
                self._offline_messages[offline_key] = queue[delivered_count:]
                self.logger.warning(f"用户 {user_id} 仍有 {len(queue) - delivered_count} 条离线消息未投递")
            else:
                self._offline_messages.pop(offline_key, None)
                self._offline_messages.pop(fallback_key, None)
                self.logger.info(f"用户 {user_id} 的所有离线消息已投递完成")

    def _apply_group_payload(self, group: Dict[str, Any], members: List[Dict[str, Any]]) -> Optional[str]:
        """更新 Hub 内存群路由表，返回 group_id。"""
        if not isinstance(group, dict) or not isinstance(members, list):
            return None
        group_id = str(group.get("id", "")).strip()
        if not group_id:
            return None
        active_members = {
            str(member.get("user_id", "")).strip()
            for member in members
            if isinstance(member, dict)
            and str(member.get("status", "active")) == "active"
            and str(member.get("user_id", "")).strip()
        }
        self._groups[group_id] = group
        self._group_members[group_id] = active_members
        self.logger.info(f"Hub 群路由更新: {group_id}, members={sorted(active_members)}")
        return group_id

    def _get_group_update_recipients(self, members: List[Dict[str, Any]]) -> Set[str]:
        """群更新需要通知 active 和 invited 成员；群消息只走 active。"""
        return {
            str(member.get("user_id", "")).strip()
            for member in members
            if isinstance(member, dict)
            and str(member.get("status", "active")) in {"active", "invited"}
            and str(member.get("user_id", "")).strip()
        }

    async def _fanout_to_users(
        self,
        target_user_ids: Set[str],
        protocol_message: ProtocolMessage,
        exclude_user_id: Optional[str] = None,
    ) -> None:
        """向多个用户 fanout 协议消息，不在线则进入离线队列。"""
        for target_user_id in sorted(target_user_ids):
            if exclude_user_id and target_user_id == exclude_user_id:
                continue
            target_connections = await self.connection_manager.get_connections(target_user_id)
            if not target_connections:
                await self._store_offline_message(target_user_id, protocol_message)
                continue
            for target_conn in target_connections:
                try:
                    target_conn.writer.write(protocol_message.to_bytes())
                    await target_conn.writer.drain()
                except Exception as e:
                    self.logger.error(f"fanout 消息失败 {target_user_id}/{target_conn.device_id}: {e}")

    async def _broadcast_online_users(self) -> None:
        """向所有已注册连接广播最新在线目录。"""
        if not self._running:
            return
        response = ProtocolMessage.create_online_users_response(
            await self._get_online_user_profile_list()
        )
        sent_count = await self.connection_manager.broadcast(response.to_wire())
        self.logger.info(f"在线用户目录已广播: receivers={sent_count}")
    
    async def process_message(self, message: ProtocolMessage, connection: ClientConnection, client_addr: tuple):
        """
        处理协议消息
        
        Args:
            message: 协议消息
            connection: 客户端连接
            client_addr: 客户端地址
        """
        self.logger.debug(f"处理消息类型: {message.type}, 用户ID: {message.get_user_id()}, 时间戳: {message.timestamp}")
        user_id = message.get_user_id()
        
        if message.type == MessageType.REGISTER:
            # 注册消息
            if not user_id:
                self.logger.warning(f"收到无用户ID的注册消息: {client_addr}")
                return

            display_name = (message.data.get("display_name") or "").strip()
            profile = {
                "device_id": str(message.data.get("device_id") or "").strip(),
                "device_name": str(message.data.get("device_name") or "").strip(),
                "device_public_key": str(message.data.get("device_public_key") or "").strip(),
                "device_fingerprint": str(message.data.get("device_fingerprint") or "").strip(),
                "client_version": str(message.data.get("client_version") or "").strip(),
            }
            
            # 注册用户
            success = await self.connection_manager.register(user_id, connection, display_name, profile)
            _, registered_device_id = await self.connection_manager.get_user_device(connection.writer)
            profile["device_id"] = registered_device_id or profile["device_id"]
            profile["user_id"] = user_id
            profile["display_name"] = display_name
            device_status = self.storage.upsert_device(profile)
            
            if success:
                self.logger.info(
                    "用户设备注册成功: user=%s, device=%s, status=%s, addr=%s, display_name=%s",
                    user_id,
                    profile.get("device_id", ""),
                    device_status,
                    client_addr,
                    display_name or "<empty>",
                )
                
                # 发送注册成功响应
                response = ProtocolMessage(MessageType.REGISTER, {
                    "user_id": user_id,
                    "device_id": profile.get("device_id", ""),
                    "device_name": profile.get("device_name", ""),
                    "device_status": device_status,
                    "display_name": display_name,
                    "status": "success",
                    "users": await self._get_online_user_profile_list(),
                })
                connection.writer.write(response.to_bytes())
                await connection.writer.drain()
                
                # 投递离线消息
                await self._deliver_offline_messages(user_id, connection, profile.get("device_id", ""))
                await self._broadcast_online_users()
            else:
                self.logger.error(f"用户注册失败: {user_id}")
        
        elif message.type == MessageType.HEARTBEAT:
            # 心跳消息
            if user_id:
                # 更新心跳
                device_id = str(message.data.get("device_id") or "").strip()
                success = await self.connection_manager.update_heartbeat(user_id, time.time(), device_id)
                if not success:
                    success = await self.connection_manager.update_heartbeat_by_writer(connection.writer, time.time())
                if success:
                    # 发送心跳响应
                    _, heartbeat_device_id = await self.connection_manager.get_user_device(connection.writer)
                    response = ProtocolMessage(MessageType.HEARTBEAT, {
                        "user_id": user_id,
                        "device_id": heartbeat_device_id,
                        "timestamp": time.time()
                    })
                    connection.writer.write(response.to_bytes())
                    await connection.writer.drain()
                else:
                    # 用户未注册，要求重新注册
                    self.logger.warning(f"收到未注册用户的心跳: {user_id}")
                    response = ProtocolMessage(MessageType.HEARTBEAT, {
                        "error": "not_registered",
                        "required_action": "register"
                    })
                    connection.writer.write(response.to_bytes())
                    await connection.writer.drain()
            else:
                self.logger.warning(f"收到无用户ID的心跳消息: {client_addr}")

        elif message.type == MessageType.ONLINE_USERS:
            response = ProtocolMessage.create_online_users_response(
                await self._get_online_user_profile_list()
            )
            connection.writer.write(response.to_bytes())
            await connection.writer.drain()

        elif message.type == MessageType.GROUP_SYNC:
            sender_id = await self.connection_manager.get_user_id(connection.writer)
            if not sender_id:
                self.logger.warning(f"收到未注册连接的群同步: {client_addr}")
                return
            if user_id and user_id != sender_id:
                self.logger.warning(f"群同步身份不匹配: 连接={sender_id}, 消息={user_id}")
                return

            groups = message.data.get("groups", [])
            if not isinstance(groups, list):
                self.logger.warning("群同步字段 groups 无效")
                return
            synced_count = 0
            for item in groups:
                if not isinstance(item, dict):
                    continue
                group = item.get("group", {})
                members = item.get("members", [])
                if self._apply_group_payload(group, members):
                    synced_count += 1
            self.logger.info(f"用户 {sender_id} 同步群组快照完成: {synced_count}")

        elif message.type == MessageType.GROUP_UPDATE:
            sender_id = await self.connection_manager.get_user_id(connection.writer)
            if not sender_id:
                self.logger.warning(f"收到未注册连接的群更新: {client_addr}")
                return
            from_user = message.data.get("from")
            if from_user != sender_id:
                self.logger.warning(f"群更新发送者身份不匹配: 连接={sender_id}, 消息声明={from_user}")
                return

            group = message.data.get("group", {})
            members = message.data.get("members", [])
            group_id = self._apply_group_payload(group, members)
            if not group_id:
                self.logger.warning("群更新字段缺失")
                return

            await self._fanout_to_users(
                self._get_group_update_recipients(members),
                message,
                exclude_user_id=sender_id,
            )
            self.logger.info(f"群更新已 fanout: {group_id}")

        elif message.type == MessageType.GROUP_MESSAGE:
            sender_id = await self.connection_manager.get_user_id(connection.writer)
            if not sender_id:
                self.logger.warning(f"收到未注册连接的群消息: {client_addr}")
                return
            from_user = message.data.get("from")
            group_id = message.data.get("group_id")
            content = message.data.get("content")
            if not from_user or not group_id or content is None:
                self.logger.warning(f"群消息字段缺失: from={from_user}, group_id={group_id}")
                return
            if from_user != sender_id:
                self.logger.warning(f"群消息发送者身份不匹配: 连接={sender_id}, 消息声明={from_user}")
                return

            members = self._group_members.get(group_id, set())
            if sender_id not in members:
                self.logger.warning(f"非群成员尝试发送群消息: sender={sender_id}, group={group_id}")
                return

            await self._fanout_to_users(members, message, exclude_user_id=sender_id)
            self.logger.info(f"群消息 fanout 成功: {from_user} -> {group_id}, members={len(members)}")
        
        elif message.type == MessageType.MESSAGE:
            # 消息转发
            # 验证发送者身份
            sender_id = await self.connection_manager.get_user_id(connection.writer)
            if not sender_id:
                self.logger.warning(f"收到未注册连接的消息: {client_addr}")
                return
            
            # 提取消息字段
            from_user = message.get_message_from()
            to_user = message.get_message_to()
            content = message.get_message_content()
            
            if not from_user or not to_user or content is None:
                self.logger.warning(f"消息字段缺失: from={from_user}, to={to_user}, content={content}")
                return
            
            # 验证 from 字段与连接身份一致
            if from_user != sender_id:
                self.logger.warning(f"消息发送者身份不匹配: 连接={sender_id}, 消息声明={from_user}")
                return
            
            target_device_id = self._message_target_device_id(message)
            target_connections = await self.connection_manager.get_connections(to_user, target_device_id)
            if not target_connections:
                self.logger.warning(f"目标用户设备不在线: {to_user}/{target_device_id or '*'}，消息暂存")
                await self._store_offline_message(to_user, message, target_device_id=target_device_id)
                return
            
            # 转发消息给接收者
            sent_count = 0
            for target_conn in target_connections:
                try:
                    target_conn.writer.write(message.to_bytes())
                    await target_conn.writer.drain()
                    sent_count += 1
                except Exception as e:
                    self.logger.error(f"转发消息失败: {to_user}/{target_conn.device_id}: {e}")
            self.logger.info(
                "消息转发完成: from=%s, to=%s, target_device=%s, receivers=%s, payload_len=%s",
                from_user,
                to_user,
                target_device_id or "*",
                sent_count,
                len(content),
            )
        
        else:
            self.logger.warning(f"收到未知类型的消息: {message.type}")

    async def _get_online_user_profile_list(self) -> List[Dict[str, str]]:
        """返回在线设备目录；同一 user_id 可出现多条 device 记录。"""
        profiles = await self.connection_manager.get_all_device_profiles()
        return [
            {
                "user_id": profile.get("user_id", ""),
                "display_name": profile.get("display_name", ""),
                "device_id": profile.get("device_id", ""),
                "device_name": profile.get("device_name", ""),
                "device_public_key": profile.get("device_public_key", ""),
                "device_fingerprint": profile.get("device_fingerprint", ""),
                "client_version": profile.get("client_version", ""),
            }
            for profile in sorted(profiles, key=lambda item: (item.get("user_id", ""), item.get("device_id", "")))
        ]
    
    async def cleanup_connection(self, connection: ClientConnection, client_addr: tuple):
        """
        清理客户端连接
        
        Args:
            connection: 客户端连接
            client_addr: 客户端地址
        """
        try:
            # 从连接管理器中移除
            user_id = await self.connection_manager.get_user_id(connection.writer)
            if user_id:
                await self.connection_manager.unregister(user_id)
                self.logger.info(f"用户 {user_id} 连接清理完成: {client_addr}")
                await self._broadcast_online_users()
            else:
                # 尝试通过writer移除
                removed_user_id = await self.connection_manager.unregister_by_writer(connection.writer)
                self.logger.info(f"未注册用户连接清理完成: {client_addr}")
                if removed_user_id:
                    await self._broadcast_online_users()
        
        except Exception as e:
            self.logger.error(f"清理连接时出错: {e}")
        
        finally:
            # 关闭连接
            try:
                connection.writer.close()
                await connection.writer.wait_closed()
            except Exception:
                pass
    
    async def _periodic_cleanup(self):
        """定期清理过期连接"""
        self.logger.info("启动定期清理任务")
        
        while self._running:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL * 2)  # 每60秒清理一次
                
                if not self._running:
                    break
                
                current_time = time.time()
                stale_users = await self.connection_manager.cleanup_stale_connections(current_time)
                
                if stale_users:
                    self.logger.info(f"清理了 {len(stale_users)} 个过期连接: {stale_users}")
                    await self._broadcast_online_users()
                else:
                    self.logger.debug("没有过期连接需要清理")
                removed_temp_files = self.temp_file_store.cleanup_expired()
                if removed_temp_files:
                    self.logger.info(f"清理了 {removed_temp_files} 个过期临时文件")
                
                # 报告当前状态
                user_count = await self.connection_manager.get_registered_count()
                self.logger.info(f"当前在线用户: {user_count}")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"定期清理任务出错: {e}")
        
        self.logger.info("定期清理任务已停止")
    
    async def get_status(self) -> dict:
        """
        获取服务器状态
        
        Returns:
            dict: 状态信息
        """
        user_count = await self.connection_manager.get_registered_count()
        all_users = await self.connection_manager.get_all_users()
        all_profiles = await self.connection_manager.get_all_user_profiles()
        all_devices = await self.connection_manager.get_all_device_profiles()
        
        return {
            "host": self.host,
            "port": self.port,
            "hub_id": self.hub_id,
            "hub_name": self.hub_name,
            "hub_dir": str(self.hub_dir),
            "running": self._running,
            "connection_count": user_count,
            "user_count": len(all_users),
            "users": list(all_users),
            "user_profiles": all_profiles,
            "devices": all_devices,
            "groups": {
                group_id: sorted(member_ids)
                for group_id, member_ids in self._group_members.items()
            },
            "storage": self.storage.status(str(self.temp_file_store.root_dir)),
            "temp_files": self.temp_file_store.status(),
            "temp_file_port": self.temp_file_port,
            "discovery_port": self.discovery_port if self.enable_discovery else 0,
            "timestamp": time.time()
        }


def setup_logging(level=logging.INFO):
    """设置日志"""
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="Hub服务器")
    parser.add_argument("--host", type=str, default="0.0.0.0",
                        help="监听主机 (默认: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080,
                        help="监听端口 (默认: 8080)")
    parser.add_argument("--hub-dir", type=str, default="runtime/hub",
                        help="Hub本地状态目录 (默认: runtime/hub)")
    parser.add_argument("--hub-db-path", type=str,
                        help="Hub本地SQLite路径 (默认: <hub-dir>/hub.db)")
    parser.add_argument("--hub-name", type=str, default="Team Chat Hub",
                        help="局域网发现显示名称")
    parser.add_argument("--temp-file-dir", type=str,
                        help="临时密文文件目录 (默认: runtime/hub/temp_files)")
    parser.add_argument("--temp-file-ttl-seconds", type=int, default=DEFAULT_TEMP_FILE_TTL_SECONDS,
                        help="临时文件TTL秒数 (默认: 1800)")
    parser.add_argument("--temp-file-max-bytes", type=int, default=DEFAULT_TEMP_FILE_MAX_BYTES,
                        help="临时文件密文大小上限 (默认: 25MB)")
    parser.add_argument("--temp-file-port", type=int,
                        help="临时文件HTTP服务端口 (默认: Hub端口+1)")
    parser.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT,
                        help="UDP局域网发现端口 (默认: 8090)")
    parser.add_argument("--disable-discovery", action="store_true",
                        help="禁用UDP局域网发现")
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="日志级别 (默认: INFO)")
    
    return parser.parse_args()


async def main_async():
    """异步主函数"""
    args = parse_args()
    
    # 设置日志
    log_level = getattr(logging, args.log_level)
    setup_logging(log_level)
    
    logger = logging.getLogger("hub")
    logger.info(f"启动Hub服务器，主机: {args.host}，端口: {args.port}")
    
    # 创建服务器
    server = HubServer(
        host=args.host,
        port=args.port,
        hub_dir=args.hub_dir,
        hub_db_path=args.hub_db_path,
        hub_name=args.hub_name,
        temp_file_dir=args.temp_file_dir or str(Path(args.hub_dir) / "temp_files"),
        temp_file_ttl_seconds=args.temp_file_ttl_seconds,
        temp_file_max_bytes=args.temp_file_max_bytes,
        temp_file_port=args.temp_file_port,
        discovery_port=args.discovery_port,
        enable_discovery=not args.disable_discovery,
    )
    
    # 设置信号处理
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    
    def signal_handler():
        logger.info("收到停止信号，正在关闭服务器...")
        stop_event.set()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        # 启动服务器
        if not await server.start():
            logger.error("服务器启动失败")
            return 1
        
        # 等待停止信号
        await stop_event.wait()
        
        # 停止服务器
        await server.stop()
        
        logger.info("Hub服务器正常退出")
        return 0
        
    except Exception as e:
        logger.error(f"服务器运行异常: {e}", exc_info=True)
        return 1


def main():
    """主函数"""
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n收到中断信号，退出")
        return 0


if __name__ == "__main__":
    sys.exit(main())
