"""
视频管理 API

提供视频列表、下载、文件访问等接口
"""
import asyncio
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse
from loguru import logger

from core.config import get_config
from core.database import Database, Video, DownloadProgress
from services.bilibili_api import BilibiliAPI
from services.downloader import Downloader
from services.s3_uploader import S3Uploader
from api.models import (
    VideoModel, DownloadProgressModel, ApiResponse,
    SingleVideoDownloadRequest
)

router = APIRouter(tags=["视频管理"])
_cover_download_tasks: Dict[str, asyncio.Task] = {}
_deleted_scan_state: Dict[str, Any] = {
    "running": False,
    "current": 0,
    "total": 0,
    "deleted": 0,
    "checked": 0,
    "message": "",
    "started_at": None,
    "finished_at": None
}


DELETED_ERROR_KEYWORDS = (
    "不存在",
    "已删除",
    "稿件不可见",
    "视频不见了",
    "404",
    "62002",
    "62004",
    "稿件已失效",
    "视频已失效",
    "not found",
    "not exist",
    "deleted",
    "unavailable"
)
COVER_EXTENSIONS = ('.jpg', '.png', '.webp', '.jpeg')
COVER_MEDIA_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
}


def _resolve_download_base() -> Path:
    config = get_config()
    base = Path(config.download.temp_dir)
    if not base.is_absolute():
        base = (Path(__file__).parent.parent.parent / base).resolve()
    return base


def _resolve_covers_dir() -> Path:
    covers_dir = _resolve_download_base().parent / "covers" / "videos"
    covers_dir.mkdir(parents=True, exist_ok=True)
    return covers_dir


def _resolve_local_path(path_value: Optional[str]) -> Optional[Path]:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = (Path(__file__).parent.parent.parent / path).resolve()
    return path


def _build_play_url(video: Video, base_dir: Path) -> Optional[str]:
    if not video.local_path or not video.id:
        return None
    path = _resolve_local_path(video.local_path)
    if not path or not path.exists():
        return None
    if base_dir not in path.parents and path != base_dir:
        return None
    return f"/api/videos/{video.id}/file"


def _build_download_url(video: Video, base_dir: Path) -> Optional[str]:
    if video.id and _build_play_url(video, base_dir):
        return f"/api/videos/{video.id}/file?inline=0"
    if video.s3_key and video.s3_key.startswith("local://"):
        return f"/api/videos/{video.id}/file?inline=0" if video.id else None
    if video.s3_key and video.s3_uploaded:
        return f"/api/videos/{video.id}/remote-download?inline=0" if video.id else None
    return None


def _is_deleted_error(error: str) -> bool:
    normalized = (error or "").lower()
    return any(keyword.lower() in normalized for keyword in DELETED_ERROR_KEYWORDS)


def _cover_ext_from_url(url: str) -> str:
    suffix = Path((url or "").split("?", 1)[0]).suffix.lower()
    return suffix if suffix in COVER_EXTENSIONS else ".jpg"


def _cover_file_response(path: Path, filename: Optional[str] = None) -> FileResponse:
    ext = path.suffix.lower()
    return FileResponse(
        str(path),
        media_type=COVER_MEDIA_TYPES.get(ext, "image/jpeg"),
        filename=filename or path.name
    )


def _find_local_cover(bvid: str, covers_dir: Path) -> Optional[Path]:
    for ext in COVER_EXTENSIONS:
        path = covers_dir / f"{bvid}{ext}"
        if path.exists():
            return path
    return None


async def _download_cover_to_local(
    db: Database,
    bvid: str,
    cover_url: str,
    covers_dir: Path,
    api: Optional[BilibiliAPI] = None
) -> Optional[Path]:
    if not cover_url:
        return None

    cover_url = cover_url.strip()
    if cover_url.startswith("//"):
        cover_url = f"https:{cover_url}"

    existing = _find_local_cover(bvid, covers_dir)
    if existing:
        await db.update_video_cache_cover(bvid, str(existing))
        return existing

    ext = _cover_ext_from_url(cover_url)
    save_path = covers_dir / f"{bvid}{ext}"
    api = api or BilibiliAPI()
    success, error = await api.download_cover(cover_url, str(save_path))
    if success and save_path.exists():
        await db.update_video_cache_cover(bvid, str(save_path))
        return save_path

    logger.warning(f"下载视频封面失败 {bvid}: {error}")
    return None


