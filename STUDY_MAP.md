# TicketPilot 代码熟悉作战地图

> 基于 2026-09 全量代码勘察；**同月完成全量修复**，本文已改写为修复后的现状。
> 标注约定：✅ = 已修复（附落点）；⚠️ = 遗留问题（知道、说得出、暂未修）。
> 用法：按「链路」读，不按「文件」读。检验标准：对每条链路，能回答"这一环挂了会发生什么"。

---

## 〇、修复总览（原来的"三步走"已完成）

| 步骤 | 状态 | 落点 |
|---|---|---|
| 1. 删尸体 | ✅ 完成 | 死代码清单全删（scheduler.py 整文件、三份提醒实现中的两份死实现、私有 PARSE_PROMPT 管线、未用模型/函数/常量），并顺手修好被死代码掩盖的 2 个 router 分类测试失败 |
| 2. 走链路 | ✅ 完成 | 四条链路重画如下（第二节），关键决策全部落在 application/chat_service.py |
| 3. 架构收敛 | ✅ 完成 | 新增 application 编排层：app.py 1103→476 行、routes.py /chat 约160行→2行，两入口退化为薄适配器；播报链接线；工具注册集中化 |

修复的提交序列（每步独立可回滚）：基线死代码清理 → DB 路径锚定 → 断循环导入 → extract_json 数组化 → 纯删除批 → 知识库契约 → 订单管线收敛 → 工具注册集中 → classify_order_action → chat_service → API 切换 → Streamlit 切换 → 播报接线 → 调度器重启补偿。

分层规则（新增，必须能背）：**application 可以 import 任何下层（core/data/tools/agent/rag）；除入口（app.py/routes.py/main.py）和 tests 外，任何模块不得 import application；agent 不得模块级 import tools（tools/broadcast_tools 反向依赖 agent，方法内惰性导入解环）。**

---

## 一、死代码删除清单（已全部执行 ✅）

原清单约 500+ 行（占全项目 10%），已全部删除，此处只留结论防复述过时信息：

- `agent/scheduler.py` 整文件、`broadcast_manager` 轮询版提醒、`notifier` 格式化版提醒——提醒功能只剩 broadcast_scheduler 的 APScheduler 一份实现（活的）。
- `core/prompts.py` 手写字典工具 schema、`chat_stream`、`extract_number`、`get_masked_order_info`、`mask_id_card`/`mask_name`、`ORDER_PATTERNS`、`Customer`/`EventReminder`/`BroadcastEvent` 模型、`broadcast_db` 四个零调用查询、`PLATFORMS`/`ORDER_STATUS_OPTIONS`、time_utils 未用 import——全删。
- `config.py`：`DATABASE_PATH` **接线复活**（database.py/broadcast_db.py 默认路径锚定 BASE_DIR，修复"从 frontend/ 启动会建空库"）；CHROMA_DB_PATH/LOG_LEVEL 删除。
- 原"⚠️ 不可达的活链路" `process_broadcast_after_forward`：✅ **已接线**——解析/入库/报告下沉为 `BroadcastManager.process_forwarded_content`（不推送），scheduler 委托它并保留推送，Streamlit 新增「📢 播报解析」页作为 UI 入口。

---

## 二、四条链路（修复后现状）

### 链路 A：聊天主流程

