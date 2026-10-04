from __future__ import annotations

import os
import unicodedata
from functools import lru_cache
from io import BytesIO
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

try:
    import regex as _regex
except Exception:  # pragma: no cover
    _regex = None

try:
    from fontTools.ttLib import TTFont
except Exception:  # pragma: no cover
    TTFont = None


CANVAS_W = 1600
CANVAS_H = 1000

TWEMOJI_VERSION = "17.0.3"
TWEMOJI_PNG_BASE = (
    "https://cdn.jsdelivr.net/gh/jdecked/"
    f"twemoji@{TWEMOJI_VERSION}/assets/72x72"
)

EMOJI_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto/NotoEmoji-VariableFont_wght.ttf",
    "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
    "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf",
    "/usr/share/fonts/truetype/unifont/unifont_sample.ttf",
    "/usr/share/fonts/truetype/unifont/unifont.otf",
)


def normalize_name_for_render(text: str) -> str:
    raw = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    if not raw:
        return "Unknown"

    normalized = unicodedata.normalize("NFKC", raw)
    cleaned: list[str] = []

    # Keep ZWJ/variation selectors/keycap marks because they are part of emoji
    # grapheme clusters. Drop other control/format characters that destabilize PIL.
    for ch in normalized:
        category = unicodedata.category(ch)
        if category.startswith("C") and ch not in {"\u200d", "\ufe0f", "\u20e3"}:
            continue
        cleaned.append(ch)

    normalized = " ".join("".join(cleaned).split())
    return normalized or "Unknown"


def _font_candidates(bold: bool = False) -> list[str]:
    if bold:
        return [
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansMyanmar-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansThai-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]

    return [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansMyanmar-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]


@lru_cache(maxsize=512)
def _font_support_score(font_path: str, text: str) -> tuple[int, int]:
    if not font_path or not os.path.exists(font_path):
        return (0, len(text))

    chars = [ch for ch in text if not ch.isspace()]
    if not chars:
        return (1, 1)

    if TTFont is None:
        return (0, len(chars))

    try:
        font = TTFont(font_path, lazy=True)
        cmap = {}
        for table in font["cmap"].tables:
            cmap.update(table.cmap)
        supported = sum(1 for ch in chars if ord(ch) in cmap)
        return (supported, len(chars))
    except Exception:
        return (0, len(chars))


def _pick_font_path(text: str, bold: bool = False) -> str | None:
    text = normalize_name_for_render(text)
    existing = [
        path
        for path in _font_candidates(bold=bold)
        if os.path.exists(path)
    ]

    if not existing:
        return None

    best = existing[0]
    best_score = (-1, 1)

    for path in existing:
        score = _font_support_score(path, text)
        if score[0] > best_score[0]:
            best = path
            best_score = score

        if score[0] >= score[1]:
            break

    return best


def _layout_engine():
    try:
        return ImageFont.Layout.RAQM
    except Exception:
        return ImageFont.Layout.BASIC


