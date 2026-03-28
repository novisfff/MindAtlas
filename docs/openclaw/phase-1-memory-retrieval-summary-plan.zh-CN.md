# MindAtlas × OpenClaw 第一期落地方案（记 / 查 / 总结）

## 1. 目标

第一期只做三件事，并把它们做稳：

1. **记**：用户明确想记住某件事时，OpenClaw 能把相关上下文提交给 MindAtlas，由 MindAtlas 内部能力物化为正式记录。
2. **查**：OpenClaw 能用自然语言检索 MindAtlas 中已有记录，并选择最合适的检索路径。
3. **总结**：OpenClaw 能调用 MindAtlas 对个人经历做基础周报、月报与主题总结。

本期不追求一步到位做复杂推荐、强图谱推理或确定性的自动沉淀钩子。重点是先把“显式记录闭环、查询闭环、总结闭环”打通，并把自动记录收口为可控的提示词增强能力。

---

## 2. 设计原则

### 2.1 职责边界

- **OpenClaw**：聊天入口、任务理解、路由决策、策略提示词。
- **openclaw-mindatlas 插件**：catalog 拉取、工具注册、执行转发、上下文透传。
- **MindAtlas**：能力目录、知识主库、记录物化、关系、报告、图谱与内部 workflow 执行。

### 2.2 Catalog First

- OpenClaw 不假设存在固定内置 tool 名单。
- 所有记录、检索、图谱、报告能力都以当前 capability catalog 为准。
- skill 负责告诉 OpenClaw “该选哪一类能力”，而不是写死某个 `toolName`。

### 2.3 Context Submission，而不是字段拼装

- OpenClaw 的职责是把“为什么值得记、发生了什么、有哪些线索”提交给 MindAtlas。
- OpenClaw 不再承担完整 `Entry` 字段拼装，不负责稳定推断 entry type、summary、content、tags、relation 等最终结构。
- 这些最终字段由 MindAtlas 内部 workflow 或高层记录能力生成。

### 2.4 Prompt-Driven Best Effort

- 自动记录继续走提示词驱动策略。
- 第一期不承诺任务完成钩子、后处理拦截器或强一致自动沉淀。
- 自动记录定位为增强项，不是可靠的系统级 guaranteed hook。

### 2.5 单用户个人系统前提

- 本期默认场景是个人单用户使用。
- 不做 per-user、per-channel、per-tenant 数据隔离设计。
- 多用户身份映射、权限分流与跨用户审计不在第一期范围内。

---

## 3. 第一期范围

### 3.1 In Scope

#### 记
- 用户明确说“记一下 / 记住 / 帮我记录”时，OpenClaw 优先调用管理员暴露的记录类 capability。
- 记录类 capability 推荐收口为“高层上下文提交”，由 MindAtlas 内部 workflow 生成最终记录字段。
- 自动记录仅作为 prompt-driven 增强项，在高价值任务收尾时可以尝试触发，但不作为可靠主路径。

#### 查
- 支持关键词检索记录。
- 支持展开具体记录详情。
- 支持知识图谱 / RAG 查询。
- 支持最近一段时间内的回顾与检索。

#### 总结
- 支持周报。
- 支持月报。
- 支持围绕主题 / 标签 / 项目的基础总结。

### 3.2 Out of Scope

第一期暂不重点投入：
- 确定性的自动记录触发机制
- 全自动关系推荐
- 大规模 embedding 去重
- 多用户隔离与身份映射
- 自动知识提炼为 SOP
- 高级趋势分析与行动建议

---

## 4. 第一期模块

### 模块 A：OpenClaw Overview Policy

**定位**：认知型 skill。

**职责**：
- 告诉 OpenClaw MindAtlas 是什么系统
- 告诉 OpenClaw 什么时候应该优先考虑 MindAtlas
- 强调 catalog-first，而不是固定工具名优先

### 模块 B：OpenClaw Auto Capture Policy

**定位**：记录策略 skill。

**职责**：
- 判断是否值得记录
- 只在高价值结果或用户明确要求时考虑记录
- 组织待提交的上下文、摘要、来源信息与候选线索
- 优先选择高层记录 capability 或专用 capture workflow

**第一期建议触发场景**：
- 用户明确要求记录
- 安装 / 部署 / 配置完成
- 修复一个明确问题
- 形成一个稳定结论
- 完成一段后续可能需要回顾的高价值任务

**第一期不主动记录的场景**：
- 闲聊
- 短问短答
- 未验证猜测
- 高重复低价值信息
- 明显只属于临时上下文的信息

### 模块 C：OpenClaw Retrieval Policy

**定位**：检索策略 skill。

**职责**：
- 根据用户提问，选择当前 catalog 中最匹配的检索类 capability
- 在关键词检索、详情查询、图谱查询之间做路由
- 结果优先返回结构化摘要，而不是原样抛大 JSON

### 模块 D：OpenClaw Summary Policy

**定位**：总结策略 skill。

**职责**：
- 识别用户是在要周报、月报还是主题总结
- 选择当前 catalog 中最匹配的报告类或总结类 capability
- 若没有现成主题总结能力，则先检索再由 OpenClaw 做整合输出

### 模块 E：MindAtlas Capture Materialization

**定位**：MindAtlas 内部记录物化能力。

**职责**：
- 接住来自 OpenClaw 的高层上下文提交
- 根据系统内部 workflow 或服务生成最终 entry 字段
- 决定 entry type、summary、content、tags、relation 等结构化结果
- 负责去重、幂等、合并等治理逻辑

### 模块 F：openclaw-mindatlas 插件增强

**定位**：保持桥接层干净，但补齐可观察性与上下文透传。

