"""
流场笔触渲染器 v3 (Flow-Field Stroke Painter, Starry-Night 强化版)
------------------------------------------------------------------
本次在 v2 基础上做的调整（针对星星大小 / 动画线条数量与循环观感）：

调整①「星星大小」
  → 英雄星星（3颗大金星）半径从 5.5 减小 50% → 2.75
  → 普通小星星半径范围从 (1.2, 3.0) 减小 20% → (0.96, 2.4)
    （光晕环数不变，只缩核心+整体尺度，避免"发光感"消失）

调整②「动画线条数量与颜色渐变」
  → build_flow_paths() 的 count 从 26 提升到 24 条"基准轨迹"，
    每条基准轨迹再派生出 5 条同轨迹变体（角度/半径/相位做极小
    扰动，肉眼看是"同一条螺旋"的多个描边），总数 = 24 * (1+5)
    = 144 条路径。
  → 同一组变体的颜色不再是随机跳变，而是在基准色附近沿色带做
    连续渐变取样（比如从冷色到暖色/从暗到亮的小范围渐变），
    让"同一条轨迹的多条线"看起来像是笔触的深浅层次，而不是
    颜色互不相关的堆叠。

调整③「消失太快 / 能看出循环断点」
  → 原来的 dash-offset 循环动画本质上是"整条线一次性画完再
    整条线一次性抹掉重画"，周期短、又所有线几乎同步，容易看出
    "啪"地重置的痕迹。
    这里做了三处调整（对应到 generate.py 里应写入的 CSS，本
    脚本只负责产出更利于"看不出断点"的路径数据 + 建议参数）：
      1) 每条路径的动画周期（duration）大幅拉长且带随机范围，
         而不是所有线用同一个固定周期。
      2) delay 的取值范围从 (0, 18) 扩大到 (0, 42)，让线条的
         "重启时刻"充分错开，任意时刻画面里总有一部分线在
         刚出现、一部分在盛开、一部分在淡出，避免整体感觉到
         同步重置。
      3) 新增 fade_frac 字段：建议该路径的 stroke-dasharray 里
         "笔触段"与"空隙段"不再是 1:1，而是笔触段更长、空隙段
         更短，且首尾各自带一段透明度渐变（配合 SVG 里用
         linearGradient/opacity keyframes 实现頭尾羽化），这样
         视觉上线条是"渐隐渐现"而不是"瞬间出现/消失"。

依赖：仅 numpy + Pillow，纯 CPU 计算，几秒内跑完。
"""

import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import base64
import io

WIDTH, HEIGHT = 1200, 560
SUPERSAMPLE = 2
SW, SH = WIDTH * SUPERSAMPLE, HEIGHT * SUPERSAMPLE

rng = np.random.default_rng(250)

# ============================================================
# [STEP 1] 噪声场：三层分形噪声 + 域扭曲
# ============================================================

class ValueNoise2D:
    def __init__(self, res_x, res_y, seed):
        r = np.random.default_rng(seed)
        self.res_x, self.res_y = res_x, res_y
        self.grid = r.uniform(-1, 1, size=(res_y + 1, res_x + 1))

    @staticmethod
    def _smooth(t):
        return t * t * t * (t * (t * 6 - 15) + 10)

    def sample(self, xn, yn):
        xn = min(max(xn, 0.0), 1.0)
        yn = min(max(yn, 0.0), 1.0)
        gx, gy = xn * self.res_x, yn * self.res_y
        x0, y0 = int(np.floor(gx)), int(np.floor(gy))
        x0 = min(max(x0, 0), self.res_x)
        y0 = min(max(y0, 0), self.res_y)
        x1, y1 = min(x0 + 1, self.res_x), min(y0 + 1, self.res_y)
        tx, ty = gx - x0, gy - y0
        sx, sy = self._smooth(tx), self._smooth(ty)
        n00, n10 = self.grid[y0, x0], self.grid[y0, x1]
        n01, n11 = self.grid[y1, x0], self.grid[y1, x1]
        nx0 = n00 * (1 - sx) + n10 * sx
        nx1 = n01 * (1 - sx) + n11 * sx
        return nx0 * (1 - sy) + nx1 * sy


