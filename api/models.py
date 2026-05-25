"""
API 数据模型
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime


class ConfigModel(BaseModel):
    """配置模型"""
    bilibili_cookie: str = ""
    bilibili_fav_id: str = ""
    bilibili_check_cookie_interval: int = 3600
    download_quality: int = 127
    download_max_parallel: int = 1
    download_temp_dir: str = "./temp"
    download_retry_times: int = 3
    download_request_delay: float = 1.0
    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_name: str = ""
    s3_enabled: bool = False
    s3_delete_after_upload: bool = False
    s3_upload_timeout: int = 300
    s3_retry_times: int = 3
    s3_rate_limit: Optional[float] = None
    s3_region_name: str = "us-east-1"
    scheduler_enabled: bool = True
    scheduler_cron: str = "0 2 * * *"
    scheduler_timezone: str = "Asia/Shanghai"
    notification_web_enabled: bool = True
    notification_log_enabled: bool = True
    notification_email_enabled: bool = False
    notification_email_smtp_server: str = ""
    notification_email_from: str = ""
    notification_email_to: str = ""
    notification_email_password: str = ""
    debug_enabled: bool = False
    debug_log_level: str = "INFO"
    debug_keep_temp_files: bool = False
    debug_biliup_proxy: Optional[str] = None
    debug_ffmpeg_path: Optional[str] = None
    skip_max_single_duration: int = 0
    skip_max_total_duration: int = 0
    skip_interactive: bool = False
    skip_max_video_size_gib: float = 0
    rclone_enabled: bool = False
    rclone_delete_after_upload: bool = False
    rclone_remote_name: str = ""
    rclone_remote_path: str = ""
    rclone_remote_type: str = ""
    rclone_host: str = ""
    rclone_user: str = ""
    rclone_password: str = ""
    rclone_token: str = ""
    rclone_vendor: str = ""
    rclone_extra_flags: str = ""
    up_sync_enabled: bool = False
    up_sync_cron: str = "0 3 * * *"


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""
    bilibili_cookie: Optional[str] = None
    bilibili_fav_id: Optional[str] = None
    bilibili_check_cookie_interval: Optional[int] = None
    download_quality: Optional[int] = None
    download_max_parallel: Optional[int] = None
    download_temp_dir: Optional[str] = None
    download_retry_times: Optional[int] = None
    download_request_delay: Optional[float] = None
    s3_endpoint_url: Optional[str] = None
    s3_access_key: Optional[str] = None
    s3_secret_key: Optional[str] = None
    s3_bucket_name: Optional[str] = None
    s3_enabled: Optional[bool] = None
    s3_delete_after_upload: Optional[bool] = None
    s3_upload_timeout: Optional[int] = None
    s3_retry_times: Optional[int] = None
    s3_rate_limit: Optional[float] = None
    s3_region_name: Optional[str] = None
    scheduler_enabled: Optional[bool] = None
    scheduler_cron: Optional[str] = None
    scheduler_timezone: Optional[str] = None
    notification_web_enabled: Optional[bool] = None
    notification_log_enabled: Optional[bool] = None
    notification_email_enabled: Optional[bool] = None
    notification_email_smtp_server: Optional[str] = None
    notification_email_from: Optional[str] = None
    notification_email_to: Optional[str] = None
    notification_email_password: Optional[str] = None
    debug_enabled: Optional[bool] = None
    debug_log_level: Optional[str] = None
    debug_keep_temp_files: Optional[bool] = None
    debug_biliup_proxy: Optional[str] = None
    debug_ffmpeg_path: Optional[str] = None
    skip_max_single_duration: Optional[int] = None
    skip_max_total_duration: Optional[int] = None
    skip_interactive: Optional[bool] = None
    skip_max_video_size_gib: Optional[float] = None
    rclone_enabled: Optional[bool] = None
    rclone_delete_after_upload: Optional[bool] = None
    rclone_remote_name: Optional[str] = None
    rclone_remote_path: Optional[str] = None
    rclone_remote_type: Optional[str] = None
    rclone_host: Optional[str] = None
    rclone_user: Optional[str] = None
    rclone_password: Optional[str] = None
    rclone_token: Optional[str] = None
    rclone_vendor: Optional[str] = None
    rclone_extra_flags: Optional[str] = None
    up_sync_enabled: Optional[bool] = None
    up_sync_cron: Optional[str] = None


class SingleVideoDownloadRequest(BaseModel):
    """单视频下载请求"""
    video_input: str
    quality: Optional[int] = None


class UserProfileModel(BaseModel):
    """当前登录用户资料"""
    uid: int
    name: str
    avatar: str = ""
    sign: str = ""
    level: Optional[int] = None


class FavoriteFolderModel(BaseModel):
    """视频收藏夹"""
    id: int
    title: str
    media_count: int = 0
    cover: str = ""
    selected: bool = False


class VideoModel(BaseModel):
    """视频模型"""
    id: Optional[int]
    bvid: str
    title: str
    page: int
    total_pages: int
    quality: int
    s3_uploaded: bool
    s3_quality: Optional[int]
    created_at: str
    local_path: Optional[str] = None
    play_url: Optional[str] = None
    cover_url: Optional[str] = None
    download_status: Optional[str] = None
    download_progress: Optional[float] = None
    # 新增字段
    max_quality: Optional[int] = None  # 视频最高可用分辨率
    upload_failed: Optional[bool] = False  # 上传是否失败
    source_available: bool = True
    source_status: str = "unknown"
    source_checked_at: Optional[str] = None
    source_deleted_at: Optional[str] = None
    source_error: Optional[str] = None
    s3_key: Optional[str] = None
    download_url: Optional[str] = None
    remote_download_url: Optional[str] = None

    class Config:
        from_attributes = True


class SyncHistoryModel(BaseModel):
    """同步历史模型"""
    id: Optional[int]
    fav_id: str
    total_videos: int
    new_videos: int
    updated_videos: int
    skipped_videos: int
    failed_videos: int
    start_time: str
    end_time: Optional[str]
    status: str
    
    class Config:
        from_attributes = True


class NotificationModel(BaseModel):
    """通知模型"""
    id: Optional[int]
    level: str
    title: str
    message: str
    created_at: str
    is_read: bool
    
    class Config:
        from_attributes = True


class DownloadProgressModel(BaseModel):
    """下载进度模型"""
    bvid: str
    title: str
    page: int
    progress: float
    speed: Optional[float]
    eta: Optional[int]
    status: str
    message: Optional[str]
    updated_at: str
    
    class Config:
        from_attributes = True


class StatusResponse(BaseModel):
    """状态响应"""
    is_syncing: bool
    total_videos: int
    downloaded_videos: int = 0
    uploaded_videos: int
    pending_videos: int
    failed_videos: int = 0
    last_sync: Optional[dict]
    scheduler_enabled: bool
    next_run_time: Optional[str]
    cookie_configured: bool = False
    cookie_valid: bool
    s3_enabled: bool = False
    s3_connected: bool


class SyncStartRequest(BaseModel):
    """同步启动请求"""
    force: bool = False


class ApiResponse(BaseModel):
    """通用 API 响应"""
    success: bool
    message: str = ""
    data: Optional[Any] = None


class DashboardStats(BaseModel):
    """仪表盘统计"""
    total_videos: int
    uploaded_videos: int
    pending_videos: int
    total_size_mb: float
    recent_sync_count: int
    failed_downloads: int


class WatchLaterModel(BaseModel):
    """稍后观看模型"""
    bvid: str
    title: str
    cover: str = ""
    duration: int = 0
    owner_name: str = ""
    owner_mid: int = 0
    add_at: int = 0


class HistoryModel(BaseModel):
    """观看历史模型"""
    bvid: str
    title: str
    cover: str = ""
    duration: int = 0
    progress: int = 0
    owner_name: str = ""
    view_at: int = 0


class VideoCacheModel(BaseModel):
    """视频缓存模型"""
    id: Optional[int]
    bvid: str
    title: str
    cover: str = ""
    cover_local: Optional[str] = None
    duration: int = 0
    owner_name: str = ""
    source_type: str
    source_id: Optional[int] = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class FavoriteFolderDetailModel(BaseModel):
    """收藏夹详情模型（带选中状态）"""
    id: int
    title: str
    media_count: int = 0
    cover: str = ""
    cover_local: Optional[str] = None
    selected: bool = False
