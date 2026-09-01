import math
import random
import requests
import os
from pathlib import Path

import flow_paint  # 流场笔触渲染器（同目录下的 flow_paint.py）


# ============================================================
# Configuration
# ============================================================

WIDTH = 1200
HEIGHT = 560
USERNAME = "PsyCube250"
OUTPUT = Path("starry_night.svg")
token = os.environ.get("GITHUB_TOKEN")

BACKGROUND = "#050D1F"

# 梵高《星夜》配色：深靛蓝 + 普鲁士蓝 + 一点青绿 + 暖金
SKY_BLUES = [
    "#0A1F3D",
    "#0F2E52",
    "#154169",
    "#1B5480",
    "#246B9A",
    "#2E82AF",
]

STAR_COLORS = [
    "#FFD84D",
    "#FFE681",
    "#FFF1A8",
    "#F8C94A",
    "#FFB84D",
]

GOLD_HALO = "#FFE58A"

# 布局关键坐标（严格分区，避免重叠）
HORIZON_Y = 380          # 山脊线大致位置
VILLAGE_TOP = 385        # 村庄剪影所在的窄带
VILLAGE_BOTTOM = 415
GRID_TOP = 435           # 贡献格数据区起始，与村庄留出间隔
GRID_ROWS = 7
GRID_CELL_Y = 17

random.seed(250)


# ============================================================
# Animation CSS
# ============================================================

ANIMATION_CSS = """
<style>
@keyframes twinkle {
    0%, 100% { opacity: 0.35; transform: scale(0.85); }
    50%      { opacity: 1;    transform: scale(1.25); }
}
@keyframes breathe {
    0%, 100% { opacity: 0.75; transform: scale(0.96); }
    50%      { opacity: 1;    transform: scale(1.05); }
}
@keyframes swirl-drift {
    0%   { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
}
.twinkle { transform-box: fill-box; transform-origin: center; animation: twinkle 3.5s ease-in-out infinite; }
.breathe { transform-box: fill-box; transform-origin: center; animation: breathe 6s ease-in-out infinite; }
.spiral-slow { transform-box: fill-box; transform-origin: center; animation: swirl-drift 240s linear infinite; }
</style>
"""


# ============================================================
# Fake / real contribution data
# ============================================================

def generate_fake_contributions():
    data = []
    for _ in range(52):
        week_data = []
        for _ in range(7):
            value = random.choices(
                [0, 1, 2, 3, 5, 8, 12],
                weights=[30, 20, 15, 12, 8, 4, 1],
            )[0]
            week_data.append(value)
        data.append(week_data)
    return data


def get_github_contributions(token):
    query = """
    query($username: String!) {
      user(login: $username) {
        contributionsCollection {
          contributionCalendar {
            weeks { contributionDays { date contributionCount weekday } }
          }
        }
      }
    }
    """
    response = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": {"username": USERNAME}},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    response.raise_for_status()
    result = response.json()
    if "errors" in result:
        raise RuntimeError(result["errors"])
    weeks = result["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    return [[d["contributionCount"] for d in w["contributionDays"]] for w in weeks]


# ============================================================
# SVG primitives
# ============================================================

def circle(cx, cy, r, fill, opacity=1.0):
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}" opacity="{opacity}"/>'


def path(d, stroke, width, opacity=1.0, fill="none", cls=""):
    cls_attr = f' class="{cls}"' if cls else ""
    return (
        f'<path{cls_attr} d="{d}" stroke="{stroke}" stroke-width="{width}" '
        f'opacity="{opacity}" fill="{fill}" stroke-linecap="round"/>'
    )


# ============================================================
# Defs: gradients + painterly texture filter
# ============================================================

