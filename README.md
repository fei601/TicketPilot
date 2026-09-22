# 🎫 TicketPilot（票务领航员）

> **AI 票务工作台** — 把群聊里零散的真实报单消息，变成结构化订单

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/LLM-DeepSeek-0078D4?style=for-the-badge&logo=openai&logoColor=white" alt="LLM">
  <img src="https://img.shields.io/badge/Frontend-Streamlit-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white" alt="Streamlit">
  <img src="https://img.shields.io/badge/Database-SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
</p>

<p align="center">
  <a href="#-项目简介">简介</a> •
  <a href="#-技术架构">架构</a> •
  <a href="#-核心功能">核心功能</a> •
  <a href="#-评测体系与结果">评测</a> •
  <a href="#-快速开始">快速开始</a> •
  <a href="#-项目结构">结构</a>
</p>

---

## 📖 项目简介

票务代拍的日常：客户消息散落在群聊里，一条报单混着人名、证件号、票价、佣金；订单状态靠脑子记；规则问题答错一次，客户就按错误规则操作真金白银的订单。

**TicketPilot** 用 LLM 把这条流水线自动化：贴入报单消息 → 自动解析成结构化订单草稿 → 人工确认落库；规则咨询走检索增强问答，检索不达标直接拒答；实时演出查询走 Agent 工具循环。

全链路遵循三条设计底线：

| 底线 | 落地机制 |
|------|----------|
| **PII 不出域** | 发给 LLM 前证件号/通行证/手机号置换为本地占位符，LLM 全程不见明文；展示层默认打星、显式解锁 |
| **宁可拒答不可错答** | RAG 硬门控：检索得分低于校准阈值直接拒答（答案阶段 0 次 LLM 调用）；通过门控的回答必带小节级来源引用 |
| **行为可度量** | 全链路结构化日志（域/路由/LLM 调用数/延迟）；32 条金标签评测集 + 跑批器，结果可复现 |

---

## 🏗️ 技术架构

路由采用**置信度级联**——便宜且确定的判断在前，贵的在后：

```
用户输入
   │
   ▼
[1] 硬特征快路径 ──命中──► ORDER 域（零 LLM 成本）
   │ 未命中
   ▼
[2] LLM 三域分类 ──失败自动回退──► [3] 关键词容错兜底
   │
   ▼
 ORDER / AGENT / QA
```

三个域对应三种**处理范式**（按范式分域，不按话题分路）：

| 域 | 范式 | 说明 |
|----|------|------|
| **ORDER** | 确定性流水线 | LLM 解析 → 草稿 → 人工确认 → SQLite 落库；删/改/查/确认/汇总 |
| **AGENT** | Function Calling 工具循环 | 实时演出查询；多源降级：平台数据源 → 联网搜索 |
| **QA** | 检索增强问答 | bigram 检索 + 硬门控，达标才调 LLM 作答，回答带来源引用 |

---

## ✨ 核心功能

### 1️⃣ 三域路由（置信度级联）

近零误判的硬特征走快路径，不烧 LLM；拿不准的交给 LLM 三域分类；LLM 失败还有关键词兜底。硬路径配**疑问句守卫**——「可以修改订单里的观演人吗」是规则咨询不是订单操作：

```python
# ticketpilot/core/router.py
def _hard_feature_domain(text: str) -> Domain | None:
    """
    级联第 1 级：硬特征快路径。

    两类近零误判特征，命中直接进订单域，不烧 LLM：
    1. 18 位身份证正则
    2. 「指令词+对象词」组合句式（删除/修改订单类指令，疑问句除外）
    """
    if _has_id_card(text):
        # ID 快路径不设疑问守卫：带完整证件号的疑问句仍是在提交订单数据；
        # 13 位半截号不命中本正则（两侧边界断言），走后面的层级
        return Domain.ORDER
    if _MANAGE_COMMAND_RE.search(text) and not _INTERROGATIVE_RE.search(text):
        return Domain.ORDER
    return None
```

