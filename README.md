# 🎫 TicketPilot

> **AI 驱动的票务订单管理助手** — 用自然语言管理你的票务工作流

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/LLM-DeepSeek-0078D4?style=for-the-badge&logo=openai&logoColor=white" alt="LLM">
  <img src="https://img.shields.io/badge/Frontend-Streamlit-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white" alt="Streamlit">
  <img src="https://img.shields.io/badge/Database-SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
</p>

<p align="center">
  <a href="#-核心功能">功能特性</a> •
  <a href="#-快速开始">快速开始</a> •
  <a href="#-技术架构">技术架构</a> •
  <a href="#-演示示例">演示</a> •
  <a href="#-联系方式">联系</a>
</p>

---

## 📖 项目简介

**TicketPilot** 是一个基于大语言模型（LLM）的票务工作流智能助手，专为票务从业者设计。

### 🎯 解决的痛点

票务代拍工作者每天需要：
- 📝 处理大量零散的客户信息（微信、短信、截图）
- ⏰ 跟踪多个项目的开票时间
- 📊 管理订单状态（中票/未中票/撤单/退款）
- 🔍 监控漏票和二次开票机会

这些重复性工作占据了大量时间，**TicketPilot 用 AI 自动化这些流程**。

### 💡 核心亮点

| 亮点 | 说明 |
|------|------|
| 🧠 **LLM 意图分类** | 自然语言理解，无需记忆命令格式 |
| 📋 **智能订单解析** | 支持多订单批量解析，自动识别佣金、票价 |
| 🔌 **大麦数据集成** | 实时获取抢票播报站数据，自动匹配订单 |
| 🔒 **隐私保护** | 身份证号、手机号自动脱敏显示 |
| 💬 **上下文记忆** | 支持"把刚才的订单改一下"等连续对话 |

---

## ✨ 核心功能

### 1️⃣ 智能订单解析

**输入零散信息，自动整理成结构化订单：**

```
上海站 薛之谦 8.17  1680内场 连坐2张
张三 310101199901011234
李四 310101199902022345
大麦 13800138000
```

**AI 自动输出：**
```
订单 1（订单号：1）

上海站薛之谦 8.17 1680内场 连坐2张

身份信息：
张三 310***********1234
李四 310***********2345

联系电话：138****5678
平台：大麦
```

### 2️⃣ 自然语言订单管理

```
👤 用户：把薛之谦的订单票价改成1880
🤖 助手：已更新订单「上海站薛之谦演唱会」：ticket_type=1880

👤 用户：删除第二条订单
🤖 助手：已删除订单：XXX演唱会（订单号：2）

👤 用户：我有孙燕姿的单子吗
🤖 助手：找到 1 条孙燕姿相关订单：...
```

### 3️⃣ 大麦抢票播报

```
👤 用户：明天都有什么项目开票？
🤖 助手：明天需要抢票的订单（3条）：

1. 周杰伦演唱会 - 上海站
   开票时间：明天 10:00
   票价：1680/1280/880

2. 薛之谦演唱会 - 北京站
   开票时间：明天 14:00
   ...
```

### 4️⃣ 知识库问答（RAG）

```
👤 用户：什么是"延顺"？
🤖 助手：延顺是指当票务订单未中签时，自动延续到下一轮...

👤 用户：大麦和猫眼的退票规则有什么区别？
🤖 助手：根据票务知识库，两者的主要区别是...
```

---

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────────────────────┐
│                      用户输入                                │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              LLM 意图分类（Intent Router）                    │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┐   │
│  │ 订单解析  │ 演出查询  │ 知识问答  │ 订单管理  │ 通用对话  │   │
│  └────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬─────┘   │
└───────┼──────────┼──────────┼──────────┼──────────┼─────────┘
        │          │          │          │          │
        ▼          ▼          ▼          ▼          ▼
   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
   │ LLM 结构 │ │ 大麦 API │ │ RAG 检索 │ │ CRUD 操 │ │ LLM 对话 │
   │ 化输出   │ │ + Tavily │ │ 知识库   │ │ 作数据库 │ │         │
   └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘
