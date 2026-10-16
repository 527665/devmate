"""订单接口单元测试。"""

from decimal import Decimal
from fastapi.testclient import TestClient

from gateway.main import app

client = TestClient(app)


def test_order_quote_success():
    """测试订单报价成功场景。"""
    request_data = {
        "items": [
            {"unitPrice": "10.50", "quantity": 2},
            {"unitPrice": "25.00", "quantity": 3}
        ]
    }
    
    response = client.post("/orders/quote", json=request_data)
    assert response.status_code == 200
    
    result = response.json()
    expected_total = Decimal("10.50") * 2 + Decimal("25.00") * 3
    assert Decimal(str(result["totalAmount"])) == expected_total


def test_order_quote_single_item():
    """测试单个商品的订单报价。"""
    request_data = {
        "items": [
            {"unitPrice": "99.99", "quantity": 1}
        ]
    }
    
    response = client.post("/orders/quote", json=request_data)
    assert response.status_code == 200
    
    result = response.json()
    assert Decimal(str(result["totalAmount"])) == Decimal("99.99")


def test_order_quote_zero_quantity():
    """测试数量为零的情况（应该失败）。"""
    request_data = {
        "items": [
            {"unitPrice": "10.00", "quantity": 0}
        ]
    }
    
    response = client.post("/orders/quote", json=request_data)
    assert response.status_code == 422  # Pydantic 验证错误


def test_order_quote_negative_price():
    """测试负单价的情况（应该失败）。"""
    request_data = {
        "items": [
            {"unitPrice": "-5.00", "quantity": 2}
        ]
    }
    
    response = client.post("/orders/quote", json=request_data)
    assert response.status_code == 422  # Pydantic 验证错误


def test_order_quote_empty_items():
    """测试空商品列表（应该失败）。"""
    request_data = {
        "items": []
    }
    
    response = client.post("/orders/quote", json=request_data)
    assert response.status_code == 422  # Pydantic 验证错误


def test_order_quote_decimal_precision():
    """测试 Decimal 精度处理。"""
    request_data = {
        "items": [
            {"unitPrice": "0.10", "quantity": 3}
        ]
    }
    
    response = client.post("/orders/quote", json=request_data)
    assert response.status_code == 200
    
    result = response.json()
    # 确保不会出现浮点数精度问题，如 0.30000000000000004
    assert result["totalAmount"] == "0.30"
