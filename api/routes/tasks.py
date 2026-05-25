"""
任务队列 + 黑名单 API
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from core.database import Database
from api.models import ApiResponse

router = APIRouter(tags=["任务队列"])


class BlacklistPayload(BaseModel):
    bvid: str
    title: Optional[str] = None
    reason: Optional[str] = None


@router.get("/tasks", response_model=ApiResponse)
async def list_tasks(status: Optional[str] = None, limit: int = 200):
    """列出任务队列。

    status: all（默认）/ queued / downloading / uploading / completed / failed / skipped / cancelling
    """
    try:
        db = await Database.get_instance()
        tasks = await db.get_tasks(status=status, limit=max(1, min(limit, 500)))

        # 合并 download_progress 里的速度/ETA：按 (bvid, current_page) 匹配
        try:
            progress_rows = await db.get_all_progress()
            progress_map = {(p.bvid, p.page): p for p in progress_rows}
            for t in tasks:
                page = t.get("current_page") or 1
                p = progress_map.get((t.get("bvid"), page))
                if p:
                    t["speed"] = p.speed                  # bytes/s
                    t["eta"] = p.eta                      # seconds
                    t["progress_message"] = p.message
                else:
                    t["speed"] = None
                    t["eta"] = None
                    t["progress_message"] = None
        except Exception as e:
            logger.debug(f"合并下载进度失败：{e}")

        return ApiResponse(success=True, message="ok", data=tasks)
    except Exception as e:
        logger.error(f"列出任务失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks/{task_id}/skip", response_model=ApiResponse)
async def skip_task(task_id: int):
    """跳过任务。

    - queued：立即标记为 skipped
    - downloading/uploading：标记为 cancelling，由同步主流程在下个 await 点退出
    """
    try:
        db = await Database.get_instance()
        ok = await db.request_skip_task(task_id)
        if not ok:
            return ApiResponse(success=False, message="任务已结束或不存在")
        return ApiResponse(success=True, message="已请求跳过")
    except Exception as e:
        logger.error(f"跳过任务失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/tasks/finished", response_model=ApiResponse)
async def clear_finished_tasks():
    """清理已完成 / 失败 / 跳过的历史任务。"""
    try:
        db = await Database.get_instance()
        n = await db.clear_finished_tasks()
        return ApiResponse(success=True, message=f"已清理 {n} 条", data={"removed": n})
    except Exception as e:
        logger.error(f"清理任务失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============ 黑名单 ============

@router.get("/blacklist", response_model=ApiResponse)
async def list_blacklist():
    try:
        db = await Database.get_instance()
        items = await db.get_blacklist()
        return ApiResponse(success=True, message="ok", data=items)
    except Exception as e:
        logger.error(f"获取黑名单失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/blacklist", response_model=ApiResponse)
async def add_blacklist(payload: BlacklistPayload):
    try:
        if not payload.bvid:
            return ApiResponse(success=False, message="bvid 不能为空")
        db = await Database.get_instance()
        await db.add_blacklist(payload.bvid, payload.title, payload.reason)
        return ApiResponse(success=True, message="已加入黑名单")
    except Exception as e:
        logger.error(f"加入黑名单失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/blacklist/{bvid}", response_model=ApiResponse)
async def remove_blacklist(bvid: str):
    try:
        db = await Database.get_instance()
        await db.remove_blacklist(bvid)
        return ApiResponse(success=True, message="已移除")
    except Exception as e:
        logger.error(f"移除黑名单失败：{e}")
        raise HTTPException(status_code=500, detail=str(e))
