"""
API 路由模块

提供 RESTful API 接口，按功能模块拆分
"""
from fastapi import APIRouter

# 导入各子模块路由
from api.routes.auth import router as auth_router
from api.routes.videos import router as videos_router
from api.routes.favorites import router as favorites_router
from api.routes.sync import router as sync_router
from api.routes.config import router as config_router
from api.routes.user import router as user_router
from api.routes.tasks import router as tasks_router
from api.routes.up_sync import router as up_sync_router

# 创建主路由
router = APIRouter(prefix="/api", tags=["API"])

# 注册子路由
router.include_router(auth_router)
router.include_router(videos_router)
router.include_router(favorites_router)
router.include_router(sync_router)
router.include_router(config_router)
router.include_router(user_router)
router.include_router(tasks_router)
router.include_router(up_sync_router)

# 导出初始化函数
from api.routes.sync import init_services

__all__ = ["router", "init_services"]