```
用户输入
  ├─ Streamlit 入口: frontend/app.py 聊天页
  │    格式选择特判（"默认"等字面量）→ 直接回复，不进编排层
  │    其余 → st.session_state.chat_service.chat(user_input, context)
  └─ FastAPI 入口: api/routes.py POST /chat
       → chat_service.chat(message, use_llm_router=默认 True)

ChatService.chat (application/chat_service.py)
  → router.classify_intent（LLM 主路径，异常回退 classify_intent_simple 关键词）
  → 五个 _handle_*，返回 ChatResult(reply, intent, route, orders)

  ORDER_PARSE   → order_manager.parse_multiple_orders → 逐单 save_order
                  → 回填 order.id → format_order_masked 脱敏回复
                  → build_sale_alerts 追加今明开抢提醒（只提醒新单）
  EVENT_QUERY   → llm.chat(tools=全量12个) 工具调用循环（_run_tool_loop，单轮）
                  → LLM 不调工具则降级：execute_tool("search_event") → _build_source_hint
                    （大麦=可信 / web=仅供参考+只列主流平台）→ 二次 llm.chat
  KNOWLEDGE_QA  → retrieve_from_knowledge（空串=无结果）→ 拼 system prompt → llm.chat
                  空结果时给 LLM 明确指令"告知暂无信息，不要编造"
  ORDER_MANAGE  → router.classify_order_action 子分类 delete/query/edit/summary
                  （关键词规则与一级分类同在 router.py，两层职责不同：选流程 vs 选操作）
  GENERAL       → llm.chat(tools=...) + _run_tool_loop
```

**原坑清单 → 现状**：
1. ✅ 两入口默认分类器不同 → 统一走 `classify_intent`（API 的 `use_llm_router` 默认已切 True）。
2. ✅ 工具循环三份、降级两份 → 各收敛为 chat_service 一份（`_run_tool_loop` 保持单轮语义，坏 arguments 降级空参数不再 500）。
3. ✅ 关键词分类散落两层 → ORDER_MANAGE 子分类迁入 router.py（`classify_order_action`），词表一处维护；一级/二级两层保留（职责不同，计划内决策）。
4. ✅ API 进程只注册 3 个工具 → `tools/__init__.py` 集中注册，任何入口 `import ticketpilot.tools` 即全量 12 个（/tools 端点可验证）。

**面试问答点（更新）**："为什么曾经两个入口各写一遍？"→ 诚实版：先做 Streamlit 单体跑通，后加 API 时照抄，没抽公共层，代价是 ~600 行漂移复制。"怎么改？"→ **已经改完**：抽 `application/chat_service.py` 收编整条编排链，两入口退化为薄适配器；能讲出分层规则（谁能 import 谁）和实例化策略（Streamlit 实例放 session_state 而非模块单例，因为 rerun 换线程）。

### 链路 B：订单（文本/截图 → 数据库）

```
文本: chat_service._handle_order_parse → order_manager.parse_multiple_orders
      （constants.CITIES 40 城拆单）→ parse_order_from_text
      用 prompts.ORDER_PARSE_PROMPT → Order 模型 → save_order → 回填 id
图片: tools/order_parser.parse_order_image → 视觉模型 + 同一个 ORDER_PARSE_PROMPT
      → Order(**data) → 自动落库 → 返回 order_id + format_order_masked 脱敏文本
落库: data/database.py Database（CRUD，默认路径 config.DATABASE_PATH，支持 :memory:）
脱敏: core/privacy.py mask_phone / mask_pii_in_text
      （电话仅 len==11 才 mask——修复"待补充"→"***"bug；ORDER_PARSE 回复、
       ORDER_MANAGE 查询分支观影人行、订单管理页三处全脱敏）
```

**原坑清单 → 现状**：
1. ✅ 同功能两条管线 → order_parser 私有 schema 管线（PARSE_PROMPT/parse_order_text/save_order 工具）已删，全项目只有 Order 模型一套；图片解析并入同一 prompt。
2. ✅ parse_order_text/parse_order_image 复制 → 抽 `_parse(messages) -> dict` 统一 JSON 提取。
3. ✅ JSON 提取 5 种写法 → `core/utils.extract_json` 统一（支持 `{...}` 和 `[...]`、代码块和裸 JSON），order_manager/order_parser/router/chat_service 全部改用。
4. ⚠️ database.py 两处 13 行 Order 重建复制仍在（缺 `_row_to_order`）——小面积重复，未列入本轮范围。
5. ⚠️ order_manager 透传层保留——面试辩护："预留校验/事件扩展点"，说得出口即可。

### 链路 C：演出查询 + 时间

