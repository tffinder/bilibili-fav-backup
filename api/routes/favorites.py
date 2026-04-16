"""
收藏夹管理 API

提供收藏夹列表、选择、添加、删除等接口
"""
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger

from core.config import get_config
from core.database import Database, FavoriteFolder, VideoCache
from services.bilibili_api import BilibiliAPI
from api.models import ApiResponse, FavoriteFolderModel

router = APIRouter(tags=["收藏夹管理"])


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
        return ApiResponse(success=True, message=f"{action}收藏夹 {fav_id}")
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
            existing.updated_at = now
            await db.add_or_update_favorite_folder(existing)
            return ApiResponse(
                success=True,
                message="收藏夹已更新",
                data=existing.to_dict()
            )

        # 创建新的收藏夹记录
        new_folder = FavoriteFolder(
            id=None,
            fav_id=fav_id,
            title=folder_info.get("title", f"收藏夹 {fav_id}"),
            media_count=folder_info.get("media_count", 0),
            cover=folder_info.get("cover", ""),
            cover_local=None,
            selected=False,
            created_at=now,
            updated_at=now
        )

        await db.add_or_update_favorite_folder(new_folder)

        logger.info(f"添加外部收藏夹：{new_folder.title} (ID: {fav_id})")

        return ApiResponse(
            success=True,
            message="收藏夹添加成功",
            data=new_folder.to_dict()
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


@router.get("/user/favorites/{fav_id}/videos", response_model=ApiResponse)
async def get_favorite_folder_videos(fav_id: int, page: int = 1, page_size: int = 50):
    """获取收藏夹内的视频列表（带下载状态）"""
    try:
        api = BilibiliAPI()
        db = await Database.get_instance()

        success, videos, error = await api.get_favorites_list(str(fav_id))

        if not success:
            return ApiResponse(success=False, message=error, data=[])

        # 获取已下载视频的状态映射
        all_downloaded = await db.get_all_videos()
        download_status_map = {}
        for v in all_downloaded:
            download_status_map[v.bvid] = {
                "downloaded": True,
                "s3_uploaded": v.s3_uploaded,
                "quality": v.quality,
                "upload_failed": v.upload_failed
            }

        # 保存到数据库缓存并添加状态
        now = datetime.now().isoformat()
        videos_with_status = []
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

            status = download_status_map.get(bvid, {
                "downloaded": False,
                "s3_uploaded": False,
                "quality": None,
                "upload_failed": False
            })

            videos_with_status.append({
                **video,
                "downloaded": status["downloaded"],
                "s3_uploaded": status["s3_uploaded"],
                "quality": status["quality"],
                "upload_failed": status["upload_failed"]
            })

        # 分页
        total = len(videos_with_status)
        start = (page - 1) * page_size
        end = start + page_size
        paginated = videos_with_status[start:end]

        return ApiResponse(
            success=True,
            message="获取成功",
            data={
                "videos": paginated,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size
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
