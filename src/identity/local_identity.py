"""
本地身份管理模块。

M3 起收敛为固定 user_id + 可变 display_name + 本地密码验证模型：
1. user_id 首次生成/指定后永久固定
2. display_name 仅用于 UI 显示，可随时修改
3. password_hash/password_salt 仅用于本地密码验证
"""

import json
import time
import uuid
from typing import Any, Dict, Optional, Union
from dataclasses import dataclass, field
from pathlib import Path

from src.models.user import User, UserStatus
from src.models.contact import Contact, ContactAuthStatus, normalize_contact_auth_status
from src.utils.logger import get_logger
from .password_manager import get_global_password_manager


@dataclass
class LocalIdentity:
    """本地身份配置"""
    user_id: str  # 系统主身份，永久固定
    display_name: str  # 用户显示名，可修改
    original_username: str = ""  # 兼容性字段，不再参与身份判断
    avatar_url: Optional[str] = None
    status: UserStatus = UserStatus.ONLINE
    last_seen: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    password_hash: Optional[str] = None
    password_salt: Optional[str] = None
    
    def to_user(self) -> User:
        """转换为User对象"""
        return User(
            user_id=self.user_id,
            display_name=self.display_name,
            original_username=self.original_username,
            avatar_url=self.avatar_url,
            status=self.status,
            last_seen=self.last_seen
        )
    
    @classmethod
    def from_user(cls, user: User) -> "LocalIdentity":
        """从User对象创建"""
        return cls(
            user_id=user.user_id,
            display_name=user.display_name,
            original_username=user.original_username,
            avatar_url=user.avatar_url,
            status=user.status,
            last_seen=user.last_seen if user.last_seen is not None else 0.0,
        )


