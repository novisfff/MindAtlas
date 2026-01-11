# MindAtlas AI 助手技术方案

> 版本：1.0
> 日期：2026年1月

---

## 一、概述

### 1.1 背景

MindAtlas 作为个人知识与经历管理系统，已具备完整的 CRUD 功能。为进一步提升用户体验，计划引入 AI 助手功能，让用户能够通过自然语言与系统交互，实现智能化的信息查询、数据分析和内容创建。

### 1.2 定位

AI 助手定位为**个人秘书**，核心职责：

- **信息检索**：快速查找和筛选记录
- **数据分析**：统计和分析用户数据
- **内容创建**：辅助创建新的 Entry
- **智能问答**：回答关于用户知识库的问题

### 1.3 设计原则

1. **渐进式增强**：AI 功能是增强而非依赖，系统核心功能不依赖 AI
2. **工具化架构**：通过 Function Calling / Tool Use 实现功能扩展
3. **对话持久化**：保存对话历史，支持上下文连续
4. **多入口交互**：独立页面 + 主页悬浮窗口

---

## 二、系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend                              │
│  ┌─────────────────┐    ┌─────────────────────────────────┐ │
│  │  Chat Page      │    │  Homepage Widget                │ │
│  │  (独立页面)      │    │  (悬浮窗口)                      │ │
│  └────────┬────────┘    └────────────────┬────────────────┘ │
│           │                              │                   │
│           └──────────────┬───────────────┘                   │
│                          ▼                                   │
│              ┌───────────────────────┐                       │
│              │   Chat API Client     │                       │
│              │   (WebSocket/SSE)     │                       │
│              └───────────┬───────────┘                       │
└──────────────────────────┼───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                        Backend                                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                   AI Assistant Module                   │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │  │
│  │  │ Chat Router  │  │ Chat Service │  │ LangChain    │  │  │
│  │  │ (SSE/WS)     │──│              │──│ Agent        │  │  │
│  │  └──────────────┘  └──────────────┘  └──────┬───────┘  │  │
│  │                                             │           │  │
│  │                    ┌────────────────────────┼────────┐  │  │
│  │                    │         Tools          │        │  │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │  │  │
│  │  │ Entry Tool  │  │ Stats Tool  │  │ Search Tool │   │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘   │  │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │  │  │
│  │  │ Tag Tool    │  │ Type Tool   │  │ Relation    │   │  │  │
│  │  │             │  │             │  │ Tool        │   │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘   │  │  │
│  │                    └─────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌────────────────┐  ┌────────────────┐                      │
│  │ Conversation   │  │ AI Provider    │                      │
│  │ Storage        │  │ Config         │                      │
│  └───────┬────────┘  └───────┬────────┘                      │
└──────────┼───────────────────┼────────────────────────────────┘
           │                   │
           ▼                   ▼
┌──────────────────────────────────────────────────────────────┐
│                      PostgreSQL                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │
│  │conversations│  │ messages    │  │ ai_providers│           │
│  └─────────────┘  └─────────────┘  └─────────────┘           │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 技术选型

| 组件 | 技术选择 | 说明 |
|------|----------|------|
| AI 框架 | LangChain | 成熟的 AI 应用开发框架，支持 Agent 和 Tools |
| 通信协议 | SSE (Server-Sent Events) | 支持流式输出，实现打字机效果 |
| LLM 接口 | OpenAI 兼容 API | 复用现有 AI Provider 配置 |
| 数据存储 | PostgreSQL | 对话历史持久化 |

---

## 三、后端设计

### 3.1 模块结构

```
backend/app/
├── assistant/                 # AI 助手模块
│   ├── __init__.py
│   ├── models.py             # 数据库模型
│   ├── schemas.py            # Pydantic 模型
│   ├── service.py            # 业务逻辑
│   ├── router.py             # API 路由
│   ├── agent.py              # LangChain Agent 定义
│   └── tools/                # 工具定义
│       ├── __init__.py
│       ├── entry_tools.py    # Entry 相关工具
│       ├── search_tools.py   # 搜索工具
│       ├── stats_tools.py    # 统计工具
│       └── base.py           # 工具基类
```

### 3.2 数据库模型

#### 3.2.1 对话表 (conversations)

```python
class Conversation(BaseEntity):
    """对话会话"""
    __tablename__ = "conversations"

    title = Column(String(200), nullable=True)        # 对话标题（可自动生成）
    summary = Column(Text, nullable=True)             # 对话摘要
    is_archived = Column(Boolean, default=False)      # 是否归档

    # 关系
    messages = relationship("Message", back_populates="conversation")
```

#### 3.2.2 消息表 (messages)

