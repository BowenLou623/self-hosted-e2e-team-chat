"""
公钥存储和信任管理

管理联系人公钥和信任状态。
支持首次配对流程和信任状态持久化。
"""

import logging
import time
from typing import Optional, List, Dict, Any
from enum import Enum

from src.models.device import Device, TrustStatus
from src.storage.sqlite_store import get_global_store


class KeyStore:
    """
    公钥存储管理器
    
    职责：
    1. 管理本地设备密钥对（模拟）
    2. 存储联系人公钥
    3. 管理信任状态
    4. 提供密钥指纹验证
    """
    
    def __init__(self, store=None):
        """
        初始化KeyStore
        
        Args:
            store: SQLiteStore实例，如果为None则使用全局实例
        """
        self.store = store or get_global_store()
        self.logger = logging.getLogger("key_store")
        
        # 本地设备密钥对（模拟）
        self._local_key_pair: Optional[Dict[str, str]] = None
        
        self.logger.info("KeyStore初始化完成")
    
    def generate_local_keypair(self, user_id: str, device_id: str) -> Dict[str, str]:
        """
        生成本地设备密钥对（模拟）
        
        Args:
            user_id: 用户ID
            device_id: 设备ID
            
        Returns:
            包含公钥和私钥的字典（模拟）
        """
        # 模拟生成密钥对：实际应用中应使用真正的非对称加密算法
        import hashlib
        import secrets
        
        seed = f"{user_id}:{device_id}:{time.time()}:{secrets.token_hex(8)}"
        private_key = hashlib.sha256(seed.encode()).hexdigest()
        public_key = hashlib.sha256(private_key.encode()).hexdigest()
        
        self._local_key_pair = {
            "public_key": public_key,
            "private_key": private_key,
            "fingerprint": public_key[:16]  # 简单指纹
        }
        
        self.logger.info(f"为设备 {device_id[:8]} 生成模拟密钥对")
        return self._local_key_pair
    
    def get_local_public_key(self) -> Optional[str]:
        """获取本地公钥"""
        if self._local_key_pair:
            return self._local_key_pair.get("public_key")
        return None
    
    def get_local_fingerprint(self) -> Optional[str]:
        """获取本地公钥指纹"""
        if self._local_key_pair:
            return self._local_key_pair.get("fingerprint")
        return None
    
    def save_contact_key(self, user_id: str, device_id: str, public_key: Optional[str], 
                        fingerprint: Optional[str], trust_status: TrustStatus = TrustStatus.UNKNOWN) -> bool:
        """
        保存联系人公钥
        
        Args:
            user_id: 联系人用户ID
            device_id: 联系人设备ID
            public_key: 联系人公钥（可选）
            fingerprint: 公钥指纹（可选）
            trust_status: 初始信任状态
            
        Returns:
            bool: 是否保存成功
        """
        try:
            device = Device(
                id=device_id,
                user_id=user_id,
                public_key=public_key,
                fingerprint=fingerprint,
                trust_status=trust_status,
                created_at=time.time()
            )
            
            success = self.store.save_device_trust(device)
            if success:
                self.logger.info(f"保存联系人公钥: {user_id}/{device_id[:8]}, 信任状态: {trust_status.value}")
            else:
                self.logger.error(f"保存联系人公钥失败: {user_id}/{device_id[:8]}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"保存联系人公钥异常: {e}", exc_info=True)
            return False
    
    def get_contact_key(self, user_id: str, device_id: str) -> Optional[Device]:
        """
        获取联系人公钥信息
        
        Args:
            user_id: 联系人用户ID
            device_id: 联系人设备ID
            
        Returns:
            Device对象，如果不存在则返回None
        """
        device = self.store.get_device_trust(user_id, device_id)
        if device:
            self.logger.debug(f"获取联系人公钥: {user_id}/{device_id[:8]}, 信任状态: {device.trust_status.value}")
        else:
            self.logger.debug(f"联系人公钥不存在: {user_id}/{device_id[:8]}")
        return device
    
    def update_trust_status(self, user_id: str, device_id: str, trust_status: TrustStatus) -> bool:
        """
        更新联系人信任状态
        
        Args:
            user_id: 联系人用户ID
            device_id: 联系人设备ID
            trust_status: 新的信任状态
            
        Returns:
            bool: 是否更新成功
        """
        success = self.store.update_trust_status(user_id, device_id, trust_status)
        if success:
            self.logger.info(f"更新信任状态: {user_id}/{device_id[:8]} -> {trust_status.value}")
        else:
            self.logger.warning(f"更新信任状态失败或记录不存在: {user_id}/{device_id[:8]}")
        return success
    
    def get_trusted_contacts(self) -> List[Device]:
        """
        获取所有已信任的联系人设备
        
        Returns:
            Device对象列表
        """
        # 注意：当前实现需要知道所有用户，这里简化返回所有信任状态为TRUSTED的设备
        # 实际应用可能需要更复杂的查询
        devices = []
        # 暂时返回空列表，后续可以扩展
        return devices
    
    def is_contact_trusted(self, user_id: str, device_id: str) -> bool:
        """
        检查联系人是否已信任
        
        Args:
            user_id: 联系人用户ID
            device_id: 联系人设备ID
            
        Returns:
            bool: 是否已信任
        """
        device = self.get_contact_key(user_id, device_id)
        if device and device.trust_status == TrustStatus.TRUSTED:
            return True
        return False
    
    def get_contact_fingerprint(self, user_id: str, device_id: str) -> Optional[str]:
        """
        获取联系人公钥指纹
        
        Args:
            user_id: 联系人用户ID
            device_id: 联系人设备ID
            
        Returns:
            指纹字符串，如果不存在则返回None
        """
        device = self.get_contact_key(user_id, device_id)
        if device:
            return device.fingerprint
        return None
    
    def verify_fingerprint(self, user_id: str, device_id: str, expected_fingerprint: str) -> bool:
        """
        验证联系人公钥指纹
        
        Args:
            user_id: 联系人用户ID
            device_id: 联系人设备ID
            expected_fingerprint: 预期的指纹
            
        Returns:
            bool: 指纹是否匹配
        """
        actual_fingerprint = self.get_contact_fingerprint(user_id, device_id)
        if actual_fingerprint is None:
            self.logger.warning(f"无法验证指纹：未找到联系人 {user_id}/{device_id[:8]}")
            return False
        
        match = actual_fingerprint == expected_fingerprint
        if match:
            self.logger.debug(f"指纹验证通过: {user_id}/{device_id[:8]}")
        else:
            self.logger.warning(f"指纹验证失败: {user_id}/{device_id[:8]}, 预期: {expected_fingerprint}, 实际: {actual_fingerprint}")
        
        return match


# 全局KeyStore实例（按存储实例隔离）
_global_key_stores: Dict[str, KeyStore] = {}


def get_global_key_store(store=None) -> KeyStore:
    """
    获取全局KeyStore实例
    
    Returns:
        KeyStore: 全局KeyStore实例
    """
    global _global_key_stores

    if store is not None and hasattr(store, "db_path"):
        store_key = str(store.db_path.expanduser().resolve())
    else:
        store_key = "__default__"
    
    if store_key not in _global_key_stores:
        _global_key_stores[store_key] = KeyStore(store=store)
    
    return _global_key_stores[store_key]
