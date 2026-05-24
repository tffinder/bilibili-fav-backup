"""
UP 主视频同步 API

提供 UP 主同步目标管理、手动触发同步等接口
"""
import asyncio
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from core.config import get_config, ConfigManager, UpSyncTarget
from core.database import Database
from api.models import ApiResponse

router = APIRouter(prefix="/up-sync", tags=["UP主同步"])


class AddUpSyncTargetRequest(BaseModel):
    mid: int
    name: Optional[str] = None


class UpSyncTargetResponse(BaseModel):
    mid: int
    name: str = ""
    enabled: bool = True
    video_count: int = 0


@router.get("/targets")
async def list_targets():
    """列出所有 UP 主同步目标"""
    config = get_config()
    db = await Database.get_instance()

    targets = []
    for t in config.up_sync.targets:
        count = len(await db.get_videos_by_source("uploader", t.mid))
        targets.append({
            "mid": t.mid,
            "name": t.name,
            "enabled": t.enabled,
            "video_count": count,
        })

    return ApiResponse(
        success=True,
        message=f"共 {len(targets)} 个目标",
        data=targets
    )


@router.post("/targets")
async def add_target(req: AddUpSyncTargetRequest):
    """添加 UP 主同步目标"""
    config = get_config()
    config_manager = ConfigManager.get_instance()

    for t in config.up_sync.targets:
        if t.mid == req.mid:
            return ApiResponse(success=False, message=f"UP 主 {req.mid} 已存在")

    name = req.name or ""
    if not name:
        try:
            from services.bilibili_api import BilibiliAPI
            api = BilibiliAPI()
            ok, info, err = await api.get_uploader_info(req.mid)
            if ok:
                name = info.get("name", "")
        except Exception as e:
            logger.warning(f"获取 UP 主 {req.mid} 信息失败：{e}")

    if not name:
        name = f"UP_{req.mid}"

    config.up_sync.targets.append(UpSyncTarget(mid=req.mid, name=name, enabled=True))
    config_manager.save_config()

    return ApiResponse(success=True, message=f"已添加 UP 主：{name} ({req.mid})")


@router.delete("/targets/{mid}")
async def remove_target(mid: int):
    """删除 UP 主同步目标"""
    config = get_config()
    config_manager = ConfigManager.get_instance()

    original_len = len(config.up_sync.targets)
    config.up_sync.targets = [t for t in config.up_sync.targets if t.mid != mid]

    if len(config.up_sync.targets) == original_len:
        return ApiResponse(success=False, message=f"UP 主 {mid} 不存在")

    config_manager.save_config()
    return ApiResponse(success=True, message=f"已删除 UP 主 {mid}")


@router.post("/targets/{mid}/toggle")
async def toggle_target(mid: int):
    """切换 UP 主启用/禁用状态"""
    config = get_config()
    config_manager = ConfigManager.get_instance()

    for t in config.up_sync.targets:
        if t.mid == mid:
            t.enabled = not t.enabled
            config_manager.save_config()
            status = "启用" if t.enabled else "禁用"
            return ApiResponse(success=True, message=f"UP 主 {t.name} 已{status}")

    return ApiResponse(success=False, message=f"UP 主 {mid} 不存在")


@router.post("/start")
async def start_up_sync():
    """手动触发 UP 主视频同步"""
    from api.routes.sync import _sync_manager

    if not _sync_manager:
        raise HTTPException(status_code=500, detail="同步管理器未初始化")

    if _sync_manager.is_syncing:
        return ApiResponse(success=False, message="同步任务正在进行中")

    asyncio.create_task(_sync_manager.start_up_sync(manual=True))
    return ApiResponse(success=True, message="UP 主同步已开始")


@router.get("/targets/{mid}/videos")
async def get_target_videos(mid: int):
    """获取指定 UP 主的缓存视频列表"""
    db = await Database.get_instance()
    videos = await db.get_videos_by_source("uploader", mid)
    return ApiResponse(
        success=True,
        message=f"共 {len(videos)} 个视频",
        data=[v.to_dict() for v in videos]
    )
