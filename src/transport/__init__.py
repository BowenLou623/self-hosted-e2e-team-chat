"""
传输层模块

提供传输层工厂函数，支持memory和network两种传输模式。
"""

import logging
from typing import Dict, Any, Optional

from .local_memory import LocalMemoryTransport
from .client_transport import ClientTransport
from .interface import Transport

logger = logging.getLogger(__name__)


def get_transport(transport_type: str, config: Optional[Dict[str, Any]] = None) -> Transport:
    """
    获取传输层实例的工厂函数
    
    Args:
        transport_type: "memory" 或 "network"
        config: 配置字典，network类型需要包含"hub_address"
                memory类型可选包含"transport_id"
    
    Returns:
        Transport实例
    
    Raises:
        ValueError: 未知的传输类型或缺少必要配置
    """
    config = config or {}
    
    if transport_type == "memory":
        # 内存传输模式
        transport_id = config.get("transport_id", "default")
        logger.info(f"创建内存传输实例: {transport_id}")
        return LocalMemoryTransport.get_global_instance(transport_id)
    
    elif transport_type == "network":
        # 网络传输模式
        hub_address = config.get("hub_address")
        if not hub_address:
            raise ValueError("network transport requires hub_address in config")
        
        logger.info(f"创建网络传输实例，连接Hub: {hub_address}")
        transport = ClientTransport(server_address=hub_address)
        
        # 可选：设置连接状态回调
        if "connection_state_callback" in config:
            transport.set_connection_state_callback(config["connection_state_callback"])
        
        return transport
    
    else:
        raise ValueError(f"Unknown transport type: {transport_type}")


__all__ = [
    "Transport",
    "LocalMemoryTransport",
    "ClientTransport",
    "get_transport",
]