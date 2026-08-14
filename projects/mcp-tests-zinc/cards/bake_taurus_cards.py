"""Bake the two Taurus People title cards as 1920x1080 RGBA PNG runs.

The titles schema's Text+ route needs a GUI-authored Text+ template already in the
media pool (T5) and this project's pool holds none - InsertFusionTitleIntoTimeline
makes a timeline item whose GetMediaPoolItem() is None, so the template cannot be
created from the API. The PNG route is the schema's other first-class route
(titles schema section 6): a %04d frame run at the timeline's resolution and frame
rate with the fade ramps baked in, and its frame count must equal out - in exactly
(T11).

Design is copied off the client's own deliverable rather than invented:
'S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos/6-17 - Zinc Set 2 -
Taurus People.mp4'. Grabs in gauntlet/recon/super_frames/ - card_1s.jpg (the
full-frame card at t=1.0 s) and pers_full.jpg (the personnel super at t=23.0 s).
Colours are sampled from those frames; the layout fractions are measured off them.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
W, H = 1920, 1080

# Sampled off card_1s.png: the label gold and the warm white of the serif.
GOLD = (216, 188, 150)
CREAM = (255, 253, 245)

FONT_DIR = Path(r"C:\Windows\Fonts")
SERIF = FONT_DIR / "constan.ttf"  # highest-contrast serif on this box; the deliverable's
SERIF_B = FONT_DIR / "constanb.ttf"  # face is a Playfair-class didone, unavailable here
SANS = FONT_DIR / "corbel.ttf"

# --- title card -------------------------------------------------------------
CARD_FRAMES = 56  # cut file g001: 56 frames = 2.3357 s, clearing 1 frame before the entrance
CARD_FADE_IN = 1  # the deliverable's card is at full strength from its frame 1

# --- personnel super --------------------------------------------------------
# openings_survey.json, taurus_people.personnel_super: fade_in_start 20.646,
# full 20.896-26.652, fade_out_end 26.777 -> 6.131 s total = 147 frames.
PERS_FRAMES = 147
PERS_FADE = 7  # ~0.29 s each way; the survey reads the ramps at "about 0.3 s"

TITLE_TEXT = "Taurus People"
TITLE_LABEL = "LIVE AT ZINC BAR"
PERSONNEL_LABEL = "PERSONNEL"
PLAYERS = [
    ("SAXOPHONE", "Ryan Devlin"),
    ("PIANO", "Leo Genovese"),
    ("BASS", "Gene Perla"),
    ("DRUMS", "Willie Bowman"),
]


def tracked(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, tracking: float, anchor_mid=True):
    """Draw letterspaced text centred on xy[0]; returns the drawn width."""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = xy[0] - total / 2 if anchor_mid else xy[0]
    for ch, w in zip(text, widths, strict=False):
        draw.text((x, xy[1]), ch, font=font, fill=fill, anchor="ls")
        x += w + tracking
    return total


def rule(draw: ImageDraw.ImageDraw, x0: float, x1: float, y: float, fill, width: int = 2):
    draw.line([(x0, y), (x1, y)], fill=fill, width=width)


def title_card() -> Image.Image:
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    label_font = ImageFont.truetype(str(SANS), 26)
    title_font = ImageFont.truetype(str(SERIF), 168)

    label_y = int(0.418 * H)
    span = tracked(d, (W / 2, label_y), TITLE_LABEL, label_font, GOLD, tracking=9.0)
    dash = 46
    gap = 26
    rule(d, W / 2 - span / 2 - gap - dash, W / 2 - span / 2 - gap, label_y - 8, GOLD, 2)
    rule(d, W / 2 + span / 2 + gap, W / 2 + span / 2 + gap + dash, label_y - 8, GOLD, 2)

    d.text((W / 2, int(0.578 * H)), TITLE_TEXT, font=title_font, fill=CREAM, anchor="ms")
    return im


def personnel_card() -> Image.Image:
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    label_font = ImageFont.truetype(str(SANS), 19)
    # 39 px puts the widest name ("Willie Bowman") at 259 px against a 279 px column
    # pitch - the deliverable's own widest name measures 262 px in the same pitch, and
    # at 54 px the four names ran into each other in the first bake.
    name_font = ImageFont.truetype(str(SERIF), 39)

    head_y = int(0.824 * H)
    inst_y = int(0.862 * H)
    name_y = int(0.916 * H)

    span = tracked(d, (W / 2, head_y), PERSONNEL_LABEL, label_font, GOLD, tracking=8.0)
    dash, gap = 40, 22
    rule(d, W / 2 - span / 2 - gap - dash, W / 2 - span / 2 - gap, head_y - 6, GOLD, 2)
    rule(d, W / 2 + span / 2 + gap, W / 2 + span / 2 + gap + dash, head_y - 6, GOLD, 2)

    centres = [0.277, 0.422, 0.568, 0.713]
    for cx, (inst, name) in zip(centres, PLAYERS, strict=False):
        x = cx * W
        tracked(d, (x, inst_y), inst, label_font, GOLD, tracking=6.0)
        d.text((x, name_y), name, font=name_font, fill=CREAM, anchor="ms")
    for a, b in zip(centres, centres[1:], strict=False):
        x = (a + b) / 2 * W
        d.line([(x, inst_y - 22), (x, name_y + 4)], fill=(*GOLD, 90), width=2)
    return im


def bake(base: Image.Image, out_dir: Path, frames: int, fade_in: int, fade_out: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    alpha = base.getchannel("A")
    for i in range(frames):
        if fade_in and i < fade_in:
            k = (i + 1) / (fade_in + 1)
        elif fade_out and i >= frames - fade_out:
            k = (frames - i) / (fade_out + 1)
        else:
            k = 1.0
        frame = base.copy()
        frame.putalpha(alpha.point(lambda v, k=k: int(v * k)))
        frame.save(out_dir / f"{out_dir.name}_{i:04d}.png")
    print(f"baked {frames} frames -> {out_dir}")


def main() -> None:
    bake(title_card(), HERE / "taurus-people" / "title", CARD_FRAMES, CARD_FADE_IN, 0)
    # New directory name on the re-bake: apply_titles reuses a card already imported
    # into the bin, so re-baking under the old name would re-place the stale stills.
    bake(personnel_card(), HERE / "taurus-people" / "personnel2", PERS_FRAMES, PERS_FADE, PERS_FADE)


if __name__ == "__main__":
    main()