noise_layers = [
    (ValueNoise2D(4, 2, seed=1), 1.0),
    (ValueNoise2D(9, 5, seed=2), 0.55),
    (ValueNoise2D(19, 11, seed=3), 0.28),
]

warp_noise_x = ValueNoise2D(3, 3, seed=11)
warp_noise_y = ValueNoise2D(3, 3, seed=12)

EPS = 0.004
WARP_STRENGTH = 0.06


def _warped_coords(xn, yn):
    wx = warp_noise_x.sample(xn, yn) * WARP_STRENGTH
    wy = warp_noise_y.sample(xn, yn) * WARP_STRENGTH
    return min(max(xn + wx, 0.0), 1.0), min(max(yn + wy, 0.0), 1.0)


def fractal_noise(xn, yn):
    xn, yn = _warped_coords(xn, yn)
    total = 0.0
    for noise, weight in noise_layers:
        total += noise.sample(xn, yn) * weight
    return total


def curl_at(xn, yn):
    dndy = (fractal_noise(xn, min(yn + EPS, 1)) - fractal_noise(xn, max(yn - EPS, 0))) / (2 * EPS)
    dndx = (fractal_noise(min(xn + EPS, 1), yn) - fractal_noise(max(xn - EPS, 0), yn)) / (2 * EPS)
    cx, cy = dndy, -dndx
    norm = math.hypot(cx, cy) + 1e-6
    return cx / norm, cy / norm


# ============================================================
# [STEP 2] 漩涡源：构图骨架
# ============================================================

VORTICES = [
    (520, 190, 1.5, 230),
    (230, 230, -1.15, 175),
    (900, 140, 0.75, 290),
]


def vortex_field(x, y):
    vx, vy = 0.0, 0.0
    for cx, cy, strength, radius in VORTICES:
        dx, dy = x - cx, y - cy
        r = math.hypot(dx, dy) + 1e-6
        falloff = radius / (radius + r)
        tx, ty = -dy / r, dx / r
        vx += tx * strength * falloff
        vy += ty * strength * falloff
    return vx, vy


def nearest_vortex_falloff(x, y):
    best = 0.0
    for cx, cy, _, radius in VORTICES:
        d = math.hypot(x - cx, y - cy)
        best = max(best, radius / (radius + d))
    return best


def flow_at(x, y):
    xn, yn = x / WIDTH, y / HEIGHT
    cvx, cvy = curl_at(xn, yn)
    vvx, vvy = vortex_field(x, y)
    v_norm = math.hypot(vvx, vvy)
    if v_norm > 1e-6:
        vvx, vvy = vvx / v_norm, vvy / v_norm
    else:
        vvx, vvy = cvx, cvy

    falloff = nearest_vortex_falloff(x, y)
    local_turb = 0.55 - 0.35 * falloff
    fx = vvx * (1 - local_turb) + cvx * local_turb
    fy = vvy * (1 - local_turb) + cvy * local_turb
    norm = math.hypot(fx, fy) + 1e-6
    return fx / norm, fy / norm


# ============================================================
# [STEP 3] 笔触积分：RK2 中点法
# ============================================================

def stroke_polyline(seed_x, seed_y, steps, step_len):
    pts = [(seed_x, seed_y)]
    x, y = seed_x, seed_y
    for _ in range(steps):
        k1x, k1y = flow_at(x, y)
        mx, my = x + k1x * step_len * 0.5, y + k1y * step_len * 0.5
        k2x, k2y = flow_at(mx, my)
        x += k2x * step_len
        y += k2y * step_len
        pts.append((x, y))
    return pts


# ============================================================
# [STEP 4] 配色：连续色带取样
# ============================================================

def _hex(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


SKY_RAMP = [
    (0.00, _hex("#060B1E")),
    (0.22, _hex("#0B2A55")),
    (0.45, _hex("#154B82")),
    (0.65, _hex("#1F74A6")),
    (0.82, _hex("#4FA8C9")),
    (0.94, _hex("#BFE6E0")),
]

GOLD_RAMP = [
    (0.0, _hex("#B8860B")),
    (0.5, _hex("#F5C542")),
    (1.0, _hex("#FFF3B0")),
]


def ramp_color(ramp, t):
    t = min(max(t, 0.0), 1.0)
    for i in range(len(ramp) - 1):
        t0, c0 = ramp[i]
        t1, c1 = ramp[i + 1]
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0 + 1e-9)
            return tuple(int(c0[k] + (c1[k] - c0[k]) * f) for k in range(3))
    return ramp[-1][1]


