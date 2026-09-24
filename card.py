"""组对象 - 卡片数据构建与降级文本（恋爱 / 单身 / 相亲三种卡）。"""
from __future__ import annotations

import os
import random
import time

from .game import copy as tx
from .game import core as game


def load_card_template(curr_dir: str) -> str | None:
    path = os.path.join(curr_dir, "template", "bond_card.html")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def avatar_url(uid: str) -> str:
    return f"https://q1.qlogo.cn/g?b=qq&nk={uid}&s=640"



# ---------------------------------------------------------------- 卡片装饰（随好感度递增）

# 档位 -> 飘落心形数量（越多越繁华）
_HEART_COUNT = {"lv1": 0, "lv2": 0, "lv3": 6, "lv4": 9, "lv5": 13}
# 档位 -> 星点数量（lv4 起出现，金色闪光）
_STAR_COUNT = {"lv1": 0, "lv2": 0, "lv3": 0, "lv4": 5, "lv5": 9}
_HEART_CHARS = ("❤", "♥", "💗")
_STAR_CHARS = ("✦", "✧", "✩", "⋆")


def _rand_spots(n: int, chars, y_range=(8, 92), size_range=(11, 20)):
    """随机撒点：返回 [{"x","y","size","ch"}]。

    装饰是为「繁华感」服务的，必须避开正文，否则会压在昵称/数值上影响阅读。
    因此只在卡片两侧的窄带撒点（左 2%~17%、右 83%~98%）。
    """
    spots = []
    for _ in range(n):
        # 卡片内边距仅 34px，装饰带必须贴在两条边缘的窄条里才不会压字
        if random.random() < 0.5:
            x = random.uniform(0.5, 5.5)    # 左边缘带（约 3~31px）
        else:
            x = random.uniform(94.5, 99)    # 右边缘带（约 529~554px）
        spots.append({
            "x": round(x, 1),
            "y": round(random.uniform(*y_range), 1),
            "size": random.randint(*size_range),
            "ch": random.choice(chars),
        })
    return spots


def build_deco(theme: str) -> dict:
    """按主题档位生成装饰元素。lv1/lv2 无飘落元素，lv3+ 逐渐繁华。"""
    return {
        "deco_hearts": _rand_spots(_HEART_COUNT.get(theme, 0), _HEART_CHARS),
        "deco_stars": _rand_spots(_STAR_COUNT.get(theme, 0), _STAR_CHARS,
                                  y_range=(10, 88), size_range=(9, 16)),
    }

# ---------------------------------------------------------------- 恋爱卡

def theme_of(affection: int, cap: int) -> str:
    """缘分卡主题：按好感度分 5 档（0-20 绿 / 21-40 蓝 / 41-60 黄 / 61-80 橙 / 81-100 红）。

    实现复用 game.theme_key，保证与「对象记录」卡片的分档口径完全一致。
    """
    return game.theme_key(affection, cap)


def build_couple_card_data(store: game.Store, gid, uid, cfg) -> dict | None:
    now_ts = time.time()
    view = game.card_view(store, gid, uid, now_ts, cfg)
    if not view:
        return None
    cap = game.cfg_get(cfg, "affection_cap")
    return {
        "title": "💍 缘 分 卡 💍",
        "badge": "🛡 新婚保护中" if view["in_safety"] else "",
        "layout": "two",
        "heart": "❤️",
        "a_name": store.name_of(gid, view["uid"]),
        "a_avatar": avatar_url(view["uid"]),
        "b_name": store.name_of(gid, view["partner_id"]),
        "b_avatar": avatar_url(view["partner_id"]),
        "theme": theme_of(view["affection"], cap),
        **build_deco(theme_of(view["affection"], cap)),
        "rows": [
            {"label": "好感度", "value": f"{view['affection']}/{cap}", "bar": view["affection"]},
            {"label": "被抢成功率", "value": f"{view['rate']}%", "bar": None},
            {"label": "在一起", "value": f"第 {view['days']} 天", "bar": None},
            {"label": "被抢次数", "value": f"{view['steal_total']} 次", "bar": None},
        ],
        "quote": random.choice(tx.LOVE_QUOTES),
    }


# ---------------------------------------------------------------- 单身卡

def _bond_line(store: game.Store, gid, uid) -> str:
    u = store.user(gid, uid)
    kind, target, value = u.get("bond_kind"), u.get("bond_target_id"), u.get("bond_value", 0)
    if kind == "steal":
        return f"抢回 {store.name_of(gid, target)} 可恢复 {value} 点好感"
    if kind == "breakup":
        return f"拆散权 ×1（对 {store.name_of(gid, target)}）"
    return "—"


