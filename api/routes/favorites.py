"""
收藏夹管理 API

提供收藏夹列表、选择、添加、删除等接口
"""
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger

from core.config import get_config
from core.database import Database, FavoriteFolder, VideoCache
from services.bilibili_api import BilibiliAPI
from api.models import ApiResponse, FavoriteFolderModel

router = APIRouter(tags=["收藏夹管理"])


async def _start_sync_when_idle(sync_manager, poll_interval: float = 5.0) -> None:
    """等待当前同步结束后启动下一轮，确保新选收藏夹不漏掉。"""
    while sync_manager.is_syncing:
        await asyncio.sleep(poll_interval)

    await sync_manager.start_sync(manual=False)


def _trigger_auto_sync() -> Tuple[bool, str]:
    """在收藏夹纳入备份后尝试启动一次后台同步。"""
    try:
        from api.routes.sync import _sync_manager
    except Exception as e:
        logger.debug(f"无法获取同步服务实例：{e}")
        return False, "同步服务未初始化"

    if not _sync_manager:
        return False, "同步服务未初始化"

    if _sync_manager.is_syncing:
        asyncio.create_task(_start_sync_when_idle(_sync_manager))
        return False, "同步任务已在运行，已排队稍后同步"

    asyncio.create_task(_sync_manager.start_sync(manual=False))
    return True, "已自动启动同步"


@router.get("/favorites", response_model=ApiResponse)
async def get_favorite_folders():
    """获取当前登录用户的视频收藏夹列表"""
    try:
        api = BilibiliAPI()
        success, data, message = await api.get_favorite_folders()
        if success:
            folders = [FavoriteFolderModel(**item).model_dump() for item in data]
            return ApiResponse(success=True, message=message or "ok", data=folders)
        return ApiResponse(success=False, message=message, data=[])
    except Exception as e:
        logger.error(f"获取收藏夹列表失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/favorites", response_model=ApiResponse)
async def get_user_favorites():
    """获取用户所有收藏夹（带选中状态，包括自己添加的外部收藏夹）"""
    try:
        api = BilibiliAPI()
        db = await Database.get_instance()

        # 从数据库获取已保存的所有收藏夹
        saved_folders = await db.get_all_favorite_folders()
        saved_map = {f.fav_id: f for f in saved_folders}

        # 获取所有收藏夹的视频缓存数量
        cache_count_map = await db.get_video_cache_count_map()

        # 从 B 站 API 获取自己的收藏夹列表
        success, folders, error = await api.get_favorite_folders()

        now = datetime.now().isoformat()
        result = []
        processed_ids = set()

        # 处理 B 站 API 返回的收藏夹
        if success:
            for folder in folders:
                fav_id = folder["id"]
                processed_ids.add(fav_id)
                saved = saved_map.get(fav_id)

                # 更新或创建收藏夹记录
                db_folder = FavoriteFolder(
                    id=saved.id if saved else None,
                    fav_id=fav_id,
                    title=folder["title"],
                    media_count=folder.get("media_count", 0),
                    cover=folder.get("cover", ""),
                    cover_local=saved.cover_local if saved else None,
                    selected=saved.selected if saved else folder.get("selected", False),
                    created_at=saved.created_at if saved else now,
                    updated_at=now
                )
                await db.add_or_update_favorite_folder(db_folder)

                result.append({
                    "id": fav_id,
                    "title": folder["title"],
                    "media_count": folder.get("media_count", 0),
                    "cache_count": cache_count_map.get(fav_id, 0),
                    "cover": folder.get("cover", ""),
                    "cover_local": db_folder.cover_local,
                    "selected": db_folder.selected,
                    "is_external": False
                })

        # 添加数据库中存在但 B 站 API 没返回的收藏夹（外部添加的）
        for saved in saved_folders:
            if saved.fav_id not in processed_ids:
                result.append({
                    "id": saved.fav_id,
                    "title": saved.title,
                    "media_count": saved.media_count,
                    "cache_count": cache_count_map.get(saved.fav_id, 0),
                    "cover": saved.cover,
                    "cover_local": saved.cover_local,
                    "selected": saved.selected,
                    "is_external": True
                })

        return ApiResponse(success=True, message="获取成功", data=result)
    except Exception as e:
        logger.error(f"获取收藏夹列表失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/favorites/{fav_id}/select", response_model=ApiResponse)
async def select_favorite_folder(fav_id: int, selected: bool = True):
    """选择/取消选择收藏夹用于定时下载"""
    try:
        db = await Database.get_instance()
        await db.set_favorite_folder_selected(fav_id, selected)

        action = "已选择" if selected else "已取消选择"
        sync_started = False
        sync_message = None
        if selected:
            sync_started, sync_message = _trigger_auto_sync()

        message = f"{action}收藏夹 {fav_id}"
        if selected and sync_message:
            message = f"{message}，{sync_message}"

        return ApiResponse(
            success=True,
            message=message,
            data={"sync_started": sync_started}
        )
    except Exception as e:
        logger.error(f"设置收藏夹选中状态失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/favorites/selected", response_model=ApiResponse)
