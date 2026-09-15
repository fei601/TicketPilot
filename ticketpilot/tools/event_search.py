"""
演出信息查询工具

数据源架构：
- DataSource（抽象基类）：定义统一接口
- MockDataSource：测试用 Mock 数据
- DamaiDataSource：大麦抢票播报站（真实 API）
- WenhuaDataSource：文旅市场通（演出审批信息）
- WebSearchDataSource：Tavily 联网搜索（实时信息）

通过 DataSourceManager 管理多个数据源，查询时自动聚合结果。
"""

import hashlib
import json
import logging
import os
import time
from abc import ABC, abstractmethod

import requests

# 配置日志
logger = logging.getLogger(__name__)

from ticketpilot.tools.base import register_tool


# ===========================================
# 数据源抽象基类
# ===========================================

class DataSource(ABC):
    """数据源抽象基类"""

    @abstractmethod
    def search(self, keyword: str, city: str | None = None) -> list[dict]:
        """搜索演出信息"""
        ...

    @abstractmethod
    def get_name(self) -> str:
        """数据源名称"""
        ...


# ===========================================
# Mock 数据源（开发测试用）
# ===========================================

class MockDataSource(DataSource):
    """Mock 数据源，用于开发和测试"""

    def get_name(self) -> str:
        return "mock"

    MOCK_EVENTS = [
        {
            "id": "MOCK001",
            "source": "mock",
            "name": "周杰伦 嘉年华 世界巡回演唱会 上海站",
            "artist": "周杰伦",
            "city": "上海",
            "venue": "上海体育场",
            "date": "2026-10-15",
            "sale_time": "2026-09-20 10:00",
            "platform": "大麦",
            "prices": [
                {"tier": "VIP", "price": 1680},
                {"tier": "内场", "price": 1280},
                {"tier": "看台A", "price": 980},
                {"tier": "看台B", "price": 580},
            ],
            "status": "即将开票",
            "refund_policy": "演出前48小时可退，扣除10%手续费",
        },
    ]

    def search(self, keyword: str, city: str | None = None) -> list[dict]:
        results = []
        for event in self.MOCK_EVENTS:
            if keyword.lower() in event.get("artist", "").lower() or keyword.lower() in event.get("name", "").lower():
                if city and city.lower() not in event.get("city", "").lower():
                    continue
                results.append(event)
        return results


# ===========================================
# 大麦抢票播报站数据源（真实 API）
# ⚠️ 仅供学习研究，严禁商业使用！
# ===========================================

