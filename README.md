# API 2 Cursor

让 Cursor 通过第三方中转站使用任意 LLM 模型的 API 代理服务。

## 它解决什么问题

Cursor 根据模型名发送不同格式的请求：

| Cursor 模型名风格 | 请求格式 |
|---|---|
| `claude-sonnet-*`、`glm-*` | `/v1/chat/completions` (OpenAI CC) |
| `gpt-*`、`claude-opus-*` | `/v1/responses` (OpenAI Responses) |

而中转站通常只支持 `/v1/chat/completions`、`/v1/messages` 或 `/v1/responses`。

本项目在中间做协议转换，**不管 Cursor 发什么格式，都能正确转发到中转站；不管中转站返回什么格式，都让 Cursor 能正确接收**。

## 架构

可以把这个项目理解成“三种入口协议 + 三种上游后端协议”的协议桥：

```text
Cursor                         API 2 Cursor                           中转站
  │                                 │                                   │
  ├─ /v1/chat/completions ─────→ chat.py ─────┬─ openai 后端 ─────────→ /v1/chat/completions
  │                                            ├─ anthropic 后端 ─────→ /v1/messages
  │                                            └─ responses 后端 ─────→ /v1/responses
  │
  ├─ /v1/responses ────────────→ responses.py ─┬─ openai 后端 ───────→ /v1/chat/completions
  │                                             ├─ anthropic 后端 ───→ /v1/messages
  │                                             └─ responses 后端 ───→ /v1/responses
  │
  └─ /v1/messages ─────────────→ messages.py ─────────────────────────→ /v1/messages
```

其中：
- `chat.py` 负责接住 Cursor 的 Chat Completions 请求，并根据模型映射决定发往哪种后端协议
- `responses.py` 负责接住 Cursor 的 Responses 请求，并在需要时做 `Responses ↔ CC` 或 `Responses ↔ Messages` 桥接
- `messages.py` 负责 Anthropic 原生消息的直通场景

## 快速开始

### 直接运行

```bash
cd api2cursor
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入中转站地址和密钥
python start.py
```

### Docker 部署

```bash
cd api2cursor
cp .env.example .env
# 编辑 .env
docker compose up -d
```

服务启动后访问 `http://localhost:3029/admin` 进入管理面板。

## 配置

### 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `PROXY_TARGET_URL` | 上游中转站地址 | `https://api.anthropic.com` |
| `PROXY_API_KEY` | 上游 API 密钥兜底值；数据面优先使用用户请求中的 key | |
| `PROXY_PORT` | 服务监听端口 | `3029` |
| `API_TIMEOUT` | 请求超时（秒） | `300` |
| `ADMIN_API_KEY` | 后台管理密钥，保护模型映射管理接口 | |
| `ACCESS_API_KEY` | 旧版后台管理密钥兼容项；未设置 `ADMIN_API_KEY` 时生效 | |
| `REQUIRE_CLIENT_API_KEY` | 数据面是否要求请求携带 API Key；只检查是否存在，不校验合法性 | `true` |
| `DEBUG` | 兼容旧版调试开关，开启后等价于 `DEBUG_MODE=simple` | `false` |
| `DEBUG_MODE` | 调试模式：`off` / `simple` / `verbose` | `off` |

### 模型映射

在管理面板 (`/admin`) 中配置模型映射：

- **Cursor 模型名** — 在 Cursor 自定义模型中填入的名称
- **上游模型名** — 发送到中转站的实际模型名
- **后端类型** — `openai` (CC 格式) / `anthropic` (Messages 格式) / `responses` (Responses 格式) / `gemini` (Gemini Contents 格式) / `auto` (自动检测)
- **自定义地址/密钥** — 地址可选覆盖全局设置；密钥只作为兜底，正常使用用户请求里的个人 sub2api key
- **日志模式** — 可在管理面板全局设置中切换 `off` / `simple` / `verbose`

