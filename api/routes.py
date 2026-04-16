"""
API 路由模块 - 向后兼容层

此模块保持向后兼容，实际实现已迁移到 api/routes/ 目录
"""
# 从新模块导入所有内容，保持向后兼容
from api.routes import router, init_services

__all__ = ["router", "init_services"]
