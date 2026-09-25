#!/usr/bin/env python3
"""Builds every MorvaneOS brand asset from one definition of the logo.

    python tools/build.py

Writes svg/, png/ and ascii/. Needs Python packages fonttools, uharfbuzz and
pillow, plus rsvg-convert (librsvg) for the PNGs. Fonts (Cinzel, JetBrains
Mono; both SIL OFL) are downloaded to tools/.cache on first run. Text is
converted to outlines, so the SVGs don't depend on any installed font.
"""

import io
import math
import random
import shutil
import subprocess
import urllib.request
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'tools' / '.cache'

# ---------------------------------------------------------------- palette

PASTEL = '#F4A6C6'  # primary: the mark on dark backgrounds
LAVENDER = '#B9A3E6'  # moon and stars, secondary accents
ROSE = '#8E4570'  # the mark and accents on light backgrounds
NIGHT = '#120C16'  # backgrounds, text on light
PAPER = '#F5EEF3'  # light backgrounds

# ---------------------------------------------------------------- the mark (viewBox 0 0 100 100)

PEAKS = 'M8 86 L32 30 L50 62 L68 30 L92 86 Z'
MOON_OUTER = (50.0, 29.0, 9.5)  # the moon's disc
MOON_CUT = (55.0, 25.0, 8.0)  # the disc cut away to leave the crescent
STARS = [(22.0, 18.0, 1.8), (80.0, 14.0, 1.4)]


def crescent_path(outer: tuple[float, float, float] = MOON_OUTER, cut: tuple[float, float, float] = MOON_CUT) -> str:
	"""Crescent as a plain path (no SVG mask), so every renderer can draw it, Qt's included."""
	(x1, y1, r1), (x2, y2, r2) = outer, cut
	d = math.hypot(x2 - x1, y2 - y1)
	a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
	h = math.sqrt(r1 * r1 - a * a)
	mx, my = x1 + a * (x2 - x1) / d, y1 + a * (y2 - y1) / d
	ox, oy = -h * (y2 - y1) / d, h * (x2 - x1) / d
	p, q = (mx + ox, my + oy), (mx - ox, my - oy)

	def flags(cx, cy, r, start, end, keep):
		# SVG sweep=1 walks increasing angle. Pick the direction whose midpoint satisfies keep().
		a0 = math.atan2(start[1] - cy, start[0] - cx)
		a1 = math.atan2(end[1] - cy, end[0] - cx)
		span = (a1 - a0) % (2 * math.pi)
		mid = a0 + span / 2
		point = (cx + r * math.cos(mid), cy + r * math.sin(mid))
		if keep(point):
			return int(span > math.pi), 1
		return int(2 * math.pi - span > math.pi), 0

	outside_cut = lambda pt: math.hypot(pt[0] - x2, pt[1] - y2) > r2
	inside_disc = lambda pt: math.hypot(pt[0] - x1, pt[1] - y1) < r1
	l1, s1 = flags(x1, y1, r1, p, q, outside_cut)
	l2, s2 = flags(x2, y2, r2, q, p, inside_disc)
	f = lambda v: f'{v:.3f}'.rstrip('0').rstrip('.')
	return (
		f'M{f(p[0])} {f(p[1])} A{f(r1)} {f(r1)} 0 {l1} {s1} {f(q[0])} {f(q[1])} '
		f'A{f(r2)} {f(r2)} 0 {l2} {s2} {f(p[0])} {f(p[1])} Z'
	)


CRESCENT = crescent_path()

# Colour schemes for the mark: (peaks, moon and stars)
SCHEMES = {
	'pastel': (PASTEL, LAVENDER),  # on dark backgrounds
	'rose': (ROSE, ROSE),  # on light backgrounds
	'night': (NIGHT, NIGHT),  # one colour, dark
	'white': ('#FFFFFF', '#FFFFFF'),  # one colour, light
}


def mark_elements(scheme: str, stars: bool = True, moon: bool = True) -> str:
	peaks, sky = SCHEMES[scheme]
	parts = []
	if moon:
		parts.append(f'<path fill="{sky}" d="{CRESCENT}"/>')
	parts.append(f'<path fill="{peaks}" d="{PEAKS}"/>')
	if stars:
		parts += [f'<circle cx="{x:g}" cy="{y:g}" r="{r:g}" fill="{sky}"/>' for x, y, r in STARS]
	return ''.join(parts)


# ---------------------------------------------------------------- fonts and text as outlines

