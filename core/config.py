"""
配置管理模块
负责加载、验证和管理系统配置
"""
import os
import yaml
from pathlib import Path
from typing import Optional, Any
from pydantic import BaseModel, Field


class BilibiliConfig(BaseModel):
    """B 站配置"""
    cookie: str = ""
    fav_id: str = ""
    check_cookie_interval: int = 3600


class DownloadConfig(BaseModel):
    """下载配置"""
    quality: int = 127
    max_parallel: int = 1
    temp_dir: str = "./temp"
    retry_times: int = 3
    request_delay: float = 1.0


class S3Config(BaseModel):
    """S3 存储配置"""
    enabled: bool = False
    delete_after_upload: bool = False
    endpoint_url: str = ""
    access_key: str = ""
    secret_key: str = ""
    bucket_name: str = ""
    upload_timeout: int = 300
    retry_times: int = 3
    rate_limit: Optional[float] = None
    region_name: str = "us-east-1"


class SchedulerConfig(BaseModel):
    """定时任务配置"""
    enabled: bool = True
    cron: str = "0 2 * * *"
    timezone: str = "Asia/Shanghai"


class NotificationConfig(BaseModel):
    """通知配置"""
    web_enabled: bool = True
    log_enabled: bool = True
    email_enabled: bool = False
    email_smtp_server: str = ""
    email_from: str = ""
    email_to: str = ""
    email_password: str = ""


class DebugConfig(BaseModel):
    """调试配置"""
    enabled: bool = False
    log_level: str = "INFO"
    keep_temp_files: bool = False
    biliup_proxy: Optional[str] = None
    ffmpeg_path: Optional[str] = None  # ffmpeg 可执行文件路径


class Config(BaseModel):
    """主配置类"""
    bilibili: BilibiliConfig = Field(default_factory=BilibiliConfig)
    download: DownloadConfig = Field(default_factory=DownloadConfig)
    s3: S3Config = Field(default_factory=S3Config)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    
    # 项目根目录
    base_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent)
    
    class Config:
        arbitrary_types_allowed = True

    def validate_bilibili_config(self) -> bool:
        """验证 B 站配置是否完整"""
        return bool(self.bilibili.cookie and self.bilibili.fav_id)

    def validate_s3_config(self) -> bool:
        """验证 S3 配置是否完整"""
        return all([
            self.s3.endpoint_url,
            self.s3.access_key,
            self.s3.secret_key,
            self.s3.bucket_name
        ])


class ConfigManager:
    """配置管理器"""
    
    _instance: Optional['ConfigManager'] = None
    _config: Optional[Config] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._config is None:
            self.load_config()
    
    @classmethod
    def get_instance(cls) -> 'ConfigManager':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def load_config(self, config_path: Optional[str] = None) -> Config:
        """
        加载配置文件
        
        Args:
            config_path: 配置文件路径，默认使用 config.yaml
            
        Returns:
            Config: 配置对象
        """
        if config_path is None:
            config_path = self._default_config_path()
        else:
            config_path = Path(config_path)

        # 兼容旧路径（项目根目录 config.yaml）
        legacy_path = Path(__file__).parent.parent / "config.yaml"
        if not config_path.exists() and legacy_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(legacy_path.read_text(encoding='utf-8'), encoding='utf-8')

        if not config_path.exists():
            # 允许首次启动后通过网页进行配置
            self._config = Config()
            self.save_config(str(config_path))
            return self._config
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f)
        
        # 转换为配置对象
        self._config = Config(
            bilibili=BilibiliConfig(**config_data.get('bilibili', {})),
            download=DownloadConfig(**config_data.get('download', {})),
            s3=S3Config(**config_data.get('s3', {})),
            scheduler=SchedulerConfig(**config_data.get('scheduler', {})),
            notification=NotificationConfig(**config_data.get('notification', {})),
            debug=DebugConfig(**config_data.get('debug', {}))
        )
        
        # 处理相对路径
        if not self._config.download.temp_dir.startswith('.'):
            temp_dir = Path(self._config.download.temp_dir)
        else:
            temp_dir = self._config.base_dir / self._config.download.temp_dir.lstrip('./')
        
        # 确保临时目录存在
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 更新为绝对路径
        self._config.download.temp_dir = str(temp_dir)
        
        return self._config
    
    def get_config(self) -> Config:
        """获取配置对象"""
        if self._config is None:
            raise RuntimeError("配置未加载，请先调用 load_config()")
        return self._config
    
    def save_config(self, config_path: Optional[str] = None) -> None:
        """
        保存配置到文件
        
        Args:
            config_path: 配置文件路径，默认使用 config.yaml
        """
        if config_path is None:
            config_path = self._default_config_path()
        else:
            config_path = Path(config_path)
        
        if self._config is None:
            raise RuntimeError("配置未加载")
        
        # 转换为字典
        config_data = {
            'bilibili': self._config.bilibili.model_dump(),
            'download': self._config.download.model_dump(),
            's3': self._config.s3.model_dump(),
            'scheduler': self._config.scheduler.model_dump(),
            'notification': self._config.notification.model_dump(),
            'debug': self._config.debug.model_dump()
        }
        
        # 写回文件
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)

    def _default_config_path(self) -> Path:
        """默认配置文件路径（不在项目根目录）"""
        return Path(__file__).parent.parent / "data" / "config.yaml"
    
    def update_bilibili_cookie(self, cookie: str) -> None:
        """更新 B 站 cookie"""
        if self._config is None:
            raise RuntimeError("配置未加载")
        self._config.bilibili.cookie = cookie
        self.save_config()
    
    def update_fav_id(self, fav_id: str) -> None:
        """更新收藏夹 ID"""
        if self._config is None:
            raise RuntimeError("配置未加载")
        self._config.bilibili.fav_id = fav_id
        self.save_config()
    
    def validate_bilibili_config(self) -> bool:
        """验证 B 站配置是否完整"""
        if self._config is None:
            return False
        return self._config.validate_bilibili_config()
    
    def validate_s3_config(self) -> bool:
        """验证 S3 配置是否完整"""
        if self._config is None:
            return False
        return self._config.validate_s3_config()


# 全局配置实例
def get_config() -> Config:
    """获取全局配置"""
    return ConfigManager.get_instance().get_config()


def load_config(config_path: Optional[str] = None) -> Config:
    """加载配置"""
    return ConfigManager.get_instance().load_config(config_path)
