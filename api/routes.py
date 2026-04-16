"""
API 路由模块
提供 RESTful API 接口
"""
import asyncio
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse
from loguru import logger

from core.config import get_config, ConfigManager
from core.database import Database, Video, FavoriteFolder, VideoCache
from services.bilibili_api import BilibiliAPI
from services.downloader import Downloader
from services.s3_uploader import S3Uploader
from services.sync_manager import SyncManager
from services.scheduler import TaskScheduler
from services.notification import NotificationService
from api.models import (
    ConfigModel, ConfigUpdateRequest, VideoModel,
    SyncHistoryModel, NotificationModel, DownloadProgressModel,
    StatusResponse, SyncStartRequest, ApiResponse, DashboardStats,
    UserProfileModel, FavoriteFolderModel, SingleVideoDownloadRequest,
    WatchLaterModel, HistoryModel, VideoCacheModel
)

router = APIRouter(prefix="/api", tags=["API"])

# 全局服务实例（由 main.py 注入）
_sync_manager: Optional[SyncManager] = None
_scheduler: Optional[TaskScheduler] = None


def init_services(sync_manager: SyncManager, scheduler: TaskScheduler) -> None:
    """初始化服务实例"""
    global _sync_manager, _scheduler
    _sync_manager = sync_manager
    _scheduler = scheduler


def _resolve_download_base() -> Path:
    config = get_config()
    base = Path(config.download.temp_dir)
    if not base.is_absolute():
        base = (Path(__file__).parent.parent / base).resolve()
    return base


def _resolve_local_path(path_value: Optional[str]) -> Optional[Path]:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = (Path(__file__).parent.parent / path).resolve()
    return path


def _build_play_url(video: Video, base_dir: Path) -> Optional[str]:
    if not video.local_path or not video.id:
        return None
    path = _resolve_local_path(video.local_path)
    if not path or not path.exists():
        return None
    if base_dir not in path.parents and path != base_dir:
        return None
    return f"/api/videos/{video.id}/file"


async def _download_single_video_task(bvid: str, target_quality: int) -> None:
    """后台下载单个视频的所有分P，并按当前配置决定是否上传 S3。"""
    api = BilibiliAPI()
    downloader = Downloader()
    uploader = S3Uploader()
    db = await Database.get_instance()
    config = get_config()

    info_ok, info, info_error = await api.get_video_info(bvid)
    if not info_ok:
        logger.error(f"获取视频信息失败 {bvid}: {info_error}")
        return

    pages_ok, pages, pages_error = await api.get_video_pages(bvid)
    if not pages_ok or not pages:
        logger.error(f"获取视频分页失败 {bvid}: {pages_error}")
        return

    quality_ok, qualities, _ = await api.get_video_quality(bvid, pages[0]["cid"])
    available_quality = qualities[0] if quality_ok and qualities else target_quality

    results = await downloader.download_multi_page_video(
        bvid=bvid,
        title=info["title"],
        pages=pages,
        target_quality=target_quality
    )

    for page_num, success, file_path, error in results:
        if not success or not file_path:
            logger.error(f"单视频下载失败 {bvid} P{page_num}: {error}")
            continue

        page_info = next((page for page in pages if page["page"] == page_num), None)
        if not page_info:
            continue

        video_obj = Video(
            id=None,
            bvid=bvid,
            title=f"{info['title']}_P{page_num}",
            cid=page_info["cid"],
            page=page_num,
            total_pages=len(pages),
            quality=available_quality,
            duration=page_info["duration"],
            pubdate=info.get("pubdate", 0),
            owner_name=info.get("owner_name", ""),
            s3_key=None,
            s3_uploaded=False,
            s3_quality=None,
            local_path=file_path,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )

        await db.add_video(video_obj)

        if config.s3.enabled:
            upload_success, s3_key, upload_error = await uploader.upload_video(
                video=video_obj,
                local_path=file_path,
                delete_after_upload=config.s3.delete_after_upload
            )
            if upload_success and s3_key:
                await db.update_video_s3_status(
                    bvid=video_obj.bvid,
                    page=video_obj.page,
                    s3_key=s3_key,
                    quality=available_quality
                )
            else:
                logger.error(f"单视频上传失败 {bvid} P{page_num}: {upload_error}")
        else:
            await db.update_video_s3_status(
                bvid=video_obj.bvid,
                page=video_obj.page,
                s3_key=f"local://{file_path}",
                quality=available_quality
            )


# ==================== 状态查询接口 ====================

