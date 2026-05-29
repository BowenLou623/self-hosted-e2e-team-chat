"""
聊天服务

核心业务逻辑，协调消息发送、接收、状态管理和UI更新。
使用传输层发送消息，通过事件总线与UI通信。
"""

import logging
import json
import time
import threading
from typing import Optional, Dict, Any, List
from queue import Queue
from pathlib import Path

from src.ai.conversation import ConversationStore
from src.ai.provider import AIProviderClient
from src.ai.service import AIService
from src.ai.settings import AISettings, AISettingsStore
from src.files.temp_file_service import TEMP_FILE_SCHEMA, TempFileService, TempFileServiceError
from src.models.message import Message, MessageStatus, MessageType, MessageAuthStatus, FILE_MESSAGE_METADATA_SCHEMA
from src.models.conversation import Conversation, ConversationType
from src.models.group import Group, GroupMember, GroupMemberStatus
from src.models.user import User, UserStatus
from src.models.contact import ContactAuthStatus
from src.transport.interface import Transport
from src.core.events import EventType, publish_simple, Event
from src.utils.logger import get_logger
from src.storage.sqlite_store import SQLiteStore, get_global_store
from src.identity.local_identity import IdentityManager, get_global_identity_manager
from src.identity.key_store import KeyStore, get_global_key_store
from src.identity.pairing import PairingManager, get_global_pairing_manager
from src.config.config_manager import get_global_config_manager
from src.app.launcher_events import emit_launcher_event
from src.sync.project_index_service import ProjectIndexService
from src.sync.sync_service import SyncService

# 加密服务
try:
    from src.crypto.interface import CryptoService
    from src.crypto.simple_aes_service import SimpleAESService
    from src.crypto.interface import DecryptionError
    from src.crypto.key_manager import KeyManager
    from src.crypto.device_identity import DeviceIdentityStore, profile_from_mapping
    from src.crypto.direct_v2_service import DirectV2CryptoService, ReplayProtectionError
    from src.crypto.group_crypto_service import GroupCryptoService
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    CryptoService = None  # type: ignore
    SimpleAESService = None  # type: ignore
    # 创建虚拟的DecryptionError类，以便异常处理能正常工作
    class DecryptionError(Exception):
        """虚拟解密错误类，当加密模块不可用时使用"""
        pass
    class ReplayProtectionError(DecryptionError):
        pass


