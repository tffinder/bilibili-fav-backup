"""
B 站 API 模块
优先使用开源库 bilibili-api-python + yt-dlp
"""
import asyncio
import base64
import os
import re
import subprocess
import uuid
import sys
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from loguru import logger
from bilibili_api import favorite_list, user
from bilibili_api.login_v2 import (
    QrCodeLogin, QrCodeLoginEvents, PhoneNumber, login_with_password,
    login_with_sms, send_sms
)
from bilibili_api.utils.geetest import Geetest, GeetestType
from bilibili_api.utils.network import Credential
from bilibili_api.video import Video

from core.config import get_config
from core.database import Video as DbVideo


class BilibiliAPI:
    """B 站 API 封装"""

    _qr_sessions: Dict[str, Dict[str, Any]] = {}
    _geetest_sessions: Dict[str, Dict[str, Any]] = {}

    def __init__(self):
        self.config = get_config()
        self.cookie = self.config.bilibili.cookie
        self.request_delay = self.config.download.request_delay

    async def _rate_limit(self) -> None:
        if self.request_delay > 0:
            await asyncio.sleep(self.request_delay)

    @staticmethod
    def _parse_cookie_string(cookie: str) -> Dict[str, str]:
        cookie_dict: Dict[str, str] = {}
        for part in cookie.split(";"):
            item = part.strip()
            if not item or "=" not in item:
                continue
            k, v = item.split("=", 1)
            cookie_dict[k.strip()] = v.strip()
        return cookie_dict

    def _build_credential(self) -> Optional[Credential]:
        if not self.cookie:
            return None
        cookie_dict = self._parse_cookie_string(self.cookie)
        if not cookie_dict:
            return None
        return Credential.from_cookies(cookie_dict)

    async def validate_cookie(self) -> Tuple[bool, str]:
        if not self.cookie:
            return False, "Cookie 未配置"
        try:
            credential = self._build_credential()
            if not credential:
                return False, "Cookie 格式无效"
            await user.get_self_info(credential=credential)
            return True, "Cookie 有效"
        except Exception as e:
            logger.error(f"Cookie 校验异常：{e}")
            return False, f"Cookie 校验失败：{str(e)}"

    async def get_self_profile(self) -> Tuple[bool, Dict[str, Any], str]:
        credential = self._build_credential()
        if not credential:
            return False, {}, "Cookie 未配置或格式错误"

        await self._rate_limit()
        try:
            info = await user.get_self_info(credential=credential)
            level_info = info.get("level_exp") or {}
            profile = {
                "uid": int(info.get("mid") or info.get("uid") or 0),
                "name": info.get("name") or info.get("uname") or "",
                "avatar": info.get("face") or info.get("avatar") or "",
                "sign": info.get("sign") or "",
                "level": level_info.get("current_level") or info.get("level")
            }
            if not profile["uid"]:
                return False, {}, "未获取到用户 UID"
            return True, profile, ""
        except Exception as e:
            logger.error(f"获取当前用户资料失败：{e}")
            return False, {}, f"获取当前用户资料失败：{str(e)}"

    async def get_favorite_folders(self) -> Tuple[bool, List[Dict[str, Any]], str]:
        success, profile, error = await self.get_self_profile()
        if not success:
            return False, [], error

        credential = self._build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self._rate_limit()
        try:
            data = await favorite_list.get_video_favorite_list(
                uid=int(profile["uid"]),
                credential=credential
            )
            raw_folders = data.get("list") or data.get("data") or []
            selected_id = str(self.config.bilibili.fav_id or "")
            folders: List[Dict[str, Any]] = []

            for item in raw_folders:
                folder_id = item.get("id") or item.get("fid") or item.get("media_id")
                if folder_id is None:
                    continue
                folders.append({
                    "id": int(folder_id),
                    "title": item.get("title") or item.get("name") or f"收藏夹 {folder_id}",
                    "media_count": int(item.get("media_count") or item.get("count") or 0),
                    "cover": item.get("cover") or "",
                    "selected": str(folder_id) == selected_id
                })

            return True, folders, ""
        except Exception as e:
            logger.error(f"获取收藏夹列表失败：{e}")
            return False, [], f"获取收藏夹列表失败：{str(e)}"

    async def generate_login_qrcode(self) -> Tuple[bool, Dict[str, Any], str]:
        try:
            qrcode = QrCodeLogin()
            await qrcode.generate_qrcode()
            picture = qrcode.get_qrcode_picture()
            image_b64 = base64.b64encode(picture.content).decode("ascii")
            session_id = uuid.uuid4().hex
            self._qr_sessions[session_id] = {
                "qrcode": qrcode,
                "created_at": datetime.now()
            }
            return True, {
                "session_id": session_id,
                "image_base64": image_b64
            }, "二维码已生成"
        except Exception as e:
            logger.error(f"生成扫码二维码失败：{e}")
            return False, {}, str(e)

    async def poll_login_qrcode(self, session_id: str) -> Tuple[bool, Dict[str, Any], str]:
        session = self._qr_sessions.get(session_id)
        if not session:
            return False, {}, "二维码会话不存在或已过期"

        # 10 分钟过期回收
        if datetime.now() - session["created_at"] > timedelta(minutes=10):
            self._qr_sessions.pop(session_id, None)
            return True, {"status_code": 86038, "status_text": "二维码已失效", "cookie": ""}, "二维码已失效"

        try:
            qrcode: QrCodeLogin = session["qrcode"]
            event = await qrcode.check_state()

            if event == QrCodeLoginEvents.DONE:
                credential = qrcode.get_credential()
                cookies = credential.get_cookies()
                cookie = "; ".join([f"{k}={v}" for k, v in cookies.items() if v])
                self._qr_sessions.pop(session_id, None)
                return True, {"status_code": 0, "status_text": "登录成功", "cookie": cookie}, "登录成功"
            if event == QrCodeLoginEvents.SCAN:
                return True, {"status_code": 86090, "status_text": "已扫码，待确认", "cookie": ""}, "已扫码，待确认"
            if event == QrCodeLoginEvents.CONF:
                return True, {"status_code": 86090, "status_text": "已确认，等待完成", "cookie": ""}, "已确认，等待完成"
            if event == QrCodeLoginEvents.TIMEOUT:
                self._qr_sessions.pop(session_id, None)
                return True, {"status_code": 86038, "status_text": "二维码已失效", "cookie": ""}, "二维码已失效"

            return True, {"status_code": 86101, "status_text": "未扫码", "cookie": ""}, "未扫码"
        except Exception as e:
            logger.error(f"轮询扫码状态失败：{e}")
            return False, {}, str(e)

    @staticmethod
    def _credential_to_cookie(credential: Credential) -> str:
        cookies = credential.get_cookies()
        return "; ".join([f"{k}={v}" for k, v in cookies.items() if v])

    @staticmethod
    def _parse_login_result(result: Any) -> Tuple[bool, str, str]:
        if isinstance(result, Credential):
            return True, BilibiliAPI._credential_to_cookie(result), "登录成功"
        # 风控二次验证
        if hasattr(result, "fetch_info"):
            return False, "", "触发二次验证，请先完成安全验证"
        return False, "", "登录失败"

    async def start_geetest(self) -> Tuple[bool, Dict[str, Any], str]:
        """开始极验流程（用于密码/短信登录）"""
        try:
            gt = Geetest()
            await gt.generate_test(GeetestType.LOGIN)
            gt.start_geetest_server()
            meta = gt.get_info()
            session_id = uuid.uuid4().hex
            self._geetest_sessions[session_id] = {
                "geetest": gt,
                "created_at": datetime.now()
            }
            return True, {
                "session_id": session_id,
                "gt": meta.gt,
                "challenge": meta.challenge,
                "token": meta.token,
                "verify_url": gt.get_geetest_server_url()
            }, "极验已生成"
        except Exception as e:
            logger.error(f"启动极验失败：{e}")
            return False, {}, str(e)

    async def get_geetest_status(self, session_id: str) -> Tuple[bool, Dict[str, Any], str]:
        session = self._geetest_sessions.get(session_id)
        if not session:
            return False, {}, "极验会话不存在或已过期"
        gt: Geetest = session["geetest"]
        if gt.has_done():
            meta = gt.get_result()
            return True, {
                "done": True,
                "validate": meta.validate,
                "seccode": meta.seccode
            }, "验证完成"
        return True, {"done": False}, "等待验证"

    async def login_by_password(
        self, username: str, password: str, geetest_session_id: str
    ) -> Tuple[bool, Dict[str, Any], str]:
        session = self._geetest_sessions.get(geetest_session_id)
        if not session:
            return False, {}, "极验会话不存在"
        gt: Geetest = session["geetest"]
        if not gt.has_done():
            return False, {}, "请先完成极验验证"
        try:
            result = await login_with_password(username, password, gt)
            ok, cookie, msg = self._parse_login_result(result)
            if ok:
                self._geetest_sessions.pop(geetest_session_id, None)
                gt.close_geetest_server()
                return True, {"cookie": cookie}, msg
            return False, {}, msg
        except Exception as e:
            logger.error(f"密码登录失败：{e}")
            return False, {}, str(e)

    async def send_sms_code(
        self, phone: str, country: str, geetest_session_id: str
    ) -> Tuple[bool, Dict[str, Any], str]:
        session = self._geetest_sessions.get(geetest_session_id)
        if not session:
            return False, {}, "极验会话不存在"
        gt: Geetest = session["geetest"]
        if not gt.has_done():
            return False, {}, "请先完成极验验证"
        try:
            pn = PhoneNumber(number=phone, country=country)
            captcha_id = await send_sms(phonenumber=pn, geetest=gt)
            return True, {"captcha_id": captcha_id}, "短信已发送"
        except Exception as e:
            logger.error(f"发送短信失败：{e}")
            return False, {}, str(e)

    async def login_by_sms(
        self, phone: str, country: str, code: str, captcha_id: str
    ) -> Tuple[bool, Dict[str, Any], str]:
        try:
            pn = PhoneNumber(number=phone, country=country)
            result = await login_with_sms(phonenumber=pn, code=code, captcha_id=captcha_id)
            ok, cookie, msg = self._parse_login_result(result)
            if ok:
                return True, {"cookie": cookie}, msg
            return False, {}, msg
        except Exception as e:
            logger.error(f"短信登录失败：{e}")
            return False, {}, str(e)

    async def get_favorite_folder_info(self, fav_id: int) -> Tuple[bool, Dict[str, Any], str]:
        """
        获取任意收藏夹信息（支持其他用户的收藏夹）

        Args:
            fav_id: 收藏夹 ID

        Returns:
            (success, folder_info, error_message)
        """
        credential = self._build_credential()
        if not credential:
            return False, {}, "Cookie 未配置或格式错误"

        await self._rate_limit()
        try:
            # 获取收藏夹信息
            data = await favorite_list.get_video_favorite_list_content(
                media_id=fav_id,
                page=1,
                credential=credential
            )

            # 从返回数据中提取收藏夹信息
            info = data.get("info", {}) or {}
            medias = data.get("medias", []) or []

            folder_info = {
                "id": fav_id,
                "title": info.get("title", f"收藏夹 {fav_id}"),
                "media_count": info.get("media_count", len(medias)),
                "cover": info.get("cover", ""),
                "intro": info.get("intro", ""),
                "owner": info.get("upper", {}),
                "is_owner": info.get("attr", 0) == 0  # 是否是自己的收藏夹
            }

            return True, folder_info, ""
        except Exception as e:
            logger.error(f"获取收藏夹信息失败：{e}")
            return False, {}, f"获取收藏夹信息失败：{str(e)}"

    async def get_favorites_list(self, fav_id: str) -> Tuple[bool, List[Dict[str, Any]], str]:
        credential = self._build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self._rate_limit()
        try:
            media_id = int(fav_id)
        except ValueError:
            return False, [], "收藏夹 ID 不是数字"

        try:
            page = 1
            videos: List[Dict[str, Any]] = []
            while True:
                data = await favorite_list.get_video_favorite_list_content(
                    media_id=media_id,
                    page=page,
                    credential=credential
                )
                medias = data.get("medias", []) or []
                if not medias:
                    break
                for media in medias:
                    videos.append({
                        "bvid": media.get("bvid", ""),
                        "title": media.get("title", ""),
                        "intro": media.get("intro", ""),
                        "pubdate": media.get("pubtime", 0),
                        "owner": (media.get("upper") or {}).get("name", "unknown"),
                        "cover": media.get("cover", ""),
                        "duration": media.get("duration", 0),
                        "fav_time": media.get("fav_time", 0)
                    })
                if len(medias) < 20:
                    break
                page += 1
                await self._rate_limit()
            return True, videos, ""
        except Exception as e:
            logger.error(f"获取收藏夹失败：{e}")
            return False, [], f"获取收藏夹失败：{str(e)}"

    async def get_video_pages(self, bvid: str) -> Tuple[bool, List[Dict[str, Any]], str]:
        credential = self._build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self._rate_limit()
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
        credential = self._build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self._rate_limit()
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
        if not video_input:
            return None
        value = video_input.strip()
        match = re.search(r"(BV[0-9A-Za-z]{10})", value)
        if match:
            return match.group(1)
        return None

    async def get_video_info(self, bvid: str) -> Tuple[bool, Dict[str, Any], str]:
        credential = self._build_credential()
        if not credential:
            return False, {}, "Cookie 未配置或格式错误"

        await self._rate_limit()
        try:
            video_obj = Video(bvid=bvid, credential=credential)
            info = await video_obj.get_info()
            owner = info.get("owner") or {}
            return True, {
                "bvid": bvid,
                "title": info.get("title") or bvid,
                "pubdate": info.get("pubdate") or 0,
                "owner_name": owner.get("name") or "",
                "desc": info.get("desc") or "",
                "cover_url": info.get("pic") or info.get("cover") or info.get("thumbnail") or ""
            }, ""
        except Exception as e:
            logger.error(f"获取视频信息失败：{e}")
            return False, {}, f"获取视频信息失败：{str(e)}"

    def _run_command(self, args: List[str]) -> Tuple[bool, str, str]:
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
    def _quality_to_height(quality: int) -> int:
        mapping = {
            127: 4320, 126: 2160, 125: 2160, 120: 2160,
            116: 1080, 112: 1080, 80: 1080, 74: 720, 64: 720, 48: 720,
            32: 480, 16: 360
        }
        return mapping.get(quality, 1080)

    @staticmethod
    def _quality_to_format(quality: int) -> str:
        """根据清晰度代码返回 yt-dlp 格式选择字符串"""
        # B站视频格式说明：
        # - 127: 8K 超高清
        # - 126: 杜比视界
        # - 125: 4K 超清 (HDR)
        # - 120: 4K HDR
        # - 116: 1080P 高码率
        # - 112: 1080P+
        # - 80: 1080P
        # - 64: 720P

        height_map = {
            127: 4320, 126: 2160, 125: 2160, 120: 2160,
            116: 1080, 112: 1080, 80: 1080, 74: 720, 64: 720, 48: 720,
            32: 480, 16: 360
        }
        target_height = height_map.get(quality, 1080)

        # 对于 4K 及以上，使用更宽松的格式选择
        if quality >= 120:
            # 4K/HDR/杜比：尝试获取最佳画质+最佳音频
            # bv* 表示最佳视频，ba 表示最佳音频
            return f"bv*[height<={target_height}]+ba/b[height<={target_height}]/bv+ba/b"
        else:
            # 普通清晰度
            return f"bv*[height<={target_height}]+ba/b[height<={target_height}]/b"

    async def download_video(
        self,
        bvid: str,
        cid: str,
        output_dir: str,
        quality: int = 127,
        page: int = 1
    ) -> Tuple[bool, str, str]:
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

        # 创建临时下载目录（用于存放下载的视频和音频文件）
        temp_download_dir = f"{output_dir}_temp"
        Path(temp_download_dir).mkdir(parents=True, exist_ok=True)

        # 获取格式选择字符串 - 分别下载视频和音频
        format_selector = self._quality_to_format(quality)
        video_url = f"https://www.bilibili.com/video/{bvid}?p={page}"

        # yt-dlp 参数：下载到临时目录，保留中间文件以便手动合并
        args = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--add-header", f"Cookie:{self.cookie}",
            "--format", format_selector,
            "--merge-output-format", "mp4",
            "--output", str(Path(temp_download_dir) / "%(title)s.%(ext)s"),
            "--keep-video",  # 保留下载的中间文件
            video_url
        ]

        # 如果有自定义 ffmpeg 路径，传递给 yt-dlp
        if ffmpeg_path:
            args.extend(["--ffmpeg-location", str(Path(ffmpeg_path).parent)])

        if self.config.debug.biliup_proxy:
            args.extend(["--proxy", self.config.debug.biliup_proxy])

        logger.info(f"yt-dlp 命令：{' '.join(args)}")
        success, stdout, stderr = self._run_command(args)

        if not success:
            logger.error(f"yt-dlp 下载失败：{stderr}")
            # 如果下载失败，尝试使用备用格式
            logger.info("尝试备用格式下载...")
            backup_args = args.copy()
            backup_args[6] = "bestvideo+bestaudio/best"
            success, stdout, stderr = self._run_command(backup_args)
            if not success:
                # 清理临时目录
                self._cleanup_temp_dir(temp_download_dir)
                return False, "", stderr or "下载失败"

        # 查找下载的文件
        merged_file = self._find_latest_media(temp_download_dir, extensions=[".mp4", ".mkv", ".flv"])
        video_file = self._find_latest_media(temp_download_dir, extensions=[".f*.mp4", ".f*.m4s", ".video.*"])
        audio_file = self._find_latest_media(temp_download_dir, extensions=[".f*.m4a", ".f*.opus", ".audio.*"])

        final_file = None

        if merged_file and self._check_audio_track(merged_file):
            # yt-dlp 已经成功合并
            final_file = merged_file
            logger.info(f"yt-dlp 已自动合并：{merged_file}")
        elif video_file and audio_file:
            # 需要手动合并
            logger.info(f"手动合并视频和音频：{video_file} + {audio_file}")
            # 生成输出文件名（使用视频文件基础名，但去掉 .f* 后缀）
            video_name = Path(video_file).stem
            # 移除 .f100026 这类后缀
            if ".f" in video_name:
                video_name = video_name.split(".f")[0]
            final_file = str(Path(output_dir) / f"{video_name}.mp4")

            merge_success, merge_error = self._merge_video_audio(
                video_file, audio_file, final_file, ffmpeg_exe
            )
            if not merge_success:
                self._cleanup_temp_dir(temp_download_dir)
                return False, "", f"合并失败：{merge_error}"
        elif merged_file:
            # 只有合并后的文件（可能是单文件下载）
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
            has_audio = self._check_audio_track(final_file)
            if not has_audio:
                logger.warning(f"视频文件没有音频轨道：{final_file}")
            return True, final_file, ""

        return False, "", "下载完成但未找到文件"

    def _merge_video_audio(
        self, video_file: str, audio_file: str, output_file: str, ffmpeg_exe: str
    ) -> Tuple[bool, str]:
        """使用 ffmpeg 合并视频和音频"""
        import subprocess

        try:
            cmd = [
                ffmpeg_exe,
                "-i", video_file,
                "-i", audio_file,
                "-c:v", "copy",
                "-c:a", "aac",
                "-y",  # 覆盖已存在的文件
                output_file
            ]

            logger.info(f"ffmpeg 合并命令：{' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                logger.error(f"ffmpeg 合并失败：{result.stderr}")
                return False, result.stderr

            logger.info(f"合并成功：{output_file}")
            return True, ""
        except subprocess.TimeoutExpired:
            return False, "合并超时"
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

    def _check_audio_track(self, file_path: str) -> bool:
        """检查视频文件是否有音频轨道"""
        try:
            import subprocess

            # 获取 ffprobe 路径
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
            return True  # 假设有音频

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
            # 也匹配带通配符的扩展名
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

    async def get_video_detail(self, bvid: str, title: str = "") -> Optional[DbVideo]:
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

    async def get_watch_later_list(self) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        获取稍后观看列表

        Returns:
            (success, videos, error_message)
        """
        credential = self._build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self._rate_limit()
        try:
            data = await user.get_toview_list(credential=credential)
            videos: List[Dict[str, Any]] = []
            for item in data.get("list", []):
                owner = item.get("owner", {})
                videos.append({
                    "bvid": item.get("bvid", ""),
                    "title": item.get("title", ""),
                    "cover": item.get("cover", ""),
                    "duration": item.get("duration", 0),
                    "owner_name": owner.get("name", ""),
                    "owner_mid": owner.get("mid", 0),
                    "add_at": item.get("add_at", 0)
                })
            return True, videos, ""
        except Exception as e:
            logger.error(f"获取稍后观看列表失败：{e}")
            return False, [], f"获取稍后观看列表失败：{str(e)}"

    async def get_watch_history(
        self, page: int = 1, page_size: int = 50
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        获取观看历史

        Args:
            page: 页码
            page_size: 每页数量

        Returns:
            (success, videos, error_message)
        """
        credential = self._build_credential()
        if not credential:
            return False, [], "Cookie 未配置或格式错误"

        await self._rate_limit()
        try:
            # 使用 HistoryType.ALL 获取所有类型，然后过滤视频
            data = await user.get_self_history_new(
                credential=credential,
                ps=page_size
            )
            videos: List[Dict[str, Any]] = []
            for item in data.get("list", {}).get("list", []):
                history = item.get("history", {})
                # 只处理视频类型
                if history.get("business") != "archive":
                    continue
                videos.append({
                    "bvid": history.get("bvid", ""),
                    "title": item.get("show_title", "") or history.get("title", ""),
                    "cover": item.get("cover", ""),
                    "duration": item.get("duration", 0),
                    "progress": history.get("progress", 0),  # 观看进度
                    "owner_name": item.get("author_name", ""),
                    "view_at": item.get("view_at", 0)  # 观看时间
                })
            return True, videos, ""
        except Exception as e:
            logger.error(f"获取观看历史失败：{e}")
            return False, [], f"获取观看历史失败：{str(e)}"

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

            # 添加 Referer 防止 403
            headers = {
                "Referer": "https://www.bilibili.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=30) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        # 确保目录存在
                        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                        with open(save_path, 'wb') as f:
                            f.write(content)
                        return True, ""
                    return False, f"HTTP {resp.status}"

        except Exception as e:
            logger.error(f"下载封面失败：{e}")
            return False, str(e)
