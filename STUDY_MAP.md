# TicketPilot 代码熟悉作战地图

> 基于 2026-09 全量代码勘察（38 个 .py 文件，约 6000 行）。
> 用法：按「链路」读，不按「文件」读。检验标准：对每条链路，能回答"这一环挂了会发生什么"。

---

## 〇、三步走

| 步骤 | 时间 | 做什么 |
|---|---|---|
| 1. 删尸体 | 半天 | 按第一节清单删死代码，删完跑通四条链路确认没炸 |
| 2. 走链路 | 1-2 天 | 四条链路各画一张手画时序图，边看第二节边走 |
| 3. 写卡片 | 半天 | 每个模块写三行职责卡片（模板见第四节），面试前背卡片 |

---

## 一、死代码删除清单（约 500+ 行，占全项目 10%）

删除前用全局搜索确认零引用，删除后跑主流程。

| 文件 | 位置 | 内容 |
|---|---|---|
| `agent/scheduler.py` | **整个文件（136 行）** | 通用 TaskScheduler，全项目无人 import；与 broadcast_scheduler 重复同一套 APScheduler 样板 |
| `agent/broadcast_manager.py` | 227-284 | `get_upcoming_reminders` + `format_reminder_message`，提醒功能的第二份实现，零调用（实际生效的是 broadcast_scheduler._create_ticket_reminders） |
| `agent/notifier.py` | 86-142 | `send_ticket_reminder` / `send_order_status_update` / `get_notifier`，零调用（提醒格式化的第三份实现） |
| `core/prompts.py` | 44-54, 166-243 | `ROUTER_PROMPT` + 全部手写字典工具 schema（约 100 行），零引用；活的 schema 在 tools/base.py 装饰器注册里 |
| `core/llm.py` | 87-111 | `chat_stream`，零调用 |
| `core/utils.py` | 59-92 | `extract_number`，零调用（其中文数字表还和 app.py:489-493 重复） |
| `core/privacy.py` | 116-138 | `get_masked_order_info`，零调用；`mask_id_card` 被 app.py:25 import 后从未调用 |
| `core/router.py` | 36-45 | `ORDER_PATTERNS`，零引用（同一批关键词在 `_is_order_content` 92-93 又内联抄了一份——留那份） |
| `data/models.py` | 40-74 | `Customer` / `EventReminder` / `BroadcastEvent` 三个模型零使用 |
| `data/broadcast_db.py` | 129-148, 163-175 | `get_tomorrow_events` / `get_today_events` / `get_pending_events` / `cleanup_old_events`，零调用 |
| `data/constants.py` | 24, 27 | `PLATFORMS` / `ORDER_STATUS_OPTIONS`，零引用 |
| `tools/time_utils.py` | 14-15 | `import socket, struct` 未使用（手写 NTP 的遗留） |
| `config.py` | 28, 39, 49 | `DATABASE_PATH` / `CHROMA_DB_PATH` / `LOG_LEVEL` 定义了没人用（要么接线要么删） |

⚠️ 特殊情况——**不可达的活链路**：`broadcast_scheduler.process_broadcast_after_forward`（81-107）零调用，但它是"转发公众号→wechat_parser 解析→broadcast_db 入库"整条链路的唯一入口。这不是死代码，是**没接线的功能**：要么在 UI/API 上加入口，要么整条链一起删。面试被问到 wechat_parser 时要想清楚说哪个。

---

## 二、四条链路

### 链路 A：聊天主流程（先走这条，我带你走一遍）