def draw_defs():
    return """
    <defs>
        <linearGradient id="skyGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"  stop-color="#020714"/>
            <stop offset="35%" stop-color="#0B2647"/>
            <stop offset="70%" stop-color="#0F3A63"/>
            <stop offset="100%" stop-color="#050D1F"/>
        </linearGradient>

        <radialGradient id="blueGlow">
            <stop offset="0%"  stop-color="#3A8FC7" stop-opacity="0.4"/>
            <stop offset="100%" stop-color="#050D1F" stop-opacity="0"/>
        </radialGradient>

        <radialGradient id="starGlow">
            <stop offset="0%"  stop-color="#FFF6C4" stop-opacity="0.95"/>
            <stop offset="35%" stop-color="#FFD84D" stop-opacity="0.4"/>
            <stop offset="100%" stop-color="#FFD84D" stop-opacity="0"/>
        </radialGradient>

        <radialGradient id="moonHalo">
            <stop offset="0%"  stop-color="#FFF3B0" stop-opacity="0.9"/>
            <stop offset="30%" stop-color="#FFE58A" stop-opacity="0.35"/>
            <stop offset="100%" stop-color="#FFE58A" stop-opacity="0"/>
        </radialGradient>

        <radialGradient id="gridPanel">
            <stop offset="0%"  stop-color="#0A1730" stop-opacity="0.55"/>
            <stop offset="100%" stop-color="#0A1730" stop-opacity="0"/>
        </radialGradient>

        <filter id="softGlow" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="3" result="blur"/>
            <feMerge>
                <feMergeNode in="blur"/>
                <feMergeNode in="SourceGraphic"/>
            </feMerge>
        </filter>

        <!-- 油画笔触纹理：细颗粒扰动叠加在画布上，模拟厚涂质感 -->
        <filter id="canvasTexture" x="0" y="0" width="100%" height="100%">
            <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="7" result="noise"/>
            <feColorMatrix in="noise" type="matrix"
                values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.05 0"/>
        </filter>

        <filter id="brushWarp" x="-20%" y="-20%" width="140%" height="140%">
            <feTurbulence type="fractalNoise" baseFrequency="0.012 0.03" numOctaves="2" seed="11" result="warp"/>
            <feDisplacementMap in="SourceGraphic" in2="warp" scale="18" xChannelSelector="R" yChannelSelector="G"/>
        </filter>
    </defs>
    """


# ============================================================
# Sky: turbulent swirls (organic, not parallel ripples)
# ============================================================

def logarithmic_spiral_path(cx, cy, turns, start_r, growth, points=90, rotation=0.0):
    """生成一条对数螺旋路径（梵高漩涡云的数学原型）。"""
    pts = []
    max_theta = turns * 2 * math.pi
    for i in range(points):
        t = i / (points - 1) * max_theta
        r = start_r * math.exp(growth * t)
        x = cx + r * math.cos(t + rotation)
        y = cy + r * math.sin(t + rotation) * 0.72  # 压扁一点，更像画中的椭圆涡流
        pts.append((x, y))
    d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f} " + " ".join(
        f"L {x:.1f} {y:.1f}" for x, y in pts[1:]
    )
    return d


def draw_vortex(cx, cy, turns=2.3, base_r=8, growth=0.34, rotation=0.0, scale=1.0):
    """画一个多圈叠加、由细到粗的漩涡，模拟厚涂笔触的明暗层次。"""
    svg = []
    layers = [
        (0.55, "#123A68", 9 * scale, 0.55),
        (0.72, "#1B5480", 6.5 * scale, 0.6),
        (0.88, "#2E82AF", 4 * scale, 0.55),
        (1.0, "#7AB6E0", 2 * scale, 0.5),
    ]
    for frac, color, width, opacity in layers:
        d = logarithmic_spiral_path(cx, cy, turns * frac, base_r, growth, rotation=rotation)
        svg.append(path(d, color, width, opacity))
    return svg


def draw_flow_strokes():
    """大范围流动的宽笔触，方向和振幅都不规则，替代原来的等距水波纹。"""
    svg = []
    band_defs = [
        (30, 95, 55, 30, 3),
        (100, 165, 70, 42, 4),
        (170, 235, 45, 55, 3),
        (240, 300, 60, 26, 2),
    ]
    for y0, y1, amp1, amp2, n_lines in band_defs:
        for i in range(n_lines):
            y = random.uniform(y0, y1)
            amp = random.uniform(min(amp1, amp2), max(amp1, amp2))
            phase = random.uniform(0, math.pi)
            x_step = 110
            pts_x = list(range(-80, WIDTH + 80, x_step))
            d = f"M {pts_x[0]} {y:.1f} "
            for idx in range(1, len(pts_x)):
                cx1 = pts_x[idx] - x_step * 0.66
                cy1 = y + amp * math.sin(phase + idx * 1.3)
                cx2 = pts_x[idx] - x_step * 0.33
                cy2 = y - amp * math.sin(phase + idx * 0.8)
                d += f"C {cx1:.1f} {cy1:.1f}, {cx2:.1f} {cy2:.1f}, {pts_x[idx]} {y + amp*0.2*math.sin(phase+idx):.1f} "
            color = random.choice(SKY_BLUES)
            width = random.uniform(3, 8)
            opacity = random.uniform(0.35, 0.7)
            svg.append(path(d, color, width, opacity))
    return svg