```python
class Message(BaseEntity):
    """对话消息"""
    __tablename__ = "messages"

    conversation_id = Column(UUID, ForeignKey("conversations.id"), nullable=False)
    role = Column(String(20), nullable=False)         # user / assistant / system
    content = Column(Text, nullable=False)            # 消息内容

    # 工具调用记录（可选）
    tool_calls = Column(JSON, nullable=True)          # 工具调用信息
    tool_results = Column(JSON, nullable=True)        # 工具执行结果

    # 关系
    conversation = relationship("Conversation", back_populates="messages")
```

### 3.3 LangChain Agent 设计

#### 3.3.1 Agent 配置

```python
# assistant/agent.py
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder

SYSTEM_PROMPT = """你是 MindAtlas 的 AI 助手，一个智能的个人秘书。

你的职责是帮助用户管理他们的知识和经历记录。你可以：
1. 搜索和查询用户的记录（Entry）
2. 统计和分析用户的数据
3. 帮助用户创建新的记录
4. 回答关于用户知识库的问题

请始终保持友好、专业的态度。在执行操作前，确认用户的意图。
对于创建或修改操作，请先向用户确认详细信息。

当前日期：{current_date}
"""

def create_assistant_agent(llm: ChatOpenAI, tools: list):
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    agent = create_openai_tools_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)
```

#### 3.3.2 工具定义

**Entry 搜索工具**

```python
# assistant/tools/entry_tools.py
from langchain.tools import tool
from typing import Optional

@tool
def search_entries(
    keyword: Optional[str] = None,
    type_code: Optional[str] = None,
    tag_names: Optional[list[str]] = None,
    limit: int = 10
) -> str:
    """搜索用户的记录（Entry）。

    Args:
        keyword: 搜索关键词，匹配标题和内容
        type_code: 记录类型编码，如 knowledge, project, competition
        tag_names: 标签名称列表
        limit: 返回结果数量限制

    Returns:
        匹配的记录列表，包含标题、类型、摘要等信息
    """
    # 实现搜索逻辑
    pass

@tool
def get_entry_detail(entry_id: str) -> str:
    """获取记录的详细信息。

    Args:
        entry_id: 记录的 UUID

    Returns:
        记录的完整信息，包含内容、标签、关联等
    """
    pass

@tool
def create_entry(
    title: str,
    content: str,
    type_code: str,
    tag_names: Optional[list[str]] = None,
    summary: Optional[str] = None
) -> str:
    """创建新的记录。

    Args:
        title: 记录标题
        content: 记录内容（Markdown 格式）
        type_code: 记录类型编码
        tag_names: 标签名称列表
        summary: 摘要（可选，不提供则自动生成）

    Returns:
        创建成功的记录信息
    """
    pass
```

**统计分析工具**

```python
# assistant/tools/stats_tools.py
@tool
def get_statistics() -> str:
    """获取用户数据的整体统计信息。

    Returns:
        统计数据，包含记录总数、各类型数量、标签使用情况等
    """
    pass

@tool
def get_entries_by_time_range(
    start_date: str,
    end_date: str,
    type_code: Optional[str] = None
) -> str:
    """获取指定时间范围内的记录。

    Args:
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        type_code: 可选的类型筛选

    Returns:
        时间范围内的记录列表
    """
    pass

@tool
def analyze_activity(period: str = "month") -> str:
    """分析用户的活动情况。

    Args:
        period: 分析周期，可选 week/month/year

    Returns:
        活动分析报告，包含创建趋势、活跃类型等
    """
    pass
```

**辅助工具**

```python
# assistant/tools/helper_tools.py
@tool
def list_entry_types() -> str:
    """列出所有可用的记录类型。

    Returns:
        类型列表，包含编码、名称、颜色等
    """
    pass

@tool
def list_tags() -> str:
    """列出所有标签。

    Returns:
        标签列表，包含名称、颜色、使用次数
    """
    pass
```

### 3.4 API 路由设计

#### 3.4.1 对话管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/assistant/conversations` | 获取对话列表 |
| POST | `/api/assistant/conversations` | 创建新对话 |
| GET | `/api/assistant/conversations/{id}` | 获取对话详情（含消息） |
| DELETE | `/api/assistant/conversations/{id}` | 删除对话 |
| PATCH | `/api/assistant/conversations/{id}` | 更新对话（标题、归档） |

#### 3.4.2 消息 API（SSE 流式）

```python
# assistant/router.py
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

@router.post("/conversations/{conversation_id}/chat")
async def chat(
    conversation_id: UUID,
    request: ChatRequest,
    db: Session = Depends(get_db)
) -> StreamingResponse:
    """发送消息并获取 AI 回复（SSE 流式）"""
    return StreamingResponse(
        chat_stream(conversation_id, request.message, db),
        media_type="text/event-stream"
    )
```

