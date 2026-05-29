"""
SQLite 存储模块

负责消息、会话的持久化存储和历史加载。
使用简单的sqlite3接口，不依赖ORM。
"""

import sqlite3
import json
import logging
import threading
import time
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime

from src.models.message import Message, MessageStatus, MessageType, MessageAuthStatus
from src.models.conversation import Conversation, ConversationType
from src.models.group import Group, GroupMember, GroupMemberStatus
from src.models.sync import FileAttachment, Project, SharedFolder, SyncDevice
from src.models.user import User
from src.models.contact import Contact, ContactAuthStatus, normalize_contact_auth_status
from src.storage.project_index_schema import create_project_index_schema
from src.utils.logger import get_logger


class SQLiteStore:
    """
    SQLite 存储管理器

    提供消息和会话的持久化存储功能。
    线程安全：使用连接池或每个线程独立连接。
    """

    def __init__(self, db_path: str = "data/chat.db"):
        """
        初始化SQLite存储

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = get_logger("sqlite_store")

        # 线程局部存储连接（每个线程使用独立连接）
        self._local = threading.local()

        # 初始化数据库
        self._init_db()

        self.logger.info(f"SQLite存储初始化完成，数据库: {db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """获取线程本地数据库连接"""
        if not hasattr(self._local, 'conn'):
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row  # 支持字典式访问
            self._local.conn = conn
        return self._local.conn

    @property
    def connection(self) -> sqlite3.Connection:
        """兼容旧测试的连接访问属性。"""
        return self._get_connection()

    def _init_db(self) -> None:
        """初始化数据库表结构"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 用户表（预留，第一阶段仍使用模拟数据）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE,
                display_name TEXT,
                status TEXT,
                last_seen REAL
            )
        """)

        # 联系人表（存储联系人授权状态）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                user_id TEXT PRIMARY KEY,          -- 联系人用户ID
                display_name TEXT,                 -- 显示名称
                alias TEXT,                        -- 别名/备注
                auth_status TEXT DEFAULT 'unknown', -- 授权状态: unknown, pending_incoming, trusted, rejected
                added_at REAL DEFAULT (unixepoch()), -- 添加时间
                last_interaction REAL,              -- 最后交互时间
                pending_message_count INTEGER DEFAULT 0, -- 待授权消息计数
                metadata TEXT DEFAULT '{}'          -- JSON字符串，扩展字段
            )
        """)

        # 会话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                conversation_type TEXT DEFAULT 'direct',
                participant_ids TEXT,  -- JSON数组: ["user1", "user2"]
                display_name TEXT,
                avatar_url TEXT,
                last_message_id TEXT,
                last_message_preview TEXT,
                last_message_time REAL,
                unread_count INTEGER DEFAULT 0,
                group_name TEXT,
                group_avatar TEXT,
                admins TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}',
                created_at REAL DEFAULT (unixepoch()),
                updated_at REAL DEFAULT (unixepoch())
            )
        """)

        # 群组表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                creator_id TEXT,
                avatar_url TEXT,
                created_at REAL DEFAULT (unixepoch()),
                updated_at REAL DEFAULT (unixepoch()),
                metadata TEXT DEFAULT '{}'
            )
        """)

        # 群成员表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT,
                status TEXT DEFAULT 'active',
                joined_at REAL DEFAULT (unixepoch()),
                metadata TEXT DEFAULT '{}',
                PRIMARY KEY (group_id, user_id),
                FOREIGN KEY (group_id) REFERENCES groups(id)
            )
        """)

        # 第四阶段项目空间与 Syncthing / 文件同步表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                group_id TEXT UNIQUE,
                name TEXT,
                root_shared_folder_id TEXT,
                status TEXT DEFAULT 'reserved',
                created_by TEXT,
                metadata TEXT DEFAULT '{}',
                created_at REAL DEFAULT (unixepoch()),
                updated_at REAL DEFAULT (unixepoch())
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shared_folders (
                id TEXT PRIMARY KEY,
                name TEXT,
                group_id TEXT,
                local_path TEXT,
                syncthing_folder_id TEXT,
                status TEXT DEFAULT 'reserved',
                project_id TEXT,
                folder_type TEXT DEFAULT 'root',
                last_status TEXT,
                last_completion REAL DEFAULT 0,
                last_error TEXT,
                last_event_id INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}',
                created_at REAL DEFAULT (unixepoch()),
                updated_at REAL DEFAULT (unixepoch())
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_attachments (
                id TEXT PRIMARY KEY,
                message_id TEXT,
                file_name TEXT,
                size INTEGER,
                mime_type TEXT,
                sha256 TEXT,
                shared_folder_id TEXT,
                relative_path TEXT,
                sync_status TEXT DEFAULT 'reserved',
                event_type TEXT,
                project_id TEXT,
                origin_user_id TEXT,
                syncthing_event_id TEXT,
                metadata TEXT DEFAULT '{}',
                created_at REAL DEFAULT (unixepoch()),
                updated_at REAL DEFAULT (unixepoch())
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_devices (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                syncthing_device_id TEXT NOT NULL,
                display_name TEXT,
                status TEXT DEFAULT 'manual',
                metadata TEXT DEFAULT '{}',
                created_at REAL DEFAULT (unixepoch()),
                updated_at REAL DEFAULT (unixepoch()),
                PRIMARY KEY (group_id, user_id, syncthing_device_id)
            )
        """)

        create_project_index_schema(conn)

        # 设备信任表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS device_trust (
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                public_key TEXT,
                fingerprint TEXT,
                trust_status TEXT DEFAULT 'unknown',
                paired_at REAL,
                updated_at REAL DEFAULT (unixepoch()),
                PRIMARY KEY (user_id, device_id)
            )
        """)

        # 第七阶段：端到端加密增强所需的最小本地状态。
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crypto_sessions (
                peer_user_id TEXT NOT NULL,
                peer_device_id TEXT NOT NULL,
                key_version INTEGER NOT NULL DEFAULT 1,
                key_id TEXT NOT NULL,
                peer_public_key TEXT,
                status TEXT DEFAULT 'active',
                send_sequence INTEGER DEFAULT 0,
                receive_sequence INTEGER DEFAULT 0,
                created_at REAL DEFAULT (unixepoch()),
                updated_at REAL DEFAULT (unixepoch()),
                metadata TEXT DEFAULT '{}',
                PRIMARY KEY (peer_user_id, peer_device_id, key_version)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_security_seen (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                direction TEXT NOT NULL,
                message_id TEXT NOT NULL,
                sender_id TEXT,
                sender_device_id TEXT NOT NULL,
                key_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                nonce TEXT NOT NULL,
                created_at REAL DEFAULT (unixepoch()),
                metadata TEXT DEFAULT '{}'
            )
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_security_seen_message
            ON message_security_seen(scope, direction, message_id)
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_security_seen_sequence
            ON message_security_seen(scope, direction, sender_device_id, key_id, sequence)
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_security_seen_nonce
            ON message_security_seen(scope, direction, sender_device_id, key_id, nonce)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decryption_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT,
                conversation_id TEXT,
                sender_id TEXT,
                encryption_version TEXT,
                key_id TEXT,
                reason TEXT,
                created_at REAL DEFAULT (unixepoch()),
                metadata TEXT DEFAULT '{}'
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS group_keys (
                group_id TEXT NOT NULL,
                group_key_version INTEGER NOT NULL,
                group_key_id TEXT NOT NULL,
                key_material TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at REAL DEFAULT (unixepoch()),
                rotated_at REAL DEFAULT 0,
                metadata TEXT DEFAULT '{}',
                PRIMARY KEY (group_id, group_key_version)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_group_keys_active
            ON group_keys(group_id, status, group_key_version DESC)
        """)

        # 消息表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                content TEXT,
                sender_id TEXT,
                receiver_id TEXT,
                conversation_id TEXT,
                status TEXT,
                message_type TEXT DEFAULT 'text',
                timestamp REAL,
                delivered_at REAL,
                read_at REAL,
                encrypted_content TEXT,  -- 预留字段
                encryption_version TEXT, -- 预留字段
                signature TEXT,         -- 预留字段
                auth_status TEXT DEFAULT 'trusted', -- 消息授权状态: pending, trusted, rejected
                metadata TEXT,          -- JSON字符串
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)

        # 检查并添加缺失的列（数据库迁移）
        self._ensure_columns_exist(cursor)

        # 索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_timestamp
            ON messages(timestamp DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_sender
            ON messages(sender_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_receiver
            ON messages(receiver_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation_timestamp
            ON messages(conversation_id, timestamp DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_group_members_user
            ON group_members(user_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_group_members_group
            ON group_members(group_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_folders_group
            ON shared_folders(group_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_projects_group
            ON projects(group_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_folders_project
            ON shared_folders(project_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_attachments_message
            ON file_attachments(message_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_attachments_project
            ON file_attachments(project_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sync_devices_group
            ON sync_devices(group_id)
        """)

        conn.commit()
        self.logger.info("数据库表结构初始化完成")
    
    def _ensure_columns_exist(self, cursor) -> None:
        """确保所有必需的列都存在（数据库迁移）"""
        # 检查messages表的auth_status列
        cursor.execute("PRAGMA table_info(messages)")
        columns = {row[1] for row in cursor.fetchall()}
        
        if 'auth_status' not in columns:
            self.logger.info("添加缺失的列: auth_status 到 messages 表")
            cursor.execute("ALTER TABLE messages ADD COLUMN auth_status TEXT DEFAULT 'trusted'")
        
        # 检查contacts表的alias列
        cursor.execute("PRAGMA table_info(contacts)")
        columns = {row[1] for row in cursor.fetchall()}
        
        if 'alias' not in columns:
            self.logger.info("添加缺失的列: alias 到 contacts 表")
            cursor.execute("ALTER TABLE contacts ADD COLUMN alias TEXT")
        
        if 'pending_message_count' not in columns:
            self.logger.info("添加缺失的列: pending_message_count 到 contacts 表")
            cursor.execute("ALTER TABLE contacts ADD COLUMN pending_message_count INTEGER DEFAULT 0")

        # 检查 conversations 表的第三阶段列
        cursor.execute("PRAGMA table_info(conversations)")
        columns = {row[1] for row in cursor.fetchall()}

        conversation_columns = {
            "conversation_type": "TEXT DEFAULT 'direct'",
            "avatar_url": "TEXT",
            "last_message_id": "TEXT",
            "group_name": "TEXT",
            "group_avatar": "TEXT",
            "admins": "TEXT DEFAULT '[]'",
            "metadata": "TEXT DEFAULT '{}'",
            "updated_at": "REAL",
        }
        for column_name, column_spec in conversation_columns.items():
            if column_name not in columns:
                self.logger.info(f"添加缺失的列: {column_name} 到 conversations 表")
                cursor.execute(f"ALTER TABLE conversations ADD COLUMN {column_name} {column_spec}")

        cursor.execute("UPDATE conversations SET conversation_type = 'direct' WHERE conversation_type IS NULL OR conversation_type = ''")
        cursor.execute("UPDATE conversations SET admins = '[]' WHERE admins IS NULL OR admins = ''")
        cursor.execute("UPDATE conversations SET metadata = '{}' WHERE metadata IS NULL OR metadata = ''")
        cursor.execute("UPDATE conversations SET updated_at = created_at WHERE updated_at IS NULL")

        # 检查第四阶段 shared_folders 扩展列
        cursor.execute("PRAGMA table_info(shared_folders)")
        columns = {row[1] for row in cursor.fetchall()}
        shared_folder_columns = {
            "project_id": "TEXT",
            "folder_type": "TEXT DEFAULT 'root'",
            "last_status": "TEXT",
            "last_completion": "REAL DEFAULT 0",
            "last_error": "TEXT",
            "last_event_id": "INTEGER DEFAULT 0",
        }
        for column_name, column_spec in shared_folder_columns.items():
            if column_name not in columns:
                self.logger.info(f"添加缺失的列: {column_name} 到 shared_folders 表")
                cursor.execute(f"ALTER TABLE shared_folders ADD COLUMN {column_name} {column_spec}")

        cursor.execute("UPDATE shared_folders SET folder_type = 'root' WHERE folder_type IS NULL OR folder_type = ''")
        cursor.execute("UPDATE shared_folders SET last_completion = 0 WHERE last_completion IS NULL")
        cursor.execute("UPDATE shared_folders SET last_event_id = 0 WHERE last_event_id IS NULL")

        # 检查第四阶段 file_attachments 扩展列
        cursor.execute("PRAGMA table_info(file_attachments)")
        columns = {row[1] for row in cursor.fetchall()}
        file_attachment_columns = {
            "event_type": "TEXT",
            "project_id": "TEXT",
            "origin_user_id": "TEXT",
            "syncthing_event_id": "TEXT",
        }
        for column_name, column_spec in file_attachment_columns.items():
            if column_name not in columns:
                self.logger.info(f"添加缺失的列: {column_name} 到 file_attachments 表")
                cursor.execute(f"ALTER TABLE file_attachments ADD COLUMN {column_name} {column_spec}")

        # 将历史复杂状态收敛到 M2-C 最小状态机
        cursor.execute("UPDATE contacts SET auth_status = 'rejected' WHERE auth_status = 'blocked'")
        cursor.execute("UPDATE contacts SET auth_status = 'unknown' WHERE auth_status = 'pending_outgoing'")

    def save_message(self, message: Message) -> bool:
        """
        保存消息到数据库

        Args:
            message: 消息对象

        Returns:
            bool: 是否保存成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO messages
                (id, content, sender_id, receiver_id, conversation_id, status,
                 message_type, timestamp, delivered_at, read_at,
                 encrypted_content, encryption_version, signature, auth_status, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message.id,
                message.content,
                message.sender_id,
                message.receiver_id,
                message.conversation_id,
                message.status.value,
                message.message_type.value,
                message.timestamp,
                message.delivered_at,
                message.read_at,
                message.encrypted_content,
                message.encryption_version,
                message.signature,
                message.auth_status.value if hasattr(message, 'auth_status') else 'trusted',
                json.dumps(message.metadata) if message.metadata else '{}'
            ))

            conn.commit()
            self.logger.debug(f"消息保存成功: {message.id[:8]}")
            return True

        except Exception as e:
            self.logger.error(f"保存消息失败: {e}", exc_info=True)
            return False

    def update_message_status(self, message_id: str, status: MessageStatus,
                            delivered_at: Optional[float] = None,
                            read_at: Optional[float] = None) -> bool:
        """
        更新消息状态

        Args:
            message_id: 消息ID
            status: 新状态
            delivered_at: 送达时间（可选）
            read_at: 已读时间（可选）

        Returns:
            bool: 是否更新成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            update_fields = ["status = ?"]
            params = [status.value]

            if delivered_at is not None:
                update_fields.append("delivered_at = ?")
                params.append(delivered_at)

            if read_at is not None:
                update_fields.append("read_at = ?")
                params.append(read_at)

            params.append(message_id)

            cursor.execute(f"""
                UPDATE messages
                SET {', '.join(update_fields)}
                WHERE id = ?
            """, params)

            conn.commit()
            self.logger.debug(f"消息状态更新: {message_id[:8]} -> {status.value}")
            return True

        except Exception as e:
            self.logger.error(f"更新消息状态失败: {e}", exc_info=True)
            return False

    def bulk_update_message_auth_status_by_sender(
        self,
        sender_id: str,
        old_auth_status: MessageAuthStatus,
        new_auth_status: MessageAuthStatus,
    ) -> int:
        """
        批量更新指定发送者的消息授权状态，并返回影响行数。
        
        Args:
            sender_id: 发送者用户ID
            old_auth_status: 原授权状态（只更新此状态的消息）
            new_auth_status: 新授权状态
            
        Returns:
            int: 更新的消息数量
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE messages
                SET auth_status = ?
                WHERE sender_id = ? AND auth_status = ?
            """, (new_auth_status.value, sender_id, old_auth_status.value))
            
            conn.commit()
            updated_count = cursor.rowcount
            if updated_count > 0:
                self.logger.info(f"更新消息授权状态: {sender_id[:8]}, {old_auth_status.value}->{new_auth_status.value}, 数量: {updated_count}")
            return updated_count
            
        except Exception as e:
            self.logger.error(f"更新消息授权状态失败: {e}", exc_info=True)
            return 0

    def update_message_auth_status_by_sender(
        self,
        sender_id: str,
        old_auth_status: MessageAuthStatus,
        new_auth_status: MessageAuthStatus,
    ) -> bool:
        """兼容旧接口：批量更新发送者消息授权状态。"""
        return self.bulk_update_message_auth_status_by_sender(
            sender_id,
            old_auth_status,
            new_auth_status,
        ) > 0

    def _loads_json(self, raw_value: Optional[str], fallback: Any) -> Any:
        """解析 JSON 字段，坏数据时返回 fallback。"""
        if not raw_value:
            return fallback
        try:
            return json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback

    def _row_to_conversation(self, row: sqlite3.Row) -> Conversation:
        """将 conversations 行转换为 Conversation。"""
        return Conversation(
            id=row['id'],
            conversation_type=Conversation._normalize_type(row['conversation_type'] if 'conversation_type' in row.keys() else 'direct'),
            participant_ids=self._loads_json(row['participant_ids'], []),
            display_name=row['display_name'],
            avatar_url=row['avatar_url'] if 'avatar_url' in row.keys() else None,
            last_message_id=row['last_message_id'] if 'last_message_id' in row.keys() else None,
            last_message_preview=row['last_message_preview'],
            last_message_time=row['last_message_time'],
            unread_count=row['unread_count'],
            group_name=row['group_name'] if 'group_name' in row.keys() else None,
            group_avatar=row['group_avatar'] if 'group_avatar' in row.keys() else None,
            admins=self._loads_json(row['admins'], []),
            metadata=self._loads_json(row['metadata'], {}),
            created_at=row['created_at'],
            updated_at=row['updated_at'] if 'updated_at' in row.keys() else row['created_at'],
        )

    def _row_to_group(self, row: sqlite3.Row) -> Group:
        """将 groups 行转换为 Group。"""
        return Group(
            id=row['id'],
            name=row['name'],
            creator_id=row['creator_id'] or "",
            avatar_url=row['avatar_url'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            metadata=self._loads_json(row['metadata'], {}),
        )

    def _row_to_group_member(self, row: sqlite3.Row) -> GroupMember:
        """将 group_members 行转换为 GroupMember。"""
        try:
            status = GroupMemberStatus(row['status'] or GroupMemberStatus.ACTIVE.value)
        except ValueError:
            status = GroupMemberStatus.ACTIVE
        return GroupMember(
            group_id=row['group_id'],
            user_id=row['user_id'],
            display_name=row['display_name'] or "",
            status=status,
            joined_at=row['joined_at'],
            metadata=self._loads_json(row['metadata'], {}),
        )

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        """将 projects 行转换为 Project。"""
        return Project(
            id=row['id'],
            group_id=row['group_id'] or "",
            name=row['name'] or "",
            root_shared_folder_id=row['root_shared_folder_id'] or "",
            status=row['status'] or "reserved",
            created_by=row['created_by'] or "",
            metadata=self._loads_json(row['metadata'], {}),
            created_at=row['created_at'],
            updated_at=row['updated_at'],
        )

    def _row_to_shared_folder(self, row: sqlite3.Row) -> SharedFolder:
        """将 shared_folders 行转换为 SharedFolder。"""
        return SharedFolder(
            id=row['id'],
            name=row['name'] or "",
            group_id=row['group_id'] or "",
            local_path=row['local_path'] or "",
            syncthing_folder_id=row['syncthing_folder_id'] or "",
            status=row['status'] or "reserved",
            project_id=row['project_id'] if 'project_id' in row.keys() and row['project_id'] else "",
            folder_type=row['folder_type'] if 'folder_type' in row.keys() and row['folder_type'] else "root",
            last_status=row['last_status'] if 'last_status' in row.keys() and row['last_status'] else "",
            last_completion=float(row['last_completion'] or 0) if 'last_completion' in row.keys() else 0.0,
            last_error=row['last_error'] if 'last_error' in row.keys() and row['last_error'] else "",
            last_event_id=int(row['last_event_id'] or 0) if 'last_event_id' in row.keys() else 0,
            metadata=self._loads_json(row['metadata'], {}),
            created_at=row['created_at'],
            updated_at=row['updated_at'],
        )

    def _row_to_sync_device(self, row: sqlite3.Row) -> SyncDevice:
        """将 sync_devices 行转换为 SyncDevice。"""
        return SyncDevice(
            group_id=row['group_id'],
            user_id=row['user_id'],
            syncthing_device_id=row['syncthing_device_id'],
            display_name=row['display_name'] or "",
            status=row['status'] or "manual",
            metadata=self._loads_json(row['metadata'], {}),
            created_at=row['created_at'],
            updated_at=row['updated_at'],
        )

    def _row_to_file_attachment(self, row: sqlite3.Row) -> FileAttachment:
        """将 file_attachments 行转换为 FileAttachment。"""
        return FileAttachment(
            id=row['id'],
            message_id=row['message_id'] or "",
            file_name=row['file_name'] or "",
            size=int(row['size'] or 0),
            mime_type=row['mime_type'] or "",
            sha256=row['sha256'] or "",
            shared_folder_id=row['shared_folder_id'] or "",
            relative_path=row['relative_path'] or "",
            sync_status=row['sync_status'] or "reserved",
            event_type=row['event_type'] if 'event_type' in row.keys() and row['event_type'] else "",
            project_id=row['project_id'] if 'project_id' in row.keys() and row['project_id'] else "",
            origin_user_id=row['origin_user_id'] if 'origin_user_id' in row.keys() and row['origin_user_id'] else "",
            syncthing_event_id=row['syncthing_event_id'] if 'syncthing_event_id' in row.keys() and row['syncthing_event_id'] else "",
            metadata=self._loads_json(row['metadata'], {}),
            created_at=row['created_at'],
            updated_at=row['updated_at'],
        )

    def save_conversation(self, conversation: Conversation) -> bool:
        """
        保存会话到数据库

        Args:
            conversation: 会话对象

        Returns:
            bool: 是否保存成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO conversations
                (id, conversation_type, participant_ids, display_name, avatar_url,
                 last_message_id, last_message_preview, last_message_time, unread_count,
                 group_name, group_avatar, admins, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                conversation.id,
                conversation.conversation_type.value,
                json.dumps(conversation.participant_ids),
                conversation.display_name,
                conversation.avatar_url,
                conversation.last_message_id,
                conversation.last_message_preview,
                conversation.last_message_time,
                conversation.unread_count,
                conversation.group_name,
                conversation.group_avatar,
                json.dumps(conversation.admins),
                json.dumps(conversation.metadata) if conversation.metadata else '{}',
                conversation.created_at,
                conversation.updated_at,
            ))

            conn.commit()
            self.logger.debug(f"会话保存成功: {conversation.id[:8]}")
            return True

        except Exception as e:
            self.logger.error(f"保存会话失败: {e}", exc_info=True)
            return False

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """
        获取会话信息

        Args:
            conversation_id: 会话ID

        Returns:
            Optional[Conversation]: 会话对象，不存在则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM conversations WHERE id = ?
            """, (conversation_id,))

            row = cursor.fetchone()
            if not row:
                return None

            return self._row_to_conversation(row)

        except Exception as e:
            self.logger.error(f"获取会话失败: {e}", exc_info=True)
            return None

    def get_conversations_for_user(self, user_id: str, limit: int = 50) -> Dict[str, Conversation]:
        """
        获取用户的所有会话

        Args:
            user_id: 用户ID
            limit: 最大返回数量

        Returns:
            Dict[str, Conversation]: 会话字典（ID -> Conversation）
        """
        conversations = {}

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT c.*
                FROM conversations c
                LEFT JOIN group_members gm
                    ON c.id = gm.group_id
                    AND gm.user_id = ?
                    AND gm.status = 'active'
                WHERE c.conversation_type = 'group' AND gm.user_id IS NOT NULL
                   OR c.conversation_type IS NULL
                   OR c.conversation_type = 'direct'
                ORDER BY COALESCE(c.last_message_time, c.updated_at, c.created_at) DESC
            """, (user_id,))

            for row in cursor.fetchall():
                conv = self._row_to_conversation(row)
                if conv.conversation_type == ConversationType.DIRECT and user_id not in conv.participant_ids:
                    continue
                if conv.conversation_type == ConversationType.GROUP:
                    active_member_ids = [
                        member.user_id
                        for member in self.get_group_members(conv.id, active_only=True)
                    ]
                    if user_id not in active_member_ids and user_id not in conv.participant_ids:
                        continue
                conversations[conv.id] = conv
                if len(conversations) >= limit:
                    break

            self.logger.debug(f"为用户 {user_id} 加载 {len(conversations)} 个会话")

        except Exception as e:
            self.logger.error(f"获取用户会话失败: {e}", exc_info=True)

        return conversations

    def get_messages(self, conversation_id: str, limit: int = 50,
                    before_time: Optional[float] = None) -> List[Message]:
        """
        获取会话中的消息历史

        Args:
            conversation_id: 会话ID
            limit: 最大消息数量
            before_time: 只获取此时间之前的消息（用于分页）

        Returns:
            List[Message]: 消息列表，按时间升序排列
        """
        messages = []

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            query = """
                SELECT * FROM messages
                WHERE conversation_id = ?
            """
            params = [conversation_id]

            if before_time is not None:
                query += " AND timestamp < ?"
                params.append(before_time)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)

            # 从数据库行转换为Message对象
            for row in cursor.fetchall():
                message = Message(
                    id=row['id'],
                    content=row['content'],
                    sender_id=row['sender_id'],
                    receiver_id=row['receiver_id'],
                    conversation_id=row['conversation_id'],
                    status=MessageStatus(row['status']),
                    message_type=MessageType(row['message_type']),
                    timestamp=row['timestamp'],
                    delivered_at=row['delivered_at'],
                    read_at=row['read_at'],
                    encrypted_content=row['encrypted_content'],
                    encryption_version=row['encryption_version'],
                    signature=row['signature'],
                    auth_status=MessageAuthStatus(row['auth_status']) if row['auth_status'] else MessageAuthStatus.TRUSTED,
                    metadata=json.loads(row['metadata']) if row['metadata'] else {}
                )
                messages.append(message)

            # 返回按时间升序排列的消息
            messages.reverse()
            self.logger.debug(f"为会话 {conversation_id[:8]} 加载 {len(messages)} 条消息")

        except Exception as e:
            self.logger.error(f"获取消息失败: {e}", exc_info=True)

        return messages

    def get_message(self, message_id: str) -> Optional[Message]:
        """按 ID 获取单条消息。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return Message(
                id=row['id'],
                content=row['content'],
                sender_id=row['sender_id'],
                receiver_id=row['receiver_id'],
                conversation_id=row['conversation_id'],
                status=MessageStatus(row['status']),
                message_type=MessageType(row['message_type']),
                timestamp=row['timestamp'],
                delivered_at=row['delivered_at'],
                read_at=row['read_at'],
                encrypted_content=row['encrypted_content'],
                encryption_version=row['encryption_version'],
                signature=row['signature'],
                auth_status=MessageAuthStatus(row['auth_status']) if row['auth_status'] else MessageAuthStatus.TRUSTED,
                metadata=json.loads(row['metadata']) if row['metadata'] else {},
            )
        except Exception as e:
            self.logger.error(f"获取消息失败: {e}", exc_info=True)
            return None

    def get_recent_messages_for_user(self, user_id: str, limit: int = 100) -> List[Message]:
        """
        获取用户相关的最近消息（用于初始化）

        Args:
            user_id: 用户ID
            limit: 最大消息数量

        Returns:
            List[Message]: 消息列表，按时间升序排列
        """
        messages = []

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 查询发送给该用户或由该用户发送的消息
            cursor.execute("""
                SELECT * FROM messages
                WHERE sender_id = ? OR receiver_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (user_id, user_id, limit))

            for row in cursor.fetchall():
                message = Message(
                    id=row['id'],
                    content=row['content'],
                    sender_id=row['sender_id'],
                    receiver_id=row['receiver_id'],
                    conversation_id=row['conversation_id'],
                    status=MessageStatus(row['status']),
                    message_type=MessageType(row['message_type']),
                    timestamp=row['timestamp'],
                    delivered_at=row['delivered_at'],
                    read_at=row['read_at'],
                    encrypted_content=row['encrypted_content'],
                    encryption_version=row['encryption_version'],
                    signature=row['signature'],
                    auth_status=MessageAuthStatus(row['auth_status']) if row['auth_status'] else MessageAuthStatus.TRUSTED,
                    metadata=json.loads(row['metadata']) if row['metadata'] else {}
                )
                messages.append(message)

            # 按时间升序排列
            messages.reverse()
            self.logger.debug(f"为用户 {user_id} 加载 {len(messages)} 条最近消息")

        except Exception as e:
            self.logger.error(f"获取用户最近消息失败: {e}", exc_info=True)

        return messages

    # ========== 群组管理方法 ==========

    def save_group(self, group: Group) -> bool:
        """保存群组信息。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO groups
                (id, name, creator_id, avatar_url, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                group.id,
                group.name,
                group.creator_id,
                group.avatar_url,
                group.created_at,
                group.updated_at,
                json.dumps(group.metadata) if group.metadata else '{}',
            ))

            conn.commit()
            self.logger.debug(f"群组保存成功: {group.id}")
            return True

        except Exception as e:
            self.logger.error(f"保存群组失败: {e}", exc_info=True)
            return False

    def get_group(self, group_id: str) -> Optional[Group]:
        """获取单个群组。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM groups WHERE id = ?", (group_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_group(row)
        except Exception as e:
            self.logger.error(f"获取群组失败: {e}", exc_info=True)
            return None

    def get_groups_for_user(self, user_id: str) -> Dict[str, Group]:
        """获取指定用户所在的 active 群组。"""
        groups: Dict[str, Group] = {}
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT g.*
                FROM groups g
                JOIN group_members gm ON g.id = gm.group_id
                WHERE gm.user_id = ? AND gm.status = 'active'
                ORDER BY g.updated_at DESC
            """, (user_id,))
            for row in cursor.fetchall():
                group = self._row_to_group(row)
                groups[group.id] = group
        except Exception as e:
            self.logger.error(f"获取用户群组失败: {e}", exc_info=True)
        return groups

    def save_group_member(self, member: GroupMember) -> bool:
        """保存群成员关系。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO group_members
                (group_id, user_id, display_name, status, joined_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                member.group_id,
                member.user_id,
                member.display_name,
                member.status.value,
                member.joined_at,
                json.dumps(member.metadata) if member.metadata else '{}',
            ))
            conn.commit()
            self.logger.debug(f"群成员保存成功: {member.group_id}/{member.user_id}")
            return True
        except Exception as e:
            self.logger.error(f"保存群成员失败: {e}", exc_info=True)
            return False

    def save_group_members(self, group_id: str, members: List[GroupMember]) -> bool:
        """批量保存群成员关系。"""
        ok = True
        for member in members:
            if member.group_id != group_id:
                member.group_id = group_id
            ok = self.save_group_member(member) and ok
        return ok

    def get_group_members(self, group_id: str, active_only: bool = True) -> List[GroupMember]:
        """获取群成员列表。"""
        members: List[GroupMember] = []
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            query = "SELECT * FROM group_members WHERE group_id = ?"
            params: List[Any] = [group_id]
            if active_only:
                query += " AND status = ?"
                params.append(GroupMemberStatus.ACTIVE.value)
            query += " ORDER BY joined_at ASC"
            cursor.execute(query, params)
            for row in cursor.fetchall():
                members.append(self._row_to_group_member(row))
        except Exception as e:
            self.logger.error(f"获取群成员失败: {e}", exc_info=True)
        return members

    def is_group_member(self, group_id: str, user_id: str) -> bool:
        """判断用户是否是群 active 成员。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 1 FROM group_members
                WHERE group_id = ? AND user_id = ? AND status = 'active'
                LIMIT 1
            """, (group_id, user_id))
            return cursor.fetchone() is not None
        except Exception as e:
            self.logger.error(f"检查群成员失败: {e}", exc_info=True)
            return False

    # ========== 第四阶段项目/同步管理方法 ==========

    def save_project(self, project: Project) -> bool:
        """保存项目空间。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO projects
                (id, group_id, name, root_shared_folder_id, status, created_by,
                 metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                project.id,
                project.group_id,
                project.name,
                project.root_shared_folder_id,
                project.status,
                project.created_by,
                json.dumps(project.metadata) if project.metadata else '{}',
                project.created_at,
                project.updated_at,
            ))
            conn.commit()
            self.logger.debug(f"项目保存成功: {project.id}")
            return True
        except Exception as e:
            self.logger.error(f"保存项目失败: {e}", exc_info=True)
            return False

    def get_project(self, project_id: str) -> Optional[Project]:
        """获取项目空间。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            row = cursor.fetchone()
            return self._row_to_project(row) if row else None
        except Exception as e:
            self.logger.error(f"获取项目失败: {e}", exc_info=True)
            return None

    def get_project_by_group(self, group_id: str) -> Optional[Project]:
        """按群组获取绑定项目。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM projects WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return self._row_to_project(row) if row else None
        except Exception as e:
            self.logger.error(f"获取群组项目失败: {e}", exc_info=True)
            return None

    def save_shared_folder(self, folder: SharedFolder) -> bool:
        """保存共享文件夹绑定。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO shared_folders
                (id, name, group_id, local_path, syncthing_folder_id, status,
                 project_id, folder_type, last_status, last_completion, last_error,
                 last_event_id, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                folder.id,
                folder.name,
                folder.group_id,
                folder.local_path,
                folder.syncthing_folder_id,
                folder.status,
                folder.project_id,
                folder.folder_type,
                folder.last_status,
                folder.last_completion,
                folder.last_error,
                folder.last_event_id,
                json.dumps(folder.metadata) if folder.metadata else '{}',
                folder.created_at,
                folder.updated_at,
            ))
            conn.commit()
            self.logger.debug(f"共享文件夹保存成功: {folder.id}")
            return True
        except Exception as e:
            self.logger.error(f"保存共享文件夹失败: {e}", exc_info=True)
            return False

    def get_shared_folder(self, folder_id: str) -> Optional[SharedFolder]:
        """获取共享文件夹。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM shared_folders WHERE id = ?", (folder_id,))
            row = cursor.fetchone()
            return self._row_to_shared_folder(row) if row else None
        except Exception as e:
            self.logger.error(f"获取共享文件夹失败: {e}", exc_info=True)
            return None

    def get_shared_folder_by_group(self, group_id: str) -> Optional[SharedFolder]:
        """获取群组 root 共享文件夹。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM shared_folders
                WHERE group_id = ?
                ORDER BY CASE WHEN folder_type = 'root' THEN 0 ELSE 1 END, updated_at DESC
                LIMIT 1
            """, (group_id,))
            row = cursor.fetchone()
            return self._row_to_shared_folder(row) if row else None
        except Exception as e:
            self.logger.error(f"获取群组共享文件夹失败: {e}", exc_info=True)
            return None

    def save_sync_device(self, device: SyncDevice) -> bool:
        """保存群成员 Syncthing Device ID 映射。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sync_devices
                (group_id, user_id, syncthing_device_id, display_name, status,
                 metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                device.group_id,
                device.user_id,
                device.syncthing_device_id,
                device.display_name,
                device.status,
                json.dumps(device.metadata) if device.metadata else '{}',
                device.created_at,
                device.updated_at,
            ))
            conn.commit()
            self.logger.debug(f"同步设备保存成功: {device.group_id}/{device.user_id}")
            return True
        except Exception as e:
            self.logger.error(f"保存同步设备失败: {e}", exc_info=True)
            return False

    def get_sync_devices_for_group(self, group_id: str) -> List[SyncDevice]:
        """获取群组手动配置的 Syncthing 设备。"""
        devices: List[SyncDevice] = []
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM sync_devices
                WHERE group_id = ?
                ORDER BY updated_at DESC
            """, (group_id,))
            for row in cursor.fetchall():
                devices.append(self._row_to_sync_device(row))
        except Exception as e:
                self.logger.error(f"获取同步设备失败: {e}", exc_info=True)
        return devices

    def delete_project_sync_binding(self, group_id: str) -> Dict[str, Any]:
        """Remove this profile's project sync binding metadata for a group.

        This intentionally leaves real files, chat messages, groups, members,
        and historical file-event metadata untouched.
        """
        if not group_id:
            raise ValueError("group_id is required")
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM projects WHERE group_id = ?", (group_id,))
            project_ids = [str(row["id"]) for row in cursor.fetchall()]
            cursor.execute("SELECT id FROM shared_folders WHERE group_id = ?", (group_id,))
            shared_folder_ids = [str(row["id"]) for row in cursor.fetchall()]

            cursor.execute("DELETE FROM sync_devices WHERE group_id = ?", (group_id,))
            deleted_sync_devices = int(cursor.rowcount or 0)
            cursor.execute("DELETE FROM shared_folders WHERE group_id = ?", (group_id,))
            deleted_shared_folders = int(cursor.rowcount or 0)
            cursor.execute("DELETE FROM projects WHERE group_id = ?", (group_id,))
            deleted_projects = int(cursor.rowcount or 0)

            group_metadata_updated = False
            cursor.execute("SELECT metadata FROM groups WHERE id = ?", (group_id,))
            row = cursor.fetchone()
            if row:
                metadata = self._loads_json(row["metadata"], {})
                for key in ("sync_status", "project_id", "shared_folder_id"):
                    metadata.pop(key, None)
                cursor.execute(
                    "UPDATE groups SET metadata = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), time.time(), group_id),
                )
                group_metadata_updated = True

            conn.commit()
            return {
                "group_id": group_id,
                "project_ids": project_ids,
                "shared_folder_ids": shared_folder_ids,
                "deleted_projects": deleted_projects,
                "deleted_shared_folders": deleted_shared_folders,
                "deleted_sync_devices": deleted_sync_devices,
                "group_metadata_updated": group_metadata_updated,
                "real_files_deleted": False,
                "messages_deleted": 0,
                "file_attachments_deleted": 0,
                "scope": "local_profile_project_sync_binding_only",
            }
        except Exception as e:
            self.logger.error(f"删除项目同步绑定失败: {e}", exc_info=True)
            raise

    def save_file_attachment(self, attachment: FileAttachment) -> bool:
        """保存文件事件 metadata。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO file_attachments
                (id, message_id, file_name, size, mime_type, sha256, shared_folder_id,
                 relative_path, sync_status, event_type, project_id, origin_user_id,
                 syncthing_event_id, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                attachment.id,
                attachment.message_id,
                attachment.file_name,
                attachment.size,
                attachment.mime_type,
                attachment.sha256,
                attachment.shared_folder_id,
                attachment.relative_path,
                attachment.sync_status,
                attachment.event_type,
                attachment.project_id,
                attachment.origin_user_id,
                attachment.syncthing_event_id,
                json.dumps(attachment.metadata) if attachment.metadata else '{}',
                attachment.created_at,
                attachment.updated_at,
            ))
            conn.commit()
            self.logger.debug(f"文件附件保存成功: {attachment.id}")
            return True
        except Exception as e:
            self.logger.error(f"保存文件附件失败: {e}", exc_info=True)
            return False

    def get_file_attachments_for_message(self, message_id: str) -> List[FileAttachment]:
        """按消息获取文件 metadata。"""
        attachments: List[FileAttachment] = []
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM file_attachments
                WHERE message_id = ?
                ORDER BY created_at ASC
            """, (message_id,))
            for row in cursor.fetchall():
                attachments.append(self._row_to_file_attachment(row))
        except Exception as e:
            self.logger.error(f"获取文件附件失败: {e}", exc_info=True)
        return attachments

    def get_recent_file_attachments_for_project(self, project_id: str, limit: int = 50) -> List[FileAttachment]:
        """获取项目最近文件事件 metadata。"""
        attachments: List[FileAttachment] = []
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM file_attachments
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (project_id, limit))
            for row in cursor.fetchall():
                attachments.append(self._row_to_file_attachment(row))
            attachments.reverse()
        except Exception as e:
            self.logger.error(f"获取项目文件附件失败: {e}", exc_info=True)
        return attachments

    def save_device_trust(self, device) -> bool:
        """
        保存设备信任记录

        Args:
            device: Device对象

        Returns:
            bool: 是否保存成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO device_trust
                (user_id, device_id, public_key, fingerprint, trust_status, paired_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                device.user_id,
                device.id,
                device.public_key,
                device.fingerprint,
                device.trust_status.value,
                device.created_at,
                time.time()
            ))
            
            conn.commit()
            self.logger.debug(f"设备信任记录保存成功: {device.user_id}/{device.id[:8]}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存设备信任记录失败: {e}", exc_info=True)
            return False

    def get_device_trust(self, user_id: str, device_id: str):
        """
        获取设备信任记录

        Args:
            user_id: 用户ID
            device_id: 设备ID

        Returns:
            Device对象，如果不存在则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT user_id, device_id, public_key, fingerprint, trust_status, paired_at
                FROM device_trust
                WHERE user_id = ? AND device_id = ?
            """, (user_id, device_id))
            
            row = cursor.fetchone()
            if row:
                from src.models.device import Device, TrustStatus
                device = Device(
                    id=row['device_id'],
                    user_id=row['user_id'],
                    public_key=row['public_key'],
                    fingerprint=row['fingerprint'],
                    trust_status=TrustStatus(row['trust_status']),
                    created_at=row['paired_at'] if row['paired_at'] else time.time()
                )
                return device
            return None
            
        except Exception as e:
            self.logger.error(f"获取设备信任记录失败: {e}", exc_info=True)
            return None

    def get_trusted_devices_for_user(self, user_id: str):
        """
        获取用户的所有受信任设备

        Args:
            user_id: 用户ID

        Returns:
            Device对象列表
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT user_id, device_id, public_key, fingerprint, trust_status, paired_at
                FROM device_trust
                WHERE user_id = ? AND trust_status = 'trusted'
            """, (user_id,))
            
            devices = []
            from src.models.device import Device, TrustStatus
            for row in cursor.fetchall():
                device = Device(
                    id=row['device_id'],
                    user_id=row['user_id'],
                    public_key=row['public_key'],
                    fingerprint=row['fingerprint'],
                    trust_status=TrustStatus(row['trust_status']),
                    created_at=row['paired_at'] if row['paired_at'] else time.time()
                )
                devices.append(device)
            return devices
            
        except Exception as e:
            self.logger.error(f"获取受信任设备失败: {e}", exc_info=True)
            return []

    def update_trust_status(self, user_id: str, device_id: str, trust_status) -> bool:
        """
        更新设备信任状态

        Args:
            user_id: 用户ID
            device_id: 设备ID
            trust_status: TrustStatus枚举值

        Returns:
            bool: 是否更新成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE device_trust
                SET trust_status = ?, updated_at = ?
                WHERE user_id = ? AND device_id = ?
            """, (trust_status.value, time.time(), user_id, device_id))
            
            conn.commit()
            self.logger.debug(f"更新设备信任状态: {user_id}/{device_id[:8]} -> {trust_status.value}")
            return cursor.rowcount > 0
            
        except Exception as e:
            self.logger.error(f"更新设备信任状态失败: {e}", exc_info=True)
            return False

    # ========== 第七阶段：消息安全与密钥状态 ==========

    def get_crypto_session(self, peer_user_id: str, peer_device_id: str, key_version: int = 1) -> Optional[Dict[str, Any]]:
        """读取直聊 session key 状态。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM crypto_sessions
                WHERE peer_user_id = ? AND peer_device_id = ? AND key_version = ?
            """, (peer_user_id, peer_device_id, int(key_version)))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            self.logger.error(f"读取 crypto session 失败: {e}", exc_info=True)
            return None

    def get_crypto_sessions_for_user(self, peer_user_id: str, status: str = "active") -> List[Dict[str, Any]]:
        """读取某个联系人所有可用于再次发送的直聊 session。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM crypto_sessions
                WHERE peer_user_id = ?
                  AND status = ?
                  AND peer_device_id != ''
                  AND peer_public_key != ''
                ORDER BY updated_at DESC, key_version DESC
            """, (peer_user_id, status))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"读取用户 crypto sessions 失败: {e}", exc_info=True)
            return []

    def save_crypto_session(
        self,
        peer_user_id: str,
        peer_device_id: str,
        key_version: int,
        key_id: str,
        peer_public_key: str,
        status: str = "active",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """保存或更新直聊 session key 状态。"""
        now = time.time()
        try:
            existing = self.get_crypto_session(peer_user_id, peer_device_id, key_version)
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO crypto_sessions
                (peer_user_id, peer_device_id, key_version, key_id, peer_public_key,
                 status, send_sequence, receive_sequence, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(peer_user_id, peer_device_id, key_version)
                DO UPDATE SET
                    key_id = excluded.key_id,
                    peer_public_key = excluded.peer_public_key,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    metadata = excluded.metadata
            """, (
                peer_user_id,
                peer_device_id,
                int(key_version),
                key_id,
                peer_public_key,
                status,
                int((existing or {}).get("send_sequence") or 0),
                int((existing or {}).get("receive_sequence") or 0),
                float((existing or {}).get("created_at") or now),
                now,
                json.dumps(metadata or {}, ensure_ascii=False),
            ))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"保存 crypto session 失败: {e}", exc_info=True)
            return False

    def next_crypto_send_sequence(self, peer_user_id: str, peer_device_id: str, key_version: int = 1) -> int:
        """递增并返回直聊发送 sequence。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT send_sequence FROM crypto_sessions
                WHERE peer_user_id = ? AND peer_device_id = ? AND key_version = ?
            """, (peer_user_id, peer_device_id, int(key_version)))
            row = cursor.fetchone()
            current = int(row["send_sequence"] or 0) if row else 0
            next_value = current + 1
            cursor.execute("""
                UPDATE crypto_sessions
                SET send_sequence = ?, updated_at = ?
                WHERE peer_user_id = ? AND peer_device_id = ? AND key_version = ?
            """, (next_value, time.time(), peer_user_id, peer_device_id, int(key_version)))
            conn.commit()
            return next_value
        except Exception as e:
            self.logger.error(f"递增 crypto sequence 失败: {e}", exc_info=True)
            return int(time.time() * 1000)

    def get_active_crypto_session_version(self, peer_user_id: str, peer_device_id: str) -> int:
        """返回指定对端设备的 active session version。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT key_version FROM crypto_sessions
                WHERE peer_user_id = ? AND peer_device_id = ? AND status = 'active'
                ORDER BY key_version DESC
                LIMIT 1
            """, (peer_user_id, peer_device_id))
            row = cursor.fetchone()
            return int(row["key_version"] or 1) if row else 1
        except Exception:
            return 1

    def rotate_crypto_session(self, peer_user_id: str, peer_device_id: str) -> int:
        """最小 key rotation：将旧 active 标记为 old，并返回下一个 version。"""
        current_version = self.get_active_crypto_session_version(peer_user_id, peer_device_id)
        next_version = current_version + 1
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE crypto_sessions
                SET status = 'old', updated_at = ?
                WHERE peer_user_id = ? AND peer_device_id = ? AND status = 'active'
            """, (time.time(), peer_user_id, peer_device_id))
            conn.commit()
        except Exception as e:
            self.logger.error(f"轮换 crypto session 失败: {e}", exc_info=True)
        return next_version

    def record_message_security_seen(
        self,
        scope: str,
        direction: str,
        message_id: str,
        sender_id: str,
        sender_device_id: str,
        key_id: str,
        sequence: int,
        nonce: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """记录 message/sequence/nonce，返回 (是否首次出现, 原因)。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO message_security_seen
                (scope, direction, message_id, sender_id, sender_device_id,
                 key_id, sequence, nonce, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                scope,
                direction,
                message_id,
                sender_id,
                sender_device_id,
                key_id,
                int(sequence),
                nonce,
                json.dumps(metadata or {}, ensure_ascii=False),
            ))
            conn.commit()
            return True, "recorded"
        except sqlite3.IntegrityError:
            return False, "duplicate_or_nonce_reuse"
        except Exception as e:
            self.logger.error(f"记录消息安全状态失败: {e}", exc_info=True)
            return False, str(e)

    def save_decryption_failure(
        self,
        message_id: str,
        conversation_id: str,
        sender_id: str,
        encryption_version: str,
        key_id: str,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """保存解密失败记录，避免 UI 暴露底层异常细节。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO decryption_failures
                (message_id, conversation_id, sender_id, encryption_version, key_id, reason, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                message_id,
                conversation_id,
                sender_id,
                encryption_version,
                key_id,
                reason,
                json.dumps(metadata or {}, ensure_ascii=False),
            ))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"保存解密失败记录失败: {e}", exc_info=True)
            return False

    def save_group_key(
        self,
        group_id: str,
        group_key_version: int,
        group_key_id: str,
        key_material: str,
        status: str = "active",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """保存群组 key。key_material 为 base64 编码。"""
        now = time.time()
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if status == "active":
                cursor.execute("""
                    UPDATE group_keys
                    SET status = 'old', rotated_at = ?
                    WHERE group_id = ? AND status = 'active'
                """, (now, group_id))
            cursor.execute("""
                INSERT OR REPLACE INTO group_keys
                (group_id, group_key_version, group_key_id, key_material, status,
                 created_at, rotated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                group_id,
                int(group_key_version),
                group_key_id,
                key_material,
                status,
                now,
                now if status != "active" else 0,
                json.dumps(metadata or {}, ensure_ascii=False),
            ))
            conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"保存群组 key 失败: {e}", exc_info=True)
            return False

    def get_group_key(self, group_id: str, group_key_version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """读取群组 key；未指定 version 时返回 active key。"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if group_key_version is None:
                cursor.execute("""
                    SELECT * FROM group_keys
                    WHERE group_id = ? AND status = 'active'
                    ORDER BY group_key_version DESC
                    LIMIT 1
                """, (group_id,))
            else:
                cursor.execute("""
                    SELECT * FROM group_keys
                    WHERE group_id = ? AND group_key_version = ?
                """, (group_id, int(group_key_version)))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            self.logger.error(f"读取群组 key 失败: {e}", exc_info=True)
            return None

    def delete_conversation(self, conversation_id: str) -> bool:
        """
        删除会话
        
        Args:
            conversation_id: 会话ID
            
        Returns:
            bool: 是否删除成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            
            # 同时删除该会话的所有消息（可选）
            cursor.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            
            conn.commit()
            self.logger.info(f"删除会话: {conversation_id[:8]}")
            return True
            
        except Exception as e:
            self.logger.error(f"删除会话失败: {e}", exc_info=True)
            return False

    # ========== 联系人管理方法 ==========
    
    def save_contact(self, contact: Contact) -> bool:
        """
        保存联系人到数据库
        
        Args:
            contact: 联系人对象
            
        Returns:
            bool: 是否保存成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO contacts
                (user_id, display_name, alias, auth_status, added_at, 
                 last_interaction, pending_message_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                contact.user_id,
                contact.display_name,
                contact.alias,
                contact.auth_status.value,
                contact.added_at,
                contact.last_interaction,
                contact.pending_message_count,
                json.dumps(contact.metadata) if contact.metadata else '{}'
            ))
            
            conn.commit()
            self.logger.debug(f"联系人保存成功: {contact.user_id[:8]}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存联系人失败: {e}", exc_info=True)
            return False
    
    def get_contact(self, user_id: str) -> Optional[Contact]:
        """
        获取联系人信息
        
        Args:
            user_id: 联系人用户ID
            
        Returns:
            Optional[Contact]: 联系人对象，不存在则返回None
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM contacts WHERE user_id = ?
            """, (user_id,))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            # 从行数据创建Contact对象
            metadata = json.loads(row['metadata']) if row['metadata'] else {}
            contact = Contact(
                user_id=row['user_id'],
                display_name=row['display_name'],
                alias=row['alias'],
                auth_status=normalize_contact_auth_status(row['auth_status']),
                added_at=row['added_at'],
                last_interaction=row['last_interaction'],
                pending_message_count=row['pending_message_count'],
                metadata=metadata
            )
            return contact
            
        except Exception as e:
            self.logger.error(f"获取联系人失败: {e}", exc_info=True)
            return None
    
    def get_all_contacts(self) -> Dict[str, Contact]:
        """
        获取所有联系人
        
        Returns:
            Dict[str, Contact]: 联系人字典（user_id -> Contact）
        """
        contacts = {}
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM contacts ORDER BY added_at DESC")
            
            for row in cursor.fetchall():
                metadata = json.loads(row['metadata']) if row['metadata'] else {}
                contact = Contact(
                    user_id=row['user_id'],
                    display_name=row['display_name'],
                    alias=row['alias'],
                    auth_status=normalize_contact_auth_status(row['auth_status']),
                    added_at=row['added_at'],
                    last_interaction=row['last_interaction'],
                    pending_message_count=row['pending_message_count'],
                    metadata=metadata
                )
                contacts[contact.user_id] = contact
            
            self.logger.debug(f"加载 {len(contacts)} 个联系人")
            
        except Exception as e:
            self.logger.error(f"获取所有联系人失败: {e}", exc_info=True)
        
        return contacts
    
    def update_contact_auth_status(self, user_id: str, auth_status: ContactAuthStatus) -> bool:
        """
        更新联系人授权状态
        
        Args:
            user_id: 联系人用户ID
            auth_status: 新的授权状态
            
        Returns:
            bool: 是否更新成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE contacts
                SET auth_status = ?, last_interaction = ?
                WHERE user_id = ?
            """, (normalize_contact_auth_status(auth_status).value, time.time(), user_id))
            
            conn.commit()
            updated = cursor.rowcount > 0
            if updated:
                self.logger.debug(f"更新联系人授权状态: {user_id[:8]} -> {auth_status.value}")
            return updated
            
        except Exception as e:
            self.logger.error(f"更新联系人授权状态失败: {e}", exc_info=True)
            return False
    
    def increment_contact_pending_count(self, user_id: str) -> bool:
        """
        增加联系人的待授权消息计数
        
        Args:
            user_id: 联系人用户ID
            
        Returns:
            bool: 是否更新成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE contacts
                SET pending_message_count = pending_message_count + 1, 
                    last_interaction = ?
                WHERE user_id = ?
            """, (time.time(), user_id))
            
            conn.commit()
            updated = cursor.rowcount > 0
            if updated:
                self.logger.debug(f"增加联系人待授权消息计数: {user_id[:8]}")
            return updated
            
        except Exception as e:
            self.logger.error(f"增加联系人待授权消息计数失败: {e}", exc_info=True)
            return False
    
    def clear_contact_pending_count(self, user_id: str) -> bool:
        """
        清空联系人的待授权消息计数
        
        Args:
            user_id: 联系人用户ID
            
        Returns:
            bool: 是否更新成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE contacts
                SET pending_message_count = 0, last_interaction = ?
                WHERE user_id = ?
            """, (time.time(), user_id))
            
            conn.commit()
            updated = cursor.rowcount > 0
            if updated:
                self.logger.debug(f"清空联系人待授权消息计数: {user_id[:8]}")
            return updated
            
        except Exception as e:
            self.logger.error(f"清空联系人待授权消息计数失败: {e}", exc_info=True)
            return False
    
    def delete_contact(self, user_id: str) -> bool:
        """
        删除联系人
        
        Args:
            user_id: 联系人用户ID
            
        Returns:
            bool: 是否删除成功
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM contacts WHERE user_id = ?", (user_id,))
            
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                self.logger.info(f"删除联系人: {user_id[:8]}")
            return deleted
            
        except Exception as e:
            self.logger.error(f"删除联系人失败: {e}", exc_info=True)
            return False

    def cleanup(self) -> None:
        """清理资源（关闭数据库连接）"""
        if hasattr(self._local, 'conn'):
            self._local.conn.close()
            delattr(self._local, 'conn')

    def close(self) -> None:
        """兼容旧测试/调用方的关闭方法。"""
        self.cleanup()

    def __del__(self):
        """析构函数，确保资源清理"""
        self.cleanup()


# 全局存储实例（按数据库路径隔离，避免多实例串库）
_global_stores: Dict[str, SQLiteStore] = {}
_global_store_lock = threading.RLock()


def get_global_store(db_path: str = "data/chat.db") -> SQLiteStore:
    """
    获取全局共享的存储实例（单例模式）

    Args:
        db_path: 数据库文件路径

    Returns:
        SQLiteStore: 全局共享的存储实例
    """
    global _global_stores

    with _global_store_lock:
        store_key = str(Path(db_path).expanduser().resolve())
        if store_key not in _global_stores:
            _global_stores[store_key] = SQLiteStore(store_key)
        return _global_stores[store_key]


def clear_global_store(db_path: Optional[str] = None) -> None:
    """清除全局存储实例（主要用于测试）"""
    global _global_stores

    with _global_store_lock:
        if db_path is not None:
            store_key = str(Path(db_path).expanduser().resolve())
            store = _global_stores.pop(store_key, None)
            if store:
                store.cleanup()
            return

        for store in _global_stores.values():
            store.cleanup()
        _global_stores.clear()
