"""
同步管理模块
负责对比本地和 S3 的视频，决策下载策略，执行同步流程
"""
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from loguru import logger

from core.config import get_config
from core.database import Database, Video, SyncHistory, FavoriteFolder
from services.bilibili_api import BilibiliAPI
from services.downloader import Downloader
from services.s3_uploader import S3Uploader
from services.notification import NotificationService, NotificationLevel


class SyncStatus:
    """同步状态枚举"""
    PENDING = "pending"
    SKIPPED = "skipped"
    NEW_DOWNLOAD = "new_download"
    QUALITY_UPGRADE = "quality_upgrade"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"


class SyncManager:
    """同步管理器"""

    def __init__(self):
        self.config = get_config()
        self.db: Optional[Database] = None
        self.api = BilibiliAPI()
        self.downloader = Downloader()
        self.s3_uploader = S3Uploader()
        self.notification = NotificationService.get_instance()

        # 同步状态
        self.is_syncing = False
        self.sync_progress = {
            "current": 0,
            "total": 0,
            "current_video": ""
        }

        # 增量同步：记录上次同步时间
        self.last_sync_time: Optional[datetime] = None

    async def get_last_sync_time(self, fav_id: str) -> Optional[datetime]:
        """获取指定收藏夹上次成功同步的时间"""
        if not self.db:
            await self.init_db()

        history = await self.db.get_recent_sync_history(limit=10)
        for h in history:
            if h.fav_id == fav_id and h.status == "completed":
                try:
                    return datetime.fromisoformat(h.end_time)
                except:
                    pass
        return None

    def filter_new_videos(
        self,
        videos: List[Dict[str, Any]],
        last_sync_time: Optional[datetime]
    ) -> List[Dict[str, Any]]:
        """
        过滤出新增的视频（增量同步）

        Args:
            videos: 视频列表
            last_sync_time: 上次同步时间

        Returns:
            新增视频列表
        """
        if not last_sync_time:
            return videos

        new_videos = []
        for video in videos:
            fav_time = video.get("fav_time", 0)
            if fav_time:
                try:
                    video_time = datetime.fromtimestamp(fav_time)
                    if video_time > last_sync_time:
                        new_videos.append(video)
                except:
                    new_videos.append(video)
            else:
                # 没有收藏时间，保守处理
                new_videos.append(video)

        return new_videos

    async def init_db(self) -> None:
        """初始化数据库连接"""
        self.db = await Database.get_instance()
        await self.downloader.init_db()
        await self.s3_uploader.init_db()

    async def start_sync(self, manual: bool = False) -> bool:
        """
        开始同步收藏夹

        Args:
            manual: 是否手动触发

        Returns:
            是否成功启动
        """
        if self.is_syncing:
            logger.warning("同步任务正在进行中")
            return False

        try:
            await self.init_db()

            # 检查配置
            if not self.config.bilibili.cookie:
                await self.notification.send_error(
                    title="配置不完整",
                    message="B 站 Cookie 未配置"
                )
                return False

            if self.config.s3.enabled and not self.config.validate_s3_config():
                await self.notification.send_error(
                    title="配置不完整",
                    message="S3 配置不完整"
                )
                return False

            # 验证 Cookie
            cookie_valid, cookie_msg = await self.api.validate_cookie()
            if not cookie_valid:
                logger.error(f"Cookie 无效：{cookie_msg}")
                await self.notification.notify_cookie_invalid(cookie_msg)
                return False

            logger.info(f"开始同步收藏夹 (手动={manual})")

            # 创建同步记录
            sync_history = SyncHistory(
                id=None,
                fav_id=self.config.bilibili.fav_id,
                total_videos=0,
                new_videos=0,
                updated_videos=0,
                skipped_videos=0,
                failed_videos=0,
                start_time=datetime.now().isoformat(),
                end_time=None,
                status="running",
                error_message=None
            )

            history_id = await self.db.add_sync_history(sync_history)
            sync_history.id = history_id

            # 设置同步状态
            self.is_syncing = True
            self.sync_progress = {
                "current": 0,
                "total": 0,
                "current_video": ""
            }

            # 执行同步
            result = await self._execute_sync(sync_history)

            # 更新同步记录
            sync_history.end_time = datetime.now().isoformat()
            sync_history.status = "completed" if result else "failed"
            if sync_history.id is not None:
                await self.db.update_sync_history(sync_history)

            # 发送完成通知
            await self.notification.notify_sync_completed(
                total=sync_history.total_videos,
                new=sync_history.new_videos,
                updated=sync_history.updated_videos,
                skipped=sync_history.skipped_videos,
                failed=sync_history.failed_videos
            )

            logger.info("同步完成")
            self.is_syncing = False

            return result

        except Exception as e:
            logger.error(f"同步异常：{e}")
            self.is_syncing = False
            return False

    async def _execute_sync(
        self, sync_history: SyncHistory
    ) -> bool:
        """
        执行同步流程（支持多收藏夹）

        Returns:
            是否成功
        """
        # 获取要同步的收藏夹列表
        selected_folders = await self.db.get_selected_favorite_folders()

        if not selected_folders:
            # 如果没有选中任何收藏夹，使用配置中的默认收藏夹
            fav_id = self.config.bilibili.fav_id
            if fav_id:
                logger.info(f"使用配置中的默认收藏夹：{fav_id}")
                fav_ids = [fav_id]
            else:
                logger.warning("没有选中任何收藏夹，也未配置默认收藏夹")
                return True
        else:
            fav_ids = [str(f.fav_id) for f in selected_folders]
            logger.info(f"将同步 {len(fav_ids)} 个收藏夹：{fav_ids}")

        # 汇总统计
        total_new = 0
        total_updated = 0
        total_skipped = 0
        total_failed = 0
        total_videos = 0

        for fav_id in fav_ids:
            logger.info(f"开始同步收藏夹：{fav_id}")

            # 同步单个收藏夹
            result = await self._sync_single_favorite(fav_id)

            total_videos += result["total"]
            total_new += result["new"]
            total_updated += result["updated"]
            total_skipped += result["skipped"]
            total_failed += result["failed"]

        # 更新汇总统计
        sync_history.total_videos = total_videos
        sync_history.new_videos = total_new
        sync_history.updated_videos = total_updated
        sync_history.skipped_videos = total_skipped
        sync_history.failed_videos = total_failed

        return total_failed == 0

    async def _sync_single_favorite(self, fav_id: str) -> Dict[str, int]:
        """
        同步单个收藏夹

        Args:
            fav_id: 收藏夹 ID

        Returns:
            统计结果字典
        """
        # 1. 获取收藏夹视频列表
        success, videos_list, error = await self.api.get_favorites_list(fav_id)

        if not success:
            logger.error(f"获取收藏夹 {fav_id} 失败：{error}")
            await self.notification.send_error(
                title="获取收藏夹失败",
                message=f"无法获取收藏夹 {fav_id}: {error}"
            )
            return {"total": 0, "new": 0, "updated": 0, "skipped": 0, "failed": 0}

        total_videos = len(videos_list)
        logger.info(f"收藏夹 {fav_id} 共有 {total_videos} 个视频")

        if total_videos == 0:
            logger.info(f"收藏夹 {fav_id} 为空")
            return {"total": 0, "new": 0, "updated": 0, "skipped": 0, "failed": 0}

        # 获取收藏夹信息（用于 S3 路径命名）
        fav_title = None
        fav_folder = await self.db.get_favorite_folder(int(fav_id))
        if fav_folder:
            fav_title = fav_folder.title
        else:
            # 尝试从第一个视频获取收藏夹名称
            if videos_list and "fav_title" in videos_list[0]:
                fav_title = videos_list[0].get("fav_title")

        # 增量同步：过滤新增视频
        last_sync_time = await self.get_last_sync_time(fav_id)
        if last_sync_time:
            original_count = len(videos_list)
            videos_list = self.filter_new_videos(videos_list, last_sync_time)
            logger.info(
                f"增量同步：发现 {len(videos_list)} 个新视频 "
                f"(共 {original_count} 个视频)"
            )

        # 2. 遍历每个视频
        new_count = 0
        updated_count = 0
        skipped_count = 0
        failed_count = 0

        self.sync_progress["total"] += len(videos_list)

        for idx, video_info in enumerate(videos_list):
            bvid = video_info["bvid"]
            title = video_info["title"]
            fav_time = video_info.get("fav_time")  # 收藏时间戳

            self.sync_progress["current"] += 1
            self.sync_progress["current_video"] = title

            logger.info(
                f"[收藏夹 {fav_id}] [{idx + 1}/{len(videos_list)}] 处理视频：{title} ({bvid})"
            )

            try:
                result = await self._process_single_video(
                    bvid=bvid,
                    title=title,
                    video_info=video_info,
                    fav_id=int(fav_id),
                    fav_title=fav_title,
                    fav_time=fav_time
                )

                if result == "new":
                    new_count += 1
                elif result == "updated":
                    updated_count += 1
                elif result == "skipped":
                    skipped_count += 1
                elif result == "failed":
                    failed_count += 1

            except Exception as e:
                logger.error(f"处理视频 {bvid} 异常：{e}")
                failed_count += 1
                continue

        return {
            "total": total_videos,
            "new": new_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "failed": failed_count
        }

    async def _process_single_video(
        self, bvid: str, title: str, video_info: Dict[str, Any],
        fav_id: Optional[int] = None, fav_title: Optional[str] = None,
        fav_time: Optional[int] = None
    ) -> str:
        """
        处理单个视频

        Args:
            bvid: 视频 BV 号
            title: 视频标题
            video_info: 视频信息
            fav_id: 收藏夹 ID
            fav_title: 收藏夹名称
            fav_time: 收藏时间戳

        Returns:
            处理结果: "new", "updated", "skipped", "failed"
        """
        # 获取视频分 P 信息
        success, pages, error = await self.api.get_video_pages(bvid)

        if not success or not pages:
            logger.warning(f"获取视频分 P 信息失败：{error}")
            return "failed"

        # 获取可用清晰度
        success, qualities, error = await self.api.get_video_quality(
            bvid, pages[0]["cid"]
        )

        available_quality = qualities[0] if qualities else 16

        # 查询 S3 中是否已存在
        existing_video = await self.db.get_video_by_bvid(bvid)

        should_download = False
        is_upgrade = False

        if not existing_video or not existing_video.s3_uploaded:
            # S3 不存在，需要下载
            should_download = True
            logger.info(f"视频 {bvid} 未备份，计划下载")
        else:
            # 对比清晰度
            s3_quality = existing_video.s3_quality or 0

            if available_quality > s3_quality:
                # 有更高清晰度，升级下载（不替换）
                should_download = True
                is_upgrade = True
                logger.info(
                    f"视频 {bvid} 发现更高清晰度："
                    f"{s3_quality} → {available_quality}"
                )
            else:
                # 清晰度相同或更低，跳过
                logger.info(f"视频 {bvid} 已备份且清晰度足够，跳过")
                return "skipped"

        # 下载视频（多 P 处理）
        if should_download:
            # 下载所有分 P
            results = await self.downloader.download_multi_page_video(
                bvid=bvid,
                title=title,
                pages=pages,
                target_quality=self.config.download.quality
            )

            # 上传到 S3
            for page_num, success, file_path, error in results:
                if success and file_path:
                    # 创建 Video 对象
                    page_info = next(
                        (p for p in pages if p["page"] == page_num),
                        None
                    )

                    if not page_info:
                        continue

                    video_obj = Video(
                        id=None,
                        bvid=bvid,
                        title=f"{title}_P{page_num}",
                        cid=page_info["cid"],
                        page=page_num,
                        total_pages=len(pages),
                        quality=available_quality,
                        duration=page_info["duration"],
                        pubdate=video_info.get("pubdate", 0),
                        owner_name=video_info.get("owner", ""),
                        s3_key=None,
                        s3_uploaded=False,
                        s3_quality=None,
                        local_path=file_path,
                        max_quality=available_quality,
                        fav_id=fav_id,
                        fav_title=fav_title,
                        fav_time=fav_time,
                        created_at=datetime.now().isoformat(),
                        updated_at=datetime.now().isoformat()
                    )

                    # 先入库，保证后续状态更新有基准记录
                    await self.db.add_video(video_obj)

                    if self.config.s3.enabled:
                        # 上传到 S3
                        upload_success, s3_key, upload_error = \
                            await self.s3_uploader.upload_video(
                                video=video_obj,
                                local_path=file_path,
                                delete_after_upload=self.config.s3.delete_after_upload
                            )

                        if not upload_success:
                            logger.error(f"上传失败 {bvid} P{page_num}: {upload_error}")
                            await self.db.set_upload_failed(bvid, video_obj.page, True)
                            await self.notification.notify_upload_failed(
                                bvid=bvid,
                                title=title,
                                error=upload_error,
                                retry_count=self.config.s3.retry_times
                            )
                            return "failed"
                    else:
                        # 仅本地备份模式：标记为已完成，避免下次重复下载
                        await self.db.update_video_s3_status(
                            bvid=video_obj.bvid,
                            page=video_obj.page,
                            s3_key=f"local://{file_path}",
                            quality=video_obj.quality
                        )
                else:
                    logger.error(f"下载失败 {bvid} P{page_num}: {error}")
                    await self.notification.notify_download_failed(
                        bvid=bvid,
                        title=f"{title}_P{page_num}",
                        error=error
                    )
                    return "failed"

            return "updated" if is_upgrade else "new"

        return "skipped"

    def get_sync_progress(self) -> Dict[str, Any]:
        """获取同步进度"""
        return {
            "is_syncing": self.is_syncing,
            **self.sync_progress
        }

    async def get_sync_status(self) -> Dict[str, Any]:
        """获取同步状态摘要"""
        if not self.db:
            await self.init_db()

        # 获取最近的同步历史
        recent_history = await self.db.get_recent_sync_history(limit=1)

        # 获取统计信息
        all_videos = await self.db.get_all_videos()
        uploaded_count = sum(1 for v in all_videos if v.s3_uploaded)

        return {
            "is_syncing": self.is_syncing,
            "total_videos": len(all_videos),
            "uploaded_videos": uploaded_count,
            "pending_videos": len(all_videos) - uploaded_count,
            "last_sync": recent_history[0].to_dict() if recent_history else None
        }
