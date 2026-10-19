import bcrypt

# bcrypt 的输入上限是 72 字节（不是字符）：超长必须提前拒绝，
# 否则 bcrypt 5.x 会直接抛 ValueError 变成 500，旧版本则会静默截断。
BCRYPT_MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    """返回可直接入库的 bcrypt 哈希串（含随机 salt）。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
