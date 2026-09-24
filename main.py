"""组对象 - AstrBot 群恋爱互动插件。

无前缀精准匹配指令 + 手打四字/引用回复确认。
规则见 README；核心逻辑在 game/core.py（框架无关）。
"""
from __future__ import annotations

import os
import random
import time
from typing import Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .card import (build_card_data, build_menu_card_data,
                   build_record_card_data, build_xiangqin_card_data,
                   load_card_template, plain_card_text)
from .game import copy as tx
from .game import core as game
from .keyword_trigger import extract_plain_text, find_reply_id

# 无前缀精准匹配路由：消息纯文本必须与 key 完全相等
ROUTES = {
    "表白": "confess",
    "同意表白": "confirm_confess_yes",
    "拒绝表白": "confirm_confess_no",
    "离婚": "divorce",
    "同意离婚": "confirm_divorce",
    "抢对象": "steal",
    "拆散": "breakup",
    "送礼": "gift",
    "相亲": "xiangqin",
    "跑路": "flee",
    "我的对象": "mylove",
    "不参与": "optout",
    "参与": "optin",
    "对象帮助": "help",
    "组对象帮助": "help",
    "对象记录": "record",
}


def _extract_message_id(resp) -> Optional[str]:
    """从 OneBot send_group_msg 响应中取 message_id。"""
    if not isinstance(resp, dict):
        return None
    mid = resp.get("message_id")
    if mid is None and isinstance(resp.get("data"), dict):
        mid = resp["data"].get("message_id")
    return str(mid) if mid is not None else None


