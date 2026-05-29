"""
群组数据模型。

第三阶段只实现基础群聊：群本身、成员关系和少量扩展元数据。
复杂权限、管理员体系和群端到端加密协议都留给后续阶段。
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class GroupMemberStatus(Enum):
    """群成员状态。"""

    ACTIVE = "active"
    INVITED = "invited"
    REMOVED = "removed"


@dataclass
class Group:
    """基础群组模型。"""

    id: str = field(default_factory=lambda: f"grp_{uuid.uuid4().hex}")
    name: str = ""
    creator_id: str = ""
    avatar_url: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "id": self.id,
            "name": self.name,
            "creator_id": self.creator_id,
            "avatar_url": self.avatar_url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Group":
        """从字典恢复群组模型。"""
        return cls(
            id=data.get("id") or f"grp_{uuid.uuid4().hex}",
            name=data.get("name", ""),
            creator_id=data.get("creator_id", ""),
            avatar_url=data.get("avatar_url"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            metadata=data.get("metadata", {}) or {},
        )


@dataclass
class GroupMember:
    """群成员关系模型。"""

    group_id: str
    user_id: str
    display_name: str = ""
    status: GroupMemberStatus = GroupMemberStatus.ACTIVE
    joined_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "group_id": self.group_id,
            "user_id": self.user_id,
            "display_name": self.display_name,
            "status": self.status.value,
            "joined_at": self.joined_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GroupMember":
        """从字典恢复群成员关系。"""
        raw_status = data.get("status", GroupMemberStatus.ACTIVE.value)
        try:
            status = GroupMemberStatus(raw_status)
        except ValueError:
            status = GroupMemberStatus.ACTIVE

        return cls(
            group_id=data.get("group_id", ""),
            user_id=data.get("user_id", ""),
            display_name=data.get("display_name", ""),
            status=status,
            joined_at=data.get("joined_at", time.time()),
            metadata=data.get("metadata", {}) or {},
        )
