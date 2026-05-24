"""
数据模型定义

使用 dataclass 定义数据库表结构
"""
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any


@dataclass
class Video:
    """视频数据模型"""
    bvid: str  # B 站视频 ID
    title: str  # 标题
    cid: str  # 分 P ID
    page: int  # 分 P 序号 (从 1 开始)
    total_pages: int  # 总分数
    quality: int  # 清晰度代码
    duration: int  # 时长 (秒)
    pubdate: int  # 发布时间戳
    owner_name: str  # 上传者
    s3_key: Optional[str]  # S3 存储键名
    s3_uploaded: bool  # 是否已上传到 S3
    s3_quality: Optional[int]  # S3 中存储的清晰度
    local_path: Optional[str]  # 本地路径
    created_at: str  # 创建时间
    updated_at: str  # 更新时间
    id: Optional[int] = None  # 自增 ID
    max_quality: Optional[int] = None  # 视频最高可用分辨率
    upload_failed: bool = False  # 上传是否失败
    fav_id: Optional[int] = None  # 收藏夹 ID
    fav_title: Optional[str] = None  # 收藏夹名称
    fav_time: Optional[int] = None  # 收藏时间戳
    up_mid: Optional[int] = None  # UP 主 UID（UP 主同步来源）
    up_name: Optional[str] = None  # UP 主名称
    source_available: bool = True  # B 站源视频是否仍可访问
    source_status: str = "unknown"  # unknown, available, deleted, check_failed
    source_checked_at: Optional[str] = None  # 最近检测时间
    source_deleted_at: Optional[str] = None  # 首次发现失效时间
    source_error: Optional[str] = None  # 最近检测错误

    def __post_init__(self) -> None:
        """Normalize SQLite integer flags to booleans."""
        self.s3_uploaded = bool(self.s3_uploaded)
        self.upload_failed = bool(self.upload_failed)
        self.source_available = bool(self.source_available)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Video':
        """从字典创建 Video 对象"""
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)


@dataclass
class SyncHistory:
    """同步历史记录"""
    id: Optional[int]
    fav_id: str  # 收藏夹 ID
    total_videos: int  # 总视频数
    new_videos: int  # 新增视频数
    updated_videos: int  # 更新视频数
    skipped_videos: int  # 跳过视频数
    failed_videos: int  # 失败视频数
    start_time: str  # 开始时间
    end_time: Optional[str]  # 结束时间
    status: str  # running, completed, failed
    error_message: Optional[str]  # 错误信息

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SyncHistory':
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Notification:
    """通知消息"""
    id: Optional[int]
    level: str  # info, warning, error
    title: str  # 标题
    message: str  # 消息内容
    created_at: str  # 创建时间
    is_read: bool  # 是否已读

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Notification':
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DownloadProgress:
    """下载进度"""
    id: Optional[int]
    bvid: str  # 视频 ID
    title: str  # 标题
    page: int  # 分 P
    progress: float  # 进度 0-100
    speed: Optional[float]  # 速度 (KB/s)
    eta: Optional[int]  # 预计剩余时间 (秒)
    status: str  # pending, downloading, uploading, completed, failed
    message: Optional[str]  # 状态消息
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DownloadProgress':
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FavoriteFolder:
    """收藏夹元数据"""
    id: Optional[int]
    fav_id: int  # 收藏夹 ID
    title: str  # 标题
    media_count: int  # 视频数量
    cover: str  # 封面 URL
    cover_local: Optional[str]  # 本地封面路径
    selected: bool  # 是否选中用于定时下载
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FavoriteFolder':
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VideoCache:
    """视频元数据缓存"""
    id: Optional[int]
    bvid: str  # 视频 ID
    title: str  # 标题
    cover: str  # 封面 URL
    cover_local: Optional[str]  # 本地封面路径
    duration: int  # 时长（秒）
    owner_name: str  # 上传者
    source_type: str  # 来源类型: 'favorite', 'watch_later', 'history'
    source_id: Optional[int]  # 来源 ID（收藏夹 ID 等）
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VideoCache':
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
