"""环境变量配置"""

import os


class Config:
    """集中声明服务运行依赖的环境变量配置。

    这个类不承担运行时逻辑，只作为模块级配置容器，统一暴露上游地址、
    鉴权密钥、端口、超时和调试开关，供应用启动、路由鉴权和请求转发层共享。
    """

    # 上游 API 地址
    PROXY_TARGET_URL = os.getenv('PROXY_TARGET_URL', 'https://api.anthropic.com')
    # 上游 API 密钥。新模式下仅作为兜底值；数据面会优先透传用户请求中的 key。
    PROXY_API_KEY = os.getenv('PROXY_API_KEY', '')
    # 服务监听端口
    PROXY_PORT = int(os.getenv('PROXY_PORT', '3029'))
    # 请求超时时间（秒）
    API_TIMEOUT = int(os.getenv('API_TIMEOUT', '300'))
    # 后台管理员密钥。兼容旧配置：未设置 ADMIN_API_KEY 时回退 ACCESS_API_KEY。
    ADMIN_API_KEY = os.getenv('ADMIN_API_KEY') or os.getenv('ACCESS_API_KEY', '')
    # 旧版访问鉴权密钥，仅保留给历史配置读取；数据面不再用它做统一鉴权。
    ACCESS_API_KEY = os.getenv('ACCESS_API_KEY', '')
    # 数据面是否要求请求携带 key。默认要求携带，但不在本服务校验合法性，由上游 sub2api 判断。
    REQUIRE_CLIENT_API_KEY = os.getenv('REQUIRE_CLIENT_API_KEY', 'true').strip().lower() not in ('0', 'false', 'no', 'off')

    # 调试模式分级：
    # - off: 关闭调试
    # - simple: 仅控制台调试日志
    # - verbose: 控制台调试 + 详细文件日志
    _debug_mode_raw = os.getenv('DEBUG_MODE', '').strip().lower()
    _legacy_debug = os.getenv('DEBUG', '').lower() in ('1', 'true', 'yes', 'on')
    if _debug_mode_raw in ('off', 'simple', 'verbose'):
        DEBUG_MODE = _debug_mode_raw
    else:
        DEBUG_MODE = 'simple' if _legacy_debug else 'off'

    DEBUG = DEBUG_MODE in ('simple', 'verbose')
    VERBOSE_FILE_LOG = DEBUG_MODE == 'verbose'
