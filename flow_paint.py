"""
流场笔触渲染器 v2 (Flow-Field Stroke Painter, Starry-Night 强化版)
------------------------------------------------------------------
针对上一版反馈的四个问题逐一改进：

  问题①「线条没流动起来」
    → 原因：用的是最简单的一阶欧拉积分（每步只看当前点方向），
      遇到漩涡边缘方向变化快的地方，折线会"抖"而不是"转"。
      这里换成二阶 RK2（中点法）积分：先看当前方向走半步，
      在半步处再采一次方向，用这个"预判"方向走完整步。
      这样每一步都提前感知了接下来的转向趋势，画出来的线才会
      真正顺着漩涡"卷"起来，而不是一段段直线硬拼。

  问题②「没有 step 式的讲解注释」
    → 每个关键阶段前都用 [STEP n] 标出来，并解释"为什么这么做"
      而不只是"这行代码做了什么"，方便你按步骤改参数实验。

  问题③「细节不够」
    → 新增：三层分形噪声(而非两层) + 域扭曲(domain warp)让湍流
      更碎更自然；星星层(带辉光)；漩涡中心的光晕层；画布颗粒
      噪点层，让整体不再是"光滑矢量图"的观感。

  问题④「配色不好看」
    → 换成更接近《星月夜》的普鲁士蓝→青蓝→月光白→暖金 的渐变
      色阶，颜色不再是几个离散色值随机选，而是在连续色带上
      按"深度"取样再叠加小幅随机扰动，过渡更自然。

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
# ------------------------------------------------------------
# 只用两层噪声时，湍流的"卷毛"尺度比较单一，看起来偏"光滑矢量"。
# 这里加了第三层高频噪声负责最细碎的纹理；并且在采样噪声之前，
# 先用另一个低频噪声场把坐标本身"扭一下"（domain warp）——
# 这是让噪声看起来更像真实流体/云雾/星夜的经典技巧，因为它让
# 噪声的"网格感"被打散，转而呈现出连续扭曲的漩涡纹理。
# ============================================================

class ValueNoise2D:
    def __init__(self, res_x, res_y, seed):
        r = np.random.default_rng(seed)
        self.res_x, self.res_y = res_x, res_y
        self.grid = r.uniform(-1, 1, size=(res_y + 1, res_x + 1))

    @staticmethod
    def _smooth(t):
        return t * t * t * (t * (t * 6 - 15) + 10)  # quintic，比三次更平滑，减少网格纹路

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


# 三个倍频：低频定大结构，中频定"卷"的尺度，高频定碎纹理
noise_layers = [
    (ValueNoise2D(4, 2, seed=1), 1.0),
    (ValueNoise2D(9, 5, seed=2), 0.55),
    (ValueNoise2D(19, 11, seed=3), 0.28),
]
# 单独一套低频噪声，专门用来扭曲坐标（不参与颜色/流场取值本身）
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
    # (cx, cy, strength, radius)
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
    """离最近漩涡中心越近，falloff 越接近 1——用来让湍流强度、
    发光强度都在漩涡附近增强，呼应星夜里"漩涡越靠近核心越翻腾"的观感。"""
    best = 0.0
    for cx, cy, _, radius in VORTICES:
        d = math.hypot(x - cx, y - cy)
        best = max(best, radius / (radius + d))
    return best


def flow_at(x, y):
    """湍流强度不再是全局固定的 0.45，而是随"离漩涡核心的远近"动态变化：
    核心附近湍流更强（对应星夜里漩涡中心最"炸"），外围更平缓。"""
    xn, yn = x / WIDTH, y / HEIGHT
    cvx, cvy = curl_at(xn, yn)
    vvx, vvy = vortex_field(x, y)
    v_norm = math.hypot(vvx, vvy)
    if v_norm > 1e-6:
        vvx, vvy = vvx / v_norm, vvy / v_norm
    else:
        vvx, vvy = cvx, cvy

    local_turb = 0.30 + 0.35 * nearest_vortex_falloff(x, y)
    fx = vvx * (1 - local_turb) + cvx * local_turb
    fy = vvy * (1 - local_turb) + cvy * local_turb
    norm = math.hypot(fx, fy) + 1e-6
    return fx / norm, fy / norm


# ============================================================
# [STEP 3] 笔触积分：RK2 中点法（这是"让线条动起来"的核心改动）
# ------------------------------------------------------------
# 原版：x += flow_at(x,y) * step        （只看当前点方向）
# 新版：k1 = flow_at(x,y)
#       mid = (x,y) + k1 * step/2
#       k2 = flow_at(*mid)              （在半步处提前探路）
#       x += k2 * step                  （用探路后的方向走完整步）
# k2 已经"看到"了半步之后方向场怎么变，所以整条折线在拐弯处
# 会自然地弧线过渡，而不是像原版那样每步独立决定、容易出现
# 转折生硬的"棱角感"。
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
# [STEP 4] 配色：连续色带取样，而不是离散色值随机挑
# ------------------------------------------------------------
# 原版从几个写死的十六进制颜色里 rng.integers 随机选一个，
# 色彩层次容易显得"分块"。这里定义一条从深普鲁士蓝到月光白
# 再到暖金的连续渐变色带，笔触颜色按"所在深度 + 小幅随机扰动"
# 在色带上连续取样，过渡自然很多，也更贴近星夜的配色。
# ============================================================

def _hex(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


SKY_RAMP = [
    (0.00, _hex("#060B1E")),   # 画面最深处：接近黑的靛蓝
    (0.22, _hex("#0B2A55")),   # 普鲁士蓝
    (0.45, _hex("#154B82")),   # 深钴蓝
    (0.65, _hex("#1F74A6")),   # 湖蓝
    (0.82, _hex("#4FA8C9")),   # 亮青
    (0.94, _hex("#BFE6E0")),   # 月光白青
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
# [STEP 5] 带渐变宽度的笔触绘制（细节感的关键来源之一）
# ------------------------------------------------------------
# PIL 的 draw.line 只能给整条折线一个统一宽度，画出来的每一笔
# 首尾粗细一样，看着"生硬"。这里把每条折线拆成若干小段，宽度
# 按"两端细、中段粗"的抛物线轮廓变化，模拟真实画笔下笔轻、
# 行笔重、收笔轻的手感。
# ============================================================

def draw_tapered_stroke(draw, pts_s, base_width, color, opacity):
    n = len(pts_s)
    if n < 2:
        return
    r, g, b = color
    for i in range(n - 1):
        t = i / max(1, n - 2)
        taper = math.sin(math.pi * t) ** 0.6  # 两端趋近0，中间趋近1
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


def build_flow_paths(count=26):
    """Generate animated SVG swirl paths for the sky.

    These are lightweight vector paths that explicitly animate with CSS dash
    offset, which makes the sky feel alive even when the raster background is
    static.
    """
    paths = []
    for i in range(count):
        cx = rng.uniform(120, WIDTH - 120)
        cy = rng.uniform(40, 310)
        turns = rng.uniform(1.8, 3.6)
        start_r = rng.uniform(18, 46)
        growth = rng.uniform(0.09, 0.16)
        rotation = rng.uniform(0, 2 * math.pi)
        pts = []
        for j in range(160):
            t = j / 159 * turns * 2 * math.pi
            r = start_r * math.exp(growth * t)
            x = cx + r * math.cos(t + rotation)
            y = cy + r * math.sin(t + rotation) * 0.72
            pts.append((x, y))

        d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f} " + " ".join(
            f"L {x:.1f} {y:.1f}" for x, y in pts[1:]
        )
        depth_t = min(max(cy / 420, 0.0), 1.0)
        color = stroke_color(depth_t, gold=False, jitter=0.06)
        color_hex = "#%02x%02x%02x" % color
        width = rng.uniform(1.2, 2.8)
        opacity = rng.uniform(0.35, 0.78)
        delay = rng.uniform(0, 18)
        paths.append({
            "d": d,
            "stroke": color_hex,
            "width": width,
            "opacity": opacity,
            "delay": delay,
        })
    return paths


# ============================================================
# [STEP 6] 细节层：星星辉光 + 漩涡光晕 + 画布颗粒
# ------------------------------------------------------------
# 这三层是原版完全没有的东西，专门解决"细节不够"的问题：
#   - 星星：小亮点 + 多圈递减透明度的光晕，制造"发光"感
#   - 漩涡光晕：每个漩涡中心一圈柔和径向渐变，暗示光源
#   - 颗粒：极细微的随机噪点，模拟画布/颜料的物理质感，
#     避免整张图看起来"太干净"（矢量感）
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
    """Render the starry-night sky.

    The project historically called this with a `base_gradient` keyword,
    so keep that argument for backward compatibility even though the current
    implementation always renders a gradient-backed sky.
    """
    # 背景渐变直接烘焙成连续色带（用新的 SKY_RAMP，而不是原来的4色线性插值）
    grad = Image.new("RGB", (1, SH), color=0)
    px = grad.load()
    for y in range(SH):
        t = y / (SH - 1) * 0.92  # 底部不完全到最亮，留出一点收束
        px[0, y] = ramp_color(SKY_RAMP, t)
    grad = grad.resize((SW, SH))
    img = grad.convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    # --- 漩涡光晕（先画，垫在所有笔触下面） ---
    glow_colors = [_hex("#3D7FB5"), _hex("#2E5C93"), _hex("#4FA8C9")]
    for i, (cx, cy, _, radius) in enumerate(VORTICES):
        draw_vortex_glow(draw, cx, cy, radius, glow_colors[i % len(glow_colors)])

    # --- 星星（在笔触之前先撒一批，让部分被后续笔触半遮住，更自然） ---
    for _ in range(55):
        sx = rng.uniform(0, WIDTH)
        sy = rng.uniform(0, 360)
        r_core = rng.uniform(1.0, 2.6)
        star_color = ramp_color(GOLD_RAMP, rng.uniform(0.4, 1.0))
        draw_star(draw, sx, sy, r_core, star_color, glow_rings=3)

    # 第1层：宽、深色、长笔触，勾出大漩涡的骨架方向
    draw_stroke_layer(
        draw, n_strokes=600, y_range=(10, 410),
        steps_range=(26, 46), step_len_range=(6, 10),
        width_range=(5, 9), opacity_range=(0.42, 0.62),
        bias_near_vortex=0.35,
    )

    # 第2层：中等宽度，撑起密度和流动感（主力层）
    draw_stroke_layer(
        draw, n_strokes=1800, y_range=(5, 410),
        steps_range=(16, 32), step_len_range=(5, 9),
        width_range=(2.5, 4.5), opacity_range=(0.48, 0.7),
        bias_near_vortex=0.42,
    )

    # 第3层：细高光，短一些，加闪烁/表面纹理感
    draw_stroke_layer(
        draw, n_strokes=1900, y_range=(0, 390),
        steps_range=(6, 14), step_len_range=(3, 6),
        width_range=(1, 2), opacity_range=(0.38, 0.62),
        bias_near_vortex=0.48,
    )

    # 第4层：金色点缀
    draw_stroke_layer(
        draw, n_strokes=170, y_range=(20, 350),
        steps_range=(7, 16), step_len_range=(4, 8),
        width_range=(1, 2), opacity_range=(0.32, 0.58),
        gold=True, bias_near_vortex=0.55,
    )

    # 亮部再补一层最细的白金高光丝线，专门贴着漩涡核心走，增强"发光感"
    draw_stroke_layer(
        draw, n_strokes=90, y_range=(30, 300),
        steps_range=(5, 10), step_len_range=(3, 5),
        width_range=(0.6, 1.2), opacity_range=(0.5, 0.75),
        gold=True, bias_near_vortex=0.85,
    )

    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    img = img.convert("RGB")
    img = add_canvas_grain(img, strength=7)
    # 极轻微的高斯模糊把超采样残留的锯齿和颗粒噪点揉得更像颜料而不是数字噪声
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


if __name__ == "__main__":
    import time
    t0 = time.time()
    img = render_painterly_sky()
    img.save("painterly_sky_v2.jpg", quality=90)
    b64, mime = to_base64(img, fmt="JPEG", quality=90)
    print(f"渲染耗时: {time.time()-t0:.2f}s")
    print(f"JPEG大小: {len(b64)/1024:.1f} KB (base64, mime={mime})")
