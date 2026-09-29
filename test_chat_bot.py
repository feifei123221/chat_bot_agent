import os
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch

import chat_bot
from providers.model_client import request_model as provider_request_model
from providers.model_client import transcribe_audio
from auth.local_accounts import LocalAccountStore
from agent.diagnosis import diagnose_request
from knowledge.inputs import build_attachment_context, extract_uploaded_file
from knowledge.indexer import KnowledgeIndexer


class ChatBotMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="chatbot_test_")
        chat_bot.DATABASE_PATH = os.path.join(self.temp_dir, "chat_history.db")
        chat_bot.KNOWLEDGE_PATH = os.path.join(self.temp_dir, "wiki")
        chat_bot.KNOWLEDGE_INDEX_PATH = os.path.join(self.temp_dir, "knowledge_index.db")
        os.makedirs(chat_bot.KNOWLEDGE_PATH)
        chat_bot.clear_history()

    def tearDown(self):
        try:
            chat_bot.clear_history()
        finally:
            try:
                if os.path.exists(chat_bot.DATABASE_PATH):
                    os.remove(chat_bot.DATABASE_PATH)
            except PermissionError:
                pass

    def test_memory_can_be_saved_and_loaded(self):
        chat_bot.initialize_history()
        chat_bot.save_memory("user_profile", "用户偏好：重实操方案")
        memory = chat_bot.load_memory()
        self.assertIn("user_profile", memory)
        self.assertIn("重实操方案", memory["user_profile"])

    def test_memory_is_injected_into_agent_context(self):
        chat_bot.initialize_history()
        chat_bot.save_memory("project_goal", "目标：为新业务规划增长方案")
        agent_messages = chat_bot.build_agent_messages([
            {"role": "user", "content": "帮我做一个商业方案"},
        ])
        self.assertTrue(
            any(
                message["role"] == "system"
                and "长期记忆" in message["content"]
                and "目标：为新业务规划增长方案" in message["content"]
                for message in agent_messages
            )
        )

    def test_task_state_memory_is_generated(self):
        chat_bot.initialize_history()
        chat_bot.update_task_memory("帮我做品牌增长方案", "建议从用户分层和渠道结构入手")
        memory = chat_bot.load_memory()
        self.assertTrue(any("增长方案" in value for value in memory.values()))
        self.assertTrue(any("任务状态" in value for value in memory.values()))

    def test_workflow_and_output_format_are_in_system_prompt(self):
        prompt = chat_bot.SYSTEM_PROMPT
        self.assertIn("工作流程", prompt)
        self.assertIn("输出格式", prompt)
        self.assertIn("结论", prompt)

    def test_auto_summary_memory_is_generated_from_recent_history(self):
        chat_bot.initialize_history()
        chat_bot.save_message("user", "我想做一个品牌增长方案")
        chat_bot.save_message("assistant", "我们先定义用户画像、渠道结构和关键指标，然后做优先级排序。")
        summary = chat_bot.summarize_recent_conversation(limit=2)
        self.assertIn("品牌增长方案", summary)
        self.assertIn("用户画像", summary)
        chat_bot.save_auto_summary_memory()
        memory = chat_bot.load_memory()
        self.assertTrue(any("品牌增长方案" in value for value in memory.values()))

    def test_task_state_can_be_inferred_from_recent_messages(self):
        chat_bot.initialize_history()
        chat_bot.save_message("user", "我想做一个品牌增长方案")
        chat_bot.save_message("assistant", "先定义用户画像和渠道优先级，然后做 KPI 设计。")
        task_state = chat_bot.update_task_state_from_recent_history()
        self.assertIn("品牌增长方案", task_state["task"])
        self.assertIn("用户画像", task_state["next_action"])
        memory = chat_bot.load_memory()
        self.assertTrue(any("品牌增长方案" in value for value in memory.values()))

    def test_wiki_retrieval_returns_relevant_source(self):
        with open(os.path.join(chat_bot.KNOWLEDGE_PATH, "growth.md"), "w", encoding="utf-8") as wiki_file:
            wiki_file.write("# 增长方法\n\n品牌增长需要先定义用户画像，再设计渠道和 KPI。\n")
        results = chat_bot.retrieve_knowledge("品牌增长 用户画像", limit=2)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "growth.md")
        self.assertIn("用户画像", results[0]["content"])

    def test_wiki_context_is_injected_with_source(self):
        with open(os.path.join(chat_bot.KNOWLEDGE_PATH, "rules.md"), "w", encoding="utf-8") as wiki_file:
            wiki_file.write("# 业务规则\n\n方案必须标注事实、假设、风险和验证方式。\n")
        messages = chat_bot.build_agent_messages([
            {"role": "user", "content": "方案需要标注哪些内容？"},
        ])
        system_message = messages[0]["content"]
        self.assertIn("知识库参考", system_message)
        self.assertIn("来源: rules.md", system_message)

    def test_knowledge_index_updates_only_changed_documents(self):
        source_path = os.path.join(chat_bot.KNOWLEDGE_PATH, "published.md")
        excluded_path = os.path.join(chat_bot.KNOWLEDGE_PATH, "99_已废止但保留")
        os.makedirs(excluded_path)
        with open(source_path, "w", encoding="utf-8") as knowledge_file:
            knowledge_file.write("# 当前规则\n\n新规则内容。")
        with open(os.path.join(excluded_path, "old.md"), "w", encoding="utf-8") as knowledge_file:
            knowledge_file.write("废止规则内容。")

        indexer = KnowledgeIndexer(chat_bot.KNOWLEDGE_INDEX_PATH)
        first_sync = indexer.sync(chat_bot.KNOWLEDGE_PATH)
        second_sync = indexer.sync(chat_bot.KNOWLEDGE_PATH)
        self.assertEqual(first_sync["added"], 1)
        self.assertEqual(second_sync["unchanged"], 1)
        self.assertEqual(indexer.retrieve("新规则", chat_bot.KNOWLEDGE_PATH)[0]["source"], "published.md")
        self.assertEqual(indexer.retrieve("废止目录专属", chat_bot.KNOWLEDGE_PATH), [])

        with open(source_path, "w", encoding="utf-8") as knowledge_file:
            knowledge_file.write("# 当前规则\n\n更新后的规则内容。")
        updated_sync = indexer.sync(chat_bot.KNOWLEDGE_PATH)
        self.assertEqual(updated_sync["updated"], 1)
        os.remove(source_path)
        removed_sync = indexer.sync(chat_bot.KNOWLEDGE_PATH)
        self.assertEqual(removed_sync["removed"], 1)

    def test_messages_and_memory_are_isolated_by_session(self):
        chat_bot.save_message("user", "A 用户的秘密任务", session_id="session-a")
        chat_bot.save_message("user", "B 用户的独立任务", session_id="session-b")
        chat_bot.save_memory("project", "A 的项目", session_id="session-a")
        chat_bot.save_memory("project", "B 的项目", session_id="session-b")

        history_a = chat_bot.load_history(session_id="session-a")
        history_b = chat_bot.load_history(session_id="session-b")
        self.assertIn("A 用户的秘密任务", str(history_a))
        self.assertNotIn("B 用户的独立任务", str(history_a))
        self.assertIn("B 用户的独立任务", str(history_b))
        self.assertNotIn("A 用户的秘密任务", str(history_b))
        self.assertEqual(chat_bot.load_memory(session_id="session-a")["project"], "A 的项目")
        self.assertEqual(chat_bot.load_memory(session_id="session-b")["project"], "B 的项目")

        chat_bot.clear_history(session_id="session-a")
        self.assertEqual(len(chat_bot.load_history(session_id="session-a")), 1)
        self.assertIn("B 用户的独立任务", str(chat_bot.load_history(session_id="session-b")))

    def test_messages_and_memory_are_isolated_by_user(self):
        chat_bot.save_message("user", "用户一的私密任务", session_id="shared", user_id="user-1")
        chat_bot.save_message("user", "用户二的私密任务", session_id="shared", user_id="user-2")
        chat_bot.save_memory("project", "用户一项目", session_id="shared", user_id="user-1")
        chat_bot.save_memory("project", "用户二项目", session_id="shared", user_id="user-2")

        history_one = chat_bot.load_history(session_id="shared", user_id="user-1")
        history_two = chat_bot.load_history(session_id="shared", user_id="user-2")
        self.assertIn("用户一的私密任务", str(history_one))
        self.assertNotIn("用户二的私密任务", str(history_one))
        self.assertIn("用户二的私密任务", str(history_two))
        self.assertNotIn("用户一的私密任务", str(history_two))
        self.assertEqual(
            chat_bot.load_memory(session_id="shared", user_id="user-1")["project"],
            "用户一项目",
        )
        self.assertEqual(
            chat_bot.load_memory(session_id="shared", user_id="user-2")["project"],
            "用户二项目",
        )

    def test_latest_session_id_returns_most_recent_session(self):
        chat_bot.save_message("user", "旧会话", session_id="session-old")
        chat_bot.save_message("user", "新会话", session_id="session-new")
        self.assertEqual(chat_bot.latest_session_id(), "session-new")

    def test_latest_session_id_is_empty_without_history(self):
        self.assertIsNone(chat_bot.latest_session_id())

    def test_text_file_is_extracted_for_current_task(self):
        uploaded_file = BytesIO("客户当前重点是提高复购率".encode("utf-8"))
        uploaded_file.name = "brief.txt"
        uploaded_file.type = "text/plain"
        result = extract_uploaded_file(uploaded_file)
        self.assertEqual(result["kind"], "text")
        self.assertIn("提高复购率", result["text"])

    def test_image_attachment_builds_vision_message(self):
        uploaded_file = BytesIO(b"fake-png-bytes")
        uploaded_file.name = "流程图.png"
        uploaded_file.type = "image/png"
        result = extract_uploaded_file(uploaded_file)
        message = build_attachment_context([result], "请分析这张流程图")
        self.assertIsInstance(message, list)
        self.assertEqual(message[0]["type"], "text")
        self.assertEqual(message[1]["type"], "image_url")

    def test_advisor_diagnosis_identifies_missing_business_context(self):
        diagnosis = diagnose_request("帮我做一个方案")
        self.assertEqual(diagnosis["task_type"], "business_planning")
        self.assertTrue(diagnosis["needs_clarification"])
        self.assertIn("目标", diagnosis["missing_information"])

    def test_advisor_diagnosis_accepts_a_specific_task(self):
        diagnosis = diagnose_request("基于这份客户复购数据，分析渠道问题并给出下周行动方案")
        self.assertFalse(diagnosis["needs_clarification"])
        self.assertEqual(diagnosis["task_type"], "business_analysis")

    def test_provider_model_client_is_exposed(self):
        self.assertTrue(callable(provider_request_model))
        self.assertTrue(callable(chat_bot.request_model))

    def test_local_accounts_hash_passwords_and_return_stable_user(self):
        account_store = LocalAccountStore(chat_bot.DATABASE_PATH)
        user = account_store.create_user("alice", "correct horse", "Alice", is_admin=True)
        self.assertTrue(user.is_admin)
        self.assertEqual(account_store.authenticate("alice", "correct horse").user_id, user.user_id)
        self.assertIsNone(account_store.authenticate("alice", "wrong password"))
        with account_store.connect() as connection:
            password_hash = connection.execute(
                "SELECT password_hash FROM local_users WHERE user_id = ?", (user.user_id,)
            ).fetchone()[0]
        self.assertNotIn("correct horse", password_hash)

    @patch("providers.model_client.urlopen")
    def test_provider_can_transcribe_audio(self, mocked_urlopen):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return '{"text":"请分析这份资料"}'.encode("utf-8")

        mocked_urlopen.return_value = FakeResponse()
        with patch.dict(os.environ, {"AI_API_KEY": "test-key", "AI_BASE_URL": "https://example.test/v1"}):
            result = transcribe_audio(b"fake-audio", "voice.wav")

        self.assertEqual(result, "请分析这份资料")
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("/audio/transcriptions", request.full_url)

    def test_deepseek_chat_endpoint_is_not_used_for_transcription(self):
        with patch.dict(
            os.environ,
            {"AI_API_KEY": "test-key", "AI_BASE_URL": "https://api.deepseek.com/v1"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "DeepSeek.*不提供语音转写"):
                transcribe_audio(b"fake-audio", "voice.wav")


if __name__ == "__main__":
    unittest.main()
