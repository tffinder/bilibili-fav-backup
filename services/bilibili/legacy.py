"""
Bilibili API 兼容层

保持原有的 BilibiliAPI 类接口，内部使用拆分后的模块实现
"""
from typing import Dict, Any, Tuple, List, Optional

from loguru import logger

from services.bilibili.auth import AuthAPI
from services.bilibili.favorite import FavoriteAPI
from services.bilibili.video import VideoAPI
from services.bilibili.user import UserAPI
from core.db.models import Video as DbVideo


class BilibiliAPI(AuthAPI, FavoriteAPI, VideoAPI, UserAPI):
    """
    Bilibili API 封装

    整合认证、收藏夹、视频、用户等功能
    保持与原 BilibiliAPI 类的向后兼容
    """

    def __init__(self):
        super().__init__()
        # 初始化各子模块的配置
        self.config = self.config  # 继承自 BilibiliClient
        self.cookie = self.cookie
        self.request_delay = self.request_delay

    # ==================== 认证相关方法（从 AuthAPI 继承）====================
    # validate_cookie, generate_login_qrcode, poll_login_qrcode
    # start_geetest, get_geetest_status, login_by_password
    # send_sms_code, login_by_sms

    # ==================== 收藏夹相关方法（从 FavoriteAPI 继承）====================
    # get_favorite_folders, get_favorite_folder_info, get_favorites_list

    # ==================== 视频相关方法（从 VideoAPI 继承）====================
    # get_video_info, get_video_pages, get_video_quality, extract_bvid
    # get_video_detail, download_video, download_cover

    # ==================== 用户相关方法（从 UserAPI 继承）====================
    # get_self_profile, get_watch_later_list, get_watch_history

    # ==================== 重写需要组合多模块的方法 ====================

    async def get_favorite_folders(self) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        获取当前登录用户的收藏夹列表

        Returns:
            (success, folders, error_message)
        """
        # 先获取用户信息
        success, profile, error = await self.get_self_profile()
        if not success:
            return False, [], error

        # 使用父类方法获取收藏夹
        return await super().get_favorite_folders(profile["uid"])