```

### 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **LLM** | DeepSeek API | OpenAI 兼容接口，支持切换任意模型 |
| **意图分类** | LLM + Prompt Engineering | 零样本意图识别，无需训练 |
| **订单解析** | LLM 结构化输出 | 支持复杂格式、佣金识别、多订单 |
| **演出数据** | 大麦 MTOP API | 实时抢票播报站数据 |
| **联网搜索** | Tavily Search API | 补充搜索实时信息 |
| **知识库** | RAG (Markdown + 向量检索) | 票务术语、平台规则、抢票技巧 |
| **数据库** | SQLite | 订单持久化存储 |
| **前端** | Streamlit | Web 聊天界面 |
| **隐私保护** | 自研脱敏模块 | 身份证号、手机号自动脱敏 |

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- DeepSeek API Key（[获取地址](https://platform.deepseek.com/)）

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
# 必填：DeepSeek API Key
LLM_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# 可选：Tavily 联网搜索（免费注册）
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxx

# 可选：日志级别
LOG_LEVEL=INFO
```

### 启动应用

```bash
# 启动 Streamlit 前端
streamlit run frontend/app.py
```

浏览器会自动打开 `http://localhost:8501`

---

## 📁 项目结构

```
TicketPilot/
├── README.md                         # 项目说明
├── LICENSE                           # MIT 开源协议
├── requirements.txt                  # Python 依赖
├── .env.example                      # 环境变量模板
├── config.py                         # 配置管理
├── main.py                           # 后端入口
│
├── ticketpilot/                      # 核心包
│   ├── core/                         # 核心模块
│   │   ├── llm.py                   # LLM 调用封装（单例模式）
│   │   ├── router.py                # LLM 意图路由
│   │   ├── prompts.py               # Prompt 模板管理
│   │   ├── privacy.py               # PII 隐私保护
│   │   └── utils.py                 # 通用工具函数
│   │
│   ├── agent/                        # Agent 模块
│   │   ├── order_manager.py         # 订单 CRUD 管理
│   │   └── scheduler.py             # 定时任务调度
│   │
│   ├── tools/                        # Function Calling 工具
│   │   ├── base.py                  # 工具注册机制
│   │   ├── event_search.py          # 大麦播报站 + Tavily 搜索
│   │   └── time_utils.py            # NTP 时间校准
│   │
│   ├── rag/                          # RAG 知识库
│   │   ├── loader.py                # 文档加载器
│   │   └── retriever.py             # 向量检索
│   │
│   ├── data/                         # 数据层
│   │   ├── database.py              # SQLite 数据库操作
│   │   ├── models.py                # 数据模型定义
│   │   └── constants.py             # 全局常量
│   │
│   └── knowledge_base/               # RAG 知识文档
│       ├── glossary.md              # 票务术语词典
│       ├── platform_rules.md        # 平台规则说明
│       └── tips.md                  # 抢票技巧
│
├── frontend/                         # 前端应用
│   └── app.py                       # Streamlit Web 界面
│
├── api/                              # API 路由
│   └── routes.py                    # FastAPI 接口
│
└── tests/                            # 测试用例
    ├── test_router.py               # 意图路由测试
    ├── test_database.py             # 数据库测试
    └── test_tools.py                # 工具测试
```

---

## 🔧 核心实现

### 1. LLM 意图分类

```python
# ticketpilot/core/router.py
INTENT_CLASSIFY_PROMPT = """
你是一个意图分类器，根据用户输入判断意图类型。

意图类型：
- ORDER_PARSE: 用户提供订单信息，需要整理
- EVENT_QUERY: 查询演出信息、开票时间
- ORDER_MANAGE: 删除/修改/查询订单状态
- KNOWLEDGE_QA: 票务知识问答
- GENERAL: 其他对话

只输出JSON: {"intent": "ORDER_PARSE"}
"""

def classify_intent(user_input: str, context: str = "") -> IntentType:
    """使用 LLM 进行意图分类"""
    response = llm.chat(messages, temperature=0, max_tokens=50)
    result = extract_json(response["content"])
    return intent_map.get(result["intent"], IntentType.GENERAL)
```

### 2. 多订单智能解析

