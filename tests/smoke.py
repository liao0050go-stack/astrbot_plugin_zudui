"""组对象核心逻辑冒烟测试。

用法：python tests/smoke.py
不依赖 AstrBot，只测 game/core.py 的规则闭环。
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game import core as game  # noqa: E402

G = "10001"
A, B, C, D, E = "111", "222", "333", "444", "555"
NOW = time.time()  # 必须用真实时间，pending 过期判定基于 time.time()

# 固定随机：win 模式恒成功，lose 模式恒失败（成功率<99 时）
MODE = {"win": True}
game.random.random = lambda: 0.0 if MODE["win"] else 0.99
win = lambda on: MODE.update(win=on)

passed = 0


def check(name, cond):
    global passed
    assert cond, f"FAIL: {name}"
    passed += 1


def expect_err(name, key, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except game.RuleError as e:
        check(f"{name} -> {e.key}", e.key == key)
        return e
    raise AssertionError(f"FAIL: {name} 未抛出 RuleError")


def reset_quota(store, uid, field="steal"):
    """测试辅助：模拟『次日』，清空某项每日额度。"""
    u = store.user(G, uid)
    u[f"{field}_date"], u[f"{field}_used"] = "", 0


# ================= 1. 表白正常线 =================
s = game.Store(tempfile.mkdtemp())
win(True)
s.remember_name(G, A, "阿A")
s.remember_name(G, B, "阿B")
p = game.request_confess(s, G, A, B, NOW, None)
check("表白创建 pending", p["to_id"] == B and p["anchor_msg_id"] is None)
expect_err("A 待决期间再表白", "busy_self",
           game.request_confess, s, G, A, C, NOW, None)
expect_err("C 再向 B 表白被锁(目标待处理)", "busy_target",
           game.request_confess, s, G, C, B, NOW, None)
res = game.confirm_confess(s, G, B, True, NOW, None)
check("正常线 new", res["line"] == "new")
expect_err("已婚者发起表白(重婚封堵)", "confess_not_single",
           game.request_confess, s, G, A, C, NOW, None)
game.request_confess(s, G, C, E, NOW, None)
game.confirm_confess(s, G, E, False, NOW, None)
expect_err("拒绝后重复表白(一天一次)", "confess_limit",
           game.request_confess, s, G, C, E, NOW, None)
rel = s.rel_of(G, A)
check("好感 0 起步", rel["affection"] == 0)
check("confess 来源有安全期", rel["origin"] == "confess" and rel["safety_until"] > NOW)
check("双方绑定", s.partner_of(G, A) == B and s.partner_of(G, B) == A)
expect_err("无 pending 时空确认", "no_pending", game.confirm_confess, s, G, C, True, NOW, None)

# ================= 2. 送礼 + 懒结算 =================
game.gift(s, G, A, None)
game.gift(s, G, B, None)
expect_err("送礼一天一次", "gift_limit", game.gift, s, G, A, None)
check("好感 +2+2", rel["affection"] == 4)
rel["settle_date"] = "2000-01-01"
s.settle(rel, None)
check("懒结算封顶 100", rel["affection"] == 100)
rel["settle_date"] = game._today()

# ================= 3. 安全期 + 金婚防御 =================
expect_err("安全期内不可抢", "safety", game.steal, s, G, C, B, NOW, None)
rel["safety_until"] = 0  # 模拟安全期已过
win(False)
res = game.steal(s, G, C, B, NOW, None)
check("好感100 抢夺失败(成功率10%)", res["success"] is False
      and abs(res["rate"] - 10.0) < 0.01)
check("目标被抢计数 1", s.rel_of(G, B)["steal_total"] == 1)

# ================= 4. 抢人成功 + 羁绊 =================
win(True)
s.rel_of(G, B)["affection"] = 20
reset_quota(s, C)
res = game.steal(s, G, C, B, NOW, None)
check("C 抢走 B", res["success"] and res["restored"] is False)
check("成功率按好感20=66%", abs(res["rate"] - 66.0) < 0.01)
check("A 获得羁绊(20//2=10)", s.user(G, A)["bond_kind"] == "steal"
      and s.user(G, A)["bond_value"] == 10 and s.user(G, A)["bond_target_id"] == B)
check("A 变单身", s.rel_of(G, A) is None)
new_rel = s.rel_of(G, C)
check("抢来的关系好感0无安全期", new_rel["affection"] == 0
      and new_rel["origin"] == "steal" and new_rel["safety_until"] == 0)

# ================= 5. 抢回：恢复羁绊值 =================
s.user(G, A)["steal_date"], s.user(G, A)["steal_used"] = game._today(), 1
expect_err("额度已用不能抢回", "steal_limit", game.steal, s, G, A, B, NOW, None)
reset_quota(s, A)  # 模拟次日
res = game.steal(s, G, A, B, NOW, None)
check("A 抢回成功", res["success"] and res["restored"] and res["bond"] == 10)
check("抢回后好感=羁绊值", s.rel_of(G, A)["affection"] == 10)
check("羁绊已清除", s.user(G, A)["bond_kind"] is None)
check("抢来的关系无安全期", s.rel_of(G, A)["origin"] == "steal")

# ================= 6. 出轨线 + 拆散 =================
s.remember_name(G, D, "阿D")
game.request_confess(s, G, D, B, NOW, None)  # B 已和 A 在一起 -> 出轨线
res = game.confirm_confess(s, G, B, True, NOW, None)
check("出轨线 betray", res["line"] == "betray" and res["betrayed_id"] == A)
check("A 获得拆散权", s.user(G, A)["bond_kind"] == "breakup"
      and s.user(G, A)["bond_target_id"] == B)
check("B-D 新关系带安全期", s.rel_of(G, B)["origin"] == "confess"
      and s.rel_of(G, B)["safety_until"] > NOW)
win(False)
reset_quota(s, A)
res = game.break_couple(s, G, A, B, NOW, None)  # 拆散不受安全期限制
check("拆散失败也消耗权利", res["success"] is False and s.user(G, A)["bond_kind"] is None)
expect_err("拆散权一次性", "no_breakup_right", game.break_couple, s, G, A, B, NOW, None)
s.user(G, A)["bond_kind"], s.user(G, A)["bond_target_id"] = "breakup", B  # 再授一次
win(True)
reset_quota(s, A)
res = game.break_couple(s, G, A, B, NOW, None)
check("拆散成功双回单身", res["success"] and s.rel_of(G, B) is None
      and s.rel_of(G, D) is None)

# ================= 7. 离婚 =================
reset_quota(s, A, "confess")
game.request_confess(s, G, A, B, NOW, None)
game.confirm_confess(s, G, B, True, NOW, None)
pd = game.request_divorce(s, G, A, NOW, None)
check("离婚 pending 发给对象", pd["to_id"] == B)
expect_err("待决中重复发起离婚", "busy_self", game.request_divorce, s, G, A, NOW, None)
expect_err("对方名下有未决请求", "busy_self", game.request_divorce, s, G, B, NOW, None)
game.confirm_divorce(s, G, B, False, NOW, None)
expect_err("被拒后重复发起(一天一次)", "divorce_limit",
           game.request_divorce, s, G, A, NOW, None)
pd = game.request_divorce(s, G, B, NOW, None)
check("换 B 发起离婚", pd["to_id"] == A)
res = game.confirm_divorce(s, G, A, True, NOW, None)
check("离婚完成双单身", res["line"] == "divorced" and s.rel_of(G, A) is None
      and s.rel_of(G, B) is None)

# ================= 8. 相亲（独立系统） =================
s.remember_name(G, E, "阿E")
x = game.xiangqin(s, G, A, None)
check("相亲配到单身", x["other_id"] in (B, D, E))
check("相亲对象可查", game.xiangqin_partner(s, G, A) == x["other_id"])
check("对方视角也可查", game.xiangqin_partner(s, G, x["other_id"]) == A)
# 配对中重复发起 -> xq_in_pair（额度只统计「主动发起」，被动被匹配不扣）
expect_err("配对中不能重复发起", "xq_in_pair", game.xiangqin, s, G, A, None)
check("对方也被锁定", x["other_id"] in s.data["xiangqin"][G]["pairs"])
check("主动方已扣相亲次数", s.user(G, A)["xq_used"] == 1)
check("被动方未扣相亲次数", s.user(G, x["other_id"])["xq_used"] == 0)
check("跑路返回对方", game.flee_xiangqin(s, G, A) == x["other_id"])
check("没配对跑路返回空", game.flee_xiangqin(s, G, A) is None)
check("跑路后查不到相亲对象", game.xiangqin_partner(s, G, A) is None)
check("跑路释放对方", x["other_id"] not in s.data["xiangqin"][G]["pairs"])
expect_err("主动发起不退还", "xq_limit", game.xiangqin, s, G, A, None)
_passive = x["other_id"]
game.xiangqin(s, G, _passive, None)
check("被动方跑路后可用自己额度", s.user(G, _passive)["xq_used"] == 1)

# ================= 9. 不参与 =================
game.set_opt_out(s, G, E, True)
expect_err("不参与者被表白", "opt_out_target", game.request_confess, s, G, A, E, NOW, None)
expect_err("不参与者自己行动", "opt_out_self", game.xiangqin, s, G, E, None)
reset_quota(s, A, "confess")
game.request_confess(s, G, A, B, NOW, None)
game.confirm_confess(s, G, B, True, NOW, None)
expect_err("恋爱中不能不参与", "opt_out_married", game.set_opt_out, s, G, A, True)
game.set_opt_out(s, G, E, False)
check("恢复参与", s.user(G, E)["opt_out"] is False)

# ================= 10. 同目标被抢上限 =================
s.rel_of(G, B)["safety_until"] = 0  # 模拟安全期已过
win(False)
for i in range(3):
    reset_quota(s, C)
    game.steal(s, G, C, B, NOW, None)  # 全失败
reset_quota(s, C)
expect_err("同目标被抢3次封顶", "target_cap", game.steal, s, G, C, B, NOW, None)

# ================= 11. 卡片视图 =================
s.rel_of(G, B)["steal_date"], s.rel_of(G, B)["steal_today"] = "", 0
reset_quota(s, C)
win(True)
game.steal(s, G, C, B, NOW, None)
v = game.card_view(s, G, C, NOW, None)
check("卡片字段", v and v["partner_id"] == B and v["origin"] == "steal"
      and v["in_safety"] is False and v["days"] >= 1)
check("单身无卡片", game.card_view(s, G, E, NOW, None) is None)
check("卡片好感=0", v["affection"] == 0)

print(f"\n全部通过：{passed} 项断言 ✔")
