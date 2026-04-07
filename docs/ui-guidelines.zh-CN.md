# MindAtlas UI 规范

## 1. 文档定位

本文件是 MindAtlas 当前默认 UI 语言的内部规范基线，用于指导后续新页面、新组件和已有页面重构时的视觉与结构决策。

这份文档的目标不是重新设计一套新系统，而是把当前已经落地的 UI 语言整理成一套可复用、可检查、可直接照着实现的标准。

适用范围：

- 主应用外壳
- 常规业务页面与详情页
- 设置页与表单页
- 日历、助手等主路由体验
- 通用 UI 原子组件与浮层

当前不纳入本规范的范围：

- workflow-editor / graph canvas 这类强交互编辑器内部专用视觉规则
- 低频特殊管理面板中尚未完成统一的历史遗留局部样式

## 2. 真相源与使用原则

### 2.1 代码级真相源

以下两个文件是当前 UI 系统的代码级真相源：

- `frontend/src/components/ui/styles.ts`
- `frontend/src/index.css`

后续实现页面时，优先复用这里定义的 token、surface 和 layout 约定，不要复制散落 class，更不要在单页里发明一套新的圆角、阴影或玻璃配方。

### 2.2 文档与代码的关系

- 当文档和代码一致时，以文档指导开发。
- 当文档和代码暂时不一致时，以代码为准，并在后续 PR 中补齐文档。
- 修改全局 token、surface、radius、page layout 约定时，必须同步更新本文件。

## 3. 设计总原则

### 3.1 默认视觉方向

MindAtlas 当前默认视觉方向是：

- 浅白
- 专业
- 克制
- 轻层级
- 强结构

目标不是“炫”，而是让信息读取更轻松、页面更稳定、界面看起来像一个统一系统，而不是多个功能页面拼接起来的集合。

### 3.2 Surface 策略

核心原则：

- 主体内容使用实体卡片
- 轻毛玻璃只用于浮层
- 页面背景允许有非常轻的气氛层，但不能抢内容主体

实体卡片适用于：

- 页面主内容块
- 数据卡片
- 列表、详情、表单 section
- 工具栏容器
- 设置页内容面板

轻毛玻璃适用于：

- Dialog
- Popover
- Hover Card
- Confirm Dialog
- 浮动助手
- sticky 顶部 chrome

不要把以下内容做成玻璃：

- 主页面大面积内容区
- 核心信息卡
- 绝大多数列表项
- 复杂表单主面板

### 3.3 克制优先

设计上优先考虑：

- 信息层级清楚
- 结构稳定
- 边界可读
- 响应式不溢出
- light / dark 一致

不追求：

- 过强的 glow
- 重阴影
- 高饱和背景洗版
- 装饰性优先于信息
- 局部页面自创视觉语言

## 4. 语义颜色系统

当前项目使用 HSL 语义 token，不要求页面直接手写颜色值，而是通过语义 token 取得稳定的一致性。

### 4.1 核心语义 token

| Token | 语义职责 | 推荐用途 | 说明 |
| --- | --- | --- | --- |
| `background` | 全局底色 | app 背景、主底面 | 页面与主内容的基底颜色 |
| `foreground` | 主文字色 | 标题、正文、强强调文本 | 所有主要可读内容优先使用 |
| `card` | 卡片基底 | 实体内容卡 | 一般不直接手写，通常由 `uiSurface.card` 封装 |
| `card-foreground` | 卡片文字色 | 卡片内部正文 | 与 `foreground` 接近，但保留语义独立性 |
| `popover` | 浮层基底 | popover / hover card / 浮层内容 | 与 `float/modal` 家族相关 |
| `popover-foreground` | 浮层文字色 | 浮层内文本 | 保证浮层中的可读性 |
| `primary` | 主操作强调色 | 主按钮、主强调动作、选中态 | 不是装饰色，优先用于交互主路径 |
| `primary-foreground` | 主按钮前景色 | 主按钮文字 / icon | 与 `primary` 成对使用 |
| `secondary` | 次级背景色 | 次级操作、弱强调区域 | 用于次级按钮或辅助容器 |
| `secondary-foreground` | 次级前景色 | 次级按钮文字 | 与 `secondary` 成对使用 |
| `muted` | 弱化背景色 | 辅助区块、骨架弱底 | 常用于 `inset`、hover、次级容器 |
| `muted-foreground` | 弱化文字色 | 副标题、说明、meta 信息 | 不能替代主正文 |
| `accent` | 轻交互强调色 | hover、选中辅助底 | 常用于列表项 hover/active 的轻反馈 |
| `accent-foreground` | accent 前景色 | accent 区域文字 | 与 `accent` 配套 |
| `destructive` | 风险操作强调色 | 删除、危险确认、错误高亮 | 只用于风险语义 |
| `destructive-foreground` | 风险前景色 | 风险按钮文字 / icon | 与 `destructive` 成对 |
| `border` | 通用边界色 | 卡片边框、分隔线、输入边界 | 当前系统很依赖清晰但轻的边界 |
| `input` | 输入边界语义色 | input / textarea / select | 与 `border` 接近，但保留独立职责 |
| `ring` | focus ring 语义色 | 聚焦状态 | 用于键盘可达性和交互反馈 |

