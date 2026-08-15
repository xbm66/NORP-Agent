# Vibe Coding Agent - 视觉/操作外挂 IPC 协议层（自定规范）
# Copyright (c) 2026 xingluosama
#
# 消息格式（见 docs/vision_agent_design.md 第 4 节）：
#   外层 XML 标签作信封（承载控制元信息），内层 JSON 承载业务数据（CDATA 包裹）。
#   - 指令消息（主架构 → 外挂）：<vision-op ...><payload><![CDATA[{...}]]></payload></vision-op>
#   - 结果消息（外挂 → 主架构）：<vision-result ...><payload>...</payload></vision-result>
#
# 信封/负载分离：XML 属性只放控制字段（version/op/risk/token/ttl_ms/ts/status），
# 业务数据（坐标、文本、窗口信息）全部沉到 JSON，互不污染。
#
# 本模块只负责「编解码 + 信封校验」，不含传输层（命名管道 / socket 等）。

import json
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from xml.sax.saxutils import quoteattr

PROTOCOL_VERSION = "1.0"

OP_TAG = "vision-op"
RESULT_TAG = "vision-result"


class ResultStatus:
    """结果消息 status 枚举。"""
    APPROVED = "approved"          # 已放行并执行
    REJECTED = "rejected"          # 被裁决器拒绝
    VETOED = "vetoed"              # 被用户否决
    CIRCUIT_OPEN = "circuit_open"  # 熔断中
    TIMEOUT = "timeout"            # 令牌/指令超时


class IPCError(Exception):
    """协议编解码/校验错误。"""


@dataclass
class Message:
    """解析后的消息对象。"""
    kind: str                       # "op" | "result"
    op: str
    payload: Dict[str, Any]
    version: str = PROTOCOL_VERSION
    risk: Optional[str] = None      # L0~L3（op 消息必填）
    token: Optional[str] = None     # 一次性授权令牌（op 消息必填）
    ttl_ms: Optional[int] = None    # 令牌有效期（毫秒）
    ts: Optional[int] = None        # 时间戳（毫秒）
    status: Optional[str] = None    # result 消息的 status 枚举


def _now_ms() -> int:
    return int(time.time() * 1000)


def _attr(name: str, value) -> str:
    """构造 XML 属性（含转义，防注入）。value 为 None 时省略。"""
    if value is None:
        return ""
    return f" {name}={quoteattr(str(value))}"


def _payload_to_cdata(payload: Dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    # CDATA 不能包含 "]]>" 序列，出现则拒绝（业务数据里几乎不可能出现）
    if "]]>" in payload_json:
        raise IPCError("payload 含非法序列 ]]>，无法安全放入 CDATA")
    return payload_json


def build_op(
    op: str,
    risk: str,
    payload: Dict[str, Any],
    token: str,
    ttl_ms: int = 3000,
    ts: Optional[int] = None,
    version: str = PROTOCOL_VERSION,
) -> str:
    """构造指令消息（主架构 → 外挂）。"""
    if not token:
        raise IPCError("指令消息必须有 token")
    ts = _now_ms() if ts is None else int(ts)
    cdata = _payload_to_cdata(payload)
    return (
        f'<{OP_TAG}{_attr("version", version)}{_attr("op", op)}{_attr("risk", risk)}'
        f'{_attr("token", token)}{_attr("ttl_ms", ttl_ms)}{_attr("ts", ts)}>'
        f"<payload><![CDATA[{cdata}]]></payload>"
        f"</{OP_TAG}>"
    )


def build_result(
    op: str,
    status: str,
    payload: Dict[str, Any],
    risk: Optional[str] = None,
    ts: Optional[int] = None,
    version: str = PROTOCOL_VERSION,
) -> str:
    """构造结果/审计消息（外挂 → 主架构）。"""
    ts = _now_ms() if ts is None else int(ts)
    cdata = _payload_to_cdata(payload)
    return (
        f'<{RESULT_TAG}{_attr("version", version)}{_attr("op", op)}'
        f'{_attr("status", status)}{_attr("risk", risk)}{_attr("ts", ts)}>'
        f"<payload><![CDATA[{cdata}]]></payload>"
        f"</{RESULT_TAG}>"
    )


def parse_message(xml_str: str) -> Message:
    """解析消息（op / result），返回 Message。"""
    if not xml_str or not xml_str.strip():
        raise IPCError("空消息")
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        raise IPCError(f"XML 解析失败：{e}")

    tag = root.tag
    if tag == OP_TAG:
        kind = "op"
    elif tag == RESULT_TAG:
        kind = "result"
    else:
        raise IPCError(f"未知消息根元素：{tag}")

    op = root.get("op") or ""
    if not op:
        raise IPCError("缺少 op 字段")

    # payload 在 CDATA 中，ET 解析后成为 <payload> 元素的 text
    payload_node = root.find("payload")
    payload_text = (payload_node.text or "") if payload_node is not None else ""
    payload_text = payload_text.strip()
    try:
        payload = json.loads(payload_text) if payload_text else {}
    except json.JSONDecodeError as e:
        raise IPCError(f"payload JSON 解析失败：{e}")
    if not isinstance(payload, dict):
        raise IPCError("payload 必须是 JSON 对象")

    def _int_opt(key):
        v = root.get(key)
        return int(v) if v else None

    return Message(
        kind=kind,
        op=op,
        payload=payload,
        version=root.get("version", PROTOCOL_VERSION),
        risk=root.get("risk"),
        token=root.get("token"),
        ttl_ms=_int_opt("ttl_ms"),
        ts=_int_opt("ts"),
        status=root.get("status"),
    )


def validate_op(
    msg: Message,
    expected_token: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> Tuple[bool, str]:
    """校验指令消息：类型 / 版本 / 令牌 / 有效期。返回 (是否通过, 原因)。"""
    if msg.kind != "op":
        return False, f"不是指令消息（kind={msg.kind}）"
    if msg.version != PROTOCOL_VERSION:
        return False, f"协议版本不匹配：收到 {msg.version}，期望 {PROTOCOL_VERSION}"
    if not msg.token:
        return False, "缺少授权令牌"
    if expected_token is not None and msg.token != expected_token:
        return False, "令牌不匹配"
    if msg.ts is None or msg.ttl_ms is None:
        return False, "缺少时间戳或有效期"
    now = _now_ms() if now_ms is None else int(now_ms)
    if now - msg.ts > msg.ttl_ms:
        return False, "指令已过期（ttl 超时）"
    return True, "ok"
