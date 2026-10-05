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


def _font_paths(bold: bool = False) -> list[str]:
    """Return installed font candidates, ordered from broad to script-specific."""
    if bold:
        names = (
            "NotoSans-Bold.ttf",
            "NotoSansMyanmar-Bold.ttf",
            "NotoSansBamum-Bold.ttf",
            "NotoSansCoptic-Regular.ttf",
            "NotoSansSymbols-Bold.ttf",
            "NotoSansSymbols-Regular.ttf",
            "NotoSansSymbols2-Regular.ttf",
            "NotoSansMath-Regular.ttf",
            "NotoSansThai-Bold.ttf",
            "NotoNaskhArabic-Bold.ttf",
            "NotoSansCJK-Bold.ttc",
            "NotoSansCJK-Regular.ttc",
            "DejaVuSans-Bold.ttf",
            "LiberationSans-Bold.ttf",
        )
    else:
        names = (
            "NotoSans-Regular.ttf",
            "NotoSansMyanmar-Regular.ttf",
            "NotoSansBamum-Regular.ttf",
            "NotoSansCoptic-Regular.ttf",
            "NotoSansSymbols-Regular.ttf",
            "NotoSansSymbols2-Regular.ttf",
            "NotoSansMath-Regular.ttf",
            "NotoSansThai-Regular.ttf",
            "NotoNaskhArabic-Regular.ttf",
            "NotoSansCJK-Regular.ttc",
            "DejaVuSans.ttf",
            "LiberationSans-Regular.ttf",
        )

    roots = (
        "/usr/share/fonts/truetype/noto",
        "/usr/share/fonts/opentype/noto",
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/liberation2",
    )
    found: list[str] = []
    for root in roots:
        for name in names:
            path = os.path.join(root, name)
            if os.path.exists(path) and path not in found:
                found.append(path)
    return found


def _font_candidates(bold: bool = False) -> list[str]:
    return _font_paths(bold=bold)


@lru_cache(maxsize=4096)
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
        cmap: dict[int, str] = {}
        for table in font["cmap"].tables:
            cmap.update(table.cmap)
        supported = sum(1 for ch in chars if ord(ch) in cmap)
        return (supported, len(chars))
    except Exception:
        return (0, len(chars))


@lru_cache(maxsize=4096)
def _font_path_for_cluster(cluster: str, bold: bool = False) -> str | None:
    """Choose a font that covers every codepoint in one grapheme cluster."""
    value = str(cluster or "")
    if not value:
        return None

    candidates = _font_candidates(bold=bold)
    if not candidates:
        return None

    best: str | None = None
    best_score = (-1, -1)
    for path in candidates:
        supported, total = _font_support_score(path, value)
        if supported > best_score[0]:
            best = path
            best_score = (supported, total)
        if supported == total and total > 0:
            return path
    return best


@lru_cache(maxsize=1024)
def _font_runs(text: str, bold: bool = False) -> tuple[tuple[str | None, str], ...]:
    """Group graphemes by a font that can actually render each cluster."""
    runs: list[tuple[str | None, str]] = []
    for cluster in _graphemes(text):
        path = _font_path_for_cluster(cluster, bold=bold)
        if runs and runs[-1][0] == path:
            runs[-1] = (path, runs[-1][1] + cluster)
        else:
            runs.append((path, cluster))
    return tuple(runs)


def _pick_font_path(text: str, bold: bool = False) -> str | None:
    """Backward-compatible whole-text picker for non-mixed labels."""
    value = normalize_name_for_render(text)
    runs = _font_runs(value, bold=bold)
    if not runs:
        return None
    for path, run in runs:
        if path and run == value:
            return path
    return _font_path_for_cluster(value, bold=bold)

def _layout_engine():
    try:
        return ImageFont.Layout.RAQM
    except Exception:
        return ImageFont.Layout.BASIC