class IdentityManager:
    """
    身份管理器
    
    管理当前用户身份和联系人列表。
    支持从配置文件加载/保存身份配置。
    """
    
    def __init__(self, config_dir: str = "data/config", store=None):
        """
        初始化身份管理器
        
        Args:
            config_dir: 配置文件目录
        """
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = get_logger("identity_manager")
        self.store = store
        
        # 当前用户身份
        self.current_identity: Optional[LocalIdentity] = None
        
        # 联系人缓存（contact_id -> Contact）
        self._contacts: Dict[str, Contact] = {}
        
        # 预定义用户池（用于本地模拟）
        self._predefined_users: Dict[str, Contact] = self._create_predefined_users()
        
        self.logger.info(f"身份管理器初始化完成，配置目录: {config_dir}")

    def generate_user_id(self) -> str:
        """生成新的固定 user_id。"""
        return f"u_{uuid.uuid4().hex[:8]}"

    def _extract_user_id_from_current_user(self, current_user_data: Dict[str, Any]) -> str:
        """从配置中的当前用户记录提取稳定 user_id。"""
        raw_user_id = (
            current_user_data.get("user_id")
            or current_user_data.get("id")
            or current_user_data.get("username")
            or current_user_data.get("original_username")
            or ""
        )
        return self.normalize_user_id(raw_user_id)

    def _read_config_data(self) -> Optional[Dict[str, Any]]:
        """读取 identity 配置文件。"""
        config_file = self.config_dir / "identity.json"
        if not config_file.exists():
            return None

        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_stored_identity_summary(self) -> Optional[Dict[str, Any]]:
        """返回持久化 identity 的最小摘要。"""
        try:
            config_data = self._read_config_data()
            if not config_data:
                return None

            current_user_data = config_data.get("current_user", {})
            stored_user_id = self._extract_user_id_from_current_user(current_user_data)
            if not stored_user_id:
                return None

            return {
                "user_id": stored_user_id,
                "display_name": current_user_data.get("display_name", ""),
                "password_hash": current_user_data.get("password_hash"),
                "password_salt": current_user_data.get("password_salt"),
            }
        except Exception as e:
            self.logger.error(f"读取身份摘要失败: {e}", exc_info=True)
            return None

    def has_stored_identity(self) -> bool:
        """当前配置目录是否已有本地 identity。"""
        return self.get_stored_identity_summary() is not None

    def normalize_user_id(self, user_id: str) -> str:
        """规范化当前用户 ID，仅做空白裁剪，不做本地映射。"""
        normalized_user_id = user_id.strip()
        if normalized_user_id != user_id:
            self.logger.warning(
                f"M2-B: 当前用户ID已规范化空白字符: '{user_id}' -> '{normalized_user_id}'"
            )
        return normalized_user_id

    def normalize_contact_id(self, contact_id: str) -> str:
        """规范化联系人 ID，仅做空白裁剪，不生成任何本地 stable id。"""
        normalized_contact_id = contact_id.strip()
        if normalized_contact_id != contact_id:
            self.logger.warning(
                f"M2-B: 联系人ID已规范化空白字符: '{contact_id}' -> '{normalized_contact_id}'"
            )
        return normalized_contact_id

    def ensure_stable_user_id(self, user_id: str) -> str:
        """兼容旧接口：M2-B 起仅返回原样 contact_id/user_id（去除首尾空白）。"""
        return self.normalize_contact_id(user_id)
    
    def _create_predefined_users(self) -> Dict[str, Contact]:
        """创建预定义用户池（用于本地模拟）
        
        注意：为了去除用户特异化逻辑，预定义用户池现在为空。
        联系人应该通过实际通信动态添加，或从配置文件加载。
        """
        # 返回空字典，不再硬编码预定义用户
        self.logger.info("预定义用户池已禁用，联系人将通过配置文件或网络动态添加")
        return {}

    
    def set_current_user(self, user_id: str, display_name: Optional[str] = None) -> bool:
        """
        设置当前用户身份
        
        Args:
            user_id: 用户ID（系统主身份，永久固定）
            display_name: 显示名称（可选，留空时由 UI 回退显示 user_id）
            
        Returns:
            bool: 是否设置成功
        """
        try:
            exact_user_id = self.normalize_user_id(user_id)
            if not exact_user_id:
                raise ValueError("用户ID不能为空")

            stored_identity = self.get_stored_identity_summary()
            if stored_identity and stored_identity["user_id"] != exact_user_id:
                raise ValueError(
                    f"本地 identity 已固定为 {stored_identity['user_id']}，不能修改为 {exact_user_id}"
                )
            
            # 显示名称只作为显示层字段，可为空
            actual_display_name = (display_name or "").strip()
            
            # 如果用户已存在于预定义用户池中，使用预定义信息（兼容性）
            if user_id in self._predefined_users:
                user = self._predefined_users[user_id]
                self.current_identity = LocalIdentity.from_user(user)
                self.logger.info(f"使用预定义用户身份: {user_id}")
            else:
                # 创建新身份
                self.current_identity = LocalIdentity(
                    user_id=exact_user_id,
                    display_name=actual_display_name,
                    original_username="",
                    created_at=time.time(),
                    updated_at=time.time(),
                    password_hash=None,
                    password_salt=None,
                )
                self.logger.info(f"创建新用户身份: user_id={exact_user_id}, display_name={actual_display_name}")
            
            # 构建联系人列表（排除当前用户自己）
            self._build_contacts()
            
            # 保存配置
            self.logger.debug("开始保存身份配置到文件")
            self._save_config()
            self.logger.debug("身份配置保存完成")
            return True
            
        except Exception as e:
            self.logger.error(f"设置当前用户失败: {e}", exc_info=True)
            return False
    
    def update_display_name(self, new_display_name: str) -> bool:
        """
        更新当前用户的显示名称
        
        Args:
            new_display_name: 新的显示名称
            
        Returns:
            bool: 是否更新成功
        """
        if not self.current_identity:
            self.logger.error("无法更新显示名称：当前身份未设置")
            return False

        normalized_display_name = (new_display_name or "").strip()
        old_name = self.current_identity.display_name
        if old_name == normalized_display_name:
            return True

        self.current_identity.display_name = normalized_display_name
        self.current_identity.updated_at = time.time()
        
        # 保存配置
        self._save_config()
        
        self.logger.info(f"显示名称已更新: {old_name or '<empty>'} -> {normalized_display_name or '<empty>'}")
        return True
    
    def _build_contacts(self) -> None:
        """构建联系人列表（排除当前用户自己）"""
        if not self.current_identity:
            return
        
        self._contacts.clear()
        
        # 添加所有预定义用户作为联系人（除了当前用户自己）
        for user_id, user in self._predefined_users.items():
            if user_id != self.current_identity.user_id:
                # 将User转换为Contact，默认授权状态为UNKNOWN
                contact = Contact.from_user(user, ContactAuthStatus.UNKNOWN)
                self._contacts[contact.contact_id] = contact
        
        self.logger.info(f"构建联系人列表，共 {len(self._contacts)} 个联系人")
    
    def get_current_user(self) -> Optional[User]:
        """获取当前用户（User对象）"""
        if self.current_identity:
            return self.current_identity.to_user()
        return None
    
    def get_current_user_id(self) -> Optional[str]:
        """获取当前用户ID"""
        if self.current_identity:
            return self.current_identity.user_id
        return None
    
    def get_contacts(self) -> Dict[str, Contact]:
        """获取联系人列表（排除当前用户自己）"""
        self.logger.debug(f"获取联系人列表，当前有 {len(self._contacts)} 个联系人")
        return {
            contact_id: Contact.from_dict(contact.to_dict())
            for contact_id, contact in self._contacts.items()
        }
    
    def get_contact(self, contact_id: str) -> Optional[Contact]:
        """按 contact_id 获取指定联系人。"""
        exact_contact_id = self.normalize_contact_id(contact_id)
        if exact_contact_id == self.get_current_user_id():
            return None
        return self._contacts.get(exact_contact_id)
    
    def add_contact(self, user: Union[User, Contact], auth_status: ContactAuthStatus = ContactAuthStatus.UNKNOWN) -> bool:
        """添加或更新联系人，唯一键固定为 contact_id。"""
        exact_contact_id = self.normalize_contact_id(user.user_id)
        if exact_contact_id == self.get_current_user_id():
            self.logger.warning(f"不能将自己添加为联系人: {exact_contact_id}")
            return False

        incoming_contact = user if isinstance(user, Contact) else Contact.from_user(user, auth_status)
        incoming_contact.contact_id = exact_contact_id
        if auth_status != ContactAuthStatus.UNKNOWN:
            incoming_contact.trust_status = auth_status

        existing_contact = self._contacts.get(exact_contact_id)
        if existing_contact is None:
            self._contacts[exact_contact_id] = incoming_contact
            self.logger.info(
                f"添加联系人: contact_id={exact_contact_id}, display_name={incoming_contact.display_name}, "
                f"trust_status={incoming_contact.trust_status.value}"
            )
            self.logger.debug(f"添加后联系人列表键: {list(self._contacts.keys())}")
            self._save_config()
            if self.store is not None:
                self.store.save_contact(incoming_contact)
            return True

        changed = False
        new_display_name = (incoming_contact.display_name or "").strip()
        if new_display_name and existing_contact.display_name != new_display_name:
            existing_contact.display_name = new_display_name
            changed = True

        if incoming_contact.status != existing_contact.status:
            existing_contact.status = incoming_contact.status
            changed = True

        if incoming_contact.avatar_url != existing_contact.avatar_url:
            existing_contact.avatar_url = incoming_contact.avatar_url
            changed = True

        if incoming_contact.last_seen is not None and incoming_contact.last_seen != existing_contact.last_seen:
            existing_contact.last_seen = incoming_contact.last_seen
            changed = True

        if incoming_contact.alias != existing_contact.alias:
            existing_contact.alias = incoming_contact.alias
            changed = True

        incoming_metadata = incoming_contact.metadata or {}
        if incoming_metadata:
            merged_metadata = {
                **(existing_contact.metadata or {}),
                **incoming_metadata,
            }
            if merged_metadata != (existing_contact.metadata or {}):
                existing_contact.metadata = merged_metadata
                changed = True

        incoming_device_keys = incoming_contact.device_keys or {}
        if incoming_device_keys:
            merged_device_keys = {
                **(existing_contact.device_keys or {}),
                **incoming_device_keys,
            }
            if merged_device_keys != (existing_contact.device_keys or {}):
                existing_contact.device_keys = merged_device_keys
                changed = True

        incoming_device_ids = [
            str(device_id).strip()
            for device_id in (incoming_contact.device_ids or [])
            if str(device_id).strip()
        ]
        if incoming_device_ids:
            merged_device_ids = list(dict.fromkeys([*(existing_contact.device_ids or []), *incoming_device_ids]))
            if merged_device_ids != (existing_contact.device_ids or []):
                existing_contact.device_ids = merged_device_ids
                changed = True

        if incoming_contact.public_key and incoming_contact.public_key != existing_contact.public_key:
            existing_contact.public_key = incoming_contact.public_key
            changed = True

        if auth_status != ContactAuthStatus.UNKNOWN and existing_contact.trust_status != auth_status:
            existing_contact.trust_status = auth_status
            changed = True

        if changed:
            existing_contact.updated_at = time.time()
            self.logger.info(
                f"更新联系人: contact_id={exact_contact_id}, display_name={existing_contact.display_name}, "
                f"trust_status={existing_contact.trust_status.value}"
            )
            self._save_config()
            if self.store is not None:
                self.store.save_contact(existing_contact)
            return True

        self.logger.debug(f"联系人已存在且无变化: {exact_contact_id}")
        return False
    
    def update_contact_auth_status(self, contact_id: str, auth_status: ContactAuthStatus) -> bool:
        """按 contact_id 更新联系人授权状态。"""
        exact_contact_id = self.normalize_contact_id(contact_id)
        if exact_contact_id not in self._contacts:
            self.logger.warning(f"尝试更新不存在的联系人授权状态: {exact_contact_id}")
            return False
        
        contact = self._contacts[exact_contact_id]
        old_status = contact.trust_status
        contact.update_auth_status(normalize_contact_auth_status(auth_status))
        contact.last_interaction = time.time()
        
        self.logger.info(f"更新联系人授权状态: {exact_contact_id}: {old_status.value} -> {auth_status.value}")
        
        # 保存配置
        self._save_config()
        
        if self.store is not None:
            self.store.save_contact(contact)
        
        return True

    def increment_contact_pending_count(self, contact_id: str) -> int:
        """增加联系人待授权消息计数，并同步到配置与存储。"""
        exact_contact_id = self.normalize_contact_id(contact_id)
        contact = self._contacts.get(exact_contact_id)
        if contact is None:
            self.logger.warning(f"尝试增加不存在联系人的待授权计数: {exact_contact_id}")
            return 0

        new_count = contact.increment_pending_count()
        contact.last_interaction = time.time()
        self._save_config()
        if self.store is not None:
            self.store.save_contact(contact)
        return new_count

    def clear_contact_pending_count(self, contact_id: str) -> bool:
        """清空联系人待授权消息计数，并同步到配置与存储。"""
        exact_contact_id = self.normalize_contact_id(contact_id)
        contact = self._contacts.get(exact_contact_id)
        if contact is None:
            self.logger.warning(f"尝试清空不存在联系人的待授权计数: {exact_contact_id}")
            return False

        contact.clear_pending_count()
        contact.last_interaction = time.time()
        self._save_config()
        if self.store is not None:
            self.store.save_contact(contact)
        return True

    def update_contact_display_name(self, contact_id: str, display_name: str) -> bool:
        """按 contact_id 更新联系人显示名。"""
        exact_contact_id = self.normalize_contact_id(contact_id)
        if exact_contact_id not in self._contacts:
            self.logger.warning(f"尝试更新不存在的联系人显示名: {exact_contact_id}")
            return False

        contact = self._contacts[exact_contact_id]
        normalized_display_name = (display_name or "").strip()
        if contact.display_name == normalized_display_name:
            self.logger.debug(f"联系人显示名无变化: {exact_contact_id}")
            return False

        contact.display_name = normalized_display_name
        contact.updated_at = time.time()
        self._save_config()
        if self.store is not None:
            self.store.save_contact(contact)

        self.logger.info(
            f"更新联系人显示名: contact_id={exact_contact_id}, display_name={normalized_display_name or '<empty>'}"
        )
        return True
    
    def remove_contact(self, contact_id: str) -> bool:
        """按 contact_id 移除联系人。"""
        exact_contact_id = self.normalize_contact_id(contact_id)
        if exact_contact_id in self._contacts:
            del self._contacts[exact_contact_id]
            self.logger.info(f"移除联系人: {exact_contact_id}")
            
            # 保存配置
            self._save_config()
            if self.store is not None:
                self.store.delete_contact(exact_contact_id)
            return True
        return False
    
    def get_all_users(self) -> Dict[str, User]:
        """获取所有用户（包括当前用户和联系人）"""
        all_users = self._contacts.copy()  # type: ignore[assignment]
        if self.current_identity:
            all_users[self.current_identity.user_id] = self.current_identity.to_user()  # type: ignore[assignment]
        return all_users  # type: ignore[return-value]
    
    def load_existing_identity(self) -> bool:
        """加载当前目录下已存在的 identity。"""
        return self._load_existing_config()

    def initialize_identity(
        self,
        user_id: str,
        password: str,
        display_name: Optional[str] = None,
    ) -> bool:
        """
        初始化或补齐本地 identity 的密码信息。

        规则：
        - 若当前目录已有 identity，则 user_id 必须与已存 user_id 一致
        - 若当前目录没有 identity，则创建新 identity
        - display_name 可为空
        """
        exact_user_id = self.normalize_user_id(user_id)
        normalized_display_name = (display_name or "").strip()
        if not exact_user_id:
            self.logger.error("初始化身份失败：user_id 不能为空")
            return False
        if not password:
            self.logger.error("初始化身份失败：密码不能为空")
            return False

        stored_identity = self.get_stored_identity_summary()
        if stored_identity and stored_identity["user_id"] != exact_user_id:
            self.logger.error(
                f"初始化身份失败：本地 identity 已固定为 {stored_identity['user_id']}，不能改为 {exact_user_id}"
            )
            return False

        try:
            password_manager = get_global_password_manager(str(self.config_dir))
            password_hash, password_salt = password_manager.hash_password(password)

            if stored_identity:
                if not self._load_existing_config():
                    return False
                if not self.current_identity or self.current_identity.user_id != exact_user_id:
                    self.logger.error("初始化身份失败：加载现有 identity 后 user_id 不一致")
                    return False

                self.current_identity.display_name = normalized_display_name
                self.current_identity.password_hash = password_hash
                self.current_identity.password_salt = password_salt
                self.current_identity.updated_at = time.time()
            else:
                self.current_identity = LocalIdentity(
                    user_id=exact_user_id,
                    display_name=normalized_display_name,
                    original_username="",
                    created_at=time.time(),
                    updated_at=time.time(),
                    password_hash=password_hash,
                    password_salt=password_salt,
                )
                self._build_contacts()

            self._save_config()
            self.logger.info(
                f"identity 初始化完成: user_id={exact_user_id}, display_name={normalized_display_name or '<empty>'}"
            )
            return True
        except Exception as e:
            self.logger.error(f"初始化身份失败: {e}", exc_info=True)
            return False

    # Password Methods
    def has_password(self) -> bool:
        """检查当前用户是否设置了密码"""
        if not self.current_identity:
            return False
        if not self.current_identity.password_hash:
            return False
        if self.current_identity.password_hash.startswith("pbkdf2_sha256$"):
            return bool(self.current_identity.password_salt)
        return True
    
    def set_password(self, password: str) -> bool:
        """
        为当前用户设置密码
        
        Args:
            password: 明文密码
            
        Returns:
            bool: 是否设置成功
        """
        if not self.current_identity:
            self.logger.error("无法设置密码：当前身份未设置")
            return False
        
        if not password:
            self.logger.error("密码不能为空")
            return False
        
        try:
            password_manager = get_global_password_manager(str(self.config_dir))
            password_hash, password_salt = password_manager.hash_password(password)
            self.current_identity.password_hash = password_hash
            self.current_identity.password_salt = password_salt
            self.current_identity.updated_at = time.time()
            self._save_config()
            self.logger.info("密码已设置")
            return True
        except Exception as e:
            self.logger.error(f"设置密码失败: {e}")
            return False
    
    def verify_password(self, password: str) -> bool:
        """
        验证当前用户的密码
        
        Args:
            password: 明文密码
            
        Returns:
            bool: 密码是否正确
        """
        if not self.current_identity or not self.current_identity.password_hash:
            return False
        
        try:
            password_manager = get_global_password_manager(str(self.config_dir))
            verified = password_manager.verify_password(
                password,
                self.current_identity.password_hash,
                self.current_identity.password_salt,
            )
            if verified and password_manager.needs_rehash(
                self.current_identity.password_hash,
                self.current_identity.password_salt,
            ):
                self.logger.info("检测到旧密码哈希，正在迁移到 M3 PBKDF2 格式")
                self.set_password(password)
            return verified
        except Exception as e:
            self.logger.error(f"验证密码失败: {e}")
            return False
    
    def reset_password(self) -> str:
        """
        重置当前用户的密码（生成新密码）
        
        Returns:
            str: 新的明文密码（用户需要保存）
            
        Raises:
            RuntimeError: 如果当前身份未设置
        """
        if not self.current_identity:
            raise RuntimeError("当前身份未设置")
        
        password_manager = get_global_password_manager(str(self.config_dir))
        new_password, password_hash, password_salt = password_manager.generate_and_hash()
        self.current_identity.password_hash = password_hash
        self.current_identity.password_salt = password_salt
        self.current_identity.updated_at = time.time()
        self._save_config()
        self.logger.info("密码已重置")
        return new_password
    
    def get_password_strength_info(self) -> dict:
        """
        获取密码强度信息（用于UI显示）
        
        Returns:
            dict: 包含哈希算法、设置时间等信息
        """
        if not self.current_identity or not self.current_identity.password_hash:
            return {"has_password": False}
        
        info = {
            "has_password": True,
            "algorithm": "unknown",
            "created_at": self.current_identity.created_at,
            "updated_at": self.current_identity.updated_at
        }
        
        if self.current_identity.password_hash.startswith("pbkdf2_sha256$"):
            info["algorithm"] = "pbkdf2_sha256"
        elif self.current_identity.password_hash.startswith("$argon2"):
            info["algorithm"] = "argon2id_legacy"
        elif self.current_identity.password_hash.startswith("$fallback"):
            info["algorithm"] = "fallback_sha256_legacy"
        
        return info
    
    def _save_config(self) -> None:
        """保存配置到文件"""
        if not self.current_identity:
            return
        
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            config_file = self.config_dir / "identity.json"
            config_data = {
                "current_user": {
                    "user_id": self.current_identity.user_id,
                    "display_name": self.current_identity.display_name,
                    "original_username": self.current_identity.original_username,
                    "avatar_url": self.current_identity.avatar_url,
                    "status": self.current_identity.status.value,
                    "last_seen": self.current_identity.last_seen,
                    "created_at": self.current_identity.created_at,
                    "updated_at": self.current_identity.updated_at,
                    "password_hash": self.current_identity.password_hash,
                    "password_salt": self.current_identity.password_salt,
                },
                "contacts": {
                    contact_id: {
                        "contact_id": contact.contact_id,
                        "user_id": contact.contact_id,
                        "display_name": contact.display_name,
                        "original_username": contact.original_username,
                        "avatar_url": contact.avatar_url,
                        "status": contact.status.value,
                        "last_seen": contact.last_seen,
                        "public_key": contact.public_key,
                        "device_keys": contact.device_keys or {},
                        "device_ids": contact.device_ids or [],
                        "trust_status": contact.trust_status.value,
                        "auth_status": contact.trust_status.value,
                        "alias": contact.alias,
                        "added_at": contact.added_at,
                        "last_interaction": contact.last_interaction,
                        "pending_message_count": contact.pending_message_count,
                        "metadata": contact.metadata or {},
                    }
                    for contact_id, contact in self._contacts.items()
                }
            }
            
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
            
            self.logger.debug(f"身份配置已保存: {config_file}，包含 {len(self._contacts)} 个联系人")
            
        except Exception as e:
            self.logger.error(f"保存身份配置失败: {e}")

    def _apply_config_data(self, config_data: Dict[str, Any]) -> bool:
        """将 identity.json 内容加载到当前管理器。"""
        current_user_data = config_data.get("current_user", {})
        stored_user_id = self._extract_user_id_from_current_user(current_user_data)
        if not stored_user_id:
            self.logger.debug("配置文件中没有有效的 current_user.user_id")
            return False

        raw_status = current_user_data.get("status", UserStatus.ONLINE.value)
        try:
            current_status = UserStatus(raw_status)
        except ValueError:
            current_status = UserStatus.ONLINE

        self.current_identity = LocalIdentity(
            user_id=stored_user_id,
            display_name=current_user_data.get("display_name", ""),
            original_username=current_user_data.get("original_username") or current_user_data.get("username", ""),
            avatar_url=current_user_data.get("avatar_url"),
            status=current_status,
            last_seen=current_user_data.get("last_seen", 0.0) or 0.0,
            created_at=current_user_data.get("created_at", time.time()),
            updated_at=current_user_data.get("updated_at", time.time()),
            password_hash=current_user_data.get("password_hash"),
            password_salt=current_user_data.get("password_salt"),
        )

        self._contacts.clear()
        contacts_data = config_data.get("contacts", {})
        for stored_contact_id, contact_data in contacts_data.items():
            raw_contact_id = self.normalize_contact_id(
                contact_data.get("contact_id")
                or contact_data.get("user_id")
                or contact_data.get("id", stored_contact_id)
                or ""
            )
            if not raw_contact_id:
                self.logger.warning(f"跳过缺少 contact_id 的联系人配置: {stored_contact_id}")
                continue

            trust_status_str = contact_data.get("trust_status") or contact_data.get("auth_status", "unknown")
            trust_status = normalize_contact_auth_status(trust_status_str)

            raw_contact_status = contact_data.get("status", UserStatus.ONLINE.value)
            try:
                contact_status = UserStatus(raw_contact_status)
            except ValueError:
                contact_status = UserStatus.ONLINE

            contact = Contact(
                user_id=raw_contact_id,
                display_name=contact_data.get("display_name", ""),
                original_username=contact_data.get("original_username") or contact_data.get("username", ""),
                avatar_url=contact_data.get("avatar_url"),
                status=contact_status,
                last_seen=contact_data.get("last_seen"),
                public_key=contact_data.get("public_key"),
                device_keys=contact_data.get("device_keys") if isinstance(contact_data.get("device_keys"), dict) else {},
                device_ids=contact_data.get("device_ids") if isinstance(contact_data.get("device_ids"), list) else [],
                auth_status=trust_status,
                alias=contact_data.get("alias", ""),
                added_at=contact_data.get("added_at", time.time()),
                last_interaction=contact_data.get("last_interaction"),
                pending_message_count=contact_data.get("pending_message_count", 0),
                metadata=contact_data.get("metadata") if isinstance(contact_data.get("metadata"), dict) else {},
            )
            self._contacts[contact.contact_id] = contact

        needs_resave = current_user_data.get("user_id") != stored_user_id
        if needs_resave:
            self.logger.info("检测到旧 identity 结构，已迁移为固定 user_id 存储")
            self._save_config()

        self.logger.info(f"加载身份成功: user_id={stored_user_id}, {len(self._contacts)} 个联系人")
        return True
    
    def _load_config(self, user_id: str) -> bool:
        """
        从配置文件加载配置
        
        Args:
            user_id: 要加载的用户ID
            
        Returns:
            bool: 是否加载成功
        """
        try:
            config_data = self._read_config_data()
            if not config_data:
                self.logger.debug("配置文件不存在或为空")
                return False

            exact_user_id = self.normalize_user_id(user_id)
            stored_user_id = self._extract_user_id_from_current_user(config_data.get("current_user", {}))
            if stored_user_id != exact_user_id:
                self.logger.debug(f"配置文件中的固定 user_id={stored_user_id} 与请求 {exact_user_id} 不匹配")
                return False

            return self._apply_config_data(config_data)
            
        except Exception as e:
            self.logger.error(f"加载身份配置失败: {e}", exc_info=True)
            return False
    
    def _load_existing_config(self) -> bool:
        """
        加载现有的配置文件（不检查用户ID匹配）
        
        Returns:
            bool: 是否加载成功
        """
        try:
            config_data = self._read_config_data()
            if not config_data:
                self.logger.debug("配置文件不存在或为空")
                return False
            return self._apply_config_data(config_data)
            
        except Exception as e:
            self.logger.error(f"加载现有配置失败: {e}", exc_info=True)
            return False
    
    def identity_exists(self, user_id: str) -> bool:
        """
        检查指定用户的身份配置是否存在
        
        Args:
            user_id: 用户ID
            
        Returns:
            bool: 身份配置是否存在
        """
        try:
            stored_identity = self.get_stored_identity_summary()
            if not stored_identity:
                return False
            return stored_identity["user_id"] == self.normalize_user_id(user_id)
        except Exception:
            return False
    
    def load_or_create_identity(self, user_id: Optional[str] = None, display_name: Optional[str] = None) -> bool:
        """
        加载或创建用户身份
        
        先尝试从配置文件加载，如果不存在则创建新身份。
        
        Args:
            user_id: 用户ID（系统主身份）。如果为None或空字符串，则尝试加载现有配置，
                    如果不存在则自动生成新ID。
            display_name: 显示名称（可选）
            
        Returns:
            bool: 是否成功
        """
        exact_user_id = self.normalize_user_id(user_id or "")
        stored_identity = self.get_stored_identity_summary()

        if stored_identity:
            stored_user_id = stored_identity["user_id"]
            if exact_user_id and stored_user_id != exact_user_id:
                self.logger.error(
                    f"本地 identity 已固定为 {stored_user_id}，拒绝切换到 {exact_user_id}"
                )
                return False

            if self._load_existing_config():
                if self.current_identity:
                    self.logger.info(
                        f"[Identity] loaded existing identity: user_id={self.current_identity.user_id}, "
                        f"display_name={self.current_identity.display_name}"
                    )
                return True
            return False

        if not exact_user_id:
            exact_user_id = self.generate_user_id()
            self.logger.info(f"自动生成新用户ID: {exact_user_id}")

        return self.set_current_user(exact_user_id, display_name)


# 全局身份管理器实例（单例模式）
_global_identity_managers: Dict[str, IdentityManager] = {}


def get_global_identity_manager(config_dir: str = "data/config", store=None) -> IdentityManager:
    """
    获取全局身份管理器实例（单例模式）
    
    Args:
        config_dir: 配置文件目录
        
    Returns:
        IdentityManager: 全局身份管理器实例
    """
    global _global_identity_managers
    
    manager_key = str(Path(config_dir).expanduser().resolve())
    if manager_key not in _global_identity_managers:
        _global_identity_managers[manager_key] = IdentityManager(config_dir, store=store)
    elif store is not None and _global_identity_managers[manager_key].store is None:
        _global_identity_managers[manager_key].store = store
    
    return _global_identity_managers[manager_key]