@router.get("/status", response_model=StatusResponse)
async def get_status():
    """获取系统状态"""
    try:
        db = await Database.get_instance()
        config = get_config()
        
        # 获取视频统计
        all_videos = await db.get_all_videos()
        uploaded_count = sum(1 for v in all_videos if v.s3_uploaded)
        
        # 获取上次同步
        recent_history = await db.get_recent_sync_history(limit=1)
        last_sync = recent_history[0].to_dict() if recent_history else None
        
        # 检查 Cookie 有效性
        api = BilibiliAPI()
        cookie_valid, _ = await api.validate_cookie()
        
        # 检查 S3 连接（禁用 S3 时视为已通过）
        if config.s3.enabled:
            s3 = S3Uploader()
            s3_connected, _ = await s3.test_connection()
        else:
            s3_connected = True
        
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
            total_size_mb=0.0,  # 可以后续实现计算总大小
            recent_sync_count=len(recent_history),
            failed_downloads=failed_count
        )
        
    except Exception as e:
        logger.error(f"获取统计失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 视频管理接口 ====================

@router.get("/videos", response_model=List[VideoModel])
async def get_videos(
    page: int = 1,
    page_size: int = 50,
    status: Optional[str] = None  # "uploaded", "pending", "failed", "downloaded"
):
    """获取视频列表"""
    try:
        db = await Database.get_instance()
        all_videos = await db.get_all_videos()

        # 过滤
        if status == "uploaded":
            videos = [v for v in all_videos if v.s3_uploaded]
        elif status == "pending":
            videos = [v for v in all_videos if not v.s3_uploaded and not v.upload_failed]
        elif status == "failed":
            videos = [v for v in all_videos if v.upload_failed]
        elif status == "downloaded":
            videos = [v for v in all_videos if v.local_path]
        else:
            videos = all_videos

        # 分页
        start = (page - 1) * page_size
        end = start + page_size
        paginated = videos[start:end]

        base_dir = _resolve_download_base()
        progress_map = await db.get_progress_map([(v.bvid, v.page) for v in paginated])
        api = BilibiliAPI()
        cover_cache: dict = {}
        result = []
        for v in paginated:
            if v.bvid not in cover_cache:
                cover_ok, cover_info, _ = await api.get_video_info(v.bvid)
                cover_cache[v.bvid] = cover_info.get("cover_url") if cover_ok else ""

            model = VideoModel.model_validate(v)
            model.local_path = v.local_path
            model.play_url = _build_play_url(v, base_dir)
            model.cover_url = cover_cache.get(v.bvid) or ""
            model.max_quality = v.max_quality or v.quality
            model.upload_failed = v.upload_failed if hasattr(v, 'upload_failed') else False

            progress = progress_map.get((v.bvid, v.page))
            if progress:
                model.download_status = progress.status
                model.download_progress = progress.progress
            else:
                if v.s3_uploaded:
                    model.download_status = "completed"
                    model.download_progress = 100.0
                elif v.upload_failed:
                    model.download_status = "upload_failed"
                    model.download_progress = 100.0
                elif v.local_path:
                    model.download_status = "completed"
                    model.download_progress = 100.0
                else:
                    model.download_status = "pending"
                    model.download_progress = 0.0

            result.append(model)

        return result

    except Exception as e:
        logger.error(f"获取视频列表失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/videos/{bvid}", response_model=VideoModel)
async def get_video(bvid: str):
    """获取单个视频详情"""
    try:
        db = await Database.get_instance()
        video = await db.get_video_by_bvid(bvid)
        
        if not video:
            raise HTTPException(status_code=404, detail="视频不存在")
        
        base_dir = _resolve_download_base()
        model = VideoModel.model_validate(video)
        model.local_path = video.local_path
        model.play_url = _build_play_url(video, base_dir)

        progress = await db.get_progress_by_bvid_page(video.bvid, video.page)
        if progress:
            model.download_status = progress.status
            model.download_progress = progress.progress
        else:
            model.download_status = "completed" if video.s3_uploaded else "pending"
            model.download_progress = 100.0 if video.s3_uploaded else 0.0

        api = BilibiliAPI()
        cover_ok, cover_info, _ = await api.get_video_info(video.bvid)
        model.cover_url = cover_info.get("cover_url") if cover_ok else ""
        return model
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取视频详情失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 同步控制接口 ====================

@router.get("/videos/{video_id}/file")
async def get_video_file(video_id: int):
    """获取本地已下载视频文件"""
    try:
        db = await Database.get_instance()
        video = await db.get_video_by_id(video_id)
        if not video or not video.local_path:
            raise HTTPException(status_code=404, detail="视频文件不存在")

        base_dir = _resolve_download_base()
        path = _resolve_local_path(video.local_path)
        if not path or not path.exists():
            raise HTTPException(status_code=404, detail="视频文件不存在")
        if base_dir not in path.parents and path != base_dir:
            raise HTTPException(status_code=403, detail="禁止访问该路径")

        media_type, _ = mimetypes.guess_type(str(path))
        return FileResponse(
            str(path),
            media_type=media_type or "application/octet-stream",
            filename=path.name
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取视频文件失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/download-video", response_model=ApiResponse)
async def download_single_video(payload: SingleVideoDownloadRequest):
    """手动下载单个视频（支持 BV 号或链接）。"""
    try:
        config = get_config()
        api = BilibiliAPI()

        if not config.bilibili.cookie:
            return ApiResponse(success=False, message="请先登录或在设置中填写 Cookie")

        bvid = api.extract_bvid(payload.video_input)
        if not bvid:
            return ApiResponse(success=False, message="请输入有效的 BV 号或 B 站视频链接")

        valid, message = await api.validate_cookie()
        if not valid:
            return ApiResponse(success=False, message=message)

        target_quality = payload.quality or config.download.quality
        asyncio.create_task(_download_single_video_task(bvid, target_quality))

        return ApiResponse(
            success=True,
            message="已开始下载单个视频",
            data={"bvid": bvid, "quality": target_quality}
        )
    except Exception as e:
        logger.error(f"启动单视频下载失败：{e}")
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


# ==================== 下载进度接口 ====================

@router.get("/download-progress", response_model=List[DownloadProgressModel])
async def get_download_progress(active_only: bool = True):
    """获取下载进度"""
    try:
        db = await Database.get_instance()
        
        if active_only:
            progress_list = await db.get_active_downloads()
        else:
            progress_list = await db.get_all_progress()
        
        return [DownloadProgressModel.from_orm(p) for p in progress_list]
        
    except Exception as e:
        logger.error(f"获取下载进度失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 通知接口 ====================

@router.get("/notifications", response_model=List[NotificationModel])
async def get_notifications(unread_only: bool = False, limit: int = 50):
    """获取通知列表"""
    try:
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


# ==================== 配置接口 ====================

@router.get("/config", response_model=ConfigModel)
async def get_config_info():
    """获取完整配置信息"""
    try:
        config = get_config()
        
        return ConfigModel(
            bilibili_cookie=config.bilibili.cookie,
            bilibili_fav_id=config.bilibili.fav_id,
            bilibili_check_cookie_interval=config.bilibili.check_cookie_interval,
            download_quality=config.download.quality,
            download_max_parallel=config.download.max_parallel,
            download_temp_dir=config.download.temp_dir,
            download_retry_times=config.download.retry_times,
            download_request_delay=config.download.request_delay,
            s3_endpoint_url=config.s3.endpoint_url,
            s3_access_key=config.s3.access_key,
            s3_secret_key=config.s3.secret_key,
            s3_bucket_name=config.s3.bucket_name,
            s3_enabled=config.s3.enabled,
            s3_delete_after_upload=config.s3.delete_after_upload,
            s3_upload_timeout=config.s3.upload_timeout,
            s3_retry_times=config.s3.retry_times,
            s3_rate_limit=config.s3.rate_limit,
            s3_region_name=config.s3.region_name,
            scheduler_enabled=config.scheduler.enabled,
            scheduler_cron=config.scheduler.cron,
            scheduler_timezone=config.scheduler.timezone,
            notification_web_enabled=config.notification.web_enabled,
            notification_log_enabled=config.notification.log_enabled,
            notification_email_enabled=config.notification.email_enabled,
            notification_email_smtp_server=config.notification.email_smtp_server,
            notification_email_from=config.notification.email_from,
            notification_email_to=config.notification.email_to,
            notification_email_password=config.notification.email_password,
            debug_enabled=config.debug.enabled,
            debug_log_level=config.debug.log_level,
            debug_keep_temp_files=config.debug.keep_temp_files,
            debug_biliup_proxy=config.debug.biliup_proxy,
            debug_ffmpeg_path=config.debug.ffmpeg_path
        )
        
    except Exception as e:
        logger.error(f"获取配置失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# 配置字段映射: request字段 -> (config section, config field)
CONFIG_FIELD_MAPPING = {
    # bilibili
    'bilibili_cookie': ('bilibili', 'cookie'),
    'bilibili_fav_id': ('bilibili', 'fav_id'),
    'bilibili_check_cookie_interval': ('bilibili', 'check_cookie_interval'),
    # download
    'download_quality': ('download', 'quality'),
    'download_max_parallel': ('download', 'max_parallel'),
    'download_temp_dir': ('download', 'temp_dir'),
    'download_retry_times': ('download', 'retry_times'),
    'download_request_delay': ('download', 'request_delay'),
    # s3
    's3_endpoint_url': ('s3', 'endpoint_url'),
    's3_access_key': ('s3', 'access_key'),
    's3_secret_key': ('s3', 'secret_key'),
    's3_bucket_name': ('s3', 'bucket_name'),
    's3_enabled': ('s3', 'enabled'),
    's3_delete_after_upload': ('s3', 'delete_after_upload'),
    's3_upload_timeout': ('s3', 'upload_timeout'),
    's3_retry_times': ('s3', 'retry_times'),
    's3_rate_limit': ('s3', 'rate_limit'),
    's3_region_name': ('s3', 'region_name'),
    # scheduler
    'scheduler_enabled': ('scheduler', 'enabled'),
    'scheduler_cron': ('scheduler', 'cron'),
    'scheduler_timezone': ('scheduler', 'timezone'),
    # notification
    'notification_web_enabled': ('notification', 'web_enabled'),
    'notification_log_enabled': ('notification', 'log_enabled'),
    'notification_email_enabled': ('notification', 'email_enabled'),
    'notification_email_smtp_server': ('notification', 'email_smtp_server'),
    'notification_email_from': ('notification', 'email_from'),
    'notification_email_to': ('notification', 'email_to'),
    'notification_email_password': ('notification', 'email_password'),
    # debug
    'debug_enabled': ('debug', 'enabled'),
    'debug_log_level': ('debug', 'log_level'),
    'debug_keep_temp_files': ('debug', 'keep_temp_files'),
    'debug_biliup_proxy': ('debug', 'biliup_proxy'),
    'debug_ffmpeg_path': ('debug', 'ffmpeg_path'),
}


@router.put("/config", response_model=ApiResponse)
async def update_config(request: ConfigUpdateRequest):
    """更新配置"""
    try:
        config_manager = ConfigManager.get_instance()
        config = get_config()

        # 遍历已设置的字段，更新对应配置
        for request_field in request.model_fields_set:
            if request_field not in CONFIG_FIELD_MAPPING:
                continue

            section_name, config_field = CONFIG_FIELD_MAPPING[request_field]
            value = getattr(request, request_field)

            if value is not None:
                section = getattr(config, section_name)
                setattr(section, config_field, value)

        # 保存配置
        config_manager.save_config()

        # 配置变更后重载调度任务
        if _scheduler:
            _scheduler.reload_from_config()

        return ApiResponse(
            success=True,
            message="配置已更新"
        )

    except Exception as e:
        logger.error(f"更新配置失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 测试接口 ====================

@router.post("/test/cookie", response_model=ApiResponse)
async def test_cookie():
    """测试 B 站 Cookie 是否有效"""
    try:
        api = BilibiliAPI()
        valid, message = await api.validate_cookie()
        
        return ApiResponse(
            success=valid,
            message=message
        )
        
    except Exception as e:
        logger.error(f"测试 Cookie 失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/qrcode/start", response_model=ApiResponse)
async def start_qrcode_login():
    """开始 B 站扫码登录，返回二维码 URL 和 key"""
    try:
        api = BilibiliAPI()
        success, data, message = await api.generate_login_qrcode()
        return ApiResponse(success=success, message=message or ("ok" if success else "fail"), data=data)
    except Exception as e:
        logger.error(f"启动扫码登录失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/qrcode/poll", response_model=ApiResponse)
async def poll_qrcode_login(
    session_id: str = Query(..., description="扫码会话 ID"),
    auto_save_cookie: bool = Query(True, description="登录成功后自动保存 Cookie")
):
    """轮询扫码登录状态，成功后可自动保存 Cookie 到配置"""
    try:
        api = BilibiliAPI()
        success, data, message = await api.poll_login_qrcode(session_id)

        if success and data and data.get("status_code") == 0 and auto_save_cookie:
            cookie = data.get("cookie", "")
            if cookie:
                config_manager = ConfigManager.get_instance()
                config = get_config()
                config.bilibili.cookie = cookie
                config_manager.save_config()

        return ApiResponse(success=success, message=message or ("ok" if success else "fail"), data=data)
    except Exception as e:
        logger.error(f"轮询扫码登录失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/geetest/start", response_model=ApiResponse)
async def start_geetest():
    """开始极验（密码/短信登录前置）"""
    try:
        api = BilibiliAPI()
        success, data, message = await api.start_geetest()
        return ApiResponse(success=success, message=message or ("ok" if success else "fail"), data=data)
    except Exception as e:
        logger.error(f"启动极验失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/geetest/status", response_model=ApiResponse)
async def geetest_status(session_id: str = Query(..., description="极验会话 ID")):
    """查询极验状态"""
    try:
        api = BilibiliAPI()
        success, data, message = await api.get_geetest_status(session_id)
        return ApiResponse(success=success, message=message or ("ok" if success else "fail"), data=data)
    except Exception as e:
        logger.error(f"查询极验状态失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/password/login", response_model=ApiResponse)
async def password_login(payload: dict):
    """账号密码登录"""
    try:
        username = payload.get("username", "")
        password = payload.get("password", "")
        geetest_session_id = payload.get("geetest_session_id", "")
        if not username or not password or not geetest_session_id:
            return ApiResponse(success=False, message="缺少用户名/密码/geetest_session_id")

        api = BilibiliAPI()
        success, data, message = await api.login_by_password(username, password, geetest_session_id)
        if success:
            cookie = data.get("cookie", "")
            if cookie:
                config_manager = ConfigManager.get_instance()
                config = get_config()
                config.bilibili.cookie = cookie
                config_manager.save_config()
        return ApiResponse(success=success, message=message, data=data)
    except Exception as e:
        logger.error(f"密码登录失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/sms/send", response_model=ApiResponse)
async def send_sms_login_code(payload: dict):
    """发送短信验证码"""
    try:
        phone = payload.get("phone", "")
        country = payload.get("country", "+86")
        geetest_session_id = payload.get("geetest_session_id", "")
        if not phone or not geetest_session_id:
            return ApiResponse(success=False, message="缺少 phone/geetest_session_id")

        api = BilibiliAPI()
        success, data, message = await api.send_sms_code(phone, country, geetest_session_id)
        return ApiResponse(success=success, message=message, data=data)
    except Exception as e:
        logger.error(f"发送短信验证码失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/sms/login", response_model=ApiResponse)
async def sms_login(payload: dict):
    """短信验证码登录"""
    try:
        phone = payload.get("phone", "")
        country = payload.get("country", "+86")
        code = payload.get("code", "")
        captcha_id = payload.get("captcha_id", "")
        if not phone or not code or not captcha_id:
            return ApiResponse(success=False, message="缺少 phone/code/captcha_id")

        api = BilibiliAPI()
        success, data, message = await api.login_by_sms(phone, country, code, captcha_id)
        if success:
            cookie = data.get("cookie", "")
            if cookie:
                config_manager = ConfigManager.get_instance()
                config = get_config()
                config.bilibili.cookie = cookie
                config_manager.save_config()
        return ApiResponse(success=success, message=message, data=data)
    except Exception as e:
        logger.error(f"短信登录失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/me", response_model=ApiResponse)
async def get_current_user():
    """获取当前登录用户资料"""
    try:
        api = BilibiliAPI()
        success, data, message = await api.get_self_profile()
        if success:
            return ApiResponse(
                success=True,
                message=message or "ok",
                data=UserProfileModel(**data).model_dump()
            )
        return ApiResponse(success=False, message=message, data=None)
    except Exception as e:
        logger.error(f"获取当前用户资料失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/favorites", response_model=ApiResponse)
async def get_favorite_folders():
    """获取当前登录用户的视频收藏夹列表"""
    try:
        api = BilibiliAPI()
        success, data, message = await api.get_favorite_folders()
        if success:
            folders = [FavoriteFolderModel(**item).model_dump() for item in data]
            return ApiResponse(success=True, message=message or "ok", data=folders)
        return ApiResponse(success=False, message=message, data=[])
    except Exception as e:
        logger.error(f"获取收藏夹列表失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test/s3", response_model=ApiResponse)
async def test_s3():
    """测试 S3 连接是否正常"""
    try:
        s3 = S3Uploader()
        connected, message = await s3.test_connection()

        return ApiResponse(
            success=connected,
            message=message
        )

    except Exception as e:
        logger.error(f"测试 S3 连接失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test/s3/speed", response_model=ApiResponse)
async def test_s3_speed(file_size_mb: int = 10, unique_folder: bool = True):
    """
    测试 S3 上传速度

    Args:
        file_size_mb: 测试文件大小（MB），默认 10MB，最大 100MB
        unique_folder: 是否使用唯一文件夹（建议开启，避免 openist 同目录限制）
    """
    try:
        # 限制文件大小
        file_size_mb = min(max(file_size_mb, 1), 100)

        s3 = S3Uploader()
        success, result, error = await s3.test_upload_speed(
            file_size_mb=file_size_mb,
            use_unique_folder=unique_folder
        )

        if success:
            return ApiResponse(
                success=True,
                message=f"上传速度：{result['speed_mbps']:.2f} MB/s ({result['speed_mbps_bits']:.2f} Mbps)",
                data=result
            )
        else:
            return ApiResponse(success=False, message=error, data={})

    except Exception as e:
        logger.error(f"测试 S3 上传速度失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/test/s3/files", response_model=ApiResponse)
async def list_s3_files(limit: int = 20):
    """列出 S3 中最近上传的文件"""
    try:
        s3 = S3Uploader()
        success, files, error = await s3.list_recent_uploads(limit=limit)

        if success:
            return ApiResponse(success=True, message=f"共 {len(files)} 个文件", data=files)
        else:
            return ApiResponse(success=False, message=error, data=[])

    except Exception as e:
        logger.error(f"获取 S3 文件列表失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 日志接口 ====================

@router.get("/logs")
async def get_logs(lines: int = 100, level: str = "INFO"):
    """获取日志（需要从日志文件读取）"""
    try:
        from datetime import datetime
        from pathlib import Path

        log_dir = Path(__file__).parent.parent / "logs"
        today_file = log_dir / f"app_{datetime.now().strftime('%Y-%m-%d')}.log"

        if today_file.exists():
            log_file = today_file
        else:
            candidates = sorted(log_dir.glob("app_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                log_file = candidates[0]
            else:
                legacy_file = log_dir / "app.log"
                if not legacy_file.exists():
                    return {"logs": [], "message": "日志文件不存在"}
                log_file = legacy_file

        if not log_file.exists():
            return {"logs": [], "message": "日志文件不存在"}
        
        # 读取最后 N 行
        with open(log_file, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            last_lines = all_lines[-lines:]
        
        # 过滤级别
        filtered_lines = [
            line for line in last_lines
            if level.upper() in line or not level
        ]
        
        return {"logs": filtered_lines}

    except Exception as e:
        logger.error(f"获取日志失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 用户数据接口 ====================

@router.get("/user/watch-later", response_model=ApiResponse)
async def get_watch_later():
    """获取稍后观看列表"""
    try:
        api = BilibiliAPI()
        success, videos, error = await api.get_watch_later_list()

        if success:
            # 保存到数据库缓存
            db = await Database.get_instance()
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
                message="获取成功",
                data=[WatchLaterModel(**v).model_dump() for v in videos]
            )
        return ApiResponse(success=False, message=error, data=[])
    except Exception as e:
        logger.error(f"获取稍后观看列表失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/history", response_model=ApiResponse)
async def get_watch_history(page: int = 1, page_size: int = 50):
    """获取观看历史"""
    try:
        api = BilibiliAPI()
        success, videos, error = await api.get_watch_history(page=page, page_size=page_size)

        if success:
            # 保存到数据库缓存
            db = await Database.get_instance()
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
                message="获取成功",
                data=[HistoryModel(**v).model_dump() for v in videos]
            )
        return ApiResponse(success=False, message=error, data=[])
    except Exception as e:
        logger.error(f"获取观看历史失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 收藏夹管理接口 ====================

@router.get("/user/favorites", response_model=ApiResponse)
async def get_user_favorites():
    """获取用户所有收藏夹（带选中状态，包括自己添加的外部收藏夹）"""
    try:
        api = BilibiliAPI()
        db = await Database.get_instance()

        # 从数据库获取已保存的所有收藏夹（包括外部添加的）
        saved_folders = await db.get_all_favorite_folders()
        saved_map = {f.fav_id: f for f in saved_folders}

        # 获取所有收藏夹的视频缓存数量
        cache_count_map = await db.get_video_cache_count_map()

        # 从 B 站 API 获取自己的收藏夹列表
        success, folders, error = await api.get_favorite_folders()

        now = datetime.now().isoformat()
        result = []
        processed_ids = set()

        # 处理 B 站 API 返回的收藏夹
        if success:
            for folder in folders:
                fav_id = folder["id"]
                processed_ids.add(fav_id)
                saved = saved_map.get(fav_id)

                # 更新或创建收藏夹记录
                db_folder = FavoriteFolder(
                    id=saved.id if saved else None,
                    fav_id=fav_id,
                    title=folder["title"],
                    media_count=folder.get("media_count", 0),
                    cover=folder.get("cover", ""),
                    cover_local=saved.cover_local if saved else None,
                    selected=saved.selected if saved else folder.get("selected", False),
                    created_at=saved.created_at if saved else now,
                    updated_at=now
                )
                await db.add_or_update_favorite_folder(db_folder)

                result.append({
                    "id": fav_id,
                    "title": folder["title"],
                    "media_count": folder.get("media_count", 0),
                    "cache_count": cache_count_map.get(fav_id, 0),  # 已缓存数量
                    "cover": folder.get("cover", ""),
                    "cover_local": db_folder.cover_local,
                    "selected": db_folder.selected,
                    "is_external": False  # 自己的收藏夹
                })

        # 添加数据库中存在但 B 站 API 没返回的收藏夹（外部添加的）
        for saved in saved_folders:
            if saved.fav_id not in processed_ids:
                result.append({
                    "id": saved.fav_id,
                    "title": saved.title,
                    "media_count": saved.media_count,
                    "cache_count": cache_count_map.get(saved.fav_id, 0),  # 已缓存数量
                    "cover": saved.cover,
                    "cover_local": saved.cover_local,
                    "selected": saved.selected,
                    "is_external": True  # 外部收藏夹
                })

        return ApiResponse(success=True, message="获取成功", data=result)
    except Exception as e:
        logger.error(f"获取收藏夹列表失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/favorites/{fav_id}/select", response_model=ApiResponse)
async def select_favorite_folder(fav_id: int, selected: bool = True):
    """选择/取消选择收藏夹用于定时下载"""
    try:
        db = await Database.get_instance()
        await db.set_favorite_folder_selected(fav_id, selected)

        action = "已选择" if selected else "已取消选择"
        return ApiResponse(success=True, message=f"{action}收藏夹 {fav_id}")
    except Exception as e:
        logger.error(f"设置收藏夹选中状态失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/favorites/selected", response_model=ApiResponse)
async def get_selected_favorites():
    """获取已选择的收藏夹列表"""
    try:
        db = await Database.get_instance()
        folders = await db.get_selected_favorite_folders()

        return ApiResponse(
            success=True,
            message="获取成功",
            data=[f.to_dict() for f in folders]
        )
    except Exception as e:
        logger.error(f"获取已选收藏夹失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 外部收藏夹接口 ====================

@router.post("/user/favorites/external", response_model=ApiResponse)
async def add_external_favorite_folder(fav_id: int):
    """
    添加其他用户的收藏夹

    Args:
        fav_id: 收藏夹 ID（可以是其他用户的公开收藏夹）
    """
    try:
        api = BilibiliAPI()
        db = await Database.get_instance()

        # 获取收藏夹信息
        success, folder_info, error = await api.get_favorite_folder_info(fav_id)

        if not success:
            return ApiResponse(success=False, message=error, data=None)

        # 检查是否已存在
        existing = await db.get_favorite_folder(fav_id)
        now = datetime.now().isoformat()

        if existing:
            # 更新已存在的收藏夹
            existing.title = folder_info.get("title", existing.title)
            existing.media_count = folder_info.get("media_count", existing.media_count)
            existing.cover = folder_info.get("cover", existing.cover)
            existing.updated_at = now
            await db.add_or_update_favorite_folder(existing)
            return ApiResponse(
                success=True,
                message="收藏夹已更新",
                data=existing.to_dict()
            )

        # 创建新的收藏夹记录
        new_folder = FavoriteFolder(
            id=None,
            fav_id=fav_id,
            title=folder_info.get("title", f"收藏夹 {fav_id}"),
            media_count=folder_info.get("media_count", 0),
            cover=folder_info.get("cover", ""),
            cover_local=None,
            selected=False,  # 默认不选中
            created_at=now,
            updated_at=now
        )

        await db.add_or_update_favorite_folder(new_folder)

        logger.info(f"添加外部收藏夹：{new_folder.title} (ID: {fav_id})")

        return ApiResponse(
            success=True,
            message="收藏夹添加成功",
            data=new_folder.to_dict()
        )

    except Exception as e:
        logger.error(f"添加外部收藏夹失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/user/favorites/{fav_id}", response_model=ApiResponse)
async def remove_favorite_folder(fav_id: int):
    """
    移除收藏夹（仅从本地数据库移除，不会删除 B 站上的收藏夹）
    """
    try:
        db = await Database.get_instance()

        # 检查是否存在
        existing = await db.get_favorite_folder(fav_id)
        if not existing:
            return ApiResponse(success=False, message="收藏夹不存在", data=None)

        # 从数据库删除
        await db.delete_favorite_folder(fav_id)

        return ApiResponse(
            success=True,
            message=f"已移除收藏夹：{existing.title}"
        )

    except Exception as e:
        logger.error(f"移除收藏夹失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 封面下载接口 ====================

@router.post("/covers/download", response_model=ApiResponse)
async def download_covers():
    """下载所有缺失的封面图片"""
    try:
        api = BilibiliAPI()
        db = await Database.get_instance()
        config = get_config()

        # 确定封面保存目录
        base_dir = Path(config.download.temp_dir).parent
        covers_dir = base_dir / "covers"
        fav_covers_dir = covers_dir / "favorites"
        video_covers_dir = covers_dir / "videos"
        fav_covers_dir.mkdir(parents=True, exist_ok=True)
        video_covers_dir.mkdir(parents=True, exist_ok=True)

        downloaded_count = 0

        # 下载收藏夹封面
        folders = await db.get_all_favorite_folders()
        for folder in folders:
            if folder.cover and not folder.cover_local:
                # 从 URL 提取文件扩展名
                ext = ".jpg"
                if "." in folder.cover.split("/")[-1]:
                    ext = "." + folder.cover.split(".")[-1].split("?")[0]

                save_path = str(fav_covers_dir / f"{folder.fav_id}{ext}")
                success, error = await api.download_cover(folder.cover, save_path)

                if success:
                    await db.update_favorite_folder_cover(folder.fav_id, save_path)
                    downloaded_count += 1
                    logger.info(f"下载收藏夹封面：{folder.title}")

        # 下载视频封面
        video_caches = await db.get_videos_by_source("favorite")
        video_caches += await db.get_videos_by_source("watch_later")
        video_caches += await db.get_videos_by_source("history")

        for video in video_caches:
            if video.cover and not video.cover_local:
                ext = ".jpg"
                if "." in video.cover.split("/")[-1]:
                    ext = "." + video.cover.split(".")[-1].split("?")[0]

                save_path = str(video_covers_dir / f"{video.bvid}{ext}")
                success, error = await api.download_cover(video.cover, save_path)

                if success:
                    await db.update_video_cache_cover(video.bvid, save_path)
                    downloaded_count += 1
                    logger.info(f"下载视频封面：{video.title}")

        return ApiResponse(
            success=True,
            message=f"已下载 {downloaded_count} 个封面图片"
        )
    except Exception as e:
        logger.error(f"下载封面失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/covers/{bvid}")
async def get_video_cover(bvid: str):
    """获取视频封面图片"""
    try:
        db = await Database.get_instance()
        config = get_config()

        # 确定封面目录
        base_dir = Path(config.download.temp_dir).parent
        covers_dir = base_dir / "covers" / "videos"

        # 尝试直接从文件系统查找
        for ext in ['.jpg', '.png', '.webp', '.jpeg']:
            path = covers_dir / f"{bvid}{ext}"
            if path.exists():
                return FileResponse(
                    str(path),
                    media_type="image/jpeg",
                    filename=f"{bvid}{ext}"
                )

        # 从缓存中查找
        video_caches = await db.get_videos_by_source("favorite")
        video_caches += await db.get_videos_by_source("watch_later")
        video_caches += await db.get_videos_by_source("history")

        for video in video_caches:
            if video.bvid == bvid and video.cover_local:
                path = Path(video.cover_local)
                if path.exists():
                    return FileResponse(
                        str(path),
                        media_type="image/jpeg",
                        filename=f"{bvid}.jpg"
                    )

        raise HTTPException(status_code=404, detail="封面不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取封面失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/covers/folder/{fav_id}")
async def get_folder_cover(fav_id: int):
    """获取收藏夹封面图片"""
    try:
        config = get_config()
        base_dir = Path(config.download.temp_dir).parent
        covers_dir = base_dir / "covers" / "favorites"

        for ext in ['.jpg', '.png', '.webp', '.jpeg']:
            path = covers_dir / f"{fav_id}{ext}"
            if path.exists():
                return FileResponse(
                    str(path),
                    media_type="image/jpeg",
                    filename=f"{fav_id}{ext}"
                )

        raise HTTPException(status_code=404, detail="封面不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取收藏夹封面失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 收藏夹内容接口 ====================

@router.get("/user/favorites/{fav_id}/videos", response_model=ApiResponse)
async def get_favorite_folder_videos(fav_id: int, page: int = 1, page_size: int = 50):
    """获取收藏夹内的视频列表（带下载状态）"""
    try:
        api = BilibiliAPI()
        db = await Database.get_instance()

        # 从 B 站 API 获取收藏夹内容
        success, videos, error = await api.get_favorites_list(str(fav_id))

        if not success:
            return ApiResponse(success=False, message=error, data=[])

        # 获取已下载视频的状态映射
        all_downloaded = await db.get_all_videos()
        download_status_map = {}
        for v in all_downloaded:
            download_status_map[v.bvid] = {
                "downloaded": True,
                "s3_uploaded": v.s3_uploaded,
                "quality": v.quality,
                "upload_failed": v.upload_failed
            }

        # 保存到数据库缓存并添加状态
        now = datetime.now().isoformat()
        videos_with_status = []
        for video in videos:
            bvid = video["bvid"]

            # 保存缓存
            cache = VideoCache(
                id=None,
                bvid=bvid,
                title=video["title"],
                cover=video.get("cover", ""),
                cover_local=None,
                duration=video.get("duration", 0),
                owner_name=video.get("owner", ""),
                source_type="favorite",
                source_id=fav_id,
                created_at=now,
                updated_at=now
            )
            await db.add_or_update_video_cache(cache)

            # 添加下载状态
            status = download_status_map.get(bvid, {
                "downloaded": False,
                "s3_uploaded": False,
                "quality": None,
                "upload_failed": False
            })

            videos_with_status.append({
                **video,
                "downloaded": status["downloaded"],
                "s3_uploaded": status["s3_uploaded"],
                "quality": status["quality"],
                "upload_failed": status["upload_failed"]
            })

        # 分页
        total = len(videos_with_status)
        start = (page - 1) * page_size
        end = start + page_size
        paginated = videos_with_status[start:end]

        return ApiResponse(
            success=True,
            message="获取成功",
            data={
                "videos": paginated,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size
            }
        )
    except Exception as e:
        logger.error(f"获取收藏夹内容失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 自动下载封面 ====================

async def download_cover_async(url: str, save_path: str) -> bool:
    """异步下载封面"""
    api = BilibiliAPI()
    success, _ = await api.download_cover(url, save_path)
    return success


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