def _schedule_cover_download(bvid: str, cover_url: str, covers_dir: Path) -> None:
    if not cover_url:
        return

    task = _cover_download_tasks.get(bvid)
    if task and not task.done():
        return

    async def _task() -> None:
        try:
            db = await Database.get_instance()
            await _download_cover_to_local(db, bvid, cover_url, covers_dir)
        finally:
            _cover_download_tasks.pop(bvid, None)

    _cover_download_tasks[bvid] = asyncio.create_task(_task())


def _progress_failed(progress: Optional[DownloadProgress]) -> bool:
    return bool(progress and progress.status in {"failed", "upload_failed"})


def _is_local_only(video: Video) -> bool:
    """本地保留模式：s3_uploaded 为真但 s3_key 是 local:// 占位符"""
    return bool(video.s3_uploaded and video.s3_key and video.s3_key.startswith("local://"))


def _matches_archive_status(
    video: Video,
    status: Optional[str],
    progress: Optional[DownloadProgress] = None
) -> bool:
    if not status or status == "all":
        return True
    if status == "uploaded":
        return video.s3_uploaded and not _is_local_only(video)
    if status == "downloaded":
        return bool(video.local_path or video.s3_uploaded)
    if status == "failed":
        return not video.s3_uploaded and bool(video.upload_failed or _progress_failed(progress))
    if status == "pending":
        return not video.s3_uploaded and not video.upload_failed and not _progress_failed(progress)
    return True


async def _hydrate_video_model(video: Video, progress_map: Dict = None) -> VideoModel:
    base_dir = _resolve_download_base()
    model = VideoModel.model_validate(video)
    model.local_path = video.local_path
    model.play_url = _build_play_url(video, base_dir)
    model.download_url = _build_download_url(video, base_dir)
    model.remote_download_url = (
        f"/api/videos/{video.id}/remote-download"
        if video.id and video.s3_key and video.s3_uploaded and not model.play_url
        else None
    )
    model.cover_url = ""
    model.max_quality = video.max_quality or video.quality
    model.upload_failed = video.upload_failed if hasattr(video, 'upload_failed') else False
    model.s3_key = video.s3_key

    progress = progress_map.get((video.bvid, video.page)) if progress_map else None
    if video.s3_uploaded:
        model.download_status = "completed"
        model.download_progress = 100.0
    elif video.upload_failed:
        model.download_status = "upload_failed"
        model.download_progress = 100.0
    elif progress and progress.status in {"failed", "pending", "downloading", "uploading"}:
        model.download_status = progress.status
        model.download_progress = progress.progress
    else:
        if video.local_path:
            model.download_status = "completed"
            model.download_progress = 100.0
        else:
            model.download_status = "pending"
            model.download_progress = 0.0
    return model


async def _scan_deleted_videos_task() -> None:
    global _deleted_scan_state

    from services.sync_manager import SyncManager
    sm = SyncManager()

    _deleted_scan_state.update({
        "running": True,
        "current": 0,
        "total": 0,
        "deleted": 0,
        "checked": 0,
        "message": "开始检测 B 站源视频状态",
        "started_at": datetime.now().isoformat(),
        "finished_at": None
    })

    def on_progress(current, total, message):
        _deleted_scan_state["current"] = current
        _deleted_scan_state["total"] = total
        _deleted_scan_state["checked"] = current
        _deleted_scan_state["message"] = message

    try:
        result = await sm.scan_source_status(on_progress=on_progress)
        _deleted_scan_state["deleted"] = result.get("deleted", 0)
    except Exception as e:
        logger.error(f"检测失效视频失败：{e}")
        _deleted_scan_state["message"] = f"检测失败：{e}"
    finally:
        _deleted_scan_state["running"] = False
        _deleted_scan_state["finished_at"] = datetime.now().isoformat()
        if not _deleted_scan_state["message"].startswith("检测失败"):
            _deleted_scan_state["message"] = "检测完成"


