"""
密码工具 — bcrypt 加密与校验

使用 bcrypt 算法，自动加盐，安全强度高。
数据库中存储的密码格式：$2b$12$... （60 字符）

使用方式：
    hashed = hash_password("123456")               # → "$2b$12$..."
    ok = verify_password("123456", hashed)          # → True
    ok = verify_password("wrong", hashed)           # → False
"""

import bcrypt


def hash_password(password: str) -> str:
    """
    加密密码

    :param password: 明文密码
    :return: bcrypt 哈希字符串
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """
    校验密码

    :param password: 用户输入的明文密码
    :param hashed:   数据库中存储的 bcrypt 哈希
    :return: 密码正确返回 True
    """
    return bcrypt.checkpw(password.encode(), hashed.encode())
