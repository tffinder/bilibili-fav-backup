"""
Bilibili 收藏夹 API

提供收藏夹列表、收藏夹内容获取等功能
"""
from typing import Dict, Any, Tuple, List

from bilibili_api import favorite_list
from loguru import logger

from services.bilibili.client import BilibiliClient


class FavoriteAPI(BilibiliClient):
    """Bilibili 收藏夹 API"""

    async def get_favorite_folders(self, uid: int) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        获取用户的所有收藏夹

        Args:
            uid: 用户 UID

        Returns:
            (success, folders, error_message)
        """
        credential = self.build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self.rate_limit()
        try:
            data = await favorite_list.get_video_favorite_list(
                uid=uid,
                credential=credential
            )
            raw_folders = data.get("list") or data.get("data") or []
            selected_id = str(self.config.bilibili.fav_id or "")
            folders: List[Dict[str, Any]] = []

            for item in raw_folders:
                folder_id = item.get("id") or item.get("fid") or item.get("media_id")
                if folder_id is None:
                    continue
                folders.append({
                    "id": int(folder_id),
                    "title": item.get("title") or item.get("name") or f"收藏夹 {folder_id}",
                    "media_count": int(item.get("media_count") or item.get("count") or 0),
                    "cover": item.get("cover") or "",
                    "selected": str(folder_id) == selected_id
                })

            return True, folders, ""
        except Exception as e:
            logger.error(f"获取收藏夹列表失败：{e}")
            return False, [], f"获取收藏夹列表失败：{str(e)}"

    async def get_favorite_folder_info(self, fav_id: int) -> Tuple[bool, Dict[str, Any], str]:
        """
        获取任意收藏夹信息（支持其他用户的收藏夹）

        Args:
            fav_id: 收藏夹 ID

        Returns:
            (success, folder_info, error_message)
        """
        credential = self.build_credential()
        if not credential:
            return False, {}, "Cookie 未配置或格式错误"

        await self.rate_limit()
        try:
            # 获取收藏夹信息
            data = await favorite_list.get_video_favorite_list_content(
                media_id=fav_id,
                page=1,
                credential=credential
            )

            # 从返回数据中提取收藏夹信息
            info = data.get("info", {}) or {}
            medias = data.get("medias", []) or []

            folder_info = {
                "id": fav_id,
                "title": info.get("title", f"收藏夹 {fav_id}"),
                "media_count": info.get("media_count", len(medias)),
                "cover": info.get("cover", ""),
                "intro": info.get("intro", ""),
                "owner": info.get("upper", {}),
                "is_owner": info.get("attr", 0) == 0  # 是否是自己的收藏夹
            }

            return True, folder_info, ""
        except Exception as e:
            logger.error(f"获取收藏夹信息失败：{e}")
            return False, {}, f"获取收藏夹信息失败：{str(e)}"

    async def get_favorites_list(self, fav_id: str) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        获取收藏夹中的视频列表

        Args:
            fav_id: 收藏夹 ID

        Returns:
            (success, videos, error_message)
        """
        credential = self.build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self.rate_limit()
        try:
            media_id = int(fav_id)
        except ValueError:
            return False, [], "收藏夹 ID 不是数字"

        try:
            page = 1
            videos: List[Dict[str, Any]] = []
            while True:
                data = await favorite_list.get_video_favorite_list_content(
                    media_id=media_id,
                    page=page,
                    credential=credential
                )
                medias = data.get("medias", []) or []
                for media in medias:
                    videos.append({
                        "bvid": media.get("bvid", ""),
                        "title": media.get("title", ""),
                        "intro": media.get("intro", ""),
                        "pubdate": media.get("pubtime", 0),
                        "owner": (media.get("upper") or {}).get("name", "unknown"),
                        "cover": media.get("cover", ""),
                        "duration": media.get("duration", 0),
                        "fav_time": media.get("fav_time", 0),
                        "fav_title": data.get("info", {}).get("title", ""),
                        "attr": media.get("attr", 0),
                    })
                has_more = data.get("has_more", False)
                if not has_more or not medias:
                    break
                page += 1
                await self.rate_limit()
            return True, videos, ""
        except Exception as e:
            logger.error(f"获取收藏夹失败：{e}")
            return False, [], f"获取收藏夹失败：{str(e)}"