```
用户输入
  ├─ Streamlit 入口: frontend/app.py:274 st.chat_input
  │    → 296 router.classify_intent(user_input, context)   ← LLM 主路径
  └─ FastAPI 入口: api/routes.py:72 POST /chat
       → 87-90 use_llm_router 默认 False
       → router.classify_intent_simple(user_input)          ← 关键词路径

router.classify_intent (core/router.py:289-300)
  → classify_intent_with_llm (240-286, 用 router.py 内部的 INTENT_CLASSIFY_PROMPT 162-237)
  → 异常时回退 classify_intent_simple (105-158, 纯关键词)

五个意图分支（app.py 299-898 / routes.py 93-226 各写一遍）：
  ORDER_PARSE   → order_manager.parse_order_from_text (agent/order_manager.py:20-57)
                  → LLM 按 prompts.ORDER_PARSE_PROMPT 抽 JSON → Order 模型 → Database.save_order
  EVENT_QUERY   → llm.chat(tools=...) 工具调用循环（routes.py:131-152 / app.py:413-430, 865-882 共三份）
                  → LLM 不调工具则降级：直接 execute_tool("search_event") → 解析 data_source
                  → 拼 source_hint（大麦=可信 / web=仅供参考）→ 二次 llm.chat
                  （降级逻辑两份：routes.py:154-190 vs app.py:432-458，文案逐字相同）
  KNOWLEDGE_QA  → rag.retriever.retrieve_from_knowledge → 拼进 system prompt → llm.chat
  ORDER_MANAGE  → app.py:469-478 用第二套关键词再分 delete/edit/query 子意图
                  （与 router.py:126-130 的 manage_keywords 大量重叠——两层各分类一次）
  GENERAL       → 直接 llm.chat
```

**走的时候盯住的坑**：
1. 两个入口默认分类器不同（app=LLM，API=关键词）——同一产品两种行为，典型"复制后漂移"。
2. 工具调用循环三份、EVENT_QUERY 降级两份——改一处忘两处的事故温床。
3. router.py:141-146 艺人+日期+票价→ORDER_PARSE 的启发式，和 app.py 的 ORDER_MANAGE 子分类，都是"关键词分类"，散落两层。
4. `main.py:40-45` 用 reload=True 起 uvicorn，子进程只执行 routes.py:21 的 import（event_search+time_utils），**API 进程里实际只注册了 3 个工具**——main.py:16 import 的 6 个工具模块在 API 侧不生效。这是个真 bug，能讲出来是巨大加分项。

**面试问答点**："为什么两个入口？"→ 诚实版：先做 Streamlit 单体跑通，后加 API 时照抄了一遍，没抽公共层；代价是 ~200 行强耦合复制。"怎么改？"→ 抽 `core/chat_service.py` 收编整条编排链，routes.py 和 app.py 退化成薄适配器（详见第五节辩护卡）。

### 链路 B：订单（文本/截图 → 数据库）

```
文本: app.py/routes.py → order_manager.parse_order_from_text (20-57)
      用 prompts.ORDER_PARSE_PROMPT (prompts.py:60-160) → Order 模型 (data/models.py)
图片: tools/order_parser.py → parse_order_image (97-142) → 视觉模型
另一条平行管线: tools/order_parser.parse_order_text (66-94)
      用本文件私有 PARSE_PROMPT (29-63) → 输出另一套 schema (event_info/viewer_info/phone)
落库: order_manager.save_order → data/database.py Database (CRUD)
脱敏: core/privacy.py mask_phone / mask_pii_in_text（app.py:353, 365 在用）
```

**盯住的坑**：
1. **同功能两条管线**：order_manager（routes.py:94 在用）vs order_parser（工具注册表在用），不同 prompt、不同输出结构。面试必问"订单解析怎么做的"，你要能说清现在有两套、该收敛成哪套。
2. `parse_order_text`(66-94) 和 `parse_order_image`(97-142) 核心 12 行只差 1 行错误文案——应抽 `_parse(prompt, content)`。
3. JSON 提取正则 `r'```(?:json)?\s*([\s\S]*?)```'` 写了 4 遍（core/utils.py:34、order_parser.py:86,134、wechat_parser.py:118），app.py:802-804 还有第 5 种手写切法。utils.py 自称"避免代码重复"，但没人用它（因为它只支持 `{...}` 不支持数组——这是它被绕开的原因，也是它该修的地方）。
4. `database.py:107-121` 和 `141-155`：13 行的 Order 重建逻辑同文件内原样抄两遍，缺 `_row_to_order`。
5. order_manager.py:125-167 全是透传/一行包装（save/update/delete/get + 5 个 mark_*），问自己：这层存在的意义是什么？

