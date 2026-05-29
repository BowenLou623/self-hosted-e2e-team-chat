"""
配对管理模块

处理首次配对流程，包括：
1. 检测未知联系人
2. 触发配对请求
3. 处理用户响应
4. 更新信任状态
"""

import logging
import time
from typing import Optional, Callable, Dict, Any
from enum import Enum

from src.models.device import Device, TrustStatus
from src.core.events import EventType, publish_simple
from .key_store import get_global_key_store
from .local_identity import get_global_identity_manager
from src.models.contact import ContactAuthStatus


class PairingResult(Enum):
    """配对结果枚举"""
    TRUSTED = "trusted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    PENDING = "pending"


class PairingManager:
    """
    配对管理器
    
    职责：
    1. 检测未知联系人
    2. 管理配对请求队列
    3. 协调用户确认流程
    4. 更新信任状态
    """
    
    def __init__(self, key_store=None, identity_manager=None):
        """
        初始化配对管理器
        
        Args:
            key_store: KeyStore实例，如果为None则使用全局实例
            identity_manager: IdentityManager实例，如果为None则使用全局实例
        """
        self.key_store = key_store or get_global_key_store()
        self.identity_manager = identity_manager or get_global_identity_manager()
        self.logger = logging.getLogger("pairing_manager")
        
        # 待处理的配对请求
        self._pending_requests: Dict[str, Dict[str, Any]] = {}  # request_id -> request_data
        
        self.logger.info("配对管理器初始化完成")
    
    def _add_contact_if_missing(self, user_id: str, auth_status: ContactAuthStatus) -> None:
        """
        如果联系人不存在则添加
        
        Args:
            user_id: 联系人用户ID
            auth_status: 授权状态
        """
        contact = self.identity_manager.get_contact(user_id)
        if not contact:
            # 创建基本用户对象
            from src.models.user import User, UserStatus
            user = User(
                user_id=user_id,
                display_name="",
                status=UserStatus.OFFLINE
            )
            # 添加为联系人
            self.identity_manager.add_contact(user, auth_status)
            self.logger.debug(f"添加缺失的联系人: {user_id}, auth_status={auth_status.value}")
    
    def check_contact_trust(self, user_id: str, device_id: str, 
                           public_key: Optional[str] = None, 
                           fingerprint: Optional[str] = None) -> PairingResult:
        """
        检查联系人信任状态，如果需要则触发配对
        
        注意：现在首先检查联系人授权状态，其次检查设备信任状态。
        
        Args:
            user_id: 联系人用户ID
            device_id: 联系人设备ID
            public_key: 联系人公钥（可选）
            fingerprint: 公钥指纹（可选）
            
        Returns:
            PairingResult: 配对结果
        """
        # 1. 首先检查联系人授权状态
        contact = self.identity_manager.get_contact(user_id)
        if contact:
            auth_status = contact.auth_status
            if auth_status == ContactAuthStatus.TRUSTED:
                self.logger.debug(f"联系人已授权: {user_id}")
                # 仍需要检查设备信任（用于加密）
                if self.key_store.is_contact_trusted(user_id, device_id):
                    return PairingResult.TRUSTED
                else:
                    # 设备未信任，但仍可通信（可能加密降级）
                    self.logger.warning(
                        f"M2-A 临时旁路: 联系人已授权但设备未信任，允许继续通信: {user_id}/{device_id[:8]}"
                    )
                    # 触发设备配对（可选）
                    # 暂时返回TRUSTED以允许通信
                    return PairingResult.TRUSTED
            elif auth_status == ContactAuthStatus.PENDING_INCOMING:
                self.logger.debug(f"联系人待授权（收到消息）: {user_id}")
                return PairingResult.PENDING
            elif auth_status == ContactAuthStatus.REJECTED:
                self.logger.info(f"联系人已被拒绝: {user_id}")
                return PairingResult.REJECTED
            else:  # UNKNOWN
                # 未知状态，继续检查设备信任
                pass
        
        # 2. 检查设备信任状态（原有逻辑）
        if self.key_store.is_contact_trusted(user_id, device_id):
            self.logger.debug(f"设备已信任: {user_id}/{device_id[:8]}")
            # 如果联系人不存在，添加为联系人并设置为TRUSTED
            if not contact:
                self._add_contact_if_missing(user_id, ContactAuthStatus.TRUSTED)
            return PairingResult.TRUSTED
        
        # 检查是否有待处理的配对请求
        request_id = f"{user_id}:{device_id}"
        if request_id in self._pending_requests:
            self.logger.debug(f"配对请求已存在: {request_id}")
            return PairingResult.PENDING
        
        # 检查是否已记录但未信任
        existing_device = self.key_store.get_contact_key(user_id, device_id)
        if existing_device:
            if existing_device.trust_status == TrustStatus.BLOCKED:
                self.logger.info(f"设备已被阻止: {user_id}/{device_id[:8]}")
                return PairingResult.REJECTED
            else:
                # 状态为UNKNOWN，需要配对
                self.logger.info(f"设备未信任，触发配对: {user_id}/{device_id[:8]}")
                return self._create_pairing_request(user_id, device_id, public_key, fingerprint)
        else:
            # 全新联系人，触发配对
            self.logger.info(f"检测到新联系人，触发配对: {user_id}/{device_id[:8]}")
            return self._create_pairing_request(user_id, device_id, public_key, fingerprint)
    
    def _create_pairing_request(self, user_id: str, device_id: str, 
                               public_key: Optional[str], fingerprint: Optional[str]) -> PairingResult:
        """
        创建配对请求
        
        Args:
            user_id: 联系人用户ID
            device_id: 联系人设备ID
            public_key: 联系人公钥
            fingerprint: 公钥指纹
            
        Returns:
            PairingResult: 配对结果（PENDING表示已创建请求）
        """
        request_id = f"{user_id}:{device_id}"
        
        # 确保设备信任记录存在（如果不存在则创建）
        existing_device = self.key_store.get_contact_key(user_id, device_id)
        if not existing_device:
            # 创建初始记录（公钥可能为空）
            self.key_store.save_contact_key(
                user_id, device_id, public_key, fingerprint, TrustStatus.UNKNOWN
            )
        
        # 生成请求数据
        request_data = {
            "request_id": request_id,
            "user_id": user_id,
            "device_id": device_id,
            "public_key": public_key,
            "fingerprint": fingerprint,
            "created_at": time.time(),
            "status": "pending"
        }
        
        self._pending_requests[request_id] = request_data
        
        # 发布配对请求事件（UI应监听并显示确认对话框）
        publish_simple(EventType.PAIRING_REQUEST, {
            "request_id": request_id,
            "user_id": user_id,
            "device_id": device_id,
            "public_key": public_key,
            "fingerprint": fingerprint,
            "timestamp": time.time()
        }, source="pairing_manager")
        
        self.logger.info(f"配对请求已发布: {request_id}")
        return PairingResult.PENDING
    
    def handle_pairing_response(self, request_id: str, response: PairingResult, 
                               user_callback: Optional[Callable] = None) -> bool:
        """
        处理用户对配对请求的响应
        
        Args:
            request_id: 配对请求ID
            response: 用户响应
            user_callback: 可选的用户回调函数
            
        Returns:
            bool: 是否处理成功
        """
        if request_id not in self._pending_requests:
            self.logger.error(f"未知的配对请求: {request_id}")
            return False
        
        request_data = self._pending_requests.pop(request_id)
        user_id = request_data["user_id"]
        device_id = request_data["device_id"]
        public_key = request_data.get("public_key")
        fingerprint = request_data.get("fingerprint")
        
        if response == PairingResult.TRUSTED:
            # 用户选择信任
            trust_status = TrustStatus.TRUSTED
            
            # 保存或更新信任状态
            if public_key and fingerprint:
                success = self.key_store.save_contact_key(
                    user_id, device_id, public_key, fingerprint, trust_status
                )
            else:
                # 如果没有公钥信息，仅更新信任状态（假设已存在记录）
                success = self.key_store.update_trust_status(user_id, device_id, trust_status)
            
            if success:
                self.logger.info(f"联系人已信任: {user_id}/{device_id[:8]}")
                
                # 更新联系人授权状态为TRUSTED
                contact = self.identity_manager.get_contact(user_id)
                if contact:
                    contact.auth_status = ContactAuthStatus.TRUSTED
                    contact.updated_at = time.time()
                    # 保存联系人（通过身份管理器保存配置）
                    self.identity_manager.add_contact(contact, ContactAuthStatus.TRUSTED)
                else:
                    # 添加新联系人
                    self._add_contact_if_missing(user_id, ContactAuthStatus.TRUSTED)
                
                # 发布配对成功事件
                publish_simple(EventType.PAIRING_COMPLETED, {
                    "request_id": request_id,
                    "user_id": user_id,
                    "device_id": device_id,
                    "result": "trusted",
                    "timestamp": time.time()
                }, source="pairing_manager")
                
                if user_callback:
                    user_callback(True, user_id, device_id)
                
                return True
            else:
                self.logger.error(f"保存信任状态失败: {user_id}/{device_id[:8]}")
                return False
                
        elif response == PairingResult.REJECTED:
            # 用户选择拒绝
            trust_status = TrustStatus.BLOCKED
            
            # 更新信任状态为阻止
            success = self.key_store.update_trust_status(user_id, device_id, trust_status)
            
            if success:
                self.logger.info(f"联系人已被阻止: {user_id}/{device_id[:8]}")
                
                # 更新联系人授权状态为REJECTED
                contact = self.identity_manager.get_contact(user_id)
                if contact:
                    contact.auth_status = ContactAuthStatus.REJECTED
                    contact.updated_at = time.time()
                    # 保存联系人
                    self.identity_manager.add_contact(contact, ContactAuthStatus.REJECTED)
                else:
                    # 添加新联系人并标记为REJECTED
                    self._add_contact_if_missing(user_id, ContactAuthStatus.REJECTED)
                
                # 发布配对拒绝事件
                publish_simple(EventType.PAIRING_COMPLETED, {
                    "request_id": request_id,
                    "user_id": user_id,
                    "device_id": device_id,
                    "result": "rejected",
                    "timestamp": time.time()
                }, source="pairing_manager")
                
                if user_callback:
                    user_callback(False, user_id, device_id)
                
                return True
            else:
                self.logger.error(f"更新阻止状态失败: {user_id}/{device_id[:8]}")
                return False
                
        elif response == PairingResult.CANCELLED:
            # 用户取消配对，保持未知状态
            self.logger.info(f"配对已取消: {user_id}/{device_id[:8]}")
            
            # 发布配对取消事件
            publish_simple(EventType.PAIRING_COMPLETED, {
                "request_id": request_id,
                "user_id": user_id,
                "device_id": device_id,
                "result": "cancelled",
                "timestamp": time.time()
            }, source="pairing_manager")
            
            if user_callback:
                user_callback(False, user_id, device_id)
            
            return True
        
        else:
            self.logger.error(f"无效的配对响应: {response}")
            return False
    
    def get_pending_requests(self) -> Dict[str, Dict[str, Any]]:
        """获取所有待处理的配对请求"""
        return self._pending_requests.copy()
    
    def cancel_all_pending(self) -> None:
        """取消所有待处理的配对请求"""
        for request_id in list(self._pending_requests.keys()):
            self.handle_pairing_response(request_id, PairingResult.CANCELLED)
        
        self.logger.info("所有待处理配对请求已取消")
    

# 全局配对管理器实例（按依赖隔离）
_global_pairing_managers: Dict[str, PairingManager] = {}


def get_global_pairing_manager(key_store=None, identity_manager=None) -> PairingManager:
    """
    获取全局配对管理器实例
    
    Returns:
        PairingManager: 全局配对管理器实例
    """
    global _global_pairing_managers

    key_store_id = str(id(key_store)) if key_store is not None else "default_keystore"
    identity_manager_id = str(id(identity_manager)) if identity_manager is not None else "default_identity"
    manager_key = f"{key_store_id}:{identity_manager_id}"
    
    if manager_key not in _global_pairing_managers:
        _global_pairing_managers[manager_key] = PairingManager(
            key_store=key_store,
            identity_manager=identity_manager
        )
    
    return _global_pairing_managers[manager_key]
