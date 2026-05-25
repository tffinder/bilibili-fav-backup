"""
同步控制 API

提供同步启动、进度查询、历史记录等接口
"""
import asyncio
import time
from typing import Optional, List, Tuple

from fastapi import APIRouter, HTTPException
from loguru import logger

from core.database import Database
from services.sync_manager import SyncManager
from services.scheduler import TaskScheduler
from api.models import (
    StatusResponse, SyncHistoryModel, ApiResponse,
    SyncStartRequest, DashboardStats
)

router = APIRouter(tags=["同步控制"])

# 全局服务实例（由 main.py 注入）
_sync_manager: Optional[SyncManager] = None
_scheduler: Optional[TaskScheduler] = None
_cookie_status_cache: Tuple[float, bool] = (0.0, False)
_s3_status_cache: Tuple[float, bool] = (0.0, False)
_STATUS_CHECK_TTL_SECONDS = 60.0


def _progress_failed(progress) -> bool:
    return bool(progress and progress.status in {"failed", "upload_failed"})


def _is_local_only(video) -> bool:
    """本地保留模式：s3_uploaded 为真但 s3_key 是 local:// 占位符"""
    return bool(video.s3_uploaded and video.s3_key and video.s3_key.startswith("local://"))


def _matches_archive_status(video, status: str, progress=None) -> bool:
    if status == "uploaded":
        return video.s3_uploaded and not _is_local_only(video)
    if status == "downloaded":
        return bool(video.local_path or video.s3_uploaded)
    if status == "failed":
        return not video.s3_uploaded and bool(video.upload_failed or _progress_failed(progress))
    if status == "pending":
        return not video.s3_uploaded and not video.upload_failed and not _progress_failed(progress)
    return True


def init_services(sync_manager: SyncManager, scheduler: TaskScheduler) -> None:
    """初始化服务实例"""
    global _sync_manager, _scheduler
    _sync_manager = sync_manager
    _scheduler = scheduler


@router.get("/status", response_model=StatusResponse)
async def get_status():
    """获取系统状态"""
    global _cookie_status_cache, _s3_status_cache

    try:
        from services.bilibili_api import BilibiliAPI
        from services.s3_uploader import S3Uploader

        db = await Database.get_instance()
        config = None
        try:
            from core.config import get_config
            config = get_config()
        except Exception:
            pass

        # 获取视频统计
        all_videos = await db.get_all_videos()
        progress_map = {
            (p.bvid, p.page): p
            for p in await db.get_all_progress()
        }
        downloaded_count = sum(
            1 for v in all_videos
            if _matches_archive_status(v, "downloaded", progress_map.get((v.bvid, v.page)))
        )
        uploaded_count = sum(
            1 for v in all_videos
            if _matches_archive_status(v, "uploaded", progress_map.get((v.bvid, v.page)))
        )
        pending_count = sum(
            1 for v in all_videos
            if _matches_archive_status(v, "pending", progress_map.get((v.bvid, v.page)))
        )
        failed_count = sum(
            1 for v in all_videos
            if _matches_archive_status(v, "failed", progress_map.get((v.bvid, v.page)))
        )

        # 获取上次同步
        recent_history = await db.get_recent_sync_history(limit=1)
        last_sync = recent_history[0].to_dict() if recent_history else None

        # 检查 Cookie 有效性（带缓存，避免页面轮询持续打外部接口）
        cookie_configured = bool(config and config.bilibili.cookie)
        cookie_valid = False
        now_ts = time.monotonic()
        if cookie_configured:
            cached_at, cached_value = _cookie_status_cache
            if now_ts - cached_at < _STATUS_CHECK_TTL_SECONDS:
                cookie_valid = cached_value
            else:
                try:
                    api = BilibiliAPI()
                    cookie_valid, _ = await api.validate_cookie()
                    _cookie_status_cache = (now_ts, cookie_valid)
                except Exception:
                    _cookie_status_cache = (now_ts, False)

        # 检查 S3 连接（带缓存，避免频繁 list bucket）
        s3_enabled = bool(config and config.s3.enabled)
        s3_connected = False
        if config and config.s3.enabled:
            cached_at, cached_value = _s3_status_cache
            if now_ts - cached_at < _STATUS_CHECK_TTL_SECONDS:
                s3_connected = cached_value
            else:
                try:
                    s3 = S3Uploader()
                    s3_connected, _ = await s3.test_connection()
                    _s3_status_cache = (now_ts, s3_connected)
                except Exception:
                    _s3_status_cache = (now_ts, False)

        # 获取调度器信息
        scheduler_enabled = False
        next_run_time = None

        if _scheduler:
            job_info = _scheduler.get_job_info()
            scheduler_enabled = job_info.get("enabled", False)
            next_run_time = job_info.get("next_run")

        return StatusResponse(
            is_syncing=_sync_manager.is_syncing if _sync_manager else False,
            total_videos=len(all_videos),
            downloaded_videos=downloaded_count,
            uploaded_videos=uploaded_count,
            pending_videos=pending_count,
            failed_videos=failed_count,
            last_sync=last_sync,
            scheduler_enabled=scheduler_enabled,
            next_run_time=next_run_time,
            cookie_configured=cookie_configured,
            cookie_valid=cookie_valid,
            s3_enabled=s3_enabled,
            s3_connected=s3_connected
        )

    except Exception as e:
        logger.error(f"获取状态失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats", response_model=DashboardStats)
