"""
B 站收藏夹视频备份系统 - 主入口
"""
import asyncio
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from loguru import logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from core.config import load_config, get_config, ConfigManager
from core.logger import setup_logging, LoggerManager
from core.database import Database
from services.bilibili_api import BilibiliAPI
from services.sync_manager import SyncManager
from services.scheduler import TaskScheduler
from api.routes import router, init_services


# 配置日志
def setup_logging_from_config():
    """从配置文件配置日志输出"""
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)

    # 加载配置以获取日志级别
    try:
        config = get_config()
        log_level = config.logging.level.upper()
        console_enabled = config.logging.console_enabled
        file_enabled = config.logging.file_enabled
        rotation = config.logging.rotation
        retention = config.logging.retention
        compression = config.logging.compression
    except RuntimeError:
        # 配置未加载时使用默认值
        log_level = "INFO"
        console_enabled = True
        file_enabled = True
        rotation = "00:00"
        retention = "7 days"
        compression = "zip"

    # 移除默认处理器
    logger.remove()

    # 控制台输出
    if console_enabled:
        logger.add(
            sys.stderr,
            level=log_level,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            colorize=True
        )

    # 文件输出
    if file_enabled:
        logger.add(
            str(log_dir / "app_{time:YYYY-MM-DD}.log"),
            level=log_level,
            rotation=rotation,
            retention=retention,
            compression=compression,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
        )

    logger.info(f"日志系统初始化完成 [级别: {log_level}]")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("正在启动应用...")

    # 初始化变量（确保 finally 块安全执行）
    db = None
    scheduler = None

    try:
        # 加载配置
        config = load_config()
        logger.info("配置加载完成")

        # 初始化数据库
        db = await Database.get_instance()
        logger.info("数据库初始化完成")

        # 清理上次进程残留的运行中任务
        try:
            orphans = await db.reset_orphan_tasks()
            if orphans:
                logger.info(f"清理上次未结束的任务 {orphans} 条")
        except Exception as e:
            logger.warning(f"清理残留任务失败：{e}")

        # 初始化服务
        sync_manager = SyncManager()
        scheduler = create_scheduler(sync_manager)

        # 注入服务到 API
        init_services(sync_manager, scheduler)
        logger.info("服务初始化完成")

        # 启动定时任务
        if config.scheduler.enabled:
            scheduler.start()
            logger.info("定时任务已启动")

        yield

    except Exception as e:
        logger.error(f"启动失败：{e}")
        raise

    finally:
        # 关闭时执行
        logger.info("正在关闭应用...")

        # 停止调度器
        if scheduler is not None:
            scheduler.stop()

        # 关闭数据库
        if db is not None:
            await db.close()

        logger.info("应用已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="B 站收藏夹备份系统",
    description="自动备份 B 站收藏夹视频到 S3 存储",
    version="1.0.0",
    lifespan=lifespan
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(router)

# 挂载静态文件（前端）
frontend_dir = project_root / "frontend"


@app.get("/")
async def root():
    """根路径，返回前端页面"""
    index_file = frontend_dir / "index.html"

    if index_file.exists():
        return FileResponse(str(index_file))

    return {
        "message": "B 站收藏夹备份系统 API",
        "docs": "/docs",
        "status": "/api/status"
    }


def create_scheduler(sync_manager: SyncManager) -> TaskScheduler:
    """创建调度器"""
    scheduler = TaskScheduler.get_instance()
    scheduler.init(sync_manager)
    return scheduler


def main():
    """主函数"""
    import uvicorn

    # 加载配置
    config = load_config()

    # 设置日志（使用配置中的日志级别）
    setup_logging_from_config()

    logger.info("=" * 50)
    logger.info("B 站收藏夹备份系统 v1.0.0")
    logger.info(f"日志级别: {config.logging.level}")
    logger.info("=" * 50)

    # 启动服务器
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )


if __name__ == "__main__":
    main()