FONT_URLS = {
	'Cinzel': 'https://github.com/google/fonts/raw/main/ofl/cinzel/Cinzel%5Bwght%5D.ttf',
	'JetBrainsMono': 'https://github.com/google/fonts/raw/main/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf',
}
_fonts: dict[tuple[str, int], tuple[TTFont, hb.Font]] = {}


def font(family: str, weight: int) -> tuple[TTFont, hb.Font]:
	key = (family, weight)
	if key not in _fonts:
		CACHE.mkdir(parents=True, exist_ok=True)
		vf = CACHE / f'{family}-VF.ttf'
		if not vf.exists():
			urllib.request.urlretrieve(FONT_URLS[family], vf)
		static = instantiateVariableFont(TTFont(vf), {'wght': weight})
		buf = io.BytesIO()
		static.save(buf)
		data = buf.getvalue()
		_fonts[key] = (TTFont(io.BytesIO(data)), hb.Font(hb.Face(data)))
	return _fonts[key]


class Text:
	"""A run of text as one SVG path, baseline at y=0, starting at x=0."""

	def __init__(self, family: str, weight: int, text: str, size: float, tracking_em: float = 0.0):
		tt, hbfont = font(family, weight)
		upem = tt['head'].unitsPerEm
		buf = hb.Buffer()
		buf.add_str(text)
		buf.guess_segment_properties()
		hb.shape(hbfont, buf, {'kern': True, 'liga': False})

		glyphs = tt.getGlyphSet()
		order = tt.getGlyphOrder()
		scale = size / upem
		pen = SVGPathPen(glyphs, ntos=lambda v: f'{v:.2f}'.rstrip('0').rstrip('.'))
		bounds = BoundsPen(glyphs)
		x = 0.0
		infos, positions = buf.glyph_infos, buf.glyph_positions
		for i, (info, pos) in enumerate(zip(infos, positions)):
			name = order[info.codepoint]
			transform = (scale, 0, 0, -scale, x + pos.x_offset * scale, -pos.y_offset * scale)
			glyphs[name].draw(TransformPen(pen, transform))
			glyphs[name].draw(TransformPen(bounds, transform))
			x += pos.x_advance * scale
			if i < len(infos) - 1:
				x += tracking_em * size
		self.d = pen.getCommands()
		self.x0, self.y0, self.x1, self.y1 = bounds.bounds  # ink box, y down (top is y0)

	@property
	def width(self) -> float:
		return self.x1 - self.x0

	@property
	def height(self) -> float:
		return self.y1 - self.y0

	def at(self, left: float, top: float, fill: str) -> str:
		"""Place so the ink box's top-left corner lands on (left, top)."""
		return f'<path fill="{fill}" transform="translate({left - self.x0:.2f} {top - self.y0:.2f})" d="{self.d}"/>'


# ---------------------------------------------------------------- SVG documents

# Ink box of the full mark inside its 100-unit viewBox: moon top to peak base
MARK_TOP, MARK_BOTTOM, MARK_LEFT, MARK_RIGHT = 12.6, 86.0, 8.0, 92.0


def svg(width: float, height: float, body: str, title: str) -> str:
	return (
		f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.2f} {height:.2f}" '
		f'width="{width:.0f}" height="{height:.0f}" role="img" aria-label="{title}">'
		f'<title>{title}</title>{body}</svg>\n'
	)


def placed_mark(scheme: str, x: float, y: float, size: float, stars: bool = True) -> str:
	return f'<g transform="translate({x:.2f} {y:.2f}) scale({size / 100:.4f})">{mark_elements(scheme, stars)}</g>'


def lockup_horizontal(theme: str) -> str:
	name_color, sub_color, scheme = (NIGHT, ROSE, 'rose') if theme == 'light' else (PASTEL, LAVENDER, 'pastel')
	mark = 150
	name = Text('Cinzel', 700, 'Morvane', 88, 0.06)
	sub = Text('JetBrainsMono', 400, 'LINUX', 30, 0.62)
	gap_x, gap_y = 36, 16
	block_h = name.height + gap_y + sub.height
	mark_ink = (MARK_BOTTOM - MARK_TOP) * mark / 100
	height = max(mark_ink, block_h)
	mark_y = (height - mark_ink) / 2 - MARK_TOP * mark / 100
	text_x = MARK_RIGHT * mark / 100 + gap_x
	text_y = (height - block_h) / 2
	width = text_x + max(name.width, sub.width + 6)
	left = -MARK_LEFT * mark / 100
	body = (
		placed_mark(scheme, left, mark_y, mark)
		+ name.at(text_x + left, text_y, name_color)
		+ sub.at(text_x + left + 6, text_y + name.height + gap_y, sub_color)
	)
	return svg(width + left, height, body, 'MorvaneOS Linux')