def build_single_card_data(store: game.Store, gid, uid, cfg) -> dict:
    other = game.xiangqin_partner(store, gid, uid)
    u = store.user(gid, uid)
    limit = game.cfg_get(cfg, "steal_daily_limit")
    used = u["steal_used"] if u.get("steal_date") == game._today() else 0
    # 相亲额度：只统计「主动发起」，被动被匹配不占
    xq_used = u.get("xq_used", 0) if u.get("xq_date") == game._today() else 0
    if other:
        line = f"与 {store.name_of(gid, other)} 相亲中"
    elif xq_used >= 1:
        line = "今日相亲次数已用完"
    else:
        line = "可相亲 1 次"
    rows = [
        {"label": "感情状态", "value": "快乐单身", "bar": None},
        {"label": "今日缘分", "value": line, "bar": None},
        {"label": "持有羁绊", "value": _bond_line(store, gid, uid), "bar": None},
        {"label": "今日行动", "value": f"抢人/拆散 剩 {max(0, limit - used)}/{limit} 次 · 相亲 剩 {max(0, 1 - xq_used)}/1 次", "bar": None},
    ]
    return {
        "title": "🕊 单 身 卡 🕊",
        "badge": "🛡 免打扰中" if u.get("opt_out") else "",
        "layout": "one",
        "heart": "",
        "a_name": store.name_of(gid, uid),
        "a_avatar": avatar_url(uid),
        "b_name": "", "b_avatar": "",
        "theme": "single",
        "rows": rows,
        "quote": random.choice(tx.SINGLE_QUOTES),
    }


# ---------------------------------------------------------------- 相亲卡

def build_xiangqin_card_data(store: game.Store, gid, uid, cfg) -> dict | None:
    other = game.xiangqin_partner(store, gid, uid)
    if not other:
        return None
    return {
        "title": "🍵 相 亲 卡 🍵",
        "badge": "⏳ 今日 0 点自动散场",
        "layout": "two",
        "heart": "🍵",
        "a_name": store.name_of(gid, uid),
        "a_avatar": avatar_url(uid),
        "b_name": store.name_of(gid, other),
        "b_avatar": avatar_url(other),
        "theme": "tea",
        "rows": [
            {"label": "关系类型", "value": "相亲 · 临时缘分（不算对象）", "bar": None},
            {"label": "被抢保护", "value": "抢人只认已婚对象，抢不走", "bar": None},
            {"label": "缘分终止", "value": "0 点散场 / 任一方表白脱单 / 跑路", "bar": None},
            {"label": "好感度", "value": "相亲没有好感度", "bar": None},
        ],
        "quote": random.choice(tx.XIANGQIN_QUOTES),
    }



# ---------------------------------------------------------------- 对象记录卡

def build_record_card_data(store: game.Store, gid, uid, cfg) -> dict:
    """本群「对象记录」卡片：列出所有恋爱组合与相亲配对。

    去重规则：一对人只渲染一次（A-B 与 B-A 视为同一条）。
    """
    rec = game.group_record(store, gid, time.time(), cfg)
    cap = game.cfg_get(cfg, "affection_cap")

    couples = []
    for c in rec["couples"]:
        couples.append({
            "a_name": c["a_name"], "b_name": c["b_name"],
            "affection": c["affection"],
            "bar": max(0, min(100, int(c["affection"] * 100 / cap) if cap else 0)),
            "theme": c["theme"],
            "tag": ("🛡保护期" if c["in_safety"] else
                    ("⚡抢来的" if c["origin"] == "steal" else "")),
            "days": c["days"],
        })

    teas = [{"a_name": t["a_name"], "b_name": t["b_name"]} for t in rec["teas"]]

    return {
        "title": "📜 本 群 对 象 记 录 📜",
        "badge": "",
        "layout": "record",
        "heart": "",
        "a_name": "", "a_avatar": "", "b_name": "", "b_avatar": "",
        "theme": "record",
        "couples": couples,
        "teas": teas,
        "couple_count": rec["couple_count"],
        "tea_count": rec["tea_count"],
        "single_count": rec["single_count"],
        "rows": [],
        "quote": "缘分天注定，记录在此留档",
    }

# ---------------------------------------------------------------- 帮助菜单卡