async def get_selected_favorites():
    """获取已选择的收藏夹列表"""
    try:
        db = await Database.get_instance()
        folders = await db.get_selected_favorite_folders()

        return ApiResponse(
            success=True,
            message="获取成功",
            data=[f.to_dict() for f in folders]
        )
    except Exception as e:
        logger.error(f"获取已选收藏夹失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/favorites/external", response_model=ApiResponse)
async def add_external_favorite_folder(fav_id: int):
    """添加其他用户的收藏夹"""
    try:
        api = BilibiliAPI()
        db = await Database.get_instance()

        # 获取收藏夹信息
        success, folder_info, error = await api.get_favorite_folder_info(fav_id)

        if not success:
            return ApiResponse(success=False, message=error, data=None)

        # 检查是否已存在
        existing = await db.get_favorite_folder(fav_id)
        now = datetime.now().isoformat()

        if existing:
            existing.title = folder_info.get("title", existing.title)
            existing.media_count = folder_info.get("media_count", existing.media_count)
            existing.cover = folder_info.get("cover", existing.cover)
            existing.selected = True
            existing.updated_at = now
            await db.add_or_update_favorite_folder(existing)
            sync_started, sync_message = _trigger_auto_sync()
            return ApiResponse(
                success=True,
                message=f"收藏夹已更新并加入备份，{sync_message}",
                data={**existing.to_dict(), "sync_started": sync_started}
            )

        # 创建新的收藏夹记录
        new_folder = FavoriteFolder(
            id=None,
            fav_id=fav_id,
            title=folder_info.get("title", f"收藏夹 {fav_id}"),
            media_count=folder_info.get("media_count", 0),
            cover=folder_info.get("cover", ""),
            cover_local=None,
            selected=True,
            created_at=now,
            updated_at=now
        )

        await db.add_or_update_favorite_folder(new_folder)
        sync_started, sync_message = _trigger_auto_sync()

        logger.info(f"添加外部收藏夹：{new_folder.title} (ID: {fav_id})")

        return ApiResponse(
            success=True,
            message=f"收藏夹添加成功并加入备份，{sync_message}",
            data={**new_folder.to_dict(), "sync_started": sync_started}
        )

    except Exception as e:
        logger.error(f"添加外部收藏夹失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/user/favorites/{fav_id}", response_model=ApiResponse)
