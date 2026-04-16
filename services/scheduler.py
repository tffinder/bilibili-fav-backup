"""
定时任务模块
使用 APScheduler 实现定时同步任务
"""
import asyncio
from datetime import datetime
from typing import Optional, Callable
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError

from core.config import get_config
from services.sync_manager import SyncManager


class TaskScheduler:
    """定时任务调度器"""
    
    _instance: Optional['TaskScheduler'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self.config = get_config()
        self.scheduler: Optional[AsyncIOScheduler] = None
        self.sync_manager: Optional[SyncManager] = None
        self.is_running = False
    
    @classmethod
    def get_instance(cls) -> 'TaskScheduler':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def init(self, sync_manager: SyncManager) -> None:
        """
        初始化调度器
        
        Args:
            sync_manager: 同步管理器实例
        """
        self.sync_manager = sync_manager
        
        # 创建调度器
        self.scheduler = AsyncIOScheduler(
            timezone=self.config.scheduler.timezone
        )
        
        # 添加定时任务
        if self.config.scheduler.enabled:
            self._add_sync_job()
    
    def _add_sync_job(self) -> None:
        """添加同步任务"""
        cron_expr = self.config.scheduler.cron
        
        # 解析 cron 表达式 (5 段式：分 时 日 月 星期)
        try:
            minute, hour, day, month, day_of_week = cron_expr.split()
            
            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
                timezone=self.config.scheduler.timezone
            )
            
            self.scheduler.add_job(
                self._run_sync,
                trigger=trigger,
                id="daily_sync",
                name="每日收藏夹同步",
                replace_existing=True
            )
            
            logger.info(
                f"已添加定时同步任务：Cron={cron_expr}, "
                f"时区={self.config.scheduler.timezone}"
            )
            
        except Exception as e:
            logger.error(f"解析 Cron 表达式失败：{e}")
            # 使用默认配置（每天凌晨 2 点）
            self.scheduler.add_job(
                self._run_sync,
                trigger='cron',
                hour=2,
                minute=0,
                id="daily_sync",
                name="每日收藏夹同步",
                replace_existing=True
            )
    
    async def _run_sync(self) -> None:
        """执行同步任务（内部方法）"""
        if not self.sync_manager:
            logger.error("同步管理器未初始化")
            return
        
        logger.info("定时同步任务触发")
        
        try:
            success = await self.sync_manager.start_sync(manual=False)
            
            if success:
                logger.info("定时同步任务执行成功")
            else:
                logger.warning("定时同步任务执行失败")
                
        except Exception as e:
            logger.error(f"定时同步任务异常：{e}")
    
    def start(self) -> None:
        """启动调度器"""
        if not self.scheduler:
            raise RuntimeError("调度器未初始化，请先调用 init()")
        
        if self.is_running:
            logger.warning("调度器已在运行中")
            return
        
        self.scheduler.start()
        self.is_running = True
        
        logger.info("定时任务调度器已启动")
        
        # 打印下一个执行时间
        job = self.scheduler.get_job("daily_sync")
        if job:
            next_run = job.next_run_time
            if next_run:
                logger.info(f"下次执行时间：{next_run}")
    
    def stop(self) -> None:
        """停止调度器"""
        if self.scheduler and self.is_running:
            self.scheduler.shutdown(wait=False)
            self.is_running = False
            logger.info("定时任务调度器已停止")
    
    def pause_job(self) -> None:
        """暂停同步任务"""
        if self.scheduler:
            self.scheduler.pause_job("daily_sync")
            logger.info("同步任务已暂停")
    
    def resume_job(self) -> None:
        """恢复同步任务"""
        if self.scheduler:
            self.scheduler.resume_job("daily_sync")
            logger.info("同步任务已恢复")
    
    def get_next_run_time(self) -> Optional[datetime]:
        """获取下次执行时间"""
        if not self.scheduler:
            return None
        
        job = self.scheduler.get_job("daily_sync")
        if job:
            return job.next_run_time
        
        return None
    
    def get_job_info(self) -> dict:
        """获取任务信息"""
        if not self.scheduler:
            return {}
        
        job = self.scheduler.get_job("daily_sync")
        if not job:
            return {}
        
        return {
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "enabled": self.config.scheduler.enabled
        }

    def reload_from_config(self) -> None:
        """根据最新配置重载调度任务"""
        self.config = get_config()

        if not self.scheduler:
            return

        try:
            self.scheduler.remove_job("daily_sync")
        except JobLookupError:
            pass

        if self.config.scheduler.enabled:
            self._add_sync_job()


def create_scheduler(sync_manager: SyncManager) -> TaskScheduler:
    """
    创建并初始化调度器
    
    Args:
        sync_manager: 同步管理器实例
        
    Returns:
        TaskScheduler 实例
    """
    scheduler = TaskScheduler.get_instance()
    scheduler.init(sync_manager)
    return scheduler