### 4.2 Light / Dark 角色原则

#### Light

- `background` 维持接近白色的稳定基底
- `foreground` 使用深 slate 方向，保证阅读清晰
- `muted` 与 `border` 负责提供轻层级，而不是强对比
- `primary` 是强操作焦点，不应滥用成页面大面积装饰色

#### Dark

- 不走纯黑路线，而是深 slate / deep navy 方向
- `background` 与 `card` 之间保持轻微层级差
- `border` 在 dark 中仍然重要，用于避免面板融成一片
- 毛玻璃浮层在 dark 中允许更明显，但仍以可读性优先

### 4.3 颜色使用边界

应当：

- 用语义 token 表达角色，而不是表达“看起来差不多”
- 让颜色服务于信息优先级和交互语义
- 在 light / dark 都检查边界、hover、focus 和 disabled 状态

不应当：

- 在页面里直接写一堆新的裸 HSL/rgba 颜色做主视觉
- 用 `primary` 当大面积背景装饰
- 用高饱和色块堆砌 dashboard / settings 主内容
- 用颜色代替结构

## 5. 结构化设计系统

### 5.1 圆角梯度

MindAtlas 圆角系统固定为五档：

| 层级 | 值 | 对应 token | 适用场景 |
| --- | --- | --- | --- |
| Shell | `24px` | `uiRadius.shell` | 外层 route shell、主大卡、模态外壳 |
| Panel | `20px` | `uiRadius.panel` | 主要内层卡片、普通面板 |
| Control | `16px` | `uiRadius.control` | 按钮、输入框、导航项、segmented 控件 |
| Inset | `12px` | `uiRadius.inset` | inset panel、小内嵌块、局部容器 |
| Pill | `full` | `uiRadius.pill` | badge、胶囊标签、圆按钮 |

执行规则：

- 页面级外壳优先使用 `24px`
- 内容卡片优先使用 `20px`
- 控件优先使用 `16px`
- 内嵌说明块优先使用 `12px`
- 不新增 `22px`、`28px`、`30px` 这类脱离系统的值

允许的局部例外：

- 日历 event chip 这类有起止边缘语义的组件可以做局部扩展
- 扩展时必须围绕 `16 / 12 / full` 体系，不得完全脱离主系统

### 5.2 阴影与层级梯度

当前系统固定四档 elevation：

| 层级 | Token | 适用场景 | 设计意图 |
| --- | --- | --- | --- |
| Base | `uiElevation.base` | 普通卡片、控件 | 提供轻微离面感，不抢层级 |
| Raised | `uiElevation.raised` | 外层 shell、大型面板 | 比普通卡片更稳定、更有承载感 |
| Float | `uiElevation.float` | Popover、HoverCard、浮动助手 | 清楚表现浮层，但不厚重 |
| Modal | `uiElevation.modal` | Dialog、确认弹窗、大型浮层 | 最强层级，承担遮罩后的主焦点 |

执行规则：

- 主内容卡用 `base` 或 `raised`
- 浮层统一走 `float` 或 `modal`
- 不允许页面自行定义一套新的阴影配方当“品牌感”

### 5.3 Surface 家族

Surface 由 `frontend/src/components/ui/styles.ts` 统一定义。

| Surface | Token | 说明 | 使用场景 |
| --- | --- | --- | --- |
| Page | `uiSurface.pageBackdrop` | 页面背景层 | 常规 route 背景 |
| Shell | `uiSurface.shell` / `uiChrome.shell` | 大卡级壳层 | route shell、主结构卡 |
| Card | `uiSurface.card` / `uiChrome.card` | 实体内容卡 | section、详情、列表卡 |
| Control | `uiSurface.control` / `uiChrome.control` | 控件表面 | 按钮、输入、segmented、toolbar 小壳 |
| Inset | `uiSurface.inset` / `uiChrome.inset` | 内嵌次级块 | 注释块、提示块、子模块块 |
| Float | `uiSurface.float` / `uiChrome.float` | 轻毛玻璃浮层 | popover、hover card、悬浮组件 |
| Modal | `uiSurface.modal` / `uiChrome.modal` | 模态浮层 | dialog、confirm dialog |

