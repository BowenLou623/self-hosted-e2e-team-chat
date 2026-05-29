"""
连接管理器

管理用户ID到网络连接的映射，支持并发安全的连接管理。
"""

import asyncio
import logging
from typing import Dict, Set, Optional, Any, List, Tuple
from dataclasses import dataclass


@dataclass
class ClientConnection:
    """客户端连接信息"""
    writer: asyncio.StreamWriter
    user_id: Optional[str] = None  # 注册后的用户ID
    device_id: str = ""
    device_name: str = ""
    display_name: str = ""  # 仅用于在线目录显示
    profile: Dict[str, str] = None
    last_heartbeat: float = 0.0
    connected_at: float = 0.0
    
    def is_registered(self) -> bool:
        """是否已注册（有用户ID）"""
        return self.user_id is not None
    
    def update_heartbeat(self, timestamp: float):
        """更新心跳时间"""
        self.last_heartbeat = timestamp


class ConnectionManager:
    """连接管理器
    
    线程安全地管理客户端连接，支持：
    1. 注册用户ID到连接的映射
    2. 根据用户ID查找连接
    3. 获取所有在线用户
    4. 清理无效连接
    """
    
    def __init__(self, heartbeat_timeout: float = 90.0):
        """
        初始化连接管理器
        
        Args:
            heartbeat_timeout: 心跳超时时间（秒），默认90秒
        """
        self.heartbeat_timeout = heartbeat_timeout
        self.logger = logging.getLogger("connection_manager")
        
        # 连接映射：online_key(user_id:device_id) -> ClientConnection
        self._connections: Dict[str, ClientConnection] = {}
        
        # 反向映射：writer -> online_key（用于快速查找）
        self._writer_to_key: Dict[asyncio.StreamWriter, str] = {}
        
        # 锁确保并发安全
        self._lock = asyncio.Lock()
        
        self.logger.info(f"连接管理器初始化完成，心跳超时: {heartbeat_timeout}秒")
    
    async def register(
        self,
        user_id: str,
        connection: ClientConnection,
        display_name: str = "",
        profile: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        注册用户连接
        
        Args:
            user_id: 用户ID
            connection: 客户端连接
            
        Returns:
            bool: 是否注册成功
        """
        async with self._lock:
            normalized_profile = dict(profile or {})
            device_id = str(normalized_profile.get("device_id") or "").strip()
            if not device_id:
                device_id = f"legacy:{id(connection.writer):x}"
                normalized_profile["device_id"] = device_id
            device_name = str(normalized_profile.get("device_name") or "").strip()
            online_key = self.online_key(user_id, device_id)

            # 检查同一用户同一设备是否已注册（重复注册）
            if online_key in self._connections:
                self.logger.warning(f"在线设备 {online_key} 已注册，将替换旧连接")
                old_conn = self._connections[online_key]
                if old_conn.writer in self._writer_to_key:
                    del self._writer_to_key[old_conn.writer]
            
            # 检查连接是否已注册其他用户
            if connection.writer in self._writer_to_key:
                old_key = self._writer_to_key[connection.writer]
                self._connections.pop(old_key, None)
            
            # 注册新连接
            connection.user_id = user_id
            connection.device_id = device_id
            connection.device_name = device_name
            connection.display_name = (display_name or "").strip()
            connection.profile = normalized_profile
            self._connections[online_key] = connection
            self._writer_to_key[connection.writer] = online_key
            
            self.logger.info(
                f"在线设备注册成功: user_id={user_id}, device_id={device_id}, "
                f"display_name={connection.display_name or '<empty>'}"
            )
            return True
    
    async def unregister(self, user_id: str) -> Optional[ClientConnection]:
        """
        取消注册用户连接
        
        Args:
            user_id: 用户ID
            
        Returns:
            Optional[ClientConnection]: 被移除的连接，如果不存在则返回None
        """
        async with self._lock:
            for online_key, connection in list(self._connections.items()):
                if connection.user_id == user_id:
                    self._connections.pop(online_key, None)
                    self._writer_to_key.pop(connection.writer, None)
                    self.logger.info(f"用户 {user_id} 设备 {connection.device_id} 取消注册")
                    return connection
            return None
    
    async def unregister_by_writer(self, writer: asyncio.StreamWriter) -> Optional[str]:
        """
        通过writer取消注册
        
        Args:
            writer: 流写入器
            
        Returns:
            Optional[str]: 被移除的用户ID，如果不存在则返回None
        """
        async with self._lock:
            online_key = self._writer_to_key.pop(writer, None)
            if online_key:
                connection = self._connections.pop(online_key, None)
                if connection:
                    self.logger.info(f"通过writer取消注册在线设备 {online_key}")
                    return connection.user_id
            return None
    
    async def get_connection(self, user_id: str) -> Optional[ClientConnection]:
        """
        获取用户连接
        
        Args:
            user_id: 用户ID
            
        Returns:
            Optional[ClientConnection]: 连接对象，如果不存在则返回None
        """
        async with self._lock:
            for connection in self._connections.values():
                if connection.user_id == user_id:
                    return connection
            return None

    async def get_connections(self, user_id: str, device_id: str = "") -> List[ClientConnection]:
        """获取用户的一个或多个在线设备连接。"""
        async with self._lock:
            if device_id:
                conn = self._connections.get(self.online_key(user_id, device_id))
                return [conn] if conn else []
            return [
                connection
                for connection in self._connections.values()
                if connection.user_id == user_id
            ]
    
    async def get_user_id(self, writer: asyncio.StreamWriter) -> Optional[str]:
        """
        通过writer获取用户ID
        
        Args:
            writer: 流写入器
            
        Returns:
            Optional[str]: 用户ID，如果不存在则返回None
        """
        async with self._lock:
            online_key = self._writer_to_key.get(writer)
            connection = self._connections.get(online_key or "")
            return connection.user_id if connection else None

    async def get_user_device(self, writer: asyncio.StreamWriter) -> Tuple[Optional[str], str]:
        """通过writer获取 user_id 和 device_id。"""
        async with self._lock:
            online_key = self._writer_to_key.get(writer)
            connection = self._connections.get(online_key or "")
            if not connection:
                return None, ""
            return connection.user_id, connection.device_id
    
    async def update_heartbeat(self, user_id: str, timestamp: float, device_id: str = "") -> bool:
        """
        更新用户心跳时间
        
        Args:
            user_id: 用户ID
            timestamp: 时间戳
            
        Returns:
            bool: 是否更新成功（用户存在）
        """
        async with self._lock:
            if device_id:
                connection = self._connections.get(self.online_key(user_id, device_id))
                if connection:
                    connection.update_heartbeat(timestamp)
                    return True
                return False
            updated = False
            for connection in self._connections.values():
                if connection.user_id == user_id:
                    connection.update_heartbeat(timestamp)
                    updated = True
            return updated

    async def update_heartbeat_by_writer(self, writer: asyncio.StreamWriter, timestamp: float) -> bool:
        """更新指定连接心跳。"""
        async with self._lock:
            online_key = self._writer_to_key.get(writer)
            connection = self._connections.get(online_key or "")
            if connection:
                connection.update_heartbeat(timestamp)
                return True
            return False
    
    async def get_all_users(self) -> Set[str]:
        """
        获取所有已注册用户ID
        
        Returns:
            Set[str]: 用户ID集合
        """
        async with self._lock:
            return {connection.user_id for connection in self._connections.values() if connection.user_id}

    async def get_all_user_profiles(self) -> Dict[str, Dict[str, str]]:
        """
        获取所有在线用户的显示信息。

        Returns:
            Dict[str, Dict[str, str]]: user_id -> profile
        """
        async with self._lock:
            grouped: Dict[str, Dict[str, Any]] = {}
            for connection in self._connections.values():
                if not connection.user_id:
                    continue
                device_profile = {
                    "user_id": connection.user_id,
                    "display_name": connection.display_name,
                    "device_id": connection.device_id,
                    "device_name": connection.device_name,
                    **(connection.profile or {}),
                }
                existing = grouped.setdefault(connection.user_id, {
                    "user_id": connection.user_id,
                    "display_name": connection.display_name,
                    "devices": [],
                })
                existing["devices"].append(device_profile)
                if not existing.get("device_id"):
                    existing.update(device_profile)
            return grouped

    async def get_all_device_profiles(self) -> List[Dict[str, str]]:
        """获取所有在线设备 profile，每个设备一条记录。"""
        async with self._lock:
            return [
                {
                    "user_id": connection.user_id or "",
                    "display_name": connection.display_name,
                    "device_id": connection.device_id,
                    "device_name": connection.device_name,
                    **(connection.profile or {}),
                }
                for connection in self._connections.values()
                if connection.user_id
            ]

    async def get_all_connections(self) -> List[ClientConnection]:
        """返回所有在线连接。"""
        async with self._lock:
            return list(self._connections.values())
    
    async def get_registered_count(self) -> int:
        """
        获取已注册用户数量
        
        Returns:
            int: 用户数量
        """
        async with self._lock:
            return len(self._connections)
    
    async def cleanup_stale_connections(self, current_time: float) -> Set[str]:
        """
        清理过期连接（心跳超时）
        
        Args:
            current_time: 当前时间戳
            
        Returns:
            Set[str]: 被清理的用户ID集合
        """
        stale_users = set()
        async with self._lock:
            users_to_remove = []
            
            for online_key, connection in self._connections.items():
                if current_time - connection.last_heartbeat > self.heartbeat_timeout:
                    users_to_remove.append(online_key)
            
            for online_key in users_to_remove:
                connection = self._connections.pop(online_key)
                self._writer_to_key.pop(connection.writer, None)
                if connection.user_id:
                    stale_users.add(connection.user_id)
                self.logger.warning(f"清理过期连接: {online_key}, 最后心跳: {connection.last_heartbeat}")
        
        return stale_users
    
    async def broadcast(self, message: str, exclude_user: Optional[str] = None) -> int:
        """
        广播消息给所有已注册用户
        
        Args:
            message: 要广播的消息（字符串）
            exclude_user: 排除的用户ID
            
        Returns:
            int: 成功发送的数量
        """
        success_count = 0
        async with self._lock:
            for online_key, connection in self._connections.items():
                if exclude_user and connection.user_id == exclude_user:
                    continue
                
                try:
                    connection.writer.write(message.encode())
                    await connection.writer.drain()
                    success_count += 1
                except Exception as e:
                    self.logger.error(f"广播消息给 {online_key} 失败: {e}")
        
        return success_count

    @staticmethod
    def online_key(user_id: str, device_id: str) -> str:
        return f"{str(user_id or '').strip()}:{str(device_id or '').strip()}"
