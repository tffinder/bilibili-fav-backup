"""
统一异常定义模块

定义项目专用异常类，提供清晰的错误层次结构
"""
from typing import Optional, Any, Dict


class BilibiliBackupError(Exception):
    """项目基础异常类"""

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code or "UNKNOWN_ERROR"
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "error": self.code,
            "message": self.message,
            "details": self.details
        }

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# ==================== 配置相关异常 ====================

class ConfigError(BilibiliBackupError):
    """配置错误基类"""
    pass


class ConfigNotFoundError(ConfigError):
    """配置文件不存在"""

    def __init__(self, path: str):
        super().__init__(
            message=f"配置文件不存在: {path}",
            code="CONFIG_NOT_FOUND",
            details={"path": path}
        )


class ConfigValidationError(ConfigError):
    """配置验证失败"""

    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(
            message=message,
            code="CONFIG_VALIDATION_ERROR",
            details={"field": field} if field else {}
        )


# ==================== 数据库相关异常 ====================

class DatabaseError(BilibiliBackupError):
    """数据库错误基类"""
    pass


class DatabaseConnectionError(DatabaseError):
    """数据库连接失败"""

    def __init__(self, reason: str):
        super().__init__(
            message=f"数据库连接失败: {reason}",
            code="DB_CONNECTION_ERROR",
            details={"reason": reason}
        )


class DatabaseOperationError(DatabaseError):
    """数据库操作失败"""

    def __init__(self, operation: str, reason: str):
        super().__init__(
            message=f"数据库操作失败 ({operation}): {reason}",
            code="DB_OPERATION_ERROR",
            details={"operation": operation, "reason": reason}
        )


class RecordNotFoundError(DatabaseError):
    """记录不存在"""

    def __init__(self, table: str, condition: str):
        super().__init__(
            message=f"记录不存在: {table} WHERE {condition}",
            code="RECORD_NOT_FOUND",
            details={"table": table, "condition": condition}
        )


# ==================== Bilibili API 相关异常 ====================

class BilibiliError(BilibiliBackupError):
    """Bilibili API 错误基类"""
    pass


class BilibiliAuthError(BilibiliError):
    """Bilibili 认证错误"""

    def __init__(self, message: str = "Bilibili 认证失败"):
        super().__init__(
            message=message,
            code="BILI_AUTH_ERROR"
        )


class CookieInvalidError(BilibiliAuthError):
    """Cookie 无效"""

    def __init__(self, reason: str = ""):
        super().__init__(
            message=f"Cookie 无效或已过期: {reason}" if reason else "Cookie 无效或已过期"
        )
        self.code = "COOKIE_INVALID"


class QRCodeExpiredError(BilibiliAuthError):
    """二维码已过期"""

    def __init__(self):
        super().__init__(message="二维码已过期，请重新获取")
        self.code = "QRCODE_EXPIRED"


class GeetestRequiredError(BilibiliAuthError):
    """需要极验验证"""

    def __init__(self, session_id: str = ""):
        super().__init__(message="需要完成极验验证")
        self.code = "GEETEST_REQUIRED"
        self.details = {"session_id": session_id}


class BilibiliAPIError(BilibiliError):
    """Bilibili API 调用错误"""

    def __init__(self, api: str, reason: str, status_code: Optional[int] = None):
        super().__init__(
            message=f"API 调用失败 ({api}): {reason}",
            code="BILI_API_ERROR",
            details={"api": api, "reason": reason, "status_code": status_code}
        )


class FavoriteNotFoundError(BilibiliError):
    """收藏夹不存在"""

    def __init__(self, fav_id: str):
        super().__init__(
            message=f"收藏夹不存在: {fav_id}",
            code="FAVORITE_NOT_FOUND",
            details={"fav_id": fav_id}
        )


class VideoNotFoundError(BilibiliError):
    """视频不存在"""

    def __init__(self, bvid: str):
        super().__init__(
            message=f"视频不存在: {bvid}",
            code="VIDEO_NOT_FOUND",
            details={"bvid": bvid}
        )


class VideoNotAvailableError(BilibiliError):
    """视频不可用（已删除、审核中等）"""

    def __init__(self, bvid: str, reason: str = ""):
        super().__init__(
            message=f"视频不可用: {bvid}" + (f" ({reason})" if reason else ""),
            code="VIDEO_NOT_AVAILABLE",
            details={"bvid": bvid, "reason": reason}
        )


# ==================== 下载相关异常 ====================

class DownloadError(BilibiliBackupError):
    """下载错误基类"""
    pass


