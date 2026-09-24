"""组对象 - 核心游戏逻辑与数据存储。

本模块不依赖 AstrBot，纯标准库实现，可独立单元测试。
约定：
- 所有"每日"额度按自然日（服务器本地时区 0 点）结算，存最后日期戳，不做每日清零任务。
- 好感度懒结算：关系被访问时按天数补算。
- 抢人成功率 = base_rate - rate_k × 好感度（%），好感 0 → 80%，100 → 10%。
- 安全期只保护「表白成功」建立的关系；「抢来的」关系无安全期。
- 羁绊（被抢走）：抢回成功时好感恢复为 被抢时好感÷2，永不过期。
- 拆散权（被出轨）：一次性，成败都消耗，不受安全期限制。
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from datetime import datetime

# ---------------------------------------------------------------- 基础工具

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _parse_day(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


# 进程内写锁：当前所有写操作都在 asyncio 事件循环内串行执行（save() 无 await 点），
# 本身不会并发。加锁是为了防止将来引入线程池后多个写者抢同一个 .tmp 文件
# （固定 tmp 名会在 os.replace 时抛 FileNotFoundError）。
_WRITE_LOCK = threading.Lock()


def _atomic_write(path: str, data) -> None:
    tmp = path + ".tmp"
    with _WRITE_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)


class RuleError(Exception):
    """业务规则错误。key 对应文案 key，kw 为格式化参数。"""

    def __init__(self, key: str, **kw):
        super().__init__(key)
        self.key = key
        self.kw = kw


DEFAULT_CONFIG = {
    "base_rate": 80,
    "rate_k": 0.7,
    "affection_cap": 100,
    "daily_affection": 5,
    "gift_affection": 2,
    "request_expire_minutes": 5,
    "target_steal_cap": 3,
    "safety_hours": 24,
    "steal_daily_limit": 1,
    "confess_daily_limit": 1,
}


def cfg_get(cfg: dict | None, key: str):
    src = cfg or {}
    default = DEFAULT_CONFIG[key]
    v = src.get(key, default)
    try:
        if isinstance(default, float):
            return float(v)
        if isinstance(default, int):
            return int(v)
    except (TypeError, ValueError):
        return default
    return v


# ---------------------------------------------------------------- 存储

class Store:
    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "db.json")
        self.data = {
            "users": {},      # "gid:uid" -> 用户档案
            "relations": {},  # rid -> 关系（好感度为双方共享值）
            "pending": {},    # "gid:to_id" -> 待确认请求（表白/离婚）
            "xiangqin": {},   # gid -> {"date", "pairs": {uid: other}}
            "names": {},      # "gid:uid" -> {"name", "ts"} 发过言的群友名字缓存
            "seq": 1,
        }
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                for k in self.data:
                    if k in loaded:
                        self.data[k] = loaded[k]
            except Exception:
                # 存档损坏时不静默清空：把坏文件改名保留，便于事后人工找回。
                # 插件仍会以空数据继续加载，避免一个坏文件导致插件无法启用。
                try:
                    bak = f"{self.path}.corrupt-{int(time.time())}"
                    os.replace(self.path, bak)
                    print(f"[组对象] db.json 解析失败，已备份为 {bak}，将以空数据启动")
                except Exception:
                    pass

    def save(self) -> None:
        _atomic_write(self.path, self.data)

    @staticmethod
    def ukey(gid, uid) -> str:
        return f"{gid}:{uid}"

    # ---- 用户 ----
    # 新档案的默认字段。老版本存档缺少的字段会在 user() 里按此补齐，
    # 因此升级后无需手动迁移 db.json。
    _USER_DEFAULTS = {
        "rel_id": None, "opt_out": False,
        "steal_date": "", "steal_used": 0,
        "confess_date": "", "divorce_date": "",
        "gift_date": "", "gift_used": 0,
        "xq_date": "", "xq_used": 0,
        "bond_kind": None, "bond_target_id": None, "bond_value": 0,
    }

    def user(self, gid, uid) -> dict:
        key = self.ukey(gid, uid)
        u = self.data["users"].get(key)
        if u is None:
            u = {"group_id": str(gid), "user_id": str(uid), **self._USER_DEFAULTS}
            self.data["users"][key] = u
        else:
            # 兼容旧存档：补齐后来新增的字段
            for k, v in self._USER_DEFAULTS.items():
                u.setdefault(k, v)
        return u

    def rel_of(self, gid, uid) -> dict | None:
        u = self.data["users"].get(self.ukey(gid, uid))
        if not u or not u.get("rel_id"):
            return None
        return self.data["relations"].get(u["rel_id"])

    def partner_of(self, gid, uid) -> str | None:
        rel = self.rel_of(gid, uid)
        if not rel:
            return None
        return rel["b_id"] if str(rel["a_id"]) == str(uid) else rel["a_id"]

    def remember_name(self, gid, uid, name: str | None) -> None:
        if not name:
            return
        self.data["names"][self.ukey(gid, uid)] = {"name": name, "ts": time.time()}

    def name_of(self, gid, uid) -> str:
        n = self.data["names"].get(self.ukey(gid, uid))
        return n["name"] if n and n.get("name") else str(uid)

    # ---- 待确认请求 ----
    def _sweep_pending(self) -> None:
        now = time.time()
        dead = [k for k, v in self.data["pending"].items()
                if not isinstance(v.get("expire"), (int, float)) or v["expire"] <= now]
        for k in dead:
            del self.data["pending"][k]

    def pending_for_target(self, gid, uid) -> dict | None:
        self._sweep_pending()
        return self.data["pending"].get(self.ukey(gid, uid))

    def pending_involving(self, gid, uid) -> dict | None:
        """该用户名下任何未决请求（发出的或收到的）。同一人同时最多一个。"""
        self._sweep_pending()
        for v in self.data["pending"].values():
            if str(v["group_id"]) == str(gid) and str(uid) in (str(v["from_id"]), str(v["to_id"])):
                return v
        return None

    def _add_pending(self, gid, from_id, to_id, ptype, now_ts, cfg) -> dict:
        minutes = cfg_get(cfg, "request_expire_minutes")
        p = {
            "type": ptype, "from_id": str(from_id), "to_id": str(to_id),
            "group_id": str(gid), "anchor_msg_id": None,
            "expire": now_ts + minutes * 60,
        }
        self.data["pending"][self.ukey(gid, to_id)] = p
        return p

    # ---- 关系 ----
    def _new_rel(self, gid, a_id, b_id, origin, affection, now_ts, cfg) -> dict:
        rid = f"r{self.data['seq']}"
        self.data["seq"] += 1
        rel = {
            "id": rid, "group_id": str(gid), "a_id": str(a_id), "b_id": str(b_id),
            "affection": int(affection), "started_date": _today(),
            "created_ts": now_ts, "origin": origin,
            "safety_until": (now_ts + cfg_get(cfg, "safety_hours") * 3600)
                            if origin == "confess" else 0,
            "settle_date": _today(),
            "steal_date": "", "steal_today": 0, "steal_total": 0,
        }
        self.data["relations"][rid] = rel
        for uid in (str(a_id), str(b_id)):
            self.user(gid, uid)["rel_id"] = rid
        return rel

    def _dissolve(self, rel: dict) -> None:
        for uid in (rel["a_id"], rel["b_id"]):
            u = self.data["users"].get(self.ukey(rel["group_id"], uid))
            if u and u.get("rel_id") == rel["id"]:
                u["rel_id"] = None
        self.data["relations"].pop(rel["id"], None)

    def _clear_xiangqin(self, gid, uid) -> str | None:
        """把 uid 从今日相亲配对里摘掉（对方也一并释放）。返回原相亲对象 id。

        用于「表白脱单」等缘分终止场景：相亲不算对象，但一旦脱单就该散场，
        否则对方会卡在一段假相亲里（还会白占当天的相亲次数）。
        """
        x = self.data["xiangqin"].get(str(gid))
        if not x or str(uid) not in x.get("pairs", {}):
            return None
        other = x["pairs"].pop(str(uid))
        x["pairs"].pop(other, None)
        return other

    def settle(self, rel: dict, cfg) -> int:
        """好感度懒结算：补算每天 +daily_affection，封顶。返回当前好感。"""
        days = (_parse_day(_today()) - _parse_day(rel["settle_date"])).days
        if days > 0:
            cap = cfg_get(cfg, "affection_cap")
            rel["affection"] = min(cap, rel["affection"] + cfg_get(cfg, "daily_affection") * days)
            rel["settle_date"] = _today()
        return rel["affection"]

    def steal_rate(self, rel: dict, cfg) -> float:
        rate = cfg_get(cfg, "base_rate") - cfg_get(cfg, "rate_k") * rel["affection"]
        return max(0.0, rate)

    def _partner_id(self, rel: dict, uid) -> str:
        return rel["b_id"] if str(rel["a_id"]) == str(uid) else rel["a_id"]

    @staticmethod
    def _use_daily(u: dict, field: str, limit: int) -> None:
        """消耗某项每日额度；超限抛 RuleError(限额 key)。"""
        today = _today()
        if u[f"{field}_date"] == today and u[f"{field}_used"] >= limit:
            raise RuleError(f"{field}_limit")
        if u[f"{field}_date"] != today:
            u[f"{field}_date"] = today
            u[f"{field}_used"] = 0
        u[f"{field}_used"] += 1

    def _in_safety(self, rel: dict, now_ts: float) -> bool:
        return rel["origin"] == "confess" and now_ts < rel.get("safety_until", 0)


# ---------------------------------------------------------------- 操作

def set_opt_out(store: Store, gid, uid, flag: bool) -> None:
    u = store.user(gid, uid)
    if flag and u.get("rel_id"):
        raise RuleError("opt_out_married")
    u["opt_out"] = flag
    store.save()


def request_confess(store: Store, gid, from_id, target_id, now_ts: float, cfg) -> dict:
    """发起表白。返回 pending（含过期时间）。"""
    gid, from_id, target_id = str(gid), str(from_id), str(target_id)
    if from_id == target_id:
        raise RuleError("self_target")
    fu, tu = store.user(gid, from_id), store.user(gid, target_id)
    if fu.get("opt_out"):
        raise RuleError("opt_out_self")
    if tu.get("opt_out"):
        raise RuleError("opt_out_target")
    if fu.get("rel_id"):
        raise RuleError("confess_not_single")
    if store.pending_involving(gid, from_id):
        raise RuleError("busy_self")
    if store.pending_involving(gid, target_id):
        raise RuleError("busy_target")
    if fu["confess_date"] == _today():
        raise RuleError("confess_limit")
    fu["confess_date"] = _today()
    p = store._add_pending(gid, from_id, target_id, "confess", now_ts, cfg)
    store.save()
    return p


def confirm_confess(store: Store, gid, uid, accept: bool, now_ts: float, cfg) -> dict:
    """被表白者确认。accept=False 仅作废请求。"""
    gid, uid = str(gid), str(uid)
    p = store.pending_for_target(gid, uid)
    if not p or p["type"] != "confess":
        raise RuleError("no_pending")
    store.data["pending"].pop(store.ukey(gid, uid), None)
    if not accept:
        store.save()
        return {"line": "rejected", "from_id": p["from_id"]}

    fu = store.user(gid, p["from_id"])
    if fu.get("rel_id"):
        store.save()
        raise RuleError("initiator_changed")
    tu = store.user(gid, uid)
    if tu.get("rel_id"):
        # 出轨线：接受者已有对象 -> 原关系破裂清零，原配获得一次性拆散权
        old_rel = store.data["relations"].get(tu["rel_id"])
        betrayed_id = store._partner_id(old_rel, uid)
        store._dissolve(old_rel)
        xu = store.user(gid, betrayed_id)
        xu["bond_kind"], xu["bond_target_id"], xu["bond_value"] = "breakup", uid, 0
        line = "betray"
    else:
        betrayed_id, line = None, "new"
    store._new_rel(gid, p["from_id"], uid, "confess", 0, now_ts, cfg)
    # 缘分终止条件之一：任一方表白脱单，今日相亲自动散场。
    # 相亲本身不挡表白，但脱单后不该再挂着相亲对象。
    store._clear_xiangqin(gid, p["from_id"])
    store._clear_xiangqin(gid, uid)
    store.save()
    return {"line": line, "from_id": p["from_id"], "betrayed_id": betrayed_id}


def request_divorce(store: Store, gid, uid, now_ts: float, cfg) -> dict:
    gid, uid = str(gid), str(uid)
    u = store.user(gid, uid)
    if u.get("opt_out"):
        raise RuleError("opt_out_self")
    partner = store.partner_of(gid, uid)
    if not partner:
        raise RuleError("not_married")
    if store.pending_involving(gid, uid):
        raise RuleError("busy_self")
    if store.pending_involving(gid, partner):
        raise RuleError("busy_target")
    if u["divorce_date"] == _today():
        raise RuleError("divorce_limit")
    u["divorce_date"] = _today()
    p = store._add_pending(gid, uid, partner, "divorce", now_ts, cfg)
    store.save()
    return p


def confirm_divorce(store: Store, gid, uid, accept: bool, now_ts: float, cfg) -> dict:
    gid, uid = str(gid), str(uid)
    p = store.pending_for_target(gid, uid)
    if not p or p["type"] != "divorce":
        raise RuleError("no_pending")
    store.data["pending"].pop(store.ukey(gid, uid), None)
    if not accept:
        store.save()
        return {"line": "rejected", "from_id": p["from_id"]}
    rel = store.rel_of(gid, uid)
    partner = store._partner_id(rel, uid) if rel else p["from_id"]
    if rel:
        store._dissolve(rel)  # 干净分手：好感清零，不产生任何恢复权
    store.save()
    return {"line": "divorced", "from_id": p["from_id"], "partner_id": partner}


def steal(store: Store, gid, actor_id, target_id, now_ts: float, cfg) -> dict:
    """抢对象（含抢回）。成功失败都消耗每日额度。"""
    gid, actor_id, target_id = str(gid), str(actor_id), str(target_id)
    if actor_id == target_id:
        raise RuleError("self_target")
    au, tu = store.user(gid, actor_id), store.user(gid, target_id)
    if au.get("opt_out"):
        raise RuleError("opt_out_self")
    if tu.get("opt_out"):
        raise RuleError("opt_out_target")
    if au.get("rel_id"):
        raise RuleError("steal_not_single")
    rel = store.rel_of(gid, target_id)
    if not rel:
        raise RuleError("target_single")
    if store.pending_involving(gid, actor_id):
        raise RuleError("busy_self")
    if store.pending_involving(gid, target_id):
        raise RuleError("busy_target")
    store.settle(rel, cfg)
    if store._in_safety(rel, now_ts):
        raise RuleError("safety")
    cap = cfg_get(cfg, "target_steal_cap")
    if rel["steal_date"] == _today() and rel["steal_today"] >= cap:
        raise RuleError("target_cap", cap=cap)

    # 校验全部通过，开始消耗额度
    store._use_daily(au, "steal", cfg_get(cfg, "steal_daily_limit"))
    if rel["steal_date"] != _today():
        rel["steal_date"] = _today()
        rel["steal_today"] = 0
    rel["steal_today"] += 1
    rel["steal_total"] += 1

    rate = store.steal_rate(rel, cfg)
    success = random.random() * 100 < rate
    result = {"success": success, "rate": rate, "restored": False, "ex_id": None,
              "bond": 0, "affection": 0}
    if success:
        ex_id = store._partner_id(rel, target_id)
        rel_affection = rel["affection"]
        store._dissolve(rel)
        # 原配获得羁绊（抢回成功时恢复好感）；已有拆散权不被覆盖
        xu = store.user(gid, ex_id)
        if xu.get("bond_kind") != "breakup":
            xu["bond_kind"], xu["bond_target_id"] = "steal", target_id
            xu["bond_value"] = rel_affection // 2
        # 抢回：持有对该目标的羁绊则恢复好感
        start_affection = 0
        if au.get("bond_kind") == "steal" and au.get("bond_target_id") == target_id:
            start_affection = au.get("bond_value", 0)
            au["bond_kind"], au["bond_target_id"], au["bond_value"] = None, None, 0
            result["restored"] = True
            result["bond"] = start_affection
        new_rel = store._new_rel(gid, actor_id, target_id, "steal", start_affection, now_ts, cfg)
        result["ex_id"], result["affection"] = ex_id, new_rel["affection"]
    store.save()
    return result


def break_couple(store: Store, gid, actor_id, target_id, now_ts: float, cfg) -> dict:
    """拆散：被出轨原配的一次性清算权，成败都消耗，不受安全期限制。"""
    gid, actor_id, target_id = str(gid), str(actor_id), str(target_id)
    if actor_id == target_id:
        raise RuleError("self_target")
    au, tu = store.user(gid, actor_id), store.user(gid, target_id)
    if au.get("bond_kind") != "breakup" or au.get("bond_target_id") != target_id:
        raise RuleError("no_breakup_right")
    if au.get("rel_id"):
        raise RuleError("steal_not_single")
    rel = store.rel_of(gid, target_id)
    if not rel:
        raise RuleError("target_single")
    if store.pending_involving(gid, actor_id):
        raise RuleError("busy_self")
    if store.pending_involving(gid, target_id):
        raise RuleError("busy_target")
    store.settle(rel, cfg)
    store._use_daily(au, "steal", cfg_get(cfg, "steal_daily_limit"))

    rate = store.steal_rate(rel, cfg)
    success = random.random() * 100 < rate
    # 一次性：无论成败都清除拆散权
    au["bond_kind"], au["bond_target_id"], au["bond_value"] = None, None, 0
    result = {"success": success, "rate": rate, "ex_id": None}
    if success:
        result["ex_id"] = store._partner_id(rel, target_id)
        store._dissolve(rel)  # 双双回单身，不产生任何新的恢复权
    store.save()
    return result


def gift(store: Store, gid, uid, cfg) -> dict:
    gid, uid = str(gid), str(uid)
    u = store.user(gid, uid)
    if u.get("opt_out"):
        raise RuleError("opt_out_self")
    rel = store.rel_of(gid, uid)
    if not rel:
        raise RuleError("not_married")
    store.settle(rel, cfg)
    store._use_daily(u, "gift", 1)
    rel["affection"] = min(cfg_get(cfg, "affection_cap"),
                           rel["affection"] + cfg_get(cfg, "gift_affection"))
    store.save()
    return {"partner_id": store._partner_id(rel, uid), "affection": rel["affection"]}


def xiangqin(store: Store, gid, uid, cfg) -> dict:
    """相亲：完全独立系统，随机匹配一名今日未相亲的单身，隔天自动散场。"""
    gid, uid = str(gid), str(uid)
    u = store.user(gid, uid)
    if u.get("opt_out"):
        raise RuleError("opt_out_self")
    if u.get("rel_id"):
        raise RuleError("not_single")
    today = _today()
    x = store.data["xiangqin"].setdefault(str(gid), {"date": today, "pairs": {}})
    if x["date"] != today:
        x["date"], x["pairs"] = today, {}
    if uid in x["pairs"]:
        raise RuleError("xq_in_pair")
    # 每日次数独立计数（只看 xq_date，不看 pairs），否则「相亲→跑路」可无限重抽。
    if u.get("xq_date") == today and u.get("xq_used", 0) >= 1:
        raise RuleError("xq_limit")
    paired = set(x["pairs"].keys()) | set(x["pairs"].values())
    pool = []
    for key, info in store.data["names"].items():
        if not key.startswith(f"{gid}:"):
            continue
        other = key.split(":", 1)[1]
        if other in (uid, "") or other in paired:
            continue
        ou = store.data["users"].get(key)
        if ou and (ou.get("rel_id") or ou.get("opt_out")):
            continue
        pool.append(other)
    if not pool:
        raise RuleError("xq_empty")
    other = random.choice(pool)
    # 只扣「主动发起」方的次数：被动被匹配不消耗额度，
    # 被匹配者「跑路」后仍可用自己的那份额度发起相亲。
    store._use_daily(u, "xq", 1)
    x["pairs"][uid] = other
    x["pairs"][other] = uid
    store.save()
    return {"other_id": other}


def xiangqin_partner(store: Store, gid, uid) -> str | None:
    """查今天的相亲对象；没有则 None。"""
    x = store.data["xiangqin"].get(str(gid))
    if not x or x.get("date") != _today():
        return None
    return x.get("pairs", {}).get(str(uid))


def flee_xiangqin(store: Store, gid, uid) -> str | None:
    """跑路：结束本次相亲。成功返回对方 id，否则 None。"""
    other = store._clear_xiangqin(gid, uid)
    if other is None:
        return None
    store.save()
    return other


def card_view(store: Store, gid, uid, now_ts: float, cfg) -> dict | None:
    """我的对象卡片数据。单身返回 None。"""
    gid, uid = str(gid), str(uid)
    rel = store.rel_of(gid, uid)
    if not rel:
        return None
    affection = store.settle(rel, cfg)
    partner_id = store._partner_id(rel, uid)
    days = (_parse_day(_today()) - _parse_day(rel["started_date"])).days + 1
    return {
        "uid": uid, "partner_id": partner_id,
        "affection": affection,
        "rate": round(store.steal_rate(rel, cfg), 1),
        "days": days,
        "steal_total": rel["steal_total"],
        "in_safety": store._in_safety(rel, now_ts),
        "origin": rel["origin"],
    }

# ---------------------------------------------------------------- 群记录（对象/相亲总览）

def _pair_key(x, y) -> tuple:
    """把一对人归一化，保证 (A,B) 与 (B,A) 得到同一个 key，用于去重。"""
    return tuple(sorted((str(x), str(y))))


def group_record(store: Store, gid, now_ts: float, cfg) -> dict:
    """汇总本群的恋爱关系与相亲配对，供「对象记录」卡片渲染。

    - 恋爱：每个 relation 只出现一次（relations 本身就是一条记录存双方，天然不重复）
    - 相亲：pairs 是双向存的（A->B 与 B->A），用 _pair_key 去重后只保留一份
    """
    gid = str(gid)
    couples, seen = [], set()
    for rel in store.data["relations"].values():
        if str(rel["group_id"]) != gid:
            continue
        key = _pair_key(rel["a_id"], rel["b_id"])
        if key in seen:
            continue
        seen.add(key)
        aff = store.settle(rel, cfg)
        a, b = str(rel["a_id"]), str(rel["b_id"])
        couples.append({
            "a_id": a, "b_id": b,
            "a_name": store.name_of(gid, a),
            "b_name": store.name_of(gid, b),
            "affection": aff,
            "origin": rel.get("origin", "confess"),
            "days": (_parse_day(_today()) - _parse_day(rel["started_date"])).days + 1,
            "steal_total": rel.get("steal_total", 0),
            "in_safety": store._in_safety(rel, now_ts),
            "theme": theme_key(aff, cfg_get(cfg, "affection_cap")),
        })
    couples.sort(key=lambda c: c["affection"], reverse=True)

    # 相亲配对去重
    x = store.data["xiangqin"].get(gid)
    teas, seen_x = [], set()
    if x and x.get("date") == _today():
        for uid, other in (x.get("pairs") or {}).items():
            key = _pair_key(uid, other)
            if key in seen_x:
                continue
            seen_x.add(key)
            u, o = str(uid), str(other)
            teas.append({
                "a_id": u, "b_id": o,
                "a_name": store.name_of(gid, u),
                "b_name": store.name_of(gid, o),
            })
        teas.sort(key=lambda t: t["a_name"])

    return {
        "couples": couples,
        "teas": teas,
        "couple_count": len(couples),
        "tea_count": len(teas),
        "single_count": _single_count(store, gid),
    }


def _single_count(store: Store, gid) -> int:
    """本群发言过、且当前单身（无关系、不在相亲）的人数。"""
    gid = str(gid)
    x = store.data["xiangqin"].get(gid)
    paired = set()
    if x and x.get("date") == _today():
        paired = set(x.get("pairs") or {})
    n = 0
    for key in store.data["names"]:
        if not key.startswith(f"{gid}:"):
            continue
        uid = key.split(":", 1)[1]
        u = store.data["users"].get(key)
        if u and u.get("rel_id"):
            continue
        if uid in paired:
            continue
        n += 1
    return n


def theme_key(affection: int, cap: int) -> str:
    """好感度 -> 主题名（5 档）。0-20 绿 / 21-40 蓝 / 41-60 黄 / 61-80 橙 / 81-100 红。"""
    a = max(0, int(affection))
    if a <= 20:
        return "lv1"
    if a <= 40:
        return "lv2"
    if a <= 60:
        return "lv3"
    if a <= 80:
        return "lv4"
    return "lv5"