def draw_gold_impasto():
    """散落的暖金色短笔触，呼应梵高画中夹杂在蓝色漩涡里的黄色高光。"""
    svg = []
    for _ in range(30):
        x = random.randint(80, 1120)
        y = random.randint(40, 330)
        length = random.randint(14, 40)
        angle = random.uniform(0, math.pi)
        dx, dy = math.cos(angle) * length, math.sin(angle) * length * 0.4
        d = (
            f"M {x - dx:.1f} {y - dy:.1f} "
            f"Q {x:.1f} {y - 8:.1f} {x + dx:.1f} {y + dy:.1f}"
        )
        color = random.choice(["#FFD84D", "#FFE681", "#F8C94A"])
        svg.append(path(d, color, random.uniform(1.2, 2.8), random.uniform(0.3, 0.75)))
    return svg


# ============================================================
# Moon: proper crescent + concentric halo rings
# ============================================================

def draw_moon(cx=1005, cy=105, r=46):
    svg = []
    # 同心光环（对应画中月亮/星星周围的多圈光晕）
    for i, (rad, op) in enumerate([(r * 2.6, 0.12), (r * 1.9, 0.18), (r * 1.35, 0.3)]):
        svg.append(f'<circle class="breathe" cx="{cx}" cy="{cy}" r="{rad:.1f}" fill="url(#moonHalo)" opacity="{op}"/>')

    # 新月：用两个圆的路径运算（大圆减小圆的偏移圆）而不是背景色硬覆盖，
    # 边缘再叠一层同色模糊，避免生硬的"日食缺口"观感
    offset = r * 0.55
    crescent_id = "crescentClip"
    svg.append(f'''
    <clipPath id="{crescent_id}">
        <circle cx="{cx}" cy="{cy}" r="{r}"/>
    </clipPath>
    ''')
    svg.append(f'<g clip-path="url(#{crescent_id})" filter="url(#softGlow)">')
    svg.append(circle(cx, cy, r, "#FFE9A8", 1))
    svg.append(circle(cx + offset, cy - offset * 0.35, r * 1.05, BACKGROUND, 1))
    svg.append("</g>")
    return svg


# ============================================================
# Cypress tree: bold flame-shaped silhouette
# ============================================================

def draw_cypress(base_x=110, base_y=None, height=430, width=100):
    """把柏树画成几段叠起来、边缘外凸内收的"火焰状"轮廓（梵高柏树的标志特征），
    而不是简单的两条边收拢成一个尖锥。"""
    if base_y is None:
        base_y = HEIGHT
    svg = ["<g>"]
    top_y = base_y - height

    # 从下到上分 6 段，每段左右各自向外"鼓包"再收回，形成扭动的火焰轮廓
    segs = 6
    # 每段的左右鼓包幅度（越往上摆动越明显，顶部收尖）
    left_bulge = [0.34, 0.42, 0.30, 0.44, 0.26, 0.05]
    right_bulge = [0.30, 0.20, 0.40, 0.24, 0.36, 0.05]

    left_pts = [(base_x - width * 0.5, base_y)]
    right_pts = [(base_x + width * 0.5, base_y)]
    for i in range(1, segs + 1):
        t = i / segs
        y = base_y - t * height
        taper = (1 - t) ** 0.85  # 顶部快速收窄，呈尖顶
        lb = left_bulge[i - 1]
        rb = right_bulge[i - 1]
        left_pts.append((base_x - width * 0.5 * taper * (0.55 + lb), y))
        right_pts.append((base_x + width * 0.5 * taper * (0.55 + rb), y))
    left_pts.append((base_x, top_y))
    right_pts.append((base_x, top_y))

    def smooth_side(pts):
        d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f} "
        for i in range(1, len(pts)):
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            cx1, cy1 = x0, y0 - (y0 - y1) * 0.5
            cx2, cy2 = x1, y0 - (y0 - y1) * 0.5
            d += f"C {cx1:.1f} {cy1:.1f}, {cx2:.1f} {cy2:.1f}, {x1:.1f} {y1:.1f} "
        return d

    d = smooth_side(left_pts)
    # 拼接右侧（倒序）形成闭合轮廓
    rev_right = list(reversed(right_pts))
    d += f"L {rev_right[0][0]:.1f} {rev_right[0][1]:.1f} "
    for i in range(1, len(rev_right)):
        x0, y0 = rev_right[i - 1]
        x1, y1 = rev_right[i]
        cx1, cy1 = x0, y0 - (y0 - y1) * 0.5
        cx2, cy2 = x1, y0 - (y0 - y1) * 0.5
        d += f"C {cx1:.1f} {cy1:.1f}, {cx2:.1f} {cy2:.1f}, {x1:.1f} {y1:.1f} "
    d += "Z"

    svg.append(f'<path d="{d}" fill="#020712"/>')

    # 树身上叠加几道深蓝/靛色高光弧线，呼应画中柏树上扭动的厚涂笔触
    for i in range(6):
        t0 = 0.1 + i * 0.14
        y0 = base_y - t0 * height
        y1 = y0 - height * 0.13
        sway = (1 if i % 2 == 0 else -1) * width * 0.16
        x0 = base_x - width * 0.12 + sway
        x1 = base_x + width * 0.16 + sway
        svg.append(path(
            f"M {x0:.1f} {y0:.1f} Q {base_x + sway*0.5:.1f} {(y0+y1)/2:.1f} {x1:.1f} {y1:.1f}",
            "#123A68", 2.6, 0.55,
        ))
    svg.append("</g>")
    return svg, top_y


