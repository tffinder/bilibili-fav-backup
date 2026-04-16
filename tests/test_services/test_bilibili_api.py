"""
Bilibili API 模块测试
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from services.bilibili.client import BilibiliClient
from services.bilibili.auth import AuthAPI
from services.bilibili.video import VideoAPI


class TestBilibiliClient:
    """Bilibili 客户端测试"""

    def test_parse_cookie_string(self):
        """测试 Cookie 字符串解析"""
        cookie_str = "SESSDATA=test123; bili_jct=abc456; DedeUserID=12345"
        result = BilibiliClient.parse_cookie_string(cookie_str)

        assert result["SESSDATA"] == "test123"
        assert result["bili_jct"] == "abc456"
        assert result["DedeUserID"] == "12345"

    def test_parse_cookie_string_with_spaces(self):
        """测试带空格的 Cookie 字符串解析"""
        cookie_str = "SESSDATA = test123 ; bili_jct=abc456"
        result = BilibiliClient.parse_cookie_string(cookie_str)

        assert result["SESSDATA"] == "test123"
        assert result["bili_jct"] == "abc456"

    def test_parse_empty_cookie(self):
        """测试空 Cookie 字符串"""
        result = BilibiliClient.parse_cookie_string("")
        assert result == {}

        result = BilibiliClient.parse_cookie_string("   ")
        assert result == {}


class TestVideoAPI:
    """视频 API 测试"""

    def test_extract_bvid(self):
        """测试 BV 号提取"""
        # 标准 BV 号
        assert VideoAPI.extract_bvid("BV1GJ411x7h7") == "BV1GJ411x7h7"

        # 视频 URL
        assert VideoAPI.extract_bvid("https://www.bilibili.com/video/BV1GJ411x7h7") == "BV1GJ411x7h7"

        # 短链接
        assert VideoAPI.extract_bvid("b23.tv/BV1GJ411x7h7") == "BV1GJ411x7h7"

        # 带 P 参数
        assert VideoAPI.extract_bvid("BV1GJ411x7h7?p=1") == "BV1GJ411x7h7"

    def test_extract_bvid_invalid(self):
        """测试无效 BV 号提取"""
        assert VideoAPI.extract_bvid("invalid") is None
        assert VideoAPI.extract_bvid("") is None
        assert VideoAPI.extract_bvid(None) is None

    def test_quality_to_height(self):
        """测试清晰度转换"""
        assert VideoAPI._quality_to_height(127) == 4320  # 8K
        assert VideoAPI._quality_to_height(125) == 2160  # 4K
        assert VideoAPI._quality_to_height(80) == 1080   # 1080P
        assert VideoAPI._quality_to_height(64) == 720    # 720P

    def test_quality_to_format(self):
        """测试格式选择字符串生成"""
        format_4k = VideoAPI._quality_to_format(125)
        assert "2160" in format_4k

        format_1080 = VideoAPI._quality_to_format(80)
        assert "1080" in format_1080