**示例**：在 Cursor 中添加 `claude-sonnet-4-5-20250929`，映射到上游 `gpt-5.3-codex`，后端选 `openai`。Cursor 会用 CC 格式发送请求，代理直接转发到中转站的 `/v1/chat/completions`。

如果你的中转站只支持 `/v1/responses`，可以把后端类型选成 `responses`。此时代理会把 Cursor 发来的请求转换或透传为 Responses 格式，再发往中转站的 `/v1/responses`。

> **提示**：使用 Claude 风格的模型名（如 `claude-sonnet-4-5-20250929`）可以让 Cursor 显示思考过程（thinking）。

### 调试日志模式

项目支持三档调试模式，可通过环境变量 `DEBUG_MODE` 或管理面板全局设置切换：

- `off` — 关闭调试日志
- `simple` — 仅输出控制台元信息日志，不写文件，不记录请求/响应正文
- `verbose` — 写入对话级元信息文件日志，不记录请求/响应正文

详细日志会写入：

```text
data/conversations/YYYY-MM-DD/{conversation_id}.json
```

特性：
- 每次请求生成独立日志 ID，不基于 prompt 内容派生会话 ID
- 只记录模型、后端、字段名、数组计数、事件计数、usage 和错误状态等元信息
- 不记录用户 prompt、上下文文件片段、工具参数、上游请求体、上游响应体或 SSE chunk 内容
- 请求头只保留少量安全字段；`Authorization`、`x-api-key` 等密钥不会写入日志

### 在 Cursor 中配置

1. 打开 Cursor 设置 → Models
2. 添加自定义模型，名称填映射中配置的 Cursor 模型名
3. Override OpenAI Base URL 填 `http://localhost:3029`
4. API Key 填用户自己的 sub2api key。代理会把这个 key 原样透传给上游，用于 sub2api 的额度统计和权限控制。

后台管理面板使用 `ADMIN_API_KEY` 登录，只负责维护全局模型映射。员工的 sub2api key 不需要导入到本项目，也不会写入本项目配置。

## 项目结构

```text
api2cursor/
├── start.py                    # 启动入口
├── app.py                      # Flask 应用工厂
├── config.py                   # 环境变量配置
├── settings.py                 # 持久化配置管理
├── routes/                     # 路由层：按对外 API 入口拆分
│   ├── chat.py                 #   /v1/chat/completions
│   ├── responses.py            #   /v1/responses
│   ├── messages.py             #   /v1/messages（透传）
│   ├── admin.py                #   管理面板 + API
│   └── common.py               #   路由公共上下文、日志与 SSE 辅助
├── adapters/                   # 适配层：按协议桥接职责拆分
│   ├── cc_anthropic_adapter.py #   Chat Completions ↔ Anthropic Messages
│   ├── openai_compat_fixer.py  #   OpenAI / Chat Completions 兼容修复
│   └── responses_cc_adapter.py #   Responses ↔ Chat Completions + 原生 Responses 流桥接
├── utils/                      # 通用工具层
│   ├── http.py                 #   请求转发、SSE 解析
│   ├── tool_fixer.py           #   工具参数修复
│   └── think_tag.py            #   <think> 标签提取
└── static/                     # 管理面板前端
    ├── admin.html
    ├── admin.css
    └── admin.js
```

## 兼容性修复

代理自动处理以下兼容性问题：

- Cursor 扁平格式 tools → 标准 OpenAI 嵌套格式
- `reasoningContent` → `reasoning_content`
- `<think>` 标签 → `reasoning_content`
- 旧版 `function_call` → 新版 `tool_calls`
- `tool_calls` 缺失 `id` / `index` / `type` 字段补全
- 智能引号 → 普通引号（StrReplace 工具精确匹配修复）
- `file_path` → `path` 字段映射
- `finish_reason` 修正

## 许可证

[MIT](LICENSE)
