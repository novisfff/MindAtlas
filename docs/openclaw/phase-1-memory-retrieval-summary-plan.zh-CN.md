# MindAtlas × OpenClaw 第一期落地方案（记 / 查 / 总结）

## 1. 目标

第一期只做三件事，并把它们做稳：

1. **记**：OpenClaw 能在高价值场景下把经历稳定写入 MindAtlas。
2. **查**：OpenClaw 能自然语言检索 MindAtlas 中已有记录，并选择合适的查询方式。
3. **总结**：OpenClaw 能调用 MindAtlas 对个人经历做基础周报 / 月报 / 主题总结。

本期不追求一步到位做复杂推荐、主动建议、强图谱推理或全自动知识提炼。重点是先把“记录闭环、查询闭环、总结闭环”打通。

---

## 2. 设计原则

### 2.1 职责边界

- **OpenClaw**：入口、任务理解、行为决策、沉淀策略、查询路由。
- **openclaw-mindatlas 插件**：能力桥接、catalog 拉取、工具注册、上下文透传、执行转发。
- **MindAtlas**：结构化存储、检索、关系、报告、知识图谱、后端治理能力。

### 2.2 任务级触发，不做消息级乱记

第一期自动沉淀只以“任务闭环”或“高价值结果”作为触发点，不按每条消息都判断，避免噪音。

### 2.3 先治理，再扩张

第一期所有自动记录都必须具备最基本的治理能力：

- 来源 metadata
- 轻量去重 / 幂等
- 记录模式区分（manual / auto / suggested）

### 2.4 Skill 负责策略，插件负责桥接

- 不把“是否值得记、什么时候查、怎么总结”的主逻辑塞进插件。
- 统一策略放在 OpenClaw skill / policy 层。

---

## 3. 第一期范围

### 3.1 In Scope

#### 记
- 用户显式要求记录时写入 MindAtlas。
- OpenClaw 在高价值任务收尾时自动沉淀经历。
- 自动记录支持基础去重和来源标记。

#### 查
- 支持关键词搜索记录。
- 支持按记录 ID 查看详情。
- 支持知识图谱 / RAG 查询（当 capability 可用时）。
- 支持按近期时间范围查找记录。

#### 总结
- 支持周报。
- 支持月报。
- 支持围绕主题 / 标签 / 项目的基础总结（如本期未完整实现能力，则先形成设计与路由预留）。

### 3.2 Out of Scope

第一期暂不重点投入：
- 全自动关系推荐
- 大规模 embedding 去重
- 自动知识提炼为 SOP
- 高级趋势分析
- 下一步行动建议
- 候选记忆大规模工作流

---

## 4. 第一期开模块

---

### 模块 A：OpenClaw Auto Capture Policy

**定位**：统一的“记”策略层。

**建议实现形态**：
- 第一阶段优先做成一个独立 skill，例如：`mindatlas-auto-capture`
- 后续如果策略复杂度上升，再升级为更正式的 orchestrator / workflow

**职责**：
- 判断是否值得记录
- 只在高价值任务收尾时触发
- 生成记录摘要
- 选择记录类型
- 调用近期检索做轻量查重
- 决定 create / upsert / skip

**第一期建议触发场景**：
- 安装 / 部署 / 配置完成
- 修复一个明确问题
- 完成一段较长任务
- 形成一个稳定结论
- 用户明确说“记一下 / 记住 / 帮我记录”

**第一期明确不自动记录的场景**：
- 闲聊
- 短问短答
- 尚未验证的猜测
- 情绪碎片
- 高重复信息

---

### 模块 B：OpenClaw Retrieval Policy

**定位**：统一的“查”策略层。

**建议实现形态**：
- 独立 skill，例如：`mindatlas-retrieval`

**职责**：
- 根据用户提问，路由到最合适的 MindAtlas capability
- 避免所有查询都简单落到同一个 search 接口

**推荐路由规则**：
- 明确关键词 / 类型 / 标签查询 → `mindatlas_search_entries`
- 已有记录 ID，查完整详情 → `mindatlas_get_entry`
- 跨多条记录提问 / 语义问题 → `mindatlas_query_knowledge_graph`
- “最近做了什么 / 这周都干了啥” → report 或 timeline summary 能力

---

### 模块 C：OpenClaw Summary Policy

**定位**：统一的“总结”策略层。

