"""
数据模型定义

使用 Pydantic 定义订单、客户等数据结构。
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
    """订单状态"""
    PENDING = "pending"          # 待抢票
    SUCCESS = "success"          # 已中票
    FAILED = "failed"            # 未中票
    CANCELLED = "cancelled"      # 已撤单
    REFUNDED = "refunded"        # 已退款
    WAITING_SECOND = "waiting_second"  # 等待二开


class Order(BaseModel):
    """订单模型"""
    id: int | None = None
    customer_name: str = Field(description="客户姓名")
    event_name: str = Field(description="演出名称")
    event_date: str | None = Field(default=None, description="演出日期")
    platform: str | None = Field(default=None, description="购票平台")
    ticket_type: str | None = Field(default=None, description="票种/档位")
    quantity: int = Field(default=1, description="数量")
    seats: str | None = Field(default=None, description="座位要求")
    budget: str | None = Field(default=None, description="预算")
    notes: str | None = Field(default=None, description="备注")
    status: OrderStatus = Field(default=OrderStatus.PENDING, description="订单状态")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")


class Customer(BaseModel):
    """客户模型"""
    id: int | None = None
    name: str = Field(description="客户姓名")
    phone: str | None = Field(default=None, description="联系电话")
    wechat: str | None = Field(default=None, description="微信号")
    notes: str | None = Field(default=None, description="备注")


class EventReminder(BaseModel):
    """开票提醒模型"""
    id: int | None = None
    event_name: str = Field(description="演出名称")
    sale_time: str = Field(description="开票时间")
    platform: str = Field(description="购票平台")
    is_sent: bool = Field(default=False, description="是否已推送")
    created_at: datetime = Field(default_factory=datetime.now)


class BroadcastEvent(BaseModel):
    """播报事件模型"""
    id: int | None = None
    event_name: str = Field(description="演出名称")
    artist: str | None = Field(default=None, description="艺人/歌手")
    city: str | None = Field(default=None, description="城市")
    venue: str | None = Field(default=None, description="场馆")
    show_date: str | None = Field(default=None, description="演出日期")
    sale_date: str | None = Field(default=None, description="开票日期")
    sale_time: str | None = Field(default=None, description="开票时间")
    platform: str | None = Field(default=None, description="购票平台")
    prices: str | None = Field(default=None, description="票价区间")
    notes: str | None = Field(default=None, description="备注")
    source: str = Field(default="wechat", description="数据来源")
    parsed_at: datetime = Field(default_factory=datetime.now)
    status: str = Field(default="pending", description="状态")
