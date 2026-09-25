"""渲染全部卡片形态的视觉预览：python tests/preview_cards.py

生成 tests/card_preview.html（用本地 8766 端口预览），覆盖：
恋爱 lv1/lv3/lv5、相亲、单身（含羁绊）、单身（免打扰）、对象记录、帮助菜单。
仅用于开发期视觉检查，不影响插件运行。
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jinja2 import Template  # noqa: E402

from astrbot_plugin_zudui.card import (build_card_data, build_menu_card_data,  # noqa: E402
                                       build_record_card_data, build_xiangqin_card_data)
from astrbot_plugin_zudui.game import core as game  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G = "g1"
NOW = time.time()


def make_pair(s, a, b, name_a, name_b, affection, safety=False):
    s.remember_name(G, a, name_a)
    s.remember_name(G, b, name_b)
    game.request_confess(s, G, a, b, NOW, None)
    game.confirm_confess(s, G, b, True, NOW, None)
    rel = s.rel_of(G, a)
    rel["affection"] = affection
    if not safety:
        rel["safety_until"] = 0


def main() -> None:
    s = game.Store(tempfile.mkdtemp())
    cfg = None
    cards = []

    make_pair(s, "1", "2", "珂朵莉", "威廉", 8)
    cards.append(("恋爱 lv1", build_card_data(s, G, "1", cfg)))

    make_pair(s, "11", "12", "珂朵莉", "威廉", 30)
    cards.append(("恋爱 lv2", build_card_data(s, G, "11", cfg)))

    make_pair(s, "3", "4", "冬雪", "阿岚", 50)
    cards.append(("恋爱 lv3", build_card_data(s, G, "3", cfg)))

    make_pair(s, "13", "14", "凌音", "夜羽", 70)
    cards.append(("恋爱 lv4", build_card_data(s, G, "13", cfg)))

    make_pair(s, "5", "6", "凌音", "夜羽", 95)
    s.user(G, "7")  # 占位避免seq混淆
    cards.append(("恋爱 lv5", build_card_data(s, G, "5", cfg)))

    s.remember_name(G, "21", "小满")
    s.remember_name(G, "22", "谷雨")
    game.xiangqin(s, G, "21", cfg)
    partner = game.xiangqin_partner(s, G, "21")
    if partner != "22":  # 随机配到别人就强行对调演示
        pairs = s.data["xiangqin"][G]["pairs"]
        pairs["21"], pairs["22"] = "22", "21"
        pairs.pop(partner, None)
        pairs.pop(pairs.get("22", ""), None)
        pairs["22"] = "21"
    cards.append(("相亲卡", build_xiangqin_card_data(s, G, "21", cfg)))

    s.remember_name(G, "31", "归晚", )
    s.user(G, "31")["bond_kind"], s.user(G, "31")["bond_value"] = "steal", 24
    s.user(G, "31")["bond_target_id"] = "6"
    cards.append(("单身卡·持羁绊", build_card_data(s, G, "31", cfg)))

    s.remember_name(G, "32", "未央")
    game.set_opt_out(s, G, "32", True)
    cards.append(("单身卡·免打扰", build_card_data(s, G, "32", cfg)))

    cards.append(("对象记录", build_record_card_data(s, G, "1", cfg)))
    cards.append(("帮助菜单", build_menu_card_data(s, G, "1", cfg)))

    with open(os.path.join(ROOT, "template", "bond_card.html"), encoding="utf-8") as f:
        html = f.read()
    style = html[html.index("<style>"): html.index("</style>") + len("</style>")]

    body_parts = []
    pages = []
    for i, (label, data) in enumerate(cards):
        doc = Template(html).render(**data)
        body = doc[doc.index('<div class="card'): doc.rindex("</body>")]
        pages.append((label, body))

    defs = open(os.path.join(ROOT, "template", "assets_src", "patterns.svg"),
                encoding="utf-8").read()
    out_dir = os.path.join(ROOT, "tests")
    for i, (label, body) in enumerate(pages):
        for with_label, tpl in ((True, "card_preview_{i}.html"),
                                (False, "card_pic_{i}.html")):
            page = f"""<!DOCTYPE html><html><head><meta charset="utf-8">{style}
            <style>.pv-label{{color:#8fe;font-family:monospace;padding:14px 20px 0;font-size:13px}}</style>
            </head><body>
            <svg width="0" height="0" style="position:absolute">{defs}</svg>
            {'<div class="pv-label">' + str(i) + ": " + label + "</div>" if with_label else ""}
            {body}
            </body></html>"""
            out = os.path.join(out_dir, tpl.format(i=i))
            with open(out, "w", encoding="utf-8") as f:
                f.write(page)
    print(f"{len(pages)} 张卡片：card_preview_*.html（带标签）+ card_pic_*.html（出图用）已生成")


if __name__ == "__main__":
    main()