#### 3.4.3 SSE 事件格式

```
event: message_start
data: {"conversation_id": "uuid", "message_id": "uuid"}

event: content_delta
data: {"delta": "你好"}

event: tool_use
data: {"tool": "search_entries", "input": {"keyword": "React"}}

event: tool_result
data: {"tool": "search_entries", "result": "找到 3 条记录..."}

event: message_end
data: {"finish_reason": "stop"}

event: error
data: {"error": "错误信息"}
```

### 3.5 Schemas 定义

```python
# assistant/schemas.py
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from uuid import UUID

class ChatRequest(BaseModel):
    message: str

class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    tool_calls: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ConversationResponse(BaseModel):
    id: UUID
    title: Optional[str]
    summary: Optional[str]
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    messages: List[MessageResponse] = []

    class Config:
        from_attributes = True

class ConversationListResponse(BaseModel):
    id: UUID
    title: Optional[str]
    summary: Optional[str]
    is_archived: bool
    created_at: datetime
    message_count: int
```

---

## 四、前端设计

### 4.1 模块结构

```
frontend/src/features/assistant/
├── api/
│   └── index.ts              # API 调用
├── components/
│   ├── ChatWindow.tsx        # 聊天窗口主组件
│   ├── MessageList.tsx       # 消息列表
│   ├── MessageItem.tsx       # 单条消息
│   ├── ChatInput.tsx         # 输入框
│   ├── ConversationList.tsx  # 对话列表
│   ├── ToolCallDisplay.tsx   # 工具调用展示
│   └── FloatingWidget.tsx    # 悬浮窗口组件
├── hooks/
│   └── useChat.ts            # 聊天逻辑 Hook
├── queries.ts                # TanStack Query hooks
├── AssistantPage.tsx         # 独立页面
└── index.ts
```

### 4.2 页面布局

#### 4.2.1 独立对话页面 (`/assistant`)

```
┌─────────────────────────────────────────────────────────────┐
│  Header                                              [语言] │
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│  对话列表     │              聊天区域                        │
│              │  ┌────────────────────────────────────────┐  │
│  [+ 新对话]   │  │  AI: 你好！我是你的 MindAtlas 助手     │  │
│              │  │                                        │  │
│  ○ 对话 1    │  │  User: 帮我查找关于 React 的记录       │  │
│  ○ 对话 2    │  │                                        │  │
│  ○ 对话 3    │  │  AI: [正在搜索...]                     │  │
│              │  │      找到 3 条相关记录：                │  │
│              │  │      1. React Hooks 学习笔记           │  │
│              │  │      2. React 项目实践                 │  │
│              │  │      ...                               │  │
│              │  └────────────────────────────────────────┘  │
│              │                                              │
│              │  ┌────────────────────────────────┐ [发送]   │
│              │  │ 输入消息...                     │         │
│              │  └────────────────────────────────┘         │
└──────────────┴──────────────────────────────────────────────┘
```

#### 4.2.2 主页悬浮窗口

```
                                    ┌──────────────────────┐
                                    │ AI 助手         [−][×]│
                                    ├──────────────────────┤
                                    │                      │
                                    │  消息区域（简化版）   │
                                    │                      │
                                    ├──────────────────────┤
                                    │ [输入...]    [发送]  │
                                    └──────────────────────┘
                                                    ┌─────┐
                                                    │ 🤖  │ ← 悬浮按钮
                                                    └─────┘
```

特点：
- 右下角悬浮按钮，点击展开对话窗口
- 窗口可拖拽、可调整大小
- 支持最小化/关闭
- 与独立页面共享对话历史

### 4.3 核心组件

#### 4.3.1 useChat Hook

```typescript
// hooks/useChat.ts
interface UseChatOptions {
  conversationId?: string;
  onMessage?: (message: Message) => void;
  onError?: (error: Error) => void;
}

interface UseChatReturn {
  messages: Message[];
  isLoading: boolean;
  sendMessage: (content: string) => Promise<void>;
  stopGeneration: () => void;
}

export function useChat(options: UseChatOptions): UseChatReturn {
  // SSE 连接管理
  // 消息状态管理
  // 流式内容处理
}
```

#### 4.3.2 MessageItem 组件

```typescript
// components/MessageItem.tsx
interface MessageItemProps {
  message: Message;
  isStreaming?: boolean;
}

// 支持展示：
// - 普通文本消息
// - Markdown 渲染
// - 工具调用状态（搜索中...、创建中...）
// - 工具执行结果（记录列表、统计数据等）
```

#### 4.3.3 FloatingWidget 组件

