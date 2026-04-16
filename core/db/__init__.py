"""
数据库模块

提供数据库连接管理、数据模型定义和仓库操作
"""
from core.db.connection import Database
from core.db.models import (
    Video,
    SyncHistory,
    Notification,
    DownloadProgress,
    FavoriteFolder,
    VideoCache
)

__all__ = [
    "Database",
    "Video",
    "SyncHistory",
    "Notification",
    "DownloadProgress",
    "FavoriteFolder",
    "VideoCache"
]
