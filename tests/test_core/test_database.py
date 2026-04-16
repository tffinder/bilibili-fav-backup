"""
数据库模块测试
"""
import pytest
import pytest_asyncio
from datetime import datetime

from core.db.models import Video, SyncHistory, Notification, DownloadProgress, FavoriteFolder
from core.db.connection import Database


class TestModels:
    """数据模型测试"""

    def test_video_model(self, sample_video_data):
        """测试视频模型"""
        video = Video(**sample_video_data)
        assert video.bvid == "BV1test12345"
        assert video.title == "测试视频"
        assert video.page == 1

    def test_video_to_dict(self, sample_video_data):
        """测试视频转字典"""
        video = Video(**sample_video_data)
        result = video.to_dict()
        assert result["bvid"] == "BV1test12345"
        assert result["title"] == "测试视频"

    def test_video_from_dict(self, sample_video_data):
        """测试从字典创建视频"""
        video = Video.from_dict(sample_video_data)
        assert video.bvid == "BV1test12345"

    def test_sync_history_model(self):
        """测试同步历史模型"""
        history = SyncHistory(
            id=None,
            fav_id="12345",
            total_videos=10,
            new_videos=2,
            updated_videos=1,
            skipped_videos=6,
            failed_videos=1,
            start_time="2024-01-01T00:00:00",
            end_time=None,
            status="running",
            error_message=None
        )
        assert history.fav_id == "12345"
        assert history.total_videos == 10

    def test_notification_model(self):
        """测试通知模型"""
        notification = Notification(
            id=None,
            level="info",
            title="测试通知",
            message="这是一条测试通知",
            created_at="2024-01-01T00:00:00",
            is_read=False
        )
        assert notification.level == "info"
        assert notification.is_read is False

    def test_favorite_folder_model(self):
        """测试收藏夹模型"""
        folder = FavoriteFolder(
            id=None,
            fav_id=12345,
            title="我的收藏",
            media_count=100,
            cover="https://example.com/cover.jpg",
            cover_local=None,
            selected=True,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00"
        )
        assert folder.fav_id == 12345
        assert folder.selected is True


@pytest.mark.asyncio
class TestDatabase:
    """数据库操作测试"""

    async def test_database_connect(self, test_db):
        """测试数据库连接"""
        assert test_db._db is not None

    async def test_add_video(self, test_db, sample_video_data):
        """测试添加视频"""
        video = Video(**sample_video_data)
        video_id = await test_db.add_video(video)
        assert video_id is not None

    async def test_get_video_by_bvid(self, test_db, sample_video_data):
        """测试根据 BVID 获取视频"""
        video = Video(**sample_video_data)
        await test_db.add_video(video)

        result = await test_db.get_video_by_bvid("BV1test12345")
        assert result is not None
        assert result.bvid == "BV1test12345"

    async def test_get_all_videos(self, test_db, sample_video_data):
        """测试获取所有视频"""
        video1 = Video(**sample_video_data)
        video2 = Video(**{**sample_video_data, "bvid": "BV2test67890"})
        await test_db.add_video(video1)
        await test_db.add_video(video2)

        videos = await test_db.get_all_videos()
        assert len(videos) == 2

    async def test_update_video_s3_status(self, test_db, sample_video_data):
        """测试更新视频 S3 状态"""
        video = Video(**sample_video_data)
        await test_db.add_video(video)

        await test_db.update_video_s3_status(
            bvid="BV1test12345",
            page=1,
            s3_key="test/video.mp4",
            quality=127
        )

        result = await test_db.get_video_by_bvid("BV1test12345")
        assert result.s3_uploaded is True
        assert result.s3_key == "test/video.mp4"

    async def test_add_notification(self, test_db):
        """测试添加通知"""
        notification = Notification(
            id=None,
            level="info",
            title="测试通知",
            message="测试消息",
            created_at=datetime.now().isoformat(),
            is_read=False
        )
        notification_id = await test_db.add_notification(notification)
        assert notification_id is not None

    async def test_get_unread_notifications(self, test_db):
        """测试获取未读通知"""
        notification = Notification(
            id=None,
            level="info",
            title="测试通知",
            message="测试消息",
            created_at=datetime.now().isoformat(),
            is_read=False
        )
        await test_db.add_notification(notification)

        notifications = await test_db.get_unread_notifications()
        assert len(notifications) == 1

    async def test_mark_notification_read(self, test_db):
        """测试标记通知已读"""
        notification = Notification(
            id=None,
            level="info",
            title="测试通知",
            message="测试消息",
            created_at=datetime.now().isoformat(),
            is_read=False
        )
        notification_id = await test_db.add_notification(notification)

        await test_db.mark_notification_read(notification_id)

        notifications = await test_db.get_unread_notifications()
        assert len(notifications) == 0
