"""
配置管理 API

提供配置查询、更新、测试等接口
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from loguru import logger

from core.config import get_config, ConfigManager
from services.s3_uploader import S3Uploader
from api.models import ConfigModel, ConfigUpdateRequest, ApiResponse

router = APIRouter(tags=["配置管理"])


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
            debug_ffmpeg_path=config.debug.ffmpeg_path,
            skip_max_single_duration=config.skip_rules.max_single_duration,
            skip_max_total_duration=config.skip_rules.max_total_duration,
            skip_interactive=config.skip_rules.skip_interactive,
            skip_max_video_size_gib=config.skip_rules.max_video_size_gib,
            up_sync_enabled=config.up_sync.enabled,
            up_sync_cron=config.up_sync.cron
        )

    except Exception as e:
        logger.error(f"获取配置失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# 配置字段映射
CONFIG_FIELD_MAPPING = {
    'bilibili_cookie': ('bilibili', 'cookie'),
    'bilibili_fav_id': ('bilibili', 'fav_id'),
    'bilibili_check_cookie_interval': ('bilibili', 'check_cookie_interval'),
    'download_quality': ('download', 'quality'),
    'download_max_parallel': ('download', 'max_parallel'),
    'download_temp_dir': ('download', 'temp_dir'),
    'download_retry_times': ('download', 'retry_times'),
    'download_request_delay': ('download', 'request_delay'),
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
    'scheduler_enabled': ('scheduler', 'enabled'),
    'scheduler_cron': ('scheduler', 'cron'),
    'scheduler_timezone': ('scheduler', 'timezone'),
    'notification_web_enabled': ('notification', 'web_enabled'),
    'notification_log_enabled': ('notification', 'log_enabled'),
    'notification_email_enabled': ('notification', 'email_enabled'),
    'notification_email_smtp_server': ('notification', 'email_smtp_server'),
    'notification_email_from': ('notification', 'email_from'),
    'notification_email_to': ('notification', 'email_to'),
    'notification_email_password': ('notification', 'email_password'),
    'debug_enabled': ('debug', 'enabled'),
    'debug_log_level': ('debug', 'log_level'),
    'debug_keep_temp_files': ('debug', 'keep_temp_files'),
    'debug_biliup_proxy': ('debug', 'biliup_proxy'),
    'debug_ffmpeg_path': ('debug', 'ffmpeg_path'),
    'skip_max_single_duration': ('skip_rules', 'max_single_duration'),
    'skip_max_total_duration': ('skip_rules', 'max_total_duration'),
    'skip_interactive': ('skip_rules', 'skip_interactive'),
    'skip_max_video_size_gib': ('skip_rules', 'max_video_size_gib'),
    'up_sync_enabled': ('up_sync', 'enabled'),
    'up_sync_cron': ('up_sync', 'cron'),
}


@router.put("/config", response_model=ApiResponse)
async def update_config(request: ConfigUpdateRequest):
    """更新配置"""
    try:
        from api.routes.sync import _scheduler

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
    """测试 S3 上传速度"""
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
    """获取日志"""
    try:
        from datetime import datetime
        from pathlib import Path

        log_dir = Path(__file__).parent.parent.parent / "logs"
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

        with open(log_file, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            last_lines = all_lines[-lines:]

        filtered_lines = [
            line for line in last_lines
            if level.upper() in line or not level
        ]

        return {"logs": filtered_lines}

    except Exception as e:
        logger.error(f"获取日志失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))