执行原则：

- `shell` 和 `card` 是主内容家族
- `control` 是交互家族
- `inset` 是主卡内部的次级容器
- `float` / `modal` 是唯一允许带明显 `backdrop-blur` 的浮层家族

### 5.4 Blur Policy

Blur 使用策略固定如下：

- `float` 可以使用 `backdrop-blur`
- `modal` 可以使用 `backdrop-blur`
- sticky 顶部 chrome 可以使用轻量 blur
- 其他主内容卡片不允许用 blur 冒充层级

这条规则非常重要。

实体卡片的层级来自：

- 结构
- border
- 轻阴影
- 适度底色差

而不是来自模糊背景。

## 6. 布局规范

### 6.1 Route 容器宽度

当前主应用 route 容器规范如下：

| 场景 | Token / 约定 | 说明 |
| --- | --- | --- |
| Dashboard、Entries | `uiLayout.page7` | 标准内容页，`max-w-7xl` |
| Settings、详情页、表单页 | `uiLayout.page6` | 更聚焦的信息页，`max-w-6xl` |
| Calendar、Assistant | route-owned full-height/full-bleed | 由页面自己控制全高与内滚动 |

执行规则：

- 普通新页面优先从 `page6` 或 `page7` 中选
- 不要每个页面都重新定义 `max-w-*`
- 只有明确需要全高交互体验时才走 route-owned

### 6.2 页面 Header 模式

标准页面 Header 统一为：

- 标题
- 可选副标题
- 可选右侧操作区

对应推荐组合：

- `uiLayout.headerRow`
- `uiLayout.headerBlock`
- `uiLayout.headerTitle`
- `uiLayout.headerSubtitle`

返回链接统一使用：

- `uiLayout.backLink`

不要：

- 自己发明新的标题字号体系
- 某些页面用 2xl，某些页面突然用 4xl，导致系统跳脱
- 把返回按钮做成和主操作一样重

### 6.3 间距默认值

当前页面密度的默认约定：

| 场景 | 默认值 | 说明 |
| --- | --- | --- |
| 页面主栈间距 | `space-y-6` | 页面 section 之间主间距 |
| Header 标题块 | `space-y-2` | 标题与副标题距离 |
| Header 左右分栏 | `gap-4` | 标题块与操作区距离 |
| 卡片常规内边距 | `p-6` | 详情、设置、主要内容卡 |
| 紧凑卡片内边距 | `p-5` | dashboard 摘要卡、列表壳层 |
| Inset 内边距 | `px-4 py-4` 或 `p-4` | 注释块、说明块、子面板 |
| 控件高度 | `h-10` | 标准按钮、输入框高度 |

不要把间距完全开放给页面自己决定。优先复用已有密度。

## 7. 组件家族规范

### 7.1 Page Shell / Section Card / Inset Panel

#### Page Shell

用于承接一个完整的大结构块。

推荐：

- `uiChrome.shell`
- 圆角 `24px`
- 比普通 card 稍强的稳定感

适用：

- 日历主壳
- 助手主聊天区域
- 大型 route 内容外壳

#### Section Card

这是最常见的内容容器。

推荐：

- `uiChrome.card`
- 圆角 `20px`
- 轻 border + 轻 shadow

适用：

- 设置页 section
- 详情页内容块
- dashboard 主要信息卡
- 列表与详情容器

#### Inset Panel

用于主卡内部次一级信息块。

推荐：

- `uiChrome.inset`
- 圆角 `12px`

适用：

- 说明区
- 提示区
- 子配置块
- 局部统计块

### 7.2 Button / Icon Button / Segmented Group

#### Button

统一使用 `frontend/src/components/ui/button.tsx`。

默认规则：

- 按钮圆角为 `uiRadius.control`
- 默认高度 `h-10`
- 语义 variant 保持：`default / secondary / outline / ghost / destructive / link`

使用建议：

- 主流程动作使用 `default`
- 次级操作优先 `outline` 或 `secondary`
- 纯文字次级操作再考虑 `ghost`
- 危险操作只用 `destructive`

#### Icon Button

