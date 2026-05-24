"""
Bilibili 用户 API

提供用户信息、稍后观看、观看历史等功能
"""
from typing import Dict, Any, Tuple, List

from bilibili_api import user
from loguru import logger

from services.bilibili.client import BilibiliClient


class UserAPI(BilibiliClient):
    """Bilibili 用户 API"""

    async def get_self_profile(self) -> Tuple[bool, Dict[str, Any], str]:
        """
        获取当前登录用户的资料

        Returns:
            (success, profile, error_message)
        """
        credential = self.build_credential()
        if not credential:
            return False, {}, "Cookie 未配置或格式错误"

        await self.rate_limit()
        try:
            info = await user.get_self_info(credential=credential)
            level_info = info.get("level_exp") or {}
            profile = {
                "uid": int(info.get("mid") or info.get("uid") or 0),
                "name": info.get("name") or info.get("uname") or "",
                "avatar": info.get("face") or info.get("avatar") or "",
                "sign": info.get("sign") or "",
                "level": level_info.get("current_level") or info.get("level")
            }
            if not profile["uid"]:
                return False, {}, "未获取到用户 UID"
            return True, profile, ""
        except Exception as e:
            logger.error(f"获取当前用户资料失败：{e}")
            return False, {}, f"获取当前用户资料失败：{str(e)}"

    async def get_watch_later_list(self) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        获取稍后观看列表

        Returns:
            (success, videos, error_message)
        """
        credential = self.build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self.rate_limit()
        try:
            data = await user.get_toview_list(credential=credential)
            videos: List[Dict[str, Any]] = []
            for item in data.get("list", []):
                owner = item.get("owner", {})
                videos.append({
                    "bvid": item.get("bvid", ""),
                    "title": item.get("title", ""),
                    "cover": item.get("cover", ""),
                    "duration": item.get("duration", 0),
                    "owner_name": owner.get("name", ""),
                    "owner_mid": owner.get("mid", 0),
                    "add_at": item.get("add_at", 0)
                })
            return True, videos, ""
        except Exception as e:
            logger.error(f"获取稍后观看列表失败：{e}")
            return False, [], f"获取稍后观看列表失败：{str(e)}"

    async def get_watch_history(
        self, page: int = 1, page_size: int = 50
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        获取观看历史

        Args:
            page: 页码
            page_size: 每页数量

        Returns:
            (success, videos, error_message)
        """
        credential = self.build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self.rate_limit()
        try:
            # 使用正确的参数调用 API
            data = await user.get_self_history_new(
                credential=credential,
                _type=user.HistoryType.ALL,
                ps=page_size
            )

            # 检查 API 返回状态
            if data.get("code", 0) != 0:
                error_msg = data.get("message", "未知错误")
                logger.error(f"获取观看历史失败：接口返回错误代码：{data.get('code')}，信息：{error_msg}")
                return False, [], f"B站API错误：{error_msg}"

            videos: List[Dict[str, Any]] = []
            for item in data.get("list", {}).get("list", []):
                history = item.get("history", {})
                # 只处理视频类型
                if history.get("business") != "archive":
                    continue
                videos.append({
                    "bvid": history.get("bvid", ""),
                    "title": item.get("show_title", "") or history.get("title", ""),
                    "cover": item.get("cover", ""),
                    "duration": item.get("duration", 0),
                    "progress": history.get("progress", 0),
                    "owner_name": item.get("author_name", ""),
                    "view_at": item.get("view_at", 0)
                })
            return True, videos, ""
        except Exception as e:
            logger.error(f"获取观看历史失败：{e}")
            return False, [], f"获取观看历史失败：{str(e)}"
