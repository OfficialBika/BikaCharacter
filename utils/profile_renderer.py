from __future__ import annotations

import os
import unicodedata
from functools import lru_cache
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

try:
    import regex as _regex
except Exception:  # pragma: no cover
    _regex = None

try:
    from fontTools.ttLib import TTFont
except Exception:  # pragma: no cover
    TTFont = None


CANVAS_W = 1400
CANVAS_H = 900

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
    return None


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
    """Render a premium profile card with robust Unicode + emoji handling."""

    full_name = normalize_name_for_render(full_name)
    collector_rank = normalize_name_for_render(collector_rank)
    collector_emoji = normalize_name_for_render(collector_emoji)
    next_rank_name = normalize_name_for_render(next_rank_name)

    img = _rounded_gradient(
        (CANVAS_W, CANVAS_H),
        (8, 13, 30),
        (23, 14, 48),
    ).convert("RGBA")

    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-260, -260, 620, 620), fill=(64, 122, 255, 92))
    gd.ellipse((1030, -180, 1690, 500), fill=(188, 74, 255, 82))
    gd.ellipse((760, 660, 1560, 1280), fill=(34, 226, 198, 50))
    glow = glow.filter(ImageFilter.GaussianBlur(115))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    panel = (34, 34, CANVAS_W - 34, CANVAS_H - 34)
    draw.rounded_rectangle(
        panel, radius=48, fill=(12, 19, 40, 236),
        outline=(104, 127, 194, 210), width=3,
    )
    draw.rounded_rectangle(
        (48, 48, CANVAS_W - 48, CANVAS_H - 48),
        radius=40, outline=(53, 70, 111, 190), width=2,
    )

    draw.rounded_rectangle(
        (88, 88, 1512, 94), radius=3, fill=(87, 132, 255, 190),
    )
    draw.ellipse((86, 78, 104, 96), fill=(71, 220, 204, 235))
    draw.ellipse((1496, 78, 1514, 96), fill=(190, 91, 255, 235))

    title_text = "BIKA CHARACTERS"
    title_font = _fit_text(draw, title_text, 980, 58, 36, bold=True)
    draw.text(
        (CANVAS_W // 2, 116), title_text, font=title_font,
        fill=(247, 250, 255), anchor="ma",
    )
    subtitle = "COLLECTOR PROFILE  •  COLLECTION NETWORK"
    subtitle_font = _fit_text(draw, subtitle, 1050, 23, 18, bold=True)
    draw.text(
        (CANVAS_W // 2, 176), subtitle, font=subtitle_font,
        fill=(135, 156, 198), anchor="ma",
    )

    avatar_size = 250
    avatar_x, avatar_y = 100, 250

    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.ellipse(
        (avatar_x - 18, avatar_y - 6, avatar_x + avatar_size + 28, avatar_y + avatar_size + 40),
        fill=(0, 0, 0, 155),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(24))
    img = Image.alpha_composite(img, shadow)
    draw = ImageDraw.Draw(img)

    draw.ellipse(
        (avatar_x - 17, avatar_y - 17, avatar_x + avatar_size + 17, avatar_y + avatar_size + 17),
        fill=(14, 23, 49, 255), outline=(104, 91, 255, 235), width=8,
    )
    draw.ellipse(
        (avatar_x - 7, avatar_y - 7, avatar_x + avatar_size + 7, avatar_y + avatar_size + 7),
        outline=(63, 211, 232, 235), width=4,
    )

    avatar = None
    if avatar_bytes:
        try:
            avatar = Image.open(BytesIO(avatar_bytes)).convert("RGB")
            avatar = ImageOps.fit(
                avatar, (avatar_size, avatar_size),
                method=Image.Resampling.LANCZOS,
            )
        except Exception:
            avatar = None

    mask = Image.new("L", (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=255)

    if avatar is not None:
        img.paste(avatar.convert("RGBA"), (avatar_x, avatar_y), mask)
    else:
        draw.ellipse(
            (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
            fill=(34, 48, 84, 255),
        )
        clusters = _graphemes(full_name.strip())
        first_cluster = clusters[0] if clusters else "?"
        if _is_emoji_cluster(first_cluster):
            emoji_img = _render_emoji_cluster(first_cluster, 118)
            if emoji_img is not None:
                px = avatar_x + avatar_size // 2 - emoji_img.width // 2
                py = avatar_y + avatar_size // 2 - emoji_img.height // 2
                img.paste(emoji_img, (px, py), emoji_img)
            else:
                draw.text(
                    (avatar_x + avatar_size // 2, avatar_y + avatar_size // 2),
                    first_cluster, font=_font(90, bold=True, text=first_cluster),
                    fill=(235, 241, 255), anchor="mm",
                )
        else:
            draw.text(
                (avatar_x + avatar_size // 2, avatar_y + avatar_size // 2),
                first_cluster.upper(),
                font=_font(98, bold=True, text=first_cluster),
                fill=(235, 241, 255), anchor="mm",
            )

    name_max = 840
    name_size = _fit_mixed_text_size(draw, full_name, name_max, 58, 28, bold=True)
    safe_name = _truncate_mixed_text(draw, full_name, name_max, name_size, bold=True)
    _draw_mixed_text(img, (405, 265), safe_name, size=name_size,
                     fill=(255, 255, 255), bold=True)

    rank_line = f"{collector_emoji}  {collector_rank}"
    rank_size = _fit_mixed_text_size(draw, rank_line, 840, 36, 24, bold=True)
    safe_rank = _truncate_mixed_text(draw, rank_line, 840, rank_size, bold=True)
    _draw_mixed_text(img, (407, 342), safe_rank, size=rank_size,
                     fill=(172, 193, 255), bold=True)

    identity_text = f"ID #{int(profile_id):,}   •   VERIFIED COLLECTOR"
    draw.text(
        (407, 401), identity_text, font=_font(23, text=identity_text),
        fill=(123, 143, 183),
    )

    badge_x1, badge_y1, badge_x2, badge_y2 = 1110, 250, 1470, 410
    draw.rounded_rectangle(
        (badge_x1, badge_y1, badge_x2, badge_y2),
        radius=32, fill=(25, 32, 62, 245),
        outline=(94, 108, 177, 210), width=2,
    )
    badge_label = "COLLECTOR LEVEL"
    draw.text(
        (badge_x1 + 28, badge_y1 + 24), badge_label,
        font=_font(20, bold=True, text=badge_label),
        fill=(119, 140, 185),
    )
    badge_size = _fit_mixed_text_size(draw, collector_emoji, 70, 44, 28, bold=True)
    _draw_mixed_text(
        img, (badge_x1 + 28, badge_y1 + 66), collector_emoji,
        size=badge_size, fill=(255, 255, 255), bold=True,
    )
    badge_rank = _truncate_mixed_text(draw, collector_rank, 265, 28, bold=True)
    draw.text(
        (badge_x1 + 96, badge_y1 + 75), badge_rank,
        font=_font(28, bold=True, text=badge_rank),
        fill=(230, 235, 255),
    )

    top = 475
    left = 100
    gap = 26
    card_w = 345
    card_h = 145
    stat_boxes = [
        ("TOTAL CARDS", f"{int(unique_cards):,}", (65, 218, 199)),
        ("GLOBAL RANK", f"#{max(0, int(global_rank)):,}", (104, 135, 255)),
        ("PROFILE ID", f"#{int(profile_id):,}", (190, 101, 255)),
        ("COLLECTOR", collector_rank, (246, 181, 76)),
    ]

    for i, (label, value, accent) in enumerate(stat_boxes):
        x1 = left + i * (card_w + gap)
        x2 = x1 + card_w
        draw.rounded_rectangle(
            (x1, top, x2, top + card_h),
            radius=28, fill=(18, 28, 54, 235),
            outline=(58, 77, 119, 190), width=2,
        )
        draw.rounded_rectangle(
            (x1, top, x1 + 9, top + card_h),
            radius=5, fill=accent,
        )
        draw.text(
            (x1 + 28, top + 22), label,
            font=_font(19, bold=True, text=label),
            fill=(133, 151, 190),
        )
        value_size = _fit_mixed_text_size(
            draw, value, card_w - 55, 38, 22, bold=True,
        )
        safe_value = _truncate_mixed_text(
            draw, value, card_w - 55, value_size, bold=True,
        )
        draw.text(
            (x1 + 28, top + 67), safe_value,
            font=_font(value_size, bold=True, text=safe_value),
            fill=(242, 246, 255),
        )

    progress_y = 675
    draw.rounded_rectangle(
        (100, progress_y, 1500, 825),
        radius=32, fill=(15, 24, 48, 238),
        outline=(58, 78, 120, 185), width=2,
    )

    if next_rank_target > 0 and next_rank_name:
        target = max(1, int(next_rank_target))
        progress = min(1.0, max(0.0, int(unique_cards) / target))
        next_label = f"NEXT LEVEL  •  {next_rank_name}"
        next_label = _truncate_mixed_text(draw, next_label, 700, 25, bold=True)
        draw.text(
            (135, progress_y + 27), next_label,
            font=_font(25, bold=True, text=next_label),
            fill=(222, 229, 248),
        )

        progress_value = f"{int(unique_cards):,} / {target:,}"
        draw.text(
            (1460, progress_y + 27), progress_value,
            font=_font(22, bold=True, text=progress_value),
            fill=(154, 173, 212), anchor="ra",
        )

        bar_x1, bar_y1, bar_x2, bar_y2 = 135, progress_y + 78, 1465, progress_y + 104
        draw.rounded_rectangle(
            (bar_x1, bar_y1, bar_x2, bar_y2),
            radius=13, fill=(35, 45, 76, 255),
        )
        fill_x2 = bar_x1 + max(18, int((bar_x2 - bar_x1) * progress))
        draw.rounded_rectangle(
            (bar_x1, bar_y1, min(bar_x2, fill_x2), bar_y2),
            radius=13, fill=(103, 122, 255, 255),
        )
    else:
        end_text = "✦  LEGENDARY COLLECTION STATUS  ✦"
        end_text = _truncate_mixed_text(draw, end_text, 1240, 30, bold=True)
        end_width = _text_width(draw, end_text, 30, True)
        _draw_mixed_text(
            img, (CANVAS_W // 2 - end_width // 2, progress_y + 50),
            end_text, size=30, fill=(238, 204, 104), bold=True,
        )

    footer = "BIKA  •  COLLECT • CLAIM • COLLECT AGAIN"
    footer = _truncate_mixed_text(draw, footer, 900, 18, bold=True)
    draw.text(
        (CANVAS_W // 2, 852), footer,
        font=_font(18, bold=True, text=footer),
        fill=(88, 105, 143), anchor="ma",
    )

    out = BytesIO()
    out.name = "bika_profile.jpg"
    img.convert("RGB").save(
        out, format="JPEG", quality=94, optimize=True, progressive=True,
    )
    out.seek(0)
    return out