```
search_event 工具 (tools/event_search.py)
  → DataSourceManager 聚合数据源（get_source(name) 公开访问）:
      DamaiDataSource (大麦播报站) / TavilyDataSource (联网搜索,
      api_key 走 config.TAVILY_API_KEY，构造参数语义 api_key is not None)
      Wenhua/Mock 注册保持注释（占位/测试用）
  → 返回 list[dict]，带 data_source 字段 → chat_service._build_source_hint 区分可信度
时间: tools/time_utils.get_accurate_time（NTP，5 分钟缓存，失败降级系统时间）
  → 所有业务时间计算（开抢匹配/提醒/播报日期/调度补偿）统一走它
```

**原坑清单 → 现状**：
1. ✅ 时间源分裂 → chat_service（大麦缓存、开抢匹配）与播报链全部走 `get_accurate_time`；剩余裸 `datetime.now()` 只在存储时间戳（created_at/parsed_at）和日志打印——不影响业务日期计算。能讲清"为什么联网查时间"：抢票对时钟敏感，本机时间可漂移/篡改。
2. ⚠️ `CITY_MAP` 仍是 11 城 → "852" 全国兜底占位，已加诚实 TODO（无真实城市 ID 数据源，计划外）。
3. ✅ 城市列表 3 份不一致 → 统一 `data/constants.CITIES`（40 城）：router 分类、order_manager 拆单、chat_service 艺人前缀剥离同源。
4. ⚠️ 错误返回约定四种并存 → 未统一（涉及所有工具+调用方，单独立项）；`process_forwarded_content` 已改返回结构化 `{"success", ...}` 作为新代码示范。

### 链路 D：定时播报

```
main.py 启动 → broadcast_scheduler (agent/broadcast_scheduler.py)
  BackgroundScheduler (APScheduler):
    cron 20:00 → 提醒转发公众号内容 → notifier.send_markdown → 企业微信 webhook
    cron 08:00 → cross_check_with_damai（对比播报 vs 大麦 API）
    动态任务   → _create_ticket_reminders 按 sale_time 建开票前 10 分钟一次性提醒
    start()    → 立即补调 _create_ticket_reminders（重启补偿，job id +
                 replace_existing 幂等，过期自动跳过）✅
转发内容处理:
  scheduler.process_broadcast_after_forward → manager.process_forwarded_content
    （wechat_parser 解析 → broadcast_db 入库 → generate_evening_report）→ 推送
  Streamlit「📢 播报解析」页 → 同一个 process_forwarded_content → 只展示不推送
知识库(旁支): rag/loader.py 读 knowledge_base/*.md → retriever.py SimpleRetriever
  关键词检索；文件头已改诚实（不再声称 ChromaDB）；契约：空串=无结果 ✅
```

**原坑清单 → 现状**：
1. ✅ 提醒三份实现 → 只剩 APScheduler 一份；推送只在 scheduler，UI 页解析不轰炸群。
2. ⚠️ `cross_check_with_damai` 与工具 `get_damai_broadcast` 仍是通往同一大麦 API 的两条平行路径（一个走 manager.damai 惰性属性，一个走 DataSourceManager）——已知、可解释，未合并。
3. ✅ 进程重启丢动态任务 → start() 重启补偿已接；面试标准答案不变：单机单进程 APScheduler 是正解，边界是多实例重复触发（要分布式锁/Celery Beat）、任务量大换消息队列延迟消息。
4. ✅ retriever"文案即接口" → 改为空串契约，knowledge_qa/chat_service 判空串，不再字符串匹配中文文案。

---

## 三、全局横切问题（现状）

