"""构建 bond_card.html：把 card_src.html 中的装饰素材占位符替换为内嵌 base64。

用法：python tests/build_card.py
- <!--PATTERNS-->      <- template/assets_src/patterns.svg（生成的扭索纹/花丝 defs）
- {{ASSET:name}}       <- template/assets_src/*.svg 的 base64 data URI

内嵌而非外链的原因：AstrBot html_render 以字符串加载模板，本地相对路径
无法解析；base64 data URI 离线可用，且模板仅在插件启动时读取一次。
"""
import base64
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "template", "card_src.html")
OUT = os.path.join(ROOT, "template", "bond_card.html")
ASSET_DIR = os.path.join(ROOT, "template", "assets_src")

ASSETS = {
    "blossom": "noto_cherry-blossom.svg",
    "petalheart": "noto_heart-suit.svg",
    "sparkle": "noto_sparkles.svg",
    "star": "noto_star.svg",
    "crown": "noto_crown.svg",
    "twohearts": "noto_two-hearts.svg",
    "teacup": "noto_teacup-without-handle.svg",
    "moon": "noto_crescent-moon.svg",
}


def data_uri(path: str) -> str:
    with open(path, "rb") as f:
        return "data:image/svg+xml;base64," + base64.b64encode(f.read()).decode()


def main() -> None:
    with open(SRC, encoding="utf-8") as f:
        html = f.read()
    patterns_path = os.path.join(ASSET_DIR, "patterns.svg")
    with open(patterns_path, encoding="utf-8") as f:
        html = html.replace("<!--PATTERNS-->", f.read())
    for name, fname in ASSETS.items():
        html = html.replace(f"ASSET:{name}", data_uri(os.path.join(ASSET_DIR, fname)))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"bond_card.html -> {OUT} ({len(html)} bytes)")
    assert "{{" not in html.replace("{{ ", "") or "ASSET:" not in html, "有未替换的占位符"
    assert "ASSET:" not in html, "有未替换的 ASSET 占位符"


if __name__ == "__main__":
    main()
