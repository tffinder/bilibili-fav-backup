"""
通知模块
统一管理通知发送，支持网页、日志等多种渠道
"""
import asyncio
from datetime import datetime
from typing import Optional, List
from enum import Enum
from loguru import logger

from core.database import Database, Notification


class NotificationLevel(str, Enum):
    """通知级别"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


class NotificationService:
    """通知服务"""
    
    _instance: Optional['NotificationService'] = None
    db: Optional[Database] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self.db is None:
            asyncio.create_task(self._init_db())
    
    @classmethod
    def get_instance(cls) -> 'NotificationService':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def _init_db(self) -> None:
        """初始化数据库连接"""
        self.db = await Database.get_instance()
    
    async def send(
        self,
        level: NotificationLevel,
        title: str,
        message: str,
        web_enabled: bool = True,
        log_enabled: bool = True
    ) -> Optional[int]:
        """
        发送通知
        
        Args:
            level: 通知级别
            title: 标题
            message: 消息内容
            web_enabled: 是否发送到网页
            log_enabled: 是否记录到日志
            
        Returns:
            通知 ID（如果成功）
        """
        notification_id = None
        
        # 网页通知（存储到数据库）
        if web_enabled:
            try:
                if not self.db:
                    await self._init_db()
                
                notification = Notification(
                    id=None,
                    level=level.value,
                    title=title,
                    message=message,
                    created_at=datetime.now().isoformat(),
                    is_read=False
                )
                
                notification_id = await self.db.add_notification(notification)
                logger.debug(f"通知已保存到数据库：{title}")
                
            except Exception as e:
                logger.error(f"保存通知到数据库失败：{e}")
        
        # 日志通知
        if log_enabled:
            log_message = f"[{level.value.upper()}] {title}: {message}"
            
            if level == NotificationLevel.ERROR:
                logger.error(log_message)
            elif level == NotificationLevel.WARNING:
                logger.warning(log_message)
            elif level == NotificationLevel.SUCCESS:
                logger.info(log_message)
            else:
                logger.info(log_message)
        
        return notification_id
    
    async def send_info(
        self, title: str, message: str, **kwargs
    ) -> Optional[int]:
        """发送信息级通知"""
        return await self.send(
            NotificationLevel.INFO, title, message, **kwargs
        )
    
    async def send_warning(
        self, title: str, message: str, **kwargs
    ) -> Optional[int]:
        """发送警告级通知"""
        return await self.send(
            NotificationLevel.WARNING, title, message, **kwargs
        )
    
    async def send_error(
        self, title: str, message: str, **kwargs
    ) -> Optional[int]:
        """发送错误级通知"""
        return await self.send(
            NotificationLevel.ERROR, title, message, **kwargs
        )
    
    async def send_success(
        self, title: str, message: str, **kwargs
    ) -> Optional[int]:
        """发送成功级通知"""
        return await self.send(
            NotificationLevel.SUCCESS, title, message, **kwargs
        )
    
    # ==================== 预设通知场景 ====================
    
    async def notify_cookie_invalid(self, error: str) -> None:
        """Cookie 失效通知"""
        await self.send_warning(
            title="B 站 Cookie 失效",
            message=f"Cookie 校验失败，请更新配置：{error}",
        )
    
    async def notify_download_failed(
        self, bvid: str, title: str, error: str
    ) -> None:
        """下载失败通知"""
        await self.send_error(
            title="视频下载失败",
            message=f"{title} ({bvid}): {error}",
        )
    
    async def notify_upload_failed(
        self, bvid: str, title: str, error: str, retry_count: int
    ) -> None:
        """上传失败通知"""
        await self.send_error(
            title="视频上传失败",
            message=f"{title} ({bvid}), 已重试{retry_count}次：{error}",
        )
    
    async def notify_sync_completed(
        self,
        total: int,
        new: int,
        updated: int,
        skipped: int,
        failed: int
    ) -> None:
        """同步完成通知"""
        message = (
            f"同步完成 - 总计：{total}, "
            f"新增：{new}, 更新：{updated}, "
            f"已备份跳过：{skipped}, 失败：{failed}"
        )
        
        level = (
            NotificationLevel.SUCCESS if failed == 0
            else NotificationLevel.WARNING
        )
        
        await self.send(
            level=level,
            title="收藏夹同步完成",
            message=message,
        )
    
    async def notify_s3_connection_failed(self, error: str) -> None:
        """S3 连接失败通知"""
        await self.send_error(
            title="S3 连接失败",
            message=f"无法连接到 S3 存储：{error}",
        )
    
    async def notify_video_upgraded(
        self, bvid: str, title: str, old_quality: int, new_quality: int
    ) -> None:
        """视频清晰度升级通知"""
        await self.send_info(
            title="视频清晰度升级",
            message=f"{title} ({bvid}): {old_quality} → {new_quality}",
        )
    
    # ==================== 查询方法 ====================
    
    async def get_unread_notifications(self) -> List[Notification]:
        """获取未读通知"""
        if not self.db:
            await self._init_db()
        return await self.db.get_unread_notifications()
    
    async def get_all_notifications(
        self, limit: int = 50
    ) -> List[Notification]:
        """获取所有通知（最近 limit 条）"""
        if not self.db:
            await self._init_db()
        return await self.db.get_all_notifications(limit)
    
    async def mark_as_read(self, notification_id: int) -> None:
        """标记通知为已读"""
        if not self.db:
            await self._init_db()
        await self.db.mark_notification_read(notification_id)
    
    async def mark_all_as_read(self) -> None:
        """标记所有通知为已读"""
        if not self.db:
            await self._init_db()
        await self.db.mark_all_notifications_read()
    
    async def clear_old_notifications(self, days: int = 7) -> None:
        """清理旧通知（保留最近 days 天）"""
        # 这个方法需要在数据库中实现对应的 SQL
        # 暂时不实现
        pass


# 全局通知服务实例
def get_notification_service() -> NotificationService:
    """获取通知服务实例"""
    return NotificationService.get_instance()