async def _download_single_video_task(bvid: str, target_quality: int) -> None:
    """后台下载单个视频的所有分P，并按当前配置决定是否上传 S3。"""
    api = BilibiliAPI()
    downloader = Downloader()
    uploader = S3Uploader()
    db = await Database.get_instance()
    config = get_config()

    info_ok, info, info_error = await api.get_video_info(bvid)
    if not info_ok:
        logger.error(f"获取视频信息失败 {bvid}: {info_error}")
        return

    pages_ok, pages, pages_error = await api.get_video_pages(bvid)
    if not pages_ok or not pages:
        logger.error(f"获取视频分页失败 {bvid}: {pages_error}")
        return

    quality_ok, qualities, _ = await api.get_video_quality(bvid, pages[0]["cid"])
    available_quality = qualities[0] if quality_ok and qualities else target_quality

    results = await downloader.download_multi_page_video(
        bvid=bvid,
        title=info["title"],
        pages=pages,
        target_quality=target_quality
    )

    for page_num, success, file_path, error in results:
        if not success or not file_path:
            logger.error(f"单视频下载失败 {bvid} P{page_num}: {error}")
            continue

        page_info = next((page for page in pages if page["page"] == page_num), None)
        if not page_info:
            continue

        video_obj = Video(
            id=None,
            bvid=bvid,
            title=f"{info['title']}_P{page_num}",
            cid=page_info["cid"],
            page=page_num,
            total_pages=len(pages),
            quality=available_quality,
            duration=page_info["duration"],
            pubdate=info.get("pubdate", 0),
            owner_name=info.get("owner_name", ""),
            s3_key=None,
            s3_uploaded=False,
            s3_quality=None,
            local_path=file_path,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )

        await db.add_video(video_obj)

        if config.s3.enabled:
            upload_success, s3_key, upload_error = await uploader.upload_video(
                video=video_obj,
                local_path=file_path,
                delete_after_upload=config.s3.delete_after_upload
            )
            if upload_success and s3_key:
                await db.update_video_s3_status(
                    bvid=video_obj.bvid,
                    page=video_obj.page,
                    s3_key=s3_key,
                    quality=available_quality
                )
            else:
                logger.error(f"单视频上传失败 {bvid} P{page_num}: {upload_error}")
        else:
            await db.update_video_s3_status(
                bvid=video_obj.bvid,
                page=video_obj.page,
                s3_key=f"local://{file_path}",
                quality=available_quality
            )