### 链路 C：演出查询 + 时间

```
search_event 工具 (tools/event_search.py)
  → DataSourceManager 聚合数据源:
      DamaiDataSource (大麦播报站, 被实例化 3 处: 478 / 561 / broadcast_manager.py:26)
      TavilyDataSource (联网搜索, event_search.py:346 直接 os.getenv("TAVILY_API_KEY") 绕过 config)
      WenhuaDataSource (314-328, search 返回 []，纯占位，注册被注释)
      MockDataSource (51-86, 注册被注释，仅测试用)
  → 返回 list[dict]，带 data_source 字段 → 调用方拼 source_hint 区分可信度
时间: tools/time_utils.py → ntplib 走 NTP 服务器校时 (get_accurate_time)
  → 只有 broadcast_manager / broadcast_scheduler 用它（6 处）
  → 全项目其余 28 处用裸 datetime.now()
```

**盯住的坑**：
1. **时间源分裂**：`broadcast_db.get_tomorrow_events` 用 datetime.now() 算"明天"，`broadcast_manager.py:76-77` 用 NTP 校准时间算同一个"明天"——机器时钟不准时两边结果不同。这是"为什么联网查时间"的完整答案：抢票场景对时钟敏感，本机时间可被篡改/漂移，所以关键路径走 NTP；坑在于只有一半路径接了。
2. `CITY_MAP`（event_search.py:131-143）11 个城市全映射到同一个 "852"——占位没做完。
3. 城市列表 **3 份且不一致**：constants.py:8-13（40 城）、order_manager.py:73-75（26 城）、app.py:194-197（33 城）。constants.py 自称"集中管理"，另外两处没引用它。
4. 错误返回约定四种并存（list 包 error / dict 包 error / success:False / 纯文本），调用方被迫写 `len(events)==1 and "error" in events[0]`（broadcast_scheduler.py:95）这种脆弱判断。

### 链路 D：定时播报

```
main.py 启动 → broadcast_scheduler (agent/broadcast_scheduler.py)
  BackgroundScheduler (APScheduler):
    cron 20:00 → broadcast_manager.generate_evening_report → notifier.send_markdown → 企业微信 webhook
    cron 08:00 → broadcast_manager.cross_check_with_damai (169 行, 调 damai.search("", None))
    动态任务   → _create_ticket_reminders (129-182) 按 sale_time 建一次性提醒 → _send_ticket_reminder (184-210)
知识库(链路C的旁支): rag/loader.py 读 knowledge_base/*.md → retriever.py SimpleRetriever 关键词检索
  （文件头声称"支持 ChromaDB 向量检索"，实际没有——面试别吹这句）
```

**盯住的坑**：
1. 提醒功能**三份实现**（broadcast_manager 轮询版=死、broadcast_scheduler APScheduler 版=活、notifier 格式化版=死），消息模板近乎相同。删掉两份死的（第一节清单里）。
2. `cross_check_with_damai` 和工具 `get_damai_broadcast`（event_search.py:563）是通往同一大麦 API 的两条平行路径。
3. **"定时播报有没有更好方案"的标准答案**：单机单进程，APScheduler 就是正解（进程内、零依赖、cron 表达式够用）。要能主动说出它的边界：多实例部署会重复触发（需要分布式锁或换 Celery Beat）、进程重启丢动态任务（现在 _create_ticket_reminders 建的 job 在内存里，重启后靠 08:00 任务重建吗？去看代码确认）、任务量大要换消息队列延迟消息。能讲清"为什么现在不用 + 什么时候必须换"，比换成 Celery 更加分。
4. retriever.py:102 返回文案"未在知识库中找到"被 knowledge_qa.py:41 用中文字符串匹配判断失败——文案即接口，改一个字就断。

