import random
import requests
import os
from pathlib import Path


# ============================================================
# Configuration
# ============================================================

WIDTH = 1200
HEIGHT = 460
USERNAME = "PsyCube250"
OUTPUT = Path("starry_night.svg")
token = os.environ.get("GITHUB_TOKEN")
# 深蓝夜空
BACKGROUND = "#071426"

# 梵高风格蓝色
BLUES = [
    "#0B1F3A",
    "#102E54",
    "#153D6B",
    "#1C4E80",
    "#24639A",
]

# 星星颜色
STAR_COLORS = [
    "#FFD84D",
    "#FFE681",
    "#FFF1A8",
    "#F8C94A",
]

random.seed(250)


# ============================================================
# Fake contribution data
# Later we will replace this with GitHub API data
# ============================================================

def generate_fake_contributions():
    """
    Generate fake GitHub contribution data.

    52 weeks × 7 days
    """

    data = []

    for week in range(52):
        week_data = []

        for day in range(7):

            # 大部分时间没有贡献
            value = random.choices(
                [0, 1, 2, 3, 5, 8, 12],
                weights=[30, 20, 15, 12, 8, 4, 1]
            )[0]

            week_data.append(value)

        data.append(week_data)

    return data


# ============================================================
# SVG helpers
# ============================================================

def circle(cx, cy, r, fill, opacity=1.0):
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" '
        f'fill="{fill}" opacity="{opacity}"/>'
    )


def path(d, stroke, width, opacity=1.0, fill="none"):
    return (
        f'<path d="{d}" '
        f'stroke="{stroke}" '
        f'stroke-width="{width}" '
        f'opacity="{opacity}" '
        f'fill="{fill}" '
        f'stroke-linecap="round"/>'
    )


# ============================================================
# Background
# ============================================================

def draw_background():
    svg = []

    svg.append(
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{BACKGROUND}"/>'
    )

    # 漩涡状蓝色笔触
    for i in range(28):

        y = random.randint(40, 280)

        start_x = random.randint(-100, 100)

        d = (
            f"M {start_x} {y} "
            f"C 250 {y-80}, "
            f"400 {y+80}, "
            f"600 {y} "
            f"S 900 {y-80}, "
            f"1300 {y+random.randint(-50, 50)}"
        )

        svg.append(
            path(
                d,
                random.choice(BLUES),
                random.randint(3, 12),
                random.uniform(0.25, 0.7)
            )
        )

    # 小山
    svg.append(
        '<path '
        'd="M0 370 '
        'C150 320 220 350 330 330 '
        'C430 310 510 360 620 335 '
        'C760 300 850 350 1000 320 '
        'C1100 300 1160 340 1200 320 '
        'L1200 460 L0 460 Z" '
        'fill="#081A2F"/>'
    )

    # 左边的树
    svg.append(
        '<path '
        'd="M110 460 '
        'C125 390 115 340 140 270 '
        'C155 330 175 350 165 460 Z" '
        'fill="#030B14"/>'
    )

    return svg


# ============================================================
# Stars
# ============================================================

def draw_star(cx, cy, level):

    result = []

    if level <= 0:
        result.append(
            circle(
                cx,
                cy,
                2,
                "#17304A",
                0.8
            )
        )
        return result

    if level <= 2:
        size = 2
        glow = 0.35

    elif level <= 5:
        size = 3
        glow = 0.5

    elif level <= 8:
        size = 4
        glow = 0.7

    else:
        size = 6
        glow = 0.9

    color = random.choice(STAR_COLORS)

    # 光晕
    result.append(
        circle(
            cx,
            cy,
            size * 3,
            color,
            0.08
        )
    )

    # 星星主体
    result.append(
        circle(
            cx,
            cy,
            size,
            color,
            glow
        )
    )

    # 高贡献做成十字星
    if level >= 5:

        result.append(
            path(
                f"M {cx-size*3} {cy} "
                f"L {cx+size*3} {cy}",
                color,
                1.2,
                0.65
            )
        )

        result.append(
            path(
                f"M {cx} {cy-size*3} "
                f"L {cx} {cy+size*3}",
                color,
                1.2,
                0.65
            )
        )

    return result


# ============================================================
# Main SVG generation
# ============================================================
def get_github_contributions(token):

    query = """
    query($username: String!) {
      user(login: $username) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                contributionCount
                weekday
              }
            }
          }
        }
      }
    }
    """

    response = requests.post(
        "https://api.github.com/graphql",
        json={
            "query": query,
            "variables": {
                "username": USERNAME
            }
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    )

    response.raise_for_status()

    result = response.json()

    if "errors" in result:
        raise RuntimeError(result["errors"])

    weeks = result["data"]["user"]["contributionsCollection"][
        "contributionCalendar"
    ]["weeks"]

    contributions = []

    for week in weeks:

        week_data = []

        for day in week["contributionDays"]:
            week_data.append(
                day["contributionCount"]
            )

        contributions.append(week_data)

    return contributions


def generate_svg(contributions):

    svg = []

    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}">'
    )

    # Background
    svg.extend(draw_background())

    # Moon
    svg.append(
        circle(
            1000,
            110,
            42,
            "#FFE58A",
            0.95
        )
    )

    # Moon shadow
    svg.append(
        circle(
            1020,
            92,
            38,
            BACKGROUND,
            1
        )
    )

    # Random decorative stars
    for _ in range(90):

        x = random.randint(20, WIDTH - 20)
        y = random.randint(20, 290)

        size = random.choice([1, 1, 1, 2])

        svg.append(
            circle(
                x,
                y,
                size,
                random.choice(STAR_COLORS),
                random.uniform(0.25, 0.8)
            )
        )

    # GitHub contribution stars
    #
    # 52 × 7
    #
    start_x = 60
    start_y = 325

    cell_x = 20
    cell_y = 15

    for week in range(len(contributions)):

        for day in range(len(contributions[week])):

            value = contributions[week][day]

            x = start_x + week * cell_x
            y = start_y + day * cell_y

            svg.extend(
                draw_star(
                    x,
                    y,
                    value
                )
            )

    # Title
    svg.append(
        '<text '
        'x="60" y="55" '
        'fill="#FFE681" '
        'font-size="24" '
        'font-family="monospace" '
        'font-weight="bold">'
        'PsyCube250 · Starry Night'
        '</text>'
    )

    svg.append("</svg>")

    OUTPUT.write_text(
        "\n".join(svg),
        encoding="utf-8"
    )

    print(f"Generated: {OUTPUT.resolve()}")


if __name__ == "__main__":

    import os

    token = os.environ.get("GITHUB_TOKEN")

    if token:
        print("Using GitHub API...")
        contributions = get_github_contributions(token)
    else:
        print("No GitHub token found.")
        print("Using fake contribution data...")
        contributions = generate_fake_contributions()

    generate_svg(contributions)