@router.get("/videos", response_model=List[VideoModel])
async def get_videos(
    response: Response,
    page: int = 1,
    page_size: int = 50,
    status: Optional[str] = None
):
    """获取视频列表"""
    try:
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        db = await Database.get_instance()
        all_videos = await db.get_all_videos()
        progress_map = {
            (p.bvid, p.page): p
            for p in await db.get_all_progress()
        }

        videos = [
            v for v in all_videos
            if _matches_archive_status(v, status, progress_map.get((v.bvid, v.page)))
        ]

        total = len(videos)
        response.headers["X-Total-Count"] = str(total)
        response.headers["X-Total-Pages"] = str((total + page_size - 1) // page_size if page_size else 1)

        # 分页
        start = (page - 1) * page_size
        end = start + page_size
        paginated = videos[start:end]

        return [await _hydrate_video_model(v, progress_map) for v in paginated]

    except Exception as e:
        logger.error(f"获取视频列表失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/videos/{bvid}", response_model=VideoModel)
async def get_video(bvid: str):
    """获取单个视频详情"""
    try:
        db = await Database.get_instance()
        video = await db.get_video_by_bvid(bvid)

        if not video:
            raise HTTPException(status_code=404, detail="视频不存在")

        base_dir = _resolve_download_base()
        model = VideoModel.model_validate(video)
        model.local_path = video.local_path
        model.play_url = _build_play_url(video, base_dir)

        progress = await db.get_progress_by_bvid_page(video.bvid, video.page)
        if progress:
            model.download_status = progress.status
            model.download_progress = progress.progress
        else:
            model.download_status = "completed" if video.s3_uploaded else "pending"
            model.download_progress = 100.0 if video.s3_uploaded else 0.0

        api = BilibiliAPI()
        cover_ok, cover_info, _ = await api.get_video_info(video.bvid)
        model.cover_url = cover_info.get("cover_url") if cover_ok else ""
        return model

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取视频详情失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/videos/{video_id}/file")
async def get_video_file(video_id: int, inline: bool = True):
    """获取本地已下载视频文件。

    Args:
        inline: True 走内联播放（浏览器内播），False 触发下载（Content-Disposition: attachment）。
    """
    try:
        db = await Database.get_instance()
        video = await db.get_video_by_id(video_id)
        if not video or not video.local_path:
            raise HTTPException(status_code=404, detail="视频文件不存在")

        base_dir = _resolve_download_base()
        path = _resolve_local_path(video.local_path)
        if not path or not path.exists():
            raise HTTPException(status_code=404, detail="视频文件不存在")
        if base_dir not in path.parents and path != base_dir:
            raise HTTPException(status_code=403, detail="禁止访问该路径")

        media_type, _ = mimetypes.guess_type(str(path))
        kwargs = {
            "path": str(path),
            "media_type": media_type or "application/octet-stream",
        }
        if inline:
            # 不传 filename，避免 FastAPI 自动加上 Content-Disposition: attachment
            response = FileResponse(**kwargs)
            response.headers["Content-Disposition"] = f'inline; filename="{path.name}"'
            return response
        return FileResponse(filename=path.name, **kwargs)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取视频文件失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/videos/{video_id}/remote-download")
async def get_video_remote(video_id: int, inline: bool = True):
    """从 S3 远程播放/下载视频：生成预签名 URL 并 302 重定向。

    Args:
        inline: True 走内联播放（浏览器边下边播放，支持 Range 拖动），
                False 强制下载（Content-Disposition: attachment）。
    """
    try:
        db = await Database.get_instance()
        video = await db.get_video_by_id(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="视频不存在")
        if not video.s3_uploaded or not video.s3_key:
            raise HTTPException(status_code=404, detail="该视频未上传 S3")
        # local:// 占位符不是真实的 S3 key
        if video.s3_key.startswith("local://"):
            raise HTTPException(status_code=404, detail="该视频只保留在本地，请用 /file 接口")

        from services.s3_uploader import S3Uploader
        uploader = S3Uploader()
        uploader.ensure_s3_initialized()

        if not uploader.s3_client:
            raise HTTPException(status_code=500, detail="S3 客户端未就绪")

        loop = asyncio.get_running_loop()

        def _gen(params: dict) -> str:
            return uploader.s3_client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=21600  # 6 小时
            )

        base_params = {
            "Bucket": uploader.bucket_name,
            "Key": video.s3_key,
        }

        # 文件名做两套：ASCII fallback + RFC 5987 (UTF-8 编码) 给现代浏览器
        from urllib.parse import quote
        raw_name = (
            Path(video.local_path).name if video.local_path
            else f"{video.bvid}_P{video.page}.mp4"
        )
        ascii_name = re.sub(r'[^\x20-\x7e]', '_', re.sub(r'[\\/:*?"<>|]', '_', raw_name))
        utf8_name = quote(raw_name, safe='')

        if inline:
            # 内联播放：不指定 Content-Disposition，让浏览器按 mime-type 决定（mp4 直接边下边播）
            url = await loop.run_in_executor(None, _gen, base_params)
        else:
            # 强制下载：用 RFC 5987 兼容两边
            disposition = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'
            params = {**base_params, "ResponseContentDisposition": disposition}
            try:
                url = await loop.run_in_executor(None, _gen, params)
            except Exception as e:
                logger.warning(f"带 disposition 签名失败，回退到无 disposition：{e}")
                url = await loop.run_in_executor(None, _gen, base_params)

        if not url:
            raise HTTPException(status_code=500, detail="生成预签名 URL 失败")
        # 用 302 重定向到 S3，浏览器直接从 S3 拉流（支持 Range，可拖动进度条）
        return RedirectResponse(url=url, status_code=302)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成 S3 远程链接失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan-deleted-videos", response_model=ApiResponse)