def lockup_stacked(theme: str) -> str:
	name_color, sub_color, scheme = (NIGHT, ROSE, 'rose') if theme == 'light' else (PASTEL, LAVENDER, 'pastel')
	mark = 380
	name = Text('Cinzel', 700, 'MORVANE', 104, 0.08)
	sub = Text('JetBrainsMono', 400, 'LINUX', 34, 0.9)
	width = max(name.width, sub.width, (MARK_RIGHT - MARK_LEFT) * mark / 100)
	mark_ink = (MARK_BOTTOM - MARK_TOP) * mark / 100
	mark_x = (width - (MARK_RIGHT - MARK_LEFT) * mark / 100) / 2 - MARK_LEFT * mark / 100
	name_y = mark_ink + 52
	sub_y = name_y + name.height + 30
	body = (
		placed_mark(scheme, mark_x, -MARK_TOP * mark / 100, mark)
		+ name.at((width - name.width) / 2, name_y, name_color)
		+ sub.at((width - sub.width) / 2, sub_y, sub_color)
	)
	return svg(width, sub_y + sub.height, body, 'MorvaneOS Linux')


def lockup_oneline(theme: str) -> str:
	if theme == 'light':
		name_color, linux_color, rule_color, scheme = NIGHT, ROSE, '#D9C4D2', 'rose'
	else:
		name_color, linux_color, rule_color, scheme = PASTEL, LAVENDER, '#3A2B3F', 'pastel'
	mark = 116
	name = Text('Cinzel', 700, 'Morvane', 72, 0.06)
	linux = Text('Cinzel', 500, 'Linux', 72, 0.06)
	gap = 32
	mark_ink = (MARK_BOTTOM - MARK_TOP) * mark / 100
	height = max(mark_ink, name.height, linux.height)
	x = (MARK_RIGHT - MARK_LEFT) * mark / 100 + gap
	body = placed_mark(scheme, -MARK_LEFT * mark / 100, (height - mark_ink) / 2 - MARK_TOP * mark / 100, mark)
	body += name.at(x, (height - name.height) / 2, name_color)
	x += name.width + gap
	body += f'<rect x="{x:.2f}" y="{height * 0.1:.2f}" width="4" height="{height * 0.8:.2f}" fill="{rule_color}"/>'
	x += 4 + gap
	body += linux.at(x, (height - linux.height) / 2, linux_color)
	return svg(x + linux.width, height, body, 'MorvaneOS Linux')


def app_icon(theme: str) -> str:
	bg, scheme = (PASTEL, 'night') if theme == 'light' else (NIGHT, 'pastel')
	size, inner = 1024, 680
	offset = (size - inner) / 2
	body = f'<rect width="{size}" height="{size}" rx="{size * 0.23:.0f}" fill="{bg}"/>'
	body += placed_mark(scheme, offset, offset, inner)
	return svg(size, size, body, 'MorvaneOS')


# ---------------------------------------------------------------- wallpapers

# Designed at 3840 wide; the height sets the aspect ratio. Everything is laid
# out in units of the height, so 16:10 gets the same scene a little taller.
WALLPAPER_SIZES = {
	'16x9': [(3840, 2160), (2560, 1440), (1920, 1080)],
	'16x10': [(3840, 2400), (2560, 1600), (1920, 1200)],
}
WALLPAPER_WIDTH = 3840


def _ridge(rng: random.Random, width: float, base: float, amplitude: float, roughness: float = 0.55) -> list[tuple[float, float]]:
	"""A mountain skyline across the width (midpoint displacement)."""
	n = 129
	ys = [0.0] * n
	ys[0], ys[-1] = rng.uniform(-1, 1), rng.uniform(-1, 1)
	step, scale = n - 1, 1.0
	while step > 1:
		half = step // 2
		for i in range(half, n - 1, step):
			ys[i] = (ys[i - half] + ys[i + half]) / 2 + rng.uniform(-1, 1) * scale
		step, scale = half, scale * roughness
	return [(width * i / (n - 1), base + amplitude * y) for i, y in enumerate(ys)]


