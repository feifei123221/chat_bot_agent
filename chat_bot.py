import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent.diagnosis import format_diagnosis_context
from agent.prompts import SYSTEM_PROMPT
from knowledge.indexer import KnowledgeIndexer
from knowledge.wiki import KnowledgeResult, format_context, retrieve
from providers.model_client import load_local_env, request_model as provider_request_model
from storage.sqlite_store import SQLiteStore

load_local_env()
DATABASE_PATH = os.path.join(os.path.dirname(__file__), "chat_history.db")
KNOWLEDGE_PATH = os.getenv(
	"KNOWLEDGE_PATH",
	os.path.join(os.path.dirname(__file__), "wiki"),
)
KNOWLEDGE_INDEX_PATH = os.getenv(
	"KNOWLEDGE_INDEX_PATH",
	os.path.join(os.path.dirname(__file__), "knowledge_index.db"),
)
LEGACY_USER_ID = "legacy-local"


def _store() -> SQLiteStore:
	return SQLiteStore(DATABASE_PATH)


def initialize_history() -> None:
	_store().initialize()


def load_history(
	session_id: str = "default", user_id: str = LEGACY_USER_ID
) -> list[dict[str, str]]:
	return [{"role": "system", "content": SYSTEM_PROMPT}] + _store().load_history(
		session_id, user_id=user_id
	)


def latest_session_id(user_id: str = LEGACY_USER_ID) -> str | None:
	return _store().latest_session_id(user_id=user_id)


def save_message(
	role: str,
	content: str,
	session_id: str = "default",
	user_id: str = LEGACY_USER_ID,
) -> None:
	_store().save_message(role, content, session_id, user_id=user_id)


def save_memory(
	key: str,
	value: str,
	session_id: str = "default",
	user_id: str = LEGACY_USER_ID,
) -> None:
	"""Persist a structured memory item for long-term context."""
	_store().save_memory(key, value, session_id, user_id=user_id)


def load_memory(
	session_id: str = "default", user_id: str = LEGACY_USER_ID
) -> dict[str, str]:
	"""Load structured memory entries keyed by name."""
	return _store().load_memory(session_id, user_id=user_id)


def clear_history(
	session_id: str | None = None, user_id: str | None = LEGACY_USER_ID
) -> None:
	_store().clear(session_id, user_id=user_id)


def format_memory_context(
	session_id: str = "default", user_id: str = LEGACY_USER_ID
) -> str:
	memory = load_memory(session_id=session_id, user_id=user_id)
	if not memory:
		return "长期记忆：目前暂无显式记忆。"
	lines = ["长期记忆："]
	for key, value in memory.items():
		lines.append(f"- {key}: {value}")
	return "\n".join(lines)


def summarize_recent_conversation(
	limit: int = 4, session_id: str = "default", user_id: str = LEGACY_USER_ID
) -> str:
	"""Build a concise summary from the most recent user-assistant exchanges."""
	rows = _store().recent_messages(session_id, limit * 2, user_id=user_id)
	rows = list(reversed(rows))
	if not rows:
		return "最近对话暂无有效内容。"

	summary_parts: list[str] = []
	for role, content in rows:
		if role == "user":
			summary_parts.append(f"用户需求：{content}")
		else:
			summary_parts.append(f"已讨论：{content}")
	return " | ".join(summary_parts)


def save_auto_summary_memory(
	session_id: str = "default", user_id: str = LEGACY_USER_ID
) -> None:
	"""Save a concise conversation summary as long-term memory."""
	summary = summarize_recent_conversation(
		limit=4, session_id=session_id, user_id=user_id
	)
	if summary and "暂无有效内容" not in summary:
		save_memory(
			"conversation_summary", summary, session_id=session_id, user_id=user_id
		)


def update_task_memory(
	task: str,
	summary: str,
	session_id: str = "default",
	user_id: str = LEGACY_USER_ID,
) -> None:
	"""Persist a concise task state so the agent can continue a long-running job."""
	save_memory(
		"task_state",
		f"任务状态：{task} | 结论：{summary}",
		session_id=session_id,
		user_id=user_id,
	)