class ZuDuiPlugin(Star):
    # 渲染视口宽度（px）。必须与 template/bond_card.html 中 body 的 width 一致：
    # body 宽 600 = 卡片 560 + 左右各 20px 的投影留白。
    VIEWPORT_WIDTH = 600

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.curr_dir = os.path.dirname(__file__)
        self.data_dir = os.path.join(
            get_astrbot_plugin_data_path(), "astrbot_plugin_zudui"
        )
        os.makedirs(self.data_dir, exist_ok=True)
        self.store = game.Store(self.data_dir)
        self._card_template = load_card_template(self.curr_dir)
        self._handlers = {
            "confess": self._cmd_confess,
            "confirm_confess_yes": self._cmd_confirm_confess_yes,
            "confirm_confess_no": self._cmd_confirm_confess_no,
            "divorce": self._cmd_divorce,
            "confirm_divorce": self._cmd_confirm_divorce_yes,
            "steal": self._cmd_steal,
            "breakup": self._cmd_breakup,
            "gift": self._cmd_gift,
            "xiangqin": self._cmd_xiangqin,
            "flee": self._cmd_flee,
            "mylove": self._cmd_mylove,
            "optout": self._cmd_optout,
            "optin": self._cmd_optin,
            "help": self._cmd_help,
            "record": self._cmd_record,
        }

    async def terminate(self):
        try:
            self.store.save()
        except Exception:
            pass

    # -------------------------------------------------- 通用小工具

    def _cfg(self, key):
        return game.cfg_get(self.config, key)

    def _group_or_none(self, event) -> Optional[str]:
        gid = event.get_group_id()
        return str(gid) if gid else None

    def _sender(self, event) -> str:
        return str(event.get_sender_id() or "")

    def _mentions(self, event) -> list:
        out = []
        for c in getattr(event.message_obj, "message", []) or []:
            if c.__class__.__name__ == "At":
                qq = str(getattr(c, "qq", "") or "")
                if qq:
                    out.append(qq)
        return out

    def _target(self, event):
        """解析 @ 目标 -> ("ok", uid) / ("bot", None) / ("err", 文案key)。"""
        self_id = str(event.get_self_id() or "")
        mentions = self._mentions(event)
        others = [m for m in mentions if m != self_id]
        if mentions and not others:
            return "bot", None
        if not others:
            return "err", "need_at"
        if len(others) > 1:
            return "err", "need_at_one"
        return "ok", others[0]

    def _name(self, gid, uid) -> str:
        return self.store.name_of(gid, uid)

    def _err(self, e: game.RuleError) -> str:
        return tx.ERRORS.get(e.key, e.key).format(**e.kw)

    def _components(self, event) -> list:
        return getattr(event.message_obj, "message", []) or []

    async def _send_request(self, event, gid, at_uid, head: str, tail: str):
        """发送需要「引用回复」校验的请求广播。

        优先走 OneBot 直发以捕获消息 id 作为锚点；失败则由调用方 yield 兜底文本
        （此时拿不到锚点，确认阶段自动放宽引用校验）。
        返回 (是否已直发, message_id 或 None)。
        """
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None) if bot is not None else None
        if api is not None:
            try:
                segs = [
                    {"type": "text", "data": {"text": head + "\n"}},
                    {"type": "at", "data": {"qq": str(at_uid)}},
                    {"type": "text", "data": {"text": "\n" + tail}},
                ]
                resp = await api.call_action(
                    "send_group_msg", group_id=int(gid), message=segs
                )
                return True, _extract_message_id(resp)
            except Exception as e:
                logger.warning(f"[组对象] 请求消息直发失败，回退普通发送: {e}")
        return False, None

    def _set_anchor(self, gid, to_id, msg_id: Optional[str]) -> None:
        if msg_id:
            p = self.store.data["pending"].get(self.store.ukey(gid, to_id))
            if p:
                p["anchor_msg_id"] = msg_id
                self.store.save()

    # -------------------------------------------------- 表白

    async def _cmd_confess(self, event):
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        me = self._sender(event)
        kind, payload = self._target(event)
        if kind == "bot":
            yield event.plain_result(random.choice(tx.CONFESS_BOT))
            return
        if kind == "err":
            yield event.plain_result(tx.ERRORS[payload])
            return
        try:
            p = game.request_confess(self.store, gid, me, payload, time.time(), self.config)
        except game.RuleError as e:
            yield event.plain_result(self._err(e))
            return
        head = random.choice(tx.CONFESS_HEAD).format(
            a=self._name(gid, me), b=self._name(gid, payload))
        tail = tx.CONFESS_TAIL.format(
            b=self._name(gid, payload), minutes=self._cfg("request_expire_minutes"))
        sent, mid = await self._send_request(event, gid, payload, head, tail)
        self._set_anchor(gid, payload, mid)
        if not sent:
            yield event.plain_result(head + "\n@" + self._name(gid, payload) + " " + tail)

    async def _confirm(self, event, ptype: str, accept: bool):
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        uid = self._sender(event)
        if not uid or uid == str(event.get_self_id() or ""):
            return
        p = self.store.pending_for_target(gid, uid)
        if not p or p["type"] != ptype:
            yield event.plain_result(tx.ERRORS["no_pending"])
            return
        anchor = p.get("anchor_msg_id")
        if anchor and self.config.get("quote_reply_required", True):
            rid = find_reply_id(self._components(event))
            if not rid or rid != str(anchor):
                yield event.plain_result(tx.NEED_QUOTE)
                return
        try:
            if ptype == "confess":
                res = game.confirm_confess(self.store, gid, uid, accept, time.time(), self.config)
            else:
                res = game.confirm_divorce(self.store, gid, uid, accept, time.time(), self.config)
        except game.RuleError as e:
            yield event.plain_result(self._err(e))
            return
        other = self._name(gid, res["from_id"])
        mine = self._name(gid, uid)
        if ptype == "confess":
            if not accept:
                yield event.plain_result(random.choice(tx.CONFESS_REJECT).format(a=other, b=mine))
            elif res["line"] == "betray":
                yield event.plain_result(random.choice(tx.CONFESS_BETRAY).format(
                    a=other, b=mine, x=self._name(gid, res["betrayed_id"])))
            else:
                yield event.plain_result(random.choice(tx.CONFESS_NEW).format(a=other, b=mine))
        else:
            if not accept:
                yield event.plain_result(random.choice(tx.DIVORCE_REJECT).format(a=other, b=mine))
            else:
                yield event.plain_result(random.choice(tx.DIVORCE_DONE).format(a=other, b=mine))

    async def _cmd_confirm_confess_yes(self, event):
        async for r in self._confirm(event, "confess", True):
            yield r

    async def _cmd_confirm_confess_no(self, event):
        async for r in self._confirm(event, "confess", False):
            yield r

    # -------------------------------------------------- 离婚

    async def _cmd_divorce(self, event):
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        me = self._sender(event)
        try:
            p = game.request_divorce(self.store, gid, me, time.time(), self.config)
        except game.RuleError as e:
            yield event.plain_result(self._err(e))
            return
        head = tx.DIVORCE_HEAD.format(a=self._name(gid, me), b=self._name(gid, p["to_id"]))
        tail = tx.DIVORCE_TAIL.format(
            b=self._name(gid, p["to_id"]), minutes=self._cfg("request_expire_minutes"))
        sent, mid = await self._send_request(event, gid, p["to_id"], head, tail)
        self._set_anchor(gid, p["to_id"], mid)
        if not sent:
            yield event.plain_result(head + "\n@" + self._name(gid, p["to_id"]) + " " + tail)

    async def _cmd_confirm_divorce_yes(self, event):
        async for r in self._confirm(event, "divorce", True):
            yield r

    # -------------------------------------------------- 抢对象 / 拆散

    async def _cmd_steal(self, event):
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        me = self._sender(event)
        kind, payload = self._target(event)
        if kind == "bot":
            yield event.plain_result("机器人没有对象，抢不了。")
            return
        if kind == "err":
            yield event.plain_result(tx.ERRORS[payload])
            return
        try:
            res = game.steal(self.store, gid, me, payload, time.time(), self.config)
        except game.RuleError as e:
            yield event.plain_result(self._err(e))
            return
        a, b = self._name(gid, me), self._name(gid, payload)
        if res["success"] and res["restored"]:
            yield event.plain_result(random.choice(tx.STEAL_BACK_SUCCESS).format(
                a=a, b=b, bond=res["bond"]))
        elif res["success"]:
            yield event.plain_result(random.choice(tx.STEAL_SUCCESS).format(
                a=a, b=b, x=self._name(gid, res["ex_id"])))
        else:
            yield event.plain_result(random.choice(tx.STEAL_FAIL).format(
                a=a, b=b, rate=round(res["rate"], 1)))

    async def _cmd_breakup(self, event):
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        me = self._sender(event)
        kind, payload = self._target(event)
        if kind == "bot":
            yield event.plain_result(tx.ERRORS["no_breakup_right"])
            return
        if kind == "err":
            yield event.plain_result(tx.ERRORS[payload])
            return
        try:
            res = game.break_couple(self.store, gid, me, payload, time.time(), self.config)
        except game.RuleError as e:
            yield event.plain_result(self._err(e))
            return
        a, b = self._name(gid, me), self._name(gid, payload)
        if res["success"]:
            yield event.plain_result(random.choice(tx.BREAKUP_SUCCESS).format(
                a=a, b=b, x=self._name(gid, res["ex_id"])))
        else:
            yield event.plain_result(random.choice(tx.BREAKUP_FAIL).format(
                a=a, b=b, rate=round(res["rate"], 1)))

    # -------------------------------------------------- 送礼 / 相亲

    async def _cmd_gift(self, event):
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        me = self._sender(event)
        try:
            res = game.gift(self.store, gid, me, self.config)
        except game.RuleError as e:
            yield event.plain_result(self._err(e))
            return
        yield event.plain_result(random.choice(tx.GIFT).format(
            a=self._name(gid, me), b=self._name(gid, res["partner_id"]),
            aff=res["affection"]))

    async def _cmd_xiangqin(self, event):
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        me = self._sender(event)
        try:
            res = game.xiangqin(self.store, gid, me, self.config)
        except game.RuleError as e:
            yield event.plain_result(self._err(e))
            return
        yield event.plain_result(random.choice(tx.XIANGQIN).format(
            a=self._name(gid, me), b=self._name(gid, res["other_id"])))
        data = build_xiangqin_card_data(self.store, gid, me, self.config)
        if data:
            async for r in self._render_card(event, data):
                yield r

    async def _cmd_flee(self, event):
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        me = self._sender(event)
        other = game.flee_xiangqin(self.store, gid, me)
        if not other:
            yield event.plain_result(tx.ERRORS["no_pairing"])
            return
        yield event.plain_result(random.choice(tx.FLEE).format(
            a=self._name(gid, me), b=self._name(gid, other)))

    # -------------------------------------------------- 我的对象 / 参与

    async def _render_card(self, event, data: dict):
        """渲染卡片图片；失败降级为纯文本。"""
        if self._card_template:
            try:
                url = await self.html_render(
                    self._card_template, data,
                    options={
                        "type": "png", "quality": None,
                        # full_page + 小视口高：截整页，高度随卡片内容自适应，不留底部空白。
                        # viewport_width 必须等于模板 body 宽度（600px），否则视口默认
                        # 800px 会在右侧留下一大块空白。
                        "full_page": True,
                        "viewport_width": self.VIEWPORT_WIDTH,
                        "viewport_height": 100,
                        "scale": "device", "device_scale_factor_level": "high",
                    },
                )
                yield event.image_result(url)
                return
            except Exception as e:
                logger.error(f"[组对象] 卡片渲染失败，降级为文本: {e}")
        yield event.plain_result(plain_card_text(data))

    async def _cmd_mylove(self, event):
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        me = self._sender(event)
        kind, payload = self._target(event)
        if kind == "err":
            # 「我的对象」的 @ 是可选的：没 @ 任何人就看自己的卡，@ 了多人才报错。
            if payload != "need_at":
                yield event.plain_result(tx.ERRORS[payload])
                return
            kind = "self"
        if kind == "bot":
            yield event.plain_result(random.choice(tx.CONFESS_BOT))
            return
        view_uid = payload if kind == "ok" else me
        data = build_card_data(self.store, gid, view_uid, self.config)
        async for r in self._render_card(event, data):
            yield r

    async def _cmd_optout(self, event):
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        try:
            game.set_opt_out(self.store, gid, self._sender(event), True)
        except game.RuleError as e:
            yield event.plain_result(self._err(e))
            return
        yield event.plain_result(tx.OPT_OUT_ON)

    async def _cmd_optin(self, event):
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        game.set_opt_out(self.store, gid, self._sender(event), False)
        yield event.plain_result(tx.OPT_OUT_OFF)

    # -------------------------------------------------- 帮助菜单

    async def _cmd_help(self, event):
        """渲染插件菜单卡；渲染失败会自动降级为纯文本。"""
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        data = build_menu_card_data(self.store, gid, self._sender(event), self.config)
        async for r in self._render_card(event, data):
            yield r

    async def _cmd_record(self, event):
        """渲染本群对象记录卡（所有恋爱组合 + 相亲配对，成对去重）。"""
        gid = self._group_or_none(event)
        if not gid:
            yield event.plain_result("请在群聊里使用～")
            return
        data = build_record_card_data(self.store, gid, self._sender(event), self.config)
        async for r in self._render_card(event, data):
            yield r

    # -------------------------------------------------- 指令注册（/ 前缀）

    @filter.command("表白")
    async def confess_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_confess(event):
            yield r
        event.stop_event()

    @filter.command("同意表白")
    async def confirm_confess_yes_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_confirm_confess_yes(event):
            yield r
        event.stop_event()

    @filter.command("拒绝表白")
    async def confirm_confess_no_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_confirm_confess_no(event):
            yield r
        event.stop_event()

    @filter.command("离婚")
    async def divorce_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_divorce(event):
            yield r
        event.stop_event()

    @filter.command("同意离婚")
    async def confirm_divorce_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_confirm_divorce_yes(event):
            yield r
        event.stop_event()

    @filter.command("抢对象", alias={"抢人"})
    async def steal_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_steal(event):
            yield r
        event.stop_event()

    @filter.command("拆散")
    async def breakup_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_breakup(event):
            yield r
        event.stop_event()

    @filter.command("送礼")
    async def gift_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_gift(event):
            yield r
        event.stop_event()

    @filter.command("相亲")
    async def xiangqin_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_xiangqin(event):
            yield r
        event.stop_event()

    @filter.command("跑路")
    async def flee_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_flee(event):
            yield r
        event.stop_event()

    @filter.command("我的对象", alias={"缘分卡"})
    async def mylove_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_mylove(event):
            yield r
        event.stop_event()

    @filter.command("不参与")
    async def optout_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_optout(event):
            yield r
        event.stop_event()

    @filter.command("参与")
    async def optin_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_optin(event):
            yield r
        event.stop_event()

    @filter.command("对象帮助", alias={"组对象帮助"})
    async def help_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_help(event):
            yield r
        event.stop_event()

    @filter.command("对象记录")
    async def record_cmd(self, event: AstrMessageEvent):
        async for r in self._cmd_record(event):
            yield r
        event.stop_event()

    # -------------------------------------------------- 无前缀精准匹配

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def keyword_trigger(self, event: AstrMessageEvent):
        gid = self._group_or_none(event)
        if not gid:
            return
        me = self._sender(event)
        if not me or me == str(event.get_self_id() or ""):
            return
        self.store.remember_name(gid, me, event.get_sender_name())

        if event.is_at_or_wake_command:
            # @机器人 / 唤醒前缀场景交给 @filter.command 处理，避免双重触发
            return
        if not self.config.get("keyword_trigger_enabled", True):
            return
        text = extract_plain_text(self._components(event)) or (event.message_str or "").strip()
        if not text or text.startswith(("/", "!", "！")):
            return
        action = ROUTES.get(text)  # 精准匹配：整条消息必须等于指令词
        if not action:
            return
        handler = self._handlers.get(action)
        if handler:
            async for result in handler(event):
                yield result
        # 结果全部 yield 之后再终止事件传播：先 stop 会让调度器提前 break，
        # 导致生成器里后续的消息（如相亲卡图片）发不出去。
        event.stop_event()