async def start_scan_deleted_videos():
    """扫描数据库中已备份视频在 B 站的失效状态（异步任务）。"""
    try:
        if _deleted_scan_state.get("running"):
            return ApiResponse(success=False, message="扫描任务正在进行中", data=_deleted_scan_state)
        asyncio.create_task(_scan_deleted_videos_task())
        return ApiResponse(success=True, message="扫描任务已启动")
    except Exception as e:
        logger.error(f"启动扫描失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scan-deleted-videos/status", response_model=ApiResponse)
async def get_scan_deleted_videos_status():
    """查询扫描进度。"""
    return ApiResponse(success=True, message="ok", data=_deleted_scan_state)


@router.post("/download-video", response_model=ApiResponse)
async def download_single_video(payload: SingleVideoDownloadRequest):
    """手动下载单个视频（支持 BV 号或链接）。"""
    try:
        config = get_config()
        api = BilibiliAPI()

        if not config.bilibili.cookie:
            return ApiResponse(success=False, message="请先登录或在设置中填写 Cookie")

        bvid = api.extract_bvid(payload.video_input)
        if not bvid:
            return ApiResponse(success=False, message="请输入有效的 BV 号或 B 站视频链接")

        valid, message = await api.validate_cookie()
        if not valid:
            return ApiResponse(success=False, message=message)

        target_quality = payload.quality or config.download.quality
        asyncio.create_task(_download_single_video_task(bvid, target_quality))

        return ApiResponse(
            success=True,
            message="已开始下载单个视频",
            data={"bvid": bvid, "quality": target_quality}
        )
    except Exception as e:
        logger.error(f"启动单视频下载失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _retry_video(bvid: str) -> int:
    """清掉 upload_failed 标记并把整个 BV 重新加入下载队列；返回清掉的分 P 数量。"""
    db = await Database.get_instance()
    config = get_config()
    videos = [v for v in await db.get_all_videos() if v.bvid == bvid]
    cleared = 0
    for v in videos:
        if v.upload_failed:
            await db.set_upload_failed(v.bvid, v.page, False, v.quality)
            cleared += 1
    asyncio.create_task(_download_single_video_task(bvid, config.download.quality))
    return cleared


@router.post("/videos/{bvid}/retry", response_model=ApiResponse)
async def retry_video(bvid: str):
    """重试某个失败视频：清除失败标记 + 重新下载。"""
    try:
        config = get_config()
        if not config.bilibili.cookie:
            return ApiResponse(success=False, message="请先登录或在设置中填写 Cookie")

        cleared = await _retry_video(bvid)
        return ApiResponse(
            success=True,
            message=f"已重新加入下载队列（清除 {cleared} 个失败标记）",
            data={"bvid": bvid, "cleared": cleared}
        )
    except Exception as e:
        logger.error(f"重试视频 {bvid} 失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/videos/retry-failed", response_model=ApiResponse)
async def retry_all_failed():
    """批量重试所有标记为失败的视频。"""
    try:
        config = get_config()
        if not config.bilibili.cookie:
            return ApiResponse(success=False, message="请先登录或在设置中填写 Cookie")

        db = await Database.get_instance()
        all_videos = await db.get_all_videos()
        progress_map = {(p.bvid, p.page): p for p in await db.get_all_progress()}

        failed_bvids: list = []
        seen = set()
        for v in all_videos:
            if v.bvid in seen:
                continue
            if _matches_archive_status(v, "failed", progress_map.get((v.bvid, v.page))):
                failed_bvids.append(v.bvid)
                seen.add(v.bvid)

        for bvid in failed_bvids:
            await _retry_video(bvid)

        return ApiResponse(
            success=True,
            message=f"已重新入队 {len(failed_bvids)} 个失败视频",
            data={"count": len(failed_bvids), "bvids": failed_bvids}
        )
    except Exception as e:
        logger.error(f"批量重试失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download-progress", response_model=List[DownloadProgressModel])
async def get_download_progress(active_only: bool = True):
    """获取下载进度"""
    try:
        db = await Database.get_instance()

        if active_only:
            progress_list = await db.get_active_downloads()
        else:
            progress_list = await db.get_all_progress()

        return [DownloadProgressModel.from_orm(p) for p in progress_list]

    except Exception as e:
        logger.error(f"获取下载进度失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 封面接口 ====================

@router.get("/covers/{bvid}")
async def get_video_cover(bvid: str):
    """获取视频封面图片"""
    try:
        db = await Database.get_instance()
        covers_dir = _resolve_covers_dir()

        local_cover = _find_local_cover(bvid, covers_dir)
        if local_cover:
            return _cover_file_response(local_cover)

        # 从缓存中查找
        video_caches = await db.get_videos_by_source("favorite")
        video_caches += await db.get_videos_by_source("watch_later")
        video_caches += await db.get_videos_by_source("history")

        for video in video_caches:
            if video.bvid != bvid:
                continue

            if video.cover_local:
                path = Path(video.cover_local)
                if path.exists():
                    return _cover_file_response(path, filename=f"{bvid}{path.suffix}")

            _schedule_cover_download(bvid, video.cover, covers_dir)
            raise HTTPException(status_code=404, detail="封面正在缓存")

        raise HTTPException(status_code=404, detail="封面不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取封面失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/covers/download", response_model=ApiResponse)
async def download_covers():
    """下载所有缺失的封面图片"""
    try:
        api = BilibiliAPI()
        db = await Database.get_instance()
        config = get_config()

        base_dir = Path(config.download.temp_dir).parent
        covers_dir = base_dir / "covers"
        fav_covers_dir = covers_dir / "favorites"
        video_covers_dir = covers_dir / "videos"
        fav_covers_dir.mkdir(parents=True, exist_ok=True)
        video_covers_dir.mkdir(parents=True, exist_ok=True)

        downloaded_count = 0

        # 下载收藏夹封面
        folders = await db.get_all_favorite_folders()
        for folder in folders:
            if folder.cover and not folder.cover_local:
                cover_url = folder.cover.strip()
                if cover_url.startswith("//"):
                    cover_url = f"https:{cover_url}"

                ext = _cover_ext_from_url(cover_url)
                save_path = str(fav_covers_dir / f"{folder.fav_id}{ext}")
                success, error = await api.download_cover(cover_url, save_path)

                if success:
                    await db.update_favorite_folder_cover(folder.fav_id, save_path)
                    downloaded_count += 1
                    logger.info(f"下载收藏夹封面：{folder.title}")

        # 下载视频封面
        video_caches = await db.get_videos_by_source("favorite")
        video_caches += await db.get_videos_by_source("watch_later")
        video_caches += await db.get_videos_by_source("history")

        for video in video_caches:
            if not video.cover:
                continue
            if video.cover_local and Path(video.cover_local).exists():
                continue

            saved = await _download_cover_to_local(
                db, video.bvid, video.cover, video_covers_dir, api=api
            )
            if saved:
                downloaded_count += 1
                logger.info(f"下载视频封面：{video.title}")

        return ApiResponse(
            success=True,
            message=f"已下载 {downloaded_count} 个封面图片"
        )
    except Exception as e:
        logger.error(f"下载封面失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))
