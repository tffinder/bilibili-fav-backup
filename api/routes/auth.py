"""
认证相关 API

提供扫码登录、密码登录、短信登录等接口
"""
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from loguru import logger

from core.config import get_config, ConfigManager
from services.bilibili_api import BilibiliAPI
from api.models import ApiResponse

router = APIRouter(tags=["认证"])


@router.post("/test/cookie", response_model=ApiResponse)
async def test_cookie():
    """测试 B 站 Cookie 是否有效"""
    try:
        api = BilibiliAPI()
        valid, message = await api.validate_cookie()

        return ApiResponse(
            success=valid,
            message=message
        )

    except Exception as e:
        logger.error(f"测试 Cookie 失败：{e}")
        return ApiResponse(success=False, message=str(e))


@router.post("/auth/qrcode/start", response_model=ApiResponse)
async def start_qrcode_login():
    """开始 B 站扫码登录，返回二维码 URL 和 key"""
    try:
        api = BilibiliAPI()
        success, data, message = await api.generate_login_qrcode()
        return ApiResponse(success=success, message=message or ("ok" if success else "fail"), data=data)
    except Exception as e:
        logger.error(f"启动扫码登录失败：{e}")
        return ApiResponse(success=False, message=str(e))


@router.get("/auth/qrcode/poll", response_model=ApiResponse)
async def poll_qrcode_login(
    session_id: str = Query(..., description="扫码会话 ID"),
    auto_save_cookie: bool = Query(True, description="登录成功后自动保存 Cookie")
):
    """轮询扫码登录状态，成功后可自动保存 Cookie 到配置"""
    try:
        api = BilibiliAPI()
        success, data, message = await api.poll_login_qrcode(session_id)

        if success and data and data.get("status_code") == 0 and auto_save_cookie:
            cookie = data.get("cookie", "")
            if cookie:
                config_manager = ConfigManager.get_instance()
                config = get_config()
                config.bilibili.cookie = cookie
                config_manager.save_config()

        return ApiResponse(success=success, message=message or ("ok" if success else "fail"), data=data)
    except Exception as e:
        logger.error(f"轮询扫码登录失败：{e}")
        return ApiResponse(success=False, message=str(e))


@router.post("/auth/geetest/start", response_model=ApiResponse)
async def start_geetest():
    """开始极验（密码/短信登录前置）"""
    try:
        api = BilibiliAPI()
        success, data, message = await api.start_geetest()
        return ApiResponse(success=success, message=message or ("ok" if success else "fail"), data=data)
    except Exception as e:
        logger.error(f"启动极验失败：{e}")
        return ApiResponse(success=False, message=str(e))


@router.get("/auth/geetest/status", response_model=ApiResponse)
async def geetest_status(session_id: str = Query(..., description="极验会话 ID")):
    """查询极验状态"""
    try:
        api = BilibiliAPI()
        success, data, message = await api.get_geetest_status(session_id)
        return ApiResponse(success=success, message=message or ("ok" if success else "fail"), data=data)
    except Exception as e:
        logger.error(f"查询极验状态失败：{e}")
        return ApiResponse(success=False, message=str(e))


@router.post("/auth/password/login", response_model=ApiResponse)
async def password_login(payload: dict):
    """账号密码登录"""
    try:
        username = payload.get("username", "")
        password = payload.get("password", "")
        geetest_session_id = payload.get("geetest_session_id", "")
        if not username or not password or not geetest_session_id:
            return ApiResponse(success=False, message="缺少用户名/密码/geetest_session_id")

        api = BilibiliAPI()
        success, data, message = await api.login_by_password(username, password, geetest_session_id)
        if success:
            cookie = data.get("cookie", "")
            if cookie:
                config_manager = ConfigManager.get_instance()
                config = get_config()
                config.bilibili.cookie = cookie
                config_manager.save_config()
        return ApiResponse(success=success, message=message, data=data)
    except Exception as e:
        logger.error(f"密码登录失败：{e}")
        return ApiResponse(success=False, message=str(e))


@router.post("/auth/sms/send", response_model=ApiResponse)
async def send_sms_login_code(payload: dict):
    """发送短信验证码"""
    try:
        phone = payload.get("phone", "")
        country = payload.get("country", "+86")
        geetest_session_id = payload.get("geetest_session_id", "")
        if not phone or not geetest_session_id:
            return ApiResponse(success=False, message="缺少 phone/geetest_session_id")

        api = BilibiliAPI()
        success, data, message = await api.send_sms_code(phone, country, geetest_session_id)
        return ApiResponse(success=success, message=message, data=data)
    except Exception as e:
        logger.error(f"发送短信验证码失败：{e}")
        return ApiResponse(success=False, message=str(e))


@router.post("/auth/sms/login", response_model=ApiResponse)
async def sms_login(payload: dict):
    """短信验证码登录"""
    try:
        phone = payload.get("phone", "")
        country = payload.get("country", "+86")
        code = payload.get("code", "")
        captcha_id = payload.get("captcha_id", "")
        if not phone or not code or not captcha_id:
            return ApiResponse(success=False, message="缺少 phone/code/captcha_id")

        api = BilibiliAPI()
        success, data, message = await api.login_by_sms(phone, country, code, captcha_id)
        if success:
            cookie = data.get("cookie", "")
            if cookie:
                config_manager = ConfigManager.get_instance()
                config = get_config()
                config.bilibili.cookie = cookie
                config_manager.save_config()
        return ApiResponse(success=success, message=message, data=data)
    except Exception as e:
        logger.error(f"短信登录失败：{e}")
        return ApiResponse(success=False, message=str(e))


@router.get("/auth/me", response_model=ApiResponse)
async def get_current_user():
    """获取当前登录用户资料"""
    try:
        from api.models import UserProfileModel
        api = BilibiliAPI()
        success, data, message = await api.get_self_profile()
        if success:
            return ApiResponse(
                success=True,
                message=message or "ok",
                data=UserProfileModel(**data).model_dump()
            )
        return ApiResponse(success=False, message=message, data=None)
    except Exception as e:
        logger.error(f"获取当前用户资料失败：{e}")
        return ApiResponse(success=False, message=str(e))