**建议实现形态**：
- 独立 skill，例如：`mindatlas-summary`

**职责**：
- 识别用户是在要“周报 / 月报 / 阶段总结 / 主题总结”
- 路由到 MindAtlas 报告与总结能力
- 统一总结输出风格

**第一期目标**：
- 周报：调用已有 `mindatlas_generate_weekly_report`
- 月报：调用已有 `mindatlas_generate_monthly_report`
- 主题总结：若本期暂未提供独立 capability，则先设计为：检索相关记录 + OpenClaw 端整合输出

---

### 模块 D：MindAtlas OpenClaw Ingestion Support

**定位**：MindAtlas 后端对 OpenClaw 自动记录的支撑层。

**职责**：
- 接住来自 OpenClaw 的写入
- 保存自动记录 metadata
- 支持轻量幂等 / upsert / merge
- 支持近期检索以辅助查重

---

### 模块 E：openclaw-mindatlas 插件增强

**定位**：保持桥接层干净，但补齐上下文透传与后续扩展位。

**第一期只做增强，不做策略逻辑。**

**职责**：
- 继续按 catalog 暴露工具
- 执行时透传更多上下文 header
- 做更清晰的错误映射和 stale catalog 提示

---

## 5. 数据与能力设计

### 5.1 Entry 类型建议（第一期）

建议确保至少有以下几类 Entry Type 可用：

- 经历（EXPERIENCE）
- 知识（KNOWLEDGE）
- 项目进展（PROJECT_PROGRESS）
- 问题（ISSUE）
- 解决方案（SOLUTION）
- 决策（DECISION）

第一期如果不想扩太多 UI，可先至少保证：
- EXPERIENCE
- KNOWLEDGE
- ISSUE
- DECISION

---

### 5.2 自动记录 metadata 建议

建议在 Entry 上增加或预留以下 metadata 字段（可放扩展 JSON 字段或独立表）：

- `source`: `openclaw`
- `captureMode`: `manual | auto | suggested`
- `channel`: 渠道名
- `sessionId`: OpenClaw 会话 ID
- `messageId`: 触发消息 ID（若可获得）
- `toolName`: 触发记录时所在工具名（若有）
- `confidence`: 自动记录置信度
- `fingerprint`: 轻量幂等键
- `taskType`: install / debug / summary / decision / note 等
- `createdByAgent`: agent 名称或标识

如果短期不想改 Entry 主表，第一期可以先：
- 用 `metadata_json` / `extra` / `source_payload` 风格的扩展字段承载
- 或新增一个 `entry_capture_meta` 辅助表

---

### 5.3 去重 / 幂等建议

第一期建议实现轻量版，不上复杂 embedding 去重。

**fingerprint 构成建议**：
- entry type
- sessionId（或 chat/session 主体）
- 时间桶（如按天或按小时）
- 标题归一化
- 摘要中的核心对象（工具 / 项目 / 主题）

**行为建议**：
- 相同 fingerprint 且时间接近 → 优先 merge / update
- 相似主题在短时间内重复触发 → skip 或 append 到已有记录

---

## 6. capability 演进建议

### 6.1 本期继续使用的现有 capability

- `mindatlas_capture_entry`
- `mindatlas_search_entries`
- `mindatlas_get_entry`
- `mindatlas_create_relation`
- `mindatlas_query_knowledge_graph`
- `mindatlas_generate_weekly_report`
- `mindatlas_generate_monthly_report`

### 6.2 第一期开建议新增 capability

#### A. `mindatlas_upsert_entry`

**原因**：自动记录比手动记录更需要幂等和合并。

**建议输入**：
- `fingerprint`
- `title`
- `summary`
- `content`
- `entryType`
- `tagNames`
- `timeAt` / `timeFrom` / `timeTo`
- `metadata`
- `mergeMode`（可选）

**行为**：
- fingerprint 已存在 → update / merge
- 不存在 → create

#### B. `mindatlas_search_recent_entries`

**原因**：给 OpenClaw 的自动沉淀做更便宜、更稳定的近期查重入口。

**建议输入**：
- `query`
- `entryType`
- `tagNames`
- `timeFrom`
- `timeTo`
- `limit`
- 可选 `source=openclaw`

#### C. `mindatlas_generate_topic_summary`（可选）

若第一期时间足够，建议增加这个能力；若来不及，可以先由 OpenClaw 做检索后整合。

