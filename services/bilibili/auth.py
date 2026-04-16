"""
Bilibili 认证 API

提供扫码登录、密码登录、短信登录等认证功能
"""
import base64
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, Optional

from bilibili_api.login_v2 import (
    QrCodeLogin, QrCodeLoginEvents, PhoneNumber,
    login_with_password, login_with_sms, send_sms
)
from bilibili_api.utils.geetest import Geetest, GeetestType
from bilibili_api.utils.network import Credential
from loguru import logger

from services.bilibili.client import BilibiliClient


class AuthAPI(BilibiliClient):
    """Bilibili 认证 API"""

    # 会话存储
    _qr_sessions: Dict[str, Dict[str, Any]] = {}
    _geetest_sessions: Dict[str, Dict[str, Any]] = {}

    async def validate_cookie(self) -> Tuple[bool, str]:
        """
        验证 Cookie 是否有效

        Returns:
            (is_valid, message)
        """
        from bilibili_api import user

        if not self.cookie:
            return False, "Cookie 未配置"
        try:
            credential = self.build_credential()
            if not credential:
                return False, "Cookie 格式无效"
            await user.get_self_info(credential=credential)
            return True, "Cookie 有效"
        except Exception as e:
            logger.error(f"Cookie 校验异常：{e}")
            return False, f"Cookie 校验失败：{str(e)}"

    async def generate_login_qrcode(self) -> Tuple[bool, Dict[str, Any], str]:
        """
        生成扫码登录二维码

        Returns:
            (success, data, message)
        """
        try:
            qrcode = QrCodeLogin()
            await qrcode.generate_qrcode()
            picture = qrcode.get_qrcode_picture()
            image_b64 = base64.b64encode(picture.content).decode("ascii")
            session_id = uuid.uuid4().hex
            self._qr_sessions[session_id] = {
                "qrcode": qrcode,
                "created_at": datetime.now()
            }
            return True, {
                "session_id": session_id,
                "image_base64": image_b64
            }, "二维码已生成"
        except Exception as e:
            logger.error(f"生成扫码二维码失败：{e}")
            return False, {}, str(e)

    async def poll_login_qrcode(self, session_id: str) -> Tuple[bool, Dict[str, Any], str]:
        """
        轮询扫码登录状态

        Args:
            session_id: 二维码会话 ID

        Returns:
            (success, data, message)
        """
        session = self._qr_sessions.get(session_id)
        if not session:
            return False, {}, "二维码会话不存在或已过期"

        # 10 分钟过期回收
        if datetime.now() - session["created_at"] > timedelta(minutes=10):
            self._qr_sessions.pop(session_id, None)
            return True, {"status_code": 86038, "status_text": "二维码已失效", "cookie": ""}, "二维码已失效"

        try:
            qrcode: QrCodeLogin = session["qrcode"]
            event = await qrcode.check_state()

            if event == QrCodeLoginEvents.DONE:
                credential = qrcode.get_credential()
                cookie = self.credential_to_cookie(credential)
                self._qr_sessions.pop(session_id, None)
                return True, {"status_code": 0, "status_text": "登录成功", "cookie": cookie}, "登录成功"
            if event == QrCodeLoginEvents.SCAN:
                return True, {"status_code": 86090, "status_text": "已扫码，待确认", "cookie": ""}, "已扫码，待确认"
            if event == QrCodeLoginEvents.CONF:
                return True, {"status_code": 86090, "status_text": "已确认，等待完成", "cookie": ""}, "已确认，等待完成"
            if event == QrCodeLoginEvents.TIMEOUT:
                self._qr_sessions.pop(session_id, None)
                return True, {"status_code": 86038, "status_text": "二维码已失效", "cookie": ""}, "二维码已失效"

            return True, {"status_code": 86101, "status_text": "未扫码", "cookie": ""}, "未扫码"
        except Exception as e:
            logger.error(f"轮询扫码状态失败：{e}")
            return False, {}, str(e)

    async def start_geetest(self) -> Tuple[bool, Dict[str, Any], str]:
        """
        开始极验流程（用于密码/短信登录）

        Returns:
            (success, data, message)
        """
        try:
            gt = Geetest()
            await gt.generate_test(GeetestType.LOGIN)
            gt.start_geetest_server()
            meta = gt.get_info()
            session_id = uuid.uuid4().hex
            self._geetest_sessions[session_id] = {
                "geetest": gt,
                "created_at": datetime.now()
            }
            return True, {
                "session_id": session_id,
                "gt": meta.gt,
                "challenge": meta.challenge,
                "token": meta.token,
                "verify_url": gt.get_geetest_server_url()
            }, "极验已生成"
        except Exception as e:
            logger.error(f"启动极验失败：{e}")
            return False, {}, str(e)

    async def get_geetest_status(self, session_id: str) -> Tuple[bool, Dict[str, Any], str]:
        """
        获取极验验证状态

        Args:
            session_id: 极验会话 ID

        Returns:
            (success, data, message)
        """
        session = self._geetest_sessions.get(session_id)
        if not session:
            return False, {}, "极验会话不存在或已过期"
        gt: Geetest = session["geetest"]
        if gt.has_done():
            meta = gt.get_result()
            return True, {
                "done": True,
                "validate": meta.validate,
                "seccode": meta.seccode
            }, "验证完成"
        return True, {"done": False}, "等待验证"

    async def login_by_password(
        self, username: str, password: str, geetest_session_id: str
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        密码登录

        Args:
            username: 用户名
            password: 密码
            geetest_session_id: 极验会话 ID

        Returns:
            (success, data, message)
        """
        session = self._geetest_sessions.get(geetest_session_id)
        if not session:
            return False, {}, "极验会话不存在"
        gt: Geetest = session["geetest"]
        if not gt.has_done():
            return False, {}, "请先完成极验验证"
        try:
            result = await login_with_password(username, password, gt)
            ok, cookie, msg = self._parse_login_result(result)
            if ok:
                self._geetest_sessions.pop(geetest_session_id, None)
                gt.close_geetest_server()
                return True, {"cookie": cookie}, msg
            return False, {}, msg
        except Exception as e:
            logger.error(f"密码登录失败：{e}")
            return False, {}, str(e)

    async def send_sms_code(
        self, phone: str, country: str, geetest_session_id: str
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        发送短信验证码

        Args:
            phone: 手机号
            country: 国家代码
            geetest_session_id: 极验会话 ID

        Returns:
            (success, data, message)
        """
        session = self._geetest_sessions.get(geetest_session_id)
        if not session:
            return False, {}, "极验会话不存在"
        gt: Geetest = session["geetest"]
        if not gt.has_done():
            return False, {}, "请先完成极验验证"
        try:
            pn = PhoneNumber(number=phone, country=country)
            captcha_id = await send_sms(phonenumber=pn, geetest=gt)
            return True, {"captcha_id": captcha_id}, "短信已发送"
        except Exception as e:
            logger.error(f"发送短信失败：{e}")
            return False, {}, str(e)

    async def login_by_sms(
        self, phone: str, country: str, code: str, captcha_id: str
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        短信验证码登录

        Args:
            phone: 手机号
            country: 国家代码
            code: 验证码
            captcha_id: 验证码 ID

        Returns:
            (success, data, message)
        """
        try:
            pn = PhoneNumber(number=phone, country=country)
            result = await login_with_sms(phonenumber=pn, code=code, captcha_id=captcha_id)
            ok, cookie, msg = self._parse_login_result(result)
            if ok:
                return True, {"cookie": cookie}, msg
            return False, {}, msg
        except Exception as e:
            logger.error(f"短信登录失败：{e}")
            return False, {}, str(e)

    def _parse_login_result(self, result: Any) -> Tuple[bool, str, str]:
        """解析登录结果"""
        if isinstance(result, Credential):
            return True, self.credential_to_cookie(result), "登录成功"
        # 风控二次验证
        if hasattr(result, "fetch_info"):
            return False, "", "触发二次验证，请先完成安全验证"
        return False, "", "登录失败"