def _font(
    size: int,
    bold: bool = False,
    text: str = "",
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _pick_font_path(text, bold=bold)

    if path:
        try:
            return ImageFont.truetype(
                path,
                size=size,
                layout_engine=_layout_engine(),
            )
        except Exception:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass

    for path in _font_candidates(bold=bold):
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue

    return ImageFont.load_default()


def _graphemes(text: str) -> list[str]:
    value = str(text or "")
    if _regex is not None:
        try:
            return _regex.findall(r"\X", value)
        except Exception:
            pass
    return list(value)


def _is_emoji_cluster(cluster: str) -> bool:
    if not cluster:
        return False

    if "\u200d" in cluster or "\ufe0f" in cluster or "\u20e3" in cluster:
        return True

    for ch in cluster:
        cp = ord(ch)
        if (
            0x1F000 <= cp <= 0x1FAFF
            or 0x2600 <= cp <= 0x27BF
            or 0x2300 <= cp <= 0x23FF
            or 0x2B00 <= cp <= 0x2BFF
        ):
            return True

    return False


def _split_runs(text: str) -> list[tuple[bool, str]]:
    runs: list[tuple[bool, str]] = []

    for cluster in _graphemes(text):
        is_emoji = _is_emoji_cluster(cluster)

        if runs and runs[-1][0] == is_emoji:
            old_is_emoji, old_text = runs[-1]
            runs[-1] = (old_is_emoji, old_text + cluster)
        else:
            runs.append((is_emoji, cluster))

    return runs


@lru_cache(maxsize=32)
def _emoji_font_for_path(path: str, size: int):
    if not path or not os.path.exists(path):
        return None

    requested = max(16, int(size))
    sizes = (109, 128, 96, 64, 48, 32) if "NotoColorEmoji" in path else (requested, 128, 96, 64, 48, 32)
    for font_size in sizes:
        try:
            return ImageFont.truetype(path, size=font_size)
        except Exception:
            continue
    return None


@lru_cache(maxsize=256)
def _twemoji_asset(cluster: str) -> bytes | None:
    """Fetch a version-pinned Twemoji PNG for a Unicode emoji grapheme."""
    if not cluster:
        return None

    codepoints = "-".join(
        f"{ord(ch):x}"
        for ch in cluster
        if ord(ch) != 0xFE0F
    )
    if not codepoints:
        return None

    url = f"{TWEMOJI_PNG_BASE}/{codepoints}.png"
    try:
        request = Request(
            url,
            headers={"User-Agent": "BikaCharacter/1.0 profile-renderer"},
        )
        with urlopen(request, timeout=3.5) as response:
            data = response.read(128 * 1024)
        if not data.startswith(b"\x89PNG"):
            return None
        return data
    except (OSError, URLError, TimeoutError):
        return None


def _render_twemoji_cluster(cluster: str, target_size: int) -> Image.Image | None:
    data = _twemoji_asset(cluster)
    if not data:
        return None

    try:
        emoji = Image.open(BytesIO(data)).convert("RGBA")
        bbox = emoji.getbbox()
        if not bbox:
            return None
        emoji = emoji.crop(bbox)
        target = max(12, int(target_size))
        ratio = min(target / emoji.width, target / emoji.height)
        return emoji.resize(
            (
                max(1, int(emoji.width * ratio)),
                max(1, int(emoji.height * ratio)),
            ),
            Image.Resampling.LANCZOS,
        )
    except Exception:
        return None


def _render_emoji_cluster(cluster: str, target_size: int) -> Image.Image | None:
    if not cluster:
        return None

    target = max(12, int(target_size))
    for path in EMOJI_FONT_CANDIDATES:
        font = _emoji_font_for_path(path, target)
        if font is None:
            continue
        try:
            canvas_size = max(180, target * 3)
            canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(canvas)
            draw.text(
                (canvas_size // 2, canvas_size // 2),
                cluster,
                font=font,
                anchor="mm",
                embedded_color=("ColorEmoji" in path or "NotoColorEmoji" in path),
                fill=(255, 255, 255, 255),
            )
            bbox = canvas.getbbox()
            if not bbox:
                continue
            cropped = canvas.crop(bbox)
            ratio = min(target / cropped.width, target / cropped.height)
            return cropped.resize(
                (max(1, int(cropped.width * ratio)), max(1, int(cropped.height * ratio))),
                Image.Resampling.LANCZOS,
            )
        except Exception:
            continue

    # Last-resort, deterministic Unicode emoji fallback. Twemoji publishes
    # versioned PNG assets for RGI Unicode emoji, including flags, skin tones,
    # keycaps, and ZWJ sequences. Keep this after local fonts so normal
    # rendering remains fast and offline-safe when Noto Color Emoji works.
    return _render_twemoji_cluster(cluster, target)

    
def _text_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    size: int,
    bold: bool,
) -> int:
    width = 0

    for is_emoji, run in _split_runs(text):
        if is_emoji:
            for cluster in _graphemes(run):
                emoji_img = _render_emoji_cluster(
                    cluster,
                    max(12, int(size * 1.08)),
                )
                width += (
                    emoji_img.width
                    if emoji_img is not None
                    else int(size * 1.05)
                )
                width += max(1, int(size * 0.08))
        else:
            font = _font(size, bold=bold, text=run)
            bbox = draw.textbbox((0, 0), run, font=font)
            width += max(0, bbox[2] - bbox[0])

    return width


def _fit_mixed_text_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    start_size: int,
    min_size: int = 24,
    bold: bool = False,
) -> int:
    size = int(start_size)

    while size > int(min_size):
        if _text_width(draw, text, size, bold) <= int(max_width):
            return size
        size -= 2

    return int(min_size)