def _font_from_path(
    path: str | None,
    size: int,
    *,
    bold: bool = False,
    text: str = "",
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(
                path,
                size=int(size),
                layout_engine=_layout_engine(),
            )
        except Exception:
            try:
                return ImageFont.truetype(path, size=int(size))
            except Exception:
                pass
    return _font(size, bold=bold, text=text)


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
            for path, text_run in _font_runs(run, bold=bold):
                font = _font_from_path(path, size, bold=bold, text=text_run)
                bbox = draw.textbbox((0, 0), text_run, font=font)
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
                    image.paste(emoji_img, (cursor_x, emoji_y), emoji_img)
                    cursor_x += emoji_img.width
                else:
                    fallback_font = _font(size, bold=bold, text=cluster)
                    draw.text((cursor_x, y), cluster, font=fallback_font, fill=fill)
                    bbox = draw.textbbox((0, 0), cluster, font=fallback_font)
                    cursor_x += max(0, bbox[2] - bbox[0])
                cursor_x += max(1, int(size * 0.08))
        else:
            # Critical mixed-Unicode path: each grapheme is assigned to a
            # font that actually contains its glyphs, while adjacent graphemes
            # using the same font stay grouped for normal Latin shaping.
            for path, text_run in _font_runs(run, bold=bold):
                font = _font_from_path(path, size, bold=bold, text=text_run)
                draw.text((cursor_x, y), text_run, font=font, fill=fill)
                bbox = draw.textbbox((0, 0), text_run, font=font)
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
    size = int(start_size)
    while size > int(min_size):
        if _text_width(draw, text, size, bold) <= int(max_width):
            return _font(size, bold=bold, text=text)
        size -= 2
    return _font(int(min_size), bold=bold, text=text)

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
    """Render the BIKA Neon Transformer HUD profile card at high resolution."""

    full_name = normalize_name_for_render(full_name)
    collector_rank = normalize_name_for_render(collector_rank)
    collector_emoji = normalize_name_for_render(collector_emoji)
    next_rank_name = normalize_name_for_render(next_rank_name)

    # High-resolution master canvas. Telegram gets a lossless PNG so thin HUD
    # lines, small typography and emoji composites stay crisp.
    W, H = 1800, 1125
    img = Image.new("RGBA", (W, H), (3, 7, 16, 255))
    draw = ImageDraw.Draw(img)

    # ---------- ATMOSPHERE ----------
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-420, -360, 650, 620), fill=(0, 229, 255, 72))
    gd.ellipse((1110, -260, 2050, 580), fill=(94, 54, 255, 70))
    gd.ellipse((650, 690, 1650, 1350), fill=(255, 0, 166, 45))
    gd.ellipse((-250, 700, 620, 1320), fill=(0, 130, 255, 38))
    glow = glow.filter(ImageFilter.GaussianBlur(125))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    # Fine technical grid.
    for x in range(60, W - 40, 45):
        draw.line((x, 45, x, H - 45), fill=(34, 72, 101, 42), width=1)
    for y in range(45, H - 40, 45):
        draw.line((45, y, W - 45, y), fill=(34, 72, 101, 42), width=1)

    # Scanlines, deliberately subtle.
    for y in range(58, H - 58, 8):
        draw.line((58, y, W - 58, y), fill=(120, 210, 255, 11), width=1)

    # ---------- OUTER ARMORED FRAME ----------
    outer = [(42, 88), (88, 42), (W - 88, 42), (W - 42, 88),
             (W - 42, H - 88), (W - 88, H - 42), (88, H - 42), (42, H - 88)]
    inner = [(58, 101), (101, 58), (W - 101, 58), (W - 58, 101),
             (W - 58, H - 101), (W - 101, H - 58), (101, H - 58), (58, H - 101)]
    draw.polygon(outer, fill=(5, 12, 24, 248), outline=(53, 99, 126, 230), width=3)
    draw.line(outer + [outer[0]], fill=(0, 220, 255, 150), width=2, joint="curve")
    draw.line(inner + [inner[0]], fill=(65, 79, 113, 175), width=2, joint="curve")

    # Corner power nodes.
    for cx, cy, accent in (
        (88, 88, (0, 238, 255, 255)),
        (W - 88, 88, (139, 90, 255, 255)),
        (88, H - 88, (0, 151, 255, 255)),
        (W - 88, H - 88, (255, 46, 173, 255)),
    ):
        draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=(5, 13, 24, 255), outline=accent, width=3)
        draw.line((cx - 26, cy, cx - 11, cy), fill=accent, width=2)
        draw.line((cx + 11, cy, cx + 26, cy), fill=accent, width=2)
        draw.line((cx, cy - 26, cx, cy - 11), fill=accent, width=2)
        draw.line((cx, cy + 11, cx, cy + 26), fill=accent, width=2)

    # ---------- HEADER ----------
    header_y = 86
    _neon_line(img, [(110, header_y + 70), (510, header_y + 70), (548, header_y + 48)],
                fill=(0, 230, 255, 225), width=3, glow_width=18)
    _neon_line(img, [(1252, header_y + 48), (1290, header_y + 70), (1690, header_y + 70)],
                fill=(184, 85, 255, 225), width=3, glow_width=18)

    title = "BIKA // CHARACTER NETWORK"
    title_font = _fit_text(draw, title, 820, 54, 34, bold=True)
    draw.text((W // 2, 92), title, font=title_font, fill=(239, 251, 255), anchor="ma")
    sub = "TRANSFORMER HUD  •  COLLECTOR CORE  •  PROFILE ONLINE"
    sub_font = _fit_text(draw, sub, 850, 21, 16, bold=True)
    draw.text((W // 2, 145), sub, font=sub_font, fill=(88, 177, 207), anchor="ma")

    # Small live-status module.
    status_box = (1400, 103, 1650, 158)
    draw.rounded_rectangle(status_box, radius=14, fill=(6, 22, 30, 240), outline=(0, 225, 255, 170), width=2)
    draw.ellipse((1420, 122, 1438, 140), fill=(0, 255, 204, 255))
    draw.text((1452, 113), "CORE ONLINE", font=_font(18, bold=True, text="CORE ONLINE"), fill=(144, 244, 225))

    # ---------- MAIN IDENTITY BAY ----------
    bay = [(94, 202), (124, 172), (1690, 172), (1720, 202),
           (1720, 505), (1690, 535), (124, 535), (94, 505)]
    _angular_panel(draw, bay, fill=(6, 15, 29, 242), outline=(41, 83, 113, 230), width=2)

    # Accent rail.
    _neon_line(img, [(120, 222), (120, 486)], fill=(0, 224, 255, 210), width=4, glow_width=16)
    _neon_line(img, [(1694, 222), (1694, 486)], fill=(178, 74, 255, 200), width=4, glow_width=16)

    # ---------- AVATAR REACTOR ----------
    ax, ay, size = 150, 230, 250
    reactor = Image.new("RGBA", (430, 430), (0, 0, 0, 0))
    rg = ImageDraw.Draw(reactor)
    rc = (215, 340)
    rg.ellipse((55, 55, 375, 375), fill=(0, 219, 255, 20), outline=(0, 229, 255, 105), width=7)
    reactor = reactor.filter(ImageFilter.GaussianBlur(20))
    img.alpha_composite(reactor, (ax - 90, ay - 90))
    draw = ImageDraw.Draw(img)

    # Mechanical concentric housing.
    cx, cy = ax + size // 2, ay + size // 2
    for rr, col, ww in (
        (174, (0, 225, 255, 175), 4),
        (164, (99, 94, 255, 230), 7),
        (151, (180, 211, 230, 135), 2),
    ):
        draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=col, width=ww)

    # Rotational tick marks.
    import math
    for i in range(24):
        angle = math.radians(i * 15)
        r1, r2 = 156, 171
        x1 = cx + int(math.cos(angle) * r1)
        y1 = cy + int(math.sin(angle) * r1)
        x2 = cx + int(math.cos(angle) * r2)
        y2 = cy + int(math.sin(angle) * r2)
        accent = (0, 239, 255, 225) if i % 3 else (194, 83, 255, 235)
        draw.line((x1, y1, x2, y2), fill=accent, width=3)

    avatar = None
    if avatar_bytes:
        try:
            avatar = Image.open(BytesIO(avatar_bytes)).convert("RGB")
            avatar = ImageOps.fit(avatar, (size, size), method=Image.Resampling.LANCZOS)
        except Exception:
            avatar = None

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    if avatar is not None:
        img.paste(avatar.convert("RGBA"), (ax, ay), mask)
    else:
        draw.ellipse((ax, ay, ax + size, ay + size), fill=(13, 31, 55, 255))
        first = _graphemes(full_name)[0] if _graphemes(full_name) else "?"
        if _is_emoji_cluster(first):
            emoji_img = _render_emoji_cluster(first, 122)
            if emoji_img is not None:
                img.paste(emoji_img, (cx - emoji_img.width // 2, cy - emoji_img.height // 2), emoji_img)
            else:
                draw.text((cx, cy), first, font=_font(92, bold=True, text=first), fill=(241, 248, 255), anchor="mm")
        else:
            draw.text((cx, cy), first.upper(), font=_font(104, bold=True, text=first), fill=(241, 248, 255), anchor="mm")

    # Corner bolts around avatar.
    for px, py in ((ax - 4, ay - 4), (ax + size + 4, ay - 4), (ax - 4, ay + size + 4), (ax + size + 4, ay + size + 4)):
        draw.ellipse((px - 6, py - 6, px + 6, py + 6), fill=(6, 14, 25, 255), outline=(112, 170, 201, 220), width=2)

    # ---------- IDENTITY TEXT ----------
    text_x = 470
    name_max = 790
    name_size = _fit_mixed_text_size(draw, full_name, name_max, 66, 30, bold=True)
    safe_name = _truncate_mixed_text(draw, full_name, name_max, name_size, bold=True)
    # Crisp dark stroke gives the title a HUD/metallic edge without blurring.
    _draw_mixed_text(img, (text_x, 246), safe_name, size=name_size, fill=(247, 252, 255), bold=True)

    rank_line = f"{collector_emoji}  {collector_rank}"
    rank_size = _fit_mixed_text_size(draw, rank_line, 760, 36, 24, bold=True)
    rank_safe = _truncate_mixed_text(draw, rank_line, 760, rank_size, bold=True)
    _draw_mixed_text(img, (text_x, 327), rank_safe, size=rank_size, fill=(93, 225, 255), bold=True)

    # Identity metadata rail.
    meta = f"UNIT ID  #{int(profile_id):,}   /   VERIFIED COLLECTOR"
    draw.text((text_x, 390), meta, font=_font(22, bold=True, text=meta), fill=(105, 137, 164))
    draw.text((text_x, 430), "COLLECTION CORE AUTHORIZED", font=_font(18, bold=True, text="COLLECTION CORE AUTHORIZED"), fill=(63, 98, 124))

    # ---------- COLLECTOR CORE MODULE ----------
    core_box = [(1295, 220), (1322, 193), (1658, 193), (1685, 220),
                (1685, 488), (1658, 515), (1322, 515), (1295, 488)]
    _angular_panel(draw, core_box, fill=(8, 20, 35, 250), outline=(86, 94, 151, 220), width=2)
    _draw_transformer_chip(img, (1490, 300), 58, accent=(0, 232, 255, 245))
    draw.text((1490, 370), "COLLECTOR CORE", font=_font(19, bold=True, text="COLLECTOR CORE"), fill=(100, 143, 170), anchor="ma")
    core_rank = _truncate_mixed_text(draw, collector_rank, 310, 31, bold=True)
    core_w = _text_width(draw, core_rank, 31, True)
    _draw_mixed_text(img, (1490 - core_w // 2, 405), core_rank, size=31, fill=(239, 246, 255), bold=True)
    draw.text((1490, 460), "SYSTEM STATUS  •  ACTIVE", font=_font(15, bold=True, text="SYSTEM STATUS  •  ACTIVE"), fill=(56, 210, 190), anchor="ma")

    # ---------- STAT MATRIX ----------
    sy = 575
    sx = 110
    gap_x, gap_y = 24, 22
    card_w, card_h = 385, 150
    stats = (
        ("TOTAL CARDS", f"{int(unique_cards):,}", (0, 230, 255, 255), "COLLECTION"),
        ("GLOBAL RANK", f"#{max(0, int(global_rank)):,}", (105, 125, 255, 255), "NETWORK"),
        ("PROFILE ID", f"#{int(profile_id):,}", (188, 78, 255, 255), "IDENTITY"),
        ("COLLECTOR", collector_rank, (255, 168, 70, 255), "CORE"),
    )
    for i, (label, value, accent, tag) in enumerate(stats):
        row, col = divmod(i, 4)
        x1 = sx + col * (card_w + gap_x)
        y1 = sy + row * (card_h + gap_y)
        x2, y2 = x1 + card_w, y1 + card_h
        pts = [(x1, y1 + 18), (x1 + 18, y1), (x2 - 32, y1), (x2, y1 + 32),
               (x2, y2 - 18), (x2 - 18, y2), (x1 + 32, y2), (x1, y2 - 32)]
        _angular_panel(draw, pts, fill=(7, 18, 32, 245), outline=(48, 78, 103, 220), width=2)
        _neon_line(img, [(x1 + 22, y2 - 14), (x1 + 100, y2 - 14)], fill=accent, width=3, glow_width=10)
        draw.text((x1 + 28, y1 + 23), label, font=_font(19, bold=True, text=label), fill=(101, 135, 159))
        draw.text((x2 - 28, y1 + 23), tag, font=_font(14, bold=True, text=tag), fill=(55, 86, 110), anchor="ra")
        value_size = _fit_mixed_text_size(draw, value, card_w - 55, 43, 23, bold=True)
        safe_value = _truncate_mixed_text(draw, value, card_w - 55, value_size, bold=True)
        if _is_emoji_cluster(safe_value[:1]):
            _draw_mixed_text(img, (x1 + 28, y1 + 67), safe_value, size=value_size, fill=(242, 247, 255), bold=True)
        else:
            draw.text((x1 + 28, y1 + 67), safe_value, font=_font(value_size, bold=True, text=safe_value), fill=(242, 247, 255))

    # ---------- POWER / NEXT-RANK CORE ----------
    power_y = 760
    power = [(110, power_y + 28), (138, power_y), (1662, power_y),
             (1690, power_y + 28), (1690, power_y + 205), (1662, power_y + 233),
             (138, power_y + 233), (110, power_y + 205)]
    _angular_panel(draw, power, fill=(6, 16, 30, 248), outline=(55, 84, 112, 225), width=2)

    if next_rank_target > 0 and next_rank_name:
        target = max(1, int(next_rank_target))
        current = max(0, int(unique_cards))
        progress = min(1.0, current / target)
        draw.text((145, power_y + 27), "NEXT CORE UPGRADE", font=_font(17, bold=True, text="NEXT CORE UPGRADE"), fill=(70, 112, 138))
        next_text = f"{collector_emoji}  {next_rank_name}"
        next_size = _fit_mixed_text_size(draw, next_text, 620, 31, 23, bold=True)
        _draw_mixed_text(img, (145, power_y + 58), next_text, size=next_size, fill=(234, 244, 255), bold=True)

        ratio_text = f"{current:,} / {target:,}"
        draw.text((1650, power_y + 47), ratio_text, font=_font(25, bold=True, text=ratio_text), fill=(110, 205, 224), anchor="ra")

        bar_x1, bar_y1, bar_x2, bar_y2 = 145, power_y + 123, 1655, power_y + 154
        draw.rounded_rectangle((bar_x1, bar_y1, bar_x2, bar_y2), radius=15, fill=(21, 37, 55, 255), outline=(55, 80, 103, 220), width=1)
        fill_x2 = bar_x1 + max(20, int((bar_x2 - bar_x1) * progress))
        if fill_x2 > bar_x1:
            _neon_line(img, [(bar_x1 + 4, (bar_y1 + bar_y2) // 2), (fill_x2 - 4, (bar_y1 + bar_y2) // 2)],
                        fill=(0, 231, 255, 235), width=22, glow_width=34)
        draw.text((145, power_y + 174), f"POWER OUTPUT  {progress * 100:.1f}%", font=_font(16, bold=True, text="POWER OUTPUT  100.0%"), fill=(72, 113, 137))
        draw.text((1655, power_y + 174), "UPGRADE PATH  //  ARMED", font=_font(16, bold=True, text="UPGRADE PATH  //  ARMED"), fill=(70, 200, 185), anchor="ra")
    else:
        msg = "MAXIMUM CORE  //  LEGENDARY STATUS"
        msg_font = _fit_text(draw, msg, 1250, 42, 30, bold=True)
        draw.text((W // 2, power_y + 83), msg, font=msg_font, fill=(255, 197, 91), anchor="ma")
        draw.text((W // 2, power_y + 136), "COLLECTION LIMIT SURPASSED  •  CORE OUTPUT STABLE", font=_font(17, bold=True, text="COLLECTION LIMIT SURPASSED  •  CORE OUTPUT STABLE"), fill=(105, 145, 164), anchor="ma")

    # ---------- TELEMETRY FOOTER ----------
    footer_y = 1034
    _neon_line(img, [(112, footer_y), (540, footer_y)], fill=(0, 205, 240, 180), width=2, glow_width=8)
    _neon_line(img, [(1260, footer_y), (1688, footer_y)], fill=(173, 75, 255, 180), width=2, glow_width=8)
    footer = "BIKA NETWORK  •  COLLECT / CLAIM / EVOLVE  •  PROFILE CORE v3"
    footer = _truncate_mixed_text(draw, footer, 650, 16, bold=True)
    draw.text((W // 2, footer_y - 17), footer, font=_font(16, bold=True, text=footer), fill=(70, 100, 122), anchor="ma")

    out = BytesIO()
    out.name = "bika_profile.png"
    img.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out

