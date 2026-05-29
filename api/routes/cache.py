"""
轻量级视频列表

基于 video_cache 表展示所有缓存视频及其下载状态
"""
from typing import Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel
from loguru import logger

from core.database import Database
from api.models import ApiResponse

router = APIRouter(prefix="/cache", tags=["缓存列表"])


class CacheVideoItem(BaseModel):
    bvid: str
    title: str
    owner_name: str = ""
    duration: int = 0
    cover_local: Optional[str] = None
    cover: Optional[str] = None
    source_type: str = ""
    source_id: Optional[int] = None
    folder_name: Optional[str] = None
    status: str = "pending"
    download_count: int = 0


@router.get("/videos", response_model=ApiResponse)
async def list_cached_videos(
    source_type: str = "favorite",
    source_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    search: str = "",
    status: str = "all",
):
    """获取缓存视频列表（轻量）"""
    try:
        db = await Database.get_instance()
        result = await db.get_cached_videos_with_status(
            source_type=source_type,
            source_id=source_id,
            page=page,
            page_size=page_size,
            search=search,
            status=status,
        )
        return ApiResponse(
            success=True,
            message=f"共 {result['total']} 个视频",
            data=result
        )
    except Exception as e:
        logger.error(f"获取缓存视频列表失败：{e}")
        raise


@router.get("/videos/stats", response_model=ApiResponse)
async def cached_videos_stats():
    """缓存视频统计"""
    try:
        db = await Database.get_instance()
        result = await db.get_cached_videos_with_status(
            source_type="favorite", page=1, page_size=1
        )
        total = result["total"]

        # 各状态计数
        counts = {"total": total, "downloaded": 0, "uploaded": 0, "pending": 0, "failed": 0, "source_deleted": 0}
        all_pages = (total + 499) // 500
        for p in range(1, all_pages + 1):
            batch = await db.get_cached_videos_with_status(
                source_type="favorite", page=p, page_size=500
            )
            for item in batch["items"]:
                s = item["status"]
                if s in counts:
                    counts[s] += 1

        return ApiResponse(success=True, message="ok", data=counts)
    except Exception as e:
        logger.error(f"获取缓存统计失败：{e}")
        raise