def _fill_below(points: list[tuple[float, float]], bottom: float, colour: str) -> str:
	pts = ' '.join(f'{x:.1f},{y:.1f}' for x, y in points)
	return f'<polygon fill="{colour}" points="0,{bottom:.1f} {pts} {points[-1][0]:.1f},{bottom:.1f}"/>'


def _sky(w: float, h: float) -> str:
	return (
		'<defs>'
		'<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
		'<stop offset="0" stop-color="#0B070E"/><stop offset="0.45" stop-color="#140D19"/>'
		'<stop offset="0.7" stop-color="#2E1A34"/><stop offset="1" stop-color="#4E2A48"/>'
		'</linearGradient>'
		f'<radialGradient id="glow" cx="0.5" cy="0.62" r="0.5"><stop offset="0" stop-color="{PASTEL}" stop-opacity="0.22"/>'
		f'<stop offset="1" stop-color="{PASTEL}" stop-opacity="0"/></radialGradient>'
		f'<radialGradient id="moonglow"><stop offset="0" stop-color="{LAVENDER}" stop-opacity="0.18"/>'
		f'<stop offset="1" stop-color="{LAVENDER}" stop-opacity="0"/></radialGradient>'
		'<linearGradient id="floor" x1="0" y1="0" x2="0" y2="1">'
		f'<stop offset="0" stop-color="{NIGHT}" stop-opacity="0"/><stop offset="1" stop-color="{NIGHT}"/>'
		'</linearGradient>'
		'</defs>'
		f'<rect width="{w}" height="{h}" fill="url(#sky)"/>'
	)


def _stars(rng: random.Random, w: float, h: float, avoid: tuple[float, float, float]) -> str:
	ax, ay, ar = avoid
	parts = []
	for _ in range(220):
		x, y = rng.uniform(0, w), rng.uniform(0, h * 0.62) ** 1.08 * (h * 0.62) ** -0.08
		if math.hypot(x - ax, y - ay) < ar:
			continue
		r = rng.choice((1.2, 1.6, 2.0, 2.4, 3.2))
		colour = rng.choice((LAVENDER, LAVENDER, PAPER))
		parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{colour}" opacity="{rng.uniform(0.25, 0.9):.2f}"/>')
	# A few four-point sparkles, like the logo's stars grown up
	for _ in range(6):
		x, y, s = rng.uniform(w * 0.05, w * 0.95), rng.uniform(h * 0.05, h * 0.45), rng.uniform(10, 18)
		if math.hypot(x - ax, y - ay) < ar * 1.3:
			continue
		d = f'M{x:.1f} {y - s:.1f} L{x + s * 0.22:.1f} {y - s * 0.22:.1f} L{x + s:.1f} {y:.1f} L{x + s * 0.22:.1f} {y + s * 0.22:.1f} L{x:.1f} {y + s:.1f} L{x - s * 0.22:.1f} {y + s * 0.22:.1f} L{x - s:.1f} {y:.1f} L{x - s * 0.22:.1f} {y - s * 0.22:.1f} Z'
		parts.append(f'<path fill="{LAVENDER}" opacity="0.8" d="{d}"/>')
	return ''.join(parts)


