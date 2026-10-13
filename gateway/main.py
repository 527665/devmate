"""FastAPI 应用入口点。"""

from fastapi import FastAPI

from gateway.routes import orders

app = FastAPI(
    title="DevMate API",
    description="DevMate 的 API 服务",
    version="0.1.0",
)

# 注册路由
app.include_router(orders.router)