# Copyright (c) 2026 xingluosama121, MIT Licensed
"""架构层（ArchLayer）：槽位连接器。

ArchLayer 是「搭积木」的积木盘：

1. 接收一组槽位值（关键字参数 / 配置字典）；
2. 每个槽位不填 → 使用默认实现（库内置逻辑）；
3. 填了地址 → 解析地址（norpagent.arch.address）并把实现接上槽位；
4. 工厂类地址按签名裁剪注入上下文（layer / slot / config / ...），
   工厂不声明的键自动忽略，保证任意风格的工厂都能接入。

装配完成后 ``layer[slot]`` 直接取到实现对象，
``layer.describe()`` 打印完整装配清单（系统性工程的可观测性）。
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Optional

from norpagent.arch.address import AddressError, resolve_address
from norpagent.arch.slots import SLOT_SPECS, SlotSpec


def call_factory(factory: Any, ctx: Dict[str, Any]) -> Any:
    """按签名裁剪调用工厂（地址函数的标准调用约定）。

    - 工厂是可调用对象（函数 / 类）→ 调用，注入 ctx 中工厂
      签名接受的键；工厂完全不接受上下文时无参调用兜底；
    - 工厂是不可调用对象（模块 / 实例 / 值）→ 原样返回。

    这样，同一个槽位既能接「文件级模块实现」，也能接
    「带上下文的工厂函数」，还能接「现成实例」。
    """
    if not callable(factory):
        return factory
    try:
        sig = inspect.signature(factory)
    except (TypeError, ValueError):
        # 内建等无法取签名的可调用对象：无参调用
        return factory()
    params = sig.parameters
    has_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    kwargs: Dict[str, Any] = {}
    for key, value in ctx.items():
        param = params.get(key)
        if param is not None:
            if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY):
                kwargs[key] = value
        elif has_var_kw:
            kwargs[key] = value
    return factory(**kwargs)


class ArchLayer:
    """架构层：一次 np() 启动的完整装配面。

    用法::

        layer = ArchLayer(async_loop="myapp.loop:create", preset="standard")
        layer.connect()
        loop = layer["async_loop"]        # 已接好的事件循环系统
        layer.describe()                  # 打印装配清单
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        **slot_values: Any,
    ) -> None:
        # 合并 config 字典与关键字：关键字优先（更具体）
        self.config: Dict[str, Any] = dict(config or {})
        self.config.update({k: v for k, v in slot_values.items() if v is not None})
        self._impls: Dict[str, Any] = {}
        self._defaults: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        self._connected = False

    # ── 默认实现登记 ──────────────────────────────────────

    def set_default(self, slot: str, factory: Callable[[Dict[str, Any]], Any]) -> None:
        """为槽位登记默认实现工厂（ctx -> 实现）。

        装配器（runtime.mount）在连接前调用，把「库内置逻辑」
        登记为各槽位的默认实现；用户填了地址则优先地址。
        """
        if slot not in SLOT_SPECS:
            raise KeyError(f"未知槽位 '{slot}'。可用槽位: {list(SLOT_SPECS)}")
        self._defaults[slot] = factory

    # ── 连接 ──────────────────────────────────────────────

    def connect(self) -> "ArchLayer":
        """解析并装配全部槽位（幂等：重复调用返回已装配结果）。"""
        if self._connected:
            return self
        for slot in SLOT_SPECS:
            self._impls[slot] = self._connect_slot(slot)
        self._connected = True
        return self

    def _connect_slot(self, slot: str) -> Any:
        spec: SlotSpec = SLOT_SPECS[slot]
        value = self.config.get(slot)
        if value is None:
            default_factory = self._defaults.get(slot)
            if default_factory is None:
                # 语义：该槽位未指定 → 实现为 None，
                # 由装配器按「预设声明」的默认逻辑处理
                # （如 model / tools / session 等组件槽位）。
                return None
            return default_factory(self._context(slot, {}))
        # 填了地址 / 值：按槽位声明的字符串语义处理
        semantics = spec.string_semantics
        if isinstance(value, str):
            if semantics in ("name", "literal"):
                # 注册表组件名 / 字面值：原样透传，由装配器语义化
                return value
            if semantics == "name_or_address":
                # 先按名、后按地址的判定放在装配器（需要注册表上下文）
                return value
        elif semantics in ("name", "literal", "name_or_address"):
            # 非字符串值（实例 / 回调 / 类）：同样原样透传，
            # 由装配器决定如何注册 / 调用（不能当作地址工厂）。
            return value
        # 默认语义 "address"：字符串解析为模块地址
        impl = resolve_address(value, slot=slot)
        sub_config = {}
        if isinstance(value, str) and ";" in value:
            sub_config = self._parse_subconfig(value)
        if callable(impl):
            return call_factory(impl, self._context(slot, sub_config or {}))
        return impl

    @staticmethod
    def _parse_subconfig(address: str) -> Dict[str, str]:
        """解析字符串地址中的附加配置子句。

        形如 ``"pkg.mod:create;port=9000;theme=dark"`` —— 分号后的
        ``键=值`` 对被解析为附加配置，注入工厂的 config 参数。
        纯地址（无分号）返回空字典。
        """
        if ";" not in address:
            return {}
        parts = address.split(";")
        cfg: Dict[str, str] = {}
        for pair in parts[1:]:
            if "=" in pair:
                k, _, v = pair.partition("=")
                cfg[k.strip()] = v.strip()
        return cfg

    def _context(self, slot: str, sub_config: Dict[str, Any]) -> Dict[str, Any]:
        """构造传给工厂的上下文（统一注入键）。"""
        cfg = dict(sub_config)
        for key, value in self.config.items():
            if key != slot:
                cfg.setdefault(key, value)
        return {
            "layer": self,
            "slot": slot,
            "config": cfg,
        }

    # ── 查询 ──────────────────────────────────────────────

    def __getitem__(self, slot: str) -> Any:
        if not self._connected:
            raise RuntimeError("架构层尚未 connect()，先调用 layer.connect()")
        return self._impls[slot]

    def get(self, slot: str, default: Any = None) -> Any:
        if not self._connected:
            return default
        return self._impls.get(slot, default)

    def describe(self) -> str:
        """装配清单：每个槽位的来源（默认 / 地址）与实现。"""
        lines = ["== NorpAgent 架构层装配清单 =="]
        for slot, spec in SLOT_SPECS.items():
            value = self.config.get(slot)
            impl = self._impls.get(slot)
            if value is None:
                source = "默认逻辑"
            elif isinstance(value, str):
                source = f"地址 {value!r}"
            else:
                source = f"直接值 {type(value).__name__}"
            impl_repr = (
                type(impl).__name__ if impl is not None else "(未连接)"
            )
            lines.append(f"  {slot:<16} <- {source:<28} => {impl_repr}")
        return "\n".join(lines)

    def is_connected(self) -> bool:
        return self._connected


__all__ = ["ArchLayer", "call_factory"]