**职责**：
- 按当前 catalog 暴露工具
- 执行时透传更多上下文 header
- 做更清晰的错误映射与 stale catalog 提示

---

## 5. 记录入口设计

### 5.1 推荐路径：高层上下文提交能力

第一期文档推荐把 OpenClaw 的记录入口收口为“高层上下文提交”能力，而不是字段级 `Entry` 创建接口。

OpenClaw 只需要提供这类信息：
- 原始上下文或压缩后的事件摘要
- 来源信息，如 channel / session / tool / source
- 用户意图，如“记一下”或“以后要查”
- 候选标签、主题线索、项目线索
- 可选的时间信息或任务阶段信息

MindAtlas 内部负责：
- 生成最终 title / summary / content
- 推断或选择 entry type
- 生成 tags、relations、metadata
- 处理去重、merge、upsert 等逻辑

### 5.2 当前实现的定位

当前系统的默认记录入口已经切换为 workflow-backed 的薄上下文提交能力。

当前系统中仍可存在字段级 `capture_entry` 风格能力，但其定位应降级为：
- 过渡期能力
- 手动记录能力
- 只有在用户明确给出结构化字段，或管理员明确希望保留该入口时才使用

它不再是 OpenClaw 自动记录的主路径。

### 5.3 去重与治理

第一期仍建议保留以下治理方向，但具体数据结构留到后续实现时再定：
- 来源 metadata
- captureMode 区分，如 `manual / auto / suggested`
- 轻量幂等或 merge 机制
- 最近记录查重或相似任务查重

---

## 6. Catalog 路由策略

### 6.1 记录

- 当用户明确要求记录时，优先选择当前 catalog 中最匹配的记录类 capability。
- 优先使用高层记录 capability 或专用 capture workflow。
- 如果当前只暴露了字段级记录能力，则仅在用户提供足够明确字段时使用。

### 6.2 检索

- 关键词 / 标签 / 时间范围类问题，优先选择搜索类 capability。
- 已有记录 ID 或明确单条记录上下文时，优先选择详情类 capability。
- 需要跨多条记录综合回答时，优先选择图谱 / RAG 类 capability。

### 6.3 总结

- 周期总结优先选择周报或月报类 capability。
- 主题总结优先选择总结类 capability。
- 若当前 catalog 中没有独立主题总结能力，则先检索相关记录，再由 OpenClaw 侧整合输出。

### 6.4 不写死 tool name

- skill 中不应假设固定 `toolName`。
- `toolName` 是 catalog 暴露出来的运行时名称，不是稳定产品契约。
- 稳定契约应该是“能力类别”和“当前 catalog 中的 exposed item”。

---

## 7. 第一期实施顺序

### Step 1：完善 skill 文案与路由认知

目标：
- 先让 OpenClaw 明白 MindAtlas 的定位
- 统一 catalog-first 的路由方式
- 让记录、检索、总结三类策略不再互相冲突

### Step 2：定义高层记录入口

目标：
- 明确未来推荐的记录入口是“上下文提交”
- 把字段级创建能力从自动记录主路径中降级出去

建议结果：
- OpenClaw 只负责提交上下文
- MindAtlas 负责物化正式记录

### Step 3：收口检索与总结路由

目标：
- 让 OpenClaw 通过当前 catalog 选择搜索、详情、图谱、周报、月报、主题总结能力
- 避免文档继续写死某个工具名

### Step 4：把自动记录定义为增强项

目标：
- 明确自动记录是 best effort
- 只在高价值任务收尾时提示 OpenClaw 考虑记录
- 不承诺 deterministic task-completion trigger

---

## 8. MVP 验收标准

### 8.1 记

- 用户说“帮我记一下今天把 OpenClaw 和 MindAtlas 集成好了”
- OpenClaw 会优先寻找当前 exposed 的记录类 capability
- OpenClaw 提交的是上下文与线索，而不是强依赖自己拼完整 entry 字段

### 8.2 查

- 用户说“搜一下我有没有记过 OpenClaw 安装”
- OpenClaw 能从当前 catalog 中选择最合适的检索类 capability
- 用户说“把那条详情给我看看”时，可继续路由到详情类 capability

### 8.3 总结

- 用户说“生成本周周报”
- OpenClaw 能从当前 catalog 中选择周报类 capability
- 用户说“总结一下我最近在折腾什么”
- OpenClaw 能选择总结类 capability，或先检索再整合输出

### 8.4 自动记录

- 文档明确自动记录只是 prompt-driven 增强项
- 不再把它表述成已经具备强触发机制的系统能力

---

## 9. 风险与控制

### 风险 1：自动记录过多或过少

**控制措施**：
- 第一期开高阈值
- 只在高价值场景下建议触发
- 把自动记录定位为增强项，而非默认强行为

### 风险 2：OpenClaw 继续错误拼装字段

**控制措施**：
- 在 skill 和方案文档中明确 OpenClaw 只负责提交上下文
- 把最终字段生成责任收回到 MindAtlas 内部 workflow

### 风险 3：文档和现有实现脱节

**控制措施**：
- 明确区分“当前仍存在的字段级能力”与“推荐演进路径”
- 不在文档中假装旧接口已经被删除

### 风险 4：toolName 漂移影响策略

**控制措施**：
- skill 不写死工具名
- 所有策略统一以 capability catalog 为准

---

## 10. 一句话总结

第一期真正要做稳的是这条链路：

**OpenClaw 负责理解用户、选择 catalog 中合适的记录 / 检索 / 总结能力，并在需要记录时提交相关上下文；MindAtlas 负责把这些上下文物化为正式知识记录并持续提供可检索、可总结的结果。**
