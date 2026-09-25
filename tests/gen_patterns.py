"""生成「星夜鎏金」卡片的复杂装饰图案（纯数学路径，无版权问题）。

输出: template/assets_src/patterns.svg  —— 内含 <g id="gz-*"> 的 defs 片段，
由 tests/build_card.py 注入模板。所有描边使用 currentColor，方便按主题着色。

图案清单：
  gz-rosette  扭索纹玫瑰盘（多层 rose curve + 蕾丝圈 + 钉点环，证书雕纹风）
  gz-corner   花丝角饰（C 形涡卷 + 叶片 + 珠点）
  gz-divider  蕾丝分隔线（中置菱形 + 双侧对称涡卷）
  gz-lozenge  小菱花（分组标题装饰钉）
"""
import math
import os

GOLD_STROKE = 1.5
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "template", "assets_src", "patterns.svg")


def pts_path(pts, close=False):
    d = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts)
    return d + (" Z" if close else "")


def polar(cx, cy, r, theta):
    return cx + r * math.cos(theta), cy + r * math.sin(theta)


def rose_curve(cx, cy, base, amp, k, phase=0.0, steps=720):
    """rose curve: r(θ) = base + amp·cos(kθ)"""
    pts = []
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        r = base + amp * math.cos(k * t + phase)
        pts.append(polar(cx, cy, r, t))
    return pts_path(pts, close=True)


def ring_dots(cx, cy, radius, n, r, phase=0.0):
    out = []
    for i in range(n):
        t = 2 * math.pi * i / n + phase
        x, y = polar(cx, cy, radius, t)
        out.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r}"/>')
    return "".join(out)


def star_polygon(cx, cy, r_out, r_in, n, skip):
    """交替半径连线，形成蕾丝星网"""
    pts = []
    for i in range(n * 2):
        t = math.pi * i / n
        r = r_out if i % 2 == 0 else r_in
        pts.append(polar(cx, cy, r, t - math.pi / 2))
    return pts_path(pts, close=True)


def build_rosette():
    parts = ['<g id="gz-rosette" fill="none" stroke="currentColor" '
             f'stroke-width="{GOLD_STROKE}" stroke-linecap="round">']
    cx = cy = 200.0
    # 三层扭索纹（rose curve）
    parts.append(f'<path d="{rose_curve(cx, cy, 148, 26, 8, 0)}" opacity=".9"/>')
    parts.append(f'<path d="{rose_curve(cx, cy, 120, 30, 8, math.pi / 8)}" opacity=".75"/>')
    parts.append(f'<path d="{rose_curve(cx, cy, 90, 16, 12, 0)}" opacity=".6"/>')
    # 双细外环
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="182" opacity=".8"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="187" opacity=".5"/>')
    # 蕾丝钉点环（交错双半径）
    parts.append(ring_dots(cx, cy, 168, 48, 2.0, 0))
    parts.append(ring_dots(cx, cy, 176, 24, 1.4, math.pi / 24))
    # 内层蕾丝星网
    parts.append(f'<path d="{star_polygon(cx, cy, 66, 38, 16, 6)}" opacity=".55"/>')
    # 中心小盘 + 十字星芒
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="10" opacity=".9"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="3.5" fill="currentColor" stroke="none"/>')
    for ang in range(4):
        t = math.pi / 4 + ang * math.pi / 2
        x2, y2 = polar(cx, cy, 26, t)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x2:.2f}" y2="{y2:.2f}" opacity=".7"/>')
    parts.append("</g>")
    return "".join(parts)


def leaf(x, y, angle, length, width):
    """叶形：两段对称二次曲线构成的梭形"""
    a = math.radians(angle)
    dx, dy = math.cos(a), math.sin(a)
    px, py = -dy, dx
    tx, ty = x + dx * length, y + dy * length
    c1x, c1y = x + dx * length * .45 + px * width, y + dy * length * .45 + py * width
    c2x, c2y = x + dx * length * .45 - px * width, y + dy * length * .45 - py * width
    return (f'<path d="M{x:.2f},{y:.2f} Q{c1x:.2f},{c1y:.2f} {tx:.2f},{ty:.2f} '
            f'Q{c2x:.2f},{c2y:.2f} {x:.2f},{y:.2f} Z" fill="currentColor" '
            'stroke="none" opacity=".55"/>')