**建议输入**：
- `topic`
- `tagNames`
- `timeFrom`
- `timeTo`
- `limit`

**输出**：
- summary
- highlights
- relatedEntries

---

## 7. OpenClaw skill 方案

### 7.1 `mindatlas-overview`

**定位**：认知型 skill

**职责**：
- 告诉 OpenClaw MindAtlas 是什么
- 告诉 OpenClaw 什么时候优先使用 MindAtlas
- 不承载具体记/查/总结策略细则

当前项目已具备该方向的文档基础，应继续保留。

---

### 7.2 `mindatlas-auto-capture`

**定位**：记录策略 skill

**建议内容**：
- 何时触发自动记录
- 哪些场景不应记录
- 先摘要再检索再写入
- 优先使用 `mindatlas_upsert_entry`，若不可用则回退到 search + capture
- 只在高价值任务收尾时触发，不按每条消息触发

**核心流程**：
1. 识别是否存在任务闭环
2. 提取经历摘要
3. 推断 entry type
4. 做近期查重
5. upsert / create / skip

---

### 7.3 `mindatlas-retrieval`

**定位**：检索策略 skill

**建议内容**：
- 如何在关键词搜索、详情查询、语义问答之间路由
- 如何在信息不足时先简短追问，而不是盲猜
- 检索结果返回时优先给结构化摘要，而不是原样抛所有字段

---

### 7.4 `mindatlas-summary`

**定位**：总结策略 skill

**建议内容**：
- 如何识别周报、月报、阶段总结、主题总结意图
- 优先使用 MindAtlas 报告能力
- 若没有现成 capability，则先检索后由 OpenClaw 进行整合输出

---

## 8. 代码与目录拆分建议

---

## 8.1 OpenClaw 插件侧

当前已有：
- `integrations/openclaw-mindatlas/`

第一期建议补充：

### A. 上下文 header 扩展
在当前已透传基础上，逐步增加（能拿多少拿多少，不强求一步到位）：
- `X-OpenClaw-Source`
- `X-OpenClaw-Channel`
- `X-OpenClaw-Session`
- `X-OpenClaw-Tool`
- 可选：`X-OpenClaw-Surface`
- 可选：`X-OpenClaw-Message`

### B. skill bundle 扩展
在插件 bundle 中逐步增加：
- `mindatlas-overview`
- `mindatlas-auto-capture`
- `mindatlas-retrieval`
- `mindatlas-summary`

> 注意：skill 负责策略，插件只负责随插件一并分发，不负责在运行时替 agent 做最终决策。

---

## 8.2 MindAtlas 后端侧

建议重点关注目录：
- `backend/app/openclaw_integration/`
- `backend/app/entry/`
- `backend/app/report/`
- `backend/app/relation/`

### A. `backend/app/openclaw_integration/`
第一期重点改造点：
- 为自动记录补 metadata 支持
- 增加 upsert / recent search 风格能力
- 保持 OpenClaw-facing schema 稳定

建议新增/扩展：
- `schemas.py`：补 upsert / recent search 输入输出 schema
- `registry.py`：注册新 system capability
- `service.py`：实现 upsert / recent search / metadata 处理

### B. `backend/app/entry/`
第一期重点改造点：
- Entry 模型支持 metadata / source 字段
- 支持按 fingerprint 查找或合并
- 搜索时支持 source / captureMode 等扩展过滤（可选）

### C. `backend/app/report/`
第一期重点改造点：
- 优化 weekly / monthly report 输出结构
- 预留 topic summary 能力

---

## 9. 第一阶段实施步骤（建议顺序）

### Step 1：定义自动记录 metadata 与 Entry 扩展方案

**目标**：先定数据承载，不然后面策略没有落点。

**产出**：
- metadata 字段设计
- fingerprint 方案
- captureMode 设计

**建议结果**：
- 支持 manual / auto / suggested
- 支持 source / session / channel / confidence / fingerprint

---

### Step 2：新增 `mindatlas_upsert_entry`

**目标**：让自动记录先有可用的幂等写入接口。

**原因**：
如果没有 upsert，OpenClaw 侧只能自己做 search + compare + capture，策略更脆。

**验收标准**：
- 同 fingerprint 的重复调用不会无脑生成新 Entry
- 支持 merge / update 基础行为

---

### Step 3：新增或增强近期检索能力