def stroke_color(depth_t, gold=False, jitter=0.05):
    base = ramp_color(GOLD_RAMP if gold else SKY_RAMP, depth_t)
    j = rng.uniform(-jitter, jitter)
    return tuple(int(min(255, max(0, c * (1 + j)))) for c in base)


# ============================================================
# [STEP 5] 带渐变宽度的笔触绘制
# ============================================================

def draw_tapered_stroke(draw, pts_s, base_width, color, opacity):
    n = len(pts_s)
    if n < 2:
        return
    r, g, b = color
    for i in range(n - 1):
        t = i / max(1, n - 2)
        taper = math.sin(math.pi * t) ** 0.6
        w = max(1, int(base_width * (0.35 + 0.65 * taper)))
        draw.line([pts_s[i], pts_s[i + 1]], fill=(r, g, b, int(opacity * 255)),
                  width=w, joint="curve")


def draw_stroke_layer(draw, n_strokes, y_range, steps_range, step_len_range,
                       width_range, opacity_range, gold=False, bias_near_vortex=0.0):
    for _ in range(n_strokes):
        if bias_near_vortex > 0 and rng.random() < bias_near_vortex:
            cx, cy, _, radius = VORTICES[rng.integers(0, len(VORTICES))]
            ang = rng.uniform(0, 2 * math.pi)
            r = rng.uniform(0, radius * 1.3)
            sx = cx + math.cos(ang) * r
            sy = cy + math.sin(ang) * r
        else:
            sx = rng.uniform(0, WIDTH)
            sy = rng.uniform(y_range[0], y_range[1])

        steps = rng.integers(*steps_range)
        step_len = rng.uniform(*step_len_range)
        pts = stroke_polyline(sx, sy, steps, step_len)
        pts_s = [(px * SUPERSAMPLE, py * SUPERSAMPLE) for px, py in pts]

        width = rng.uniform(*width_range) * SUPERSAMPLE
        depth_t = min(max(sy / 420, 0.0), 1.0)
        color = stroke_color(depth_t, gold=gold)
        opacity = rng.uniform(*opacity_range)
        draw_tapered_stroke(draw, pts_s, width, color, opacity)


# ============================================================
# [STEP 5.5] 动画 SVG 螺旋线：24 条基准轨迹 × 5 条同轨迹变体
# ------------------------------------------------------------
# - 基准轨迹（24条）：中心取自 VORTICES，决定"这团螺旋长在哪、
#   多大、往哪转"，跟背景笔触对齐（沿用 v2 的做法）。
# - 每条基准轨迹再派生 5 条"变体"：同一个中心、同一个旋向，只在
#   起始半径、增长速率、旋转相位、生长圈数上做很小的随机扰动，
#   让它们看起来像"同一条螺旋"被多次描边（呼应油画笔触的重复
#   叠加感），而不是 5 条各自独立、随机长在别处的新线。
# - 颜色：同一组（1 基准 + 5 变体）共 6 条线，不再各自独立随机
#   取色，而是沿色带在一个小范围 [t-Δ, t+Δ] 内做线性渐变分布，
#   由暗到亮/由冷到暖过渡，模拟同一批笔触深浅不一的层次感。
# - 动画节奏（duration / delay / fade_frac）：专门加宽随机范围、
#   拉长周期，配合建议的 CSS 做法，让循环重置的时刻在 144 条线
#   之间充分错开，肉眼很难抓到"所有线同时闪一下重置"的瞬间。
# ============================================================

N_BASE_TRACKS = 24
VARIANTS_PER_TRACK = 5


