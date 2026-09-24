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


def _crescent_path() -> str:
	"""Crescent as a plain path (no SVG mask), so every renderer can draw it, Qt's included."""
	(x1, y1, r1), (x2, y2, r2) = MOON_OUTER, MOON_CUT
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


CRESCENT = _crescent_path()

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


# ---------------------------------------------------------------- output


def write(path: Path, text: str) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(text)


def build_svgs() -> dict[str, Path]:
	out = ROOT / 'svg'
	shutil.rmtree(out, ignore_errors=True)
	files: dict[str, Path] = {}

	def add(name: str, text: str) -> None:
		files[name] = out / f'{name}.svg'
		write(files[name], text)

	for scheme in SCHEMES:
		add(f'mark-{scheme}', svg(100, 100, mark_elements(scheme), 'MorvaneOS'))
		# Tiny sizes: moon and stars turn to specks, so just the peaks
		add(f'favicon-{scheme}', svg(100, 100, mark_elements(scheme, stars=False, moon=False), 'MorvaneOS'))
	for theme in ('light', 'dark'):
		add(f'lockup-horizontal-{theme}', lockup_horizontal(theme))
		add(f'lockup-stacked-{theme}', lockup_stacked(theme))
		add(f'lockup-oneline-{theme}', lockup_oneline(theme))
		add(f'app-icon-{theme}', app_icon(theme))
	return files


def build_pngs(svgs: dict[str, Path]) -> None:
	out = ROOT / 'png'
	shutil.rmtree(out, ignore_errors=True)
	out.mkdir(parents=True)

	def render(src: Path, dest: Path, width: int) -> None:
		subprocess.run(['rsvg-convert', '-w', str(width), '-o', str(dest), str(src)], check=True)

	for name, src in svgs.items():
		if name.startswith(('mark-', 'app-icon-')):
			for px in (64, 128, 256, 512, 1024):
				render(src, out / f'{name}-{px}.png', px)
		elif name.startswith('favicon-'):
			for px in (16, 32, 48):
				render(src, out / f'{name}-{px}.png', px)
		else:  # lockups: 1x and 2x
			render(src, out / f'{name}.png', 800)
			render(src, out / f'{name}@2x.png', 1600)


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
	print(f'Built {len(svgs)} SVGs, PNGs and ASCII art in {ROOT}')