| 问题 | 状态 | 说明 |
|---|---|---|
| 日志双轨 | ⚠️ 遗留 | print 仍在 scheduler/main；app.py 分支删除后混用面已缩小；整改方向：全量 logging + 入口 basicConfig |
| 单例样板 | ✅ 部分收敛 | 工具注册收敛到 tools/__init__；broadcast_tools 改 `_get_manager()` 惰性单例；其余 `global _x` 样板保留 |
| try/except 配方 | ⚠️ 遗留 | 工具层统一错误装饰器未做（与错误契约统一同批立项）；但 chat_service 工具循环已兜住坏 arguments |
| 工具返回即 json.dumps | ⚠️ 遗留 | 同进程 loads 回来的浪费仍在，未动（改动面大） |
| sys.path.insert 多份 | ⚠️ 遗留 | 正规打包（pyproject.toml + pip install -e .）未做 |
| prompt 散落 | ✅ 部分收敛 | app.py 三份内联 prompt 随分支迁入 chat_service（仍内联在使用处）；order_parser 图片解析并入 prompts.ORDER_PARSE_PROMPT；wechat_parser 内联 prompt 保留。原则：使用处内联，跨模块复用的进 prompts.py |

---

## 四、职责卡片（模板 + 示例）

格式固定三行：**做什么 / 谁调它 / 坑是什么**。面试被问到任何模块 = 背卡片 + 坑 + 一句整改。

> **application/chat_service.py**：五意图编排唯一实现，返回 ChatResult(reply, intent, route, orders)。app.py 和 routes.py 两个薄适配器调它；除入口和 tests 外谁都不许 import 它。坑：Streamlit 实例必须放 session_state（rerun 换线程）且与订单页共享同一 order_manager；大麦缓存挂在实例上，1 小时 TTL、失败回退旧缓存。

> **router.py**：意图分类统一入口。一级 `classify_intent`（LLM 主、异常回退关键词）选流程，二级 `classify_order_action`（纯关键词）选 ORDER_MANAGE 子操作，词表一处维护。坑：LLM 分类每请求 +1 次调用；"把…订单"无动作词默认按删除处理是产品决策不是 bug。

自己给这些模块各写一张：`llm.py`、`prompts.py`、`database.py`、`broadcast_db.py`、`order_manager.py`、`broadcast_manager.py`、`broadcast_scheduler.py`、`notifier.py`、`event_search.py`、`time_utils.py`、`retriever.py`、`tools/__init__.py`、`app.py`、`routes.py`。

---

## 五、面试辩护速查（修复后版本）

1. **"代码曾经大量冗余"总攻**：不辩解，直接讲修复叙事——① 双入口 600 行复制已收敛到 application/chat_service.py，入口变薄适配器；② 三份城市列表/两份"明天"算法/三份提醒实现/五份 JSON 提取已归一；③ 死代码 500+ 行已删；④ 剩余已知重复（database 行重建、大麦双路径、错误契约四种）能逐条说出为什么暂不修（改动面/无数据源/单独立项）。**能报出"修了什么、怎么分步提交、哪些故意不修"，冗余指控就变成工程能力加分点。**
2. **Streamlit 是不是玩具**：内部工具/demo/看板场景它是对的选择；原来的真问题是 1103 行表现层塞满业务逻辑——**已修**：app.py 现在 476 行纯展示/交互，业务全在编排层，换前端框架不用动业务代码。
3. **五个意图要不要 function calling**：外层显式路由（选流程，可控可测试便宜）+ 内层 function calling（EVENT_QUERY/GENERAL 分支里 LLM 自主选工具）的混合架构，现在两层都在 chat_service 一处实现，能画出来。
4. **NTP 校时**：抢票对时钟敏感，关键路径（开抢匹配、播报日期、开票提醒、调度补偿）全走 `get_accurate_time`（5 分钟缓存 + 降级）；存储时间戳保留 datetime.now（不需要防伪）。
5. **开抢提醒曾经是死代码**：save_order 不回填 order.id → `{o.id}` 恒为 {None} → 提醒永不触发。修复后首次真实触发属预期行为变更——这种"修好之后行为会变"的意识要主动讲。
6. **被问到不记得的细节**：说"这块我需要看代码确认，但它的上下游是 X 和 Y"——给链路位置，不编造。编造是面试死刑。
