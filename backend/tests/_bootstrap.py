from __future__ import annotations

import os
import sys
import types
from pathlib import Path


def _install_test_stub_modules() -> None:
    """Provide minimal stubs for optional runtime deps in lightweight unit-test envs."""
    if "langchain_openai" not in sys.modules:
        try:
            __import__("langchain_openai")
        except ModuleNotFoundError:
            langchain_openai = types.ModuleType("langchain_openai")

            class ChatOpenAI:  # pragma: no cover - simple import stub
                pass

            langchain_openai.ChatOpenAI = ChatOpenAI
            sys.modules["langchain_openai"] = langchain_openai

    if "langchain_core.messages" not in sys.modules:
        try:
            __import__("langchain_core.messages")
        except ModuleNotFoundError:
            langchain_core = sys.modules.get("langchain_core") or types.ModuleType("langchain_core")
            messages = types.ModuleType("langchain_core.messages")

            class BaseMessage:  # pragma: no cover - simple import stub
                def __init__(self, content: str = "", **kwargs):
                    self.content = content
                    self.additional_kwargs = kwargs

            class SystemMessage(BaseMessage):
                pass

            class HumanMessage(BaseMessage):
                pass

            class AIMessage(BaseMessage):
                pass

            class AIMessageChunk(BaseMessage):
                pass

            class ToolMessage(BaseMessage):
                pass

            messages.BaseMessage = BaseMessage
            messages.SystemMessage = SystemMessage
            messages.HumanMessage = HumanMessage
            messages.AIMessage = AIMessage
            messages.AIMessageChunk = AIMessageChunk
            messages.ToolMessage = ToolMessage

            langchain_core.messages = messages
            sys.modules["langchain_core"] = langchain_core
            sys.modules["langchain_core.messages"] = messages

    if "langchain_core.tools" not in sys.modules:
        try:
            __import__("langchain_core.tools")
        except ModuleNotFoundError:
            langchain_core = sys.modules.get("langchain_core") or types.ModuleType("langchain_core")
            tools = types.ModuleType("langchain_core.tools")

            def tool(*decorator_args, **decorator_kwargs):  # pragma: no cover - simple import stub
                if decorator_args and callable(decorator_args[0]) and len(decorator_args) == 1 and not decorator_kwargs:
                    return decorator_args[0]

                def _decorator(fn):
                    return fn

                return _decorator

            tools.tool = tool
            langchain_core.tools = tools
            sys.modules["langchain_core"] = langchain_core
            sys.modules["langchain_core.tools"] = tools

    if "langgraph.graph.message" not in sys.modules:
        try:
            __import__("langgraph.graph.message")
        except ModuleNotFoundError:
            langgraph = sys.modules.get("langgraph") or types.ModuleType("langgraph")
            graph = sys.modules.get("langgraph.graph") or types.ModuleType("langgraph.graph")
            graph_message = types.ModuleType("langgraph.graph.message")

            def add_messages(left, right):  # pragma: no cover - simple import stub
                left_list = list(left or [])
                right_list = list(right or [])
                return left_list + right_list

            graph_message.add_messages = add_messages
            graph.message = graph_message

            if not hasattr(graph, "END"):
                graph.END = "__END__"

            if not hasattr(graph, "StateGraph"):
                class StateGraph:  # pragma: no cover - simple import stub
                    def __init__(self, *_args, **_kwargs):
                        pass

                    def add_node(self, *_args, **_kwargs):
                        return None

                    def add_edge(self, *_args, **_kwargs):
                        return None

                    def add_conditional_edges(self, *_args, **_kwargs):
                        return None

                    def set_entry_point(self, *_args, **_kwargs):
                        return None

                    def compile(self):
                        return self

                graph.StateGraph = StateGraph

            langgraph.graph = graph
            sys.modules["langgraph"] = langgraph
            sys.modules["langgraph.graph"] = graph
            sys.modules["langgraph.graph.message"] = graph_message


def bootstrap_backend_imports() -> None:
    """Ensure `import app.*` works and avoids requiring PostgreSQL drivers in unit tests."""
    repo_root = Path(__file__).resolve().parents[2]
    backend_dir = repo_root / "backend"
    sys.path.insert(0, str(backend_dir))

    # Avoid importing psycopg2 just to import `app.database` / models in unit tests.
    os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    _install_test_stub_modules()


def reset_caches() -> None:
    """Clear lru_cache-backed singletons to isolate tests."""
    # bootstrap first so these imports work
    bootstrap_backend_imports()

    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass

    try:
        from app.common.storage import get_minio_client

        get_minio_client.cache_clear()
    except Exception:
        pass

    try:
        from app.assistant.skill_catalog.defaults_loader import clear_system_defaults_cache

        clear_system_defaults_cache()
    except Exception:
        pass

    try:
        from app.assistant_config.system_behavior_defaults_loader import clear_system_behavior_defaults_cache

        clear_system_behavior_defaults_cache()
    except Exception:
        pass

    try:
        from app.assistant_config.system_behavior_registry import clear_system_behavior_registry_cache

        clear_system_behavior_registry_cache()
    except Exception:
        pass

    try:
        from app.lightrag.manager import reset_lightrag_singletons_for_tests

        reset_lightrag_singletons_for_tests()
    except Exception:
        pass

    try:
        from app.lightrag.service import reset_lightrag_query_state_for_tests

        reset_lightrag_query_state_for_tests()
    except Exception:
        pass