def _truncate_mixed_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    size: int,
    bold: bool = False,
) -> str:
    value = normalize_name_for_render(text)
    if _text_width(draw, value, size, bold) <= max_width:
        return value

    clusters = _graphemes(value)
    while clusters:
        candidate = "".join(clusters).rstrip() + "…"
        if _text_width(draw, candidate, size, bold) <= max_width:
            return candidate
        clusters.pop()
    return "…"


def _draw_mixed_text(
    image: Image.Image,
    xy: tuple[int, int],
    text: str,
    *,
    size: int,
    fill: tuple[int, int, int],
    bold: bool = False,
) -> int:
    draw = ImageDraw.Draw(image)
    x, y = int(xy[0]), int(xy[1])
    cursor_x = x

    for is_emoji, run in _split_runs(text):
        if is_emoji:
            for cluster in _graphemes(run):
                emoji_img = _render_emoji_cluster(
                    cluster,
                    max(12, int(size * 1.08)),
                )

                if emoji_img is not None:
                    emoji_y = y + max(
                        0,
                        int((size * 1.1 - emoji_img.height) / 2),
                    )
                    image.paste(
                        emoji_img,
                        (cursor_x, emoji_y),
                        emoji_img,
                    )
                    cursor_x += emoji_img.width
                else:
                    fallback_font = _font(
                        size,
                        bold=bold,
                        text=cluster,
                    )
                    draw.text(
                        (cursor_x, y),
                        cluster,
                        font=fallback_font,
                        fill=fill,
                    )
                    bbox = draw.textbbox(
                        (0, 0),
                        cluster,
                        font=fallback_font,
                    )
                    cursor_x += max(0, bbox[2] - bbox[0])

                cursor_x += max(1, int(size * 0.08))

        else:
            font = _font(size, bold=bold, text=run)
            draw.text(
                (cursor_x, y),
                run,
                font=font,
                fill=fill,
            )
            bbox = draw.textbbox((0, 0), run, font=font)
            cursor_x += max(0, bbox[2] - bbox[0])

    return cursor_x


def _rounded_gradient(
    size: tuple[int, int],
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()

    for y in range(h):
        ratio = y / max(1, h - 1)
        r = int(top[0] * (1 - ratio) + bottom[0] * ratio)
        g = int(top[1] * (1 - ratio) + bottom[1] * ratio)
        b = int(top[2] * (1 - ratio) + bottom[2] * ratio)

        for x in range(w):
            px[x, y] = (r, g, b)

    return img


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    start_size: int,
    min_size: int = 24,
    bold: bool = False,
):
    text = normalize_name_for_render(text)
    size = start_size

    while size > min_size:
        font = _font(size, bold=bold, text=text)
        box = draw.textbbox((0, 0), text, font=font)

        if box[2] - box[0] <= max_width:
            return font

        size -= 2

    return _font(min_size, bold=bold, text=text)


def _draw_stat_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    label: str,
    value: str,
    accent: tuple[int, int, int],
) -> None:
    x1, y1, x2, y2 = box

    draw.rounded_rectangle(
        box,
        radius=28,
        fill=(18, 28, 52),
        outline=(65, 83, 121),
        width=2,
    )
    draw.rounded_rectangle(
        (x1, y1, x1 + 10, y2),
        radius=5,
        fill=accent,
    )

    draw.text(
        (x1 + 34, y1 + 22),
        label.upper(),
        font=_font(24, bold=True, text=label),
        fill=(148, 163, 194),
    )

    value_font = _fit_text(
        draw,
        value,
        x2 - x1 - 65,
        42,
        24,
        bold=True,
    )

    draw.text(
        (x1 + 34, y1 + 62),
        value,
        font=value_font,
        fill=(240, 245, 255),
    )


def _hex_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return (255, 255, 255, alpha)
    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
        int(alpha),
    )