def build_flow_paths():
    paths = []
    n_vortices = len(VORTICES)

    for i in range(N_BASE_TRACKS):
        base_cx, base_cy, strength, radius = VORTICES[i % n_vortices]

        # --- 基准轨迹的"锚定"参数：决定这一组 6 条线共享的骨架 ---
        anchor_cx = base_cx + rng.uniform(-radius * 0.12, radius * 0.12)
        anchor_cy = base_cy + rng.uniform(-radius * 0.10, radius * 0.10)
        anchor_turns = rng.uniform(1.8, 3.6)
        anchor_start_r = rng.uniform(radius * 0.08, radius * 0.20)
        anchor_growth = rng.uniform(0.09, 0.16)
        anchor_rotation = rng.uniform(0, 2 * math.pi)
        spin = 1.0 if strength >= 0 else -1.0

        # 这一组（1 基准 + 5 变体）在色带上的中心深度 + 渐变范围
        depth_t_center = min(max(anchor_cy / 420, 0.0), 1.0)
        group_span = rng.uniform(0.06, 0.14)  # 组内颜色渐变的跨度

        # 这一组共享的动画节奏基调（组内 6 条线在此基础上再各自扰动）
        group_duration = rng.uniform(26, 46)  # 秒，比 v2 明显拉长
        group_delay_base = rng.uniform(0, 42)

        variants = [None] + list(range(VARIANTS_PER_TRACK))  # None = 基准本身
        for vi, _v in enumerate(variants):
            is_base = (vi == 0)

            if is_base:
                cx, cy = anchor_cx, anchor_cy
                turns = anchor_turns
                start_r = anchor_start_r
                growth = anchor_growth
                rotation = anchor_rotation
            else:
                # 同一轨迹的变体：中心几乎不动（极小抖动），半径/圈数/相位
                # 做小幅扰动，制造"同一条螺旋被反复描了几笔"的效果
                cx = anchor_cx + rng.uniform(-radius * 0.02, radius * 0.02)
                cy = anchor_cy + rng.uniform(-radius * 0.02, radius * 0.02)
                turns = anchor_turns * rng.uniform(0.94, 1.06)
                start_r = anchor_start_r * rng.uniform(0.85, 1.18)
                growth = anchor_growth * rng.uniform(0.9, 1.1)
                rotation = anchor_rotation + rng.uniform(-0.35, 0.35)

            pts = []
            for j in range(160):
                t = j / 159 * turns * 2 * math.pi
                r = start_r * math.exp(growth * t)
                theta = spin * t + rotation
                x = cx + r * math.cos(theta)
                y = cy + r * math.sin(theta) * 0.72
                pts.append((x, y))

            d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f} " + " ".join(
                f"L {x:.1f} {y:.1f}" for x, y in pts[1:]
            )

            # --- 组内颜色渐变：沿色带在 [center-span, center+span] 内线性分布 ---
            frac = 0.5 if VARIANTS_PER_TRACK == 0 else vi / (VARIANTS_PER_TRACK)
            depth_t = min(max(depth_t_center - group_span + 2 * group_span * frac, 0.0), 1.0)
            color = stroke_color(depth_t, gold=False, jitter=0.03)
            color_hex = "#%02x%02x%02x" % color

            width = rng.uniform(1.0, 2.6)
            opacity = rng.uniform(0.30, 0.72)

            # --- 动画节奏：组内共享基调 + 各自小扰动，且整体错开 delay ---
            duration = group_duration * rng.uniform(0.9, 1.15)
            delay = (group_delay_base + vi * rng.uniform(3.0, 7.0)) % 48

            # 建议给 SVG 端使用：笔触段更长、空隙段更短，且首尾各留一段
            # 透明度渐变（配合 stroke-dasharray + opacity keyframes 实现
            # "渐隐渐现"而不是"瞬间出现/消失"），从而看不出循环断点
            fade_frac = rng.uniform(0.18, 0.28)  # 首尾各自的淡入/淡出占比
            dash_on_frac = rng.uniform(0.62, 0.8)  # 笔触段占整条虚线周期的比例

            paths.append({
                "d": d,
                "stroke": color_hex,
                "width": width,
                "opacity": opacity,
                "delay": delay,
                "duration": duration,
                "fade_frac": fade_frac,
                "dash_on_frac": dash_on_frac,
                "track_id": i,
                "is_base": is_base,
            })
    return paths


