"""
Bilibili 客户端基础封装

提供 Cookie 解析、Credential 构建、请求限流等基础功能
"""
import asyncio
from typing import Dict, Optional, Any

from bilibili_api.utils.network import Credential
from loguru import logger

from core.config import get_config


class BilibiliClient:
    """Bilibili 基础客户端"""

    def __init__(self):
        self.config = get_config()
        self.cookie = self.config.bilibili.cookie
        self.request_delay = self.config.download.request_delay

    async def rate_limit(self) -> None:
        """请求限流"""
        if self.request_delay > 0:
            await asyncio.sleep(self.request_delay)

    @staticmethod
    def parse_cookie_string(cookie: str) -> Dict[str, str]:
        """解析 Cookie 字符串为字典"""
        cookie_dict: Dict[str, str] = {}
        for part in cookie.split(";"):
            item = part.strip()
            if not item or "=" not in item:
                continue
            k, v = item.split("=", 1)
            cookie_dict[k.strip()] = v.strip()
        return cookie_dict

    def build_credential(self) -> Optional[Credential]:
        """从 Cookie 构建 Credential 对象"""
        if not self.cookie:
            return None
        cookie_dict = self.parse_cookie_string(self.cookie)
        if not cookie_dict:
            return None
        return Credential.from_cookies(cookie_dict)

    @staticmethod
    def credential_to_cookie(credential: Credential) -> str:
        """将 Credential 对象转换为 Cookie 字符串"""
        cookies = credential.get_cookies()
        return "; ".join([f"{k}={v}" for k, v in cookies.items() if v])

    def update_cookie(self, cookie: str) -> None:
        """更新 Cookie"""
        self.cookie = cookie
        self.config.bilibili.cookie = cookie
