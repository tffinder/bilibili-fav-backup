"""
Bilibili API 模块

提供 B 站 API 的封装，包括认证、收藏夹、视频、用户等功能
"""
from services.bilibili.client import BilibiliClient
from services.bilibili.auth import AuthAPI
from services.bilibili.favorite import FavoriteAPI
from services.bilibili.video import VideoAPI
from services.bilibili.user import UserAPI
from services.bilibili.uploader import UploaderAPI

# 为向后兼容，导出原有的 BilibiliAPI 类
from services.bilibili.legacy import BilibiliAPI

__all__ = [
    "BilibiliClient",
    "AuthAPI",
    "FavoriteAPI",
    "VideoAPI",
    "UserAPI",
    "UploaderAPI",
    "BilibiliAPI"
]
