"""
视频下载模块
负责调用 yt-dlp 下载视频，支持进度跟踪、重试机制和断点续传
"""
import asyncio
import os
import shutil
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, Callable, Dict, Any, List
from loguru import logger

from core.config import get_config
from core.database import Database, Video, DownloadProgress
from services.bilibili_api import BilibiliAPI


class DownloadState:
    """下载状态持久化（用于断点续传）"""

    def __init__(self, temp_dir: str):
        self.temp_dir = Path(temp_dir)
        self.state_file = self.temp_dir / ".download_state.json"

    def save_state(self, bvid: str, page: int, state: Dict[str, Any]) -> None:
        """保存下载状态"""
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        states = self._load_all_states()
        states[f"{bvid}_P{page}"] = {
            **state,
            "updated_at": datetime.now().isoformat()
        }
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(states, f, ensure_ascii=False, indent=2)

    def load_state(self, bvid: str, page: int) -> Optional[Dict[str, Any]]:
        """加载下载状态"""
        states = self._load_all_states()
        return states.get(f"{bvid}_P{page}")

    def clear_state(self, bvid: str, page: int) -> None:
        """清除下载状态"""
        states = self._load_all_states()
        key = f"{bvid}_P{page}"
        if key in states:
            del states[key]
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(states, f, ensure_ascii=False, indent=2)

    def _load_all_states(self) -> Dict[str, Any]:
        """加载所有下载状态"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError, OSError) as e:
                logger.warning(f"加载下载状态文件失败: {e}")
                return {}
        return {}

    def get_pending_downloads(self) -> List[Dict[str, Any]]:
        """获取所有待续传的下载"""
        states = self._load_all_states()
        pending = []
        for key, state in states.items():
            if state.get("status") == "partial":
                pending.append(state)
        return pending


class Downloader:
    """视频下载器"""

    def __init__(self):
        self.config = get_config()
        self.api = BilibiliAPI()
        self.db: Optional[Database] = None
        self.retry_times = self.config.download.retry_times
        self.temp_dir = self.config.download.temp_dir
        self.quality = self.config.download.quality

        # 进度回调函数
        self.progress_callback: Optional[Callable[[DownloadProgress], None]] = None

        # 断点续传状态管理
        self.download_state = DownloadState(self.temp_dir)

        self._last_progress_update: Dict[Tuple[str, int], Tuple[float, float, str]] = {}

    async def resume_pending_downloads(self) -> List[Tuple[str, int, bool, Optional[str], str]]:
        """
        恢复所有待续传的下载任务

        Returns:
            恢复结果列表 [(bvid, page, success, file_path, error)]
        """
        pending = self.download_state.get_pending_downloads()
        if not pending:
            logger.info("没有待续传的下载任务")
            return []

        logger.info(f"发现 {len(pending)} 个待续传的下载任务")
        results = []

        for state in pending:
            bvid = state.get("bvid")
            page = state.get("page")

            if not bvid or not page:
                continue

            logger.info(f"尝试恢复下载：{bvid} P{page}")

            try:
                # 检查是否有部分文件
                partial_file = state.get("partial_file")
                if partial_file and Path(partial_file).exists():
                    file_size = Path(partial_file).stat().st_size
                    logger.info(f"发现部分下载文件：{partial_file} ({file_size / 1024 / 1024:.1f}MB)")

                    # 如果文件大小足够大，尝试继续下载
                    # 注意：yt-dlp 不支持真正的断点续传，但我们可以检查文件是否已完整
                    if self._validate_partial_file(partial_file):
                        # 文件已完整，直接使用
                        logger.info(f"部分文件已完整，直接使用：{partial_file}")
                        self.download_state.clear_state(bvid, page)
                        results.append((bvid, page, True, partial_file, ""))
                        continue

                # 文件不完整，重新下载
                cid = state.get("cid")
                title = state.get("title", f"{bvid}_P{page}")
                target_quality = state.get("target_quality", self.quality)

                if cid:
                    success, file_path, error = await self.download_single_video(
                        bvid=bvid,
                        cid=cid,
                        title=title,
                        page=page,
                        target_quality=target_quality,
                        resume=False  # 不再尝试断点续传，直接重新下载
                    )
                    results.append((bvid, page, success, file_path, error))
                else:
                    # 缺少必要信息，清除状态
                    logger.warning(f"缺少下载信息，清除状态：{bvid} P{page}")
                    self.download_state.clear_state(bvid, page)
                    results.append((bvid, page, False, None, "缺少下载信息"))

            except Exception as e:
                logger.error(f"恢复下载失败 {bvid} P{page}: {e}")
                results.append((bvid, page, False, None, str(e)))

        # 统计恢复结果
        success_count = sum(1 for _, _, success, _, _ in results if success)
        logger.info(f"恢复下载完成：成功 {success_count}/{len(results)}")

        return results

    def _validate_partial_file(self, file_path: str) -> bool:
        """
        验证部分下载的文件是否完整

        Args:
            file_path: 文件路径

        Returns:
            是否完整
        """
        try:
            # 使用 ffprobe 检查文件是否可读取且有音视频轨道
            import subprocess

            ffprobe = shutil.which("ffprobe") or "ffprobe"
            cmd = [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode == 0 and result.stdout.strip():
                duration = float(result.stdout.strip())
                # 如果能读取时长且大于0，认为文件完整
                return duration > 0

        except Exception as e:
            logger.debug(f"验证文件失败: {e}")

        return False

    async def init_db(self) -> None:
        """初始化数据库连接"""
        self.db = await Database.get_instance()

    def set_progress_callback(
        self, callback: Callable[[DownloadProgress], None]
    ) -> None:
        """设置进度回调函数"""
        self.progress_callback = callback

    async def _update_progress(
        self,
        bvid: str,
        title: str,
        page: int,
        progress: float,
        speed: Optional[float] = None,
        eta: Optional[int] = None,
        status: str = "downloading",
        message: Optional[str] = None
    ) -> None:
        """更新下载进度"""
        key = (bvid, page)
        now_ts = time.monotonic()
        last = self._last_progress_update.get(key)
        should_write = (
            status in {"pending", "completed", "failed"}
            or progress <= 0
            or progress >= 100
            or last is None
            or status != last[2]
            or now_ts - last[0] >= 1.0
            or abs(progress - last[1]) >= 5.0
        )
        if not should_write:
            if self.progress_callback:
                self.progress_callback(DownloadProgress(
                    id=None,
                    bvid=bvid,
                    title=title,
                    page=page,
                    progress=progress,
                    speed=speed,
                    eta=eta,
                    status=status,
                    message=message,
                    created_at="",
                    updated_at=datetime.now().isoformat()
                ))
            return
        self._last_progress_update[key] = (now_ts, progress, status)

        if not self.db:
            await self.init_db()

        now = datetime.now().isoformat()

        progress_obj = DownloadProgress(
            id=None,
            bvid=bvid,
            title=title,
            page=page,
            progress=progress,
            speed=speed,
            eta=eta,
            status=status,
            message=message,
            created_at=now,
            updated_at=now
        )

        await self.db.update_download_progress(progress_obj)

        # 调用回调函数
        if self.progress_callback:
            self.progress_callback(progress_obj)

    async def download_single_video(
        self,
        bvid: str,
        cid: str,
        title: str,
        page: int = 1,
        target_quality: int = 127,
        resume: bool = True
    ) -> Tuple[bool, Optional[str], str]:
        """
        下载单个视频（单 P），支持断点续传

        Args:
            bvid: 视频 BV 号
            cid: 分 P ID
            title: 视频标题
            page: 分 P 序号
            target_quality: 目标清晰度
            resume: 是否尝试断点续传

        Returns:
            (success, file_path, error_message)
        """
        if not self.db:
            await self.init_db()

        # 创建临时目录（按视频分类）
        video_temp_dir = os.path.join(self.temp_dir, bvid)
        Path(video_temp_dir).mkdir(parents=True, exist_ok=True)

        # 检查是否有断点续传
        partial_file = None
        if resume:
            state = self.download_state.load_state(bvid, page)
            if state and state.get("status") == "partial":
                partial_file = state.get("partial_file")
                if partial_file and Path(partial_file).exists():
                    logger.info(f"发现断点续传文件：{partial_file}")
                    # 检查文件大小是否合理
                    file_size = Path(partial_file).stat().st_size
                    if file_size > 1024 * 1024:  # 至少 1MB
                        logger.info(f"从断点继续下载，已有 {file_size / 1024 / 1024:.1f}MB")
                    else:
                        partial_file = None

        # 初始化进度
        await self._update_progress(
            bvid=bvid,
            title=title,
            page=page,
            progress=0,
            status="pending",
            message="准备下载..."
        )

        retry_count = 0
        last_error = ""

        while retry_count <= self.retry_times:
            try:
                logger.info(
                    f"下载视频：{bvid} P{page}, 清晰度：{target_quality}, "
                    f"尝试 {retry_count + 1}/{self.retry_times + 1}"
                )

                # 更新状态为下载中
                await self._update_progress(
                    bvid=bvid,
                    title=title,
                    page=page,
                    progress=10,
                    status="downloading",
                    message=f"正在下载 (尝试 {retry_count + 1}/{self.retry_times + 1})..."
                )

                # 保存下载状态（用于断点续传）
                self.download_state.save_state(bvid, page, {
                    "bvid": bvid,
                    "cid": cid,
                    "title": title,
                    "page": page,
                    "target_quality": target_quality,
                    "temp_dir": video_temp_dir,
                    "status": "partial",
                    "partial_file": None
                })

                # 调用下载
                success, file_path, error = await self.api.download_video(
                    bvid=bvid,
                    cid=cid,
                    output_dir=video_temp_dir,
                    quality=target_quality,
                    page=page
                )

                if success and file_path:
                    # 下载成功，清除断点状态
                    self.download_state.clear_state(bvid, page)

                    await self._update_progress(
                        bvid=bvid,
                        title=title,
                        page=page,
                        progress=100,
                        status="completed",
                        message=f"下载完成：{file_path}"
                    )

                    logger.info(f"视频下载成功：{file_path}")
                    return True, file_path, ""
                else:
                    last_error = error or "下载失败"
                    logger.warning(f"下载失败：{last_error}")

                    # 保存部分下载状态
                    self.download_state.save_state(bvid, page, {
                        "bvid": bvid,
                        "cid": cid,
                        "title": title,
                        "page": page,
                        "target_quality": target_quality,
                        "temp_dir": video_temp_dir,
                        "status": "partial",
                        "partial_file": None,
                        "error": last_error
                    })

            except Exception as e:
                last_error = str(e)
                logger.error(f"下载异常：{e}")

                # 保存异常状态
                self.download_state.save_state(bvid, page, {
                    "bvid": bvid,
                    "cid": cid,
                    "title": title,
                    "page": page,
                    "target_quality": target_quality,
                    "temp_dir": video_temp_dir,
                    "status": "partial",
                    "error": last_error
                })

            # 重试前等待
            retry_count += 1
            if retry_count <= self.retry_times:
                wait_time = min(2 ** retry_count, 30)  # 指数退避，最多 30 秒
                logger.info(f"{wait_time}秒后重试...")
                await self._update_progress(
                    bvid=bvid,
                    title=title,
                    page=page,
                    progress=0,
                    status="pending",
                    message=f"下载失败，{wait_time}秒后重试..."
                )
                await asyncio.sleep(wait_time)

        # 所有重试都失败
        await self._update_progress(
            bvid=bvid,
            title=title,
            page=page,
            progress=0,
            status="failed",
            message=f"下载失败：{last_error}"
        )

        logger.error(f"视频 {bvid} P{page} 下载失败，已重试 {self.retry_times} 次")
        return False, None, last_error

    async def download_multi_page_video(
        self,
        bvid: str,
        title: str,
        pages: list,
        target_quality: int = 127,
        max_parallel: Optional[int] = None
    ) -> list:
        """
        下载多 P 视频的所有分 P（支持并发下载）

        Args:
            bvid: 视频 BV 号
            title: 视频标题
            pages: 分 P 列表 [{"cid": "...", "page": 1, "title": "..."}]
            target_quality: 目标清晰度
            max_parallel: 最大并发数，None 时使用配置值

        Returns:
            下载结果列表 [(page, success, file_path, error)]
        """
        # 获取最大并发数
        if max_parallel is None:
            max_parallel = self.config.download.max_parallel

        # 确保并发数至少为 1
        max_parallel = max(1, max_parallel)

        logger.info(f"开始下载多 P 视频：{bvid}, 共 {len(pages)} P, 并发数: {max_parallel}")

        # 创建信号量控制并发
        semaphore = asyncio.Semaphore(max_parallel)

        # 下载任务结果存储
        results: Dict[int, Tuple[bool, Optional[str], str]] = {}

        async def download_single_page(page_info: dict) -> Tuple[int, bool, Optional[str], str]:
            """下载单个分 P（带信号量控制）"""
            async with semaphore:
                cid = page_info["cid"]
                page_num = page_info["page"]
                page_title = page_info.get("title", f"P{page_num}")

                # 组合完整标题
                full_title = f"{title}_{page_title}"

                # 添加请求延迟（用于风控）
                if self.config.download.request_delay > 0:
                    await asyncio.sleep(self.config.download.request_delay)

                success, file_path, error = await self.download_single_video(
                    bvid=bvid,
                    cid=cid,
                    title=full_title,
                    page=page_num,
                    target_quality=target_quality
                )

                if not success:
                    logger.warning(f"视频 {bvid} P{page_num} 下载失败: {error}")

                return page_num, success, file_path, error

        # 创建所有下载任务
        tasks = [download_single_page(page_info) for page_info in pages]

        # 并发执行所有任务
        completed_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理结果
        for result in completed_results:
            if isinstance(result, Exception):
                logger.error(f"下载任务异常: {result}")
                # 无法确定是哪个 page 失败，记录错误
                continue
            page_num, success, file_path, error = result
            results[page_num] = (success, file_path, error)

        # 按 page 顺序返回结果
        ordered_results = []
        for page_info in pages:
            page_num = page_info["page"]
            if page_num in results:
                success, file_path, error = results[page_num]
                ordered_results.append((page_num, success, file_path, error))
            else:
                # 任务异常的情况
                ordered_results.append((page_num, False, None, "任务执行异常"))

        # 统计结果
        success_count = sum(1 for _, success, _, _ in ordered_results if success)
        logger.info(f"多 P 视频下载完成: {bvid}, 成功 {success_count}/{len(pages)} P")

        return ordered_results

    async def download_and_prepare_video(
        self,
        bvid: str,
        cid: str,
        title: str,
        page: int = 1,
        target_quality: int = 127
    ) -> Tuple[bool, Optional[Video], str]:
        """
        下载视频并准备 Video 对象

        Args:
            bvid: 视频 BV 号
            cid: 分 P ID
            title: 视频标题
            page: 分 P 序号
            target_quality: 目标清晰度

        Returns:
            (success, video_obj, error_message)
        """
        if not self.db:
            await self.init_db()

        # 下载视频
        success, file_path, error = await self.download_single_video(
            bvid=bvid,
            cid=cid,
            title=title,
            page=page,
            target_quality=target_quality
        )

        if not success or not file_path:
            return False, None, error

        # 获取视频时长等信息
        try:
            # 使用 ffprobe 或文件信息获取时长
            duration = await self._get_video_duration(file_path)
        except Exception as e:
            logger.warning(f"无法获取视频时长：{e}")
            duration = 0

        # 创建 Video 对象
        now = datetime.now().isoformat()
        video = Video(
            id=None,
            bvid=bvid,
            title=title,
            cid=cid,
            page=page,
            total_pages=1,  # 单 P 下载
            quality=target_quality,
            duration=duration,
            pubdate=int(datetime.now().timestamp()),
            owner_name="",
            s3_key=None,
            s3_uploaded=False,
            s3_quality=None,
            local_path=file_path,
            created_at=now,
            updated_at=now
        )

        # 保存到数据库
        await self.db.add_video(video)

        return True, video, ""

    async def _get_video_duration(self, file_path: str) -> int:
        """
        获取视频时长（秒）

        尝试使用 ffprobe，如果不可用则返回 0
        """
        try:
            import subprocess

            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                duration = float(result.stdout.strip())
                return int(duration)
        except Exception as e:
            logger.debug(f"ffprobe 不可用或执行失败：{e}")

        return 0

    def cleanup_temp(self, keep_files: bool = False) -> None:
        """
        清理临时目录

        Args:
            keep_files: 是否保留文件（调试用）
        """
        if keep_files or self.config.debug.keep_temp_files:
            logger.info("保留临时文件（调试模式）")
            return

        try:
            temp_path = Path(self.temp_dir)
            if temp_path.exists():
                # 删除目录下所有内容
                for item in temp_path.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)

                logger.info(f"已清理临时目录：{self.temp_dir}")
        except Exception as e:
            logger.error(f"清理临时目录失败：{e}")

    def cleanup_video_temp(self, bvid: str) -> None:
        """
        清理指定视频的临时文件

        Args:
            bvid: 视频 BV 号
        """
        if self.config.debug.keep_temp_files:
            return

        try:
            video_temp_dir = Path(self.temp_dir) / bvid
            if video_temp_dir.exists():
                shutil.rmtree(video_temp_dir)
                logger.info(f"已清理视频临时文件：{bvid}")
        except Exception as e:
            logger.error(f"清理临时文件失败：{e}")
