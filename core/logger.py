"""
日志配置模块
提供统一的日志配置和管理功能
"""
import sys
from pathlib import Path
from typing import Optional
from loguru import logger


class LogLevel:
    """日志级别常量"""
    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @classmethod
    def valid_levels(cls) -> list:
        """返回所有有效的日志级别"""
        return [cls.TRACE, cls.DEBUG, cls.INFO, cls.SUCCESS,
                cls.WARNING, cls.ERROR, cls.CRITICAL]


class LogConfig:
    """日志配置"""

    def __init__(
        self,
        level: str = "INFO",
        log_dir: str = "./logs",
        console_enabled: bool = True,
        file_enabled: bool = True,
        rotation: str = "00:00",
        retention: str = "7 days",
        compression: str = "zip",
        format_console: Optional[str] = None,
        format_file: Optional[str] = None
    ):
        self.level = level.upper()
        self.log_dir = Path(log_dir)
        self.console_enabled = console_enabled
        self.file_enabled = file_enabled
        self.rotation = rotation
        self.retention = retention
        self.compression = compression
        self.format_console = format_console or (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        )
        self.format_file = format_file or (
            "{time:YYYY-MM-DD HH:mm:ss} | "
            "{level: <8} | "
            "{name}:{function}:{line} - "
            "{message}"
        )

    @classmethod
    def from_config(cls, config) -> 'LogConfig':
        """从配置对象创建日志配置"""
        return cls(
            level=config.debug.log_level if hasattr(config, 'debug') else "INFO",
            log_dir=str(Path(__file__).parent.parent / "logs"),
            console_enabled=True,
            file_enabled=True
        )


class LoggerManager:
    """日志管理器"""

    _initialized: bool = False
    _current_level: str = "INFO"

    @classmethod
    def setup(cls, config: LogConfig) -> None:
        """
        配置日志系统

        Args:
            config: 日志配置对象
        """
        # 确保日志目录存在
        config.log_dir.mkdir(parents=True, exist_ok=True)

        # 验证日志级别
        if config.level not in LogLevel.valid_levels():
            logger.warning(f"无效的日志级别 '{config.level}'，使用默认值 'INFO'")
            config.level = "INFO"

        # 移除所有现有处理器
        logger.remove()

        # 控制台输出
        if config.console_enabled:
            logger.add(
                sys.stderr,
                level=config.level,
                format=config.format_console,
                colorize=True,
                enqueue=True  # 线程安全
            )

        # 文件输出
        if config.file_enabled:
            logger.add(
                str(config.log_dir / "app_{time:YYYY-MM-DD}.log"),
                level=config.level,
                rotation=config.rotation,
                retention=config.retention,
                compression=config.compression,
                format=config.format_file,
                enqueue=True,  # 线程安全
                encoding="utf-8"
            )

        cls._initialized = True
        cls._current_level = config.level

        logger.success(f"日志系统初始化完成 [级别: {config.level}]")

    @classmethod
    def get_level(cls) -> str:
        """获取当前日志级别"""
        return cls._current_level

    @classmethod
    def set_level(cls, level: str) -> bool:
        """
        动态设置日志级别

        Args:
            level: 新的日志级别

        Returns:
            bool: 是否设置成功
        """
        level = level.upper()
        if level not in LogLevel.valid_levels():
            logger.warning(f"无效的日志级别 '{level}'")
            return False

        # 需要重新配置处理器才能改变级别
        # 这里只是记录新的级别，实际应用需要重新 setup
        cls._current_level = level
        logger.info(f"日志级别已设置为: {level}")
        return True

    @classmethod
    def is_initialized(cls) -> bool:
        """检查日志系统是否已初始化"""
        return cls._initialized


def setup_logging(config=None) -> None:
    """
    配置日志系统的便捷函数

    Args:
        config: 可选的配置对象，如果提供则从中读取日志级别
    """
    if config is not None:
        log_config = LogConfig.from_config(config)
    else:
        log_config = LogConfig()

    LoggerManager.setup(log_config)


def get_logger(name: str = None):
    """
    获取带有模块名称的 logger

    Args:
        name: 模块名称，通常使用 __name__

    Returns:
        logger 实例
    """
    if name:
        return logger.bind(name=name)
    return logger