图标按钮也属于 control 家族，应满足：

- 使用 `16px` 控件圆角或 `full`
- hover 背景与普通按钮一致
- icon 尺寸统一，避免忽大忽小

#### Segmented Group

分段切换器应采用：

- 外层使用 `uiChrome.control`
- 内部激活项使用 control 或轻 accent 背景
- 不应长得像另一套 tab 系统

### 7.3 Input / Textarea / Select / Switch

表单控件统一复用：

- `uiField.input`
- `uiField.textarea`
- `uiField.select`

规则：

- 默认圆角 `16px`
- 默认高度 `h-10`
- 统一 focus ring：`focus-visible:ring-[3px]` + `primary/10`
- 不要自行定义另一套输入框阴影和边框风格

Switch 要求：

- 与表单控件共享 focus 行为
- 外层说明卡优先搭配 `ToggleCard`

### 7.4 Badge / Pill

Badge 属于小尺寸信息标识，不承担大面积结构职责。

规则：

- 使用 `uiRadius.pill`
- 尽量保持轻量
- 用于状态、来源、标签、小型 meta

不要：

- 用 badge 模拟按钮
- 用很厚重的阴影让 badge 抢主层级

### 7.5 Dialog / Popover / HoverCard / Confirm Dialog

这些组件统一归入浮层语言。

#### Dialog / Confirm Dialog

使用规则：

- 外壳用 `uiChrome.modal`
- 遮罩用 `uiSurface.overlay`
- 允许轻毛玻璃
- 圆角优先 `24px`

#### Popover / HoverCard

使用规则：

- 外壳用 `uiChrome.float`
- 不要写成新的卡片语言
- 体量小，但层级要明确

#### Tooltip

Tooltip 保持简单、直接、可读：

- 不需要做成玻璃
- 不承担复杂布局和结构信息

### 7.6 Sidebar / Header / Route Top Chrome

这些区域属于 app shell，不是普通内容卡。

规则：

- 保持轻、稳定、可持续使用
- 使用清晰边界，不要做大面积高饱和底色
- sticky top chrome 可以有轻 blur，但不能有重 glow

Header 要求：

- 是结构 chrome，不是 hero 区
- 控件与页面内容风格统一

Sidebar 要求：

- active 和 hover 用同一套 control 语言
- 不要把 active 态做得像另一种产品

### 7.7 Empty State / Toolbar / Form Section / List Detail

#### Empty State

应轻量、克制：

- 优先说明状态和下一步动作
- 不要喧宾夺主

#### Toolbar

应看作一组 control，而不是一块独立视觉秀场。

推荐：

- 外壳可用 `uiChrome.card` 或 `uiChrome.control`
- 内部控件统一高度与圆角

#### Form Section

推荐结构：

- section card
- 标题 + 描述
- 表单内容
- inset 提示块
- 底部 actions

#### List / Detail

应属于同一组件家族：

- 列表页与详情页使用同一套 card 语言
- 详情弹窗与详情页面应有明显家族关系

## 8. 页面模板

以下模板可作为后续新页面的直接起点。

### 8.1 标准内容页模板

```tsx
import { uiChrome, uiLayout } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

export function ExamplePage() {
  return (
    <div className={uiLayout.page6}>
      <div className={uiLayout.headerRow}>
        <div className={uiLayout.headerBlock}>
          <h1 className={uiLayout.headerTitle}>页面标题</h1>
          <p className={uiLayout.headerSubtitle}>页面副标题说明</p>
        </div>
        <div className="flex items-center gap-2">{/* actions */}</div>
      </div>

      <section className={cn(uiChrome.card, 'p-6')}>
        内容区域
      </section>
    </div>
  )
}
```

适用于：

- settings detail
- entries detail
- 常规管理页
- 一般列表与详情组合页

### 8.2 表单 / 设置页模板

```tsx
import { uiChrome, uiLayout } from '@/components/ui/styles'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export function ExampleSettingsPage() {
  return (
    <div className={uiLayout.page6}>
      <div className={uiLayout.headerRow}>
        <div className="space-y-3">
          <button className={uiLayout.backLink}>返回</button>
          <div className={uiLayout.headerBlock}>
            <h1 className={uiLayout.headerTitle}>设置标题</h1>
            <p className={uiLayout.headerSubtitle}>设置说明</p>
          </div>
        </div>

        <Button>保存</Button>
      </div>

      <section className={cn(uiChrome.card, 'p-6')}>
        <div className="space-y-5">
          <div className="space-y-2">
            <h2 className="text-lg font-semibold text-foreground">分组标题</h2>
            <p className="text-sm leading-6 text-muted-foreground">
              分组说明
            </p>
          </div>

          <div className={cn(uiChrome.inset, 'p-4')}>
            辅助说明或受控子配置
          </div>
        </div>
      </section>
    </div>
  )
}
```