async def get_stats():
    """获取仪表盘统计"""
    try:
        db = await Database.get_instance()

        all_videos = await db.get_all_videos()
        uploaded_count = sum(1 for v in all_videos if v.s3_uploaded)

        # 获取最近的失败记录
        recent_history = await db.get_recent_sync_history(limit=10)
        failed_count = sum(h.failed_videos for h in recent_history)

        return DashboardStats(
            total_videos=len(all_videos),
            uploaded_videos=uploaded_count,
            pending_videos=len(all_videos) - uploaded_count,
            total_size_mb=0.0,
            recent_sync_count=len(recent_history),
            failed_downloads=failed_count
        )

    except Exception as e:
        logger.error(f"获取统计失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sync/preview")
async def get_sync_preview():
    """
    预览待同步的视频列表

    返回：
    - 待下载的新视频
    - 待升级清晰度的视频（有更高清晰度可用）
    """
    try:
        from services.bilibili_api import BilibiliAPI

        if not _sync_manager:
            raise HTTPException(status_code=503, detail="同步服务未初始化")

        db = await Database.get_instance()
        api = BilibiliAPI()

        # 获取选中的收藏夹
        selected_folders = await db.get_selected_favorite_folders()

        if not selected_folders:
            # 使用默认收藏夹
            from core.config import get_config
            config = get_config()
            if config.bilibili.fav_id:
                fav_ids = [str(config.bilibili.fav_id)]
            else:
                return {"new_videos": [], "upgrade_videos": [], "total": 0, "message": "未选择收藏夹"}
        else:
            fav_ids = [str(f.fav_id) for f in selected_folders]

        new_videos = []
        upgrade_videos = []

        for fav_id in fav_ids:
            # 获取收藏夹视频列表
            success, videos_list, error = await api.get_favorites_list(fav_id)
            if not success:
                continue

            for video_info in videos_list:
                bvid = video_info["bvid"]
                title = video_info["title"]

                # 获取视频分P和清晰度信息
                success, pages, _ = await api.get_video_pages(bvid)
                if not success or not pages:
                    continue

                success, qualities, _ = await api.get_video_quality(bvid, pages[0]["cid"])
                available_quality = qualities[0] if qualities else 16

                # 检查是否已备份
                best_uploaded = await db.get_best_uploaded_quality(bvid)

                if best_uploaded is None:
                    # 新视频
                    new_videos.append({
                        "bvid": bvid,
                        "title": title,
                        "available_quality": available_quality,
                        "quality_label": _quality_to_label(available_quality),
                        "pages": len(pages),
                        "duration": pages[0].get("duration", 0),
                        "cover": video_info.get("cover", ""),
                        "owner": video_info.get("owner", "")
                    })
                elif available_quality > best_uploaded:
                    # 可升级
                    upgrade_videos.append({
                        "bvid": bvid,
                        "title": title,
                        "current_quality": best_uploaded,
                        "current_quality_label": _quality_to_label(best_uploaded),
                        "available_quality": available_quality,
                        "available_quality_label": _quality_to_label(available_quality),
                        "pages": len(pages),
                        "duration": pages[0].get("duration", 0),
                        "cover": video_info.get("cover", ""),
                        "owner": video_info.get("owner", "")
                    })

        return {
            "new_videos": new_videos,
            "upgrade_videos": upgrade_videos,
            "total": len(new_videos) + len(upgrade_videos),
            "message": None
        }

    except Exception as e:
        logger.error(f"预览同步失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


def _quality_to_label(quality: int) -> str:
    """将清晰度代码转换为可读标签"""
    mapping = {
        127: "8K", 126: "4K HDR", 125: "4K Dolby", 120: "4K",
        116: "1080P60", 112: "1080P+", 80: "1080P",
        74: "720P60", 64: "720P", 48: "720P Dolby",
        32: "480P", 16: "360P"
    }
    return mapping.get(quality, f"{quality}P")


@router.post("/sync/start", response_model=ApiResponse)
async def start_sync(request: SyncStartRequest = None):
    """手动启动同步"""
    try:
        if not _sync_manager:
            raise HTTPException(status_code=503, detail="同步服务未初始化")

        if _sync_manager.is_syncing:
            return ApiResponse(
                success=False,
                message="同步任务正在进行中"
            )

        # 在后台启动同步任务
        asyncio.create_task(_sync_manager.start_sync(manual=True))

        return ApiResponse(
            success=True,
            message="同步任务已启动"
        )

    except Exception as e:
        logger.error(f"启动同步失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sync/interrupted", response_model=ApiResponse)
async def get_interrupted_count():
    """查询上次中断的任务数量。"""
    try:
        db = await Database.get_instance()
        tasks = await db.get_interrupted_tasks()
        return ApiResponse(success=True, message="ok", data={
            "count": len(tasks),
            "tasks": [
                {"id": t["id"], "bvid": t["bvid"], "title": t["title"], "fav_title": t.get("fav_title")}
                for t in tasks[:20]
            ]
        })
    except Exception as e:
        logger.error(f"查询中断任务失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync/resume", response_model=ApiResponse)
async def resume_interrupted_sync():
    """继续上次被中断的任务。"""
    try:
        if not _sync_manager:
            raise HTTPException(status_code=503, detail="同步服务未初始化")
        if _sync_manager.is_syncing:
            return ApiResponse(success=False, message="同步任务正在进行中")

        db = await Database.get_instance()
        tasks = await db.get_interrupted_tasks()
        if not tasks:
            return ApiResponse(success=False, message="没有需要继续的中断任务")

        asyncio.create_task(_sync_manager.resume_interrupted())
        return ApiResponse(
            success=True,
            message=f"已继续 {len(tasks)} 条中断任务",
            data={"count": len(tasks)}
        )
    except Exception as e:
        logger.error(f"继续中断任务失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sync/progress")
async def get_sync_progress():
    """获取同步进度"""
    if not _sync_manager:
        return {"is_syncing": False, "progress": {}}

    return {
        "is_syncing": _sync_manager.is_syncing,
        "progress": _sync_manager.get_sync_progress()
    }


@router.get("/sync/history", response_model=List[SyncHistoryModel])
async def get_sync_history(limit: int = 10):
    """获取同步历史"""
    try:
        db = await Database.get_instance()
        history = await db.get_recent_sync_history(limit=limit)
        return [SyncHistoryModel.from_orm(h) for h in history]

    except Exception as e:
        logger.error(f"获取同步历史失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 通知接口 ====================

@router.get("/notifications", response_model=List)
async def get_notifications(unread_only: bool = False, limit: int = 50):
    """获取通知列表"""
    try:
        from services.notification import NotificationService
        from api.models import NotificationModel

        notification_service = NotificationService.get_instance()

        if unread_only:
            notifications = await notification_service.get_unread_notifications()
        else:
            notifications = await notification_service.get_all_notifications(limit)

        return [NotificationModel.from_orm(n) for n in notifications]

    except Exception as e:
        logger.error(f"获取通知失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notifications/mark-read", response_model=ApiResponse)
async def mark_notifications_read(notification_id: Optional[int] = None):
    """标记通知为已读"""
    try:
        from services.notification import NotificationService

        notification_service = NotificationService.get_instance()

        if notification_id:
            await notification_service.mark_as_read(notification_id)
        else:
            await notification_service.mark_all_as_read()

        return ApiResponse(
            success=True,
            message="操作成功"
        )

    except Exception as e:
        logger.error(f"标记通知失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))
