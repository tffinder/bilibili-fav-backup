"""
Bilibili UP 主视频 API

提供获取指定 UP 主的投稿视频列表、用户信息等功能
"""
from typing import Dict, Any, Tuple, List

from bilibili_api import user
from loguru import logger

from services.bilibili.client import BilibiliClient


class UploaderAPI(BilibiliClient):
    """Bilibili UP 主视频 API"""

    async def get_uploader_info(self, mid: int) -> Tuple[bool, Dict[str, Any], str]:
        """
        获取 UP 主基本信息

        Args:
            mid: UP 主 UID

        Returns:
            (success, info_dict, error_message)
        """
        credential = self.build_credential()
        if not credential:
            return False, {}, "Cookie 未配置或格式错误"

        await self.rate_limit()
        try:
            u = user.User(uid=mid, credential=credential)
            info = await u.get_user_info()
            return True, {
                "mid": mid,
                "name": info.get("name", ""),
                "face": info.get("face", ""),
                "sign": info.get("sign", ""),
                "level": info.get("level", 0),
            }, ""
        except Exception as e:
            logger.error(f"获取 UP 主 {mid} 信息失败：{e}")
            return False, {}, f"获取 UP 主信息失败：{str(e)}"

    async def get_uploader_videos(
        self, mid: int, page: int = 1, page_size: int = 30
    ) -> Tuple[bool, List[Dict[str, Any]], int, str]:
        """
        获取 UP 主投稿视频列表（单页）

        Args:
            mid: UP 主 UID
            page: 页码
            page_size: 每页数量

        Returns:
            (success, videos_list, total_count, error_message)
        """
        credential = self.build_credential()
        if not credential:
            return False, [], 0, "Cookie 未配置或格式错误"

        await self.rate_limit()
        try:
            u = user.User(uid=mid, credential=credential)
            data = await u.get_videos(pn=page, ps=page_size)

            vlist = data.get("list", {}).get("vlist", [])
            total = data.get("page", {}).get("count", 0)

            videos: List[Dict[str, Any]] = []
            for item in vlist:
                videos.append({
                    "bvid": item.get("bvid", ""),
                    "title": item.get("title", ""),
                    "pubdate": item.get("created", 0),
                    "owner": item.get("author", ""),
                    "cover": item.get("pic", ""),
                    "duration": self._parse_duration(item.get("length", "0:00")),
                    "fav_time": None,
                    "fav_title": None,
                })
            return True, videos, total, ""
        except Exception as e:
            logger.error(f"获取 UP 主 {mid} 投稿列表失败：{e}")
            return False, [], 0, f"获取投稿列表失败：{str(e)}"

    async def get_all_uploader_videos(
        self, mid: int
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        获取 UP 主全部投稿视频（自动分页）

        Args:
            mid: UP 主 UID

        Returns:
            (success, all_videos, error_message)
        """
        page = 1
        page_size = 30
        all_videos: List[Dict[str, Any]] = []

        while True:
            success, videos, total, error = await self.get_uploader_videos(
                mid, page=page, page_size=page_size
            )
            if not success:
                if all_videos:
                    logger.warning(
                        f"UP 主 {mid} 第 {page} 页获取失败，已获取 {len(all_videos)} 个视频: {error}"
                    )
                    return True, all_videos, ""
                return False, [], error

            all_videos.extend(videos)

            if len(all_videos) >= total or len(videos) < page_size:
                break
            page += 1

        logger.info(f"UP 主 {mid} 共获取 {len(all_videos)} 个投稿视频")
        return True, all_videos, ""

    @staticmethod
    def _parse_duration(length_str: str) -> int:
        """将 'MM:SS' 或 'HH:MM:SS' 格式转为秒数"""
        try:
            parts = length_str.split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            return 0
        except (ValueError, AttributeError):
            return 0
