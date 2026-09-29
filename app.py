import os
import uuid

import streamlit as st

import chat_bot
from auth.identity import logout_current_user, require_login
from auth.local_accounts import LocalAccountStore
from chat_bot import SYSTEM_PROMPT, build_agent_messages, clear_history, latest_session_id, load_history, request_model, save_auto_summary_memory, save_message
from knowledge.inputs import build_attachment_context, extract_uploaded_file
from providers.model_client import transcribe_audio


st.set_page_config(page_title="我的智能体", page_icon="✦", layout="centered")

current_user = require_login(os.path.join(os.path.dirname(__file__), "chat_history.db"))

st.title("专属您的超级参谋")

if "messages" not in st.session_state:
	st.session_state.messages = []
if "session_id" not in st.session_state:
	persisted_session_id = st.query_params.get("session_id")
	if not persisted_session_id:
		persisted_session_id = latest_session_id(user_id=current_user.user_id) or uuid.uuid4().hex
		st.query_params["session_id"] = persisted_session_id
	st.session_state.session_id = persisted_session_id
if not st.session_state.messages:
	st.session_state.messages = load_history(
		session_id=st.session_state.session_id, user_id=current_user.user_id
	)

with st.sidebar:
	st.subheader("会话")
	if st.button("清空当前对话", use_container_width=True):
		clear_history(
			session_id=st.session_state.session_id, user_id=current_user.user_id
		)
		st.session_state.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
		st.rerun()
	if st.button("退出登录"):
		logout_current_user()
	if current_user.is_admin:
		with st.expander("管理员：创建内网用户"):
			with st.form("create_local_user"):
				new_username = st.text_input("新用户名")
				new_display_name = st.text_input("新用户显示名称")
				new_password = st.text_input("新用户初始密码", type="password")
				create_user = st.form_submit_button("创建用户")
			if create_user:
				try:
					LocalAccountStore(chat_bot.DATABASE_PATH).create_user(
						new_username, new_password, new_display_name
					)
					st.success("用户已创建，可以使用新账号登录。")
				except ValueError as error:
					st.error(str(error))

	st.divider()
	st.caption("首次使用")
	st.write("对话会自动保存在本机，刷新页面后仍可继续。")

for message in st.session_state.messages:
	if message["role"] != "system":
		with st.chat_message(message["role"]):
			st.markdown(message["content"])

submission = st.chat_input(
	"输入你想咨询的内容，或点击附件按钮上传资料...",
	accept_file="multiple",
	accept_audio=True,
	file_type=["txt", "md", "markdown", "csv", "json", "log", "pdf", "docx", "xlsx", "xlsm", "png", "jpg", "jpeg", "webp", "gif"],
)

if submission:
	prompt = (submission.text or "").strip()
	uploaded_files = submission.files
	if submission.audio:
		try:
			with st.spinner("正在识别语音..."):
				transcribed_text = transcribe_audio(
					submission.audio.getvalue(),
					submission.audio.name or "audio.wav",
				)
		except RuntimeError as error:
			st.error(f"语音处理失败：{error}")
			st.stop()
		prompt = f"{prompt}\n\n语音补充：{transcribed_text}" if prompt else transcribed_text
	if not prompt and uploaded_files:
		prompt = "请分析我上传的资料。"
	try:
		attachments = [extract_uploaded_file(uploaded_file) for uploaded_file in uploaded_files]
	except (ValueError, ImportError) as error:
		st.error(f"附件处理失败：{error}")
		st.stop()
	attachment_context = build_attachment_context(attachments, prompt)
	st.session_state.messages.append({"role": "user", "content": prompt})
	save_message(
		"user",
		prompt,
		session_id=st.session_state.session_id,
		user_id=current_user.user_id,
	)
	save_auto_summary_memory(
		session_id=st.session_state.session_id, user_id=current_user.user_id
	)
	with st.chat_message("user"):
		st.markdown(prompt)

	try:
		with st.chat_message("assistant"):
			with st.spinner("正在思考..."):
				request_messages = list(st.session_state.messages)
				request_messages[-1] = {
					"role": "user",
					"content": attachment_context,
				}
				answer = request_model(
					build_agent_messages(
						request_messages,
						session_id=st.session_state.session_id,
						user_id=current_user.user_id,
					)
				)
			st.markdown(answer)
		st.session_state.messages.append({"role": "assistant", "content": answer})
		save_message(
			"assistant",
			answer,
			session_id=st.session_state.session_id,
			user_id=current_user.user_id,
		)
		save_auto_summary_memory(
			session_id=st.session_state.session_id, user_id=current_user.user_id
		)
	except RuntimeError as error:
		st.session_state.messages.pop()
		clear_history(
			session_id=st.session_state.session_id, user_id=current_user.user_id
		)
		for message in st.session_state.messages[1:]:
			save_message(
				message["role"],
				message["content"],
				session_id=st.session_state.session_id,
				user_id=current_user.user_id,
			)
		st.error(str(error))