class DownloadFailedError(DownloadError):
    """下载失败"""

    def __init__(self, bvid: str, reason: str, page: int = 1):
        super().__init__(
            message=f"视频下载失败: {bvid} P{page} - {reason}",
            code="DOWNLOAD_FAILED",
            details={"bvid": bvid, "page": page, "reason": reason}
        )


class DownloadTimeoutError(DownloadError):
    """下载超时"""

    def __init__(self, bvid: str, timeout: int):
        super().__init__(
            message=f"下载超时: {bvid} ({timeout}秒)",
            code="DOWNLOAD_TIMEOUT",
            details={"bvid": bvid, "timeout": timeout}
        )


class NoQualityAvailableError(DownloadError):
    """无可用清晰度"""

    def __init__(self, bvid: str):
        super().__init__(
            message=f"无可用清晰度: {bvid}",
            code="NO_QUALITY_AVAILABLE",
            details={"bvid": bvid}
        )


class FFmpegNotFoundError(DownloadError):
    """FFmpeg 未找到"""

    def __init__(self):
        super().__init__(
            message="未找到 FFmpeg，无法合并音视频",
            code="FFMPEG_NOT_FOUND"
        )


# ==================== 上传相关异常 ====================

class UploadError(BilibiliBackupError):
    """上传错误基类"""
    pass


class S3ConnectionError(UploadError):
    """S3 连接错误"""

    def __init__(self, reason: str):
        super().__init__(
            message=f"S3 连接失败: {reason}",
            code="S3_CONNECTION_ERROR",
            details={"reason": reason}
        )


class S3UploadError(UploadError):
    """S3 上传错误"""

    def __init__(self, key: str, reason: str):
        super().__init__(
            message=f"S3 上传失败: {key} - {reason}",
            code="S3_UPLOAD_ERROR",
            details={"key": key, "reason": reason}
        )


class S3ConfigError(UploadError):
    """S3 配置错误"""

    def __init__(self, missing_fields: list):
        super().__init__(
            message=f"S3 配置不完整: 缺少 {', '.join(missing_fields)}",
            code="S3_CONFIG_ERROR",
            details={"missing_fields": missing_fields}
        )


class UploadTimeoutError(UploadError):
    """上传超时"""

    def __init__(self, key: str, timeout: int):
        super().__init__(
            message=f"上传超时: {key} ({timeout}秒)",
            code="UPLOAD_TIMEOUT",
            details={"key": key, "timeout": timeout}
        )


# ==================== 同步相关异常 ====================

class SyncError(BilibiliBackupError):
    """同步错误基类"""
    pass


class SyncInProgressError(SyncError):
    """同步正在进行中"""

    def __init__(self):
        super().__init__(
            message="同步任务正在进行中，请稍后再试",
            code="SYNC_IN_PROGRESS"
        )


class SyncConfigError(SyncError):
    """同步配置错误"""

    def __init__(self, reason: str):
        super().__init__(
            message=f"同步配置错误: {reason}",
            code="SYNC_CONFIG_ERROR",
            details={"reason": reason}
        )


# ==================== 任务调度相关异常 ====================

class SchedulerError(BilibiliBackupError):
    """调度器错误基类"""
    pass


class SchedulerConfigError(SchedulerError):
    """调度器配置错误"""

    def __init__(self, cron: str, reason: str):
        super().__init__(
            message=f"Cron 表达式无效: {cron} - {reason}",
            code="SCHEDULER_CONFIG_ERROR",
            details={"cron": cron, "reason": reason}
        )


# ==================== 文件操作相关异常 ====================

class FileOperationError(BilibiliBackupError):
    """文件操作错误基类"""
    pass


class FileNotFoundError(FileOperationError):
    """文件不存在"""

    def __init__(self, path: str):
        super().__init__(
            message=f"文件不存在: {path}",
            code="FILE_NOT_FOUND",
            details={"path": path}
        )


class FileWriteError(FileOperationError):
    """文件写入失败"""

    def __init__(self, path: str, reason: str):
        super().__init__(
            message=f"文件写入失败: {path} - {reason}",
            code="FILE_WRITE_ERROR",
            details={"path": path, "reason": reason}
        )


class DiskSpaceError(FileOperationError):
    """磁盘空间不足"""

    def __init__(self, required: int, available: int):
        super().__init__(
            message=f"磁盘空间不足: 需要 {required}MB, 可用 {available}MB",
            code="DISK_SPACE_ERROR",
            details={"required_mb": required, "available_mb": available}
        )