**目标**：给自动沉淀做查重支持，也给总结做最近记录抽取支持。

**建议优先级**：高

**验收标准**：
- 能在最近 1 天 / 7 天内按 query / type / tag 查记录
- 支持 limit，响应轻量稳定

---

### Step 4：设计并发布 `mindatlas-auto-capture` skill

**目标**：让 OpenClaw 真正知道“什么时候该记”。

**验收标准**：
- 用户明确说“记一下”时，优先使用 MindAtlas
- 在安装 / 配置 / 修复类任务完成后，可自动考虑记录
- 闲聊与短问答不主动记录

---

### Step 5：设计并发布 `mindatlas-retrieval` skill

**目标**：让 OpenClaw 知道“怎么查最合适”。

**验收标准**：
- 对不同查询意图能路由到 search / get / graph query
- 输出结果优先结构化摘要，不直接甩大 JSON

---

### Step 6：设计并发布 `mindatlas-summary` skill

**目标**：让 OpenClaw 知道“怎么总结”。

**验收标准**：
- “生成周报 / 月报”意图稳定命中相应 capability
- “总结我最近在做什么”时，能选择 report 或检索后整合输出

---

### Step 7：增强 weekly / monthly report 输出

**目标**：让“总结”不是流水账，而是能看。

**建议输出结构**：
- 本周期重点事项
- 主要主题
- 关键进展
- 遇到的问题
- 形成的经验 / 结论
- 可选：下一步建议（若不稳，本期可先不做）

---

## 10. MVP 验收标准

第一期完成后，至少满足以下场景：

### 10.1 记
- 用户说“帮我记一下今天把 OpenClaw 和 MindAtlas 集成好了”
- OpenClaw 能写入 MindAtlas
- 自动记录条目带来源 metadata
- 短时间重复触发不会创建大量重复记录

### 10.2 查
- 用户说“搜一下我有没有记过 OpenClaw 安装”
- OpenClaw 能调用正确 capability 并返回结构化结果
- 用户说“把那条详情给我看看”时可进一步拉详情

### 10.3 总结
- 用户说“生成本周周报”
- OpenClaw 能稳定调用周报能力
- 用户说“总结一下我最近在折腾什么”
- OpenClaw 能给出基于 MindAtlas 记录的总结结果

---

## 11. 风险与控制

### 风险 1：自动记录过多

**控制措施**：
- 只按任务闭环触发
- 第一期开高阈值
- 必须有去重

### 风险 2：记录质量不稳定

**控制措施**：
- 先限制在高价值、结构明确的场景
- Skill 中强调先摘要后写入
- 记录结构尽量收敛

### 风险 3：插件职责膨胀

**控制措施**：
- 插件不承载主策略
- skill 才负责记 / 查 / 总结行为规则

### 风险 4：后端 schema 漂移影响 OpenClaw 工具稳定性

**控制措施**：
- 保持 capability schema 稳定
- 对 catalog 变化给出 reload 提示
- 尽量避免频繁重命名 toolName

---

## 12. 建议的第一期交付物清单

### OpenClaw 侧
- [ ] `mindatlas-overview` skill（完善）
- [ ] `mindatlas-auto-capture` skill
- [ ] `mindatlas-retrieval` skill
- [ ] `mindatlas-summary` skill

### 插件侧
- [ ] 透传更完整上下文 header
- [ ] 优化 tool 描述与错误提示

### MindAtlas 后端侧
- [ ] Entry metadata 支持
- [ ] `mindatlas_upsert_entry`
- [ ] `mindatlas_search_recent_entries` 或等价增强
- [ ] weekly / monthly report 输出增强

### 文档侧
- [ ] OpenClaw 集成一期落地方案（本文）
- [ ] 后续在 docs/openclaw 中补 skill 文案草案
- [ ] 补部署 / 使用说明

---

## 13. 第二期预留方向（不在本期硬做）

- 候选记录层
- 自动关系推荐
- 项目 / 主题级总结
- 更稳的 topic summary capability
- 自动知识提炼
- 趋势分析与下一步建议

---

## 14. 一句话总结

第一期不要贪多，目标就是把这条链路做稳：

**OpenClaw 知道什么时候该记、什么时候该查、什么时候该总结；MindAtlas 负责把这些经历稳定存下来、找出来、汇总出来。**

只要这条链路打通，这套系统就已经具备长期演进成“个人经历操作系统”的基础。