class DamaiDataSource(DataSource):
    """
    大麦抢票播报站数据源

    ⚠️ 免责声明：
    - 本模块仅供技术学习和研究使用
    - 禁止用于任何商业用途或盈利目的
    - 使用本模块可能违反大麦网用户协议
    - 使用者需自行承担所有风险和法律责任

    通过大麦 MTOP API 获取实时播报数据，包括：
    - 🔥 热门必抢：正在热抢的项目
    - ⏰ 即将开抢：带倒计时的待开票项目
    - 👀 正在热抢：所有在售项目

    API 流程：
    1. 请求获取 _m_h5_tk cookie（MTOP Token）
    2. 用 token 计算 MD5 签名
    3. 带签名请求播报站接口

    数据字段：
    - name: 演出名称
    - cityName: 城市
    - venueName: 场馆
    - showTime: 演出时间
    - priceLow: 最低价
    - onSaleTime: 开票时间（如"今天17:17开抢"）
    - countdownTime: 倒计时（毫秒）
    - itemSaleStatus: 售卖状态（0=在售, 2=待开票）
    - categoryName: 分类（演唱会/话剧/音乐节等）
    - verticalPic: 封面图
    """

    MTOP_API = "https://mtop.damai.cn/h5/mtop.damai.wireless.search.broadcast.list/1.0/"
    APP_KEY = "12574478"

    # 城市 ID 映射（常用城市）
    CITY_MAP = {
        "北京": "852",
        "上海": "852",
        "广州": "852",
        "深圳": "852",
        "成都": "852",
        "杭州": "852",
        "南京": "852",
        "武汉": "852",
        "重庆": "852",
        "西安": "852",
        "全国": "852",
    }

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
            "Referer": "https://m.damai.cn/",
        })
        self._token = None
        self._token_time = 0

    def get_name(self) -> str:
        return "damai"

    def _get_token(self) -> str:
        """
        获取 MTOP Token。

        第一次请求时大麦会返回 _m_h5_tk cookie，
        格式为 {token}_{timestamp}，取前半部分用于签名。
        """
        # Token 缓存 30 分钟
        if self._token and (time.time() - self._token_time) < 1800:
            return self._token

        try:
            timestamp = str(int(time.time() * 1000))
            params = {
                "jsv": "2.7.2",
                "appKey": self.APP_KEY,
                "t": timestamp,
                "api": "mtop.common.getTimestamp",
                "v": "1.0",
                "type": "originaljson",
                "dataType": "json",
                "data": "{}",
            }
            resp = self._session.get(self.MTOP_API, params=params, timeout=10)

            # 从 cookie 中提取 token
            for cookie in self._session.cookies:
                if cookie.name == "_m_h5_tk":
                    self._token = cookie.value.split("_")[0]
                    self._token_time = time.time()
                    return self._token
        except Exception:
            pass

        return ""

    def _sign(self, token: str, timestamp: str, data: str) -> str:
        """
        计算 MTOP 签名。

        签名算法：MD5(token + & + timestamp + & + appKey + & + data)
        """
        raw = f"{token}&{timestamp}&{self.APP_KEY}&{data}"
        return hashlib.md5(raw.encode()).hexdigest()

    def search(self, keyword: str, city: str | None = None) -> list[dict]:
        """
        从大麦抢票播报站搜索演出信息。

        Args:
            keyword: 搜索关键词（艺人名/演出名/分类）
            city: 城市筛选

        Returns:
            标准化的演出信息列表
        """
        token = self._get_token()
        if not token:
            return [{"source": "damai", "error": "获取 MTOP Token 失败"}]

        timestamp = str(int(time.time() * 1000))
        city_id = self.CITY_MAP.get(city, "852") if city else "852"
        data = json.dumps({"cityId": city_id}, separators=(",", ":"))
        sign = self._sign(token, timestamp, data)

        params = {
            "jsv": "2.7.2",
            "appKey": self.APP_KEY,
            "t": timestamp,
            "sign": sign,
            "api": "mtop.damai.wireless.search.broadcast.list",
            "v": "1.0",
            "type": "originaljson",
            "dataType": "json",
            "data": data,
        }

        try:
            resp = self._session.get(self.MTOP_API, params=params, timeout=15)
            resp_data = resp.json()

            if resp_data.get("ret", [""])[0] != "SUCCESS::调用成功":
                return [{"source": "damai", "error": f"API 返回错误：{resp_data.get('ret')}"}]

            # 解析播报数据
            all_events = []
            modules = resp_data.get("data", {}).get("modules", [])

            for module in modules:
                section_title = module.get("title", "")
                for item in module.get("items", []):
                    event = {
                        "source": "damai",
                        "section": section_title,
                        "id": item.get("itemId"),
                        "name": item.get("name", ""),
                        "city": item.get("cityName", ""),
                        "venue": item.get("venueName", ""),
                        "show_time": item.get("showTime", ""),
                        "price_low": item.get("priceLow", ""),
                        "sale_time": item.get("onSaleTime") or item.get("onSaleTimeNew", ""),
                        "sale_status": item.get("itemSaleStatus"),
                        "status_text": item.get("title", ""),
                        "category": item.get("categoryName", ""),
                        "countdown_ms": item.get("countdownTime"),
                        "image": item.get("verticalPic", ""),
                        "link": f"https://m.damai.cn/app/damai2/pages/project-detail/index.html?itemId={item.get('itemId')}",
                    }
                    all_events.append(event)

            # 关键词筛选（支持部分匹配）
            if keyword:
                keyword_lower = keyword.lower()
                # 移除常见后缀，提取核心关键词
                core_keyword = keyword_lower.replace("演唱会", "").replace("音乐节", "").replace("演出", "").strip()

                def match_keyword(text: str, kw: str) -> bool:
                    """检查关键词是否匹配（支持部分匹配）"""
                    if not kw:
                        return True
                    text_lower = text.lower()
                    # 完整匹配
                    if kw in text_lower:
                        return True
                    # 核心关键词匹配（去掉后缀后）
                    if core_keyword and core_keyword in text_lower:
                        return True
                    # 单字匹配（每个字都在文本中）
                    if all(char in text_lower for char in kw if char.strip()):
                        return True
                    return False

                all_events = [
                    e for e in all_events
                    if match_keyword(e["name"], keyword_lower)
                    or match_keyword(e.get("category", ""), keyword_lower)
                    or match_keyword(e.get("city", ""), keyword_lower)
                ]

            # 城市筛选
            if city:
                city_lower = city.lower()
                all_events = [
                    e for e in all_events
                    if city_lower in e.get("city", "").lower()
                ]

            return all_events

        except Exception as e:
            return [{"source": "damai", "error": f"请求失败：{e}"}]