# ============================================================
# [STEP 6] 细节层：星星辉光 + 漩涡光晕 + 画布颗粒
# ------------------------------------------------------------
# 星星大小调整：
#   - 英雄星星 r_core: 5.5 -> 2.75 （减少 50%）
#   - 普通星星 r_core 范围: (1.2, 3.0) -> (0.96, 2.4) （减少 20%）
# 光晕环数（glow_rings）保持不变，只缩核心半径本身，避免"发光
# 感"随尺寸一起消失。
# ============================================================

def draw_star(draw, x, y, r_core, color, glow_rings=4):
    cx, cy = x * SUPERSAMPLE, y * SUPERSAMPLE
    r_core *= SUPERSAMPLE
    for i in range(glow_rings, 0, -1):
        rr = r_core * (1 + i * 1.4)
        alpha = int(70 * (1 - i / (glow_rings + 1)))
        draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                     fill=(*color, alpha))
    draw.ellipse([cx - r_core, cy - r_core, cx + r_core, cy + r_core],
                 fill=(*color, 255))


def draw_vortex_glow(draw, cx, cy, radius, color):
    cx_s, cy_s = cx * SUPERSAMPLE, cy * SUPERSAMPLE
    steps = 10
    for i in range(steps, 0, -1):
        rr = radius * SUPERSAMPLE * (i / steps) * 0.75
        alpha = int(14 * (1 - i / steps) + 4)
        draw.ellipse([cx_s - rr, cy_s - rr, cx_s + rr, cy_s + rr],
                     fill=(*color, alpha))


def add_canvas_grain(img, strength=9):
    arr = np.array(img).astype(np.int16)
    noise = rng.integers(-strength, strength + 1, size=arr.shape[:2])
    for c in range(3):
        arr[:, :, c] = np.clip(arr[:, :, c] + noise, 0, 255)
    return Image.fromarray(arr.astype(np.uint8), mode=img.mode)


# ============================================================
# [STEP 7] 组装所有图层
# ============================================================

def render_painterly_sky(base_gradient=True):
    grad = Image.new("RGB", (1, SH), color=0)
    px = grad.load()
    for y in range(SH):
        t = y / (SH - 1) * 0.92
        px[0, y] = ramp_color(SKY_RAMP, t)
    grad = grad.resize((SW, SH))
    img = grad.convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    glow_colors = [_hex("#3D7FB5"), _hex("#2E5C93"), _hex("#4FA8C9")]
    for i, (cx, cy, _, radius) in enumerate(VORTICES):
        draw_vortex_glow(draw, cx, cy, radius, glow_colors[i % len(glow_colors)])

    draw_stroke_layer(
        draw, n_strokes=600, y_range=(10, 410),
        steps_range=(26, 46), step_len_range=(6, 10),
        width_range=(5, 9), opacity_range=(0.42, 0.62),
        bias_near_vortex=0.35,
    )

    draw_stroke_layer(
        draw, n_strokes=1800, y_range=(5, 410),
        steps_range=(16, 32), step_len_range=(5, 9),
        width_range=(2.5, 4.5), opacity_range=(0.48, 0.7),
        bias_near_vortex=0.42,
    )

    draw_stroke_layer(
        draw, n_strokes=1900, y_range=(0, 390),
        steps_range=(6, 14), step_len_range=(3, 6),
        width_range=(1, 2), opacity_range=(0.38, 0.62),
        bias_near_vortex=0.48,
    )

    draw_stroke_layer(
        draw, n_strokes=170, y_range=(20, 350),
        steps_range=(7, 16), step_len_range=(4, 8),
        width_range=(1, 2), opacity_range=(0.32, 0.58),
        gold=True, bias_near_vortex=0.55,
    )

    draw_stroke_layer(
        draw, n_strokes=90, y_range=(30, 300),
        steps_range=(5, 10), step_len_range=(3, 5),
        width_range=(0.6, 1.2), opacity_range=(0.5, 0.75),
        gold=True, bias_near_vortex=0.85,
    )

    # --- 星星（画在所有笔触之后，尺寸已按要求缩小） ---
    # 普通星星：r_core 原 (1.2, 3.0) -> 减少20% -> (0.96, 2.4)
    for _ in range(55):
        sx = rng.uniform(0, WIDTH)
        sy = rng.uniform(0, 360)
        r_core = rng.uniform(1.2 * 0.8, 3.0 * 0.8)
        star_color = ramp_color(GOLD_RAMP, rng.uniform(0.4, 1.0))
        draw_star(draw, sx, sy, r_core, star_color, glow_rings=4)

    # 英雄星星：r_core 原 5.5 -> 减少50% -> 2.75
    hero_stars = [
        (170, 95, "#FFF3B0"),
        (760, 55, "#FFE58A"),
        (1060, 215, "#FFF6C4"),
    ]
    for sx, sy, hex_color in hero_stars:
        draw_star(draw, sx, sy, r_core=5.5 * 0.5, color=_hex(hex_color), glow_rings=6)

    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    img = img.convert("RGB")
    img = add_canvas_grain(img, strength=7)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.4))
    return img