def build_menu_card_data(store: game.Store, gid, uid, cfg) -> dict:
    """插件菜单卡：按分组列出全部指令，并显示当前生效的关键配置。"""
    cap = game.cfg_get(cfg, "affection_cap")
    base = game.cfg_get(cfg, "base_rate")
    k = game.cfg_get(cfg, "rate_k")
    daily = game.cfg_get(cfg, "daily_affection")
    gift = game.cfg_get(cfg, "gift_affection")
    steal_n = game.cfg_get(cfg, "steal_daily_limit")
    safety = game.cfg_get(cfg, "safety_hours")
    expire = game.cfg_get(cfg, "request_expire_minutes")
    tcap = game.cfg_get(cfg, "target_steal_cap")

    # 命中率示例：好感 0 与 好感 100 时的被抢成功率
    rate0 = max(0.0, base - k * 0)
    rate_full = max(0.0, base - k * cap)

    groups = [
        {"group": "恋爱", "items": [
            {"cmd": "表白 @某人", "desc": "向对方表白，等 TA 确认"},
            {"cmd": "同意表白 / 拒绝表白", "desc": "引用机器人的请求消息回复"},
            {"cmd": "离婚", "desc": "发起离婚，对方确认后分手"},
            {"cmd": "同意离婚", "desc": "确认分手，好感清零"},
        ]},
        {"group": "抢人", "items": [
            {"cmd": "抢对象 @某人", "desc": f"抢走 TA 的对象，每日 {steal_n} 次"},
            {"cmd": "拆散 @前对象", "desc": "被出轨方的一次性清算权"},
        ]},
        {"group": "相亲", "tag": "独立系统", "note": "相亲不算对象：不挡表白/抢人，也不占它们的次数。每人每天可主动发起 1 次；被动被匹配不扣次数，跑路后仍可自己发起。任一方脱单即自动散场。", "items": [
            {"cmd": "相亲", "desc": "随机匹配临时缘分，每天主动发起 1 次"},
            {"cmd": "跑路", "desc": "结束本次相亲（主动发起的那次不退还）"},
        ]},
        {"group": "其他", "items": [
            {"cmd": "送礼", "desc": f"给对象 +{gift} 好感，每天 1 次"},
            {"cmd": "我的对象（可 @他人）", "desc": "查看缘分卡 / 相亲卡 / 单身卡"},
            {"cmd": "不参与 / 参与", "desc": "免疫表白、相亲、被抢、被送礼"},
            {"cmd": "对象记录", "desc": "本群所有对象组合 + 相亲组（成对只列一次）"},
            {"cmd": "对象帮助", "desc": "就是这张菜单（别名：组对象帮助）"},
        ]},
    ]

    tips = [
        {"label": "好感度", "value": f"0 ~ {cap}，每天自动 +{daily}，送礼 +{gift}"},
        {"label": "被抢成功率", "value": f"{rate0:.0f}%（好感 0）→ {rate_full:.0f}%（好感 {cap}）"},
        {"label": "新婚保护", "value": f"表白成功后 {safety} 小时内不可被抢"},
        {"label": "确认时限", "value": f"{expire} 分钟内回复，超时作废"},
        {"label": "同目标被抢", "value": f"每天最多 {tcap} 次"},
    ]

    return {
        "title": "组 对 象 帮 助",
        "badge": "",
        "layout": "menu",
        "heart": "",
        "a_name": "", "a_avatar": "", "b_name": "", "b_avatar": "",
        "theme": "menu",
        "groups": groups,
        "tips": tips,
        "rows": [],
        "quote": "直接发送指令词即可，无需前缀；带参数的指令记得 @ 人",
    }


# ---------------------------------------------------------------- 渲染辅助

def build_card_data(store: game.Store, gid, uid, cfg) -> dict | None:
    """按状态自动选卡：恋爱 -> 相亲中 -> 单身。"""
    data = build_couple_card_data(store, gid, uid, cfg)
    if data:
        return data
    data = build_xiangqin_card_data(store, gid, uid, cfg)
    if data:
        return data
    return build_single_card_data(store, gid, uid, cfg)


def plain_card_text(data: dict) -> str:
    lines = [data["title"]]
    if data.get("badge"):
        lines.append(data["badge"])

    # 对象记录卡
    if data.get("layout") == "record":
        cs = data.get("couples", [])
        ts = data.get("teas", [])
        lines.append(f"单身 {data.get('single_count',0)} 人 · 恋爱 {len(cs)} 对 · 相亲 {len(ts)} 对")
        lines.append("")
        lines.append("【恋爱中】" if cs else "【恋爱中】暂无")
        for c in cs:
            tag = f" {c['tag']}" if c.get("tag") else ""
            lines.append(f"  {c['a_name']} ❤ {c['b_name']}  好感 {c['affection']}{tag}")
        lines.append("")
        lines.append("【相亲中】" if ts else "【相亲中】暂无")
        for t in ts:
            lines.append(f"  {t['a_name']} 🍵 {t['b_name']}")
    # 菜单卡：分组罗列指令 + 关键参数
    elif data.get("layout") == "menu":
        for g in data.get("groups", []):
            lines.append("")
            title = g["group"] + (f"（{g['tag']}）" if g.get("tag") else "")
            lines.append(title)
            if g.get("note"):
                lines.append(f"  ※ {g['note']}")
            for it in g["items"]:
                lines.append(f"  {it['cmd']} — {it['desc']}")
        if data.get("tips"):
            lines.append("")
            lines.append("参数")
            for t in data["tips"]:
                lines.append(f"  {t['label']}：{t['value']}")
    else:
        if data["layout"] == "two":
            lines.append(f"{data['a_name']} ❤ {data['b_name']}")
        else:
            lines.append(data["a_name"])
        for r in data["rows"]:
            lines.append(f"{r['label']}：{r['value']}")

    if data.get("quote"):
        lines.append(f"「 {data['quote']} 」")
    return "\n".join(lines)