```typescript
// components/FloatingWidget.tsx
interface FloatingWidgetProps {
  defaultOpen?: boolean;
  position?: { x: number; y: number };
}

// 功能：
// - 悬浮按钮（可配置图标）
// - 展开/收起动画
// - 拖拽移动
// - 记住位置状态
```

### 4.4 状态管理

```typescript
// stores/assistant-store.ts
interface AssistantState {
  // 当前对话
  currentConversationId: string | null;

  // 悬浮窗口状态
  widgetOpen: boolean;
  widgetPosition: { x: number; y: number };

  // Actions
  setCurrentConversation: (id: string | null) => void;
  toggleWidget: () => void;
  setWidgetPosition: (pos: { x: number; y: number }) => void;
}
```

### 4.5 SSE 处理

```typescript
// api/index.ts
export async function* streamChat(
  conversationId: string,
  message: string
): AsyncGenerator<ChatEvent> {
  const response = await fetch(
    `/api/assistant/conversations/${conversationId}/chat`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    }
  );

  const reader = response.body?.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader!.read();
    if (done) break;

    const chunk = decoder.decode(value);
    // 解析 SSE 事件
    for (const event of parseSSE(chunk)) {
      yield event;
    }
  }
}
```

---

## 五、实施计划

### 5.1 阶段一：基础架构

**目标**：搭建 AI 助手的基础框架

**任务清单**：
1. 创建数据库模型（conversations, messages）
2. 生成数据库迁移
3. 实现对话管理 API（CRUD）
4. 安装 LangChain 依赖
5. 创建基础 Agent 框架

**依赖添加**：
```
# requirements.txt
langchain>=0.1.0
langchain-openai>=0.0.5
```

### 5.2 阶段二：工具实现

**目标**：实现核心工具函数

**任务清单**：
1. 实现 Entry 搜索工具
2. 实现 Entry 详情工具
3. 实现 Entry 创建工具
4. 实现统计分析工具
5. 实现辅助工具（类型列表、标签列表）
6. 工具单元测试

### 5.3 阶段三：SSE 流式输出

**目标**：实现流式对话能力

**任务清单**：
1. 实现 SSE 流式响应
2. 集成 LangChain Agent 与流式输出
3. 实现工具调用事件推送
4. 错误处理和重试机制

### 5.4 阶段四：前端独立页面

**目标**：实现 AI 助手独立对话页面

**任务清单**：
1. 创建 assistant 模块目录结构
2. 实现对话列表组件
3. 实现聊天窗口组件
4. 实现 SSE 消息处理
5. 实现 Markdown 渲染
6. 添加路由和导航入口

### 5.5 阶段五：悬浮窗口

**目标**：实现主页悬浮对话窗口

**任务清单**：
1. 实现 FloatingWidget 组件
2. 实现拖拽和位置记忆
3. 集成到主布局
4. 与独立页面共享状态

### 5.6 阶段六：优化与完善

**目标**：提升用户体验和稳定性

**任务清单**：
1. 国际化支持（中英文）
2. 工具调用结果美化展示
3. 对话标题自动生成
4. 性能优化
5. 错误提示优化

---

## 六、工具清单汇总

| 工具名称 | 功能描述 | 参数 |
|----------|----------|------|
| `search_entries` | 搜索记录 | keyword, type_code, tag_names, limit |
| `get_entry_detail` | 获取记录详情 | entry_id |
| `create_entry` | 创建新记录 | title, content, type_code, tag_names, summary |
| `get_statistics` | 获取统计数据 | - |
| `get_entries_by_time_range` | 按时间范围查询 | start_date, end_date, type_code |
| `analyze_activity` | 分析活动情况 | period |
| `list_entry_types` | 列出记录类型 | - |
| `list_tags` | 列出所有标签 | - |

---

## 七、未来扩展

### 7.1 知识库集成（V2）

- 向量数据库集成（如 pgvector）
- 文档嵌入和语义搜索
- RAG（检索增强生成）能力

### 7.2 更多工具（V2+）

- 记录编辑/删除工具
- 关系管理工具
- 附件管理工具
- 日程提醒工具

### 7.3 MCP 支持（可选）

- 将工具封装为 MCP Server
- 支持外部 AI 客户端调用

---

## 八、总结

本方案设计了一个基于 LangChain 的 AI 助手系统，具备以下特点：

1. **工具化架构**：通过 Function Calling 实现与系统的深度集成
2. **流式输出**：SSE 实现打字机效果，提升用户体验
3. **多入口交互**：独立页面 + 悬浮窗口，满足不同场景需求
4. **对话持久化**：保存历史记录，支持上下文连续
5. **可扩展性**：预留知识库和更多工具的扩展空间

---

*MindAtlas AI 助手 - 让知识管理更智能*