```python
# ticketpilot/agent/order_manager.py
def parse_multiple_orders(self, text: str) -> list[Order]:
    """
    智能合并行：只有当行以城市名开头或包含开票关键词时才认为是新订单
    支持：
    - 多订单批量解析
    - 佣金识别（🧧后面的数字是佣金）
    - 复杂票型（看台随机不要580）
    """
    # 使用 LLM 结构化输出
    response = llm.chat(messages, temperature=0.1, max_tokens=500)
    return Order(**extract_json(response["content"]))
```

### 3. 隐私保护

```python
# ticketpilot/core/privacy.py
def mask_id_card(id_card: str) -> str:
    """身份证号脱敏: 310101199901011234 → 310***********1234"""

def mask_phone(phone: str) -> str:
    """手机号脱敏: 13812345678 → 138****5678"""

def mask_pii_in_text(text: str) -> str:
    """自动检测并脱敏文本中的所有 PII"""
```

---

## 💡 设计理念

| 理念 | 实现 |
|------|------|
| **数据分层路由** | 根据数据特性选择 RAG / API / 搜索 / Agent |
| **真实数据优先** | 大麦播报站 API + Tavily 联网搜索，不依赖 LLM 记忆 |
| **隐私第一** | 默认脱敏显示，用户主动选择才展示完整信息 |
| **上下文感知** | 会话记忆，支持"把刚才的订单改一下" |
| **优雅降级** | LLM 失败时自动降级到关键词匹配 |
| **OpenAI 兼容** | 切换 LLM 只改配置，不改代码 |

---

## 📊 功能矩阵

| 功能 | 状态 | 技术实现 |
|------|------|----------|
| 自然语言订单解析 | ✅ 已完成 | LLM 结构化输出 |
| 多订单批量解析 | ✅ 已完成 | 智能行合并 + LLM |
| 订单 CRUD | ✅ 已完成 | SQLite + Agent |
| LLM 意图分类 | ✅ 已完成 | Prompt Engineering |
| 大麦数据集成 | ✅ 已完成 | MTOP API 签名 |
| 开票提醒 | ✅ 已完成 | 订单-数据自动匹配 |
| RAG 知识库 | ✅ 已完成 | Markdown + 向量检索 |
| PII 隐私保护 | ✅ 已完成 | 自研脱敏模块 |
| 企业微信推送 | 🔜 规划中 | Webhook |
| 漏票监控 | 🔜 规划中 | 定时轮询 |

---

## 🎓 AI 技术应用

本项目展示了以下 AI 应用开发能力：

### 1. LLM 应用开发
- ✅ Prompt Engineering（意图分类、订单解析）
- ✅ 结构化输出（JSON 格式化）
- ✅ Function Calling（工具调用）
- ✅ 上下文管理（会话记忆）

### 2. RAG 检索增强生成
- ✅ 知识库构建（Markdown 文档）
- ✅ 向量检索（语义相似度匹配）
- ✅ 上下文注入（增强 LLM 回答）

### 3. Agent 架构
- ✅ 意图路由（Intent Router）
- ✅ 工具调用（Tool Use）
- ✅ 状态管理（订单 CRUD）

### 4. 工程实践
- ✅ 模块化设计（core / agent / tools / data）
- ✅ 错误处理（优雅降级）
- ✅ 隐私保护（PII 脱敏）
- ✅ 性能优化（LLM 客户端单例、数据缓存）

---

## 🤝 联系方式

📧 **Email**: fei601@example.com

💼 **GitHub**: [github.com/fei601](https://github.com/fei601)

---

## 📝 开源协议

本项目采用 [MIT License](LICENSE) 开源协议。

---

## ⚠️ 免责声明

**本项目仅供学习研究，严禁商业使用。**

- 🚫 禁止用于代抢、黄牛、票务代理等商业行为
- 🚫 禁止大规模爬取、对服务器造成压力
- ⚠️ 使用 MTOP 私有 API 存在法律风险，请自行承担
- ✅ 建议使用官方 API 或公开数据源

详细条款请阅读 [DISCLAIMER.md](DISCLAIMER.md)

---

<p align="center">
  如果这个项目对你有帮助，请给一个 ⭐ Star 支持一下！
</p>

