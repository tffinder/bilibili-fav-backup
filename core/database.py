"""
数据库模块 - 向后兼容层

此模块保持向后兼容，实际实现已迁移到 core/db/ 目录
"""
# 从新模块导入所有内容，保持向后兼容
from core.db import (
    Database,
    Video,
    SyncHistory,
    Notification,
    DownloadProgress,
    FavoriteFolder,
    VideoCache
)
from core.db.connection import require_db

__all__ = [
    "Database",
    "Video",
    "SyncHistory",
    "Notification",
    "DownloadProgress",
    "FavoriteFolder",
    "VideoCache",
    "require_db"
]
