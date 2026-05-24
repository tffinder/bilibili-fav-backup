"""
Bilibili 视频 API

提供视频信息获取、视频下载等功能
"""
import asyncio
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

from bilibili_api.video import Video
from loguru import logger

from core.config import get_config
from core.db.models import Video as DbVideo
from services.bilibili.client import BilibiliClient


class VideoAPI(BilibiliClient):
    """Bilibili 视频 API"""

    def __init__(self):
        super().__init__()
        self.config = get_config()

    async def get_video_info(self, bvid: str) -> Tuple[bool, Dict[str, Any], str]:
        """
        获取视频信息

        Args:
            bvid: 视频 BV 号

        Returns:
            (success, video_info, error_message)
        """
        credential = self.build_credential()
        if not credential:
            return False, {}, "Cookie 未配置或格式错误"

        await self.rate_limit()
        try:
            video_obj = Video(bvid=bvid, credential=credential)
            info = await video_obj.get_info()
            owner = info.get("owner") or {}
            rights = info.get("rights") or {}
            is_interactive = bool(
                rights.get("is_stein_gate")
                or info.get("is_stein_gate")
                or info.get("interaction")
            )
            return True, {
                "bvid": bvid,
                "title": info.get("title") or bvid,
                "pubdate": info.get("pubdate") or 0,
                "owner_name": owner.get("name") or "",
                "desc": info.get("desc") or "",
                "cover_url": info.get("pic") or info.get("cover") or info.get("thumbnail") or "",
                "is_interactive": is_interactive
            }, ""
        except Exception as e:
            logger.error(f"获取视频信息失败：{e}")
            return False, {}, f"获取视频信息失败：{str(e)}"

    async def get_video_pages(self, bvid: str) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        获取视频分 P 信息

        Args:
            bvid: 视频 BV 号

        Returns:
            (success, pages, error_message)
        """
        credential = self.build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self.rate_limit()
        try:
            v = Video(bvid=bvid, credential=credential)
            pages_data = await v.get_pages()
            pages: List[Dict[str, Any]] = []
            for p in pages_data:
                pages.append({
                    "cid": str(p.get("cid", "")),
                    "page": p.get("page", 1),
                    "title": p.get("part", f"P{p.get('page', 1)}"),
                    "duration": p.get("duration", 0),
                    "dimension": p.get("dimension", {})
                })
            return True, pages, ""
        except Exception as e:
            logger.error(f"获取视频分 P 信息失败：{e}")
            return False, [], f"获取视频分 P 信息失败：{str(e)}"

    async def get_video_quality(self, bvid: str, cid: str) -> Tuple[bool, List[int], str]:
        """
        获取视频可用清晰度

        Args:
            bvid: 视频 BV 号
            cid: 分 P ID

        Returns:
            (success, qualities, error_message)
        """
        credential = self.build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self.rate_limit()
        try:
            v = Video(bvid=bvid, credential=credential)
            url_data = await v.get_download_url(cid=int(cid))
            qualities = url_data.get("accept_quality", []) or []
            return True, qualities, ""
        except Exception as e:
            logger.error(f"获取视频清晰度失败：{e}")
            return False, [], f"获取视频清晰度失败：{str(e)}"

    @staticmethod
    def extract_bvid(video_input: str) -> Optional[str]:
        """
        从输入字符串中提取 BV 号

        Args:
            video_input: BV 号或视频链接

        Returns:
            BV 号或 None
        """
        if not video_input:
            return None
        value = video_input.strip()
        match = re.search(r"(BV[0-9A-Za-z]{10})", value)
        if match:
            return match.group(1)
        return None

    async def get_video_detail(self, bvid: str, title: str = "") -> Optional[DbVideo]:
        """
        获取视频详情，返回 DbVideo 对象

        Args:
            bvid: 视频 BV 号
            title: 视频标题（可选）

        Returns:
            DbVideo 对象或 None
        """
        try:
            success, pages, error = await self.get_video_pages(bvid)
            if not success or not pages:
                logger.warning(f"获取视频 {bvid} 分 P 信息失败：{error}")
                return None

            first_page = pages[0]
            success, qualities, _ = await self.get_video_quality(bvid, first_page["cid"])
            max_quality = qualities[0] if success and qualities else 16

            return DbVideo(
                id=None,
                bvid=bvid,
                title=title or f"视频_{bvid}",
                cid=first_page["cid"],
                page=first_page["page"],
                total_pages=len(pages),
                quality=max_quality,
                duration=first_page["duration"],
                pubdate=int(datetime.now().timestamp()),
                owner_name="unknown",
                s3_key=None,
                s3_uploaded=False,
                s3_quality=None,
                local_path=None,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat()
            )
        except Exception as e:
            logger.error(f"获取视频详情失败：{e}")
            return None

    # ==================== 下载相关方法 ====================

    @staticmethod
    def _quality_to_height(quality: int) -> int:
        """清晰度代码转高度"""
        mapping = {
            127: 4320, 126: 2160, 125: 2160, 120: 2160,
            116: 1080, 112: 1080, 80: 1080, 74: 720, 64: 720, 48: 720,
            32: 480, 16: 360
        }
        return mapping.get(quality, 1080)

    @staticmethod
    def _quality_to_format(quality: int) -> str:
        """根据清晰度代码返回 yt-dlp 格式选择字符串"""
        height_map = {
            127: 4320, 126: 2160, 125: 2160, 120: 2160,
            116: 1080, 112: 1080, 80: 1080, 74: 720, 64: 720, 48: 720,
            32: 480, 16: 360
        }
        target_height = height_map.get(quality, 1080)

        # 对于 4K 及以上，使用更宽松的格式选择
        if quality >= 120:
            return f"bv*[height<={target_height}]+ba/b[height<={target_height}]/bv+ba/b"
        else:
            return f"bv*[height<={target_height}]+ba/b[height<={target_height}]/b"

    async def download_video(
        self,
        bvid: str,
        cid: str,
        output_dir: str,
        quality: int = 127,
        page: int = 1
    ) -> Tuple[bool, str, str]:
        """
        下载视频

        Args:
            bvid: 视频 BV 号
            cid: 分 P ID
            output_dir: 输出目录
            quality: 目标清晰度
            page: 分 P 序号

        Returns:
            (success, file_path, error_message)
        """
        if not self.cookie:
            return False, "", "Cookie 未配置"

        # 检查并配置 ffmpeg
        ffmpeg_path = self.config.debug.ffmpeg_path
        if ffmpeg_path:
            ffmpeg_dir = str(Path(ffmpeg_path).parent)
            if ffmpeg_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
            logger.info(f"使用 ffmpeg 路径：{ffmpeg_path}")
            ffmpeg_exe = ffmpeg_path
        else:
            ffmpeg_exe = shutil.which("ffmpeg")
            if not ffmpeg_exe:
                return False, "", "未找到 ffmpeg，无法合并音视频，请先安装或在设置中配置 ffmpeg 路径"

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # 创建临时下载目录
        temp_download_dir = f"{output_dir}_temp"
        Path(temp_download_dir).mkdir(parents=True, exist_ok=True)

        # 获取格式选择字符串
        format_selector = self._quality_to_format(quality)
        video_url = f"https://www.bilibili.com/video/{bvid}?p={page}"

        # yt-dlp 参数
        args = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--add-header", f"Cookie:{self.cookie}",
            "--format", format_selector,
            "--merge-output-format", "mp4",
            "--output", str(Path(temp_download_dir) / "%(title)s.%(ext)s"),
            "--keep-video",
            video_url
        ]

        if ffmpeg_path:
            args.extend(["--ffmpeg-location", str(Path(ffmpeg_path).parent)])

        if self.config.debug.biliup_proxy:
            args.extend(["--proxy", self.config.debug.biliup_proxy])

        max_size_gib = self.config.skip_rules.max_video_size_gib
        if max_size_gib > 0:
            max_size_bytes = int(max_size_gib * 1024 * 1024 * 1024)
            args.extend(["--max-filesize", str(max_size_bytes)])

        logger.info(f"yt-dlp 命令：{' '.join(args)}")
        success, stdout, stderr = await self._run_command(args)

        if not success:
            combined = (stdout or "") + (stderr or "")
            if "File is larger than max-filesize" in combined:
                self._cleanup_temp_dir(temp_download_dir)
                return False, "", f"文件大小超过限制 ({max_size_gib} GiB)"
            logger.error(f"yt-dlp 下载失败：{stderr}")
            # 尝试备用格式
            logger.info("尝试备用格式下载...")
            backup_args = self._replace_format_selector(args, "bestvideo+bestaudio/best")
            success, stdout, stderr = await self._run_command(backup_args)
            if not success:
                self._cleanup_temp_dir(temp_download_dir)
                return False, "", stderr or "下载失败"

        # 查找下载的文件
        merged_file = self._find_latest_media(temp_download_dir, extensions=[".mp4", ".mkv", ".flv"])
        video_file = self._find_latest_media(temp_download_dir, extensions=[".f*.mp4", ".f*.m4s", ".video.*"])
        audio_file = self._find_latest_media(temp_download_dir, extensions=[".f*.m4a", ".f*.opus", ".audio.*"])

        final_file = None

        if merged_file and await self._check_audio_track(merged_file):
            final_file = merged_file
            logger.info(f"yt-dlp 已自动合并：{merged_file}")
        elif video_file and audio_file:
            # 手动合并
            logger.info(f"手动合并视频和音频：{video_file} + {audio_file}")
            video_name = Path(video_file).stem
            if ".f" in video_name:
                video_name = video_name.split(".f")[0]
            final_file = str(Path(output_dir) / f"{video_name}.mp4")

            merge_success, merge_error = await self._merge_video_audio(
                video_file, audio_file, final_file, ffmpeg_exe
            )
            if not merge_success:
                self._cleanup_temp_dir(temp_download_dir)
                return False, "", f"合并失败：{merge_error}"
        elif merged_file:
            final_file = merged_file
        else:
            self._cleanup_temp_dir(temp_download_dir)
            return False, "", "下载完成但未找到视频文件"

        # 移动最终文件到输出目录
        if final_file and Path(final_file).parent != Path(output_dir):
            import shutil as sh
            dest_file = str(Path(output_dir) / Path(final_file).name)
            sh.move(final_file, dest_file)
            final_file = dest_file

        # 清理临时目录
        self._cleanup_temp_dir(temp_download_dir)

        # 验证最终文件
        if final_file and Path(final_file).exists():
            has_audio = await self._check_audio_track(final_file)
            if not has_audio:
                logger.warning(f"视频文件没有音频轨道：{final_file}")
            return True, final_file, ""

        return False, "", "下载完成但未找到文件"

    async def _run_command(self, args: List[str]) -> Tuple[bool, str, str]:
        """运行命令"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._run_command_sync, args)

    def _run_command_sync(self, args: List[str]) -> Tuple[bool, str, str]:
        """在线程中运行阻塞命令"""
        try:
            logger.debug(f"运行命令：{' '.join(args)}")
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=1800,
                encoding="utf-8",
                errors="ignore"
            )
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", "命令执行超时"
        except Exception as e:
            return False, "", str(e)

    @staticmethod
    def _replace_format_selector(args: List[str], selector: str) -> List[str]:
        """替换 yt-dlp 的 --format 参数值。"""
        updated_args = args.copy()
        try:
            format_index = updated_args.index("--format")
            updated_args[format_index + 1] = selector
        except (ValueError, IndexError):
            updated_args.extend(["--format", selector])
        return updated_args

    async def _merge_video_audio(
        self, video_file: str, audio_file: str, output_file: str, ffmpeg_exe: str
    ) -> Tuple[bool, str]:
        """使用 ffmpeg 合并视频和音频"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._merge_video_audio_sync,
            video_file,
            audio_file,
            output_file,
            ffmpeg_exe,
        )

    def _merge_video_audio_sync(
        self, video_file: str, audio_file: str, output_file: str, ffmpeg_exe: str
    ) -> Tuple[bool, str]:
        """在线程中使用 ffmpeg 合并视频和音频"""
        def _run(audio_codec: str) -> subprocess.CompletedProcess:
            cmd = [
                ffmpeg_exe,
                "-i", video_file,
                "-i", audio_file,
                "-c:v", "copy",
                "-c:a", audio_codec,
                "-y",
                output_file,
            ]
            logger.info(f"ffmpeg 合并命令：{' '.join(cmd)}")
            return subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

        try:
            # B 站音频通常是 AAC m4a，直接 copy 进 MP4 容器即可，秒级完成
            result = _run("copy")

            if result.returncode != 0:
                # 极少数 opus / 不兼容情况回退到 AAC 重编码
                logger.warning(
                    f"ffmpeg copy 合并失败，回退到 AAC 重编码：{result.stderr.strip()[:200]}"
                )
                result = _run("aac")

            if result.returncode != 0:
                logger.error(f"ffmpeg 合并失败：{result.stderr}")
                return False, result.stderr

            logger.info(f"合并成功：{output_file}")
            return True, ""
        except subprocess.TimeoutExpired:
            return False, "合并超时（已放宽至 30 分钟，仍超时请检查 ffmpeg 或磁盘）"
        except Exception as e:
            return False, str(e)

    def _cleanup_temp_dir(self, temp_dir: str) -> None:
        """清理临时目录"""
        import shutil as sh
        try:
            if Path(temp_dir).exists():
                sh.rmtree(temp_dir)
                logger.info(f"已清理临时目录：{temp_dir}")
        except Exception as e:
            logger.warning(f"清理临时目录失败：{e}")

    async def _check_audio_track(self, file_path: str) -> bool:
        """检查视频文件是否有音频轨道"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._check_audio_track_sync, file_path)

    def _check_audio_track_sync(self, file_path: str) -> bool:
        """在线程中检查视频文件是否有音频轨道"""
        try:
            ffmpeg_path = self.config.debug.ffmpeg_path
            if ffmpeg_path:
                ffprobe_path = str(Path(ffmpeg_path).parent / "ffprobe.exe")
            else:
                ffprobe_path = "ffprobe"

            cmd = [
                ffprobe_path,
                "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                file_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return bool(result.stdout.strip())
        except Exception as e:
            logger.debug(f"检查音频轨道失败：{e}")
            return True

    def _find_latest_media(self, directory: str, extensions: list = None) -> Optional[str]:
        """查找最新的媒体文件"""
        if extensions is None:
            extensions = [".mp4", ".mkv", ".flv"]

        dir_path = Path(directory)
        if not dir_path.exists():
            return None

        media_files = []
        for ext in extensions:
            media_files.extend(dir_path.glob(f"*{ext}"))
            if "*" in ext:
                media_files.extend(dir_path.glob(ext))

        if not media_files:
            return None

        # 排除临时文件
        media_files = [f for f in media_files if not f.name.startswith('.')]

        if not media_files:
            return None

        latest = max(media_files, key=lambda p: p.stat().st_mtime)
        return str(latest)

    async def download_cover(
        self, url: str, save_path: str
    ) -> Tuple[bool, str]:
        """
        下载封面图片

        Args:
            url: 封面图片 URL
            save_path: 本地保存路径

        Returns:
            (success, error_message)
        """
        try:
            import aiohttp

            headers = {
                "Referer": "https://www.bilibili.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            timeout = aiohttp.ClientTimeout(total=8, connect=3, sock_connect=3, sock_read=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                        with open(save_path, 'wb') as f:
                            f.write(content)
                        return True, ""
                    return False, f"HTTP {resp.status}"

        except Exception as e:
            logger.error(f"下载封面失败：{e}")
            return False, str(e)