# ===========================================
# 文旅市场通数据源
# ===========================================

class WenhuaDataSource(DataSource):
    """
    文旅市场通数据源

    数据来源：全国文化市场技术监管与服务平台
    网址：https://www.mct.gov.cn/

    TODO: 后续实现对接逻辑
    """

    def get_name(self) -> str:
        return "wenhua"

    def search(self, keyword: str, city: str | None = None) -> list[dict]:
        return []


# ===========================================
# Tavily 联网搜索数据源
# ===========================================

class WebSearchDataSource(DataSource):
    """
    基于 Tavily 的联网搜索数据源。

    注册地址：https://tavily.com
    免费额度：1000 次/月
    """

    TAVILY_API_URL = "https://api.tavily.com/search"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", "")

    def get_name(self) -> str:
        return "web"

    def search(self, keyword: str, city: str | None = None) -> list[dict]:
        if not self.api_key:
            return [{
                "source": "web",
                "error": "未配置 TAVILY_API_KEY，请在 .env 中设置",
                "suggestion": "注册 https://tavily.com 获取免费 API Key",
            }]

        query = f"{keyword} 演唱会 演出"
        if city:
            query += f" {city}"
        query += " 票价 开票时间 购票平台"

        try:
            response = requests.post(
                self.TAVILY_API_URL,
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": True,
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()

            results = []
            if data.get("answer"):
                results.append({"source": "web", "type": "answer", "content": data["answer"]})
            for item in data.get("results", []):
                results.append({
                    "source": "web",
                    "type": "search_result",
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", ""),
                })
            return results

        except requests.exceptions.RequestException as e:
            return [{"source": "web", "error": f"搜索请求失败：{e}"}]


# ===========================================
# 数据源管理器
# ===========================================

class DataSourceManager:
    """数据源管理器"""

    def __init__(self):
        self._sources: dict[str, DataSource] = {}

    def register(self, source: DataSource):
        self._sources[source.get_name()] = source

    def remove(self, name: str):
        self._sources.pop(name, None)

    def list_sources(self) -> list[str]:
        return list(self._sources.keys())

    def search(self, keyword: str, city: str | None = None, source_name: str | None = None) -> list[dict]:
        """搜索演出信息，按优先级分层返回结果

        优先级逻辑：
        1. 如果指定了数据源，只搜索该数据源
        2. 否则按优先级搜索：damai > mock > web
        3. 如果高优先级数据源有结果，直接返回，不再搜索低优先级
        """
        # 确定要搜索的数据源（按优先级排序）
        if source_name and source_name in self._sources:
            sources = [self._sources[source_name]]
        else:
            # 优先级：damai > web（不使用 mock，避免返回假数据）
            priority_order = ["damai", "web"]
            sources = []
            for name in priority_order:
                if name in self._sources:
                    sources.append(self._sources[name])

        # 按优先级搜索，找到结果就停止
        for source in sources:
            try:
                source_results = source.search(keyword, city)

                # 标记数据来源
                for r in source_results:
                    if not r.get("error"):
                        r["data_source"] = source.get_name()
                        r["data_source_label"] = self._get_source_label(source.get_name())

                # 检查是否有有效结果（非错误）
                valid_results = [r for r in source_results if not r.get("error")]

                if valid_results:
                    # 找到有效结果，直接返回（不再搜索低优先级数据源）
                    return valid_results

                # 没有有效结果，记录错误并继续搜索下一个数据源
                if source_results:
                    logger.info(f"[DataSourceManager] {source.get_name()} 返回无结果，尝试下一个数据源")

            except Exception as e:
                logger.error(f"[DataSourceManager] {source.get_name()} 查询失败: {e}")

        # 所有数据源都没有结果
        return []

    def _get_source_label(self, source_name: str) -> str:
        """获取数据源的中文标签"""
        labels = {
            "damai": "大麦播报站（官方）",
            "mock": "测试数据",
            "web": "网络搜索（仅供参考）",
        }
        return labels.get(source_name, source_name)


# ===========================================
# 初始化数据源管理器
# ===========================================

_manager = DataSourceManager()
# _manager.register(MockDataSource())  # 已禁用：避免返回假数据
_manager.register(DamaiDataSource())  # 大麦抢票播报站（真实 API）
_manager.register(WebSearchDataSource())  # Tavily 联网搜索
# _manager.register(WenhuaDataSource())  # TODO: 文旅市场通对接后启用


# ===========================================
# 注册工具
# ===========================================

@register_tool(
    name="search_event",
    description="查询演出信息，包括开票时间、票价、购票平台等。数据来源：大麦抢票播报站（实时）+ 联网搜索。",
    parameters={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词，如艺人名称或演出名称",
            },
            "city": {
                "type": "string",
                "description": "城市名称（可选筛选条件）",
            },
        },
        "required": ["keyword"],
    },
)
def search_event(keyword: str, city: str | None = None) -> str:
    """
    搜索演出信息。

    优先从大麦播报站获取实时数据，补充 Tavily 联网搜索。
    """
    results = _manager.search(keyword, city)

    # 过滤掉错误结果，只保留有效数据
    valid_results = [r for r in results if not r.get("error")]

    if not valid_results:
        # 检查是否有特定数据源的错误
        errors = [r for r in results if r.get("error")]
        if errors:
            # 如果只有 Tavily 错误但 Damai 无结果，返回"未找到"而不是错误
            return json.dumps(
                {"message": f"大麦播报站暂未收录「{keyword}」的演出信息", "suggestion": "该演出可能尚未上架或不在当前播报范围内，建议关注官方票务平台"},
                ensure_ascii=False,
            )
        return json.dumps(
            {"message": f"未找到与 '{keyword}' 相关的演出信息", "suggestion": "可以尝试换个关键词，或者指定城市搜索"},
            ensure_ascii=False,
        )

    return json.dumps(valid_results, ensure_ascii=False, indent=2)


