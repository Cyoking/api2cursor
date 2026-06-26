"""对话级文件日志

将同一段多轮对话聚合到一个 JSON 文件中，而不是按单次请求散落成多个文件。
仅在详细日志模式开启时记录。
日志目录: data/conversations/YYYY-MM-DD/{conversation_id}.json
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any

from settings import DATA_DIR
import settings
from utils.http import gen_id

logger = logging.getLogger(__name__)

_LOG_DIR = os.path.join(DATA_DIR, 'conversations')
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_STREAM_KEEP_HEAD = 12
_STREAM_KEEP_TAIL = 12


def start_turn(
    *,
    route: str,
    client_model: str,
    backend: str,
    stream: bool,
    client_request: dict[str, Any],
    request_headers: dict[str, Any] | None = None,
    target_url: str = '',
    upstream_model: str = '',
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """创建一条新的对话 turn 上下文。"""
    if settings.get_debug_mode() != 'verbose':
        return None

    now = datetime.utcnow().isoformat() + 'Z'
    conversation_id = get_conversation_id(route=route, payload=client_request)
    turn_id = gen_id('turn_')
    return {
        'conversation_id': conversation_id,
        'turn_id': turn_id,
        'route': route,
        'client_model': client_model,
        'backend': backend,
        'stream': stream,
        'target_url': target_url,
        'upstream_model': upstream_model,
        'started_at': now,
        'updated_at': now,
        'request_headers': summarize_headers(request_headers or {}),
        'client_request': summarize_payload(client_request),
        'metadata': deep_copy_jsonable(metadata or {}),
        'upstream_request': None,
        'upstream_response': None,
        'client_response': None,
        'stream_trace': {
            'upstream_events': [],
            'client_events': [],
            'upstream_total': 0,
            'client_total': 0,
            'upstream_dropped': 0,
            'client_dropped': 0,
            'summary': {},
        },
        'error': None,
    }


def get_conversation_id(*, route: str, payload: dict[str, Any]) -> str:
    """生成会话日志 ID。

    隐私要求下不再根据请求正文生成稳定 ID，避免把 prompt 内容写入或派生进日志。
    """
    return gen_id('conv_')


def attach_upstream_request(turn: dict[str, Any] | None, payload: dict[str, Any], headers: dict[str, Any] | None = None) -> None:
    """记录最终发往上游的请求摘要，不保存请求正文。"""
    if turn is None:
        return
    turn['upstream_request'] = {
        'headers': summarize_headers(headers or {}),
        'body': summarize_payload(payload),
    }
    _touch(turn)


def attach_upstream_response(turn: dict[str, Any] | None, response_data: Any) -> None:
    """记录上游非流式响应摘要，不保存响应正文。"""
    if turn is None:
        return
    turn['upstream_response'] = summarize_payload(response_data)
    _touch(turn)


def attach_client_response(turn: dict[str, Any] | None, response_data: Any) -> None:
    """记录最终返回给客户端的响应摘要，不保存响应正文。"""
    if turn is None:
        return
    turn['client_response'] = summarize_payload(response_data)
    _touch(turn)


def append_upstream_event(turn: dict[str, Any] | None, event: Any) -> None:
    """记录一条上游流式事件摘要，不保存 chunk 内容。"""
    if turn is None:
        return
    _append_stream_event(turn['stream_trace'], 'upstream', summarize_payload(event))
    _touch(turn)


def append_client_event(turn: dict[str, Any] | None, event: Any) -> None:
    """记录一条返回给客户端的流式事件摘要，不保存 chunk 内容。"""
    if turn is None:
        return
    _append_stream_event(turn['stream_trace'], 'client', summarize_payload(event))
    _touch(turn)


def set_stream_summary(turn: dict[str, Any] | None, summary: dict[str, Any]) -> None:
    """记录流式摘要，例如累计文本、事件数、usage 等。"""
    if turn is None:
        return
    turn['stream_trace']['summary'] = sanitize_summary(summary)
    _touch(turn)


def attach_error(turn: dict[str, Any] | None, error: Any) -> None:
    """记录错误摘要，不保存可能包含请求或响应正文的详细内容。"""
    if turn is None:
        return
    turn['error'] = sanitize_summary(error)
    _touch(turn)


def finalize_turn(
    turn: dict[str, Any] | None,
    *,
    usage: dict[str, Any] | None = None,
    duration_ms: int = 0,
) -> None:
    """将 turn 追加/更新到对应的会话日志文件。"""
    if turn is None or settings.get_debug_mode() != 'verbose':
        return

    turn['updated_at'] = datetime.utcnow().isoformat() + 'Z'
    turn['duration_ms'] = duration_ms
    if usage is not None:
        turn['usage'] = deep_copy_jsonable(usage)

    stream_trace = turn.get('stream_trace', {})
    summary = stream_trace.setdefault('summary', {})
    summary['upstream_total'] = stream_trace.get('upstream_total', 0)
    summary['client_total'] = stream_trace.get('client_total', 0)
    summary['upstream_dropped'] = stream_trace.get('upstream_dropped', 0)
    summary['client_dropped'] = stream_trace.get('client_dropped', 0)
    if stream_trace.get('upstream_dropped', 0) or stream_trace.get('client_dropped', 0):
        summary['truncated'] = True

    threading.Thread(target=_write_turn, args=(deep_copy_jsonable(turn),), daemon=True).start()


def sanitize_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """对敏感请求头做脱敏。"""
    sanitized: dict[str, Any] = {}
    for key, value in headers.items():
        key_lower = str(key).lower()
        if key_lower in {'authorization', 'x-api-key', 'api-key', 'x-goog-api-key'}:
            sanitized[key] = _mask_secret(value)
        else:
            sanitized[key] = value
    return sanitized


def summarize_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """只保留排障需要的安全请求头摘要。"""
    sanitized = sanitize_headers(headers)
    allowed = {
        'accept',
        'content-type',
        'user-agent',
        'x-request-id',
        'cf-connecting-ip',
        'cf-ray',
    }
    result: dict[str, Any] = {}
    for key, value in sanitized.items():
        if str(key).lower() in allowed:
            result[key] = value
    return result


def summarize_payload(value: Any) -> Any:
    """生成不含正文内容的结构摘要。

    只记录字段、模型、流式标记、数组长度、usage 等排障元信息。
    """
    if isinstance(value, dict):
        summary: dict[str, Any] = {'fields': sorted(str(k) for k in value.keys())}
        for key in ('id', 'model', 'object', 'type', 'role', 'status', 'finish_reason', 'stream'):
            item = value.get(key)
            if isinstance(item, (str, int, float, bool)) or item is None:
                summary[key] = item

        if isinstance(value.get('messages'), list):
            summary['message_count'] = len(value['messages'])
        if isinstance(value.get('input'), list):
            summary['input_item_count'] = len(value['input'])
        elif 'input' in value:
            summary['input_type'] = type(value.get('input')).__name__
        if isinstance(value.get('tools'), list):
            summary['tool_count'] = len(value['tools'])
        if isinstance(value.get('choices'), list):
            summary['choice_count'] = len(value['choices'])
            finish_reasons = [
                choice.get('finish_reason')
                for choice in value['choices']
                if isinstance(choice, dict) and choice.get('finish_reason') is not None
            ]
            if finish_reasons:
                summary['finish_reasons'] = finish_reasons
        if isinstance(value.get('output'), list):
            summary['output_item_count'] = len(value['output'])
        if isinstance(value.get('content'), list):
            summary['content_block_count'] = len(value['content'])
        if isinstance(value.get('usage'), dict):
            summary['usage'] = sanitize_summary(value['usage'])
        return summary

    if isinstance(value, list):
        return {'type': 'list', 'count': len(value)}

    if isinstance(value, (str, bytes, bytearray)):
        return {'type': type(value).__name__}

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    return {'type': type(value).__name__}


def sanitize_summary(value: Any) -> Any:
    """递归清理摘要，删除可能承载正文的字段。"""
    sensitive_keys = {
        'content',
        'text',
        'input',
        'output',
        'message',
        'messages',
        'delta',
        'arguments',
        'thinking',
        'reasoning',
        'reasoning_content',
        'reasoningContent',
        'raw',
        'body',
        'prompt',
        'instructions',
        'system',
    }
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) in sensitive_keys:
                result[str(key)] = '[redacted]'
            else:
                result[str(key)] = sanitize_summary(item)
        return result
    if isinstance(value, list):
        return [sanitize_summary(item) for item in value]
    if isinstance(value, (str, bytes, bytearray)):
        return '[redacted]'
    return deep_copy_jsonable(value)


def deep_copy_jsonable(value: Any) -> Any:
    """尽量深拷贝 JSON 兼容数据。"""
    try:
        return copy.deepcopy(value)
    except Exception:
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except Exception:
            return str(value)


def _write_turn(turn: dict[str, Any]) -> None:
    conversation_id = turn['conversation_id']
    lock = _get_lock(conversation_id)
    with lock:
        try:
            date_str = turn['started_at'][:10]
            day_dir = os.path.join(_LOG_DIR, date_str)
            os.makedirs(day_dir, exist_ok=True)
            filepath = os.path.join(day_dir, f'{conversation_id}.json')

            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    doc = json.load(f)
            else:
                doc = {
                    'conversation_id': conversation_id,
                    'route': turn.get('route', ''),
                    'created_at': turn['started_at'],
                    'updated_at': turn['updated_at'],
                    'turns': [],
                }

            turns = doc.setdefault('turns', [])
            replaced = False
            for index, existing in enumerate(turns):
                if existing.get('turn_id') == turn.get('turn_id'):
                    turns[index] = turn
                    replaced = True
                    break
            if not replaced:
                turns.append(turn)

            doc['updated_at'] = turn['updated_at']
            doc['last_client_model'] = turn.get('client_model', '')
            doc['last_backend'] = turn.get('backend', '')
            doc['turn_count'] = len(turns)

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(doc, f, ensure_ascii=False, indent=2, default=str)
        except OSError as e:
            logger.warning('写入对话日志失败: %s', e)
        except json.JSONDecodeError as e:
            logger.warning('解析对话日志失败: %s', e)


def _get_lock(conversation_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if conversation_id not in _LOCKS:
            _LOCKS[conversation_id] = threading.Lock()
        return _LOCKS[conversation_id]


def _append_stream_event(stream_trace: dict[str, Any], kind: str, event: Any) -> None:
    events_key = f'{kind}_events'
    total_key = f'{kind}_total'
    dropped_key = f'{kind}_dropped'

    events = stream_trace.setdefault(events_key, [])
    stream_trace[total_key] = stream_trace.get(total_key, 0) + 1

    # 前 KEEP_HEAD 条完整保留；之后只保留最后 KEEP_TAIL 条，
    # 中间部分通过 dropped 计数折叠，避免文件膨胀。
    if len(events) < (_STREAM_KEEP_HEAD + _STREAM_KEEP_TAIL):
        events.append(event)
        return

    head = events[:_STREAM_KEEP_HEAD]
    tail = events[_STREAM_KEEP_HEAD:]
    if len(tail) >= _STREAM_KEEP_TAIL:
        tail.pop(0)
        stream_trace[dropped_key] = stream_trace.get(dropped_key, 0) + 1
    tail.append(event)
    stream_trace[events_key] = head + tail


def _touch(turn: dict[str, Any] | None) -> None:
    if turn is None:
        return
    turn['updated_at'] = datetime.utcnow().isoformat() + 'Z'


def _pick_explicit_conversation_id(payload: dict[str, Any]) -> str:
    candidates = (
        payload.get('conversation_id'),
        payload.get('conversationId'),
        payload.get('session_id'),
        payload.get('sessionId'),
        payload.get('chat_id'),
        payload.get('chatId'),
        payload.get('metadata', {}).get('conversation_id') if isinstance(payload.get('metadata'), dict) else None,
        payload.get('metadata', {}).get('session_id') if isinstance(payload.get('metadata'), dict) else None,
    )
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ''


def _conversation_seed(route: str, payload: dict[str, Any]) -> str:
    """隐私模式下不根据请求正文生成种子。"""
    return route


def _root_seed_from_messages(messages: Any) -> str:
    return ''


def _root_seed_from_responses_input(payload: dict[str, Any]) -> str:
    return ''


def _root_seed_from_responses_items(items: list[Any]) -> str:
    return ''


def _normalize_messages_seed(messages: Any) -> str:
    return ''


def _normalize_content(content: Any) -> Any:
    return '[redacted]'


def _safe_id(raw: str) -> str:
    cleaned = ''.join(ch if ch.isalnum() or ch in ('-', '_', '.') else '_' for ch in raw.strip())
    return cleaned[:120] or gen_id('conv_')


def _mask_secret(value: Any) -> str:
    text = str(value or '')
    if len(text) <= 8:
        return '***'
    return text[:4] + '***' + text[-4:]
