"""
Rclone 上传模块
负责将视频通过 rclone 上传到各种网盘（WebDAV、Google Drive、OneDrive 等）
"""
import asyncio
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from loguru import logger

from core.config import get_config
from core.database import Database, Video
from services.s3_uploader import S3Uploader


class RcloneUploader:
    """Rclone 上传器"""

    def __init__(self):
        self.config = get_config()
        self.db: Optional[Database] = None
        self._conf_path = Path(self.config.base_dir) / "data" / "rclone.conf"

    async def init_db(self) -> None:
        self.db = await Database.get_instance()

    def _write_rclone_conf(self) -> None:
        """根据当前配置生成 rclone.conf"""
        rc = self.config.rclone
        if not rc.remote_name or not rc.remote_type:
            return

        lines = [f"[{rc.remote_name}]", f"type = {rc.remote_type}"]

        if rc.remote_type == "webdav":
            if rc.host:
                lines.append(f"url = {rc.host}")
            if rc.vendor:
                lines.append(f"vendor = {rc.vendor}")
            if rc.user:
                lines.append(f"user = {rc.user}")
            if rc.password:
                lines.append(f"pass = {rc.password}")
        elif rc.remote_type in ("sftp", "ftp"):
            if rc.host:
                lines.append(f"host = {rc.host}")
            if rc.user:
                lines.append(f"user = {rc.user}")
            if rc.password:
                lines.append(f"pass = {rc.password}")
        elif rc.remote_type in ("drive", "onedrive", "dropbox"):
            if rc.token:
                lines.append(f"token = {rc.token}")
        elif rc.remote_type == "s3":
            if rc.host:
                lines.append(f"endpoint = {rc.host}")
            if rc.user:
                lines.append(f"access_key_id = {rc.user}")
            if rc.password:
                lines.append(f"secret_access_key = {rc.password}")

        self._conf_path.parent.mkdir(parents=True, exist_ok=True)
        self._conf_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info(f"已生成 rclone 配置：{self._conf_path}")

    def _build_remote_dest(self, sub_path: str) -> str:
        """构建 rclone 远端路径 remote:base_path/sub_path"""
        rc = self.config.rclone
        base = rc.remote_path.rstrip("/")
        if base:
            return f"{rc.remote_name}:{base}/{sub_path}"
        return f"{rc.remote_name}:{sub_path}"

    def generate_remote_key(self, video: Video) -> str:
        """生成远端存储子路径（与 S3Uploader.generate_s3_key 逻辑一致）"""
        safe_title = S3Uploader.normalize_filename(video.title, max_length=100)
        if safe_title.endswith('.mp4'):
            safe_title = safe_title[:-4]

        folder_name = video.fav_title or video.up_name or "未分类"
        folder_name = re.sub(r'[:*?"<>|]', '_', folder_name).strip()
        if len(folder_name) > 50:
            folder_name = folder_name[:50]

        if video.fav_time:
            year_month = datetime.fromtimestamp(video.fav_time).strftime("%Y-%m")
        else:
            year_month = datetime.now().strftime("%Y-%m")

        quality_label = S3Uploader.quality_to_label(video.quality)

        if video.total_pages > 1:
            filename = f"{video.bvid}_{safe_title}_P{video.page:02d}_{quality_label}.mp4"
        else:
            filename = f"{video.bvid}_{safe_title}_{quality_label}.mp4"

        return f"{folder_name}/{year_month}/{filename}"

    async def upload_video(
        self, video: Video, local_path: str, delete_after_upload: bool = True
    ) -> Tuple[bool, Optional[str], str]:
        """
        通过 rclone 上传视频

        Returns:
            (success, remote_key, error_message)
        """
        if not self.db:
            await self.init_db()

        rc = self.config.rclone
        if not rc.remote_name or not rc.remote_type:
            return False, None, "Rclone 未配置"

        self._write_rclone_conf()

        remote_key = self.generate_remote_key(video)
        remote_dir = self._build_remote_dest(
            "/".join(remote_key.split("/")[:-1])
        )

        args = [
            "rclone", "copy",
            "--config", str(self._conf_path),
            local_path,
            remote_dir,
            "--progress",
            "--stats-one-line",
            "--no-traverse",
        ]

        if rc.extra_flags:
            args.extend(rc.extra_flags.split())

        logger.info(f"rclone 上传：{local_path} -> {remote_dir}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=3600
            )
            stdout_text = stdout.decode("utf-8", errors="ignore")
            stderr_text = stderr.decode("utf-8", errors="ignore")

            if proc.returncode != 0:
                error_msg = stderr_text.strip() or stdout_text.strip() or "rclone 上传失败"
                logger.error(f"rclone 上传失败 (exit={proc.returncode}): {error_msg[:500]}")
                return False, None, error_msg[:500]

            full_key = f"rclone://{self._build_remote_dest(remote_key)}"

            await self.db.update_video_s3_status(
                bvid=video.bvid,
                page=video.page,
                s3_key=full_key,
                quality=video.quality
            )

            if delete_after_upload and os.path.exists(local_path):
                os.remove(local_path)
                logger.info(f"已删除本地文件：{local_path}")

            logger.info(f"rclone 上传成功：{full_key}")
            return True, full_key, ""

        except asyncio.TimeoutError:
            return False, None, "rclone 上传超时（1 小时）"
        except FileNotFoundError:
            return False, None, "未找到 rclone 命令，请先安装 rclone"
        except Exception as e:
            logger.error(f"rclone 上传异常：{e}")
            return False, None, str(e)[:500]

    async def test_connection(self) -> Tuple[bool, str]:
        """测试 rclone 连接"""
        rc = self.config.rclone
        if not rc.remote_name or not rc.remote_type:
            return False, "Rclone 未配置 remote_name 或 remote_type"

        self._write_rclone_conf()

        dest = self._build_remote_dest("")

        args = [
            "rclone", "lsd",
            "--config", str(self._conf_path),
            dest,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30
            )
            stderr_text = stderr.decode("utf-8", errors="ignore")

            if proc.returncode == 0:
                return True, f"连接成功：{dest}"
            return False, stderr_text.strip()[:500] or "连接失败"

        except asyncio.TimeoutError:
            return False, "连接超时（30 秒）"
        except FileNotFoundError:
            return False, "未找到 rclone 命令，请先安装 rclone"
        except Exception as e:
            return False, str(e)[:500]
