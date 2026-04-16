"""
B 站 API 模块 - 向后兼容层

此模块保持向后兼容，实际实现已迁移到 services/bilibili/ 目录
"""
# 从新模块导入所有内容，保持向后兼容
from services.bilibili import BilibiliAPI

__all__ = ["BilibiliAPI"]