async def remove_favorite_folder(fav_id: int):
    """移除收藏夹（仅从本地数据库移除）"""
    try:
        db = await Database.get_instance()

        existing = await db.get_favorite_folder(fav_id)
        if not existing:
            return ApiResponse(success=False, message="收藏夹不存在", data=None)

        await db.delete_favorite_folder(fav_id)

        return ApiResponse(
            success=True,
            message=f"已移除收藏夹：{existing.title}"
        )

    except Exception as e:
        logger.error(f"移除收藏夹失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


def _paginate_items(items: list, page: int, page_size: int) -> tuple:
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    total_pages = (total + page_size - 1) // page_size if page_size else 1
    return items[start:end], total, total_pages


@router.get("/user/favorites/{fav_id}/videos", response_model=ApiResponse)
async def get_favorite_folder_videos(
    fav_id: int,
    page: int = 1,
    page_size: int = 50,
    force_refresh: bool = False,
    q: Optional[str] = None,
    status: Optional[str] = None
):
    """获取收藏夹内的视频列表（默认读本地缓存，force_refresh=true 时刷新 B 站数据）

    Args:
        q: 搜索关键字（标题/UP 主，大小写不敏感）
        status: 过滤项 — all/uploaded/downloaded/pending/failed/source_deleted
    """
    try:
        api = BilibiliAPI()
        db = await Database.get_instance()

        # 按 bvid 聚合下载/上传/源状态信息
        all_downloaded = await db.get_all_videos()
        download_status_map: Dict[int, Dict[str, object]] = {}
        for v in all_downloaded:
            current = download_status_map.get(v.bvid)
            # 多 P 时取最优状态：任意 P 已上传则视为已上传
            if current is None:
                download_status_map[v.bvid] = {
                    "video_id": v.id,
                    "downloaded": bool(v.local_path) or bool(v.s3_uploaded),
                    "s3_uploaded": bool(v.s3_uploaded),
                    "s3_key": v.s3_key,
                    "quality": v.quality,
                    "upload_failed": bool(v.upload_failed),
                    "local_path": v.local_path,
                    "source_available": bool(v.source_available),
                    "source_status": v.source_status or "unknown",
                }
            else:
                if v.s3_uploaded:
                    current["s3_uploaded"] = True
                    current["s3_key"] = v.s3_key
                if v.local_path:
                    current["downloaded"] = True
                    current["local_path"] = v.local_path
                # source 字段在所有 P 都一样，保留最早写入的
                if current.get("source_status") == "unknown" and v.source_status:
                    current["source_status"] = v.source_status
                    current["source_available"] = bool(v.source_available)

        def _hydrate(bvid: str) -> Dict[str, object]:
            status_info = download_status_map.get(bvid, {})
            video_id = status_info.get("video_id")
            local_path = status_info.get("local_path")
            s3_key = status_info.get("s3_key") or ""
            s3_uploaded = bool(status_info.get("s3_uploaded"))
            is_local_only = isinstance(s3_key, str) and s3_key.startswith("local://")

            # 本地文件是否还真的存在（防止 delete_after_upload 后残留 DB 字段）
            local_exists = False
            if local_path:
                try:
                    local_exists = Path(local_path).exists()
                except Exception:
                    local_exists = False

            # 选择最佳来源：本地优先 → S3 → 无
            play_url = None
            download_url = None
            if video_id:
                if local_exists or is_local_only and local_exists:
                    play_url = f"/api/videos/{video_id}/file"
                    download_url = f"/api/videos/{video_id}/file?inline=0"
                elif s3_uploaded and not is_local_only:
                    play_url = f"/api/videos/{video_id}/remote-download"
                    download_url = f"/api/videos/{video_id}/remote-download?inline=0"

            return {
                "video_id": video_id,
                # downloaded：只要本地或 S3 任一可用就算备份成功
                "downloaded": bool(local_exists or (s3_uploaded and not is_local_only) or is_local_only),
                "s3_uploaded": s3_uploaded and not is_local_only,
                "quality": status_info.get("quality"),
                "upload_failed": status_info.get("upload_failed", False),
                "source_available": status_info.get("source_available", True),
                "source_status": status_info.get("source_status", "unknown"),
                "play_url": play_url,
                "download_url": download_url,
            }

        videos_with_status = []

        cached_videos = [] if force_refresh else await db.get_videos_by_source("favorite", fav_id)
        if cached_videos:
            for video in cached_videos:
                videos_with_status.append({
                    "bvid": video.bvid,
                    "title": video.title,
                    "cover": video.cover,
                    "duration": video.duration,
                    "owner": video.owner_name,
                    "owner_name": video.owner_name,
                    "updated_at": video.updated_at,
                    **_hydrate(video.bvid)
                })
            source_label = "cache"
            message = "from cache"
        else:
            success, videos, error = await api.get_favorites_list(str(fav_id))

            if not success:
                return ApiResponse(success=False, message=error, data=[])

            # 保存到数据库缓存并组装结果
            now = datetime.now().isoformat()
            await db.clear_video_cache_by_source("favorite", fav_id)
            for video in videos:
                bvid = video["bvid"]
                cache = VideoCache(
                    id=None,
                    bvid=bvid,
                    title=video["title"],
                    cover=video.get("cover", ""),
                    cover_local=None,
                    duration=video.get("duration", 0),
                    owner_name=video.get("owner", ""),
                    source_type="favorite",
                    source_id=fav_id,
                    created_at=now,
                    updated_at=now
                )
                await db.add_or_update_video_cache(cache)
                videos_with_status.append({**video, **_hydrate(bvid)})
            source_label = "api"
            message = "from api"

        # 应用搜索
        if q:
            keyword = q.strip().lower()
            if keyword:
                videos_with_status = [
                    v for v in videos_with_status
                    if keyword in (v.get("title") or "").lower()
                    or keyword in (v.get("owner") or v.get("owner_name") or "").lower()
                ]

        # 应用状态过滤
        if status and status != "all":
            def _match(v: Dict[str, object]) -> bool:
                if status == "uploaded":
                    return bool(v.get("s3_uploaded"))
                if status == "downloaded":
                    return bool(v.get("downloaded"))
                if status == "pending":
                    return not v.get("downloaded") and not v.get("upload_failed")
                if status == "failed":
                    return bool(v.get("upload_failed"))
                if status == "source_deleted":
                    # 已备份且 B 站源失效
                    return bool(v.get("downloaded")) and v.get("source_status") == "deleted"
                return True
            videos_with_status = [v for v in videos_with_status if _match(v)]

        paginated, total, total_pages = _paginate_items(videos_with_status, page, page_size)

        return ApiResponse(
            success=True,
            message=message,
            data={
                "videos": paginated,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "source": source_label
            }
        )
    except Exception as e:
        logger.error(f"获取收藏夹内容失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/covers/folder/{fav_id}")
async def get_folder_cover(fav_id: int):
    """获取收藏夹封面图片"""
    try:
        config = get_config()
        base_dir = Path(config.download.temp_dir).parent
        covers_dir = base_dir / "covers" / "favorites"

        for ext in ['.jpg', '.png', '.webp', '.jpeg']:
            path = covers_dir / f"{fav_id}{ext}"
            if path.exists():
                return FileResponse(
                    str(path),
                    media_type="image/jpeg",
                    filename=f"{fav_id}{ext}"
                )

        raise HTTPException(status_code=404, detail="封面不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取收藏夹封面失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))
