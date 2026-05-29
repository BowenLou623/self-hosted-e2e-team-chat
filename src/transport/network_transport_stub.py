"""
网络传输层存根（预留）

为第二阶段网络传输预留的存根实现。
当前仅定义接口，具体实现将在第二阶段完成。
"""

import logging
from typing import Callable, Dict, Any, Optional

from .interface import Transport
from src.models.message import Message
from src.utils.logger import get_logger


class NetworkTransportStub(Transport):
    """
    网络传输层存根
    
    预留的网络传输实现，目前仅记录日志并模拟失败。
    第二阶段将实现真实的网络通信。
    """
    
    def __init__(self, server_address: str = "localhost:8080"):
        """
        初始化网络传输存根
        
        Args:
            server_address: 服务器地址（格式：host:port）
        """
        self.server_address = server_address
        self.logger = get_logger("network_transport_stub")
        self._connected = False
        self._receivers: Dict[str, Callable[[Message], None]] = {}
        
        self.logger.info(f"网络传输存根初始化，服务器地址: {server_address}")
        self.logger.warning("网络传输存根尚未实现，所有消息发送将失败")
    
    def send_message(self, message: Message) -> bool:
        """
        发送消息（存根实现）
        
        当前阶段仅记录日志并返回失败。
        
        Args:
            message: 消息对象
            
        Returns:
            bool: 总是返回False（模拟发送失败）
        """
        self.logger.warning(
            f"网络传输存根：尝试发送消息 [{message.id[:8]}] "
            f"from {message.sender_id} to {message.receiver_id}"
        )
        self.logger.warning("网络传输尚未实现，消息发送失败")
        return False
    
    def register_receiver(self, receiver_id: str, callback: Callable[[Message], None]) -> None:
        """注册消息接收回调（存根实现）"""
        self.logger.warning(f"网络传输存根：注册接收者 {receiver_id}（功能尚未实现）")
        self._receivers[receiver_id] = callback
    
    def unregister_receiver(self, receiver_id: str) -> None:
        """取消注册消息接收回调（存根实现）"""
        self.logger.warning(f"网络传输存根：取消注册接收者 {receiver_id}（功能尚未实现）")
        self._receivers.pop(receiver_id, None)
    
    def connect(self) -> bool:
        """连接服务器（存根实现）"""
        self.logger.warning(f"网络传输存根：尝试连接服务器 {self.server_address}（功能尚未实现）")
        self._connected = False
        return False
    
    def disconnect(self) -> None:
        """断开连接（存根实现）"""
        self.logger.warning("网络传输存根：断开连接（功能尚未实现）")
        self._connected = False
    
    def is_connected(self) -> bool:
        """检查是否已连接（存根实现）"""
        return self._connected
    
    def get_status(self) -> Dict[str, Any]:
        """获取传输层状态（存根实现）"""
        return {
            "transport_type": "network_transport_stub",
            "connected": self._connected,
            "server_address": self.server_address,
            "receiver_count": len(self._receivers),
            "implementation_status": "stub_not_implemented"
        }