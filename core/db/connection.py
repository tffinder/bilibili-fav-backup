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
                UNIQUE(bvid, cid, page)
            )
        ''')

        # 添加新列（如果不存在）
        await self._safe_add_column('videos', 'max_quality', 'INTEGER')
        await self._safe_add_column('videos', 'upload_failed', 'INTEGER DEFAULT 0')
        await self._safe_add_column('videos', 'fav_id', 'INTEGER')
        await self._safe_add_column('videos', 'fav_title', 'TEXT')
        await self._safe_add_column('videos', 'fav_time', 'INTEGER')

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

        # 创建索引
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_videos_bvid ON videos(bvid)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_videos_s3_uploaded ON videos(s3_uploaded)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_notifications_is_read ON notifications(is_read)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_favorite_folders_selected ON favorite_folders(selected)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_video_cache_bvid ON video_cache(bvid)')
        await self._db.execute('CREATE INDEX IF NOT EXISTS idx_video_cache_source ON video_cache(source_type, source_id)')

        await self._db.commit()

    async def _safe_add_column(self, table: str, column: str, definition: str) -> None:
        """安全添加列（如果不存在）"""
        try:
            await self._db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        except:
            pass

    # ==================== Video 操作方法 ====================

    @require_db
    async def add_video(self, video: Video) -> Optional[int]:
        """添加视频"""
        cursor = await self._db.execute('''
            INSERT OR REPLACE INTO videos
            (bvid, title, cid, page, total_pages, quality, duration, pubdate,
             owner_name, s3_key, s3_uploaded, s3_quality, local_path, created_at, updated_at,
             max_quality, upload_failed, fav_id, fav_title, fav_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            video.bvid, video.title, video.cid, video.page, video.total_pages,
            video.quality, video.duration, video.pubdate, video.owner_name,
            video.s3_key, 1 if video.s3_uploaded else 0, video.s3_quality,
            video.local_path, video.created_at, video.updated_at,
            video.max_quality, 1 if video.upload_failed else 0,
            video.fav_id, video.fav_title, video.fav_time
        ))
        await self._db.commit()
        return cursor.lastrowid

    @require_db
    async def get_video_by_bvid(self, bvid: str) -> Optional[Video]:
        """根据 BVID 获取视频"""
        cursor = await self._db.execute(
            'SELECT * FROM videos WHERE bvid = ?', (bvid,)
        )
        row = await cursor.fetchone()
        if row:
            return Video(**dict(row))
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
        """更新视频 S3 上传状态"""
        await self._db.execute('''
            UPDATE videos
            SET s3_key = ?, s3_uploaded = 1, s3_quality = ?, upload_failed = 0, updated_at = ?
            WHERE bvid = ? AND page = ?
        ''', (s3_key, quality, datetime.now().isoformat(), bvid, page))
        await self._db.commit()

    @require_db
    async def set_upload_failed(self, bvid: str, page: int, failed: bool = True) -> None:
        """设置上传失败状态"""
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
    async def clear_video_cache_by_source(self, source_type: str) -> None:
        """清除指定来源的视频缓存"""
        await self._db.execute(
            'DELETE FROM video_cache WHERE source_type = ?', (source_type,)
        )
        await self._db.commit()
