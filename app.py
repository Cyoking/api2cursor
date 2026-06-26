"""Flask 应用工厂

创建并配置 Flask 应用：
  - 注册所有路由蓝图
  - 设置 JSON 错误处理器（避免返回 HTML）
  - 配置全局鉴权中间件
"""

import logging
import time

from flask import Flask, g, jsonify, request
from flask_cors import CORS

import settings
from config import Config
from routes import register_routes
from utils.auth import extract_request_api_key

logger = logging.getLogger(__name__)


def _is_data_path(path: str) -> bool:
    """判断是否为 Cursor/模型供应商访问的数据面路径。"""
    prefixes = (
        '/v1/',
        '/models',
        '/chat/completions',
        '/responses',
        '/messages',
    )
    return any(path == p.rstrip('/') or path.startswith(p) for p in prefixes)


def create_app():
    """创建并配置 Flask 应用实例。

    这里统一完成跨路由共享的初始化逻辑，包括配置加载、跨域、错误处理、
    访问鉴权、健康检查以及蓝图注册。
    """
    app = Flask(__name__)
    CORS(app)
    settings.load()

    # ─── JSON 错误处理器 ──────────────────────────

    @app.errorhandler(404)
    def not_found(e):
        """将未匹配到的路径统一转换为 JSON 404 响应。"""
        return jsonify({'error': {'message': '未找到', 'type': 'not_found'}}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        """将不支持的请求方法统一转换为 JSON 405 响应。"""
        return jsonify({'error': {'message': '方法不允许', 'type': 'method_not_allowed'}}), 405

    @app.errorhandler(500)
    def internal_error(e):
        """将未捕获的服务端异常统一包装为 JSON 500 响应。"""
        return jsonify({'error': {'message': '服务器内部错误', 'type': 'server_error'}}), 500

    # ─── 全局鉴权中间件 ──────────────────────────

    @app.before_request
    def start_access_log():
        """记录请求开始时间，用于输出轻量访问日志。"""
        g._access_start = time.time()

    @app.after_request
    def log_access(response):
        """输出数据面访问日志，便于排查 Cursor 实际调用的路径。

        日志只记录路径、模型、状态等元信息，不记录 Authorization 或 API Key。
        """
        data_paths = (
            '/v1/',
            '/models',
            '/chat/completions',
            '/responses',
            '/messages',
        )
        should_log = any(request.path == p.rstrip('/') or request.path.startswith(p) for p in data_paths)
        if should_log:
            payload = request.get_json(silent=True) if request.is_json else None
            model = payload.get('model') if isinstance(payload, dict) else ''
            stream = payload.get('stream') if isinstance(payload, dict) else ''
            duration_ms = int((time.time() - getattr(g, '_access_start', time.time())) * 1000)
            logger.info(
                '[访问] %s %s status=%s model=%s stream=%s duration_ms=%s ua=%s',
                request.method,
                request.path,
                response.status_code,
                model or '-',
                stream if stream != '' else '-',
                duration_ms,
                request.headers.get('User-Agent', '-')[:120],
            )
        return response

    @app.before_request
    def check_access():
        """在进入业务路由前提取数据面用户 key。

        后台 API 使用独立的 ADMIN_API_KEY 在 `routes.admin` 中校验。
        数据面不再比对本地白名单，只把用户在 Cursor 中填写的 key
        传给上游 sub2api，由 sub2api 负责额度统计和合法性校验。
        """
        if not _is_data_path(request.path):
            return

        g.client_api_key = extract_request_api_key()
        if Config.REQUIRE_CLIENT_API_KEY and not g.client_api_key:
            logger.warning('数据面请求未携带 API Key: %s', request.path)
            return jsonify({
                'error': {'message': '缺少 API Key', 'type': 'authentication_error'}
            }), 401

    # ─── 健康检查 ────────────────────────────────

    @app.route('/health', methods=['GET'])
    def health():
        """返回服务健康状态和当前生效的上游地址。"""
        return jsonify({'status': 'ok', 'target': settings.get_url()})

    # ─── 注册路由蓝图 ────────────────────────────

    register_routes(app)

    return app