def update_task_state_from_recent_history(
	limit: int = 4,
	session_id: str = "default",
	user_id: str = LEGACY_USER_ID,
) -> dict[str, str]:
	"""Infer the current task and next action from the most recent conversation."""
	rows = _store().recent_messages(session_id, limit * 2, user_id=user_id)
	rows = list(reversed(rows))
	last_user_content = ""
	last_assistant_content = ""
	for role, content in rows:
		if role == "user" and not last_user_content:
			last_user_content = content
		if role == "assistant" and not last_assistant_content:
			last_assistant_content = content

	task = last_user_content or "当前任务尚未明确"
	next_action = last_assistant_content or "继续梳理目标、约束和关键指标"
	if "用户画像" in next_action:
		next_action = "下一步：先定义用户画像和渠道优先级"
	elif "指标" in next_action:
		next_action = "下一步：设计 KPI 和衡量指标"
	elif "优先级" in next_action:
		next_action = "下一步：梳理优先级和执行路径"
	else:
		next_action = "下一步：继续梳理目标、约束和风险"

	state = {"task": task, "next_action": next_action}
	save_memory(
		"task_state",
		f"任务状态：{task} | 下一步：{next_action}",
		session_id=session_id,
		user_id=user_id,
	)
	return state


def retrieve_knowledge(query: str, limit: int = 3) -> list[KnowledgeResult]:
	"""Compatibility wrapper for the configured external Wiki source."""
	return KnowledgeIndexer(KNOWLEDGE_INDEX_PATH).retrieve(
		query, KNOWLEDGE_PATH, limit=limit
	)


def format_knowledge_context(query: str, limit: int = 3) -> str:
	"""Format Wiki results without coupling the agent to Wiki storage."""
	results = retrieve_knowledge(query, limit=limit)
	if not results:
		return "知识库参考：当前没有检索到相关内容。"
	lines = ["知识库参考："]
	for result in results:
		lines.append(f"- 来源: {result['source']}\n  内容: {result['content']}")
	return "\n".join(lines)


def build_agent_messages(
	messages: list[dict[str, str]],
	session_id: str = "default",
	user_id: str = LEGACY_USER_ID,
) -> list[dict[str, str]]:
	"""Add long-term memory context while preserving the current chat history."""
	memory_context = format_memory_context(session_id=session_id, user_id=user_id)
	latest_user_message = next(
		(
			message["content"]
			for message in reversed(messages)
			if message.get("role") == "user"
		),
		"",
	)
	if not isinstance(latest_user_message, str):
		latest_user_message = " ".join(
			part.get("text", "")
			for part in latest_user_message
			if isinstance(part, dict) and part.get("type") == "text"
		)
	knowledge_context = format_knowledge_context(latest_user_message)
	diagnosis_context = format_diagnosis_context(latest_user_message)
	assistant_prompt = (
		f"{SYSTEM_PROMPT}\n\n"
		f"{memory_context}\n\n"
		f"{knowledge_context}\n\n"
		f"{diagnosis_context}\n\n"
		"请在回答中优先参考这些长期记忆，并在缺少事实时明确说明假设与待验证信息。"
		"引用知识库时保留来源名称；知识库没有依据时，不要声称已有依据。"
	)
	final_messages: list[dict[str, str]] = [{"role": "system", "content": assistant_prompt}]
	for message in messages:
		if message.get("role") == "system":
			continue
		final_messages.append({"role": message["role"], "content": message["content"]})
	return final_messages


def request_model(messages: list[dict[str, str]]) -> str:
	"""Compatibility wrapper over the provider layer."""
	return provider_request_model(messages)


def run_chat() -> None:
	messages = [{"role": "system", "content": SYSTEM_PROMPT}]
	print("专属智能体已启动。输入 /reset 清空对话，输入 /exit 退出。")

	while True:
		try:
			user_input = input("\n你：").strip()
		except (EOFError, KeyboardInterrupt):
			print("\n智能体：再见。")
			return

		if not user_input:
			continue
		if user_input.lower() in {"/exit", "/quit"}:
			print("智能体：再见。")
			return
		if user_input.lower() == "/reset":
			clear_history()
			messages = [{"role": "system", "content": SYSTEM_PROMPT}]
			print("智能体：对话记忆已清空。")
			continue

		messages.append({"role": "user", "content": user_input})
		save_message("user", user_input)
		save_auto_summary_memory()
		try:
			answer = request_model(build_agent_messages(messages))
		except RuntimeError as error:
			messages.pop()
			clear_history()
			for message in messages[1:]:
				save_message(message["role"], message["content"])
			print(f"智能体：{error}", file=sys.stderr)
			continue

		messages.append({"role": "assistant", "content": answer})
		save_message("assistant", answer)
		save_auto_summary_memory()
		print(f"智能体：{answer}")


if __name__ == "__main__":
	run_chat()