**32 条脱敏评测集路由准确率 100%。**

### 2️⃣ LLM 订单解析 + PII 输入最小化

发给 LLM 之前，所有 PII 先置换成占位符；LLM 返回后在本地还原落库。替换顺序固定 **ID → PERMIT → PHONE**（18 位证件号内部可能包含形如手机号的 11 位子串，先换手机号会把证件号切碎）：

```python
# ticketpilot/core/privacy.py
_ID_CARD_RE = re.compile(r'\d{17}[\dXx]')
_PHONE_RE = re.compile(r'1[3-9]\d{9}')
_PERMIT_RE = re.compile(r'(?<![A-Za-z0-9])[HCWSPEDFGhcwspedfg]\d{8}(?!\d)')

text = _ID_CARD_RE.sub(_repl("ID"), text)
text = _PERMIT_RE.sub(_repl("PERMIT"), text)
text = _PHONE_RE.sub(_repl("PHONE"), text)
```

LLM 输出**先落草稿**，人工确认后才生效——守住「LLM 输出 ≠ 事实」的边界。展示层默认打星，完整信息需显式解锁并给出警示。

**脱敏评测集订单字段准确率 100%**（覆盖多人报单块、佣金/票价混淆、通行证形态等难样本）。

### 3️⃣ RAG 硬门控 + 来源引用

自建 22 条票务规则语料（含「常见问法」变体），字符 bigram 覆盖度检索。**不用向量库**是这个规模下的取舍：每条拒答都能回答「为什么拒」，阈值可以用工具校准：

```python
# ticketpilot/rag/retriever.py
# 硬门控阈值：查询 bigram 至少这个比例命中文档才算检索成功（0~1）。
# 校准记录（D5，32 条评测）：应拒组最高分 0.333，应答组最低分 0.462，
# 0.40 取可分窗口 (0.333, 0.462) 中间，两侧余量均 ~0.06。
# 窗口是靠两个检索侧修复撑开的：数字串噪声剥离 + 语料「常见问法」补强。
# 语料/算法再改动后重跑 eval/calibrate_scores.py 复核窗口是否仍成立
MIN_RETRIEVE_SCORE = 0.40
```

检索未达阈值 → 直接拒答，答案阶段 0 次 LLM 调用；通过门控 → 回答必带小节级来源引用。

### 4️⃣ 自然语言订单管理

```
👤 把薛之谦的订单票价改成1880
🤖 已更新订单「上海站薛之谦演唱会」：ticket_type=1880

👤 查一下这场的配票进度
🤖 订单 #3 上海站薛之谦 8.17 —— 状态：等待二开

👤 什么是"延顺"？
🤖 延顺是指……（来源：《票务术语词典 · 延顺》）
```

---

## 📊 评测体系与结果

评测集不是手编的，是从**真实群聊消息**经脱敏流水线加工来的：

```
raw_messages.txt        真实消息（gitignore，永不入库）
  → eval/mask_eval.py   证件号/手机号/通行证换假号
                        （保结构、同值同映射、真值回扫自检）
  → 人工换假名           同一假号 = 同一客户
  → eval/eval_set.jsonl 32 条金标签（域/路由/子动作/字段/拒答/引用）
  → eval/run_eval.py    逐条内存库隔离跑批，端到端 + 子动作双层判分
  → eval/calibrate_scores.py  阈值可分窗口复核工具
```

首跑 22/32——抓出 5 处当时 123 个全绿单元测试看不见的静默缺陷（硬路径把规则咨询误判成订单操作、数字串稀释检索分、阈值落在不可分区间等），逐个修复后 32/32。

| 指标 | 结果 |
|------|------|
| 路由准确率 | **100%**（32 条脱敏评测集） |
| 订单字段准确率 | **100%**（36/36） |
| 误拒 / 漏拒 | **0 / 0** |
| 来源引用率 | **15/15** |
| 单元测试 | **133 passed** |

