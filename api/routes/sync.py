"""
同步控制 API

提供同步启动、进度查询、历史记录等接口
"""
import asyncio
from typing import Optional, List

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


def init_services(sync_manager: SyncManager, scheduler: TaskScheduler) -> None:
    """初始化服务实例"""
    global _sync_manager, _scheduler
    _sync_manager = sync_manager
    _scheduler = scheduler


@router.get("/status", response_model=StatusResponse)
async def get_status():
    """获取系统状态"""
    try:
        from services.bilibili_api import BilibiliAPI
        from services.s3_uploader import S3Uploader

        db = await Database.get_instance()
        config = None
        try:
            from core.config import get_config
            config = get_config()
        except:
            pass

        # 获取视频统计
        all_videos = await db.get_all_videos()
        uploaded_count = sum(1 for v in all_videos if v.s3_uploaded)

        # 获取上次同步
        recent_history = await db.get_recent_sync_history(limit=1)
        last_sync = recent_history[0].to_dict() if recent_history else None

        # 检查 Cookie 有效性
        cookie_valid = False
        try:
            api = BilibiliAPI()
            cookie_valid, _ = await api.validate_cookie()
        except:
            pass

        # 检查 S3 连接
        s3_connected = True
        if config and config.s3.enabled:
            try:
                s3 = S3Uploader()
                s3_connected, _ = await s3.test_connection()
            except:
                s3_connected = False

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
            uploaded_videos=uploaded_count,
            pending_videos=len(all_videos) - uploaded_count,
            last_sync=last_sync,
            scheduler_enabled=scheduler_enabled,
            next_run_time=next_run_time,
            cookie_valid=cookie_valid,
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
