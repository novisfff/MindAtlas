from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class SkillRouterDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def _make_router(self, response_json: dict, *, settings_overrides: dict | None = None):
        class _Resp:
            def __init__(self, content: str) -> None:
                self.content = content

        class _FakeChatOpenAI:
            response_content = "{}"
            last_messages = None

            def __init__(self, *args, **kwargs) -> None:
                pass

            def invoke(self, messages):
                _FakeChatOpenAI.last_messages = messages
                return _Resp(self.response_content)

        _FakeChatOpenAI.response_content = json.dumps(response_json, ensure_ascii=False)
        _FakeChatOpenAI.last_messages = None
        settings = SimpleNamespace(
            assistant_router_history_turns=3,
            assistant_router_history_max_chars_per_message=400,
            assistant_router_history_max_messages=6,
            assistant_router_include_last_skill_hint=True,
        )
        if settings_overrides:
            for key, value in settings_overrides.items():
                setattr(settings, key, value)
        with patch("langchain_openai.ChatOpenAI", new=_FakeChatOpenAI), patch(
            "app.assistant.orchestration.intent_router.get_settings",
            return_value=settings,
        ):
            from app.assistant.orchestration.intent_router import SkillRouter  # noqa: E402

            return SkillRouter(api_key="k", base_url="https://x", model="m", db=None), _FakeChatOpenAI

    def test_new_format_valid_skill_selects_target_skill(self) -> None:
        router, _ = self._make_router(
            {
                "skill": "quick_stats",
                "reason": "命中统计意图",
            }
        )

        decision = router.route("帮我统计一下记录数量")
        self.assertEqual(decision.skill, "quick_stats")
        self.assertEqual(decision.selected_skill, "quick_stats")
        self.assertIsNone(decision.fallback_reason)

    def test_legacy_skills_format_is_compatible(self) -> None:
        router, _ = self._make_router({"skills": ["quick_stats"]})

        decision = router.route("统计记录")
        self.assertEqual(decision.skill, "quick_stats")
        self.assertEqual(decision.selected_skill, "quick_stats")
        self.assertIsNone(decision.fallback_reason)

    def test_invalid_skill_falls_back_to_default(self) -> None:
        router, _ = self._make_router(
            {
                "skill": "not_exists",
                "reason": "错误示例",
            }
        )

        decision = router.route("test")
        self.assertEqual(decision.skill, "not_exists")
        self.assertEqual(decision.selected_skill, "general_chat")
        self.assertEqual(decision.fallback_reason, "invalid_skill")

    def test_empty_skill_falls_back_to_default(self) -> None:
        router, _ = self._make_router(
            {
                "skill": "",
                "reason": "无法确定",
            }
        )

        decision = router.route("test")
        self.assertEqual(decision.skill, "")
        self.assertEqual(decision.selected_skill, "general_chat")
        self.assertEqual(decision.fallback_reason, "missing_skill")

    def test_route_includes_recent_history_messages(self) -> None:
        router, fake_llm = self._make_router(
            {
                "skill": "quick_stats",
                "reason": "承接上文统计",
            }
        )
        history = [
            {"role": "system", "content": "system seed"},
            {"role": "user", "content": "先看一下我的数据"},
            {"role": "assistant", "content": "好的，我来统计"},
            {"role": "tool", "content": "tool output"},
            {"role": "user", "content": "再按标签看"},
            {"role": "assistant", "content": "按标签统计如下"},
        ]

        decision = router.route("继续这个", history=history)

        self.assertEqual(decision.selected_skill, "quick_stats")
        self.assertIsNotNone(fake_llm.last_messages)
        roles = [item.get("role") for item in fake_llm.last_messages]
        self.assertEqual(roles, ["system", "user", "assistant", "user", "assistant", "user"])
        self.assertEqual(fake_llm.last_messages[-1]["content"], "继续这个")

    def test_history_limit_and_truncation_are_applied(self) -> None:
        router, fake_llm = self._make_router(
            {
                "skill": "smart_capture",
                "reason": "明确创建意图",
            },
            settings_overrides={
                "assistant_router_history_turns": 5,
                "assistant_router_history_max_messages": 2,
                "assistant_router_history_max_chars_per_message": 20,
            },
        )
        long_text = "x" * 64
        history = [
            {"role": "user", "content": long_text},
            {"role": "assistant", "content": long_text},
            {"role": "user", "content": long_text},
            {"role": "assistant", "content": long_text},
        ]

        _ = router.route("创建一条记录", history=history)

        self.assertIsNotNone(fake_llm.last_messages)
        history_messages = fake_llm.last_messages[1:-1]
        self.assertEqual(len(history_messages), 2)
        for item in history_messages:
            self.assertLessEqual(len(str(item.get("content") or "")), 20)
            self.assertTrue(str(item.get("content") or "").endswith("..."))

    def test_history_accepts_clear_intent_switch(self) -> None:
        router, _ = self._make_router(
            {
                "skill": "smart_capture",
                "reason": "本轮明确要求创建记录",
            }
        )
        decision = router.route(
            "帮我创建一条记录：今天完成了 Router 改造",
            history=[
                {"role": "user", "content": "统计我最近的记录数"},
                {"role": "assistant", "content": "已经帮你统计完成"},
            ],
        )
        self.assertEqual(decision.selected_skill, "smart_capture")

    def test_missing_conversation_id_does_not_break_route(self) -> None:
        router, _ = self._make_router(
            {
                "skill": "quick_stats",
                "reason": "统计意图",
            }
        )
        decision = router.route(
            "继续这个",
            history=[{"role": "user", "content": "统计数据"}],
            runtime_context={"conversation_id": "not-a-uuid"},
        )
        self.assertEqual(decision.selected_skill, "quick_stats")


if __name__ == "__main__":
    unittest.main()