def _neon_line(
    image: Image.Image,
    points: list[tuple[int, int]],
    *,
    fill: tuple[int, int, int, int],
    width: int = 3,
    glow_width: int = 14,
) -> None:
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.line(points, fill=fill, width=glow_width, joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(max(2, glow_width // 2)))
    image.alpha_composite(glow)
    ImageDraw.Draw(image).line(points, fill=fill, width=width, joint="curve")


def _angular_panel(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[int, int]],
    *,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int],
    width: int = 2,
) -> None:
    draw.polygon(points, fill=fill)
    draw.line(points + [points[0]], fill=outline, width=width, joint="curve")


def _draw_transformer_chip(
    image: Image.Image,
    center: tuple[int, int],
    radius: int,
    *,
    accent: tuple[int, int, int, int],
) -> None:
    draw = ImageDraw.Draw(image)
    cx, cy = center
    outer = [
        (cx, cy - radius),
        (cx + radius, cy - radius // 2),
        (cx + radius, cy + radius // 2),
        (cx, cy + radius),
        (cx - radius, cy + radius // 2),
        (cx - radius, cy - radius // 2),
    ]
    inner_r = int(radius * 0.58)
    inner = [
        (cx, cy - inner_r),
        (cx + inner_r, cy - inner_r // 2),
        (cx + inner_r, cy + inner_r // 2),
        (cx, cy + inner_r),
        (cx - inner_r, cy + inner_r // 2),
        (cx - inner_r, cy - inner_r // 2),
    ]
    _angular_panel(draw, outer, fill=(9, 17, 30, 255), outline=accent, width=4)
    _angular_panel(draw, inner, fill=(21, 31, 48, 255), outline=(188, 210, 232, 150), width=2)
    draw.line(
        [(cx - inner_r // 2, cy), (cx + inner_r // 2, cy)],
        fill=accent,
        width=max(2, radius // 8),
    )
    draw.line(
        [(cx, cy - inner_r // 2), (cx, cy + inner_r // 2)],
        fill=accent,
        width=max(2, radius // 8),
    )


def render_profile_card(
    *,
    full_name: str,
    profile_id: int,
    unique_cards: int,
    global_rank: int,
    collector_rank: str,
    collector_emoji: str,
    avatar_bytes: Optional[bytes] = None,
    next_rank_name: str = "",
    next_rank_target: int = 0,
) -> BytesIO:
    """Render a high-resolution neon mechanical / transformer-inspired profile card."""

    full_name = normalize_name_for_render(full_name)
    collector_rank = normalize_name_for_render(collector_rank)
    collector_emoji = normalize_name_for_render(collector_emoji)
    next_rank_name = normalize_name_for_render(next_rank_name)

    # 1600x1000 keeps Telegram text and avatar details sharp while remaining
    # compact enough for a profile-card image. PNG is used to avoid JPEG ringing
    # around thin neon lines and small glyphs.
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), (4, 8, 15, 255))
    bg = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(bg)

    # Layered reactor glows.
    for box, color in (
        ((-320, -260, 700, 620), (0, 224, 255, 72)),
        ((1000, -240, 1780, 520), (255, 82, 183, 55)),
        ((760, 650, 1760, 1230), (255, 143, 42, 52)),
        ((-260, 700, 650, 1240), (74, 112, 255, 48)),
    ):
        bd.ellipse(box, fill=color)
    bg = bg.filter(ImageFilter.GaussianBlur(120))
    img.alpha_composite(bg)

    draw = ImageDraw.Draw(img)

    # Technical grid / scanline texture.
    for x in range(70, CANVAS_W - 50, 55):
        draw.line((x, 70, x, CANVAS_H - 55), fill=(31, 75, 99, 34), width=1)
    for y in range(70, CANVAS_H - 55, 55):
        draw.line((70, y, CANVAS_W - 50, y), fill=(31, 75, 99, 28), width=1)

    # Main armored shell: chamfered rather than a soft rounded card.
    shell = [
        (70, 60), (1530, 60), (1560, 90), (1560, 910),
        (1530, 940), (70, 940), (40, 910), (40, 90),
    ]
    _angular_panel(
        draw, shell,
        fill=(7, 15, 27, 246),
        outline=(76, 120, 148, 220),
        width=3,
    )
    inner_shell = [
        (86, 78), (1514, 78), (1538, 102), (1538, 898),
        (1514, 922), (86, 922), (62, 898), (62, 102),
    ]
    _angular_panel(
        draw, inner_shell,
        fill=(8, 18, 32, 150),
        outline=(22, 65, 91, 210),
        width=2,
    )

    # Neon perimeter segments.
    _neon_line(
        img,
        [(95, 92), (560, 92)],
        fill=(46, 232, 255, 235),
        width=5,
        glow_width=20,
    )
    _neon_line(
        img,
        [(580, 92), (1030, 92)],
        fill=(78, 133, 255, 235),
        width=5,
        glow_width=20,
    )
    _neon_line(
        img,
        [(1050, 92), (1505, 92)],
        fill=(255, 75, 175, 230),
        width=5,
        glow_width=20,
    )
    _neon_line(
        img,
        [(1508, 105), (1508, 350)],
        fill=(255, 146, 49, 210),
        width=4,
        glow_width=16,
    )

    # Header / identity.
    draw.text(
        (112, 122),
        "BIKA // CHARACTER NETWORK",
        font=_font(27, bold=True, text="BIKA // CHARACTER NETWORK"),
        fill=(92, 229, 255),
    )
    draw.text(
        (112, 166),
        "NEON COLLECTOR SYSTEM  •  PROFILE CORE",
        font=_font(19, bold=True, text="NEON COLLECTOR SYSTEM  •  PROFILE CORE"),
        fill=(113, 139, 163),
    )
    _draw_transformer_chip(img, (1450, 151), 42, accent=(55, 232, 255, 225))

    # Avatar reactor housing.
    avatar_size = 270
    avatar_x, avatar_y = 112, 270
    reactor_glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    rg = ImageDraw.Draw(reactor_glow)
    rg.ellipse(
        (avatar_x - 42, avatar_y - 42, avatar_x + avatar_size + 42, avatar_y + avatar_size + 42),
        fill=(0, 218, 255, 88),
    )
    reactor_glow = reactor_glow.filter(ImageFilter.GaussianBlur(32))
    img.alpha_composite(reactor_glow)
    draw = ImageDraw.Draw(img)

    # Mechanical ring.
    draw.ellipse(
        (avatar_x - 22, avatar_y - 22, avatar_x + avatar_size + 22, avatar_y + avatar_size + 22),
        fill=(5, 13, 23, 255),
        outline=(46, 227, 255, 240),
        width=7,
    )
    draw.ellipse(
        (avatar_x - 10, avatar_y - 10, avatar_x + avatar_size + 10, avatar_y + avatar_size + 10),
        outline=(139, 85, 255, 210),
        width=3,
    )
    for angle in range(0, 360, 45):
        # Small armor ticks around the reactor.
        import math
        rad = math.radians(angle)
        cx = avatar_x + avatar_size // 2
        cy = avatar_y + avatar_size // 2
        r1 = avatar_size // 2 + 28
        r2 = avatar_size // 2 + 42
        p1 = (int(cx + r1 * math.cos(rad)), int(cy + r1 * math.sin(rad)))
        p2 = (int(cx + r2 * math.cos(rad)), int(cy + r2 * math.sin(rad)))
        draw.line((p1, p2), fill=(76, 129, 153, 190), width=4)

    avatar = None
    if avatar_bytes:
        try:
            avatar = Image.open(BytesIO(avatar_bytes)).convert("RGB")
            avatar = ImageOps.fit(
                avatar,
                (avatar_size, avatar_size),
                method=Image.Resampling.LANCZOS,
            )
        except Exception:
            avatar = None

    mask = Image.new("L", (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=255)

    if avatar is not None:
        # Slight sharpening after fit for a cleaner high-resolution face/avatar.
        avatar = avatar.filter(ImageFilter.UnsharpMask(radius=1.1, percent=135, threshold=3))
        img.paste(avatar.convert("RGBA"), (avatar_x, avatar_y), mask)
    else:
        draw.ellipse(
            (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
            fill=(17, 34, 55, 255),
        )
        clusters = _graphemes(full_name.strip())
        first_cluster = clusters[0] if clusters else "?"
        if _is_emoji_cluster(first_cluster):
            emoji_img = _render_emoji_cluster(first_cluster, 132)
            if emoji_img is not None:
                px = avatar_x + avatar_size // 2 - emoji_img.width // 2
                py = avatar_y + avatar_size // 2 - emoji_img.height // 2
                img.paste(emoji_img, (px, py), emoji_img)
            else:
                draw.text(
                    (avatar_x + avatar_size // 2, avatar_y + avatar_size // 2),
                    first_cluster,
                    font=_font(100, bold=True, text=first_cluster),
                    fill=(237, 248, 255),
                    anchor="mm",
                )
        else:
            draw.text(
                (avatar_x + avatar_size // 2, avatar_y + avatar_size // 2),
                first_cluster.upper(),
                font=_font(112, bold=True, text=first_cluster),
                fill=(237, 248, 255),
                anchor="mm",
            )

    # Identity block.
    name_max = 670
    name_size = _fit_mixed_text_size(draw, full_name, name_max, 66, 30, bold=True)
    safe_name = _truncate_mixed_text(draw, full_name, name_max, name_size, bold=True)
    _draw_mixed_text(
        img, (438, 300), safe_name,
        size=name_size, fill=(245, 251, 255), bold=True,
    )
    rank_line = f"{collector_emoji}  {collector_rank}"
    rank_size = _fit_mixed_text_size(draw, rank_line, 650, 38, 24, bold=True)
    safe_rank = _truncate_mixed_text(draw, rank_line, 650, rank_size, bold=True)
    _draw_mixed_text(
        img, (438, 386), safe_rank,
        size=rank_size, fill=(74, 231, 255), bold=True,
    )
    identity_text = f"UNIT ID  #{int(profile_id):,}   //   VERIFIED COLLECTOR"
    draw.text(
        (438, 447),
        identity_text,
        font=_font(22, bold=True, text=identity_text),
        fill=(116, 143, 166),
    )

    # Accent data rail.
    _neon_line(
        img,
        [(438, 505), (1110, 505)],
        fill=(43, 184, 218, 155),
        width=2,
        glow_width=10,
    )

    # Collector level armor badge.
    badge = [
        (1170, 268), (1490, 268), (1510, 288), (1510, 476),
        (1490, 496), (1170, 496), (1150, 476), (1150, 288),
    ]
    _angular_panel(
        draw, badge,
        fill=(10, 23, 39, 250),
        outline=(61, 191, 219, 205),
        width=3,
    )
    draw.text(
        (1180, 292),
        "COLLECTOR CORE",
        font=_font(19, bold=True, text="COLLECTOR CORE"),
        fill=(108, 141, 164),
    )
    badge_size = _fit_mixed_text_size(draw, collector_emoji, 90, 55, 30, bold=True)
    _draw_mixed_text(
        img, (1180, 331), collector_emoji,
        size=badge_size, fill=(255, 255, 255), bold=True,
    )
    badge_rank = _truncate_mixed_text(draw, collector_rank, 235, 30, bold=True)
    draw.text(
        (1270, 343),
        badge_rank,
        font=_font(30, bold=True, text=badge_rank),
        fill=(239, 246, 255),
    )
    draw.text(
        (1180, 413),
        "SYSTEM STATUS",
        font=_font(16, bold=True, text="SYSTEM STATUS"),
        fill=(89, 119, 141),
    )
    draw.text(
        (1320, 408),
        "ONLINE",
        font=_font(20, bold=True, text="ONLINE"),
        fill=(77, 246, 180),
    )
    draw.ellipse((1447, 411, 1465, 429), fill=(77, 246, 180, 255))

    # Stat modules.
    top = 555
    left = 110
    gap = 20
    card_w = 355
    card_h = 128
    stat_boxes = [
        ("TOTAL CARDS", f"{int(unique_cards):,}", (52, 224, 244)),
        ("GLOBAL RANK", f"#{max(0, int(global_rank)):,}", (98, 130, 255)),
        ("PROFILE ID", f"#{int(profile_id):,}", (212, 91, 255)),
        ("COLLECTOR", collector_rank, (255, 154, 54)),
    ]
    for i, (label, value, accent) in enumerate(stat_boxes):
        x1 = left + i * (card_w + gap)
        x2 = x1 + card_w
        panel_points = [
            (x1 + 18, top), (x2 - 18, top), (x2, top + 18),
            (x2, top + card_h - 18), (x2 - 18, top + card_h),
            (x1 + 18, top + card_h), (x1, top + card_h - 18),
            (x1, top + 18),
        ]
        _angular_panel(
            draw, panel_points,
            fill=(9, 22, 37, 235),
            outline=(42, 73, 94, 220),
            width=2,
        )
        draw.rectangle((x1 + 2, top + 18, x1 + 8, top + card_h - 18), fill=accent + (235,))
        draw.text(
            (x1 + 30, top + 21),
            label,
            font=_font(19, bold=True, text=label),
            fill=(103, 133, 155),
        )
        value_size = _fit_mixed_text_size(
            draw, value, card_w - 58, 39, 23, bold=True,
        )
        safe_value = _truncate_mixed_text(
            draw, value, card_w - 58, value_size, bold=True,
        )
        draw.text(
            (x1 + 30, top + 61),
            safe_value,
            font=_font(value_size, bold=True, text=safe_value),
            fill=(241, 248, 255),
        )

    # Progress / power core.
    progress_y = 735
    progress_points = [
        (110, progress_y), (1490, progress_y), (1510, progress_y + 20),
        (1510, progress_y + 118), (1490, progress_y + 138),
        (110, progress_y + 138), (90, progress_y + 118), (90, progress_y + 20),
    ]
    _angular_panel(
        draw, progress_points,
        fill=(7, 19, 32, 245),
        outline=(45, 84, 108, 225),
        width=2,
    )

    if next_rank_target > 0 and next_rank_name:
        target = max(1, int(next_rank_target))
        progress = min(1.0, max(0.0, int(unique_cards) / target))
        next_label = f"NEXT CORE UPGRADE  //  {next_rank_name}"
        next_label = _truncate_mixed_text(draw, next_label, 830, 23, bold=True)
        draw.text(
            (125, progress_y + 24),
            next_label,
            font=_font(23, bold=True, text=next_label),
            fill=(218, 234, 244),
        )
        progress_value = f"{int(unique_cards):,} / {target:,}"
        draw.text(
            (1470, progress_y + 24),
            progress_value,
            font=_font(21, bold=True, text=progress_value),
            fill=(122, 161, 184),
            anchor="ra",
        )
        bar_x1, bar_y1, bar_x2, bar_y2 = 125, progress_y + 70, 1475, progress_y + 101
        draw.rounded_rectangle(
            (bar_x1, bar_y1, bar_x2, bar_y2),
            radius=15,
            fill=(22, 43, 59, 255),
            outline=(43, 75, 95, 200),
            width=2,
        )
        fill_x2 = bar_x1 + max(20, int((bar_x2 - bar_x1) * progress))
        _neon_line(
            img,
            [(bar_x1 + 3, (bar_y1 + bar_y2) // 2), (min(bar_x2 - 3, fill_x2), (bar_y1 + bar_y2) // 2)],
            fill=(42, 230, 255, 240),
            width=15,
            glow_width=28,
        )
        draw.ellipse(
            (min(bar_x2 - 16, fill_x2 - 9), bar_y1 + 7, min(bar_x2 - 2, fill_x2 + 5), bar_y2 - 7),
            fill=(229, 255, 255, 255),
        )
        pct = f"{progress * 100:.1f}%"
        draw.text(
            (1470, progress_y + 105),
            pct,
            font=_font(16, bold=True, text=pct),
            fill=(67, 224, 247),
            anchor="ra",
        )
    else:
        end_text = "✦  MAXIMUM CORE // LEGENDARY STATUS  ✦"
        end_text = _truncate_mixed_text(draw, end_text, 1250, 31, bold=True)
        end_width = _text_width(draw, end_text, 31, True)
        _draw_mixed_text(
            img,
            (CANVAS_W // 2 - end_width // 2, progress_y + 48),
            end_text,
            size=31,
            fill=(255, 186, 67),
            bold=True,
        )

    # Bottom mechanical telemetry.
    _neon_line(
        img,
        [(112, 900), (520, 900)],
        fill=(44, 218, 244, 180),
        width=2,
        glow_width=10,
    )
    footer = "BIKA // COLLECT • CLAIM • BUILD   |   PROFILE CORE v3"
    footer = _truncate_mixed_text(draw, footer, 640, 17, bold=True)
    draw.text(
        (800, 884),
        footer,
        font=_font(17, bold=True, text=footer),
        fill=(83, 113, 136),
        anchor="ma",
    )
    draw.text(
        (1488, 884),
        "1600×1000  //  HI-RES",
        font=_font(15, bold=True, text="1600×1000  //  HI-RES"),
        fill=(58, 91, 111),
        anchor="ra",
    )

    out = BytesIO()
    out.name = "bika_profile.png"
    # Lossless PNG preserves thin neon geometry, text edges and emoji composites.
    img.convert("RGB").save(
        out,
        format="PNG",
        optimize=True,
    )
    out.seek(0)
    return out