def spiral(cx, cy, r0, r1, a0, a1, clockwise=1, steps=60):
    pts = []
    for i in range(steps + 1):
        t = a0 + (a1 - a0) * i / steps
        r = r0 + (r1 - r0) * i / steps
        s = clockwise
        pts.append((cx + s * r * math.cos(t), cy + r * math.sin(t)))
    return pts


def build_corner():
    """左上角花丝：双线框架角 + 内 C 涡卷 + 叶簇 + 珠点（其余三角用 CSS 旋转镜像）。"""
    parts = ['<g id="gz-corner" fill="none" stroke="currentColor" '
             f'stroke-width="{GOLD_STROKE}" stroke-linecap="round">']
    # 外沿双线框架角（贴卡片圆角的走向）
    parts.append('<path d="M148,10 C96,6 44,10 26,26 C10,42 6,96 10,148" opacity=".95"/>')
    parts.append('<path d="M148,26 C104,22 62,26 46,46 C26,64 22,104 26,148" opacity=".5"/>')
    # 内 C 涡卷（一对镜像回勾）
    parts.append('<path d="M118,34 C96,30 74,34 60,50 C48,63 44,82 46,102 '
                 'C50,86 58,74 72,66 C58,66 48,72 40,84" opacity=".85"/>')
    parts.append('<path d="M34,118 C38,96 34,74 50,60 C63,48 82,44 102,46 '
                 'C86,50 74,58 66,72 C66,58 72,48 84,40" opacity=".85"/>')
    # 对角线叶簇（从内向外渐小）
    for (x, y, ang, ln, wd) in [
            (52, 52, 45, 30, 6.5), (74, 74, 45, 24, 5.5), (94, 94, 45, 19, 4.6),
            (111, 111, 45, 15, 3.8), (126, 126, 45, 11, 3.0)]:
        parts.append(leaf(x, y, ang, ln, wd))
        parts.append(leaf(x, y, ang + 180, ln * .8, wd * .8))
    # 珠点：沿对角与弧端
    for (x, y, r) in [(38, 38, 2.6), (30, 30, 1.8), (140, 12, 2.0),
                      (12, 140, 2.0), (58, 24, 1.3), (24, 58, 1.3)]:
        parts.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="currentColor" stroke="none"/>')
    parts.append("</g>")
    return "".join(parts)


def build_divider():
    """分隔线：中置菱花 + 双层对称涡卷延伸。"""
    parts = ['<g id="gz-divider" fill="none" stroke="currentColor" '
             f'stroke-width="{GOLD_STROKE}" stroke-linecap="round">'
             '<g transform="translate(280,12)">']
    # 中心菱形（实心小 + 空心大）
    parts.append('<path d="M0,-9 L6,0 L0,9 L-6,0 Z" fill="currentColor" stroke="none"/>')
    parts.append('<path d="M0,-14 L10,0 L0,14 L-10,0 Z" opacity=".8"/>')
    # 两侧对称涡卷（负 x 侧生成，正 x 侧镜像复用）
    one_side = []
    for i in range(4):
        x0 = 18 + i * 52
        r = 9 - i * 1.2
        pts = spiral(x0, 0, r * 2.4, r * .6, -math.pi / 2, math.pi / 2.2,
                     clockwise=-1 if i % 2 else 1)
        one_side.append(f'<path d="{pts_path(pts)}" opacity="{.9 - i * .15:.2f}"/>')
        one_side.append(f'<circle cx="{x0 + 44:.0f}" cy="0" r="{1.8 - i * .3:.1f}" '
                        'fill="currentColor" stroke="none"/>')
    parts += one_side
    parts.append('<g transform="scale(-1,1)">' + "".join(one_side) + "</g>")
    parts.append("</g></g>")
    return "".join(parts)


def build_lozenge():
    parts = ['<g id="gz-lozenge" fill="none" stroke="currentColor" stroke-width="1.4">'
             '<path d="M8,0 L16,8 L8,16 L0,8 Z" opacity=".9"/>'
             '<path d="M8,3.5 L12.5,8 L8,12.5 L3.5,8 Z" fill="currentColor" stroke="none" opacity=".8"/>'
             '<path d="M0,8 L-8,8 M16,8 L24,8" opacity=".6"/>'
             "</g>"]
    return "".join(parts)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    svg = ("<defs xmlns=\"http://www.w3.org/2000/svg\">"
           + build_rosette() + build_corner() + build_divider() + build_lozenge()
           + "</defs>")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"patterns.svg -> {OUT} ({len(svg)} bytes)")


if __name__ == "__main__":
    main()
