from dataclasses import dataclass
from typing import Any

import streamlit as st

from .local_accounts import LocalAccountStore, LocalUser


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str
    name: str
    is_admin: bool = False


def _auth_configured() -> bool:
    try:
        auth_config = st.secrets.get("auth")
    except FileNotFoundError:
        return False
    if not auth_config:
        return False
    required_keys = {"redirect_uri", "cookie_secret"}
    if not required_keys.issubset(auth_config):
        return False
    provider_config = auth_config
    if not {"client_id", "client_secret", "server_metadata_url"}.issubset(provider_config):
        provider_names = [key for key in auth_config if isinstance(auth_config[key], dict)]
        if not provider_names:
            return False
        provider_config = auth_config[provider_names[0]]
    return {"client_id", "client_secret", "server_metadata_url"}.issubset(provider_config)


def _claim(user: Any, name: str, default: str = "") -> str:
    value = getattr(user, name, default)
    return str(value or default).strip()


def get_current_user() -> AuthenticatedUser | None:
    if not _auth_configured() or not st.user.is_logged_in:
        return None

    subject = _claim(st.user, "sub")
    email = _claim(st.user, "email")
    name = _claim(st.user, "name") or email
    user_id = subject or email
    if not user_id:
        raise RuntimeError("身份提供商未返回稳定的用户标识，无法建立用户数据隔离。")
    return AuthenticatedUser(user_id=user_id, email=email, name=name)


def require_login(database_path: str) -> AuthenticatedUser:
    """Require a local intranet account, with optional OIDC compatibility."""
    if _auth_configured():
        return _require_oidc_login()

    account_store = LocalAccountStore(database_path)
    if "local_user" in st.session_state:
        local_user = st.session_state.local_user
        return _as_authenticated_user(local_user)

    st.title("登录智能体")
    if not account_store.has_users():
        st.info("这是首次使用，请先创建管理员账号。后续用户由管理员创建。")
        with st.form("create_first_account"):
            username = st.text_input("用户名")
            display_name = st.text_input("显示名称")
            password = st.text_input("密码", type="password")
            confirm_password = st.text_input("确认密码", type="password")
            submitted = st.form_submit_button("创建管理员账号", type="primary")
        if submitted:
            if password != confirm_password:
                st.error("两次输入的密码不一致。")
            else:
                try:
                    st.session_state.local_user = account_store.create_user(
                        username, password, display_name, is_admin=True
                    )
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))
        st.stop()

    st.write("登录后，你的对话、记忆和任务数据只会属于你的账户。")
    with st.form("local_login"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", type="primary")
    if submitted:
        local_user = account_store.authenticate(username, password)
        if local_user is None:
            st.error("用户名或密码错误。")
        else:
            st.session_state.local_user = local_user
            st.rerun()
    st.stop()


def _require_oidc_login() -> AuthenticatedUser:
    if not st.user.is_logged_in:
        st.title("登录智能体")
        st.write("登录后，你的对话、记忆和任务数据只会属于你的账户。")
        if st.button("使用身份提供商登录", type="primary"):
            st.login()
        st.stop()

    user = get_current_user()
    if user is None:
        st.error("无法读取当前登录身份，请重新登录。")
        st.stop()
    return user


def _as_authenticated_user(user: LocalUser) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user.user_id,
        email=f"{user.username}@local",
        name=user.display_name,
        is_admin=user.is_admin,
    )


def logout_current_user() -> None:
    if "local_user" in st.session_state:
        del st.session_state.local_user
        st.rerun()
    st.logout()
