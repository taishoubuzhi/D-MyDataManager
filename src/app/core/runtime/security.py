"""口令散列工具：用户口令（进入口令/隐藏数据解锁）的散列与校验。

首选 **argon2id**（`argon2-cffi`，缺依赖时自动退回 PBKDF2，程序照常启动）。
老库里的 PBKDF2 散列仍然能校验：`verify_hash()` 按散列前缀分派，
`needs_rehash()` 告诉调用方这份散列该不该升到当前算法——调用方在**校验成功之后**
顺手升级（惰性 rehash），不必一次性改动全库。

PBKDF2 兜底路径的算法 / 迭代次数 / 盐长读同目录的 `runtime.json`
（`password_hash_algorithm` / `password_hash_iterations` / `password_salt_bytes`）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

from loguru import logger

from .module_data import load_module_data

try:  # 首选 argon2id；缺依赖时退回 PBKDF2（批 K 会把它写进 requirements-optional）
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
except ImportError:  # pragma: no cover - 取决于运行环境
    PasswordHasher = None  # type: ignore[assignment]
    InvalidHashError = VerificationError = VerifyMismatchError = Exception  # type: ignore[assignment,misc]

_DATA = load_module_data(__file__, "runtime")

_HASH_ALGORITHM = _DATA.value("password_hash_algorithm")
_SALT_BYTES = int(_DATA.value("password_salt_bytes"))

#: 兜底（PBKDF2）的迭代次数，测试与迁移脚本按它判断老散列
ITERATIONS = int(_DATA.value("password_hash_iterations"))

#: argon2id 散列的前缀（`$argon2id$v=19$...`）；PBKDF2 散列是 JSON 文本，不会以此开头
ARGON2_PREFIX = "$argon2"

#: 用 argon2-cffi 的推荐默认参数（m=64 MiB, t=3, p=4）
_hasher = PasswordHasher() if PasswordHasher is not None else None


def argon2_available() -> bool:
    """当前环境有没有 argon2（缺依赖时走 PBKDF2 兜底）。"""
    return _hasher is not None


def hash_password(password: str) -> str:
    """把口令散列成可存储的字符串：有 argon2id 用它，否则退到 PBKDF2-HMAC。"""
    if _hasher is not None:
        return _hasher.hash(password or "")
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_HASH_ALGORITHM, (password or "").encode("utf-8"), salt, ITERATIONS)
    return json.dumps(
        {"iter": ITERATIONS, "salt": salt.hex(), "hash": digest.hex()}, ensure_ascii=False
    )


def verify_hash(stored: str, password: str) -> bool:
    """校验口令散列；无散列时视为不校验（该用户没有口令）。"""
    if not stored:
        return True
    if stored.startswith(ARGON2_PREFIX):
        if _hasher is None:
            logger.error("这份口令用的是 argon2id，但当前环境没装 argon2-cffi，无法校验")
            return False
        try:
            _hasher.verify(stored, password or "")
            return True
        except VerifyMismatchError:
            return False
        except (VerificationError, InvalidHashError) as exc:
            logger.error("口令数据损坏：{}", exc)
            return False
    return _verify_pbkdf2(stored, password)


def needs_rehash(stored: str) -> bool:
    """这份散列该不该升级成当前算法（老 PBKDF2 要升级，argon2 参数过时也要）。

    没有 argon2 时不升级：否则会把 argon2 散列降级成 PBKDF2，反而更弱。
    既不是 argon2、又不是老 PBKDF2 形状的（损坏数据）也不升级——升级的语义是
    「这份散列还能用，只是算法旧了」，损坏数据应该走校验失败那条路。
    """
    if not stored or _hasher is None:
        return False
    if not stored.startswith(ARGON2_PREFIX):
        return _is_pbkdf2_payload(stored)
    try:
        return bool(_hasher.check_needs_rehash(stored))
    except (InvalidHashError, VerificationError) as exc:
        logger.error("口令数据损坏：{}", exc)
        return False


def _is_pbkdf2_payload(stored: str) -> bool:
    """老散列的形状：`{"iter", "salt", "hash"}` 的 JSON 文本。"""
    try:
        payload = json.loads(stored)
    except (ValueError, TypeError):
        return False
    return isinstance(payload, dict) and {"iter", "salt", "hash"} <= set(payload)


def _verify_pbkdf2(stored: str, password: str) -> bool:
    """PBKDF2 兜底校验：老散列是 `{"iter", "salt", "hash"}` 的 JSON 文本。"""
    try:
        payload = json.loads(stored)
        digest = hashlib.pbkdf2_hmac(
            _HASH_ALGORITHM,
            (password or "").encode("utf-8"),
            bytes.fromhex(payload["salt"]),
            int(payload["iter"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        logger.error("口令数据损坏：{}", exc)
        return False
    return hmac.compare_digest(digest.hex(), payload["hash"])


__all__ = [
    "ARGON2_PREFIX",
    "ITERATIONS",
    "argon2_available",
    "hash_password",
    "needs_rehash",
    "verify_hash",
]