def wallpaper_twilight(aspect: str) -> str:
	"""The logo as a landscape: twin peaks under the crescent moon, at twilight."""
	w = WALLPAPER_WIDTH
	h = WALLPAPER_SIZES[aspect][0][1]
	cx = w / 2
	rng = random.Random(20260925)

	# The moon: the logo's crescent, scaled up, above the valley between the peaks
	r = h * 0.055
	mx, my = cx + h * 0.01, h * 0.24
	(ox, oy, orr), (kx, ky, kr) = MOON_OUTER, MOON_CUT
	moon = crescent_path((mx, my, r), (mx + (kx - ox) / orr * r, my + (ky - oy) / orr * r, kr / orr * r))

	# The peaks: the logo's M, scaled so its base sits at 90% of the height, and
	# lit by the moon: the slopes facing it light, the outer slopes in shade,
	# moonlight catching the ridges of the valley between them
	k = h * 0.0085
	base = h * 0.9

	def at(x: float, y: float) -> tuple[float, float]:
		return cx + (x - 50) * k, base - (86 - y) * k

	def poly(colour: str, *pts: tuple[float, float]) -> str:
		return f'<polygon fill="{colour}" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in (at(*p) for p in pts))}"/>'

	faces = (
		poly('#5E2C4E', (8, 86), (32, 30), (29, 86))  # left peak, outer (shade)
		+ poly(ROSE, (32, 30), (50, 62), (50, 86), (29, 86))  # left peak, facing the moon
		+ poly(ROSE, (50, 62), (68, 30), (71, 86), (50, 86))  # right peak, facing the moon
		+ poly('#5E2C4E', (68, 30), (92, 86), (71, 86))  # right peak, outer (shade)
	)
	moonlit = ' '.join(f'{x:.1f},{y:.1f}' for x, y in (at(32, 30), at(50, 62), at(68, 30)))

	body = _sky(w, h)
	body += f'<rect width="{w}" height="{h}" fill="url(#glow)"/>'
	body += _stars(rng, w, h, (mx, my, r * 3))
	body += f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="{r * 3.2:.1f}" fill="url(#moonglow)"/>'
	body += f'<path fill="{LAVENDER}" d="{moon}"/>'
	body += _fill_below(_ridge(rng, w, h * 0.64, h * 0.11, 0.62), h, '#3A2140')  # far range, in the haze
	body += _fill_below(_ridge(rng, w, h * 0.77, h * 0.08, 0.6), h, '#4E2646')  # side ranges
	body += faces
	body += f'<polyline fill="none" stroke="{PASTEL}" stroke-width="{h * 0.0035:.1f}" stroke-linejoin="miter" stroke-linecap="round" opacity="0.9" points="{moonlit}"/>'
	body += _fill_below(_ridge(rng, w, h * 0.88, h * 0.045, 0.6), h, '#1C1022')  # foothills
	body += f'<rect y="{h * 0.8:.1f}" width="{w}" height="{h * 0.2:.1f}" fill="url(#floor)"/>'
	return svg(w, h, body, 'MorvaneOS wallpaper: Twilight Peaks')


def wallpaper_minimal(aspect: str) -> str:
	"""Night, a soft glow and the mark: quiet enough for a lock screen."""
	w = WALLPAPER_WIDTH
	h = WALLPAPER_SIZES[aspect][0][1]
	size = h * 0.16
	body = (
		'<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
		f'<stop offset="0" stop-color="#0B070E"/><stop offset="1" stop-color="#1A1020"/></linearGradient>'
		f'<radialGradient id="halo"><stop offset="0" stop-color="{PASTEL}" stop-opacity="0.12"/>'
		f'<stop offset="1" stop-color="{PASTEL}" stop-opacity="0"/></radialGradient></defs>'
		f'<rect width="{w}" height="{h}" fill="url(#bg)"/>'
		f'<circle cx="{w / 2}" cy="{h / 2}" r="{size * 1.6:.1f}" fill="url(#halo)"/>'
	)
	body += placed_mark('pastel', w / 2 - size / 2, h / 2 - size / 2, size)
	return svg(w, h, body, 'MorvaneOS wallpaper: Minimal')


# ---------------------------------------------------------------- output


def write(path: Path, text: str) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(text)


# Output layout: svg/<asset>/<variant>.svg and png/<asset>/<variant>/<size>.png
Asset = tuple[str, str]  # (asset, variant), e.g. ('lockup-oneline', 'dark')


def build_svgs() -> dict[Asset, Path]:
	out = ROOT / 'svg'
	shutil.rmtree(out, ignore_errors=True)
	files: dict[Asset, Path] = {}

	def add(asset: str, variant: str, text: str) -> None:
		files[asset, variant] = out / asset / f'{variant}.svg'
		write(files[asset, variant], text)

	for scheme in SCHEMES:
		add('mark', scheme, svg(100, 100, mark_elements(scheme), 'MorvaneOS'))
		# Tiny sizes: moon and stars turn to specks, so just the peaks
		add('favicon', scheme, svg(100, 100, mark_elements(scheme, stars=False, moon=False), 'MorvaneOS'))
	for theme in ('light', 'dark'):
		add('lockup-horizontal', theme, lockup_horizontal(theme))
		add('lockup-stacked', theme, lockup_stacked(theme))
		add('lockup-oneline', theme, lockup_oneline(theme))
		add('app-icon', theme, app_icon(theme))
	for aspect in WALLPAPER_SIZES:
		add('wallpaper', f'twilight-{aspect}', wallpaper_twilight(aspect))
		add('wallpaper', f'minimal-{aspect}', wallpaper_minimal(aspect))
	return files