> 口径说明：评测集同时承担校准职能（修复由它驱动），数字描述的是「校准后在这批样本上的表现」，不外推总体。它的价值在于抓出并修复了单测覆盖不到的行为缺陷，且随时可复现。

复现：

```bash
python -m pytest -q          # 133 个单元测试，LLM 全部打桩，无需 API Key
python eval/run_eval.py      # 端到端评测，需要 .env 配置 LLM_API_KEY（产生真实 API 调用）
```

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- DeepSeek API Key（[获取地址](https://platform.deepseek.com/)，任何 OpenAI 兼容接口均可）

### 安装步骤

```bash
# 1. 克隆项目
git clone https://github.com/fei601/TicketPilot.git
cd TicketPilot

# 2. 创建虚拟环境
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 API Key
```

### 配置 `.env`

```bash
# 必填：LLM API Key（DeepSeek 或其他 OpenAI 兼容服务）
LLM_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# 可选：Tavily 联网搜索（Agent 域降级数据源，免费注册）
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxx

# 可选：日志级别
LOG_LEVEL=INFO
```

### 启动应用

```bash
streamlit run frontend/app.py
```

浏览器自动打开 `http://localhost:8501`。

---

## 📁 项目结构

```
TicketPilot/
├── main.py / config.py              # 后端入口 / 配置管理
├── ticketpilot/
│   ├── core/                        # llm.py（OpenAI 兼容客户端）
│   │                                # router.py（三域置信度级联）
│   │                                # prompts.py（Prompt 模板）
│   │                                # privacy.py（PII 置换/还原/打星）
│   │                                # utils.py
│   ├── application/chat_service.py  # 聊天编排 + 结构化日志
│   ├── agent/order_manager.py       # 订单解析与 CRUD（草稿→确认）
│   ├── tools/                       # Function Calling 工具
│   │                                # （演出检索/订单解析/知识问答）
│   ├── rag/                         # loader.py（按小节切分，引用粒度）
│   │                                # retriever.py（bigram 覆盖度 + 硬门控）
│   ├── data/                        # database.py（SQLite 参数化查询）
│   │                                # models.py（Pydantic 模型）/ constants.py
│   └── knowledge_base/              # faq.md（22 条规则）+ 术语/平台规则/购票常识
├── eval/                            # 脱敏流水线 / 32 条金标签 / 跑批器 / 阈值校准
├── frontend/app.py                  # Streamlit 界面（默认脱敏展示）
├── api/routes.py                    # FastAPI 接口
└── tests/                           # 13 个测试文件，133 个用例
```

---

## 💡 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **LLM** | DeepSeek API | OpenAI 兼容接口，换模型只改配置 |
| **路由** | 硬特征 + LLM 分类 + 关键词兜底 | 置信度级联，确定性判断不烧 token |
| **订单解析** | LLM 结构化输出 + Pydantic 校验 | 多订单拆分、佣金识别、复杂票价 |
| **检索** | 字符 bigram 覆盖度 | 无向量库，可解释、阈值可校准 |
| **隐私** | 占位符置换 + 展示打星 | LLM 全程不见明文 PII |
| **存储** | SQLite | 参数化查询防注入 |
| **前端** | Streamlit | Web 聊天界面 |
| **测试** | pytest + 自建评测跑批器 | 133 单测 + 32 条端到端金标签 |

---

## ⚠️ 免责声明

**本项目仅供学习研究，严禁商业使用。**

- 🚫 禁止用于代抢、黄牛、票务代理等商业行为
- 🚫 禁止大规模爬取、对服务器造成压力
- ⚠️ 使用 MTOP 私有 API 存在法律风险，请自行承担
- ✅ 建议使用官方 API 或公开数据源

详细条款请阅读 [DISCLAIMER.md](DISCLAIMER.md)

---

## 📝 开源协议

本项目采用 [MIT License](LICENSE) 开源协议。

💼 **GitHub**: [github.com/fei601](https://github.com/fei601)