def to_base64(img, fmt="JPEG", quality=90):
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
        mime = "image/jpeg"
    else:
        img.save(buf, format="PNG", optimize=True)
        mime = "image/png"
    return base64.b64encode(buf.getvalue()).decode("ascii"), mime


def build_animated_svg_snippet(paths, width=WIDTH, height=HEIGHT):
    """把 build_flow_paths() 的结果转成一段可直接用的 SVG + CSS。

    关键点（针对"看不出循环断点"）：
      - 每条 path 用 stroke-dasharray 做"画出-空隙"循环，但 dash 的
        on/off 比例由 dash_on_frac 决定（笔触段更长），并且用
        stroke-dashoffset 的关键帧配合 opacity 关键帧一起动，使得
        线条在"即将循环重置"的瞬间已经先淡出到 0，重置完成后再淡入，
        这样人眼看不到"瞬间跳变"，只会看到柔和的隐现。
      - duration / delay 都读取自路径自身的字段，天然是错开的，
        避免所有线同步在同一时刻重置。
    """
    style_rules = []
    path_tags = []
    for idx, p in enumerate(paths):
        cls = f"flow-path-{idx}"
        fade = p["fade_frac"]
        # 用百分比关键帧描述：0% 淡入开始 -> fade% 完全显现 ->
        # (100-fade)% 开始淡出 -> 100% 完全消失（此时再无缝接回0%）
        style_rules.append(f"""
.{cls} {{
  stroke: {p['stroke']};
  stroke-width: {p['width']:.2f};
  fill: none;
  stroke-dasharray: {p['dash_on_frac']*1000:.0f} {(1-p['dash_on_frac'])*1000:.0f};
  animation: dash-{idx} {p['duration']:.2f}s linear {p['delay']:.2f}s infinite,
             fade-{idx} {p['duration']:.2f}s ease-in-out {p['delay']:.2f}s infinite;
}}
@keyframes dash-{idx} {{
  from {{ stroke-dashoffset: 0; }}
  to   {{ stroke-dashoffset: -2000; }}
}}
@keyframes fade-{idx} {{
  0% {{ opacity: 0; }}
  {fade*100:.1f}% {{ opacity: {p['opacity']:.2f}; }}
  {(1-fade)*100:.1f}% {{ opacity: {p['opacity']:.2f}; }}
  100% {{ opacity: 0; }}
}}
""")
        path_tags.append(f'<path class="{cls}" d="{p["d"]}" />')

    svg = f"""<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
<style>
{''.join(style_rules)}
</style>
<g>
{''.join(path_tags)}
</g>
</svg>"""
    return svg


if __name__ == "__main__":
    import time
    t0 = time.time()
    img = render_painterly_sky()
    img.save("painterly_sky_v3.jpg", quality=90)
    b64, mime = to_base64(img, fmt="JPEG", quality=90)

    paths = build_flow_paths()
    svg = build_animated_svg_snippet(paths)
    with open("flow_paths_v3.svg", "w", encoding="utf-8") as f:
        f.write(svg)

    print(f"渲染耗时: {time.time()-t0:.2f}s")
    print(f"JPEG大小: {len(b64)/1024:.1f} KB (base64, mime={mime})")
    print(f"动画路径总数: {len(paths)} (基准 {N_BASE_TRACKS} × (1+{VARIANTS_PER_TRACK}))")