@register_tool(
    name="get_damai_broadcast",
    description="获取大麦抢票播报站的实时播报数据，包括即将开抢、正在热抢的演出列表。可用于查看明天有什么演出开票。",
    parameters={
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称（可选，默认全国）",
            },
            "category": {
                "type": "string",
                "description": "演出类型筛选（可选）：演唱会、话剧、音乐节、脱口秀等",
            },
        },
        "required": [],
    },
)
def get_damai_broadcast(city: str | None = None, category: str | None = None) -> str:
    """
    获取大麦抢票播报站数据。

    直接调用大麦 MTOP API，返回播报站的实时数据。
    用于每日播报、开票提醒等场景。
    """
    # 使用全局管理器中的缓存实例，避免重复创建
    damai = _manager._sources.get("damai")
    if not damai:
        damai = DamaiDataSource()
        _manager.register(damai)
    results = damai.search("", city)

    # 分类筛选
    if category:
        results = [e for e in results if category.lower() in e.get("category", "").lower()]

    if not results:
        return json.dumps({"message": "暂无演出信息"}, ensure_ascii=False)

    # 按板块分组
    grouped = {}
    for event in results:
        section = event.get("section", "其他")
        if section not in grouped:
            grouped[section] = []
        grouped[section].append(event)

    return json.dumps(grouped, ensure_ascii=False, indent=2)


@register_tool(
    name="check_approval",
    description="查询某场演出是否已通过文旅部门审批，用于核实演出信息的真实性。",
    parameters={
        "type": "object",
        "properties": {
            "event_name": {
                "type": "string",
                "description": "演出名称",
            },
            "city": {
                "type": "string",
                "description": "演出城市（可选）",
            },
        },
        "required": ["event_name"],
    },
)
def check_approval(event_name: str, city: str | None = None) -> str:
    """
    查询演出审批状态。

    生成文旅市场通查询链接，用户点击即可查看审批信息。
    """
    # 生成搜索关键词
    search_keyword = event_name
    if city:
        search_keyword = f"{city} {event_name}"

    # 文旅市场通搜索链接（UTF-8 编码）
    from urllib.parse import quote
    encoded_keyword = quote(search_keyword)
    search_url = f"https://www.mct.gov.cn/whzx/ggwhfw/whywzc/index.html?keyword={encoded_keyword}"

    return json.dumps({
        "event_name": event_name,
        "city": city,
        "query_url": search_url,
        "message": f"点击链接查询「{event_name}」的审批状态",
        "platform": "全国文化市场技术监管与服务平台",
        "instructions": "在打开的页面中查看是否有相关演出的审批记录，正规演出必须有文旅部门审批才能售票",
    }, ensure_ascii=False)