适用于：

- automation / lightrag / docling / system setup
- 表单驱动页面
- 分组设置页

### 8.3 浮层模板

```tsx
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { uiChrome, uiSurface } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

export function ExampleOverlay() {
  return (
    <Dialog open>
      <DialogContent className="max-w-xl">
        <div className="space-y-4">
          Dialog 内容
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function ExampleCustomOverlay() {
  return (
    <div className={cn(uiSurface.overlay, 'fixed inset-0 flex items-center justify-center')}>
      <div className={cn(uiChrome.modal, 'w-[min(40rem,calc(100vw-2rem))] p-6')}>
        自定义模态层内容
      </div>
    </div>
  )
}

export function ExamplePopoverShell() {
  return (
    <div className={cn(uiChrome.float, 'w-80 p-4')}>
      Popover 内容
    </div>
  )
}
```

适用于：

- dialog
- confirm dialog
- popover
- hover card
- 浮动助手小窗

## 9. 新页面实现流程

后续做一个新页面时，建议按下面顺序决策：

1. 先判断页面属于 `page7`、`page6` 还是 route-owned full-height。
2. 再判断主体结构是 `shell` 还是 `card`。
3. 页面内部的二级区块统一优先用 `card` 或 `inset`。
4. 所有输入类控件优先复用 `uiField`。
5. 所有浮层优先复用 `float` / `modal`。
6. 若想写新的圆角、阴影、玻璃效果，先回到本规范判断是否真的有必要。

## 10. Do / Don’t

### 10.1 Do

- 优先复用 `uiChrome`
- 优先复用 `uiLayout`
- 优先复用 `uiField`
- 优先复用 `uiSurface`
- 用语义 token 表达层级与用途
- 让页面结构先成立，再做视觉修饰
- 检查 light / dark 下边界、焦点、hover 和 disabled 状态
- 检查窄屏下是否有横向溢出或局部裁切

### 10.2 Don’t

- 不要再写 `rounded-[28px]`、`rounded-[22px]` 这类脱离系统的值
- 不要把主内容卡做成玻璃
- 不要为单个页面自造阴影配方
- 不要为了视觉效果引入会造成溢出或横向滚动的外发光、负边距或绝对定位装饰
- 不要把 dashboard、settings、entries、calendar、assistant 各做成不同视觉语言
- 不要用过饱和色块覆盖大面积主内容
- 不要把副信息做得比主信息更重

## 11. 自查清单

新页面或新组件提 PR 前，至少自查以下问题：

- 是否用了 `uiLayout.page6` / `uiLayout.page7` 或明确 route-owned 结构？
- 是否优先复用了 `uiChrome.card/control/inset/float/modal`？
- 是否出现了脱离系统的圆角值？
- 是否手写了新的大面积阴影配方？
- 主内容是否误用了玻璃效果？
- light / dark 下是否都可读？
- 是否有横向滚动、阴影溢出、内容裁切或 sticky 区重叠问题？
- 是否和已有 dashboard / entries / settings / calendar / assistant 看起来属于同一产品？

## 12. 维护规则

- 本文档是当前 UI 系统说明书，不是重新设计提案。
- 当全局 token、surface、radius、page layout 约定发生变化时，必须在同一个 PR 中更新本文档。
- 新页面落地时，如发现文档无法支撑实现，应优先补充文档，再扩展系统。
- 若出现真实业务需要的例外，优先在共享 token 中新增受控能力，不要直接在页面里写一次性样式。

## 13. 附录：当前关键导出

当前开发时最常用的共享导出包括：

- `uiRadius`
- `uiElevation`
- `uiSurface`
- `uiChrome`
- `uiField`
- `uiLayout`

推荐优先组合：

- 页面：`uiLayout.page6/page7`
- 大壳层：`uiChrome.shell`
- 主内容卡：`uiChrome.card`
- 控件壳：`uiChrome.control`
- 内嵌块：`uiChrome.inset`
- 浮层：`uiChrome.float`
- 模态：`uiChrome.modal`

如果一个页面无法被这些组合覆盖，应先判断是：

- 真的需要新增共享能力

还是：

- 页面本身在偏离统一系统
