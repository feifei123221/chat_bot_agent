import base64
import hashlib
import hmac
import os
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalUser:
    user_id: str
    username: str
    display_name: str
    is_admin: bool = False


class LocalAccountStore:
    def __init__(self, database_path: str):
        self.database_path = database_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS local_users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    disabled INTEGER NOT NULL DEFAULT 0
                )"""
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(local_users)")}
            if "is_admin" not in columns:
                connection.execute(
                    "ALTER TABLE local_users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
                )

    def has_users(self) -> bool:
        self.initialize()
        with self.connect() as connection:
            return connection.execute("SELECT 1 FROM local_users LIMIT 1").fetchone() is not None

    def create_user(
        self, username: str, password: str, display_name: str, is_admin: bool = False
    ) -> LocalUser:
        username = username.strip().lower()
        display_name = display_name.strip() or username
        if len(username) < 3:
            raise ValueError("用户名至少需要 3 个字符。")
        if len(password) < 8:
            raise ValueError("密码至少需要 8 个字符。")
        if not display_name:
            raise ValueError("显示名称不能为空。")

        self.initialize()
        user_id = os.urandom(16).hex()
        password_hash = _hash_password(password)
        try:
            with self.connect() as connection:
                connection.execute(
                    "INSERT INTO local_users (user_id, username, display_name, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
                    (user_id, username, display_name, password_hash, int(is_admin)),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("用户名已存在。") from error
        return LocalUser(
            user_id=user_id,
            username=username,
            display_name=display_name,
            is_admin=is_admin,
        )

    def authenticate(self, username: str, password: str) -> LocalUser | None:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT user_id, username, display_name, password_hash, is_admin FROM local_users WHERE username = ? AND disabled = 0",
                (username.strip().lower(),),
            ).fetchone()
        if not row or not _verify_password(password, row[3]):
            return None
        return LocalUser(
            user_id=row[0], username=row[1], display_name=row[2], is_admin=bool(row[4])
        )


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return "scrypt$16384$8$1$" + _encode(salt) + "$" + _encode(digest)


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_decode(salt_text),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(digest, _decode(digest_text))
    except (ValueError, TypeError):
        return False


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))