# Pixel sizes per asset: widths for most, full WxH for wallpapers
PNG_SIZES = {
	'mark': (64, 128, 256, 512, 1024),
	'app-icon': (64, 128, 256, 512, 1024),
	'favicon': (16, 32, 48),
	'lockup-horizontal': (800, 1600),
	'lockup-stacked': (800, 1600),
	'lockup-oneline': (800, 1600),
}


def build_pngs(svgs: dict[Asset, Path]) -> None:
	out = ROOT / 'png'
	shutil.rmtree(out, ignore_errors=True)

	def render(src: Path, dest: Path, width: int, height: int | None = None) -> None:
		dest.parent.mkdir(parents=True, exist_ok=True)
		size = ['-w', str(width)] + (['-h', str(height)] if height else [])
		subprocess.run(['rsvg-convert', *size, '-o', str(dest), str(src)], check=True)

	for (asset, variant), src in svgs.items():
		if asset == 'wallpaper':
			name, aspect = variant.rsplit('-', 1)
			for w, h in WALLPAPER_SIZES[aspect]:
				render(src, out / asset / name / f'{w}x{h}.png', w, h)
		else:
			for px in PNG_SIZES[asset]:
				render(src, out / asset / variant / f'{px}.png', px)


def build_ascii(cols: int = 44) -> None:
	"""The mark as terminal art, two pixels per character cell with half blocks."""
	out = ROOT / 'ascii'
	shutil.rmtree(out, ignore_errors=True)
	out.mkdir(parents=True)

	# Crop to the mark's ink so no blank rows or columns are wasted
	w_units, h_units = MARK_RIGHT - MARK_LEFT, MARK_BOTTOM - 10.0
	rows = round(cols * h_units / w_units / 2)
	doc = (
		f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{MARK_LEFT} 10 {w_units} {h_units}" '
		f'width="{cols}" height="{rows * 2}">{mark_elements("pastel")}</svg>'
	)
	png = subprocess.run(
		['rsvg-convert', '-w', str(cols), '-h', str(rows * 2)], input=doc.encode(), capture_output=True, check=True
	).stdout
	img = Image.open(io.BytesIO(png)).convert('RGBA')

	palette = {1: tuple(int(PASTEL[i : i + 2], 16) for i in (1, 3, 5)), 2: tuple(int(LAVENDER[i : i + 2], 16) for i in (1, 3, 5))}

	def classify(x: int, y: int) -> int:
		r, g, b, a = img.getpixel((x, y))
		if a < 110:
			return 0
		return min(palette, key=lambda k: sum((c - p) ** 2 for c, p in zip((r, g, b), palette[k])))

	cells = []
	for row in range(rows):
		line = []
		for x in range(cols):
			top, bottom = classify(x, row * 2), classify(x, row * 2 + 1)
			if top and bottom:
				line.append(('█', top))
			elif top:
				line.append(('▀', top))
			elif bottom:
				line.append(('▄', bottom))
			else:
				line.append((' ', 0))
		cells.append(line)

	while cells and all(ch == ' ' for ch, _ in cells[0]):
		cells.pop(0)
	while cells and all(ch == ' ' for ch, _ in cells[-1]):
		cells.pop()

	def render(style: str) -> str:
		lines = []
		for line in cells:
			text, current = '', 0
			for ch, colour in line:
				if colour and colour != current:
					if style == 'fastfetch':
						text += f'${colour}'
					elif style == 'ansi':
						text += '\x1b[38;2;{};{};{}m'.format(*palette[colour])
					current = colour
				text += ch
			if style == 'ansi':
				text += '\x1b[0m'
			lines.append(text.rstrip() if style == 'plain' else text)
		return '\n'.join(lines) + '\n'

	write(out / 'morvane.txt', render('fastfetch'))  # $1 = pastel pink, $2 = lavender
	write(out / 'morvane-plain.txt', render('plain'))
	write(out / 'morvane.ans', render('ansi'))  # `cat` it in a truecolour terminal


def build_swatches() -> None:
	"""Small colour chips for the README's palette table."""
	out = ROOT / 'png' / 'swatches'
	out.mkdir(parents=True, exist_ok=True)
	for name, colour in (('pastel', PASTEL), ('lavender', LAVENDER), ('rose', ROSE), ('night', NIGHT), ('paper', PAPER)):
		Image.new('RGB', (48, 48), colour).save(out / f'{name}.png')


if __name__ == '__main__':
	svgs = build_svgs()
	build_pngs(svgs)
	build_swatches()
	build_ascii()
	print(f'Built {len(svgs)} SVGs, their PNGs and the ASCII art in {ROOT}')
