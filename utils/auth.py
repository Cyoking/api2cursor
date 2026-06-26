"""鉴权辅助。

数据面只负责提取用户在 Cursor 中填写的 key，并将其透传给上游中转站。
管理员接口则使用独立的 ADMIN_API_KEY 保护。
"""

from flask import request


def extract_request_api_key() -> str:
    """从当前请求中提取 API key，不做合法性判断。"""
    auth = request.headers.get('Authorization', '').strip()
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return request.headers.get('x-api-key', '').strip()
