"""
用户数据 API

提供稍后观看、观看历史等接口
"""
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from loguru import logger

from core.config import get_config
from core.database import Database, VideoCache
from services.bilibili_api import BilibiliAPI
from api.models import ApiResponse, WatchLaterModel, HistoryModel

router = APIRouter(tags=["用户数据"])


@router.get("/user/watch-later", response_model=ApiResponse)
async def get_watch_later(force_refresh: bool = False):
    """获取稍后观看列表

    Args:
        force_refresh: 是否强制从 B 站 API 刷新数据，默认 False 从缓存读取
    """
    try:
        db = await Database.get_instance()

        # 缓存优先：非强制刷新时先从缓存读取
        if not force_refresh:
            cached = await db.get_videos_by_source("watch_later")
            if cached:
                return ApiResponse(
                    success=True,
                    message="from cache",
                    data=[{
                        "bvid": v.bvid,
                        "title": v.title,
                        "cover": v.cover,
                        "duration": v.duration,
                        "owner_name": v.owner_name,
                        "updated_at": v.updated_at
                    } for v in cached]
                )

        # 调用 B 站 API 获取数据
        api = BilibiliAPI()
        success, videos, error = await api.get_watch_later_list()

        if success:
            # 清除旧缓存，保存新数据
            await db.clear_video_cache_by_source("watch_later")
            now = datetime.now().isoformat()
            for video in videos:
                cache = VideoCache(
                    id=None,
                    bvid=video["bvid"],
                    title=video["title"],
                    cover=video.get("cover", ""),
                    cover_local=None,
                    duration=video.get("duration", 0),
                    owner_name=video.get("owner_name", ""),
                    source_type="watch_later",
                    source_id=None,
                    created_at=now,
                    updated_at=now
                )
                await db.add_or_update_video_cache(cache)

            return ApiResponse(
                success=True,
                message="from api",
                data=[WatchLaterModel(**v).model_dump() for v in videos]
            )
        return ApiResponse(success=False, message=error, data=[])
    except Exception as e:
        logger.error(f"获取稍后观看列表失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/history", response_model=ApiResponse)
async def get_watch_history(page: int = 1, page_size: int = 50, force_refresh: bool = False):
    """获取观看历史

    Args:
        page: 页码
        page_size: 每页数量
        force_refresh: 是否强制从 B 站 API 刷新数据，默认 False 从缓存读取
    """
    try:
        db = await Database.get_instance()

        # 缓存优先：非强制刷新时先从缓存读取
        if not force_refresh:
            cached = await db.get_videos_by_source("history")
            if cached:
                return ApiResponse(
                    success=True,
                    message="from cache",
                    data=[{
                        "bvid": v.bvid,
                        "title": v.title,
                        "cover": v.cover,
                        "duration": v.duration,
                        "owner_name": v.owner_name,
                        "progress": 0,  # 缓存中没有进度信息
                        "view_at": None,
                        "updated_at": v.updated_at
                    } for v in cached]
                )

        # 调用 B 站 API 获取数据
        api = BilibiliAPI()
        success, videos, error = await api.get_watch_history(page=page, page_size=page_size)

        if success:
            # 清除旧缓存，保存新数据
            await db.clear_video_cache_by_source("history")
            now = datetime.now().isoformat()
            for video in videos:
                cache = VideoCache(
                    id=None,
                    bvid=video["bvid"],
                    title=video["title"],
                    cover=video.get("cover", ""),
                    cover_local=None,
                    duration=video.get("duration", 0),
                    owner_name=video.get("owner_name", ""),
                    source_type="history",
                    source_id=None,
                    created_at=now,
                    updated_at=now
                )
                await db.add_or_update_video_cache(cache)

            return ApiResponse(
                success=True,
                message="from api",
                data=[HistoryModel(**v).model_dump() for v in videos]
            )
        return ApiResponse(success=False, message=error, data=[])
    except Exception as e:
        logger.error(f"获取观看历史失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


async def auto_download_covers(videos: list, cover_type: str = "videos") -> None:
    """后台自动下载封面"""
    try:
        config = get_config()
        base_dir = Path(config.download.temp_dir).parent
        covers_dir = base_dir / "covers" / cover_type
        covers_dir.mkdir(parents=True, exist_ok=True)

        api = BilibiliAPI()

        for video in videos:
            cover_url = video.get("cover", "")
            identifier = video.get("bvid") or video.get("id")

            if not cover_url or not identifier:
                continue

            # 检查是否已下载
            exists = False
            for ext in ['.jpg', '.png', '.webp', '.jpeg']:
                if (covers_dir / f"{identifier}{ext}").exists():
                    exists = True
                    break

            if exists:
                continue

            # 确定扩展名
            ext = ".jpg"
            if "." in cover_url.split("/")[-1]:
                ext = "." + cover_url.split(".")[-1].split("?")[0]

            save_path = str(covers_dir / f"{identifier}{ext}")
            await api.download_cover(cover_url, save_path)

    except Exception as e:
        logger.error(f"自动下载封面失败：{e}")
