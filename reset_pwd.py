from getpass import getpass
from auth.local_accounts import LocalAccountStore, _hash_password

store = LocalAccountStore(r"D:\chat_bot\chat_history.db")
store.initialize()

with store.connect() as connection:
    users = connection.execute(
        "SELECT username, display_name, is_admin, disabled "
        "FROM local_users ORDER BY username"
    ).fetchall()

print("本地账号：")
for username, display_name, is_admin, disabled in users:
    status = "已停用" if disabled else "正常"
    role = "管理员" if is_admin else "普通用户"
    print(f"- {username}（{display_name}，{role}，{status}）")

username = input("要重置密码的用户名：").strip().lower()
password = getpass("输入新密码（至少 8 位）：")
confirmation = getpass("再次输入新密码：")

if password != confirmation:
    raise SystemExit("两次输入不一致，未修改密码。")
if len(password) < 8:
    raise SystemExit("密码至少需要 8 个字符，未修改密码。")

with store.connect() as connection:
    result = connection.execute(
        "UPDATE local_users SET password_hash = ? WHERE username = ?",
        (_hash_password(password), username),
    )

if result.rowcount:
    print("密码已重置。")
else:
    print("没有找到该用户名，未修改密码。")