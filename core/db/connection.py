"""
数据库连接管理

提供 SQLite 数据库连接、表初始化和基础操作
"""
import asyncio
import aiosqlite
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any

from loguru import logger

from core.db.models import (
    Video, SyncHistory, Notification, DownloadProgress,
    FavoriteFolder, VideoCache
)


def require_db(func: Callable) -> Callable:
    """
    装饰器：确保数据库已连接

    用法：
        @require_db
        async def some_method(self, ...):
            # self._db 已确保可用
            ...
    """
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not self._db:
            raise RuntimeError("数据库未连接")
        return await func(self, *args, **kwargs)
    return wrapper


class Database:
    """数据库管理类"""

    _instance: Optional['Database'] = None
    _db: Optional[aiosqlite.Connection] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._db is None:
            self.db_path = Path(__file__).parent.parent.parent / "data.db"

    @classmethod
    async def get_instance(cls) -> 'Database':
        """获取单例实例"""
        if cls._instance is None:
            instance = cls()
            await instance.connect()
            cls._instance = instance
        return cls._instance

    async def connect(self) -> None:
        """连接数据库"""
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._init_tables()

    async def close(self) -> None:
        """关闭数据库"""
        if self._db:
            await self._db.close()
            self._db = None

    @require_db
    async def _init_tables(self) -> None:
        """初始化数据表"""
        # 视频表
        # 注意：UNIQUE(bvid, cid, page, quality) 允许同一视频的不同清晰度版本共存
        await self._db.execute('''
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid TEXT NOT NULL,
                title TEXT NOT NULL,
                cid TEXT NOT NULL,
                page INTEGER NOT NULL,
                total_pages INTEGER NOT NULL,
                quality INTEGER NOT NULL,
                duration INTEGER NOT NULL,
                pubdate INTEGER NOT NULL,
                owner_name TEXT NOT NULL,
                s3_key TEXT,
                s3_uploaded INTEGER DEFAULT 0,
                s3_quality INTEGER,
                local_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                max_quality INTEGER,
                upload_failed INTEGER DEFAULT 0,
                source_available INTEGER DEFAULT 1,
                source_status TEXT DEFAULT 'unknown',
                source_checked_at TEXT,
                source_deleted_at TEXT,
                source_error TEXT,
                UNIQUE(bvid, cid, page, quality)
            )
        ''')

        # 添加新列（如果不存在）
        await self._safe_add_column('videos', 'max_quality', 'INTEGER')
        await self._safe_add_column('videos', 'upload_failed', 'INTEGER DEFAULT 0')
        await self._safe_add_column('videos', 'fav_id', 'INTEGER')
        await self._safe_add_column('videos', 'fav_title', 'TEXT')
        await self._safe_add_column('videos', 'fav_time', 'INTEGER')
        await self._safe_add_column('videos', 'up_mid', 'INTEGER')
        await self._safe_add_column('videos', 'up_name', 'TEXT')
        await self._safe_add_column('videos', 'source_available', 'INTEGER DEFAULT 1')
        await self._safe_add_column('videos', 'source_status', "TEXT DEFAULT 'unknown'")
        await self._safe_add_column('videos', 'source_checked_at', 'TEXT')
        await self._safe_add_column('videos', 'source_deleted_at', 'TEXT')
        await self._safe_add_column('videos', 'source_error', 'TEXT')

        # 同步历史表
        await self._db.execute('''
            CREATE TABLE IF NOT EXISTS sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fav_id TEXT NOT NULL,
                total_videos INTEGER NOT NULL,
                new_videos INTEGER NOT NULL,
                updated_videos INTEGER NOT NULL,
                skipped_videos INTEGER NOT NULL,
                failed_videos INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                status TEXT NOT NULL,
                error_message TEXT
            )
        ''')

        # 通知表
        await self._db.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_read INTEGER DEFAULT 0
            )
        ''')

        # 下载进度表
        await self._db.execute('''
            CREATE TABLE IF NOT EXISTS download_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid TEXT NOT NULL,
                title TEXT NOT NULL,
                page INTEGER NOT NULL,
                progress REAL DEFAULT 0,
                speed REAL,
                eta INTEGER,
                status TEXT NOT NULL,
                message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(bvid, page)
            )
        ''')

        # 收藏夹元数据表
        await self._db.execute('''
            CREATE TABLE IF NOT EXISTS favorite_folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fav_id INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                media_count INTEGER DEFAULT 0,
                cover TEXT,
                cover_local TEXT,
                selected INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')

        # 视频元数据缓存表
        await self._db.execute('''
            CREATE TABLE IF NOT EXISTS video_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid TEXT NOT NULL,
                title TEXT NOT NULL,
                cover TEXT,
                cover_local TEXT,
                duration INTEGER DEFAULT 0,
                owner_name TEXT,
                source_type TEXT NOT NULL,
                source_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(bvid, source_type, source_id)
            )
        ''')

        # 任务队列表（同步流程产生的视频任务，按 BV 粒度）
        await self._db.execute('''
            CREATE TABLE IF NOT EXISTS task_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid TEXT NOT NULL,
                title TEXT NOT NULL,
                fav_id INTEGER,
                fav_title TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                current_page INTEGER DEFAULT 0,
                total_pages INTEGER DEFAULT 0,
                progress REAL DEFAULT 0,
                current_action TEXT,
                error_message TEXT,
                source TEXT DEFAULT 'sync',
                enqueued_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
        ''')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_task_queue_status ON task_queue(status)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_task_queue_bvid ON task_queue(bvid)')

        # 视频黑名单（永久跳过）
        await self._db.execute('''
            CREATE TABLE IF NOT EXISTS task_blacklist (
                bvid TEXT PRIMARY KEY,
                title TEXT,
                reason TEXT,
                added_at TEXT NOT NULL
            )
        ''')

        # 创建索引
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_videos_bvid ON videos(bvid)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_videos_s3_uploaded ON videos(s3_uploaded)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_videos_source_status ON videos(source_status)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON notifications(is_read)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_favorite_folders_selected ON favorite_folders(selected)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_video_cache_bvid ON video_cache(bvid)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_video_cache_source ON video_cache(source_type, source_id)')

        await self._db.commit()

    async def _safe_add_column(self, table: str, column: str, definition: str) -> None:
        """安全添加列（如果不存在）"""
        try:
            await self._db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        except aiosqlite.OperationalError as e:
            # 列已存在时会抛出 duplicate column name 错误，这是预期行为
            logger.debug(f"添加列 {table}.{column} 失败（可能已存在）: {e}")

    # ==================== Video 操作方法 ====================

    @require_db
    async def add_video(self, video: Video) -> Optional[int]:
        """添加视频"""
        cursor = await self._db.execute('''
            INSERT OR REPLACE INTO videos
            (bvid, title, cid, page, total_pages, quality, duration, pubdate,
             owner_name, s3_key, s3_uploaded, s3_quality, local_path, created_at, updated_at,
             max_quality, upload_failed, fav_id, fav_title, fav_time, up_mid, up_name,
             source_available, source_status, source_checked_at, source_deleted_at, source_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            video.bvid, video.title, video.cid, video.page, video.total_pages,
            video.quality, video.duration, video.pubdate, video.owner_name,
            video.s3_key, 1 if video.s3_uploaded else 0, video.s3_quality,
            video.local_path, video.created_at, video.updated_at,
            video.max_quality, 1 if video.upload_failed else 0,
            video.fav_id, video.fav_title, video.fav_time,
            video.up_mid, video.up_name,
            1 if video.source_available else 0, video.source_status,
            video.source_checked_at, video.source_deleted_at, video.source_error
        ))
        await self._db.commit()
        return cursor.lastrowid

    @require_db
    async def get_video_by_bvid(self, bvid: str) -> Optional[Video]:
        """根据 BVID 获取视频（返回最高清晰度版本）"""
        cursor = await self._db.execute(
            'SELECT * FROM videos WHERE bvid = ? ORDER BY quality DESC LIMIT 1', (bvid,)
        )
        row = await cursor.fetchone()
        if row:
            return Video(**dict(row))
        return None

    @require_db
    async def get_video_by_bvid_quality(self, bvid: str, quality: int) -> Optional[Video]:
        """根据 BVID 和清晰度获取视频"""
        cursor = await self._db.execute(
            'SELECT * FROM videos WHERE bvid = ? AND quality = ?', (bvid, quality)
        )
        row = await cursor.fetchone()
        if row:
            return Video(**dict(row))
        return None

    @require_db
    async def get_all_videos_by_bvid(self, bvid: str) -> List[Video]:
        """根据 BVID 获取所有清晰度版本"""
        cursor = await self._db.execute(
            'SELECT * FROM videos WHERE bvid = ? ORDER BY quality DESC', (bvid,)
        )
        rows = await cursor.fetchall()
        return [Video(**dict(row)) for row in rows]

    @require_db
    async def get_best_uploaded_quality(self, bvid: str) -> Optional[int]:
        """获取已上传的最高清晰度"""
        cursor = await self._db.execute(
            'SELECT MAX(quality) FROM videos WHERE bvid = ? AND s3_uploaded = 1', (bvid,)
        )
        row = await cursor.fetchone()
        if row and row[0]:
            return row[0]
        return None

    @require_db
    async def get_video_by_id(self, video_id: int) -> Optional[Video]:
        """根据 ID 获取视频"""
        cursor = await self._db.execute(
            'SELECT * FROM videos WHERE id = ?', (video_id,)
        )
        row = await cursor.fetchone()
        if row:
            return Video(**dict(row))
        return None

    @require_db
    async def get_all_videos(self) -> List[Video]:
        """获取所有视频"""
        cursor = await self._db.execute('SELECT * FROM videos ORDER BY created_at DESC')
        rows = await cursor.fetchall()
        return [Video(**dict(row)) for row in rows]

    @require_db
    async def get_deleted_videos(self) -> List[Video]:
        """获取已确认从 B 站失效的视频。"""
        cursor = await self._db.execute('''
            SELECT * FROM videos
            WHERE source_status = 'deleted' OR source_available = 0
            ORDER BY COALESCE(source_deleted_at, source_checked_at, updated_at) DESC
        ''')
        rows = await cursor.fetchall()
        return [Video(**dict(row)) for row in rows]

    @require_db
    async def update_video_source_status(
        self,
        bvid: str,
        source_available: bool,
        source_status: str,
        source_error: Optional[str] = None
    ) -> None:
        """更新某个 BVID 所有版本的 B 站源状态。"""
        now = datetime.now().isoformat()
        deleted_at_expr = (
            "COALESCE(source_deleted_at, ?)"
            if source_status == "deleted"
            else "source_deleted_at"
        )
        params: List[Any] = [
            1 if source_available else 0,
            source_status,
            now,
            source_error,
        ]
        if source_status == "deleted":
            params.append(now)
        params.append(bvid)

        await self._db.execute(f'''
            UPDATE videos
            SET source_available = ?,
                source_status = ?,
                source_checked_at = ?,
                source_error = ?,
                source_deleted_at = {deleted_at_expr},
                updated_at = ?
            WHERE bvid = ?
        ''', (*params[:-1], now, params[-1]))
        await self._db.commit()

    @require_db
    async def get_progress_by_bvid_page(self, bvid: str, page: int) -> Optional[DownloadProgress]:
        """根据 BVID 和分 P 获取下载进度"""
        cursor = await self._db.execute(
            'SELECT * FROM download_progress WHERE bvid = ? AND page = ?',
            (bvid, page)
        )
        row = await cursor.fetchone()
        if row:
            return DownloadProgress(**dict(row))
        return None

    @require_db
    async def get_progress_map(
        self, keys: List[tuple]
    ) -> Dict[tuple, DownloadProgress]:
        """批量获取下载进度 (key: (bvid, page))"""
        if not keys:
            return {}

        conditions = " OR ".join(["(bvid = ? AND page = ?)"] * len(keys))
        params: List[Any] = []
        for bvid, page in keys:
            params.append(bvid)
            params.append(page)

        cursor = await self._db.execute(
            f"SELECT * FROM download_progress WHERE {conditions}",
            params
        )
        rows = await cursor.fetchall()
        return {(row["bvid"], row["page"]): DownloadProgress(**dict(row)) for row in rows}

    @require_db
    async def update_video_s3_status(
        self, bvid: str, page: int, s3_key: str, quality: int
    ) -> None:
        """更新视频 S3 上传状态（根据 bvid、page 和 quality 定位记录）"""
        await self._db.execute('''
            UPDATE videos
            SET s3_key = ?, s3_uploaded = 1, s3_quality = ?, upload_failed = 0, updated_at = ?
            WHERE bvid = ? AND page = ? AND quality = ?
        ''', (s3_key, quality, datetime.now().isoformat(), bvid, page, quality))
        await self._db.commit()

    @require_db
    async def clear_video_local_path(self, bvid: str, page: int) -> None:
        """删除本地文件后清掉数据库里的 local_path 引用，避免后续 /file 接口返回 404。"""
        await self._db.execute('''
            UPDATE videos SET local_path = NULL, updated_at = ?
            WHERE bvid = ? AND page = ?
        ''', (datetime.now().isoformat(), bvid, page))
        await self._db.commit()

    @require_db
    async def set_upload_failed(self, bvid: str, page: int, failed: bool = True, quality: Optional[int] = None) -> None:
        """设置上传失败状态"""
        if quality is not None:
            # 根据清晰度定位记录
            await self._db.execute('''
                UPDATE videos SET upload_failed = ?, updated_at = ?
                WHERE bvid = ? AND page = ? AND quality = ?
            ''', (1 if failed else 0, datetime.now().isoformat(), bvid, page, quality))
        else:
            # 兼容旧逻辑：更新所有匹配的记录
            await self._db.execute('''
                UPDATE videos SET upload_failed = ?, updated_at = ?
                WHERE bvid = ? AND page = ?
            ''', (1 if failed else 0, datetime.now().isoformat(), bvid, page))
        await self._db.commit()

    @require_db
    async def update_max_quality(self, bvid: str, page: int, max_quality: int) -> None:
        """更新视频最高可用分辨率"""
        await self._db.execute('''
            UPDATE videos SET max_quality = ?, updated_at = ?
            WHERE bvid = ? AND page = ?
        ''', (max_quality, datetime.now().isoformat(), bvid, page))
        await self._db.commit()

    @require_db
    async def get_pending_videos(self) -> List[Video]:
        """获取待上传的视频"""
        cursor = await self._db.execute(
            'SELECT * FROM videos WHERE s3_uploaded = 0 ORDER BY created_at'
        )
        rows = await cursor.fetchall()
        return [Video(**dict(row)) for row in rows]

    # ==================== SyncHistory 操作方法 ====================

    @require_db
    async def add_sync_history(self, history: SyncHistory) -> Optional[int]:
        """添加同步记录"""
        cursor = await self._db.execute('''
            INSERT INTO sync_history
            (fav_id, total_videos, new_videos, updated_videos, skipped_videos,
             failed_videos, start_time, end_time, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            history.fav_id, history.total_videos, history.new_videos,
            history.updated_videos, history.skipped_videos, history.failed_videos,
            history.start_time, history.end_time, history.status, history.error_message
        ))
        await self._db.commit()
        return cursor.lastrowid

    @require_db
    async def update_sync_history(self, history: SyncHistory) -> None:
        """更新同步记录"""
        if history.id is None:
            raise ValueError("history.id 不能为空")

        await self._db.execute('''
            UPDATE sync_history
            SET total_videos = ?, new_videos = ?, updated_videos = ?, skipped_videos = ?,
                failed_videos = ?, end_time = ?, status = ?, error_message = ?
            WHERE id = ?
        ''', (
            history.total_videos, history.new_videos, history.updated_videos,
            history.skipped_videos, history.failed_videos, history.end_time,
            history.status, history.error_message, history.id
        ))
        await self._db.commit()

    @require_db
    async def get_recent_sync_history(self, limit: int = 10) -> List[SyncHistory]:
        """获取最近的同步记录"""
        cursor = await self._db.execute(
            'SELECT * FROM sync_history ORDER BY start_time DESC LIMIT ?', (limit,)
        )
        rows = await cursor.fetchall()
        return [SyncHistory(**dict(row)) for row in rows]

    # ==================== Notification 操作方法 ====================

    @require_db
    async def add_notification(self, notification: Notification) -> Optional[int]:
        """添加通知"""
        cursor = await self._db.execute('''
            INSERT INTO notifications (level, title, message, created_at, is_read)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            notification.level, notification.title, notification.message,
            notification.created_at, 1 if notification.is_read else 0
        ))
        await self._db.commit()
        return cursor.lastrowid

    @require_db
    async def get_unread_notifications(self) -> List[Notification]:
        """获取未读通知"""
        cursor = await self._db.execute(
            'SELECT * FROM notifications WHERE is_read = 0 ORDER BY created_at DESC'
        )
        rows = await cursor.fetchall()
        return [Notification(**dict(row)) for row in rows]

    @require_db
    async def get_all_notifications(self, limit: int = 50) -> List[Notification]:
        """获取所有通知（最近 50 条）"""
        cursor = await self._db.execute(
            'SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?', (limit,)
        )
        rows = await cursor.fetchall()
        return [Notification(**dict(row)) for row in rows]

    @require_db
    async def mark_notification_read(self, notification_id: int) -> None:
        """标记通知为已读"""
        await self._db.execute(
            'UPDATE notifications SET is_read = 1 WHERE id = ?', (notification_id,)
        )
        await self._db.commit()

    @require_db
    async def mark_all_notifications_read(self) -> None:
        """标记所有通知为已读"""
        await self._db.execute('UPDATE notifications SET is_read = 1')
        await self._db.commit()

    # ==================== DownloadProgress 操作方法 ====================

    @require_db
    async def update_download_progress(self, progress: DownloadProgress) -> None:
        """更新下载进度"""
        await self._db.execute('''
            INSERT OR REPLACE INTO download_progress
            (bvid, title, page, progress, speed, eta, status, message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            progress.bvid, progress.title, progress.page, progress.progress,
            progress.speed, progress.eta, progress.status, progress.message,
            progress.created_at, progress.updated_at
        ))
        await self._db.commit()

    @require_db
    async def get_active_downloads(self) -> List[DownloadProgress]:
        """获取进行中的下载"""
        cursor = await self._db.execute('''
            SELECT * FROM download_progress
            WHERE status IN ('downloading', 'uploading', 'pending')
            ORDER BY updated_at DESC
        ''')
        rows = await cursor.fetchall()
        return [DownloadProgress(**dict(row)) for row in rows]

    @require_db
    async def clear_completed_progress(self) -> None:
        """清除已完成的进度记录"""
        await self._db.execute('''
            DELETE FROM download_progress
            WHERE status IN ('completed', 'failed')
        ''')
        await self._db.commit()

    @require_db
    async def get_all_progress(self) -> List[DownloadProgress]:
        """获取所有进度记录"""
        cursor = await self._db.execute(
            'SELECT * FROM download_progress ORDER BY updated_at DESC'
        )
        rows = await cursor.fetchall()
        return [DownloadProgress(**dict(row)) for row in rows]

    # ==================== FavoriteFolder 操作方法 ====================

    @require_db
    async def add_or_update_favorite_folder(self, folder: FavoriteFolder) -> Optional[int]:
        """添加或更新收藏夹"""
        cursor = await self._db.execute('''
            INSERT INTO favorite_folders (fav_id, title, media_count, cover, cover_local, selected, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fav_id) DO UPDATE SET
                title = excluded.title,
                media_count = excluded.media_count,
                cover = excluded.cover,
                cover_local = COALESCE(excluded.cover_local, cover_local),
                selected = excluded.selected,
                updated_at = excluded.updated_at
        ''', (
            folder.fav_id, folder.title, folder.media_count, folder.cover,
            folder.cover_local, 1 if folder.selected else 0,
            folder.created_at, folder.updated_at
        ))
        await self._db.commit()
        return cursor.lastrowid

    @require_db
    async def get_all_favorite_folders(self) -> List[FavoriteFolder]:
        """获取所有收藏夹"""
        cursor = await self._db.execute(
            'SELECT * FROM favorite_folders ORDER BY title'
        )
        rows = await cursor.fetchall()
        return [FavoriteFolder(**dict(row)) for row in rows]

    @require_db
    async def get_selected_favorite_folders(self) -> List[FavoriteFolder]:
        """获取选中的收藏夹（用于定时下载）"""
        cursor = await self._db.execute(
            'SELECT * FROM favorite_folders WHERE selected = 1 ORDER BY title'
        )
        rows = await cursor.fetchall()
        return [FavoriteFolder(**dict(row)) for row in rows]

    @require_db
    async def get_favorite_folder(self, fav_id: int) -> Optional[FavoriteFolder]:
        """根据收藏夹 ID 获取单个收藏夹"""
        cursor = await self._db.execute(
            'SELECT * FROM favorite_folders WHERE fav_id = ?', (fav_id,)
        )
        row = await cursor.fetchone()
        if row:
            return FavoriteFolder(**dict(row))
        return None

    @require_db
    async def set_favorite_folder_selected(self, fav_id: int, selected: bool) -> None:
        """设置收藏夹选中状态"""
        await self._db.execute('''
            UPDATE favorite_folders SET selected = ?, updated_at = ? WHERE fav_id = ?
        ''', (1 if selected else 0, datetime.now().isoformat(), fav_id))
        await self._db.commit()

    @require_db
    async def update_favorite_folder_cover(self, fav_id: int, cover_local: str) -> None:
        """更新收藏夹本地封面路径"""
        await self._db.execute('''
            UPDATE favorite_folders SET cover_local = ?, updated_at = ? WHERE fav_id = ?
        ''', (cover_local, datetime.now().isoformat(), fav_id))
        await self._db.commit()

    @require_db
    async def clear_favorite_folder_selection(self) -> None:
        """清除所有收藏夹的选中状态"""
        await self._db.execute('UPDATE favorite_folders SET selected = 0')
        await self._db.commit()

    @require_db
    async def delete_favorite_folder(self, fav_id: int) -> None:
        """删除收藏夹"""
        await self._db.execute(
            'DELETE FROM favorite_folders WHERE fav_id = ?', (fav_id,)
        )
        await self._db.commit()

    # ==================== VideoCache 操作方法 ====================

    @require_db
    async def add_or_update_video_cache(self, video: VideoCache) -> Optional[int]:
        """添加或更新视频缓存"""
        cursor = await self._db.execute('''
            INSERT INTO video_cache (bvid, title, cover, cover_local, duration, owner_name, source_type, source_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bvid, source_type, source_id) DO UPDATE SET
                title = excluded.title,
                cover = excluded.cover,
                cover_local = COALESCE(excluded.cover_local, cover_local),
                duration = excluded.duration,
                owner_name = excluded.owner_name,
                updated_at = excluded.updated_at
        ''', (
            video.bvid, video.title, video.cover, video.cover_local,
            video.duration, video.owner_name, video.source_type,
            video.source_id, video.created_at, video.updated_at
        ))
        await self._db.commit()
        return cursor.lastrowid

    @require_db
    async def batch_add_video_cache(self, videos: List[VideoCache]) -> None:
        """批量添加视频缓存"""
        for video in videos:
            await self.add_or_update_video_cache(video)

    @require_db
    async def get_videos_by_source(
        self, source_type: str, source_id: Optional[int] = None
    ) -> List[VideoCache]:
        """根据来源获取视频缓存"""
        if source_id:
            cursor = await self._db.execute(
                'SELECT * FROM video_cache WHERE source_type = ? AND source_id = ? ORDER BY created_at DESC',
                (source_type, source_id)
            )
        else:
            cursor = await self._db.execute(
                'SELECT * FROM video_cache WHERE source_type = ? ORDER BY created_at DESC',
                (source_type,)
            )
        rows = await cursor.fetchall()
        return [VideoCache(**dict(row)) for row in rows]

    @require_db
    async def get_video_cache_count_by_folder(self, fav_id: int) -> int:
        """获取指定收藏夹的视频缓存数量"""
        cursor = await self._db.execute(
            'SELECT COUNT(*) FROM video_cache WHERE source_type = ? AND source_id = ?',
            ('favorite', fav_id)
        )
        result = await cursor.fetchone()
        return result[0] if result else 0

    @require_db
    async def get_video_cache_count_map(self) -> Dict[int, int]:
        """获取所有收藏夹的视频缓存数量映射"""
        cursor = await self._db.execute(
            'SELECT source_id, COUNT(*) as count FROM video_cache WHERE source_type = ? GROUP BY source_id',
            ('favorite',)
        )
        rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    @require_db
    async def update_video_cache_cover(self, bvid: str, cover_local: str) -> None:
        """更新视频缓存本地封面路径"""
        await self._db.execute('''
            UPDATE video_cache SET cover_local = ?, updated_at = ? WHERE bvid = ?
        ''', (cover_local, datetime.now().isoformat(), bvid))
        await self._db.commit()

    @require_db
    async def clear_video_cache_by_source(
        self, source_type: str, source_id: Optional[int] = None
    ) -> None:
        """清除指定来源的视频缓存，可按来源 ID 精确清理。"""
        if source_id is None:
            await self._db.execute(
                'DELETE FROM video_cache WHERE source_type = ?', (source_type,)
            )
        else:
            await self._db.execute(
                'DELETE FROM video_cache WHERE source_type = ? AND source_id = ?',
                (source_type, source_id)
            )
        await self._db.commit()

    @require_db
    async def get_cached_videos_with_status(
        self,
        source_type: str = "favorite",
        source_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 50,
        search: str = "",
        status: str = "all",
    ) -> Dict[str, Any]:
        """获取缓存视频及其下载状态（轻量列表）。"""
        where_clauses = ["vc.source_type = ?"]
        params: List[Any] = [source_type]

        if source_id is not None:
            where_clauses.append("vc.source_id = ?")
            params.append(source_id)

        if search:
            where_clauses.append("(vc.title LIKE ? OR vc.bvid LIKE ? OR vc.owner_name LIKE ?)")
            params.extend([f"%{search}%"] * 3)

        where_sql = " AND ".join(where_clauses)

        # 总数
        count_cursor = await self._db.execute(
            f'SELECT COUNT(DISTINCT vc.bvid) FROM video_cache vc WHERE {where_sql}', params
        )
        total = (await count_cursor.fetchone())[0]

        # 分页查询
        offset = (page - 1) * page_size
        query = f'''
            SELECT
                vc.bvid,
                vc.title,
                vc.owner_name,
                vc.duration,
                vc.cover_local,
                vc.cover,
                vc.source_type,
                vc.source_id,
                vc.created_at,
                ff.title AS folder_name,
                MIN(CASE
                    WHEN v.s3_uploaded = 1 THEN 'uploaded'
                    WHEN v.local_path IS NOT NULL THEN 'downloaded'
                    WHEN v.upload_failed = 1 THEN 'failed'
                    ELSE NULL
                END) AS dl_status,
                MIN(v.source_status) AS source_status,
                COUNT(v.id) AS download_count
            FROM video_cache vc
            LEFT JOIN videos v ON vc.bvid = v.bvid
            LEFT JOIN favorite_folders ff ON vc.source_type = 'favorite' AND vc.source_id = ff.fav_id
            WHERE {where_sql}
            GROUP BY vc.bvid
            ORDER BY vc.created_at DESC
            LIMIT ? OFFSET ?
        '''
        params.extend([page_size, offset])
        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        items = []
        for row in rows:
            r = dict(row)
            dl = r['dl_status']
            src = r['source_status']
            if src == 'deleted':
                r['status'] = 'source_deleted'
            elif dl:
                r['status'] = dl
            else:
                r['status'] = 'pending'
            items.append(r)

        # 前端状态过滤
        if status != "all":
            items = [i for i in items if i['status'] == status]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        }

    # ==================== 任务队列 ====================

    @require_db
    async def enqueue_task(
        self,
        bvid: str,
        title: str,
        fav_id: Optional[int] = None,
        fav_title: Optional[str] = None,
        total_pages: int = 0,
        source: str = "sync",
    ) -> int:
        """新增一条任务，返回 task_id。"""
        now = datetime.now().isoformat()
        cursor = await self._db.execute('''
            INSERT INTO task_queue (
                bvid, title, fav_id, fav_title, status,
                current_page, total_pages, progress, current_action,
                source, enqueued_at
            ) VALUES (?, ?, ?, ?, 'queued', 0, ?, 0, '排队中', ?, ?)
        ''', (bvid, title, fav_id, fav_title, total_pages, source, now))
        await self._db.commit()
        return cursor.lastrowid

    @require_db
    async def update_task(
        self,
        task_id: int,
        *,
        status: Optional[str] = None,
        current_page: Optional[int] = None,
        total_pages: Optional[int] = None,
        progress: Optional[float] = None,
        current_action: Optional[str] = None,
        error_message: Optional[str] = None,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
    ) -> None:
        fields = []
        params: list = []
        for key, value in (
            ("status", status),
            ("current_page", current_page),
            ("total_pages", total_pages),
            ("progress", progress),
            ("current_action", current_action),
            ("error_message", error_message),
            ("started_at", started_at),
            ("finished_at", finished_at),
        ):
            if value is not None:
                fields.append(f"{key} = ?")
                params.append(value)
        if not fields:
            return
        params.append(task_id)
        await self._db.execute(
            f"UPDATE task_queue SET {', '.join(fields)} WHERE id = ?",
            params
        )
        await self._db.commit()

    @require_db
    async def get_task(self, task_id: int) -> Optional[dict]:
        cursor = await self._db.execute(
            'SELECT * FROM task_queue WHERE id = ?', (task_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    @require_db
    async def get_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> list:
        if status and status != 'all':
            cursor = await self._db.execute(
                'SELECT * FROM task_queue WHERE status = ? ORDER BY id DESC LIMIT ?',
                (status, limit)
            )
        else:
            cursor = await self._db.execute(
                'SELECT * FROM task_queue ORDER BY id DESC LIMIT ?',
                (limit,)
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    @require_db
    async def request_skip_task(self, task_id: int) -> bool:
        """请求跳过任务：queued 改 skipped；运行中改 cancelling（同步循环看到后自行结束）。"""
        task = await self.get_task(task_id)
        if not task:
            return False
        current = task["status"]
        if current in ("completed", "failed", "skipped"):
            return False
        if current == "queued":
            await self.update_task(
                task_id,
                status="skipped",
                current_action="已跳过",
                finished_at=datetime.now().isoformat()
            )
        else:
            # downloading / uploading：标记为 cancelling，由同步主流程在下一个 await 点退出
            await self.update_task(
                task_id,
                status="cancelling",
                current_action="正在跳过…"
            )
        return True

    @require_db
    async def clear_finished_tasks(self) -> int:
        """清理 completed / skipped / failed 的历史任务。"""
        cursor = await self._db.execute(
            "DELETE FROM task_queue WHERE status IN ('completed', 'skipped', 'failed')"
        )
        await self._db.commit()
        return cursor.rowcount or 0

    @require_db
    async def reset_orphan_tasks(self) -> int:
        """启动时把上次进程残留的 queued/downloading/uploading/cancelling 标记为 failed。"""
        now = datetime.now().isoformat()
        cursor = await self._db.execute(
            "UPDATE task_queue SET status = 'failed', current_action = '进程异常退出', "
            "error_message = '上次运行未结束', finished_at = ? "
            "WHERE status IN ('queued', 'downloading', 'uploading', 'cancelling')",
            (now,)
        )
        await self._db.commit()
        return cursor.rowcount or 0

    @require_db
    async def get_interrupted_tasks(self) -> list:
        """返回上次进程被打断的任务（由 reset_orphan_tasks 标记的）。"""
        cursor = await self._db.execute(
            "SELECT * FROM task_queue "
            "WHERE status = 'failed' AND error_message = '上次运行未结束' "
            "ORDER BY id ASC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ==================== 黑名单 ====================

    @require_db
    async def add_blacklist(
        self, bvid: str, title: Optional[str] = None, reason: Optional[str] = None
    ) -> None:
        await self._db.execute('''
            INSERT INTO task_blacklist (bvid, title, reason, added_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                title = COALESCE(excluded.title, task_blacklist.title),
                reason = COALESCE(excluded.reason, task_blacklist.reason)
        ''', (bvid, title, reason, datetime.now().isoformat()))
        await self._db.commit()

    @require_db
    async def remove_blacklist(self, bvid: str) -> None:
        await self._db.execute('DELETE FROM task_blacklist WHERE bvid = ?', (bvid,))
        await self._db.commit()

    @require_db
    async def get_blacklist(self) -> list:
        cursor = await self._db.execute(
            'SELECT * FROM task_blacklist ORDER BY added_at DESC'
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    @require_db
    async def is_blacklisted(self, bvid: str) -> bool:
        cursor = await self._db.execute(
            'SELECT 1 FROM task_blacklist WHERE bvid = ? LIMIT 1', (bvid,)
        )
        row = await cursor.fetchone()
        return row is not None