# ============================================================
# Mountains + village skyline (kept strictly above the data grid)
# ============================================================

def draw_mountains():
    svg = []
    svg.append(f"""
    <path d="M0 {HORIZON_Y-15}
        C120 {HORIZON_Y-45} 200 {HORIZON_Y-25} 300 {HORIZON_Y-55}
        C420 {HORIZON_Y-85} 520 {HORIZON_Y-35} 640 {HORIZON_Y-60}
        C760 {HORIZON_Y-90} 880 {HORIZON_Y-30} 1000 {HORIZON_Y-55}
        C1080 {HORIZON_Y-70} 1150 {HORIZON_Y-40} 1200 {HORIZON_Y-50}
        L1200 {HEIGHT} L0 {HEIGHT} Z" fill="#071A30"/>
    """)
    svg.append(f"""
    <path d="M0 {HORIZON_Y+10}
        C160 {HORIZON_Y-20} 260 {HORIZON_Y} 380 {HORIZON_Y-25}
        C500 {HORIZON_Y-45} 600 {HORIZON_Y-5} 720 {HORIZON_Y-20}
        C860 {HORIZON_Y-35} 960 {HORIZON_Y+5} 1080 {HORIZON_Y-10}
        C1130 {HORIZON_Y-18} 1170 {HORIZON_Y} 1200 {HORIZON_Y-5}
        L1200 {HEIGHT} L0 {HEIGHT} Z" fill="#09213C"/>
    """)
    return svg


def draw_village_skyline():
    """连续剪影 + 教堂尖顶，严格限制在 VILLAGE_TOP~VILLAGE_BOTTOM 窄带内，
    与下方的贡献数据网格完全分开，不再随机散落造成重叠。"""
    svg = []
    roof_pts = []
    x = 260
    rng = random.Random(99)
    while x < 980:
        w = rng.randint(26, 46)
        h = rng.randint(10, 24)
        roof_pts.append((x, w, h))
        x += w + rng.randint(2, 8)

    base_y = VILLAGE_BOTTOM
    d = f"M 240 {base_y} "
    for x0, w, h in roof_pts:
        d += f"L {x0} {base_y - h} L {x0 + w/2:.1f} {base_y - h - 8} L {x0 + w} {base_y - h} "
    d += f"L 1000 {base_y} Z"
    svg.append(f'<path d="{d}" fill="#050F1E"/>')

    # 教堂尖顶（画面焦点，放在村庄中段）
    church_x = 560
    svg.append(f'''
    <path d="M {church_x-10} {base_y}
        L {church_x-10} {base_y-34}
        L {church_x} {base_y-58}
        L {church_x+10} {base_y-34}
        L {church_x+10} {base_y}
        Z" fill="#040B17"/>
    ''')

    # 少量点亮的窗户，位置固定不越界
    lit = rng.sample(roof_pts, k=min(9, len(roof_pts)))
    for x0, w, h in lit:
        svg.append(
            f'<rect x="{x0 + w/2 - 2:.1f}" y="{base_y - h/2 - 2:.1f}" width="4" height="4" '
            f'fill="#FFD75A" opacity="{rng.uniform(0.5, 0.95):.2f}"/>'
        )
    return svg


# ============================================================
# Contribution grid — its own clearly-separated "data band"
# ============================================================

