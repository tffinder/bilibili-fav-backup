"""
同步管理模块
负责对比本地和 S3 的视频，决策下载策略，执行同步流程
"""
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from loguru import logger

from core.config import get_config
from core.database import Database, Video, SyncHistory, FavoriteFolder, VideoCache
from services.bilibili_api import BilibiliAPI
from services.downloader import Downloader
from services.s3_uploader import S3Uploader
from services.notification import NotificationService, NotificationLevel


def _progress_failed(progress) -> bool:
    return bool(progress and progress.status in {"failed", "upload_failed"})


def _is_local_only(video: Video) -> bool:
    """本地保留模式：s3_uploaded 为真但 s3_key 是 local:// 占位符"""
    return bool(video.s3_uploaded and video.s3_key and video.s3_key.startswith("local://"))


def _matches_archive_status(video: Video, status: str, progress=None) -> bool:
    if status == "uploaded":
        # 本地保留模式不计入 uploaded
        return video.s3_uploaded and not _is_local_only(video)
    if status == "downloaded":
        return bool(video.local_path or video.s3_uploaded)
    if status == "failed":
        return not video.s3_uploaded and bool(video.upload_failed or _progress_failed(progress))
    if status == "pending":
        return not video.s3_uploaded and not video.upload_failed and not _progress_failed(progress)
    return True


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
                except (ValueError, TypeError) as e:
                    logger.debug(f"解析同步时间失败: {h.end_time}, 错误: {e}")
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
                except (ValueError, TypeError, OSError) as e:
                    logger.debug(f"解析收藏时间失败: {fav_time}, 错误: {e}")
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

    async def resume_interrupted(self) -> Dict[str, int]:
        """重跑上次进程中断时残留的任务（不再扫收藏夹列表）。"""
        if self.is_syncing:
            logger.warning("同步任务正在进行中，无法启动 resume")
            return {"total": 0, "completed": 0, "failed": 0, "skipped": 0}

        await self.init_db()
        interrupted = await self.db.get_interrupted_tasks()
        if not interrupted:
            logger.info("没有需要继续的中断任务")
            return {"total": 0, "completed": 0, "failed": 0, "skipped": 0}

        # Cookie / S3 健康检查
        if not self.config.bilibili.cookie:
            await self.notification.send_error("配置不完整", "B 站 Cookie 未配置")
            return {"total": 0, "completed": 0, "failed": 0, "skipped": 0}
        cookie_valid, cookie_msg = await self.api.validate_cookie()
        if not cookie_valid:
            await self.notification.notify_cookie_invalid(cookie_msg)
            return {"total": 0, "completed": 0, "failed": 0, "skipped": 0}

        self.is_syncing = True
        self.sync_progress = {
            "current": 0,
            "total": len(interrupted),
            "current_video": ""
        }

        completed = 0
        failed = 0
        skipped = 0

        # 创建 SyncHistory 让前端能看到 "同步中"
        sync_history = SyncHistory(
            id=None,
            fav_id="resume",
            total_videos=len(interrupted),
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

        try:
            for old in interrupted:
                bvid = old["bvid"]
                title = old["title"]
                fav_id = old.get("fav_id")
                fav_title = old.get("fav_title")

                self.sync_progress["current"] += 1
                self.sync_progress["current_video"] = title
                logger.info(f"[继续中断] [{self.sync_progress['current']}/{len(interrupted)}] {title} ({bvid})")

                # 黑名单则直接跳，并把旧任务清掉
                if await self.db.is_blacklisted(bvid):
                    skipped += 1
                    continue

                # 把旧 failed 任务清掉，避免下次又把它当中断
                await self.db.update_task(
                    old["id"],
                    error_message="已重跑"
                )

                task_id = await self.db.enqueue_task(
                    bvid=bvid,
                    title=title,
                    fav_id=fav_id,
                    fav_title=fav_title,
                    source="resume"
                )

                # 构造一个最小 video_info（owner/pubdate 可空，不影响下载逻辑）
                video_info = {
                    "bvid": bvid,
                    "title": title,
                    "owner": "",
                    "pubdate": 0,
                }

                try:
                    result = await self._process_single_video(
                        bvid=bvid,
                        title=title,
                        video_info=video_info,
                        fav_id=fav_id,
                        fav_title=fav_title,
                        fav_time=None,
                        task_id=task_id
                    )
                    if result == "skipped":
                        skipped += 1
                    elif result == "failed":
                        failed += 1
                    else:
                        completed += 1
                except Exception as e:
                    logger.error(f"重跑视频 {bvid} 异常：{e}")
                    failed += 1
                    await self.db.update_task(
                        task_id,
                        status="failed",
                        error_message=str(e)[:500],
                        current_action="异常退出",
                        finished_at=datetime.now().isoformat()
                    )

            sync_history.total_videos = len(interrupted)
            sync_history.new_videos = completed
            sync_history.skipped_videos = skipped
            sync_history.failed_videos = failed
            sync_history.end_time = datetime.now().isoformat()
            sync_history.status = "completed" if failed == 0 else "failed"
            await self.db.update_sync_history(sync_history)

            await self.notification.notify_sync_completed(
                total=len(interrupted),
                new=completed,
                updated=0,
                skipped=skipped,
                failed=failed
            )
        finally:
            self.is_syncing = False

        return {"total": len(interrupted), "completed": completed, "failed": failed, "skipped": skipped}

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

        # 本地缓存收藏夹视频资料，页面默认读缓存，需要时再手动刷新。
        now = datetime.now().isoformat()
        await self.db.clear_video_cache_by_source("favorite", int(fav_id))
        for video in videos_list:
            cache = VideoCache(
                id=None,
                bvid=video.get("bvid", ""),
                title=video.get("title", ""),
                cover=video.get("cover", ""),
                cover_local=None,
                duration=video.get("duration", 0),
                owner_name=video.get("owner", ""),
                source_type="favorite",
                source_id=int(fav_id),
                created_at=now,
                updated_at=now
            )
            await self.db.add_or_update_video_cache(cache)

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

        # 获取收藏夹标题用于任务记录
        fav_folder = await self.db.get_favorite_folder(int(fav_id)) if not fav_title else None
        if fav_folder and not fav_title:
            fav_title = fav_folder.title

        for idx, video_info in enumerate(videos_list):
            bvid = video_info["bvid"]
            title = video_info["title"]
            fav_time = video_info.get("fav_time")  # 收藏时间戳

            self.sync_progress["current"] += 1
            self.sync_progress["current_video"] = title

            logger.info(
                f"[收藏夹 {fav_id}] [{idx + 1}/{len(videos_list)}] 处理视频：{title} ({bvid})"
            )

            # 黑名单：直接跳过，不入队（不打扰用户视野）
            if await self.db.is_blacklisted(bvid):
                logger.info(f"视频 {bvid} 在黑名单中，跳过")
                skipped_count += 1
                continue

            # 写入任务队列
            task_id = await self.db.enqueue_task(
                bvid=bvid,
                title=title,
                fav_id=int(fav_id),
                fav_title=fav_title,
                source="sync"
            )

            try:
                result = await self._process_single_video(
                    bvid=bvid,
                    title=title,
                    video_info=video_info,
                    fav_id=int(fav_id),
                    fav_title=fav_title,
                    fav_time=fav_time,
                    task_id=task_id
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
                await self.db.update_task(
                    task_id,
                    status="failed",
                    error_message=str(e)[:500],
                    current_action="异常退出",
                    finished_at=datetime.now().isoformat()
                )
                continue

        return {
            "total": total_videos,
            "new": new_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "failed": failed_count
        }

    async def _check_skip_rules(
        self, bvid: str, title: str, pages: List[Dict[str, Any]]
    ) -> Optional[str]:
        """根据 config.skip_rules 判断是否跳过；返回原因字符串或 None。"""
        rules = self.config.skip_rules
        durations = [p.get("duration", 0) or 0 for p in pages]
        total_duration = sum(durations)
        max_page_duration = max(durations) if durations else 0

        if rules.max_single_duration > 0 and max_page_duration > rules.max_single_duration:
            return (
                f"单 P 时长 {max_page_duration}s 超过上限 {rules.max_single_duration}s"
            )

        if rules.max_total_duration > 0 and total_duration > rules.max_total_duration:
            return (
                f"合计时长 {total_duration}s 超过上限 {rules.max_total_duration}s"
            )

        if rules.skip_interactive:
            ok, info, _ = await self.api.get_video_info(bvid)
            if ok and info.get("is_interactive"):
                return "互动视频已跳过"

        return None

    async def _process_single_video(
        self, bvid: str, title: str, video_info: Dict[str, Any],
        fav_id: Optional[int] = None, fav_title: Optional[str] = None,
        fav_time: Optional[int] = None,
        task_id: Optional[int] = None,
        up_mid: Optional[int] = None, up_name: Optional[str] = None
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
            task_id: 关联的任务队列 id（None 表示不更新）

        Returns:
            处理结果: "new", "updated", "skipped", "failed"
        """

        async def _is_cancelled() -> bool:
            if task_id is None:
                return False
            task = await self.db.get_task(task_id)
            return bool(task and task.get("status") == "cancelling")

        async def _finish_task(status: str, action: str, error: Optional[str] = None) -> None:
            if task_id is None:
                return
            await self.db.update_task(
                task_id,
                status=status,
                current_action=action,
                error_message=error,
                progress=100.0 if status == "completed" else None,
                finished_at=datetime.now().isoformat()
            )

        # 标记任务开始
        if task_id is not None:
            await self.db.update_task(
                task_id,
                status="downloading",
                current_action="读取分 P 信息",
                started_at=datetime.now().isoformat()
            )

        # 获取视频分 P 信息
        success, pages, error = await self.api.get_video_pages(bvid)

        if not success or not pages:
            logger.warning(f"获取视频分 P 信息失败：{error}")
            await _finish_task("failed", "获取分 P 失败", error or "")
            return "failed"

        if await _is_cancelled():
            await _finish_task("skipped", "用户跳过")
            return "skipped"

        # 按用户规则跳过：时长 / 互动视频
        skip_reason = await self._check_skip_rules(bvid, title, pages)
        if skip_reason:
            logger.info(f"视频 {bvid} 命中跳过规则：{skip_reason}")
            await _finish_task("skipped", f"规则跳过：{skip_reason}")
            return "skipped"

        # 更新任务总分 P
        if task_id is not None:
            await self.db.update_task(task_id, total_pages=len(pages))

        # 获取可用清晰度
        success, qualities, error = await self.api.get_video_quality(
            bvid, pages[0]["cid"]
        )

        available_quality = qualities[0] if qualities else 16

        # 查询 S3 中已上传的最高清晰度
        best_uploaded_quality = await self.db.get_best_uploaded_quality(bvid)

        is_upgrade = False

        if best_uploaded_quality is None:
            logger.info(f"视频 {bvid} 未备份，计划下载")
        elif available_quality > best_uploaded_quality:
            is_upgrade = True
            logger.info(
                f"视频 {bvid} 发现更高清晰度："
                f"{best_uploaded_quality} → {available_quality}，将保留原版本"
            )
        else:
            logger.info(f"视频 {bvid} 已备份且清晰度足够，跳过")
            await _finish_task("completed", "已有同等或更高清晰度备份")
            return "skipped"

        # 下载并发：尊重配置；上传并发：与共享线程池对齐
        download_sem = asyncio.Semaphore(max(1, self.config.download.max_parallel))
        upload_sem = asyncio.Semaphore(4)
        target_quality = self.config.download.quality

        async def _process_page(page_info: Dict[str, Any]) -> Tuple[str, int, Optional[str]]:
            page_num = page_info["page"]
            page_label = page_info.get("title") or f"P{page_num}"
            full_title = f"{title}_{page_label}"

            if await _is_cancelled():
                return ("cancelled", page_num, "用户跳过")

            # 1) 下载（受 download_sem 控制；多 P 时也尊重 request_delay）
            async with download_sem:
                if await _is_cancelled():
                    return ("cancelled", page_num, "用户跳过")
                if task_id is not None:
                    await self.db.update_task(
                        task_id,
                        status="downloading",
                        current_page=page_num,
                        current_action=f"下载 P{page_num}/{len(pages)}",
                        progress=round((page_num - 1) / len(pages) * 100, 1)
                    )
                if self.config.download.request_delay > 0:
                    await asyncio.sleep(self.config.download.request_delay)
                d_success, file_path, d_error = await self.downloader.download_single_video(
                    bvid=bvid,
                    cid=page_info["cid"],
                    title=full_title,
                    page=page_num,
                    target_quality=target_quality
                )

            if not d_success or not file_path:
                if d_error and "文件大小超过限制" in d_error:
                    logger.info(f"视频 {bvid} P{page_num} 因文件大小超限跳过: {d_error}")
                    return ("size_skipped", page_num, d_error)
                logger.error(f"下载失败 {bvid} P{page_num}: {d_error}")
                await self.notification.notify_download_failed(
                    bvid=bvid,
                    title=f"{title}_P{page_num}",
                    error=d_error or ""
                )
                return ("download_failed", page_num, d_error)

            # 2) 入库
            now_iso = datetime.now().isoformat()
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
                fav_title=fav_title or up_name,
                fav_time=fav_time,
                up_mid=up_mid,
                up_name=up_name,
                created_at=now_iso,
                updated_at=now_iso
            )
            await self.db.add_video(video_obj)

            # 3) 上传或本地标记（与下一 P 的下载流水线并行）
            if self.config.s3.enabled:
                if await _is_cancelled():
                    return ("cancelled", page_num, "用户跳过")
                async with upload_sem:
                    if await _is_cancelled():
                        return ("cancelled", page_num, "用户跳过")
                    if task_id is not None:
                        await self.db.update_task(
                            task_id,
                            status="uploading",
                            current_action=f"上传 P{page_num}/{len(pages)}",
                            progress=round((page_num - 0.5) / len(pages) * 100, 1)
                        )
                    up_success, s3_key, up_error = await self.s3_uploader.upload_video(
                        video=video_obj,
                        local_path=file_path,
                        delete_after_upload=self.config.s3.delete_after_upload
                    )
                if not up_success:
                    logger.error(f"上传失败 {bvid} P{page_num}: {up_error}")
                    await self.db.set_upload_failed(
                        bvid, video_obj.page, True, video_obj.quality
                    )
                    await self.notification.notify_upload_failed(
                        bvid=bvid,
                        title=title,
                        error=up_error or "",
                        retry_count=self.config.s3.retry_times
                    )
                    return ("upload_failed", page_num, up_error)
            else:
                # 仅本地备份模式：标记为已完成，避免下次重复下载
                await self.db.update_video_s3_status(
                    bvid=video_obj.bvid,
                    page=video_obj.page,
                    s3_key=f"local://{file_path}",
                    quality=video_obj.quality
                )

            return ("ok", page_num, None)

        results = await asyncio.gather(
            *(_process_page(p) for p in pages),
            return_exceptions=True
        )

        total = len(pages)
        failed_pages = 0
        cancelled_pages = 0
        last_error: Optional[str] = None
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"处理分 P 异常 {bvid}: {r}")
                failed_pages += 1
                last_error = str(r)
                continue
            status_tag, _page_num, _err = r
            if status_tag == "cancelled" or status_tag == "size_skipped":
                cancelled_pages += 1
            elif status_tag != "ok":
                failed_pages += 1
                last_error = _err or last_error

        if cancelled_pages > 0:
            await _finish_task("skipped", "用户跳过")
            return "skipped"
        if failed_pages == total:
            await _finish_task("failed", "全部分 P 失败", last_error)
            return "failed"
        # 部分失败时仍计为 updated/new；具体失败状态保存在数据库里
        await _finish_task("completed", "完成", last_error if failed_pages else None)
        return "updated" if is_upgrade else "new"

    def get_sync_progress(self) -> Dict[str, Any]:
        """获取同步进度"""
        return {
            "is_syncing": self.is_syncing,
            **self.sync_progress
        }

    async def start_up_sync(self, manual: bool = False) -> bool:
        """
        开始同步 UP 主视频

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

            cookie_valid, cookie_msg = await self.api.validate_cookie()
            if not cookie_valid:
                logger.error(f"Cookie 无效：{cookie_msg}")
                await self.notification.notify_cookie_invalid(cookie_msg)
                return False

            targets = [t for t in self.config.up_sync.targets if t.enabled]
            if not targets:
                logger.info("没有启用的 UP 主同步目标")
                return True

            logger.info(f"开始同步 UP 主视频 (手动={manual})，共 {len(targets)} 个目标")

            sync_history = SyncHistory(
                id=None,
                fav_id="up_sync",
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

            self.is_syncing = True
            self.sync_progress = {
                "current": 0,
                "total": 0,
                "current_video": ""
            }

            total_new = 0
            total_skipped = 0
            total_failed = 0

            for target in targets:
                result = await self._sync_single_uploader(target.mid, target.name)
                total_new += result["new"]
                total_skipped += result["skipped"]
                total_failed += result["failed"]

            sync_history.total_videos = total_new + total_skipped + total_failed
            sync_history.new_videos = total_new
            sync_history.skipped_videos = total_skipped
            sync_history.failed_videos = total_failed
            sync_history.end_time = datetime.now().isoformat()
            sync_history.status = "completed" if total_failed == 0 else "failed"
            if sync_history.id is not None:
                await self.db.update_sync_history(sync_history)

            await self.notification.notify_sync_completed(
                total=sync_history.total_videos,
                new=total_new,
                updated=0,
                skipped=total_skipped,
                failed=total_failed
            )

            logger.info("UP 主视频同步完成")
            self.is_syncing = False
            return total_failed == 0

        except Exception as e:
            logger.error(f"UP 主同步异常：{e}")
            self.is_syncing = False
            return False

    async def _sync_single_uploader(self, mid: int, name: str) -> Dict[str, int]:
        """
        同步单个 UP 主的全部投稿视频

        Args:
            mid: UP 主 UID
            name: UP 主名称

        Returns:
            统计结果字典
        """
        logger.info(f"开始同步 UP 主：{name} (UID={mid})")

        success, videos_list, error = await self.api.get_all_uploader_videos(mid)
        if not success:
            logger.error(f"获取 UP 主 {name} 投稿列表失败：{error}")
            await self.notification.send_error(
                title="获取 UP 主投稿失败",
                message=f"无法获取 UP 主 {name}({mid}): {error}"
            )
            return {"new": 0, "skipped": 0, "failed": 0}

        logger.info(f"UP 主 {name} 共有 {len(videos_list)} 个投稿视频")
        if not videos_list:
            return {"new": 0, "skipped": 0, "failed": 0}

        now = datetime.now().isoformat()
        await self.db.clear_video_cache_by_source("uploader", mid)
        for video in videos_list:
            cache = VideoCache(
                id=None,
                bvid=video.get("bvid", ""),
                title=video.get("title", ""),
                cover=video.get("cover", ""),
                cover_local=None,
                duration=video.get("duration", 0),
                owner_name=video.get("owner", ""),
                source_type="uploader",
                source_id=mid,
                created_at=now,
                updated_at=now
            )
            await self.db.add_or_update_video_cache(cache)

        new_count = 0
        skipped_count = 0
        failed_count = 0

        self.sync_progress["total"] += len(videos_list)

        for idx, video_info in enumerate(videos_list):
            bvid = video_info["bvid"]
            title = video_info["title"]

            self.sync_progress["current"] += 1
            self.sync_progress["current_video"] = title

            logger.info(
                f"[UP主 {name}] [{idx + 1}/{len(videos_list)}] 处理视频：{title} ({bvid})"
            )

            if await self.db.is_blacklisted(bvid):
                logger.info(f"视频 {bvid} 在黑名单中，跳过")
                skipped_count += 1
                continue

            best_quality = await self.db.get_best_uploaded_quality(bvid)
            if best_quality is not None:
                logger.info(f"视频 {bvid} 已备份（清晰度 {best_quality}），跳过")
                skipped_count += 1
                continue

            task_id = await self.db.enqueue_task(
                bvid=bvid,
                title=title,
                fav_id=None,
                fav_title=name,
                source="up_sync"
            )

            try:
                result = await self._process_single_video(
                    bvid=bvid,
                    title=title,
                    video_info=video_info,
                    fav_id=None,
                    fav_title=None,
                    fav_time=None,
                    task_id=task_id,
                    up_mid=mid,
                    up_name=name
                )

                if result == "new" or result == "updated":
                    new_count += 1
                elif result == "skipped":
                    skipped_count += 1
                elif result == "failed":
                    failed_count += 1

            except Exception as e:
                logger.error(f"处理 UP 主视频 {bvid} 异常：{e}")
                failed_count += 1
                await self.db.update_task(
                    task_id,
                    status="failed",
                    error_message=str(e)[:500],
                    current_action="异常退出",
                    finished_at=datetime.now().isoformat()
                )
                continue

        return {"new": new_count, "skipped": skipped_count, "failed": failed_count}

    async def get_sync_status(self) -> Dict[str, Any]:
        """获取同步状态摘要"""
        if not self.db:
            await self.init_db()

        # 获取最近的同步历史
        recent_history = await self.db.get_recent_sync_history(limit=1)

        # 获取统计信息
        all_videos = await self.db.get_all_videos()
        progress_map = {
            (p.bvid, p.page): p
            for p in await self.db.get_all_progress()
        }
        uploaded_count = sum(
            1 for v in all_videos
            if _matches_archive_status(v, "uploaded", progress_map.get((v.bvid, v.page)))
        )
        pending_count = sum(
            1 for v in all_videos
            if _matches_archive_status(v, "pending", progress_map.get((v.bvid, v.page)))
        )
        downloaded_count = sum(
            1 for v in all_videos
            if _matches_archive_status(v, "downloaded", progress_map.get((v.bvid, v.page)))
        )
        failed_count = sum(
            1 for v in all_videos
            if _matches_archive_status(v, "failed", progress_map.get((v.bvid, v.page)))
        )

        return {
            "is_syncing": self.is_syncing,
            "total_videos": len(all_videos),
            "downloaded_videos": downloaded_count,
            "uploaded_videos": uploaded_count,
            "pending_videos": pending_count,
            "failed_videos": failed_count,
            "last_sync": recent_history[0].to_dict() if recent_history else None
        }
