"""
视频管理 API

提供视频列表、下载、文件访问等接口
"""
import asyncio
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
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


def _resolve_download_base() -> Path:
    config = get_config()
    base = Path(config.download.temp_dir)
    if not base.is_absolute():
        base = (Path(__file__).parent.parent.parent / base).resolve()
    return base


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
    page: int = 1,
    page_size: int = 50,
    status: Optional[str] = None
):
    """获取视频列表"""
    try:
        db = await Database.get_instance()
        all_videos = await db.get_all_videos()

        # 过滤
        if status == "uploaded":
            videos = [v for v in all_videos if v.s3_uploaded]
        elif status == "pending":
            videos = [v for v in all_videos if not v.s3_uploaded and not v.upload_failed]
        elif status == "failed":
            videos = [v for v in all_videos if v.upload_failed]
        elif status == "downloaded":
            videos = [v for v in all_videos if v.local_path]
        else:
            videos = all_videos

        # 分页
        start = (page - 1) * page_size
        end = start + page_size
        paginated = videos[start:end]

        base_dir = _resolve_download_base()
        progress_map = await db.get_progress_map([(v.bvid, v.page) for v in paginated])
        result = []
        for v in paginated:
            model = VideoModel.model_validate(v)
            model.local_path = v.local_path
            model.play_url = _build_play_url(v, base_dir)
            model.cover_url = ""
            model.max_quality = v.max_quality or v.quality
            model.upload_failed = v.upload_failed if hasattr(v, 'upload_failed') else False

            progress = progress_map.get((v.bvid, v.page))
            if progress:
                model.download_status = progress.status
                model.download_progress = progress.progress
            else:
                if v.s3_uploaded:
                    model.download_status = "completed"
                    model.download_progress = 100.0
                elif v.upload_failed:
                    model.download_status = "upload_failed"
                    model.download_progress = 100.0
                elif v.local_path:
                    model.download_status = "completed"
                    model.download_progress = 100.0
                else:
                    model.download_status = "pending"
                    model.download_progress = 0.0

            result.append(model)

        return result

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
async def get_video_file(video_id: int):
    """获取本地已下载视频文件"""
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
        return FileResponse(
            str(path),
            media_type=media_type or "application/octet-stream",
            filename=path.name
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取视频文件失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        config = get_config()

        base_dir = Path(config.download.temp_dir).parent
        covers_dir = base_dir / "covers" / "videos"

        for ext in ['.jpg', '.png', '.webp', '.jpeg']:
            path = covers_dir / f"{bvid}{ext}"
            if path.exists():
                return FileResponse(
                    str(path),
                    media_type="image/jpeg",
                    filename=f"{bvid}{ext}"
                )

        # 从缓存中查找
        video_caches = await db.get_videos_by_source("favorite")
        video_caches += await db.get_videos_by_source("watch_later")
        video_caches += await db.get_videos_by_source("history")

        for video in video_caches:
            if video.bvid == bvid and video.cover_local:
                path = Path(video.cover_local)
                if path.exists():
                    return FileResponse(
                        str(path),
                        media_type="image/jpeg",
                        filename=f"{bvid}.jpg"
                    )

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
        from api.routes.user import auto_download_covers

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
                ext = ".jpg"
                if "." in folder.cover.split("/")[-1]:
                    ext = "." + folder.cover.split(".")[-1].split("?")[0]

                save_path = str(fav_covers_dir / f"{folder.fav_id}{ext}")
                success, error = await api.download_cover(folder.cover, save_path)

                if success:
                    await db.update_favorite_folder_cover(folder.fav_id, save_path)
                    downloaded_count += 1
                    logger.info(f"下载收藏夹封面：{folder.title}")

        # 下载视频封面
        video_caches = await db.get_videos_by_source("favorite")
        video_caches += await db.get_videos_by_source("watch_later")
        video_caches += await db.get_videos_by_source("history")

        for video in video_caches:
            if video.cover and not video.cover_local:
                ext = ".jpg"
                if "." in video.cover.split("/")[-1]:
                    ext = "." + video.cover.split(".")[-1].split("?")[0]

                save_path = str(video_covers_dir / f"{video.bvid}{ext}")
                success, error = await api.download_cover(video.cover, save_path)

                if success:
                    await db.update_video_cache_cover(video.bvid, save_path)
                    downloaded_count += 1
                    logger.info(f"下载视频封面：{video.title}")

        return ApiResponse(
            success=True,
            message=f"已下载 {downloaded_count} 个封面图片"
        )
    except Exception as e:
        logger.error(f"下载封面失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))
