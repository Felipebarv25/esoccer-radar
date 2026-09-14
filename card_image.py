"""Genera una IMAGEN (PNG) de tarjeta con diseño para las mejores oportunidades.

Se usa solo en picks fuertes (⭐⭐ ALTA / 💎 TOP) y se envía como foto a Telegram.
Requiere Pillow (pip install pillow). Si no está, run_esoccer sigue con la
tarjeta de texto normal (esto es un extra visual).
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

_CARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cards")

W, H = 1000, 560
_BG_TOP = (18, 32, 58)
_BG_BOT = (9, 18, 34)
_WHITE = (236, 241, 246)
_GREY = (150, 166, 182)
_GOLD = (255, 201, 60)
_GREEN = (57, 217, 138)
_SILVER = (176, 190, 205)
_CHIP = (30, 46, 74)

_FONT_PATHS = {
    True: ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    False: ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"],
}


def _font(size, bold=False):
    for p in _FONT_PATHS[bold]:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _bg(draw):
    for y in range(H):
        f = y / H
        c = tuple(int(_BG_TOP[i] + (_BG_BOT[i] - _BG_TOP[i]) * f) for i in range(3))
        draw.line([(0, y), (W, y)], fill=c)


def _center(draw, cx, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def _star(draw, cx, cy, r, fill):
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    draw.polygon(pts, fill=fill)


def _chip(draw, x, y, w, h, label, value, accent):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=16, fill=_CHIP)
    draw.rounded_rectangle([x, y, x + 8, y + h], radius=4, fill=accent)
    draw.text((x + 24, y + 16), label, font=_font(20), fill=_GREY)
    draw.text((x + 24, y + 42), value, font=_font(34, bold=True), fill=_WHITE)


def _trim(s, n=16):
    s = str(s or "?")
    return s if len(s) <= n else s[:n - 1] + "…"


def make_card(d: dict) -> str:
    """d: tipo, hora, pa, ta, pb, tb, favored, fav_score, elo_prob, team_wr,
    hour_tasa, level ('TOP'|'ALTA'|'MEDIA'), top_wr, top_g."""
    accent = _GOLD if d.get("level") in ("TOP", "ALTA") else _SILVER
    img = Image.new("RGB", (W, H), _BG_TOP)
    dr = ImageDraw.Draw(img)
    _bg(dr)

    # marco + barra de acento
    dr.rectangle([0, 0, 14, H], fill=accent)
    dr.rounded_rectangle([20, 20, W - 20, H - 20], radius=24, outline=(44, 62, 92), width=2)

    # badge de nivel (estrellas dibujadas + texto)
    if d.get("level") == "TOP":
        label, nstars = "OPORTUNIDAD TOP", 2
    elif d.get("level") == "ALTA":
        label, nstars = "ALTA CONFIANZA", 2
    else:
        label, nstars = "CONFIANZA MEDIA", 1
    bf = _font(26, bold=True)
    tw = dr.textlength(label, font=bf)
    star_w = nstars * 30
    bw = 26 + star_w + 14 + tw + 26
    dr.rounded_rectangle([44, 44, 44 + bw, 92], radius=24, fill=accent)
    sx = 44 + 26 + 14
    for _ in range(nstars):
        _star(dr, sx, 68, 12, (22, 30, 48))
        sx += 30
    dr.text((44 + 26 + star_w + 14, 54), label, font=bf, fill=(22, 30, 48))

    # tipo + hora (derecha)
    top_right = f"eSoccer {d.get('tipo', '')}  ·  {d.get('hora', '')} COL"
    tw = dr.textlength(top_right, font=_font(22))
    dr.text((W - 60 - tw, 56), top_right, font=_font(22), fill=_GREY)

    # matchup
    fav = _trim(d.get("favored"))
    opp_is_a = d.get("favored") == d.get("pa")
    opp = _trim(d.get("pb") if opp_is_a else d.get("pa"))
    fav_team = _trim(d.get("ta") if opp_is_a else d.get("tb"), 18)
    opp_team = _trim(d.get("tb") if opp_is_a else d.get("ta"), 18)

    dr.text((60, 150), "FAVORITO", font=_font(20, bold=True), fill=accent)
    dr.text((60, 176), fav, font=_font(58, bold=True), fill=_WHITE)
    dr.text((62, 250), f"con {fav_team}", font=_font(24), fill=_GREY)

    dr.text((60, 300), "vs", font=_font(22), fill=_GREY)
    dr.text((100, 296), f"{opp}  ({opp_team})", font=_font(30, bold=True), fill=_SILVER)

    # chips de stats
    y = 360
    cw, ch, gap = 216, 110, 18
    x = 60
    _chip(dr, x, y, cw, ch, "SCORE", f"{d.get('fav_score', '?')}/100", accent)
    x += cw + gap
    _chip(dr, x, y, cw, ch, "PROB. ELO", f"{d.get('elo_prob', '?')}%", _GREEN)
    x += cw + gap
    tw2 = d.get("team_wr")
    _chip(dr, x, y, cw, ch, "CON SU EQUIPO",
          (f"{tw2}%" if tw2 is not None else "s/d"), _GOLD)
    x += cw + gap
    hr = d.get("hour_tasa")
    _chip(dr, x, y, cw, ch, "FRANJA HORA",
          (f"{hr}%" if hr is not None else "s/d"),
          _GREEN if (hr or 0) >= 54 else (_SILVER if (hr or 0) >= 49 else (230, 90, 90)))

    # footer honesto
    foot = ("Fuerza relativa + datos, NO probabilidad ni garantia. "
            "Apuesta solo lo que puedas perder.")
    dr.text((60, 500), foot, font=_font(18), fill=_GREY)

    os.makedirs(_CARD_DIR, exist_ok=True)
    path = os.path.join(_CARD_DIR, "card.png")
    img.save(path)
    return path
