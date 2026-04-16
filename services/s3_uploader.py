"""
S3 上传模块
负责将视频上传到 S3 兼容存储（OpenList）
"""
import asyncio
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, Callable, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor
from loguru import logger

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from botocore.config import Config

from core.config import get_config
from core.database import Database, Video, DownloadProgress


class S3Uploader:
    """S3 上传器"""
    
    def __init__(self):
        self.config = get_config()
        self.db: Optional[Database] = None
        self.s3_client = None
        self.bucket_name = self.config.s3.bucket_name
        self.upload_timeout = self.config.s3.upload_timeout
        self.retry_times = self.config.s3.retry_times
        self.rate_limit = self.config.s3.rate_limit
        
        # 延迟初始化 S3 客户端（避免启动时配置为空报错）
        # self._init_s3_client()
        
        # 进度回调
        self.progress_callback: Optional[Callable[[DownloadProgress], None]] = None
    
    def ensure_s3_initialized(self) -> None:
        """确保 S3 客户端已初始化"""
        if self.s3_client is None:
            self._init_s3_client()
    
    def _init_s3_client(self) -> None:
        """初始化 S3 客户端"""
        try:
            s3_config = self.config.s3
            
            # 检查配置是否为空
            if not s3_config.endpoint_url:
                logger.warning("S3 endpoint_url 未配置，跳过初始化")
                return
            
            # boto3 配置
            botocore_config = Config(
                connect_timeout=10,
                read_timeout=s3_config.upload_timeout,
                retries={'max_attempts': 3}
            )
            
            self.s3_client = boto3.client(
                's3',
                endpoint_url=s3_config.endpoint_url,
                aws_access_key_id=s3_config.access_key,
                aws_secret_access_key=s3_config.secret_key,
                region_name=s3_config.region_name or 'us-east-1',
                config=botocore_config
            )
            
            logger.info("S3 客户端初始化成功")
            
        except Exception as e:
            logger.error(f"S3 客户端初始化失败：{e}")
            # 不抛出异常，允许程序继续运行
    
    async def init_db(self) -> None:
        """初始化数据库连接"""
        self.db = await Database.get_instance()
    
    def set_progress_callback(
        self, callback: Callable[[DownloadProgress], None]
    ) -> None:
        """设置进度回调函数"""
        self.progress_callback = callback
    
    @staticmethod
    def normalize_filename(filename: str, max_length: int = 150) -> str:
        """
        规范化文件名
        
        Args:
            filename: 原始文件名
            max_length: 最大长度（不含扩展名）
            
        Returns:
            规范化后的文件名
        """
        # 分离文件名和扩展名
        name, ext = os.path.splitext(filename)
        
        # 移除非法字符 \ / : * ? " < > |
        name = re.sub(r'[\\/:*?"<>|]', '_', name)
        
        # 截断到指定长度
        if len(name) > max_length:
            name = name[:max_length]
        
        # 清理首尾空格和下划线
        name = name.strip('_').strip()
        
        # 确保扩展名为 .mp4
        ext = ext.lower()
        if ext not in ['.mp4', '.flv', '.mkv']:
            ext = '.mp4'
        
        return f"{name}{ext}"
    
    def generate_s3_key(self, video: Video, page_title: str = "") -> str:
        """
        生成 S3 存储键名

        格式：视频文件夹/年月/BV号_视频名称.mp4

        Args:
            video: 视频对象
            page_title: 分 P 标题

        Returns:
            S3 key (路径格式)
        """
        # 规范化文件名（normalize_filename 会自动添加 .mp4 后缀）
        safe_title = self.normalize_filename(video.title, max_length=100)
        # 移除 .mp4 后缀，后面会统一添加
        if safe_title.endswith('.mp4'):
            safe_title = safe_title[:-4]

        # 获取收藏夹名称作为文件夹名（如果没有则使用默认值）
        folder_name = video.fav_title or "未分类"
        # 移除文件夹名称中的非法字符
        folder_name = re.sub(r'[\\/:*?"<>|]', '_', folder_name).strip()
        # 限制文件夹名称长度
        if len(folder_name) > 50:
            folder_name = folder_name[:50]

        # 获取年月（格式：YYYY-MM）
        if video.fav_time:
            year_month = datetime.fromtimestamp(video.fav_time).strftime("%Y-%m")
        else:
            year_month = datetime.now().strftime("%Y-%m")

        # 构建文件名：BV号_视频名称
        # 如果是多 P 视频，添加分 P 信息
        if video.total_pages > 1:
            filename = f"{video.bvid}_{safe_title}_P{video.page:02d}.mp4"
        else:
            filename = f"{video.bvid}_{safe_title}.mp4"

        # 最终路径：视频文件夹/年月/BV号_视频名称.mp4
        key = f"{folder_name}/{year_month}/{filename}"

        return key
    
    async def _update_progress(
        self,
        bvid: str,
        title: str,
        page: int,
        progress: float,
        status: str = "uploading",
        message: Optional[str] = None
    ) -> None:
        """更新上传进度"""
        if not self.db:
            await self.init_db()
        
        now = datetime.now().isoformat()
        
        progress_obj = DownloadProgress(
            id=None,
            bvid=bvid,
            title=title,
            page=page,
            progress=progress,
            speed=None,
            eta=None,
            status=status,
            message=message,
            created_at=now,
            updated_at=now
        )
        
        await self.db.update_download_progress(progress_obj)
        
        # 调用回调函数
        if self.progress_callback:
            self.progress_callback(progress_obj)
    
    def _upload_file_sync(
        self, 
        local_path: str, 
        s3_key: str,
        callback: Optional[Callable[[int], None]] = None
    ) -> bool:
        """
        同步上传文件（在后台线程中运行）
        
        Args:
            local_path: 本地文件路径
            s3_key: S3 键名
            callback: 进度回调函数 (bytes_transferred)
            
        Returns:
            是否成功
        """
        try:
            file_size = Path(local_path).stat().st_size
            
            # 创建 ProgressCallback 类实例
            class ProgressCallback:
                def __init__(self, total_size, cb):
                    self.total_size = total_size
                    self.cb = cb
                    self.seen_so_far = 0
                
                def __call__(self, bytes_amount):
                    self.seen_so_far += bytes_amount
                    if self.cb:
                        self.cb(self.seen_so_far)
            
            # 上传文件
            self.s3_client.upload_file(
                local_path,
                self.bucket_name,
                s3_key,
                ExtraArgs={
                    'ContentType': 'video/mp4',
                    'ACL': 'private'  # 私有访问
                },
                Callback=ProgressCallback(file_size, callback)
            )
            
            logger.info(f"上传成功：{s3_key}")
            return True
            
        except NoCredentialsError:
            logger.error("S3 凭证无效")
            raise
        except ClientError as e:
            error_code = e.response['Error']['Code']
            logger.error(f"S3 上传错误 [{error_code}]: {e}")
            raise
        except Exception as e:
            logger.error(f"上传异常：{e}")
            raise
    
    async def upload_video(
        self,
        video: Video,
        local_path: str,
        delete_after_upload: bool = True
    ) -> Tuple[bool, Optional[str], str]:
        """
        上传视频到 S3
        
        Args:
            video: 视频对象
            local_path: 本地文件路径
            delete_after_upload: 上传成功后是否删除本地文件
            
        Returns:
            (success, s3_key, error_message)
        """
        # 确保 S3 已初始化
        self.ensure_s3_initialized()
        
        if not self.s3_client:
            return False, None, "S3 未配置，请先在系统配置中填写 S3 信息"
        
        if not self.db:
            await self.init_db()
        
        # 检查文件是否存在
        if not Path(local_path).exists():
            return False, None, f"文件不存在：{local_path}"
        
        # 生成 S3 key
        s3_key = self.generate_s3_key(video)
        
        # 更新进度
        await self._update_progress(
            bvid=video.bvid,
            title=video.title,
            page=video.page,
            progress=0,
            status="uploading",
            message=f"开始上传到 S3..."
        )
        
        retry_count = 0
        last_error = ""
        
        while retry_count <= self.retry_times:
            try:
                logger.info(
                    f"上传视频：{video.bvid} P{video.page}, "
                    f"尝试 {retry_count + 1}/{self.retry_times + 1}"
                )
                
                # 在后台线程中执行上传
                uploaded = await self._execute_upload(
                    local_path, s3_key, video
                )
                
                if uploaded:
                    # 上传成功

                    # 更新数据库
                    await self.db.update_video_s3_status(
                        bvid=video.bvid,
                        page=video.page,
                        s3_key=s3_key,
                        quality=video.quality
                    )

                    # 删除本地文件（仅在配置允许时）
                    if delete_after_upload:
                        try:
                            os.remove(local_path)
                            logger.info(f"已删除本地文件：{local_path}")

                            # 清理视频临时目录（仅在删除本地文件时）
                            from services.downloader import Downloader
                            downloader = Downloader()
                            downloader.cleanup_video_temp(video.bvid)
                        except Exception as e:
                            logger.warning(f"删除本地文件失败：{e}")
                    else:
                        logger.info(f"保留本地文件：{local_path}")

                    # 更新进度
                    await self._update_progress(
                        bvid=video.bvid,
                        title=video.title,
                        page=video.page,
                        progress=100,
                        status="completed",
                        message=f"上传成功：{s3_key}"
                    )

                    logger.info(f"视频上传成功：{s3_key}")
                    return True, s3_key, ""
                
            except Exception as e:
                last_error = str(e)
                logger.error(f"上传异常：{e}")
            
            # 重试前等待
            retry_count += 1
            if retry_count <= self.retry_times:
                wait_time = min(2 ** retry_count, 30)
                logger.info(f"{wait_time}秒后重试...")
                
                await self._update_progress(
                    bvid=video.bvid,
                    title=video.title,
                    page=video.page,
                    progress=0,
                    status="uploading",
                    message=f"上传失败，{wait_time}秒后重试..."
                )
                
                await asyncio.sleep(wait_time)
        
        # 所有重试都失败
        await self._update_progress(
            bvid=video.bvid,
            title=video.title,
            page=video.page,
            progress=0,
            status="failed",
            message=f"上传失败：{last_error}"
        )
        
        logger.error(
            f"视频 {video.bvid} P{video.page} 上传失败，"
            f"已重试 {self.retry_times} 次"
        )
        
        return False, None, last_error

    async def _execute_upload(
        self,
        local_path: str,
        s3_key: str,
        video: Video
    ) -> bool:
        """执行上传（带进度跟踪）"""

        # 保存当前事件循环的引用
        loop = asyncio.get_running_loop()

        def progress_callback(bytes_transferred):
            """进度回调"""
            file_size = Path(local_path).stat().st_size
            progress = (bytes_transferred / file_size) * 100

            # 异步更新进度（使用保存的事件循环引用）
            try:
                asyncio.run_coroutine_threadsafe(
                    self._update_progress(
                        bvid=video.bvid,
                        title=video.title,
                        page=video.page,
                        progress=progress,
                        status="uploading",
                        message=f"已上传 {bytes_transferred / 1024 / 1024:.1f}MB"
                    ),
                    loop
                )
            except Exception as e:
                logger.debug(f"更新进度失败：{e}")

        # 在线程池中执行上传
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                self._upload_file_sync,
                local_path,
                s3_key,
                progress_callback
            )

            try:
                # 使用 asyncio.wait_for 来处理超时
                return await asyncio.wait_for(
                    asyncio.wrap_future(future),
                    timeout=self.upload_timeout + 60  # 额外 60 秒缓冲
                )
            except asyncio.TimeoutError:
                logger.error(f"上传超时：{s3_key}")
                raise TimeoutError(f"上传超时 ({self.upload_timeout}秒)")
    
    async def check_file_exists(self, s3_key: str) -> bool:
        """
        检查 S3 文件是否存在
        
        Args:
            s3_key: S3 键名
            
        Returns:
            是否存在
        """
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.s3_client.head_object(
                    Bucket=self.bucket_name,
                    Key=s3_key
                )
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            raise
        except Exception as e:
            logger.error(f"检查文件存在失败：{e}")
            return False
    
    async def get_video_quality_from_s3(
        self, bvid: str
    ) -> Optional[int]:
        """
        从 S3 获取视频的清晰度信息
        
        Args:
            bvid: 视频 BV 号
            
        Returns:
            清晰度代码，如果不存在返回 None
        """
        if not self.db:
            await self.init_db()
        
        # 从数据库查询
        video = await self.db.get_video_by_bvid(bvid)
        if video and video.s3_uploaded:
            return video.s3_quality
        
        return None
    
    async def test_connection(self) -> Tuple[bool, str]:
        """
        测试 S3 连接是否正常

        Returns:
            (success, message)
        """
        # 确保 S3 已初始化
        self.ensure_s3_initialized()

        if not self.s3_client:
            return False, "S3 未配置，请先在系统配置中填写 S3 信息"

        try:
            # 尝试列出 bucket 中的对象
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.s3_client.list_objects_v2(
                    Bucket=self.bucket_name,
                    MaxKeys=1
                )
            )

            return True, "S3 连接正常"

        except NoCredentialsError:
            return False, "S3 凭证无效"
        except ClientError as e:
            error_code = e.response['Error']['Code']
            return False, f"S3 错误 [{error_code}]"
        except Exception as e:
            return False, f"连接失败：{str(e)}"

    async def test_upload_speed(
        self, file_size_mb: int = 10, use_unique_folder: bool = True
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        测试 S3 上传速度

        Args:
            file_size_mb: 测试文件大小（MB）
            use_unique_folder: 是否使用唯一文件夹（避免 openist 同目录限制）

        Returns:
            (success, result_data, error_message)
        """
        import time
        import uuid

        # 确保 S3 已初始化
        self.ensure_s3_initialized()

        if not self.s3_client:
            return False, {}, "S3 未配置"

        try:
            # 创建临时测试文件
            temp_dir = Path(self.config.download.temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)

            test_file = temp_dir / f"s3_speed_test_{uuid.uuid4().hex[:8]}.bin"

            # 生成随机数据
            logger.info(f"创建 {file_size_mb}MB 测试文件...")
            start_create = time.time()

            with open(test_file, 'wb') as f:
                # 分块写入，避免内存溢出
                chunk_size = 1024 * 1024  # 1MB
                chunks = file_size_mb
                for i in range(chunks):
                    f.write(os.urandom(chunk_size))

            create_time = time.time() - start_create
            actual_size = test_file.stat().st_size

            # 生成 S3 key
            if use_unique_folder:
                # 使用唯一文件夹，避免 openist 同目录限制
                folder_id = uuid.uuid4().hex[:8]
                s3_key = f"_test/{folder_id}/speed_test_{int(time.time())}.bin"
            else:
                s3_key = f"_test/speed_test_{int(time.time())}.bin"

            # 上传测试
            logger.info(f"开始上传测试：{s3_key}")
            start_upload = time.time()

            def upload_callback(bytes_transferred):
                progress = (bytes_transferred / actual_size) * 100
                logger.debug(f"上传进度：{progress:.1f}%")

            # 在线程池中执行上传
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self._upload_file_sync,
                    str(test_file),
                    s3_key,
                    upload_callback
                )
                await asyncio.wait_for(
                    asyncio.wrap_future(future),
                    timeout=600
                )

            upload_time = time.time() - start_upload

            # 计算速度
            size_mb = actual_size / (1024 * 1024)
            speed_mbps = size_mb / upload_time if upload_time > 0 else 0
            speed_mbps_display = speed_mbps * 8  # 转换为 Mbps (比特每秒)

            # 清理测试文件
            try:
                test_file.unlink()
                logger.info(f"已删除本地测试文件")
            except Exception as e:
                logger.warning(f"删除测试文件失败：{e}")

            # 清理 S3 测试文件
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.s3_client.delete_object(
                        Bucket=self.bucket_name,
                        Key=s3_key
                    )
                )
                logger.info(f"已删除 S3 测试文件")
            except Exception as e:
                logger.warning(f"删除 S3 测试文件失败：{e}")

            result = {
                "file_size_mb": round(size_mb, 2),
                "upload_time_seconds": round(upload_time, 2),
                "speed_mbps": round(speed_mbps, 2),  # MB/s
                "speed_mbps_bits": round(speed_mbps_display, 2),  # Mbps
                "s3_key": s3_key,
                "create_time_seconds": round(create_time, 2)
            }

            logger.info(f"上传测试完成：{speed_mbps:.2f} MB/s ({speed_mbps_display:.2f} Mbps)")

            return True, result, ""

        except asyncio.TimeoutError:
            return False, {}, "上传超时"
        except NoCredentialsError:
            return False, {}, "S3 凭证无效"
        except ClientError as e:
            error_code = e.response['Error']['Code']
            return False, {}, f"S3 错误 [{error_code}]: {str(e)}"
        except Exception as e:
            logger.error(f"上传测试失败：{e}")
            return False, {}, f"测试失败：{str(e)}"

    async def list_recent_uploads(self, limit: int = 20) -> Tuple[bool, List[Dict], str]:
        """
        列出最近上传的文件

        Args:
            limit: 返回数量限制

        Returns:
            (success, files_list, error_message)
        """
        self.ensure_s3_initialized()

        if not self.s3_client:
            return False, [], "S3 未配置"

        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.s3_client.list_objects_v2(
                    Bucket=self.bucket_name,
                    Prefix="bilibili/",
                    MaxKeys=limit
                )
            )

            files = []
            for obj in response.get('Contents', []):
                files.append({
                    "key": obj['Key'],
                    "size": obj['Size'],
                    "last_modified": obj['LastModified'].isoformat() if obj.get('LastModified') else None
                })

            return True, files, ""

        except Exception as e:
            return False, [], f"获取列表失败：{str(e)}"