class ChatService:
    """
    聊天服务

    负责：
    1. 发送消息（通过传输层）
    2. 接收消息（处理传输层的回调）
    3. 管理消息状态
    4. 模拟对方回复（第一阶段测试用）
    5. 通过事件总线通知UI更新
    """

    def __init__(
        self,
        transport: Transport,
        current_user_id: str = "self",
        db_path: str = "data/chat.db",
        crypto_service: Optional[CryptoService] = None,
        display_name: Optional[str] = None,
        store: Optional[SQLiteStore] = None,
        identity_manager: Optional[IdentityManager] = None,
        key_store: Optional[KeyStore] = None,
        pairing_manager: Optional[PairingManager] = None,
        config_dir: Optional[str] = None,
        data_dir: Optional[str] = None,
    ):
        """
        初始化聊天服务

        Args:
            transport: 传输层实例
            current_user_id: 当前用户ID（系统主身份）
            db_path: SQLite数据库文件路径
            crypto_service: 加密服务实例
            display_name: 显示名称（可选）
        """
        self.transport = transport
        self.current_user_id = (current_user_id or "").strip()
        self.logger = get_logger("chat_service")
        self.db_path = db_path
        self.config_dir = config_dir or "data/config"
        self.data_dir = data_dir or "data"
        self.temp_file_base_url = ""
        self._uses_injected_identity_manager = identity_manager is not None

        # SQLite存储
        self.storage = store or get_global_store(db_path)

        # 身份管理器
        self.identity_manager = identity_manager or get_global_identity_manager(self.config_dir, store=self.storage)
        self._ensure_current_identity(current_user_id, display_name)

        # 第四阶段：Syncthing 项目文件同步服务。只管理配置、状态和文件事件
        # metadata，文件内容始终由 Syncthing 同步。
        self.sync_service = SyncService(
            storage=self.storage,
            config_dir=self.config_dir,
            current_user_id=self.current_user_id,
        )
        self.project_index_service = ProjectIndexService(storage=self.storage)
        self.temp_file_service = self._build_temp_file_service()

        # 当前活跃会话
        self.current_conversation_id: Optional[str] = None
        
        # 线程锁，保护会话和群组缓存
        self._conversations_lock = threading.Lock()
        self._groups_lock = threading.Lock()

        # 群组从数据库加载
        self._groups: Dict[str, Group] = {}
        self._group_members: Dict[str, List[GroupMember]] = {}
        self._load_groups()
        
        # 会话从数据库加载，如果没有则使用模拟数据
        self._conversations: Dict[str, Conversation] = self._load_conversations()

        # 运行标志（用于可能的后台任务）
        self._running = False

        # 加密服务
        if crypto_service is not None:
            self.crypto_service = crypto_service
        elif CRYPTO_AVAILABLE:
            # 创建默认的AES加密服务
            from src.crypto.key_manager import KeyManager
            from src.crypto.simple_aes_service import SimpleAESService
            key_manager = KeyManager()
            self.crypto_service = SimpleAESService(key_manager)
            self.logger.info(f"使用默认加密服务: {self.crypto_service.get_encryption_version()}")
        else:
            self.crypto_service = None
            self.logger.warning("加密服务不可用，消息将以明文传输")

        self.device_identity_store = None
        self.direct_crypto_service = None
        self.group_crypto_service = None
        if CRYPTO_AVAILABLE:
            try:
                self.device_identity_store = DeviceIdentityStore(self.config_dir)
                self.device_identity_store.load_or_create()
                self.direct_crypto_service = DirectV2CryptoService(
                    self.device_identity_store,
                    self.storage,
                    self.current_user_id,
                )
                self.group_crypto_service = GroupCryptoService(self.storage)
                self.logger.info(
                    "第七阶段加密已启用: direct_encrypted_v2, group_encrypted_v1, "
                    f"device_id={self.device_identity_store.load_or_create().local_device_id}"
                )
            except Exception as e:
                self.logger.error(f"初始化第七阶段加密失败，保留 legacy 加密: {e}", exc_info=True)
                self.direct_crypto_service = None
                self.group_crypto_service = None

        # 信任管理
        self.key_store = key_store or get_global_key_store(self.storage)
        self.pairing_manager = pairing_manager or get_global_pairing_manager(self.key_store, self.identity_manager)

        # 注册传输层回调
        self.transport.register_receiver(self.current_user_id, self._on_message_received)
        
        # 设置连接状态回调（如果传输层支持）
        if hasattr(self.transport, 'set_connection_state_callback'):
            self.transport.set_connection_state_callback(self._on_connection_state_changed)
        if hasattr(self.transport, 'set_online_users_callback'):
            self.transport.set_online_users_callback(self._on_online_users_changed)

        # 解码鲁棒性配置
        self.decode_robust_mode = get_global_config_manager().get_config().decode_robust_mode
        self.logger.info(f"解码鲁棒性模式: {'启用' if self.decode_robust_mode else '禁用'}")
        
        self.logger.info(
            f"聊天服务初始化完成，当前用户: user_id={self.current_user_id}, "
            f"display_name={self.get_current_user_display_name()}"
        )

    def _build_temp_file_service(self) -> TempFileService:
        status = {}
        try:
            status = self.transport.get_status()
        except Exception:
            status = {}
        hub_address = str(status.get("server_address") or "127.0.0.1:8080")
        return TempFileService(
            hub_address=hub_address,
            data_dir=self.data_dir,
            temp_file_base_url=self.temp_file_base_url,
        )

    def _ensure_current_identity(self, requested_user_id: Optional[str], requested_display_name: Optional[str]) -> None:
        """确保 ChatService 与 IdentityManager 最终绑定到同一个 user_id。"""
        exact_requested_user_id = self.identity_manager.normalize_user_id(requested_user_id or "")
        exact_requested_display_name = (requested_display_name or "").strip()

        if self._uses_injected_identity_manager and self.identity_manager.get_current_user_id():
            injected_user_id = self.identity_manager.get_current_user_id()
            if exact_requested_user_id and injected_user_id != exact_requested_user_id:
                self.logger.warning(
                    f"M2-B: 使用已注入身份管理器中的 user_id={injected_user_id}，忽略不一致的请求 user_id={exact_requested_user_id}"
                )
            if (
                exact_requested_display_name
                and injected_user_id == exact_requested_user_id
                and self.identity_manager.get_current_user() is not None
                and self.identity_manager.get_current_user().display_name != exact_requested_display_name
            ):
                self.identity_manager.update_display_name(exact_requested_display_name)
        else:
            if not self.identity_manager.load_or_create_identity(
                exact_requested_user_id or None,
                exact_requested_display_name or None,
            ):
                raise ValueError(f"无法初始化当前用户身份: {exact_requested_user_id or '<auto>'}")

        current_user = self.identity_manager.get_current_user()
        if current_user is None:
            raise ValueError("身份管理器未返回当前用户")
        self.current_user_id = current_user.user_id

    def get_current_user_display_name(self) -> str:
        """当前用户显示层名称，优先 display_name，否则回退到 user_id。"""
        current_user = self.identity_manager.get_current_user()
        if current_user is not None:
            return current_user.get_display_name()
        return self.current_user_id

    def get_current_user_raw_display_name(self) -> str:
        """当前用户真实 display_name；为空时不回退 user_id。"""
        current_user = self.identity_manager.get_current_user()
        if current_user is not None:
            return (current_user.display_name or "").strip()
        return ""

    def sync_current_user_profile_to_transport(self) -> None:
        """把当前 user_id/display_name 同步给传输层在线目录。"""
        raw_display_name = self.get_current_user_raw_display_name()
        profile = {}
        if self.direct_crypto_service is not None:
            try:
                profile = self.direct_crypto_service.local_public_profile()
            except Exception as e:
                self.logger.warning(f"读取本机设备公钥失败: {e}")
        set_profile = getattr(self.transport, "set_user_profile", None)
        if callable(set_profile):
            try:
                set_profile(self.current_user_id, raw_display_name, profile)
            except TypeError:
                set_profile(self.current_user_id, raw_display_name)

    def update_transport_display_name(self) -> None:
        """display_name 修改后同步传输层。"""
        raw_display_name = self.get_current_user_raw_display_name()
        self.sync_current_user_profile_to_transport()
        update_display_name = getattr(self.transport, "update_display_name", None)
        if callable(update_display_name):
            update_display_name(raw_display_name)
        else:
            return

    def sync_groups_to_transport(self) -> None:
        """把本地群组快照同步给传输层/Hub，用于 Hub 重启后恢复 fanout。"""
        sync_func = getattr(self.transport, "sync_groups", None)
        if not callable(sync_func):
            return

        payload = []
        with self._groups_lock:
            for group_id, group in self._groups.items():
                members = self._group_members.get(group_id, [])
                payload.append({
                    "group": group.to_dict(),
                    "members": [member.to_dict() for member in members],
                })

        try:
            sync_func(payload)
        except Exception as e:
            self.logger.warning(f"同步群组到传输层失败: {e}")

    def get_online_user_profiles(self, refresh: bool = False) -> Dict[str, Dict[str, str]]:
        """返回传输层已知的在线用户目录，键为真实 user_id。"""
        profiles: Dict[str, Dict[str, str]] = {}
        if refresh:
            refresh_func = getattr(self.transport, "refresh_online_users", None)
            if callable(refresh_func):
                try:
                    refreshed = refresh_func()
                    if isinstance(refreshed, dict):
                        profiles = refreshed
                except Exception as e:
                    self.logger.warning(f"刷新在线用户目录失败: {e}")

        if not profiles:
            get_profiles = getattr(self.transport, "get_online_user_profiles", None)
            if callable(get_profiles):
                try:
                    loaded = get_profiles()
                    if isinstance(loaded, dict):
                        profiles = loaded
                except Exception as e:
                    self.logger.warning(f"读取在线用户目录失败: {e}")

        normalized_profiles: Dict[str, Dict[str, str]] = {}
        for user_id, profile in profiles.items():
            exact_user_id = self.identity_manager.normalize_contact_id(str(user_id))
            if not exact_user_id or not isinstance(profile, dict):
                continue
            normalized_profiles[exact_user_id] = {
                "user_id": exact_user_id,
                "display_name": str(profile.get("display_name", "") or "").strip(),
                "device_id": str(profile.get("device_id", "") or "").strip(),
                "device_name": str(profile.get("device_name", "") or "").strip(),
                "device_public_key": str(profile.get("device_public_key", "") or "").strip(),
                "device_fingerprint": str(profile.get("device_fingerprint", "") or "").strip(),
                "devices": profile.get("devices", []) if isinstance(profile.get("devices"), list) else [],
            }
        if refresh:
            self.logger.info(f"在线用户目录刷新完成: users={sorted(normalized_profiles.keys())}")
        return normalized_profiles

    def _normalize_online_profiles(
        self,
        profiles: Optional[Dict[str, Dict[str, str]]],
    ) -> Dict[str, Dict[str, str]]:
        """规范化在线目录 profile，确保键是精确 user_id。"""
        normalized_profiles: Dict[str, Dict[str, str]] = {}
        for user_id, profile in (profiles or {}).items():
            exact_user_id = self.identity_manager.normalize_contact_id(str(user_id))
            if not exact_user_id or not isinstance(profile, dict):
                continue
            normalized_profiles[exact_user_id] = {
                "user_id": exact_user_id,
                "display_name": str(profile.get("display_name", "") or "").strip(),
                "device_id": str(profile.get("device_id", "") or "").strip(),
                "device_name": str(profile.get("device_name", "") or "").strip(),
                "device_public_key": str(profile.get("device_public_key", "") or "").strip(),
                "device_fingerprint": str(profile.get("device_fingerprint", "") or "").strip(),
                "devices": profile.get("devices", []) if isinstance(profile.get("devices"), list) else [],
            }
        return normalized_profiles

    def sync_contact_presence_from_online_profiles(
        self,
        profiles: Optional[Dict[str, Dict[str, str]]] = None,
        refresh: bool = False,
    ) -> bool:
        """以 Hub 在线目录为准，同步已有联系人的在线状态和显示名。"""
        normalized_profiles = self._normalize_online_profiles(
            profiles if profiles is not None else self.get_online_user_profiles(refresh=refresh)
        )
        online_user_ids = set(normalized_profiles.keys())
        contacts = self.identity_manager.get_contacts()
        changed_contact_ids: List[str] = []
        updated_profile_ids: List[str] = []
        now = time.time()

        for contact_id, contact in contacts.items():
            exact_contact_id = self.identity_manager.normalize_contact_id(contact_id)
            if not exact_contact_id or exact_contact_id == self.current_user_id:
                continue

            profile = normalized_profiles.get(exact_contact_id)
            desired_status = UserStatus.ONLINE if profile else UserStatus.OFFLINE
            desired_display_name = (
                profile.get("display_name", "").strip()
                if profile
                else (contact.display_name or "")
            )
            desired_device_profile = {
                key: value
                for key, value in (profile or {}).items()
                if key in {"device_id", "device_name", "device_public_key", "device_fingerprint"} and value
            }
            desired_device_keys = {}
            desired_device_ids: List[str] = []
            if desired_device_profile.get("device_id") and desired_device_profile.get("device_public_key"):
                desired_device_keys = {
                    desired_device_profile["device_id"]: desired_device_profile["device_public_key"]
                }
                desired_device_ids = [desired_device_profile["device_id"]]

            status_changed = contact.status != desired_status
            display_name_changed = bool(profile and desired_display_name and contact.display_name != desired_display_name)
            metadata_changed = bool(
                desired_device_profile
                and (contact.metadata or {}).get("device_identity") != desired_device_profile
            )
            if not status_changed and not display_name_changed and not metadata_changed:
                continue

            updated_user = User(
                user_id=exact_contact_id,
                display_name=desired_display_name,
                original_username=contact.original_username,
                status=desired_status,
                last_seen=now if desired_status == UserStatus.ONLINE else contact.last_seen,
                avatar_url=contact.avatar_url,
                public_key=contact.public_key,
                device_keys={**(contact.device_keys or {}), **desired_device_keys},
                device_ids=list(dict.fromkeys([*(contact.device_ids or []), *desired_device_ids])),
                metadata={
                    **(contact.metadata or {}),
                    **({"device_identity": desired_device_profile} if desired_device_profile else {}),
                },
            )
            if self.identity_manager.add_contact(updated_user, contact.trust_status):
                changed_contact_ids.append(exact_contact_id)
                if display_name_changed:
                    updated_profile_ids.append(exact_contact_id)

        if not changed_contact_ids:
            return False

        publish_simple(EventType.USER_STATUS_CHANGED, {
            "online_user_ids": sorted(online_user_ids),
            "changed_contact_ids": sorted(set(changed_contact_ids)),
            "profiles": normalized_profiles,
        }, source="chat_service")

        if updated_profile_ids:
            publish_simple(EventType.USER_UPDATED, {
                "user_ids": sorted(set(updated_profile_ids)),
                "profiles": normalized_profiles,
            }, source="chat_service")

        self.logger.info(
            f"联系人在线状态已同步: changed={sorted(set(changed_contact_ids))}, "
            f"online={sorted(online_user_ids)}"
        )
        return True

    def refresh_presence(self) -> bool:
        """刷新在线目录并同步联系人 presence。"""
        profiles = self.get_online_user_profiles(refresh=True)
        return self.sync_contact_presence_from_online_profiles(profiles=profiles)

    def _on_online_users_changed(self, profiles: Dict[str, Dict[str, str]]) -> None:
        """传输层在线目录变化回调。"""
        normalized_profiles = self._normalize_online_profiles(profiles)
        self.logger.info(f"收到在线用户目录更新: users={sorted(normalized_profiles.keys())}")
        self.sync_contact_presence_from_online_profiles(profiles=normalized_profiles)

    def _get_peer_device_profile(self, user_id: str, refresh: bool = False) -> Dict[str, str]:
        """读取对端设备公钥信息，优先在线目录，其次联系人 metadata。"""
        profiles = self._get_peer_device_profiles(user_id, refresh=refresh)
        return profiles[0] if profiles else {}

    def _public_key_fingerprint(self, public_key: str) -> str:
        if not public_key or not CRYPTO_AVAILABLE:
            return ""
        try:
            return DeviceIdentityStore._fingerprint(public_key)
        except Exception:
            return ""

    def _cache_peer_device_profiles(self, user_id: str, profiles: List[Dict[str, str]]) -> None:
        """把在线目录中见过的设备公钥记入联系人，供离线发送继续使用。"""
        if not profiles:
            return
        exact_user_id = self.identity_manager.normalize_contact_id(user_id)
        contact = self.identity_manager.get_contact(exact_user_id)
        if contact is None:
            return

        device_keys = {
            str(profile.get("device_id") or "").strip(): str(profile.get("device_public_key") or "").strip()
            for profile in profiles
            if str(profile.get("device_id") or "").strip()
            and str(profile.get("device_public_key") or "").strip()
        }
        if not device_keys:
            return

        device_ids = list(device_keys.keys())
        primary_profile = profiles[0]
        metadata = {
            **(contact.metadata or {}),
            "device_identity": primary_profile,
        }
        if len(profiles) > 1:
            metadata["device_identities"] = profiles

        updated_user = User(
            user_id=exact_user_id,
            display_name=contact.display_name,
            original_username=contact.original_username,
            status=contact.status,
            last_seen=contact.last_seen,
            avatar_url=contact.avatar_url,
            public_key=contact.public_key,
            device_keys={**(contact.device_keys or {}), **device_keys},
            device_ids=list(dict.fromkeys([*(contact.device_ids or []), *device_ids])),
            metadata=metadata,
        )
        self.identity_manager.add_contact(updated_user, contact.trust_status)

    def _get_peer_device_profiles(self, user_id: str, refresh: bool = False) -> List[Dict[str, str]]:
        """读取对端所有已知设备公钥信息，优先在线目录，其次联系人缓存和历史 session。"""
        exact_user_id = self.identity_manager.normalize_contact_id(user_id)
        profiles = self.get_online_user_profiles(refresh=refresh)
        online_profile = profiles.get(exact_user_id) or {}
        device_profiles: List[Dict[str, str]] = []
        seen_device_ids = set()

        def add_profile(raw_profile: Any) -> None:
            profile = profile_from_mapping(raw_profile) if CRYPTO_AVAILABLE else {}
            device_id = str(profile.get("device_id") or "")
            if device_id and profile.get("device_public_key") and device_id not in seen_device_ids:
                seen_device_ids.add(device_id)
                device_profiles.append(profile)

        add_profile(online_profile)
        for device in online_profile.get("devices", []) if isinstance(online_profile, dict) else []:
            add_profile(device)
        if device_profiles:
            self._cache_peer_device_profiles(exact_user_id, device_profiles)
            return device_profiles

        contact = self.identity_manager.get_contact(exact_user_id)
        if contact is not None:
            metadata_profile = (contact.metadata or {}).get("device_identity")
            add_profile(metadata_profile)
            for metadata_device in (contact.metadata or {}).get("device_identities", []):
                add_profile(metadata_device)
            for device_id, public_key in (contact.device_keys or {}).items():
                add_profile({
                    "device_id": device_id,
                    "device_public_key": public_key,
                    "device_fingerprint": self._public_key_fingerprint(public_key),
                })
            if device_profiles:
                return device_profiles

        session_reader = getattr(self.storage, "get_crypto_sessions_for_user", None)
        if callable(session_reader):
            for session in session_reader(exact_user_id):
                public_key = str(session.get("peer_public_key") or "").strip()
                add_profile({
                    "device_id": str(session.get("peer_device_id") or "").strip(),
                    "device_public_key": public_key,
                    "device_fingerprint": self._public_key_fingerprint(public_key),
                })
            if device_profiles:
                self._cache_peer_device_profiles(exact_user_id, device_profiles)
                return device_profiles
        return []

    def get_current_device_summary(self) -> Dict[str, str]:
        """返回本机设备身份摘要，不包含私钥。"""
        if self.device_identity_store is None:
            return {}
        identity = self.device_identity_store.load_or_create()
        return {
            "device_id": identity.local_device_id,
            "device_name": identity.device_name,
            "device_fingerprint": identity.fingerprint,
            "device_public_key": identity.public_key,
        }

    def _attach_group_key_packets(self, group: Group, members: List[GroupMember], reason: str) -> None:
        """Rotate/attach encrypted group key packets for current group members."""
        if self.group_crypto_service is None or self.direct_crypto_service is None:
            return
        try:
            group_key = self.group_crypto_service.rotate_group_key(group.id, reason=reason)
            packet_plaintext = json.dumps(self.group_crypto_service.export_key_packet(group.id), ensure_ascii=False)
            packets: Dict[str, Dict[str, Any]] = {}
            for member in members:
                if member.user_id == self.current_user_id or member.status == GroupMemberStatus.REMOVED:
                    continue
                peer_device = self._get_peer_device_profile(member.user_id, refresh=True)
                if not (peer_device.get("device_id") and peer_device.get("device_public_key")):
                    self.logger.warning(f"无法为群成员生成 group key packet，缺少设备密钥: {member.user_id}")
                    continue
                packet_message_id = f"groupkey:{group.id}:{group_key['group_key_version']}:{member.user_id}"
                packets[member.user_id] = self.direct_crypto_service.encrypt_message(
                    plaintext=packet_plaintext,
                    sender_id=self.current_user_id,
                    recipient_id=member.user_id,
                    recipient_device_id=peer_device["device_id"],
                    recipient_public_key=peer_device["device_public_key"],
                    message_id=packet_message_id,
                    scope="group_key",
                )
            group.metadata = {
                **(group.metadata or {}),
                "group_key_id": group_key["group_key_id"],
                "group_key_version": int(group_key["group_key_version"]),
                "group_key_packets": packets,
                "group_key_rotated_at": time.time(),
                "group_key_rotation_reason": reason,
            }
        except Exception as e:
            self.logger.error(f"生成 group key packet 失败: {e}", exc_info=True)

    def _import_group_key_packet_if_present(self, group: Group, inviter_id: str) -> None:
        """Decrypt and store a group key packet addressed to the current user."""
        if self.group_crypto_service is None or self.direct_crypto_service is None:
            return
        if inviter_id == self.current_user_id:
            return
        packets = (group.metadata or {}).get("group_key_packets")
        if not isinstance(packets, dict):
            return
        packet_metadata = packets.get(self.current_user_id)
        if not isinstance(packet_metadata, dict):
            return
        peer_device = self._get_peer_device_profile(inviter_id or group.creator_id, refresh=True)
        if not peer_device.get("device_public_key"):
            self.logger.warning(f"无法导入群密钥，缺少邀请者设备公钥: group={group.id}, inviter={inviter_id}")
            return
        try:
            packet_message_id = (
                f"groupkey:{group.id}:{packet_metadata.get('key_version', group.metadata.get('group_key_version', 1))}:"
                f"{self.current_user_id}"
            )
            plaintext = self.direct_crypto_service.decrypt_message(
                metadata=packet_metadata,
                message_id=packet_message_id,
                sender_id=inviter_id or group.creator_id,
                recipient_id=self.current_user_id,
                sender_public_key=peer_device["device_public_key"],
                scope="group_key",
            )
            packet = json.loads(plaintext)
            if isinstance(packet, dict) and packet.get("schema") == "group_key_packet_v1":
                self.group_crypto_service.import_group_key(
                    group.id,
                    int(packet.get("group_key_version") or 1),
                    str(packet.get("group_key_id") or ""),
                    str(packet.get("key_material") or ""),
                    metadata={"imported_from": inviter_id, "imported_at": time.time()},
                )
                self.logger.info(
                    f"已导入群密钥: group={group.id}, version={packet.get('group_key_version')}, "
                    f"key_id={packet.get('group_key_id')}"
                )
        except ReplayProtectionError:
            self.logger.info(f"group key packet 已处理过: group={group.id}")
        except Exception as e:
            self.logger.error(f"导入 group key packet 失败: group={group.id}, error={e}", exc_info=True)

    def resolve_contact_input(self, raw_contact_input: str) -> Dict[str, Any]:
        """
        解析联系人输入。

        只把真实 user_id 视为可添加目标；display_name 命中时返回明确状态，
        避免把显示名误写成 contact_id。
        """
        raw_input = (raw_contact_input or "").strip()
        if not raw_input:
            return {"status": "invalid", "reason": "empty"}
        if any(ch.isspace() for ch in raw_input):
            return {"status": "invalid", "reason": "whitespace"}

        exact_contact_id = self.identity_manager.normalize_contact_id(raw_input)
        if exact_contact_id == self.current_user_id:
            return {"status": "self", "user_id": exact_contact_id}

        profiles = self.get_online_user_profiles(refresh=True)
        self.sync_contact_presence_from_online_profiles(profiles=profiles)
        if exact_contact_id in profiles:
            profile = profiles[exact_contact_id]
            return {
                "status": "found",
                "user_id": exact_contact_id,
                "display_name": profile.get("display_name", ""),
            }

        for online_user_id, profile in profiles.items():
            display_name = (profile.get("display_name") or "").strip()
            if display_name and display_name == raw_input:
                return {
                    "status": "display_name",
                    "user_id": online_user_id,
                    "display_name": display_name,
                }

        if profiles:
            self.logger.info(
                f"联系人解析未命中: input={raw_input}, normalized={exact_contact_id}, "
                f"known_online_users={sorted(profiles.keys())}"
            )
            return {"status": "not_found", "user_id": exact_contact_id}

        existing_contact = self.identity_manager.get_contact(exact_contact_id)
        if existing_contact is not None:
            return {
                "status": "found",
                "user_id": exact_contact_id,
                "display_name": existing_contact.display_name,
            }

        self.logger.info(
            f"联系人解析未命中: input={raw_input}, normalized={exact_contact_id}, "
            "known_online_users=[]"
        )
        return {"status": "not_found", "user_id": exact_contact_id}

    def cleanup_stale_display_name_contact(self, display_name: str, actual_user_id: str) -> None:
        """移除由旧 UI 把 display_name 误当 contact_id 创建的联系人。"""
        stale_contact_id = (display_name or "").strip()
        exact_actual_user_id = self.identity_manager.normalize_contact_id(actual_user_id)
        if not stale_contact_id or stale_contact_id == exact_actual_user_id:
            return
        profiles = self.get_online_user_profiles(refresh=False)
        if stale_contact_id in profiles:
            return
        stale_contact = self.identity_manager.get_contact(stale_contact_id)
        if stale_contact is None:
            return
        if (stale_contact.display_name or "").strip() not in {"", stale_contact_id}:
            return
        self.identity_manager.remove_contact(stale_contact_id)
        self.logger.info(
            f"已清理旧版误建联系人: display_name={stale_contact_id}, actual_user_id={exact_actual_user_id}"
        )

    def sync_contact_display_names_from_online_profiles(self, refresh: bool = False) -> bool:
        """用在线目录回填已有联系人的 display_name。"""
        profiles = self.get_online_user_profiles(refresh=refresh)
        if not profiles:
            return False

        changed = False
        contacts = self.identity_manager.get_contacts()
        for contact_id, contact in contacts.items():
            profile = profiles.get(contact_id)
            if not profile:
                continue
            display_name = (profile.get("display_name") or "").strip()
            if display_name and contact.display_name != display_name:
                if self.identity_manager.update_contact_display_name(contact_id, display_name):
                    changed = True
        return changed

    def get_contact_display_name(self, contact_id: str) -> str:
        """联系人主显示名：优先 display_name，否则回退 contact_id。"""
        exact_contact_id = self.identity_manager.normalize_contact_id(contact_id)
        contact = self.identity_manager.get_contact(exact_contact_id)
        if contact is not None:
            return contact.get_display_name()
        return exact_contact_id

    def get_contact_display_label(self, contact_id: str) -> str:
        """联系人列表显示：主显示优先名称，必要时附带 contact_id。"""
        exact_contact_id = self.identity_manager.normalize_contact_id(contact_id)
        contact = self.identity_manager.get_contact(exact_contact_id)
        if contact is not None:
            return contact.get_list_display_name()
        return exact_contact_id

    def get_contact_trust_status(self, contact_id: str) -> ContactAuthStatus:
        """返回联系人当前聊天授权状态。"""
        exact_contact_id = self.identity_manager.normalize_contact_id(contact_id)
        contact = self.identity_manager.get_contact(exact_contact_id)
        if contact is None:
            return ContactAuthStatus.UNKNOWN
        return contact.trust_status

    def can_send_to_contact(self, contact_id: str) -> bool:
        """发送前统一只看联系人授权状态。"""
        return self.get_contact_trust_status(contact_id) == ContactAuthStatus.TRUSTED

    def get_send_block_reason(self, contact_id: str) -> Optional[str]:
        """返回发送被拦截的原因；可发送时返回 None。"""
        exact_contact_id = self.identity_manager.normalize_contact_id(contact_id)
        if not exact_contact_id:
            return "联系人ID不能为空"
        if exact_contact_id == self.current_user_id:
            return "不能给自己发送消息"

        trust_status = self.get_contact_trust_status(exact_contact_id)
        if trust_status == ContactAuthStatus.TRUSTED:
            return None
        if trust_status == ContactAuthStatus.REJECTED:
            return "该联系人已被拒绝，发送已被拦截"
        if trust_status == ContactAuthStatus.PENDING_INCOMING:
            return "该联系人仍待授权，接受后才能发送消息"
        return "该联系人尚未授权，暂不能发送消息"

    def _build_message_preview(self, content: str, limit: int = 50) -> str:
        """生成统一的消息预览文本。"""
        if not content:
            return ""
        return content[:limit] + ("..." if len(content) > limit else "")

    def accept_contact(self, contact_id: str) -> bool:
        """接受联系人，将 pending 消息提升为正式消息。"""
        exact_contact_id = self.identity_manager.normalize_contact_id(contact_id)
        contact = self.identity_manager.get_contact(exact_contact_id)
        if contact is None:
            self.logger.warning(f"尝试接受不存在的联系人: {exact_contact_id}")
            return False

        promoted_count = self.storage.bulk_update_message_auth_status_by_sender(
            exact_contact_id,
            MessageAuthStatus.PENDING,
            MessageAuthStatus.TRUSTED,
        )
        self.identity_manager.update_contact_auth_status(exact_contact_id, ContactAuthStatus.TRUSTED)
        self.identity_manager.clear_contact_pending_count(exact_contact_id)
        self._sync_conversation_after_accept(exact_contact_id, promoted_count)

        self.logger.info(
            f"M2-C 接受联系人: contact_id={exact_contact_id}, promoted_messages={promoted_count}"
        )
        return True

    def reject_contact(self, contact_id: str) -> bool:
        """拒绝联系人，将 pending 消息标记为 rejected。"""
        exact_contact_id = self.identity_manager.normalize_contact_id(contact_id)
        contact = self.identity_manager.get_contact(exact_contact_id)
        if contact is None:
            self.logger.warning(f"尝试拒绝不存在的联系人: {exact_contact_id}")
            return False

        rejected_count = self.storage.bulk_update_message_auth_status_by_sender(
            exact_contact_id,
            MessageAuthStatus.PENDING,
            MessageAuthStatus.REJECTED,
        )
        self.identity_manager.update_contact_auth_status(exact_contact_id, ContactAuthStatus.REJECTED)
        self.identity_manager.clear_contact_pending_count(exact_contact_id)

        self.logger.info(
            f"M2-C 拒绝联系人: contact_id={exact_contact_id}, rejected_messages={rejected_count}"
        )
        return True

    def _sync_conversation_after_accept(self, contact_id: str, promoted_count: int) -> None:
        """
        pending 消息被接受后，补齐正式会话的最后消息与未读数。
        """
        conversation_id = self._get_or_create_conversation_id(contact_id)
        trusted_messages = [
            message
            for message in self.load_messages_for_contact(contact_id, limit=max(promoted_count * 5, 100))
            if message.auth_status == MessageAuthStatus.TRUSTED
        ]

        with self._conversations_lock:
            conv = self._conversations.get(conversation_id)
            if conv is None:
                conv = Conversation(
                    id=conversation_id,
                    participant_ids=[self.current_user_id, contact_id],
                    display_name=self._get_display_name_for_user(contact_id),
                )
                self._conversations[conversation_id] = conv

            if trusted_messages:
                latest_message = trusted_messages[-1]
                conv.update_last_message(
                    message_id=latest_message.id,
                    preview=self._build_message_preview(latest_message.content),
                    timestamp=latest_message.timestamp,
                )

            if self.current_conversation_id == conversation_id:
                conv.unread_count = 0
            elif promoted_count > 0:
                conv.unread_count += promoted_count

            self.storage.save_conversation(conv)

        publish_simple(EventType.CONVERSATION_UPDATED, {
            "conversation_id": conversation_id,
            "conversation": self._conversations[conversation_id].to_dict(),
        }, source="chat_service")

    def is_message_for_contact(self, message: Message, contact_id: str) -> bool:
        """消息归属规则：仅按 current_user_id 与 active_contact_id 双向精确匹配。"""
        exact_contact_id = self.identity_manager.normalize_contact_id(contact_id)
        if not exact_contact_id:
            return False
        return (
            (message.sender_id == self.current_user_id and message.receiver_id == exact_contact_id)
            or
            (message.sender_id == exact_contact_id and message.receiver_id == self.current_user_id)
        )

    def is_message_for_conversation(self, message: Message, conversation_id: str) -> bool:
        """判断消息是否属于指定会话，支持 direct/group。"""
        if not conversation_id:
            return False
        if conversation_id.startswith("grp_"):
            return message.conversation_id == conversation_id or message.receiver_id == conversation_id
        conv = self._conversations.get(conversation_id)
        if conv and conv.conversation_type == ConversationType.GROUP:
            return message.conversation_id == conversation_id or message.receiver_id == conversation_id
        return message.conversation_id == conversation_id

    def _normalize_encrypted_message_metadata(self, message: Message) -> None:
        """
        从 message.content 中兜底识别加密元数据。

        网络层理论上应该已经把 encryption_version / encrypted_content 填好，
        但 M2-A 先在业务层再做一次兜底，避免原始 JSON 直接落进 UI。
        """
        if message.encryption_version and message.encrypted_content:
            return

        try:
            metadata = json.loads(message.content)
        except (json.JSONDecodeError, TypeError, ValueError):
            return

        if not isinstance(metadata, dict):
            return
        if "encryption_version" not in metadata or "ciphertext" not in metadata or "nonce" not in metadata:
            return

        message.encryption_version = str(metadata.get("encryption_version"))
        message.encrypted_content = json.dumps(metadata, ensure_ascii=False)
        self.logger.warning(
            f"M2-A 解密兜底命中: 从 message.content 自动恢复加密元数据, msg_id={message.id[:8]}, "
            f"version={message.encryption_version}"
        )

    def _is_encrypted_payload_text(self, content: str) -> bool:
        """判断文本本身是否还是加密载荷 JSON。"""
        if not content or not isinstance(content, str):
            return False

        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            return False

        if not isinstance(payload, dict):
            return False

        required_keys = {"ciphertext", "nonce", "encryption_version", "key_fingerprint"}
        return required_keys.issubset(payload.keys())

    def _decrypt_message_if_needed(self, message: Message) -> str:
        """
        尝试就地解密消息，返回解密状态。
        """
        self._normalize_encrypted_message_metadata(message)

        content_looks_encrypted = self._is_encrypted_payload_text(message.content)
        is_placeholder = message.content.startswith("[Encrypted message:")
        is_outgoing = message.sender_id == self.current_user_id

        # 本端自己发出的消息在数据库中保存的是明文展示内容；
        # 即使保留了 encrypted_content，也不应该在历史加载时再次尝试解密。
        if is_outgoing and not content_looks_encrypted:
            return "outgoing_plaintext"

        # 已经是可展示明文的历史消息，不要在重复点击联系人时反复解密。
        if not content_looks_encrypted and not is_placeholder:
            return "already_plaintext"

        if not (message.encryption_version and message.encrypted_content):
            self.logger.info(
                f"收到未加密消息 from {message.sender_id}, msg_id={message.id[:8]}, "
                f"payload_len={len(message.content or '')}"
            )
            return "not_encrypted"

        self.logger.info(
            f"收到加密消息 from {message.sender_id}, "
            f"msg_id={message.id[:8]}, "
            f"version={message.encryption_version}, "
            f"encrypted_len={len(message.encrypted_content) if message.encrypted_content else 0}"
        )

        if self.crypto_service is None:
            self.logger.error(
                f"收到加密消息但本地加密服务不可用: msg_id={message.id[:8]}, "
                f"sender={message.sender_id}, version={message.encryption_version}"
            )
            if message.encryption_version != "direct_encrypted_v2":
                message.content = "[Encrypted message: local crypto unavailable]"
                return "failed"

        if message.encryption_version == "direct_encrypted_v2":
            if self.direct_crypto_service is None:
                message.content = "[Encrypted message: local crypto unavailable]"
                return "failed"
            try:
                encrypted_metadata = json.loads(message.encrypted_content)
                sender_device_id = encrypted_metadata.get("sender_device_id", "")
                peer_device = self._get_peer_device_profile(message.sender_id, refresh=True)
                if not peer_device.get("device_public_key"):
                    raise DecryptionError("缺少发送方设备公钥")
                plaintext = self.direct_crypto_service.decrypt_message(
                    metadata=encrypted_metadata,
                    message_id=message.id,
                    sender_id=message.sender_id,
                    recipient_id=self.current_user_id,
                    sender_public_key=peer_device["device_public_key"],
                    scope="direct",
                )
                message.content = plaintext
                self._apply_decrypted_payload_if_needed(message, plaintext)
                self.logger.info(
                    f"direct v2 消息解密成功: msg_id={message.id[:8]}, "
                    f"sender_device={sender_device_id}, key_id={encrypted_metadata.get('key_id', '')}"
                )
                return "success"
            except ReplayProtectionError as e:
                self.logger.warning(
                    f"direct v2 replay/nonce 拒绝: msg_id={message.id[:8]}, sender={message.sender_id}, error={e}"
                )
                message.metadata = {
                    **(message.metadata or {}),
                    "security_rejected": True,
                    "security_reason": str(e),
                }
                message.content = "[Encrypted message: replay rejected]"
                return "replay_rejected"
            except DecryptionError as e:
                encrypted_metadata = {}
                try:
                    encrypted_metadata = json.loads(message.encrypted_content or "{}")
                except Exception:
                    pass
                self.storage.save_decryption_failure(
                    message.id,
                    message.conversation_id,
                    message.sender_id,
                    message.encryption_version or "",
                    str(encrypted_metadata.get("key_id", "")),
                    str(e),
                )
                self.logger.error(
                    f"direct v2 消息解密失败: msg_id={message.id[:8]}, sender={message.sender_id}, error={e}"
                )
                message.content = "[Encrypted message: unable to decrypt]"
                return "failed"
            except Exception as e:
                self.storage.save_decryption_failure(
                    message.id,
                    message.conversation_id,
                    message.sender_id,
                    message.encryption_version or "",
                    "",
                    str(e),
                )
                self.logger.error(
                    f"direct v2 消息解密异常: msg_id={message.id[:8]}, sender={message.sender_id}, error={e}",
                    exc_info=True,
                )
                message.content = "[Encrypted message: unable to decrypt]"
                return "failed"

        current_version = self.crypto_service.get_encryption_version()
        self.logger.debug(f"当前加密服务版本: {current_version}, 消息版本: {message.encryption_version}")

        if hasattr(self.crypto_service, 'is_version_supported'):
            version_supported = self.crypto_service.is_version_supported(message.encryption_version)
        else:
            version_supported = (message.encryption_version == current_version)

        if not version_supported:
            self.logger.error(
                f"消息加密版本不支持: {message.encryption_version}, 当前支持: {current_version}. 降级处理."
            )
            message.content = "[Encrypted message: unable to decrypt]"
            return "failed"

        try:
            encrypted_metadata = json.loads(message.encrypted_content)
            key_fingerprint = encrypted_metadata.get('key_fingerprint', 'unknown')
            self.logger.debug(f"加密元数据指纹: {key_fingerprint}")

            plaintext = self.crypto_service.decrypt_message(
                metadata=encrypted_metadata,
                recipient_id=self.current_user_id
            )
            ciphertext_len = len(message.content) if message.content else 0
            message.content = plaintext

            self.logger.info(
                f"加密消息解密成功 from {message.sender_id}, "
                f"msg_id={message.id[:8]}, "
                f"plaintext_len={len(plaintext)}, "
                f"ciphertext_len={ciphertext_len}, 指纹: {key_fingerprint}"
            )
            return "success"
        except DecryptionError as e:
            self.logger.error(
                f"消息解密失败 (DecryptionError): msg_id={message.id[:8]}, "
                f"sender={message.sender_id}, version={message.encryption_version}, "
                f"error={e}", exc_info=True
            )
            message.content = "[Encrypted message: unable to decrypt]"
            return "failed"
        except Exception as e:
            self.logger.error(
                f"消息解密失败 (Exception): msg_id={message.id[:8]}, "
                f"sender={message.sender_id}, version={message.encryption_version}, "
                f"error={e}", exc_info=True
            )
            message.content = "[Encrypted message: unable to decrypt]"
            return "failed"

    def _on_connection_state_changed(self, state: str) -> None:
        """处理传输层连接状态变化
        
        Args:
            state: 连接状态，可以是 "connecting", "connected", "disconnected", "registered"
        """
        self.logger.info(f"传输层连接状态变化: {state}")
        emit_launcher_event(
            "transport_state",
            state=state,
            user_id=self.current_user_id,
            transport_status=self.transport.get_status(),
        )
        
        if state == "connecting":
            publish_simple(EventType.CONNECTING, {
                "user_id": self.current_user_id,
                "transport_status": self.transport.get_status()
            }, source="chat_service")
        elif state == "connected":
            publish_simple(EventType.CONNECTED, {
                "user_id": self.current_user_id,
                "transport_status": self.transport.get_status()
            }, source="chat_service")
        elif state == "disconnected":
            publish_simple(EventType.DISCONNECTED, {
                "user_id": self.current_user_id
            }, source="chat_service")
        elif state == "registered":
            self.sync_contact_presence_from_online_profiles(refresh=False)
            self.sync_groups_to_transport()
        # "registered" 状态不需要单独事件，已包含在connected中

    def _get_contacts(self) -> Dict[str, User]:
        """获取联系人列表（从身份管理器）"""
        self.logger.debug("从身份管理器获取联系人列表")
        contacts = self.identity_manager.get_contacts()
        self.logger.info(f"从身份管理器获取到 {len(contacts)} 个联系人，键: {list(contacts.keys())}")
        return contacts  # type: ignore[return-value]

    def _create_mock_conversations(self) -> Dict[str, Conversation]:
        """创建模拟会话（使用前两个联系人）"""
        conversations = {}
        contacts = self._get_contacts()
        
        # 获取前两个联系人（如果有）
        contact_ids = list(contacts.keys())[:2]
        
        for i, contact_id in enumerate(contact_ids, 1):
            contact = contacts[contact_id]
            conv_id = f"conv{i}"
            
            conv = Conversation(
                id=conv_id,
                participant_ids=[self.current_user_id, contact_id],
                display_name=self.get_contact_display_name(contact_id),
                last_message_preview=f"这是与{self.get_contact_display_name(contact_id)}的对话",
                last_message_time=time.time() - 300 * i,  # 时间偏移，避免同时
                unread_count=i  # 第一个有1条未读，第二个有2条未读
            )
            conversations[conv.id] = conv
        
        return conversations

    def _load_groups(self) -> None:
        """从 SQLite 加载当前用户所在群组与成员缓存。"""
        groups = self.storage.get_groups_for_user(self.current_user_id)
        group_members = {
            group_id: self.storage.get_group_members(group_id, active_only=True)
            for group_id in groups.keys()
        }
        with self._groups_lock:
            self._groups = groups
            self._group_members = group_members
        self.logger.info(f"从数据库加载 {len(groups)} 个群组")

    def _build_group_conversation(self, group: Group, members: List[GroupMember]) -> Conversation:
        """根据群组与成员关系构建群会话。"""
        participant_ids = [member.user_id for member in members if member.status == GroupMemberStatus.ACTIVE]
        if self.current_user_id not in participant_ids:
            participant_ids.append(self.current_user_id)
        return Conversation(
            id=group.id,
            conversation_type=ConversationType.GROUP,
            participant_ids=sorted(set(participant_ids)),
            display_name=group.name,
            avatar_url=group.avatar_url,
            group_name=group.name,
            group_avatar=group.avatar_url,
            metadata={
                **(group.metadata or {}),
                "sync_status": group.metadata.get("sync_status", "reserved") if group.metadata else "reserved",
            },
            created_at=group.created_at,
            updated_at=group.updated_at,
        )

    def _deduplicate_conversations(self, conversations: Dict[str, Conversation]) -> Dict[str, Conversation]:
        """去重会话：确保每个参与者组合只有一个会话"""
        # 按参与者组合分组
        participant_groups: Dict[str, List[Conversation]] = {}
        for conv in conversations.values():
            if conv.conversation_type == ConversationType.GROUP:
                participant_groups[conv.id] = [conv]
                continue
            # 对参与者ID排序并生成键
            sorted_participants = sorted(conv.participant_ids)
            key = "|".join(sorted_participants)
            participant_groups.setdefault(key, []).append(conv)
        
        result: Dict[str, Conversation] = {}
        for key, group in participant_groups.items():
            if len(group) == 1:
                # 唯一会话，直接保留
                result[group[0].id] = group[0]
                continue
            
            # 多个会话对应同一组参与者，需要合并
            self.logger.warning(f"发现重复会话，参与者组合: {key}, 数量: {len(group)}")
            
            # 选择最新的会话（基于last_message_time）
            latest_conv = max(group, key=lambda c: c.last_message_time or c.created_at)
            
            # 合并未读计数
            total_unread = sum(c.unread_count for c in group)
            latest_conv.unread_count = total_unread
            
            # 更新数据库：删除旧会话，保留最新会话
            for conv in group:
                if conv.id != latest_conv.id:
                    self.logger.info(f"  删除重复会话: {conv.id}")
                    # 从数据库中删除旧会话及其消息
                    self.storage.delete_conversation(conv.id)
                    # 不保存到结果中
            
            # 保存最新会话到数据库
            self.storage.save_conversation(latest_conv)
            result[latest_conv.id] = latest_conv
        
        return result

    def _load_conversations(self) -> Dict[str, Conversation]:
        """从数据库加载会话，如果不存在则使用模拟数据"""
        conversations = {}

        # 先从数据库加载
        db_conversations = self.storage.get_conversations_for_user(self.current_user_id, limit=20)
        if db_conversations:
            conversations.update(db_conversations)
            self.logger.info(f"从数据库加载 {len(db_conversations)} 个会话")
            # 去重
            conversations = self._deduplicate_conversations(conversations)
            self.logger.info(f"去重后剩余 {len(conversations)} 个会话")

        with self._groups_lock:
            for group_id, group in self._groups.items():
                if group_id in conversations:
                    continue
                members = self._group_members.get(group_id, [])
                group_conversation = self._build_group_conversation(group, members)
                conversations[group_id] = group_conversation
                self.storage.save_conversation(group_conversation)

        # 如果数据库中没有会话，使用模拟数据（兼容第一阶段）
        if not conversations:
            mock_conversations = self._create_mock_conversations()
            conversations.update(mock_conversations)

            # 将模拟会话保存到数据库（便于后续使用）
            for conv in mock_conversations.values():
                self.storage.save_conversation(conv)

            self.logger.info(f"使用模拟数据创建 {len(mock_conversations)} 个会话")

        return conversations

    def start(self) -> bool:
        """
        启动聊天服务

        Returns:
            bool: 是否成功启动
        """
        try:
            self.sync_current_user_profile_to_transport()

            # 启动传输层
            if not self.transport.connect():
                self.logger.error("传输层连接失败")
                return False

            register_user_func = getattr(self.transport, "register_user", None)
            if callable(register_user_func):
                if not register_user_func(self.current_user_id):
                    self.logger.error(f"M2-B: 传输层注册当前 user_id 失败: {self.current_user_id}")
                    self.transport.disconnect()
                    publish_simple(EventType.DISCONNECTED, {
                        "user_id": self.current_user_id,
                        "reason": "register_failed",
                    }, source="chat_service")
                    return False
                self.logger.info(f"M2-B: 传输层已注册当前 user_id: {self.current_user_id}")
                profiles = self.get_online_user_profiles(refresh=True)
                self.sync_contact_presence_from_online_profiles(profiles=profiles)
                self.sync_groups_to_transport()

            self._running = True

            # 发布连接事件
            publish_simple(EventType.CONNECTED, {
                "user_id": self.current_user_id,
                "transport_status": self.transport.get_status()
            }, source="chat_service")

            self.logger.info("聊天服务启动成功")
            return True

        except Exception as e:
            self.logger.error(f"聊天服务启动失败: {e}", exc_info=True)
            return False

    def stop(self) -> None:
        """
        停止聊天服务
        """
        self.logger.info("正在停止聊天服务...")
        self._running = False

        # 停止传输层
        self.transport.disconnect()

        # 发布断开连接事件
        publish_simple(EventType.DISCONNECTED, {
            "user_id": self.current_user_id
        }, source="chat_service")

        self.logger.info("聊天服务已停止")

    def send_message(self, content: str, receiver_id: str, conversation_id: Optional[str] = None) -> Optional[Message]:
        """
        发送消息

        Args:
            content: 消息内容
            receiver_id: 接收者ID
            conversation_id: 会话ID（可选，如果为空则根据参与者查找或创建）

        Returns:
            Optional[Message]: 发送的消息对象，失败时返回None
        """
        if not content.strip():
            self.logger.warning("尝试发送空消息")
            return None
        return self._send_text_message(content, receiver_id, conversation_id)

    def send_temp_file(
        self,
        file_path: str,
        receiver_id: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> Optional[Message]:
        """发送 30 分钟有效的端到端加密临时文件 metadata。"""
        if group_id:
            return self._send_group_temp_file(file_path, group_id)
        if not receiver_id:
            return None
        return self._send_direct_temp_file(file_path, receiver_id)

    def _send_direct_temp_file(self, file_path: str, receiver_id: str) -> Optional[Message]:
        exact_receiver_id = self.identity_manager.normalize_contact_id(receiver_id)
        if not exact_receiver_id:
            return None
        if self.direct_crypto_service is None:
            self._publish_temp_file_failed("", "临时文件发送需要 direct_encrypted_v2 加密链路")
            return None
        block_reason = self.get_send_block_reason(exact_receiver_id)
        if block_reason is not None:
            self._publish_temp_file_failed("", block_reason)
            return None
        if not self._running:
            self._publish_temp_file_failed("", "聊天服务未启动或尚未完成注册")
            return None

        conversation_id = self._get_or_create_conversation_id(exact_receiver_id)
        message = Message(
            sender_id=self.current_user_id,
            receiver_id=exact_receiver_id,
            conversation_id=conversation_id,
            status=MessageStatus.SENDING,
            message_type=MessageType.FILE,
            timestamp=time.time(),
        )
        try:
            self.temp_file_service = self._build_temp_file_service()
            metadata = self.temp_file_service.encrypt_and_upload(
                file_path=file_path,
                message_id=message.id,
                sender_id=self.current_user_id,
                scope="direct",
                conversation_id=conversation_id,
            )
            content = self._build_temp_file_content(metadata)
            message.content = content
            message.metadata = metadata.copy()

            peer_device = self._get_peer_device_profile(exact_receiver_id, refresh=True)
            if not (peer_device.get("device_id") and peer_device.get("device_public_key")):
                raise TempFileServiceError("缺少对端设备密钥，请刷新在线状态或重新授权联系人")
            payload = {
                "content": content,
                "message_type": MessageType.FILE.value,
                "metadata": metadata,
            }
            encrypted_metadata = self.direct_crypto_service.encrypt_message(
                plaintext=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                sender_id=self.current_user_id,
                recipient_id=exact_receiver_id,
                recipient_device_id=peer_device["device_id"],
                recipient_public_key=peer_device["device_public_key"],
                message_id=message.id,
                scope="direct",
            )
            local_plain_metadata = metadata.copy()
            message.encryption_version = encrypted_metadata["encryption_version"]
            message.encrypted_content = json.dumps(encrypted_metadata, ensure_ascii=False)
            message.metadata = encrypted_metadata.copy()
            message.content = "[Encrypted message: direct_encrypted_v2]"
            success = self.transport.send_message(message)
            if not success:
                raise TempFileServiceError("临时文件消息发送失败")
            message.content = content
            message.metadata = local_plain_metadata
            message.status = MessageStatus.SENT
            self.storage.save_message(message)
            self.storage.save_file_attachment(
                self.sync_service.build_file_attachment(message.id, local_plain_metadata)
            )
            self._update_conversation_after_send(conversation_id, message.id, content, message.timestamp)
            publish_simple(EventType.MESSAGE_SENT, {
                "message": message.to_dict(),
                "conversation_id": conversation_id,
            }, source="chat_service")
            return message
        except Exception as e:
            message.status = MessageStatus.FAILED
            self._publish_temp_file_failed(message.id, str(e), message)
            self.logger.error(f"发送临时文件失败: {e}", exc_info=True)
            return None

    def _send_group_temp_file(self, file_path: str, group_id: str) -> Optional[Message]:
        if self.group_crypto_service is None or self.direct_crypto_service is None:
            self._publish_temp_file_failed("", "临时文件发送需要 group_encrypted_v1 加密链路")
            return None
        with self._groups_lock:
            group = self._groups.get(group_id)
            members = list(self._group_members.get(group_id, []))
        if group is None:
            return None
        active_member_ids = {member.user_id for member in members if member.status == GroupMemberStatus.ACTIVE}
        if self.current_user_id not in active_member_ids:
            self._publish_temp_file_failed("", "当前用户不是群成员")
            return None

        message = Message(
            sender_id=self.current_user_id,
            receiver_id=group_id,
            conversation_id=group_id,
            status=MessageStatus.SENDING,
            message_type=MessageType.FILE,
            timestamp=time.time(),
        )
        try:
            self.temp_file_service = self._build_temp_file_service()
            metadata = self.temp_file_service.encrypt_and_upload(
                file_path=file_path,
                message_id=message.id,
                sender_id=self.current_user_id,
                scope="group",
                conversation_id=group_id,
                group_id=group_id,
            )
            metadata = {
                **metadata,
                "conversation_type": ConversationType.GROUP.value,
                "group_name": group.name,
                "encryption_scope": "group_encrypted_v1",
            }
            content = self._build_temp_file_content(metadata)
            local_plain_metadata = metadata.copy()
            payload = {
                "content": content,
                "message_type": MessageType.FILE.value,
                "metadata": local_plain_metadata,
            }
            encrypted_group_metadata = self.group_crypto_service.encrypt_payload(
                group_id=group_id,
                payload=payload,
                sender_id=self.current_user_id,
                sender_device_id=self.direct_crypto_service.local_identity.local_device_id,
                message_id=message.id,
            )
            message.content = "[Encrypted group message]"
            message.encryption_version = encrypted_group_metadata["encryption_version"]
            message.encrypted_content = json.dumps(encrypted_group_metadata, ensure_ascii=False)
            message.metadata = {
                "conversation_type": ConversationType.GROUP.value,
                "group_id": group_id,
                "group_name": group.name,
                **encrypted_group_metadata,
            }
            send_group = getattr(self.transport, "send_group_message", None)
            success = send_group(message, sorted(active_member_ids)) if callable(send_group) else self.transport.send_message(message)
            if not success:
                raise TempFileServiceError("临时文件群消息发送失败")
            message.content = content
            message.metadata = local_plain_metadata
            message.status = MessageStatus.SENT
            self.storage.save_message(message)
            self.storage.save_file_attachment(
                self.sync_service.build_file_attachment(message.id, local_plain_metadata)
            )
            self._update_conversation_after_send(group_id, message.id, content, message.timestamp)
            publish_simple(EventType.MESSAGE_SENT, {
                "message": message.to_dict(),
                "conversation_id": group_id,
                "conversation_type": ConversationType.GROUP.value,
            }, source="chat_service")
            return message
        except Exception as e:
            message.status = MessageStatus.FAILED
            self._publish_temp_file_failed(message.id, str(e), message)
            self.logger.error(f"发送群临时文件失败: {e}", exc_info=True)
            return None

    def _update_conversation_after_send(self, conversation_id: str, message_id: str, preview: str, timestamp: float) -> None:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return
        conv.update_last_message(
            message_id=message_id,
            preview=self._build_message_preview(preview),
            timestamp=timestamp,
        )
        self.storage.save_conversation(conv)
        publish_simple(EventType.CONVERSATION_UPDATED, {
            "conversation_id": conversation_id,
            "conversation": conv.to_dict(),
        }, source="chat_service")

    def _publish_temp_file_failed(self, message_id: str, reason: str, message: Optional[Message] = None) -> None:
        failed_message = message or Message(
            content="[临时文件] 发送失败",
            sender_id=self.current_user_id,
            receiver_id="",
            conversation_id="",
            status=MessageStatus.FAILED,
            message_type=MessageType.FILE,
            timestamp=time.time(),
        )
        publish_simple(EventType.MESSAGE_FAILED, {
            "message": failed_message.to_dict(),
            "reason": reason,
        }, source="chat_service")

    def download_temp_file(self, message_id: str, output_dir: Optional[str] = None) -> str:
        message = self.storage.get_message(message_id)
        if message is None or not self._is_temp_file_message(message):
            raise TempFileServiceError("找不到临时文件消息")
        path = self.temp_file_service.download_and_decrypt(message.metadata, output_dir=output_dir)
        message.metadata = {
            **(message.metadata or {}),
            "sync_status": "downloaded",
            "downloaded_path": path,
            "downloaded_at": time.time(),
        }
        self.storage.save_message(message)
        self.storage.save_file_attachment(
            self.sync_service.build_file_attachment(message.id, message.metadata)
        )
        return path

    def _send_text_message(self, content: str, receiver_id: str, conversation_id: Optional[str] = None) -> Optional[Message]:
        exact_receiver_id = self.identity_manager.normalize_contact_id(receiver_id)
        if not exact_receiver_id:
            self.logger.warning("尝试发送给空联系人ID")
            return None
        if exact_receiver_id == self.current_user_id:
            self.logger.warning("尝试给自己发送消息，已阻止")
            return None

        transport_status = {}
        try:
            transport_status = self.transport.get_status()
        except Exception:
            transport_status = {}
        allow_mock_send_without_start = transport_status.get("transport_type") == "mock"

        if not self._running and not allow_mock_send_without_start:
            failed_message = Message(
                content=content,
                sender_id=self.current_user_id,
                receiver_id=exact_receiver_id,
                conversation_id=conversation_id or "",
                status=MessageStatus.FAILED,
                message_type=MessageType.TEXT,
                timestamp=time.time(),
            )
            self.logger.warning(f"聊天服务未启动，阻止发送: receiver={exact_receiver_id}")
            publish_simple(EventType.MESSAGE_FAILED, {
                "message": failed_message.to_dict(),
                "reason": "聊天服务未启动或尚未完成注册",
            }, source="chat_service")
            return None

        block_reason = self.get_send_block_reason(exact_receiver_id)
        if block_reason is not None:
            blocked_message = Message(
                content=content,
                sender_id=self.current_user_id,
                receiver_id=exact_receiver_id,
                conversation_id=conversation_id or "",
                status=MessageStatus.FAILED,
                message_type=MessageType.TEXT,
                timestamp=time.time(),
            )
            self.logger.warning(f"M2-C 发送拦截: receiver={exact_receiver_id}, reason={block_reason}")
            publish_simple(EventType.MESSAGE_FAILED, {
                "message": blocked_message.to_dict(),
                "reason": block_reason,
            }, source="chat_service")
            return None

        # 确定会话ID
        if not conversation_id:
            conversation_id = self._get_or_create_conversation_id(exact_receiver_id)

        # 创建消息对象（初始使用明文内容）
        message = Message(
            content=content,
            sender_id=self.current_user_id,
            receiver_id=exact_receiver_id,
            conversation_id=conversation_id,
            status=MessageStatus.SENDING,
            message_type=MessageType.TEXT,
            timestamp=time.time()
        )

        # 加密消息：第七阶段默认直聊 v2，legacy v1 只作为缺少新服务时的兼容路径。
        encrypted_metadata = None
        outbound_messages: List[Message] = []
        if self.direct_crypto_service is not None:
            peer_devices = self._get_peer_device_profiles(exact_receiver_id, refresh=True)
            if not peer_devices:
                reason = "缺少对端设备密钥，请刷新在线状态或重新授权联系人"
                message.status = MessageStatus.FAILED
                publish_simple(EventType.MESSAGE_FAILED, {
                    "message": message.to_dict(),
                    "reason": reason,
                }, source="chat_service")
                self.logger.warning(f"直聊 v2 发送失败: receiver={exact_receiver_id}, reason={reason}")
                return None
            try:
                for index, peer_device in enumerate(peer_devices):
                    outbound_message = message if index == 0 else Message(
                        content=content,
                        sender_id=self.current_user_id,
                        receiver_id=exact_receiver_id,
                        conversation_id=conversation_id,
                        status=MessageStatus.SENDING,
                        message_type=MessageType.TEXT,
                        timestamp=message.timestamp,
                    )
                    device_metadata = self.direct_crypto_service.encrypt_message(
                        plaintext=content,
                        sender_id=self.current_user_id,
                        recipient_id=exact_receiver_id,
                        recipient_device_id=peer_device["device_id"],
                        recipient_public_key=peer_device["device_public_key"],
                        message_id=outbound_message.id,
                        scope="direct",
                    )
                    outbound_message.encrypted_content = json.dumps(device_metadata, ensure_ascii=False)
                    outbound_message.encryption_version = device_metadata["encryption_version"]
                    outbound_message.metadata = device_metadata.copy()
                    outbound_message.content = "[Encrypted message: direct_encrypted_v2]"
                    outbound_messages.append(outbound_message)
                    if encrypted_metadata is None:
                        encrypted_metadata = device_metadata
                self.logger.info(
                    f"消息已加密，版本: {message.encryption_version}, "
                    f"设备数={len(outbound_messages)}, "
                    f"first_key_id={encrypted_metadata.get('key_id') if encrypted_metadata else ''}"
                )
            except Exception as e:
                reason = f"消息加密失败: {e}"
                message.status = MessageStatus.FAILED
                publish_simple(EventType.MESSAGE_FAILED, {
                    "message": message.to_dict(),
                    "reason": reason,
                }, source="chat_service")
                self.logger.error(reason, exc_info=True)
                return None
        elif self.crypto_service is not None:
            try:
                encrypted_metadata = self.crypto_service.encrypt_message(
                    plaintext=content,
                    sender_id=self.current_user_id,
                    recipient_id=exact_receiver_id
                )
                # 将加密元数据转换为JSON字符串
                encrypted_content_json = json.dumps(encrypted_metadata)
                
                # 更新消息的加密字段
                message.encrypted_content = encrypted_content_json
                message.encryption_version = self.crypto_service.get_encryption_version()
                
                # 临时替换消息内容为加密元数据（用于传输）
                message.content = encrypted_content_json
                outbound_messages = [message]
                
                self.logger.info(f"消息已加密，版本: {message.encryption_version}")
            except Exception as e:
                self.logger.error(f"消息加密失败，将以明文发送: {e}", exc_info=True)
                # 加密失败，继续使用明文发送
                encrypted_metadata = None
                outbound_messages = [message]
        else:
            self.logger.warning("加密服务不可用，消息将以明文发送")
            outbound_messages = [message]

        if not outbound_messages:
            outbound_messages = [message]

        # 更新会话的最后消息（使用原始明文预览）
        if conversation_id in self._conversations:
            conv = self._conversations[conversation_id]
            conv.update_last_message(
                message_id=message.id,
                preview=content[:50] + ("..." if len(content) > 50 else ""),
                timestamp=message.timestamp
            )

            # 保存会话到数据库
            self.storage.save_conversation(conv)

            # 发布会话更新事件
            publish_simple(EventType.CONVERSATION_UPDATED, {
                "conversation_id": conversation_id,
                "conversation": conv.to_dict()
            }, source="chat_service")

        # 发送消息
        try:
            if encrypted_metadata is not None:
                self.logger.info(
                    f"发送加密消息到 {receiver_id}, version={message.encryption_version}, "
                    f"envelopes={len(outbound_messages)}"
                )
            else:
                self.logger.info(f"发送消息到 {receiver_id}, payload_len={len(content or '')}")

            success = True
            for outbound_message in outbound_messages:
                if encrypted_metadata is not None:
                    target_device_id = ""
                    if isinstance(outbound_message.metadata, dict):
                        target_device_id = str(outbound_message.metadata.get("recipient_device_id") or "")
                    self.logger.debug(
                        "发送 direct-v2 envelope: message=%s target_device=%s",
                        outbound_message.id[:8],
                        target_device_id,
                    )
                # 通过传输层发送（此时 outbound_message.content 可能是加密元数据）
                success = self.transport.send_message(outbound_message) and success
            
            # 发送后恢复消息内容为原始明文（用于本地存储和UI显示）
            if encrypted_metadata is not None:
                message.content = content  # 恢复原始明文
                self.logger.debug("已恢复消息内容为原始明文")

            if success:
                # 立即更新状态为已发送（模拟立即发送成功）
                message.status = MessageStatus.SENT

                # 保存消息到数据库
                self.storage.save_message(message)

                # 发布消息发送事件
                publish_simple(EventType.MESSAGE_SENT, {
                    "message": message.to_dict(),
                    "conversation_id": conversation_id
                }, source="chat_service")

                self.logger.info(f"消息发送成功: {message.id[:8]}")

                return message
            else:
                # 发送失败
                message.status = MessageStatus.FAILED
                publish_simple(EventType.MESSAGE_FAILED, {
                    "message": message.to_dict(),
                    "reason": "传输层发送失败"
                }, source="chat_service")
                self.logger.error(f"消息发送失败: {message.id[:8]}")
                return None

        except Exception as e:
            self.logger.error(f"发送消息异常: {e}", exc_info=True)
            message.status = MessageStatus.FAILED
            publish_simple(EventType.MESSAGE_FAILED, {
                "message": message.to_dict(),
                "reason": str(e)
            }, source="chat_service")
            return None

    def _get_member_display_name(self, user_id: str) -> str:
        """群成员显示名，优先联系人/当前用户显示名。"""
        if user_id == self.current_user_id:
            return self.get_current_user_display_name()
        return self.get_contact_display_name(user_id)

    def _normalize_group_members(self, member_user_ids: List[str]) -> Optional[List[GroupMember]]:
        """创建群时规范化成员；本人 active，其他已授权联系人进入 invited。"""
        normalized_ids = {self.current_user_id}
        for raw_user_id in member_user_ids:
            user_id = self.identity_manager.normalize_contact_id(raw_user_id or "")
            if not user_id:
                continue
            if user_id == self.current_user_id:
                normalized_ids.add(user_id)
                continue
            if not self.can_send_to_contact(user_id):
                self.logger.warning(f"群成员未授权，拒绝加入群: {user_id}")
                return None
            normalized_ids.add(user_id)

        return [
            GroupMember(
                group_id="",
                user_id=user_id,
                display_name=self._get_member_display_name(user_id),
                status=GroupMemberStatus.ACTIVE if user_id == self.current_user_id else GroupMemberStatus.INVITED,
                metadata={
                    "invited_by": self.current_user_id,
                    "invited_at": time.time(),
                } if user_id != self.current_user_id else {},
            )
            for user_id in sorted(normalized_ids)
        ]

    def _save_group_invitation(self, group: Group, members: List[GroupMember]) -> None:
        """保存待确认群邀请，但不加入 active 会话列表。"""
        self.storage.save_group(group)
        for member in members:
            member.group_id = group.id
            self.storage.save_group_member(member)

    def _get_group_member_from_list(self, members: List[GroupMember], user_id: str) -> Optional[GroupMember]:
        """从成员列表按 user_id 取成员。"""
        for member in members:
            if member.user_id == user_id:
                return member
        return None

    def _is_valid_group_update(self, members: List[GroupMember], inviter_id: str) -> bool:
        """基础验证：邀请者必须是该群 active 成员。"""
        if not inviter_id:
            return False
        inviter = self._get_group_member_from_list(members, inviter_id)
        return inviter is not None and inviter.status == GroupMemberStatus.ACTIVE

    def _persist_group_state(self, group: Group, members: List[GroupMember]) -> Conversation:
        """保存群、成员与对应群会话，并更新内存缓存。"""
        now = time.time()
        group.updated_at = now
        self.storage.save_group(group)

        for member in members:
            member.group_id = group.id
            self.storage.save_group_member(member)

        active_members = [member for member in members if member.status == GroupMemberStatus.ACTIVE]
        conversation = self.storage.get_conversation(group.id) or self._build_group_conversation(group, active_members)
        conversation.conversation_type = ConversationType.GROUP
        conversation.participant_ids = sorted({member.user_id for member in active_members})
        conversation.display_name = group.name
        conversation.group_name = group.name
        conversation.avatar_url = group.avatar_url
        conversation.group_avatar = group.avatar_url
        conversation.updated_at = now
        conversation.metadata = {
            **(conversation.metadata or {}),
            "sync_status": "reserved",
        }

        self.storage.save_conversation(conversation)

        with self._groups_lock:
            self._groups[group.id] = group
            self._group_members[group.id] = active_members
        with self._conversations_lock:
            self._conversations[group.id] = conversation

        return conversation

    def _send_group_update_to_transport(self, group: Group, members: List[GroupMember]) -> None:
        """向传输层广播群信息更新。"""
        send_update = getattr(self.transport, "send_group_update", None)
        if not callable(send_update):
            return
        try:
            send_update(group.to_dict(), [member.to_dict() for member in members], self.current_user_id)
        except Exception as e:
            self.logger.warning(f"发送群更新到传输层失败: {e}")

    def create_group(self, name: str, member_user_ids: List[str]) -> Optional[Group]:
        """创建基础群组，并把当前用户自动加入成员。"""
        group_name = (name or "").strip()
        if not group_name:
            self.logger.warning("尝试创建空名称群组")
            return None

        members = self._normalize_group_members(member_user_ids)
        if members is None:
            return None

        group = Group(
            name=group_name,
            creator_id=self.current_user_id,
            metadata={
                "sync_status": "reserved",
                "file_message_schema": FILE_MESSAGE_METADATA_SCHEMA.copy(),
            },
        )
        self._attach_group_key_packets(group, members, reason="group_created")
        conversation = self._persist_group_state(group, members)
        self._send_group_update_to_transport(group, members)
        self.sync_groups_to_transport()

        publish_simple(EventType.GROUP_CREATED, {
            "group": group.to_dict(),
            "members": [member.to_dict() for member in members],
        }, source="chat_service")
        publish_simple(EventType.CONVERSATION_CREATED, {
            "conversation": conversation.to_dict(),
        }, source="chat_service")
        return group

    def add_group_member(self, group_id: str, user_id: str) -> bool:
        """向群组添加成员。第三阶段不做管理员体系，active 成员可添加 trusted 联系人。"""
        with self._groups_lock:
            group = self._groups.get(group_id)
            members = list(self._group_members.get(group_id, []))

        if group is None:
            self.logger.warning(f"尝试添加成员到不存在的群组: {group_id}")
            return False
        if self.current_user_id not in {member.user_id for member in members}:
            self.logger.warning(f"当前用户不是群成员，不能添加成员: {group_id}")
            return False

        normalized_user_id = self.identity_manager.normalize_contact_id(user_id)
        if not normalized_user_id or normalized_user_id == self.current_user_id:
            return False
        if not self.can_send_to_contact(normalized_user_id):
            self.logger.warning(f"群成员未授权，拒绝加入群: {normalized_user_id}")
            return False

        all_members = self.storage.get_group_members(group_id, active_only=False) or members
        member_map = {member.user_id: member for member in all_members}
        existing_member = member_map.get(normalized_user_id)
        if existing_member and existing_member.status == GroupMemberStatus.ACTIVE:
            return True
        member_map[normalized_user_id] = GroupMember(
            group_id=group_id,
            user_id=normalized_user_id,
            display_name=self._get_member_display_name(normalized_user_id),
            status=GroupMemberStatus.INVITED,
            metadata={
                "invited_by": self.current_user_id,
                "invited_at": time.time(),
            },
        )
        updated_members = list(member_map.values())
        self._attach_group_key_packets(group, updated_members, reason="member_added")
        conversation = self._persist_group_state(group, updated_members)
        self._send_group_update_to_transport(group, updated_members)
        self.sync_groups_to_transport()

        publish_simple(EventType.GROUP_UPDATED, {
            "group": group.to_dict(),
            "members": [member.to_dict() for member in updated_members],
        }, source="chat_service")
        publish_simple(EventType.CONVERSATION_UPDATED, {
            "conversation_id": conversation.id,
            "conversation": conversation.to_dict(),
        }, source="chat_service")
        return True

    def remove_group_member(self, group_id: str, user_id: str) -> bool:
        """从群组移除成员，并触发最小 group key rotation。"""
        with self._groups_lock:
            group = self._groups.get(group_id)
            members = list(self._group_members.get(group_id, []))
        if group is None:
            return False
        if self.current_user_id not in {member.user_id for member in members}:
            return False
        normalized_user_id = self.identity_manager.normalize_contact_id(user_id)
        if not normalized_user_id or normalized_user_id == self.current_user_id:
            return False
        all_members = self.storage.get_group_members(group_id, active_only=False) or members
        changed = False
        for member in all_members:
            if member.user_id == normalized_user_id and member.status != GroupMemberStatus.REMOVED:
                member.status = GroupMemberStatus.REMOVED
                member.metadata = {
                    **(member.metadata or {}),
                    "removed_by": self.current_user_id,
                    "removed_at": time.time(),
                }
                changed = True
        if not changed:
            return False
        self._attach_group_key_packets(group, all_members, reason="member_removed")
        conversation = self._persist_group_state(group, all_members)
        self._send_group_update_to_transport(group, all_members)
        self.sync_groups_to_transport()
        publish_simple(EventType.GROUP_UPDATED, {
            "group": group.to_dict(),
            "members": [member.to_dict() for member in all_members],
        }, source="chat_service")
        publish_simple(EventType.CONVERSATION_UPDATED, {
            "conversation_id": conversation.id,
            "conversation": conversation.to_dict(),
        }, source="chat_service")
        return True

    def get_groups(self) -> Dict[str, Group]:
        """获取当前用户所在群组。"""
        with self._groups_lock:
            return self._groups.copy()

    def get_group_members(self, group_id: str) -> List[GroupMember]:
        """获取群成员列表。"""
        with self._groups_lock:
            return list(self._group_members.get(group_id, []))

    def _get_active_group_for_current_user(self, group_id: str) -> Optional[Group]:
        """Return group only when the current user is an active member."""
        with self._groups_lock:
            group = self._groups.get(group_id)
            members = list(self._group_members.get(group_id, []))
        if group is None:
            self.logger.warning(f"同步操作目标群不存在: {group_id}")
            return None
        active_member_ids = {member.user_id for member in members if member.status == GroupMemberStatus.ACTIVE}
        if self.current_user_id not in active_member_ids:
            self.logger.warning(f"当前用户不是群成员，不能执行同步操作: {group_id}")
            return None
        return group

    def detect_local_syncthing(self) -> Dict[str, Any]:
        """检测本机 Syncthing API 状态。"""
        return self.sync_service.detect_local_syncthing()

    def get_syncthing_settings(self) -> Dict[str, Any]:
        """返回当前 profile 的 Syncthing 设置，API key 会被打码。"""
        return self.sync_service.load_settings().to_dict(mask_key=True)

    def save_syncthing_settings(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """保存当前 profile 的 Syncthing API 设置。"""
        settings = self.sync_service.save_settings(base_url, api_key, timeout_seconds)
        return settings.to_dict(mask_key=True)

    def get_group_sync_overview(self, group_id: str) -> Dict[str, Any]:
        """获取群组项目同步概览。"""
        return self.sync_service.get_group_sync_overview(group_id)

    def bind_group_folder(self, group_id: str, local_path: str, project_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """将群组绑定到本地项目目录。"""
        group = self._get_active_group_for_current_user(group_id)
        if group is None:
            return None
        try:
            project, folder = self.sync_service.bind_group_folder(group, local_path, project_name)
            group.metadata = {
                **(group.metadata or {}),
                "sync_status": folder.status,
                "project_id": project.id,
                "shared_folder_id": folder.id,
            }
            self.storage.save_group(group)
            return self.sync_service.get_group_sync_overview(group_id)
        except Exception as e:
            self.logger.error(f"绑定群组项目文件夹失败: {e}", exc_info=True)
            return None

    def configure_group_syncthing_folder(self, group_id: str) -> Optional[Dict[str, Any]]:
        """为群组绑定的共享文件夹创建/更新 Syncthing folder 配置。"""
        if self._get_active_group_for_current_user(group_id) is None:
            return None
        try:
            overview = self.sync_service.configure_syncthing_folder(group_id)
            group = self.storage.get_group(group_id)
            if group is not None:
                group.metadata = {
                    **(group.metadata or {}),
                    "sync_status": overview.get("status", "configured"),
                    "project_id": (overview.get("project") or {}).get("id", ""),
                    "shared_folder_id": (overview.get("shared_folder") or {}).get("id", ""),
                }
                self.storage.save_group(group)
            return overview
        except Exception as e:
            self.logger.error(f"配置 Syncthing folder 失败: {e}", exc_info=True)
            return None

    def stop_group_syncthing_folder(self, group_id: str) -> Optional[Dict[str, Any]]:
        """停止本机 Syncthing folder 同步，保留群项目绑定和本地文件。"""
        if self._get_active_group_for_current_user(group_id) is None:
            return None
        try:
            overview = self.sync_service.stop_group_sync(group_id)
            group = self.storage.get_group(group_id)
            if group is not None:
                group.metadata = {
                    **(group.metadata or {}),
                    "sync_status": overview.get("status", "stopped"),
                    "project_id": (overview.get("project") or {}).get("id", ""),
                    "shared_folder_id": (overview.get("shared_folder") or {}).get("id", ""),
                }
                self.storage.save_group(group)
            return overview
        except Exception as e:
            self.logger.error(f"停止 Syncthing folder 失败: {e}", exc_info=True)
            return None

    def unbind_group_project_sync(self, group_id: str, local_only: bool = False) -> Optional[Dict[str, Any]]:
        """移除当前 profile 的群项目绑定，不删除群聊、消息或真实文件。"""
        if self._get_active_group_for_current_user(group_id) is None:
            return None
        try:
            return self.sync_service.unbind_group_project(group_id, local_only=local_only)
        except Exception as e:
            self.logger.error(f"解绑群组项目同步失败: {e}", exc_info=True)
            return None

    def add_group_sync_device(
        self,
        group_id: str,
        user_id: str,
        syncthing_device_id: str,
        display_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """手动记录群成员 Syncthing Device ID，并尽量同步到 Syncthing 配置。"""
        if self._get_active_group_for_current_user(group_id) is None:
            return None
        all_members = self.storage.get_group_members(group_id, active_only=False)
        if user_id and user_id not in {member.user_id for member in all_members}:
            self.logger.warning(f"不能为非群成员添加同步设备: group={group_id}, user={user_id}")
            return None
        try:
            device = self.sync_service.add_member_device(
                group_id,
                user_id,
                syncthing_device_id,
                display_name or self._get_member_display_name(user_id),
            )
            return device.to_dict()
        except Exception as e:
            self.logger.error(f"添加同步设备失败: {e}", exc_info=True)
            return None

    def refresh_group_sync_status(
        self,
        group_id: str,
        publish_file_events: bool = False,
    ) -> Dict[str, Any]:
        """刷新 Syncthing 同步状态；可选把新文件事件发到群聊。"""
        if self._get_active_group_for_current_user(group_id) is None:
            return {"group_id": group_id, "status": "forbidden", "error": "not a group member"}
        overview = self.sync_service.poll_sync_status(group_id)
        if publish_file_events:
            for event_metadata in overview.get("recent_events", []):
                self.send_file_event_message(group_id, event_metadata)
        return overview

    def scan_group_sync_folder(self, group_id: str) -> Optional[Dict[str, Any]]:
        """请求 Syncthing 扫描群组绑定文件夹。"""
        if self._get_active_group_for_current_user(group_id) is None:
            return None
        try:
            return self.sync_service.scan_group_folder(group_id)
        except Exception as e:
            self.logger.error(f"扫描同步文件夹失败: {e}", exc_info=True)
            return None

    def scan_project_index(self, group_id: str = "") -> Dict[str, Any]:
        """Scan indexed metadata for one group project or all bound projects."""
        if group_id and self._get_active_group_for_current_user(group_id) is None:
            return {"status": "forbidden", "summary": {}, "runs": []}
        return self.project_index_service.scan(group_id=group_id)

    def get_project_index_status(self, group_id: str = "") -> Dict[str, Any]:
        """Return local project index counts and latest scan information."""
        if group_id and self._get_active_group_for_current_user(group_id) is None:
            return {"tables_ready": True, "total_count": 0, "existing_count": 0, "missing_count": 0}
        return self.project_index_service.status(group_id=group_id)

    def search_project_files(
        self,
        query: str = "",
        group_id: str = "",
        extension: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Search indexed project files by name, extension, or relative path."""
        if group_id and self._get_active_group_for_current_user(group_id) is None:
            return []
        return self.project_index_service.search(
            query=query,
            group_id=group_id,
            extension=extension,
            limit=limit,
        )

    def locate_project_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Resolve an indexed file id to metadata including absolute path."""
        return self.project_index_service.locate(file_id)

    def get_ai_provider_status(self) -> Dict[str, Any]:
        """Return profile-local AI provider status for chat UI surfaces."""
        settings = self._load_ai_settings()
        configured = bool(settings.provider_type and settings.base_url and settings.model)
        return {
            "provider_type": settings.provider_type,
            "base_url": settings.base_url,
            "model": settings.model,
            "configured": configured,
            "has_api_key": bool(settings.api_key),
            "provider_location": self._ai_provider_location(settings),
            "rag_max_chunks": settings.rag_max_chunks,
            "rag_max_context_chars": settings.rag_max_context_chars,
        }

    def get_ai_document_library_status(self, group_id: str = "", project_id: str = "") -> Dict[str, Any]:
        if group_id and self._get_active_group_for_current_user(group_id) is None:
            return {"tables_ready": True, "source_count": 0, "chunk_count": 0, "error": "not a group member"}
        return self._ai_service().document_library_status(group_id=group_id, project_id=project_id)

    def build_ai_document_library(self, group_id: str, project_id: str = "") -> Dict[str, Any]:
        if self._get_active_group_for_current_user(group_id) is None:
            return {"status": "forbidden", "summary": {}, "library": {}}
        return self._ai_service().build_document_library(group_id=group_id, project_id=project_id)

    def diagnose_ai_document_library(self, group_id: str, project_id: str = "", query: str = "") -> Dict[str, Any]:
        if self._get_active_group_for_current_user(group_id) is None:
            return {"status": "forbidden", "error": "not a group member", "provider_called": False}
        return self._ai_service().diagnose_document_library(group_id=group_id, project_id=project_id, query=query)

    def list_ai_document_sources(
        self,
        group_id: str = "",
        project_id: str = "",
        status: str = "",
        query: str = "",
        limit: int = 100,
    ) -> Dict[str, Any]:
        if group_id and self._get_active_group_for_current_user(group_id) is None:
            return {"sources": [], "count": 0, "error": "not a group member"}
        return self._ai_service().list_document_sources(
            group_id=group_id,
            project_id=project_id,
            status=status,
            query=query,
            limit=limit,
        )

    def delete_ai_document_source(
        self,
        source_id: str = "",
        file_id: str = "",
        group_id: str = "",
        project_id: str = "",
    ) -> Dict[str, Any]:
        if group_id and self._get_active_group_for_current_user(group_id) is None:
            return {"deleted": False, "error": "not a group member", "real_file_deleted": False}
        return self._ai_service().delete_document_source(
            source_id=source_id,
            file_id=file_id,
            group_id=group_id,
            project_id=project_id,
        )

    def restore_ai_document_source(
        self,
        source_id: str = "",
        file_id: str = "",
        group_id: str = "",
        project_id: str = "",
    ) -> Dict[str, Any]:
        if group_id and self._get_active_group_for_current_user(group_id) is None:
            return {"restored": False, "error": "not a group member", "real_file_deleted": False}
        return self._ai_service().restore_document_source(
            source_id=source_id,
            file_id=file_id,
            group_id=group_id,
            project_id=project_id,
        )

    def ask_ai_assistant(
        self,
        question: str,
        target_kind: str = "",
        target_id: str = "",
        conversation_id: str = "",
    ) -> Dict[str, Any]:
        """Ask the profile-local assistant from the current chat window."""
        question = (question or "").strip()
        if not question:
            raise ValueError("问题不能为空")
        if target_kind == "group":
            if self._get_active_group_for_current_user(target_id) is None:
                raise ValueError("当前用户不是该群成员")
            project_id = self._project_id_for_group(target_id)
            return self._ai_service().ask_project_question(
                question=question,
                group_id=target_id,
                project_id=project_id,
                conversation_id=conversation_id,
                chat_context=self._recent_chat_context_for_group(target_id),
            )
        if target_kind == "direct":
            if not target_id:
                raise ValueError("请选择联系人后再提问")
            return self._ask_ai_direct_context(question, target_id, conversation_id=conversation_id)
        raise ValueError("请选择聊天对象后再提问")

    def _ask_ai_direct_context(
        self,
        question: str,
        contact_id: str,
        conversation_id: str = "",
    ) -> Dict[str, Any]:
        settings = self._load_ai_settings()
        provider = AIProviderClient(settings)
        provider.validate()
        conversations = ConversationStore(self.storage, profile=self._ai_profile_name())
        scope_id = f"direct:{contact_id}"
        conversation = conversations.get_or_create(
            conversation_id=conversation_id,
            group_id=scope_id,
            project_id="",
            settings=settings,
            title=question[:80],
        )
        conversation_id = conversation["conversation_id"]
        recent_ai_messages = conversations.recent_messages(
            conversation_id,
            turns=int(settings.conversation_recent_turns or 6),
        )
        chat_context = self._recent_chat_context_for_contact(contact_id)
        user_message = conversations.add_message(
            conversation_id,
            "user",
            question,
            metadata={"scope": "direct_chat_context", "contact_id": contact_id},
        )
        messages = self._build_direct_ai_messages(question, recent_ai_messages, chat_context, settings)
        answer = provider.chat(messages).strip()
        assistant_message = conversations.add_message(
            conversation_id,
            "assistant",
            answer,
            metadata={
                "provider": {
                    "provider_type": settings.provider_type,
                    "base_url": settings.base_url,
                    "model": settings.model,
                    "has_api_key": bool(settings.api_key),
                },
                "scope": "direct_chat_context",
                "source_refs": [],
            },
            sources=[],
        )
        return {
            "answer": answer,
            "conversation_id": conversation_id,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "sources": [],
            "retrieval": {
                "mode": "chat_context_only",
                "query": question,
                "candidate_count": len(chat_context),
                "source_count": 0,
            },
            "provider": {
                "provider_type": settings.provider_type,
                "base_url": settings.base_url,
                "model": settings.model,
                "has_api_key": bool(settings.api_key),
            },
            "privacy_policy": {
                "scope": "direct_chat_context",
                "upload_policy": "only_recent_chat_messages_sent_to_selected_provider",
                "no_command_execution": True,
                "no_file_modification": True,
                "embedding_enabled": bool(settings.embedding_enabled),
            },
        }

    def _build_direct_ai_messages(
        self,
        question: str,
        recent_ai_messages: List[Dict[str, Any]],
        chat_context: List[Dict[str, Any]],
        settings: AISettings,
    ) -> List[Dict[str, str]]:
        system = (
            "你是聊天窗口旁边的本地 AI 小助手。只能基于当前聊天上下文和用户问题回答。"
            "不要声称读取了项目文件或文档库；不要执行命令、不要修改文件、不要联网搜索。"
            "默认使用中文，回答要简洁。"
        )
        parts = [
            "当前聊天上下文模式",
            f"profile: {self._ai_profile_name()}",
            f"provider: {settings.provider_type}",
            f"model: {settings.model}",
            "retrieval_mode: chat_context_only",
            "",
            "最近聊天:",
        ]
        for item in chat_context[-20:]:
            role = "我" if item.get("is_self") else (item.get("sender") or item.get("sender_id") or "对方")
            parts.append(f"{role}: {str(item.get('content') or '')[:1000]}")
        if recent_ai_messages:
            parts.append("\n最近 AI 对话:")
            for message in recent_ai_messages[-8:]:
                role = "用户" if message.get("role") == "user" else "助手"
                parts.append(f"{role}: {str(message.get('content') or '')[:800]}")
        parts.append("\n用户问题:\n" + question)
        return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(parts)}]

    def _recent_chat_context_for_group(self, group_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        return self._message_context(self.load_messages_for_conversation(group_id, limit=limit))

    def _recent_chat_context_for_contact(self, contact_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        return self._message_context(self.load_messages_for_contact(contact_id, limit=limit))

    def _message_context(self, messages: List[Message]) -> List[Dict[str, Any]]:
        context: List[Dict[str, Any]] = []
        for message in messages:
            if message.auth_status != MessageAuthStatus.TRUSTED:
                continue
            content = (message.content or "").strip()
            if not content:
                continue
            context.append({
                "message_id": message.id,
                "sender_id": message.sender_id,
                "sender": "我" if message.sender_id == self.current_user_id else self.get_contact_display_name(message.sender_id),
                "is_self": message.sender_id == self.current_user_id,
                "content": content,
                "timestamp": message.timestamp,
            })
        return context

    def _ai_service(self) -> AIService:
        return AIService(self.storage, self._load_ai_settings(), profile=self._ai_profile_name())

    def _load_ai_settings(self) -> AISettings:
        return AISettingsStore(self.config_dir).load()

    def _ai_profile_name(self) -> str:
        data_name = Path(self.data_dir or "").name
        if data_name and data_name not in {".", ".."}:
            return data_name
        return ""

    def _project_id_for_group(self, group_id: str) -> str:
        project = self.storage.get_project_by_group(group_id)
        return project.id if project else ""

    def _ai_provider_location(self, settings: AISettings) -> str:
        if settings.provider_type in {"ollama", "lm_studio"}:
            return "local"
        base_url = settings.base_url or ""
        if any(host in base_url for host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")):
            return "local"
        return "remote" if base_url else "unknown"

    def send_file_event_message(self, group_id: str, metadata: Dict[str, Any]) -> Optional[Message]:
        """发送聊天内文件事件。只发送 metadata，不发送文件内容。"""
        if self._get_active_group_for_current_user(group_id) is None:
            return None
        if not isinstance(metadata, dict):
            return None

        with self._groups_lock:
            group = self._groups.get(group_id)
            members = list(self._group_members.get(group_id, []))
        if group is None:
            return None

        active_member_ids = {member.user_id for member in members if member.status == GroupMemberStatus.ACTIVE}
        event_metadata = {
            **FILE_MESSAGE_METADATA_SCHEMA.copy(),
            **metadata,
            "schema": "file_event_v1",
            "conversation_type": ConversationType.GROUP.value,
            "group_id": group_id,
            "group_name": group.name,
            "encryption_scope": "group_encrypted_v1" if self.group_crypto_service is not None else "group_plain_v3",
            "origin_user_id": metadata.get("origin_user_id") or self.current_user_id,
        }
        content = self._build_file_event_content(event_metadata)

        try:
            if len(json.dumps(event_metadata, ensure_ascii=False).encode("utf-8")) > 60000:
                self.logger.warning("文件事件 metadata 过大，已拒绝发送")
                return None
        except Exception:
            return None

        message = Message(
            content=content,
            sender_id=self.current_user_id,
            receiver_id=group_id,
            conversation_id=group_id,
            status=MessageStatus.SENDING,
            message_type=MessageType.FILE,
            timestamp=time.time(),
            metadata=event_metadata,
        )
        local_plain_metadata = event_metadata.copy()
        encrypted_group_metadata = None
        if self.group_crypto_service is not None and self.direct_crypto_service is not None:
            try:
                payload = {
                    "content": content,
                    "message_type": message.message_type.value,
                    "metadata": local_plain_metadata,
                }
                encrypted_group_metadata = self.group_crypto_service.encrypt_payload(
                    group_id=group_id,
                    payload=payload,
                    sender_id=self.current_user_id,
                    sender_device_id=self.direct_crypto_service.local_identity.local_device_id,
                    message_id=message.id,
                )
                message.encryption_version = encrypted_group_metadata["encryption_version"]
                message.encrypted_content = json.dumps(encrypted_group_metadata, ensure_ascii=False)
                message.metadata = {
                    "conversation_type": ConversationType.GROUP.value,
                    "group_id": group_id,
                    "group_name": group.name,
                    **encrypted_group_metadata,
                }
                message.content = "[Encrypted group message]"
            except Exception as e:
                self.logger.error(f"文件事件群加密失败: {e}", exc_info=True)
                message.status = MessageStatus.FAILED
                publish_simple(EventType.MESSAGE_FAILED, {
                    "message": message.to_dict(),
                    "reason": f"文件事件加密失败: {e}",
                }, source="chat_service")
                return None

        conv = self._conversations.get(group_id)
        if conv is not None:
            conv.update_last_message(
                message_id=message.id,
                preview=self._build_message_preview(content),
                timestamp=message.timestamp,
            )
            self.storage.save_conversation(conv)
            publish_simple(EventType.CONVERSATION_UPDATED, {
                "conversation_id": group_id,
                "conversation": conv.to_dict(),
            }, source="chat_service")

        send_group = getattr(self.transport, "send_group_message", None)
        try:
            if callable(send_group):
                success = send_group(message, sorted(active_member_ids))
            else:
                success = self.transport.send_message(message)
            if not success:
                message.status = MessageStatus.FAILED
                publish_simple(EventType.MESSAGE_FAILED, {
                    "message": message.to_dict(),
                    "reason": "文件事件发送失败",
                }, source="chat_service")
                return None

            if encrypted_group_metadata is not None:
                message.content = content
                message.metadata = local_plain_metadata
            message.status = MessageStatus.SENT
            self.storage.save_message(message)
            self.storage.save_file_attachment(
                self.sync_service.build_file_attachment(message.id, event_metadata)
            )
            publish_simple(EventType.MESSAGE_SENT, {
                "message": message.to_dict(),
                "conversation_id": group_id,
                "conversation_type": ConversationType.GROUP.value,
            }, source="chat_service")
            return message
        except Exception as e:
            self.logger.error(f"发送文件事件异常: {e}", exc_info=True)
            message.status = MessageStatus.FAILED
            publish_simple(EventType.MESSAGE_FAILED, {
                "message": message.to_dict(),
                "reason": str(e),
            }, source="chat_service")
            return None

    def _build_file_event_content(self, metadata: Dict[str, Any]) -> str:
        """构建文件事件可读摘要。"""
        event_type = metadata.get("event_type", "updated")
        path = metadata.get("relative_path") or metadata.get("file_name") or "项目文件"
        mapping = {
            "created": "新增文件",
            "updated": "更新文件",
            "deleted": "删除文件",
            "renamed": "重命名文件",
            "conflict": "检测到文件冲突",
            "sync_error": "文件同步错误",
            "device_suggestion": "同步设备建议",
        }
        prefix = mapping.get(event_type, "文件事件")
        error = metadata.get("error")
        if error:
            return f"[文件同步] {prefix}: {path} ({error})"
        return f"[文件同步] {prefix}: {path}"

    def _build_temp_file_content(self, metadata: Dict[str, Any]) -> str:
        file_name = metadata.get("file_name") or "临时文件"
        size = int(metadata.get("size") or 0)
        expires_at = float(metadata.get("expires_at") or 0)
        remaining = max(0, int(expires_at - time.time()))
        if remaining <= 0:
            return f"[临时文件] {file_name} ({self._format_size_for_preview(size)}) 已过期"
        minutes = max(1, remaining // 60)
        return f"[临时文件] {file_name} ({self._format_size_for_preview(size)}) 剩余约 {minutes} 分钟"

    def _format_size_for_preview(self, size: int) -> str:
        value = float(max(0, size))
        for unit in ["B", "KB", "MB", "GB"]:
            if value < 1024 or unit == "GB":
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024

    def _apply_decrypted_payload_if_needed(self, message: Message, plaintext: str) -> None:
        try:
            payload = json.loads(plaintext)
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        if "content" not in payload or "metadata" not in payload:
            return
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return
        message.content = str(payload.get("content") or "")
        try:
            message.message_type = MessageType(str(payload.get("message_type") or MessageType.TEXT.value))
        except ValueError:
            message.message_type = MessageType.TEXT
        message.metadata = metadata

    def _is_temp_file_message(self, message_or_metadata: Any) -> bool:
        metadata = message_or_metadata
        if isinstance(message_or_metadata, Message):
            metadata = message_or_metadata.metadata
        return isinstance(metadata, dict) and metadata.get("schema") == TEMP_FILE_SCHEMA

    def accept_group_invite(self, group_id: str) -> bool:
        """接受群邀请，当前用户从 invited 变为 active。"""
        group = self.storage.get_group(group_id)
        if group is None:
            self.logger.warning(f"尝试接受不存在的群邀请: {group_id}")
            return False

        members = self.storage.get_group_members(group_id, active_only=False)
        current_member = self._get_group_member_from_list(members, self.current_user_id)
        if current_member is None or current_member.status != GroupMemberStatus.INVITED:
            self.logger.warning(f"当前用户没有待接受的群邀请: {group_id}")
            return False

        current_member.status = GroupMemberStatus.ACTIVE
        current_member.display_name = self.get_current_user_display_name()
        current_member.metadata = {
            **(current_member.metadata or {}),
            "accepted_at": time.time(),
        }
        conversation = self._persist_group_state(group, members)
        self._send_group_update_to_transport(group, members)
        self.sync_groups_to_transport()

        publish_simple(EventType.GROUP_UPDATED, {
            "group": group.to_dict(),
            "members": [member.to_dict() for member in members],
        }, source="chat_service")
        publish_simple(EventType.CONVERSATION_CREATED, {
            "conversation": conversation.to_dict(),
        }, source="chat_service")
        return True

    def reject_group_invite(self, group_id: str) -> bool:
        """拒绝群邀请，当前用户标记为 removed。"""
        group = self.storage.get_group(group_id)
        if group is None:
            return False

        members = self.storage.get_group_members(group_id, active_only=False)
        current_member = self._get_group_member_from_list(members, self.current_user_id)
        if current_member is None:
            return False

        current_member.status = GroupMemberStatus.REMOVED
        current_member.metadata = {
            **(current_member.metadata or {}),
            "rejected_at": time.time(),
        }
        self.storage.save_group(group)
        self.storage.save_group_member(current_member)
        self._send_group_update_to_transport(group, members)
        return True

    def send_group_message(self, content: str, group_id: str) -> Optional[Message]:
        """发送基础群聊消息。群消息第三阶段不走单聊 recipient 加密。"""
        if not content.strip():
            self.logger.warning("尝试发送空群消息")
            return None

        with self._groups_lock:
            group = self._groups.get(group_id)
            members = list(self._group_members.get(group_id, []))

        if group is None:
            self.logger.warning(f"尝试向不存在的群组发送消息: {group_id}")
            return None

        member_ids = {member.user_id for member in members if member.status == GroupMemberStatus.ACTIVE}
        if self.current_user_id not in member_ids:
            self.logger.warning(f"当前用户不是群成员，不能发送群消息: {group_id}")
            return None

        message = Message(
            content=content,
            sender_id=self.current_user_id,
            receiver_id=group_id,
            conversation_id=group_id,
            status=MessageStatus.SENDING,
            message_type=MessageType.TEXT,
            timestamp=time.time(),
            metadata={
                "conversation_type": ConversationType.GROUP.value,
                "group_id": group_id,
                "group_name": group.name,
                "encryption_scope": "group_encrypted_v1" if self.group_crypto_service is not None else "group_plain_v3",
                "sync_status": "reserved",
            },
        )
        local_plain_metadata = message.metadata.copy()
        encrypted_group_metadata = None
        if self.group_crypto_service is not None and self.direct_crypto_service is not None:
            try:
                payload = {
                    "content": content,
                    "message_type": message.message_type.value,
                    "metadata": local_plain_metadata,
                }
                encrypted_group_metadata = self.group_crypto_service.encrypt_payload(
                    group_id=group_id,
                    payload=payload,
                    sender_id=self.current_user_id,
                    sender_device_id=self.direct_crypto_service.local_identity.local_device_id,
                    message_id=message.id,
                )
                message.encryption_version = encrypted_group_metadata["encryption_version"]
                message.encrypted_content = json.dumps(encrypted_group_metadata, ensure_ascii=False)
                message.metadata = {
                    "conversation_type": ConversationType.GROUP.value,
                    "group_id": group_id,
                    "group_name": group.name,
                    **encrypted_group_metadata,
                }
                message.content = "[Encrypted group message]"
            except Exception as e:
                self.logger.error(f"群消息加密失败: {e}", exc_info=True)
                message.status = MessageStatus.FAILED
                publish_simple(EventType.MESSAGE_FAILED, {
                    "message": message.to_dict(),
                    "reason": f"群消息加密失败: {e}",
                }, source="chat_service")
                return None

        conv = self._conversations.get(group_id)
        if conv is not None:
            conv.update_last_message(
                message_id=message.id,
                preview=self._build_message_preview(content),
                timestamp=message.timestamp,
            )
            self.storage.save_conversation(conv)
            publish_simple(EventType.CONVERSATION_UPDATED, {
                "conversation_id": group_id,
                "conversation": conv.to_dict(),
            }, source="chat_service")

        send_group = getattr(self.transport, "send_group_message", None)
        try:
            if callable(send_group):
                success = send_group(message, sorted(member_ids))
            else:
                success = self.transport.send_message(message)

            if success:
                if encrypted_group_metadata is not None:
                    message.content = content
                    message.metadata = local_plain_metadata
                message.status = MessageStatus.SENT
                self.storage.save_message(message)
                publish_simple(EventType.MESSAGE_SENT, {
                    "message": message.to_dict(),
                    "conversation_id": group_id,
                    "conversation_type": ConversationType.GROUP.value,
                }, source="chat_service")
                return message

            message.status = MessageStatus.FAILED
            publish_simple(EventType.MESSAGE_FAILED, {
                "message": message.to_dict(),
                "reason": "群消息传输失败",
            }, source="chat_service")
            return None

        except Exception as e:
            self.logger.error(f"发送群消息异常: {e}", exc_info=True)
            message.status = MessageStatus.FAILED
            publish_simple(EventType.MESSAGE_FAILED, {
                "message": message.to_dict(),
                "reason": str(e),
            }, source="chat_service")
            return None

    def send_contact_request(self, receiver_id: str) -> Optional[Message]:
        """
        发送联系人请求消息（绕过信任检查）
        
        Args:
            receiver_id: 接收者ID
            
        Returns:
            Optional[Message]: 发送的消息对象，失败时返回None
        """
        exact_receiver_id = self.identity_manager.normalize_contact_id(receiver_id)
        if not exact_receiver_id:
            self.logger.warning("联系人请求目标为空，已忽略")
            return None
        
        # 确定会话ID
        conversation_id = self._get_or_create_conversation_id(exact_receiver_id)
        
        # 创建联系人请求消息（特殊内容，用于识别）
        content = "__contact_request__"
        message = Message(
            content=content,
            sender_id=self.current_user_id,
            receiver_id=exact_receiver_id,
            conversation_id=conversation_id,
            status=MessageStatus.SENDING,
            message_type=MessageType.TEXT,
            timestamp=time.time()
        )
        
        # 跳过信任检查，直接发送
        self.logger.info(f"发送联系人请求到 {exact_receiver_id}")
        
        # 通过传输层发送
        try:
            success = self.transport.send_message(message)
            if success:
                message.status = MessageStatus.SENT
                self.logger.info(f"联系人请求发送成功: {message.id[:8]}")
                # 保存消息到数据库
                self.storage.save_message(message)
                return message
            else:
                message.status = MessageStatus.FAILED
                self.logger.error(f"联系人请求发送失败: {message.id[:8]}")
                return None
        except Exception as e:
            self.logger.error(f"发送联系人请求异常: {e}", exc_info=True)
            message.status = MessageStatus.FAILED
            return None

    def _on_group_update_received(
        self,
        group_data: Dict[str, Any],
        members_data: List[Dict[str, Any]],
        inviter_id: str = "",
    ) -> None:
        """处理传输层收到的群组/成员更新控制消息。"""
        if not group_data:
            return

        group = Group.from_dict(group_data)
        members = [GroupMember.from_dict(item) for item in members_data if isinstance(item, dict)]
        self._import_group_key_packet_if_present(group, inviter_id)
        current_member = self._get_group_member_from_list(members, self.current_user_id)
        if current_member is None or current_member.status == GroupMemberStatus.REMOVED:
            self.logger.info(f"忽略不包含当前用户的群更新: {group.id}")
            return

        if not self._is_valid_group_update(members, inviter_id):
            self.logger.warning(f"群更新验证失败: group={group.id}, inviter={inviter_id}")
            return

        if current_member.status == GroupMemberStatus.INVITED:
            previous_members = self.storage.get_group_members(group.id, active_only=False)
            previous_member = self._get_group_member_from_list(previous_members, self.current_user_id)
            self._save_group_invitation(group, members)
            if previous_member is None or previous_member.status != GroupMemberStatus.INVITED:
                publish_simple(EventType.GROUP_INVITE_RECEIVED, {
                    "group": group.to_dict(),
                    "members": [member.to_dict() for member in members],
                    "inviter_id": inviter_id,
                }, source="chat_service")
            return

        existed = group.id in self._conversations
        conversation = self._persist_group_state(group, members)

        publish_simple(EventType.GROUP_UPDATED, {
            "group": group.to_dict(),
            "members": [member.to_dict() for member in members],
        }, source="chat_service")
        publish_simple(
            EventType.CONVERSATION_UPDATED if existed else EventType.CONVERSATION_CREATED,
            {
                "conversation_id": conversation.id,
                "conversation": conversation.to_dict(),
            },
            source="chat_service",
        )

    def _ensure_placeholder_group_for_message(self, message: Message, group_id: str) -> None:
        """群消息先于 group_update 到达时，创建最小占位群，避免丢消息。"""
        with self._groups_lock:
            if group_id in self._groups:
                return

        group_name = (
            message.metadata.get("group_name")
            if isinstance(message.metadata, dict)
            else None
        ) or group_id
        group = Group(
            id=group_id,
            name=group_name,
            creator_id=message.sender_id,
            metadata={"sync_status": "reserved", "placeholder": True},
        )
        members = [
            GroupMember(
                group_id=group_id,
                user_id=self.current_user_id,
                display_name=self.get_current_user_display_name(),
            ),
            GroupMember(
                group_id=group_id,
                user_id=message.sender_id,
                display_name=self._get_member_display_name(message.sender_id),
            ),
        ]
        self._persist_group_state(group, members)

    def _decrypt_group_message_if_needed(self, message: Message) -> str:
        """Decrypt group_encrypted_v1 payloads in place."""
        if not isinstance(message.metadata, dict):
            return "group_plain_v3"
        if message.metadata.get("encryption_version") != "group_encrypted_v1":
            return message.metadata.get("encryption_scope", "group_plain_v3")
        if self.group_crypto_service is None:
            message.content = "[Encrypted group message: local crypto unavailable]"
            return "failed"
        try:
            encrypted_metadata = {
                key: value
                for key, value in message.metadata.items()
                if key in {
                    "encryption_version",
                    "alg",
                    "group_id",
                    "group_key_id",
                    "group_key_version",
                    "sender_device_id",
                    "sequence",
                    "nonce",
                    "ciphertext",
                    "encryption_scope",
                }
            }
            message.encryption_version = "group_encrypted_v1"
            message.encrypted_content = json.dumps(encrypted_metadata, ensure_ascii=False)
            payload = self.group_crypto_service.decrypt_payload(message.id, message.sender_id, encrypted_metadata)
            inner_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            message.content = str(payload.get("content") or "")
            raw_message_type = str(payload.get("message_type") or MessageType.TEXT.value)
            try:
                message.message_type = MessageType(raw_message_type)
            except ValueError:
                message.message_type = MessageType.TEXT
            message.metadata = {
                **inner_metadata,
                "conversation_type": ConversationType.GROUP.value,
                "group_id": encrypted_metadata.get("group_id", message.receiver_id),
                "encryption_scope": "group_encrypted_v1",
                "decrypted_from": "group_encrypted_v1",
            }
            return "success"
        except ReplayProtectionError as e:
            message.metadata = {
                **(message.metadata or {}),
                "security_rejected": True,
                "security_reason": str(e),
            }
            message.content = "[Encrypted group message: replay rejected]"
            return "replay_rejected"
        except DecryptionError as e:
            self.storage.save_decryption_failure(
                message.id,
                message.conversation_id,
                message.sender_id,
                "group_encrypted_v1",
                str((message.metadata or {}).get("group_key_id", "")),
                str(e),
            )
            self.logger.error(f"群消息解密失败: msg_id={message.id[:8]}, error={e}")
            message.content = "[Encrypted group message: unable to decrypt]"
            return "failed"

    def _on_group_message_received(self, message: Message) -> None:
        """处理群消息，绕开单聊联系人授权链路。"""
        if message.sender_id == self.current_user_id:
            return

        group_id = (
            message.metadata.get("group_id")
            if isinstance(message.metadata, dict)
            else None
        ) or message.conversation_id or message.receiver_id
        if not group_id:
            self.logger.warning("收到缺少 group_id 的群消息，已忽略")
            return

        self._ensure_placeholder_group_for_message(message, group_id)

        message.receiver_id = group_id
        message.conversation_id = group_id
        message.status = MessageStatus.SENT
        message.auth_status = MessageAuthStatus.TRUSTED
        decryption_status = self._decrypt_group_message_if_needed(message)
        if (message.metadata or {}).get("security_rejected"):
            self.logger.warning(f"拒绝重复/nonce 异常群消息: msg_id={message.id[:8]}")
            return
        message.metadata = {
            **(message.metadata or {}),
            "conversation_type": ConversationType.GROUP.value,
            "group_id": group_id,
            "encryption_scope": message.metadata.get("encryption_scope", "group_plain_v3") if message.metadata else "group_plain_v3",
            "sync_status": message.metadata.get("sync_status", "reserved") if message.metadata else "reserved",
        }
        if isinstance(message.metadata, dict) and message.metadata.get("schema") in {"file_event_v1", TEMP_FILE_SCHEMA}:
            message.message_type = MessageType.FILE

        self.storage.save_message(message)
        if isinstance(message.metadata, dict) and message.metadata.get("schema") in {"file_event_v1", TEMP_FILE_SCHEMA}:
            self.storage.save_file_attachment(
                self.sync_service.build_file_attachment(message.id, message.metadata)
            )

        conversation_created_event = None
        conversation_updated_event = None
        with self._conversations_lock:
            conv = self._conversations.get(group_id)
            if conv is None:
                group = self.storage.get_group(group_id) or Group(id=group_id, name=group_id)
                members = self.storage.get_group_members(group_id, active_only=True)
                conv = self._build_group_conversation(group, members)
                self._conversations[group_id] = conv
                self.storage.save_conversation(conv)
                conversation_created_event = {"conversation": conv.to_dict()}

            conv.update_last_message(
                message_id=message.id,
                preview=self._build_message_preview(message.content),
                timestamp=message.timestamp,
            )
            if self.current_conversation_id != group_id:
                conv.unread_count += 1
            self.storage.save_conversation(conv)
            conversation_updated_event = {
                "conversation_id": group_id,
                "conversation": conv.to_dict(),
            }

        if conversation_created_event is not None:
            publish_simple(EventType.CONVERSATION_CREATED, conversation_created_event, source="chat_service")
        if conversation_updated_event is not None:
            publish_simple(EventType.CONVERSATION_UPDATED, conversation_updated_event, source="chat_service")

        publish_simple(EventType.MESSAGE_RECEIVED, {
            "message": message.to_dict(),
            "conversation_id": group_id,
            "conversation_type": ConversationType.GROUP.value,
            "trust_status": "trusted",
            "pairing_pending": False,
            "decryption_status": decryption_status,
        }, source="chat_service")

    def _on_message_received(self, message: Message) -> None:
        """
        处理接收到的消息（传输层回调）

        Args:
            message: 接收到的消息
        """
        if isinstance(message.metadata, dict) and message.metadata.get("control_type") == "group_update":
            self._on_group_update_received(
                message.metadata.get("group", {}),
                message.metadata.get("members", []),
                message.metadata.get("inviter_id") or message.sender_id,
            )
            return

        if (
            isinstance(message.metadata, dict)
            and message.metadata.get("conversation_type") == ConversationType.GROUP.value
        ) or message.receiver_id.startswith("grp_") or message.conversation_id.startswith("grp_"):
            self._on_group_message_received(message)
            return

        sender_id = message.sender_id
        exact_sender_contact_id = self.identity_manager.normalize_contact_id(sender_id)
        if not exact_sender_contact_id:
            self.logger.warning("收到空发送者ID的消息，已忽略")
            return

        message.sender_id = exact_sender_contact_id
        message.receiver_id = self.current_user_id

        contact = self.identity_manager.get_contact(exact_sender_contact_id)
        sender_display_name = (getattr(message, "sender_display_name", "") or "").strip()
        if contact is None:
            from src.models.user import User, UserStatus

            self.identity_manager.add_contact(
                User(
                    user_id=exact_sender_contact_id,
                    display_name=sender_display_name,
                    status=UserStatus.ONLINE,
                ),
                ContactAuthStatus.PENDING_INCOMING,
            )
            contact = self.identity_manager.get_contact(exact_sender_contact_id)
            self.logger.info(f"收到新联系人的消息，创建联系人并进入 pending: {sender_id}")
        elif sender_display_name and contact.display_name != sender_display_name:
            self.identity_manager.update_contact_display_name(exact_sender_contact_id, sender_display_name)
            contact = self.identity_manager.get_contact(exact_sender_contact_id)
        elif contact.trust_status == ContactAuthStatus.UNKNOWN:
            self.identity_manager.update_contact_auth_status(
                exact_sender_contact_id,
                ContactAuthStatus.PENDING_INCOMING,
            )
            contact = self.identity_manager.get_contact(exact_sender_contact_id)
            self.logger.info(f"收到未授权联系人的消息，状态切换为 pending: {sender_id}")

        trust_status = contact.trust_status if contact is not None else ContactAuthStatus.PENDING_INCOMING

        if message.status == MessageStatus.SENDING:
            message.status = MessageStatus.SENT

        if trust_status == ContactAuthStatus.REJECTED:
            message.auth_status = MessageAuthStatus.REJECTED
            self.storage.save_message(message)
            self.logger.info(f"收到已拒绝联系人的消息，已拦截正式聊天: {sender_id}")
            return

        if trust_status != ContactAuthStatus.TRUSTED:
            message.auth_status = MessageAuthStatus.PENDING
            decryption_status = self._decrypt_message_if_needed(message)
            if (message.metadata or {}).get("security_rejected"):
                self.logger.warning(f"拒绝重复/nonce 异常待授权消息: msg_id={message.id[:8]}")
                return
            pending_count = self.identity_manager.increment_contact_pending_count(exact_sender_contact_id)
            self.storage.save_message(message)
            publish_simple(EventType.CONTACT_AUTH_REQUIRED, {
                "contact_id": exact_sender_contact_id,
                "message_id": message.id,
                "message_preview": self._build_message_preview(message.content),
                "timestamp": message.timestamp,
                "pending_message_count": pending_count,
                "decryption_status": decryption_status,
            }, source="chat_service")
            self.logger.info(
                f"M2-C 收到待授权消息: sender={sender_id}, pending_count={pending_count}, msg_id={message.id[:8]}"
            )
            return

        message.auth_status = MessageAuthStatus.TRUSTED

        decryption_status = self._decrypt_message_if_needed(message)
        if (message.metadata or {}).get("security_rejected"):
            self.logger.warning(f"拒绝重复/nonce 异常消息: msg_id={message.id[:8]}")
            return

        # 更新消息状态（如果还是SENDING状态）
        if message.status == MessageStatus.SENDING:
            message.status = MessageStatus.SENT

        # 保存消息到数据库（此时message.content可能是解密后的明文）
        self.storage.save_message(message)
        if self._is_temp_file_message(message):
            self.storage.save_file_attachment(
                self.sync_service.build_file_attachment(message.id, message.metadata)
            )

        # 确保会话存在并更新（线程安全）
        conversation_created_event = None
        conversation_updated_event = None
        with self._conversations_lock:
            if message.conversation_id not in self._conversations:
                # 创建新会话（使用消息中的conversation_id）
                # 参与者：发送者和当前用户
                participant_ids = sorted([message.sender_id, self.current_user_id])
                # 注意：message.conversation_id应该基于参与者生成，但为了兼容性，我们使用现有的ID
                conv = Conversation(
                    id=message.conversation_id,
                    participant_ids=participant_ids,
                    display_name=self._get_display_name_for_user(message.sender_id)
                )
                self._conversations[message.conversation_id] = conv
                self.storage.save_conversation(conv)
                self.logger.info(f"为新消息创建会话: {message.conversation_id}, 参与者: {participant_ids}")
                
                conversation_created_event = {
                    "conversation": conv.to_dict()
                }
            
            # 更新会话
            conv = self._conversations[message.conversation_id]
            conv.update_last_message(
                message_id=message.id,
                preview=message.content[:50] + ("..." if len(message.content) > 50 else ""),
                timestamp=message.timestamp
            )
            conv.unread_count += 1

            # 保存会话到数据库
            self.storage.save_conversation(conv)

            conversation_updated_event = {
                "conversation_id": message.conversation_id,
                "conversation": conv.to_dict()
            }

        if conversation_created_event is not None:
            publish_simple(EventType.CONVERSATION_CREATED, conversation_created_event, source="chat_service")
        if conversation_updated_event is not None:
            publish_simple(EventType.CONVERSATION_UPDATED, conversation_updated_event, source="chat_service")

        # 发布消息接收事件
        publish_simple(EventType.MESSAGE_RECEIVED, {
            "message": message.to_dict(),
            "conversation_id": message.conversation_id,
            "trust_status": "trusted",
            "pairing_pending": False,
            "decryption_status": decryption_status
        }, source="chat_service")




    def _get_display_name_for_user(self, user_id: str) -> str:
        """获取用户的显示名称"""
        exact_user_id = self.identity_manager.normalize_contact_id(user_id)
        contact = self.identity_manager.get_contact(exact_user_id)
        if contact:
            return contact.get_display_name()
        
        # 如果是当前用户自己
        if exact_user_id == self.current_user_id:
            return self.get_current_user_display_name()
        
        # 默认返回用户ID
        return exact_user_id

    def _get_or_create_conversation_id(self, other_user_id: str) -> str:
        """
        获取或创建与指定用户的会话ID

        Args:
            other_user_id: 对方用户ID

        Returns:
            str: 会话ID
        """
        other_user_id = self.identity_manager.normalize_contact_id(other_user_id)
        # 生成确定性会话ID
        deterministic_id = Conversation.generate_id([self.current_user_id, other_user_id])
        
        # 首先在本地缓存中查找
        if deterministic_id in self._conversations:
            return deterministic_id
        
        # 在数据库中查找（可能已由对方创建）
        db_conv = self.storage.get_conversation(deterministic_id)
        if db_conv:
            self._conversations[deterministic_id] = db_conv
            return deterministic_id
        
        # 查找现有会话（兼容旧版本，使用非确定性ID的会话）
        for conv_id, conv in self._conversations.items():
            if other_user_id in conv.participant_ids and self.current_user_id in conv.participant_ids:
                # 迁移到确定性ID：保存新ID的会话到数据库
                conv.id = deterministic_id
                self.storage.save_conversation(conv)
                self._conversations[deterministic_id] = conv
                # 移除旧ID的条目并删除数据库中的旧会话
                if conv_id != deterministic_id:
                    del self._conversations[conv_id]
                    # 删除数据库中的旧会话记录
                    self.storage.delete_conversation(conv_id)
                return deterministic_id
        
        # 创建新会话，使用确定性ID
        new_conv = Conversation(
            id=deterministic_id,
            participant_ids=[self.current_user_id, other_user_id],
            display_name=self._get_display_name_for_user(other_user_id)
        )
        self._conversations[deterministic_id] = new_conv
        
        # 保存到数据库
        self.storage.save_conversation(new_conv)

        # 发布会话创建事件
        publish_simple(EventType.CONVERSATION_CREATED, {
            "conversation": new_conv.to_dict()
        }, source="chat_service")

        return deterministic_id

    def get_conversation_with_user(self, other_user_id: str) -> Optional[str]:
        """
        获取与指定用户的会话ID，如果不存在则创建

        Args:
            other_user_id: 对方用户ID

        Returns:
            Optional[str]: 会话ID，如果出错返回None
        """
        try:
            return self._get_or_create_conversation_id(other_user_id)
        except Exception as e:
            self.logger.error(f"获取会话ID失败: {e}")
            return None

    def get_conversation_with_group(self, group_id: str) -> Optional[str]:
        """获取群组对应会话ID。第三阶段群会话 ID 等于 group_id。"""
        with self._groups_lock:
            if group_id not in self._groups:
                return None
        if group_id not in self._conversations:
            group = self.storage.get_group(group_id)
            if group is None:
                return None
            members = self.storage.get_group_members(group_id, active_only=True)
            conv = self._build_group_conversation(group, members)
            with self._conversations_lock:
                self._conversations[group_id] = conv
            self.storage.save_conversation(conv)
        return group_id

    def get_group_display_name(self, group_id: str) -> str:
        """获取群组显示名。"""
        with self._groups_lock:
            group = self._groups.get(group_id)
        if group is not None and group.name:
            return group.name
        return group_id

    def load_messages_for_conversation(self, conversation_id: str, limit: int = 50) -> List[Message]:
        """
        加载会话中的历史消息

        Args:
            conversation_id: 会话ID
            limit: 最大消息数量

        Returns:
            List[Message]: 消息列表，按时间升序排列
        """
        try:
            messages = self.storage.get_messages(conversation_id, limit=limit)
            conv = self._conversations.get(conversation_id)
            if not (conv and conv.conversation_type == ConversationType.GROUP) and not conversation_id.startswith("grp_"):
                for message in messages:
                    self._decrypt_message_if_needed(message)
            self.logger.info(f"为会话 {conversation_id[:8]} 加载 {len(messages)} 条历史消息")
            return messages
        except Exception as e:
            self.logger.error(f"加载历史消息失败: {e}")
            return []

    def load_messages_for_contact(self, contact_id: str, limit: int = 50) -> List[Message]:
        """
        按当前用户和联系人ID加载消息。

        UI 在 M2-A 只允许以 active_contact_id 作为当前聊天状态源，
        因此消息面板不再依赖 conversation_id 兜底。
        """
        exact_contact_id = self.identity_manager.normalize_contact_id(contact_id)
        if not exact_contact_id:
            return []

        deterministic_conversation_id = Conversation.generate_id([self.current_user_id, exact_contact_id])
        messages = self.storage.get_messages(deterministic_conversation_id, limit=limit)

        filtered_messages = [
            msg for msg in messages
            if self.is_message_for_contact(msg, exact_contact_id)
        ]
        if filtered_messages:
            for message in filtered_messages:
                self._decrypt_message_if_needed(message)
            self.logger.info(f"为联系人 {exact_contact_id} 加载 {len(filtered_messages)} 条消息")
            return filtered_messages

        recent_messages = self.storage.get_recent_messages_for_user(self.current_user_id, limit=max(limit * 10, 200))
        filtered_messages = [
            msg for msg in recent_messages
            if self.is_message_for_contact(msg, exact_contact_id)
        ]
        if len(filtered_messages) > limit:
            filtered_messages = filtered_messages[-limit:]
        for message in filtered_messages:
            self._decrypt_message_if_needed(message)
        self.logger.info(f"为联系人 {exact_contact_id} 回退加载 {len(filtered_messages)} 条消息")
        return filtered_messages

    def get_contacts(self) -> Dict[str, User]:
        """
        获取联系人列表

        Returns:
            Dict[str, User]: 联系人字典
        """
        return self._get_contacts()

    def get_conversations(self) -> Dict[str, Conversation]:
        """
        获取会话列表

        Returns:
            Dict[str, Conversation]: 会话字典
        """
        with self._conversations_lock:
            return self._conversations.copy()

    def select_conversation(self, conversation_id: str) -> bool:
        """
        选择当前会话，并清零未读计数

        Args:
            conversation_id: 会话ID

        Returns:
            bool: 是否成功选择
        """
        conversation_updated_event = None
        conversation_selected_event = None
        with self._conversations_lock:
            if conversation_id not in self._conversations:
                self.logger.warning(f"尝试选择不存在的会话: {conversation_id}")
                return False

            self.current_conversation_id = conversation_id
            
            conv = self._conversations[conversation_id]
            if conv.unread_count > 0:
                conv.unread_count = 0
                self.storage.save_conversation(conv)
                conversation_updated_event = {
                    "conversation_id": conversation_id,
                    "conversation": conv.to_dict()
                }

            conversation_selected_event = {
                "conversation_id": conversation_id,
                "conversation": conv.to_dict()
            }

        if conversation_updated_event is not None:
            publish_simple(EventType.CONVERSATION_UPDATED, conversation_updated_event, source="chat_service")
        if conversation_selected_event is not None:
            publish_simple(EventType.CONVERSATION_SELECTED, conversation_selected_event, source="chat_service")

        self.logger.info(f"选择会话: {conversation_id}")
        return True

    def get_current_user_id(self) -> str:
        """
        获取当前用户ID

        Returns:
            str: 当前用户ID
        """
        return self.current_user_id

    def __del__(self):
        """析构函数，确保资源清理"""
        try:
            self.stop()
        except:
            # Python关闭时可能无法正常停止，忽略错误
            pass
