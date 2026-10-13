"""订单相关路由。"""

from decimal import Decimal
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/orders", tags=["orders"])


class OrderItem(BaseModel):
    """订单商品项。"""
    unitPrice: Decimal = Field(..., gt=0, description="商品单价", alias="unitPrice")
    quantity: int = Field(..., gt=0, description="商品数量")
    
    model_config = {"populate_by_name": True}


class OrderQuoteRequest(BaseModel):
    """订单报价请求。"""
    items: List[OrderItem] = Field(..., min_items=1, description="商品列表")
    
    model_config = {"populate_by_name": True}


class OrderQuoteResponse(BaseModel):
    """订单报价响应。"""
    totalAmount: Decimal = Field(..., description="订单总价", alias="totalAmount")
    
    model_config = {"populate_by_name": True}


@router.post("/quote", response_model=OrderQuoteResponse)
async def get_order_quote(request: OrderQuoteRequest) -> OrderQuoteResponse:
    """获取订单报价。
    
    接收一组商品（单价、数量），返回订单总价。
    """
    try:
        total = Decimal("0")
        for item in request.items:
            # 使用 Decimal 进行精确计算，避免浮点数精度问题
            item_total = item.unitPrice * item.quantity
            total += item_total
        
        return OrderQuoteResponse(totalAmount=total)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"计算订单总价时出错: {str(e)}")