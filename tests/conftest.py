"""
测试配置

提供测试夹具和共享配置
"""
import asyncio
import os
import sys
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """创建事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_config():
    """测试配置"""
    return {
        "bilibili": {
            "cookie": "test_cookie",
            "fav_id": "12345"
        },
        "download": {
            "quality": 127,
            "max_parallel": 1,
            "temp_dir": "./temp_test",
            "retry_times": 1,
            "request_delay": 0
        },
        "s3": {
            "enabled": False,
            "endpoint_url": "",
            "access_key": "",
            "secret_key": "",
            "bucket_name": ""
        }
    }


@pytest_asyncio.fixture
async def test_db():
    """测试数据库"""
    from core.db.connection import Database

    # 使用内存数据库
    db = Database()
    db.db_path = ":memory:"
    await db.connect()

    yield db

    await db.close()


@pytest.fixture
def sample_video_data():
    """示例视频数据"""
    return {
        "bvid": "BV1test12345",
        "title": "测试视频",
        "cid": "12345678",
        "page": 1,
        "total_pages": 1,
        "quality": 127,
        "duration": 300,
        "pubdate": 1609459200,
        "owner_name": "测试UP主",
        "s3_key": None,
        "s3_uploaded": False,
        "s3_quality": None,
        "local_path": None,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    }