def draw_star_cell(cx, cy, level):
    result = []
    if level <= 0:
        result.append(circle(cx, cy, 1.4, "#1B3A5C", 0.6))
        return result

    if level <= 2:
        size, glow = 1.8, 0.5
    elif level <= 5:
        size, glow = 2.5, 0.7
    elif level <= 8:
        size, glow = 3.2, 0.9
    else:
        size, glow = 4.2, 1.0

    color = random.choice(STAR_COLORS)
    delay = random.uniform(0, 4)

    result.append(f'<g class="twinkle" style="animation-delay:{delay:.2f}s">')
    result.append(f'<circle cx="{cx}" cy="{cy}" r="{size*3.2:.1f}" fill="url(#starGlow)" opacity="0.35"/>')
    result.append(circle(cx, cy, size, color, glow))
    if level >= 5:
        result.append(path(f"M {cx-size*2.2:.1f} {cy} L {cx+size*2.2:.1f} {cy}", color, 1.1, 0.7))
        result.append(path(f"M {cx} {cy-size*2.2:.1f} L {cx} {cy+size*2.2:.1f}", color, 1.1, 0.7))
    result.append("</g>")
    return result


def draw_contribution_grid(contributions):
    svg = []
    n_weeks = len(contributions)
    cell_x = min(20, (WIDTH - 120) / max(n_weeks, 1))
    start_x = 60
    grid_w = cell_x * n_weeks
    grid_h = GRID_CELL_Y * GRID_ROWS

    # 柔和的面板底色，让数据区在场景里读得出来是"一块"，不再和村庄混在一起
    svg.append(
        f'<rect x="{start_x-14}" y="{GRID_TOP-14}" width="{grid_w+28:.1f}" height="{grid_h+28}" '
        f'rx="10" fill="url(#gridPanel)"/>'
    )

    for week in range(n_weeks):
        for day in range(len(contributions[week])):
            value = contributions[week][day]
            x = start_x + week * cell_x
            y = GRID_TOP + day * GRID_CELL_Y
            svg.extend(draw_star_cell(x, y, value))
    return svg


# ============================================================
# Assemble
# ============================================================

def generate_svg(contributions):
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">'
    ]
    svg.append(ANIMATION_CSS)
    svg.append(draw_defs())

    # 天空底图：流场笔触渲染的漩涡（见 flow_paint.py），
    # 比纯矢量贝塞尔曲线笔触密度高两个数量级，是真正"画出来"而不是"画几条线"
    sky_img = flow_paint.render_painterly_sky(base_gradient=True)
    sky_b64, sky_mime = flow_paint.to_base64(sky_img, fmt="JPEG", quality=87)
    svg.append(
        f'<image x="0" y="0" width="{WIDTH}" height="{HEIGHT}" '
        f'xlink:href="data:{sky_mime};base64,{sky_b64}" '
        f'href="data:{sky_mime};base64,{sky_b64}" preserveAspectRatio="none"/>'
    )

    # 月亮
    svg.extend(draw_moon())

    # 装饰性小星星（只铺在天空区域，不进入村庄/数据带）
    for _ in range(120):
        x = random.randint(20, WIDTH - 20)
        y = random.randint(20, HORIZON_Y - 70)
        size = random.choice([1, 1, 1, 1.5, 2])
        color = random.choice(STAR_COLORS)
        delay = random.uniform(0, 6)
        svg.append(
            f'<circle class="twinkle" cx="{x}" cy="{y}" r="{size}" fill="{color}" '
            f'opacity="0.5" style="animation-delay:{delay:.2f}s"/>'
        )

    # 山 -> 村庄剪影（窄带，与数据区分离）-> 数据网格
    svg.extend(draw_mountains())
    svg.extend(draw_village_skyline())
    svg.extend(draw_contribution_grid(contributions))

    # 柏树放在最前景，压住村庄左侧边界，顶部伸进天空
    cypress_svg, _ = draw_cypress(base_x=105, base_y=HEIGHT, height=440, width=92)
    svg.extend(cypress_svg)

    # 画布纹理叠加（整体最上层，微弱颗粒感）
    svg.append(f'<rect width="{WIDTH}" height="{HEIGHT}" filter="url(#canvasTexture)" opacity="0.5"/>')

    # 标题
    svg.append(
        '<text x="46" y="46" fill="#FFE681" font-size="24" font-family="Georgia, serif" '
        'font-weight="bold" letter-spacing="1">PsyCube250 · Starry Night</text>'
    )

    svg.append("</svg>")
    OUTPUT.write_text("\n".join(svg), encoding="utf-8")
    print(f"Generated: {OUTPUT.resolve()}")


if __name__ == "__main__":
    if token:
        print("Using GitHub API...")
        contributions = get_github_contributions(token)
    else:
        print("No GitHub token found.")
        print("Using fake contribution data...")
        contributions = generate_fake_contributions()
    generate_svg(contributions)