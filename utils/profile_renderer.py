from __future__ import annotations

import os
import unicodedata
from functools import lru_cache
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

try:
    from fontTools.ttLib import TTFont
except Exception:  # pragma: no cover
    TTFont = None


CANVAS_W = 1400
CANVAS_H = 900


def normalize_name_for_render(text: str) -> str:
    raw = str(text or "").replace("\n", " ").strip()
    if not raw:
        return "Unknown"

    normalized = unicodedata.normalize("NFKC", raw)
    normalized = " ".join(normalized.split())
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


@lru_cache(maxsize=256)
def _font_support_score(font_path: str, text: str) -> tuple[int, int]:
    """Higher is better: (supported_chars, total_chars)."""
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
    candidates = _font_candidates(bold=bold)
    existing = [path for path in candidates if os.path.exists(path)]

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


def _font(size: int, bold: bool = False, text: str = "") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _pick_font_path(text, bold=bold)
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass

    # very last fallback
    for path in _font_candidates(bold=bold):
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue

    return ImageFont.load_default()


def _rounded_gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
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


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, min_size: int = 24, bold: bool = False):
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
    draw.rounded_rectangle(box, radius=28, fill=(18, 28, 52), outline=(65, 83, 121), width=2)
    draw.rounded_rectangle((x1, y1, x1 + 10, y2), radius=5, fill=accent)
    draw.text((x1 + 34, y1 + 22), label.upper(), font=_font(24, bold=True, text=label), fill=(148, 163, 194))
    value_font = _fit_text(draw, value, x2 - x1 - 65, 42, 24, bold=True)
    draw.text((x1 + 34, y1 + 62), value, font=value_font, fill=(240, 245, 255))


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
    """Render a premium profile card as JPEG with Unicode-safe name handling."""
    full_name = normalize_name_for_render(full_name)
    collector_rank = normalize_name_for_render(collector_rank)
    next_rank_name = normalize_name_for_render(next_rank_name)

    img = _rounded_gradient((CANVAS_W, CANVAS_H), (12, 18, 39), (28, 18, 58))
    draw = ImageDraw.Draw(img)

    glow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.ellipse((-180, -220, 520, 480), fill=(61, 121, 255, 90))
    gd.ellipse((980, -100, 1580, 500), fill=(174, 72, 255, 75))
    gd.ellipse((830, 620, 1500, 1200), fill=(43, 218, 190, 40))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(100))
    img = Image.alpha_composite(img.convert("RGBA"), glow_layer).convert("RGB")
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((38, 38, CANVAS_W - 38, CANVAS_H - 38), radius=46, outline=(95, 122, 190), width=3)
    draw.rounded_rectangle((50, 50, CANVAS_W - 50, CANVAS_H - 50), radius=40, outline=(52, 64, 101), width=2)

    title_text = "BIKA CHARACTERS PROFILE"
    title_font = _fit_text(draw, title_text, 1120, 62, 42, bold=True)
    draw.text((CANVAS_W // 2, 74), title_text, font=title_font, fill=(244, 247, 255), anchor="ma")
    draw.rounded_rectangle((460, 145, 940, 151), radius=3, fill=(107, 112, 255))

    avatar_size = 230
    avatar_x, avatar_y = 105, 205
    if avatar_bytes:
        try:
            avatar = Image.open(BytesIO(avatar_bytes)).convert("RGB")
            avatar = ImageOps.fit(avatar, (avatar_size, avatar_size), method=Image.Resampling.LANCZOS)
        except Exception:
            avatar = None
    else:
        avatar = None

    mask = Image.new("L", (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=255)

    draw.ellipse((avatar_x - 16, avatar_y - 16, avatar_x + avatar_size + 16, avatar_y + avatar_size + 16), outline=(111, 88, 255), width=8)
    draw.ellipse((avatar_x - 7, avatar_y - 7, avatar_x + avatar_size + 7, avatar_y + avatar_size + 7), outline=(72, 204, 255), width=4)

    if avatar is not None:
        img.paste(avatar, (avatar_x, avatar_y), mask)
    else:
        draw.ellipse((avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size), fill=(41, 54, 92))
        initial = (full_name.strip()[:1] or "?").upper()
        draw.text(
            (avatar_x + avatar_size // 2, avatar_y + avatar_size // 2),
            initial,
            font=_font(96, bold=True, text=initial),
            fill=(226, 233, 255),
            anchor="mm",
        )

    name_font = _fit_text(draw, full_name, 870, 58, 30, bold=True)
    draw.text((390, 245), full_name, font=name_font, fill=(255, 255, 255))
    rank_line = f"{collector_emoji}  {collector_rank}"
    draw.text(
        (392, 320),
        rank_line,
        font=_font(34, bold=True, text=rank_line),
        fill=(166, 184, 255),
    )
    identity_text = "Collector Identity • Global Collection Network"
    draw.text((392, 370), identity_text, font=_font(25, text=identity_text), fill=(140, 153, 184))

    left = 105
    top = 500
    gap = 28
    card_w = 580
    card_h = 135

    _draw_stat_card(draw, (left, top, left + card_w, top + card_h), "Profile ID", f"#{profile_id:,}", (104, 129, 255))
    _draw_stat_card(draw, (left + card_w + gap, top, left + 2 * card_w + gap, top + card_h), "Total Cards", f"{unique_cards:,} UNIQUE", (65, 215, 194))
    _draw_stat_card(draw, (left, top + card_h + gap, left + card_w, top + 2 * card_h + gap), "Global Rank", f"#{global_rank:,}", (246, 179, 67))
    _draw_stat_card(
        draw,
        (left + card_w + gap, top + card_h + gap, left + 2 * card_w + gap, top + 2 * card_h + gap),
        "Collector Rank",
        collector_rank,
        (194, 99, 255),
    )

    footer_y = 826
    if next_rank_target > 0 and next_rank_name:
        progress = min(1.0, max(0.0, unique_cards / next_rank_target))
        next_label = f"NEXT: {next_rank_name}"
        draw.text((105, footer_y - 30), next_label, font=_font(21, bold=True, text=next_label), fill=(150, 164, 198))
        draw.rounded_rectangle((420, footer_y - 20, 1270, footer_y), radius=10, fill=(34, 43, 73))
        draw.rounded_rectangle(
            (420, footer_y - 20, 420 + int(850 * progress), footer_y),
            radius=10,
            fill=(105, 117, 255),
        )
        progress_text = f"{unique_cards:,}/{next_rank_target:,}"
        draw.text((1290, footer_y - 30), progress_text, font=_font(20, bold=True, text=progress_text), fill=(191, 203, 232), anchor="ra")
    else:
        end_text = "LEGENDARY COLLECTION STATUS"
        draw.text((CANVAS_W // 2, footer_y - 8), end_text, font=_font(24, bold=True, text=end_text), fill=(232, 198, 98), anchor="ma")

    out = BytesIO()
    out.name = "bika_profile.jpg"
    img.save(out, format="JPEG", quality=93, optimize=True)
    out.seek(0)
    return out
