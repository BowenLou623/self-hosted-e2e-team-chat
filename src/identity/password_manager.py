"""
本地密码哈希管理。

M3 起统一使用 PBKDF2-HMAC-SHA256 + 随机 salt 保存本地密码。
同时保留对 M2 遗留 Argon2 / fallback 哈希的验证兼容，避免现有 identity 失效。
"""

import base64
import hashlib
import logging
import secrets
import string
from pathlib import Path
from typing import Optional, Tuple

try:
    from argon2 import PasswordHasher, exceptions

    ARGON2_AVAILABLE = True
except ImportError:
    ARGON2_AVAILABLE = False
    logging.warning("argon2-cffi not available, legacy argon2 hashes cannot be verified")

from src.utils.logger import get_logger


class PasswordManager:
    """负责密码生成、哈希与验证。"""

    PBKDF2_PREFIX = "pbkdf2_sha256"
    PBKDF2_ALGORITHM = "sha256"
    PBKDF2_ITERATIONS = 310_000
    SALT_BYTES = 16
    HASH_BYTES = 32

    def __init__(self, config_dir: str = "data/config"):
        self.logger = get_logger("password_manager")
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.legacy_argon2_hasher: Optional[PasswordHasher] = None
        if ARGON2_AVAILABLE:
            self.legacy_argon2_hasher = PasswordHasher(
                time_cost=3,
                memory_cost=65536,
                parallelism=4,
                hash_len=32,
                salt_len=16,
            )

        self.logger.info("Password manager using PBKDF2-HMAC-SHA256")

    def generate_password(self, length: int = 16) -> str:
        """生成随机密码（仅保留给兼容代码/测试使用）。"""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def hash_password(self, password: str) -> Tuple[str, str]:
        """
        生成密码哈希与 salt。

        Returns:
            (password_hash, password_salt)
        """
        if not password:
            raise ValueError("Password cannot be empty")

        salt = secrets.token_bytes(self.SALT_BYTES)
        derived_key = hashlib.pbkdf2_hmac(
            self.PBKDF2_ALGORITHM,
            password.encode("utf-8"),
            salt,
            self.PBKDF2_ITERATIONS,
            dklen=self.HASH_BYTES,
        )

        password_hash = (
            f"{self.PBKDF2_PREFIX}${self.PBKDF2_ITERATIONS}$"
            f"{base64.b64encode(derived_key).decode('ascii')}"
        )
        password_salt = base64.b64encode(salt).decode("ascii")
        return password_hash, password_salt

    def verify_password(
        self,
        password: str,
        hashed_password: str,
        password_salt: Optional[str] = None,
    ) -> bool:
        """验证密码。"""
        if not password or not hashed_password:
            return False

        if hashed_password.startswith(f"{self.PBKDF2_PREFIX}$"):
            return self._verify_pbkdf2_password(password, hashed_password, password_salt)

        return self._verify_legacy_password(password, hashed_password)

    def needs_rehash(
        self,
        hashed_password: str,
        password_salt: Optional[str] = None,
    ) -> bool:
        """判断密码记录是否需要迁移/重哈希。"""
        if not hashed_password:
            return True

        if hashed_password.startswith(f"{self.PBKDF2_PREFIX}$"):
            if not password_salt:
                return True

            parts = hashed_password.split("$", 2)
            if len(parts) != 3:
                return True

            try:
                iterations = int(parts[1])
            except ValueError:
                return True

            return iterations != self.PBKDF2_ITERATIONS

        return True

    def generate_and_hash(self) -> Tuple[str, str, str]:
        """生成随机密码，并返回明文、哈希与 salt。"""
        password = self.generate_password()
        hashed_password, password_salt = self.hash_password(password)
        return password, hashed_password, password_salt

    def _verify_pbkdf2_password(
        self,
        password: str,
        hashed_password: str,
        password_salt: Optional[str],
    ) -> bool:
        if not password_salt:
            self.logger.error("PBKDF2 password record missing salt")
            return False

        parts = hashed_password.split("$", 2)
        if len(parts) != 3:
            self.logger.error("Invalid PBKDF2 password hash format")
            return False

        try:
            iterations = int(parts[1])
            expected_hash = base64.b64decode(parts[2])
            salt = base64.b64decode(password_salt)
        except (ValueError, TypeError) as exc:
            self.logger.error(f"Invalid PBKDF2 password record: {exc}")
            return False

        actual_hash = hashlib.pbkdf2_hmac(
            self.PBKDF2_ALGORITHM,
            password.encode("utf-8"),
            salt,
            iterations,
            dklen=len(expected_hash),
        )
        return secrets.compare_digest(expected_hash, actual_hash)

    def _verify_legacy_password(self, password: str, hashed_password: str) -> bool:
        """兼容验证旧 M2 哈希。"""
        if hashed_password.startswith("$argon2"):
            if not self.legacy_argon2_hasher:
                self.logger.error("Argon2 hash provided but argon2-cffi is unavailable")
                return False
            try:
                return self.legacy_argon2_hasher.verify(hashed_password, password)
            except exceptions.VerifyMismatchError:
                return False
            except exceptions.VerificationError as exc:
                self.logger.error(f"Legacy Argon2 verification error: {exc}")
                return False

        if hashed_password.startswith("$fallback$"):
            try:
                parts = hashed_password.split("$")
                if len(parts) != 4:
                    self.logger.error("Invalid legacy fallback hash format")
                    return False
                salt = base64.b64decode(parts[2])
                expected_hash = base64.b64decode(parts[3])
            except Exception as exc:
                self.logger.error(f"Legacy fallback hash parse error: {exc}")
                return False

            actual_hash = hashlib.sha256(salt + password.encode("utf-8")).digest()
            return secrets.compare_digest(expected_hash, actual_hash)

        self.logger.error("Unknown password hash format")
        return False


_global_password_manager: Optional[PasswordManager] = None


def get_global_password_manager(config_dir: str = "data/config") -> PasswordManager:
    """获取全局密码管理器实例。"""
    global _global_password_manager
    if _global_password_manager is None:
        _global_password_manager = PasswordManager(config_dir)
    return _global_password_manager