---

## 三、全局横切问题（面试官扫一眼就能看出的）

| 问题 | 事实 | 一句话整改 |
|---|---|---|
| 日志双轨 | print 26 处 vs logging 16 处，app.py 同文件混用 | 全量换 logging，入口统一 basicConfig |
| 单例样板 | `global _x; if None...` 抄 5 遍 | functools.lru_cache 或统一 get_x() 模块 |
| try/except 配方 | "捕获 Exception→返回 {error}" ≥8 处；裸 except 1 处（routes.py:170）；静默吞异常 2 处 | 工具层统一装饰器包错误 |
| 工具返回即 json.dumps | 26 处序列化，调用方同进程再 loads 回来 | 工具直接返回 dict，仅 LLM 边界序列化 |
| sys.path.insert | 6 份 | 正规打包（pyproject.toml + pip install -e .） |
| prompt 散落 | prompts.py 之外还有 6+ 份内联 prompt（wechat_parser/order_parser/router/app.py 546-579, 639-662, 769-793） | 全部收编 prompts.py，或反过来全放使用处——选一个原则并说得出为什么 |

---

## 四、职责卡片（模板 + 示例）

格式固定三行：**做什么 / 谁调它 / 坑是什么**。面试被问到任何模块 = 背卡片 + 坑 + 一句整改。

> **router.py**：意图分类统一入口，五类意图。app.py 调 `classify_intent`（LLM 主、异常回退关键词），routes.py 默认调 `classify_intent_simple`（纯关键词）——两个入口行为不一致。坑：两套实现只是故障降级式串联，不是成本优化式分层；关键词表内联抄两份。整改：关键词高置信命中先短路，未命中走 LLM；词表收敛一份。

自己给这些模块各写一张：`llm.py`、`prompts.py`、`database.py`、`broadcast_db.py`、`order_manager.py`、`broadcast_scheduler.py`、`notifier.py`、`event_search.py`、`time_utils.py`、`retriever.py`、`app.py`、`routes.py`。

---

## 五、面试辩护速查

1. **"代码大量冗余"总攻**：不辩解，主动交代三条轴线——① API/前端两个入口各写一遍聊天编排（~200 行复制，最贵）；② 数据层无基类，连接/CRUD 样板成对复制；③ "集中管理"模块（prompts/constants/utils/config）建了但调用方绕开内联，形成名义单一来源+事实多份副本，且已漂移（3 份城市列表不一致、2 种"明天"算法）。收尾给整改顺序：先删 500 行死代码 → 抽 chat_service 收敛双入口 → 数据层抽基类 → 常量/prompt 归一。**能自己说出冗余在哪并给整改顺序，"冗余"这个指控就从挂点变成加分点。**
2. **Streamlit 是不是玩具**：内部工具/demo/看板场景它是对的选择，开发效率碾压前后端分离；真问题不是 Streamlit，是 1098 行表现层文件里塞满业务逻辑。逻辑长错层，换 React 也没救。
3. **五个意图要不要 function calling**：意图路由（选流程）和工具调用（LLM 自主选函数）是两个层次。五分支固定流程用显式路由更可控、可测试、便宜；function calling 适合开放工具集。现在项目里 EVENT_QUERY 分支内部已经在用工具调用了——"外层显式路由 + 内层 function calling"是说得通的混合架构，要能主动这样讲。
4. **NTP 校时**：抢票对时钟敏感，本机时间可漂移/篡改，关键路径走 ntplib；坑是只有播报模块接了，其余 28 处裸 datetime.now()，"明天"有两种算法。
5. **被问到不记得的细节**：说"这块我需要看代码确认，但它的上下游是 X 和 Y"——给链路位置，不编造。编造（"系统会优先艺人信息吗"式回答）是面试死刑。
