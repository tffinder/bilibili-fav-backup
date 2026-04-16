"""
异常模块测试
"""
import pytest
from core.exceptions import (
    BilibiliBackupError,
    ConfigError,
    ConfigNotFoundError,
    DatabaseError,
    DatabaseConnectionError,
    BilibiliError,
    BilibiliAuthError,
    CookieInvalidError,
    DownloadError,
    DownloadFailedError,
    UploadError,
    S3ConnectionError
)


class TestExceptions:
    """异常类测试"""

    def test_base_exception(self):
        """测试基础异常"""
        exc = BilibiliBackupError("测试错误")
        assert exc.message == "测试错误"
        assert exc.code == "UNKNOWN_ERROR"
        assert str(exc) == "[UNKNOWN_ERROR] 测试错误"

    def test_exception_with_code(self):
        """测试带错误码的异常"""
        exc = BilibiliBackupError("测试错误", code="TEST_ERROR")
        assert exc.code == "TEST_ERROR"
        assert str(exc) == "[TEST_ERROR] 测试错误"

    def test_exception_to_dict(self):
        """测试异常转字典"""
        exc = BilibiliBackupError(
            "测试错误",
            code="TEST_ERROR",
            details={"key": "value"}
        )
        result = exc.to_dict()
        assert result["error"] == "TEST_ERROR"
        assert result["message"] == "测试错误"
        assert result["details"]["key"] == "value"

    def test_config_not_found_error(self):
        """测试配置未找到异常"""
        exc = ConfigNotFoundError("/path/to/config.yaml")
        assert exc.code == "CONFIG_NOT_FOUND"
        assert "/path/to/config.yaml" in exc.message

    def test_database_connection_error(self):
        """测试数据库连接异常"""
        exc = DatabaseConnectionError("连接超时")
        assert exc.code == "DB_CONNECTION_ERROR"
        assert "连接超时" in exc.message

    def test_cookie_invalid_error(self):
        """测试 Cookie 无效异常"""
        exc = CookieInvalidError("会话已过期")
        assert exc.code == "COOKIE_INVALID"
        assert "会话已过期" in exc.message

    def test_download_failed_error(self):
        """测试下载失败异常"""
        exc = DownloadFailedError("BV1test12345", "网络错误", page=1)
        assert exc.code == "DOWNLOAD_FAILED"
        assert "BV1test12345" in exc.message
        assert exc.details["bvid"] == "BV1test12345"
        assert exc.details["page"] == 1

    def test_s3_connection_error(self):
        """测试 S3 连接异常"""
        exc = S3ConnectionError("认证失败")
        assert exc.code == "S3_CONNECTION_ERROR"
        assert "认证失败" in exc.message
