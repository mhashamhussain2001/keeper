"""Generate Keeper icons from the bundled Figtree 700 glyph (fontTools, Brotli, Pillow, Playwright)."""

from pathlib import Path
from base64 import b64encode

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def main() -> None:
    """Return None after generating standalone SVG, PNG and ICO assets and a visual check."""
    # No full Figtree TTF/OTF is bundled or installed; this subset includes lowercase k.
    font = TTFont(STATIC / "fonts" / "font-3.woff2")
    glyphs = font.getGlyphSet()
    glyph = glyphs[font.getBestCmap()[ord("k")]]
    bounds = BoundsPen(glyphs)
    glyph.draw(bounds)
    x0, y0, x1, y1 = bounds.bounds
    scale = 64 / max(x1 - x0, y1 - y0)
    tx = 50 - (x0 + x1) * scale / 2
    ty = 50 + (y0 + y1) * scale / 2
    pen = SVGPathPen(glyphs)
    glyph.draw(pen)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           f'<rect width="100" height="100" rx="22" fill="#0E0F11"/>'
           f'<path fill="#1FB7F2" transform="translate({tx:g} {ty:g}) scale({scale:g} {-scale:g})" '
           f'd="{pen.getCommands()}"/></svg>\n')
    (STATIC / "favicon.svg").write_text(svg, encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(device_scale_factor=1)
        for size, name in [(32, "favicon-32.png"), (192, "favicon-192.png"),
                           (512, "favicon-512.png"), (180, "apple-touch-icon.png")]:
            page.set_viewport_size({"width": size, "height": size})
            background = "#0E0F11" if name == "apple-touch-icon.png" else "transparent"
            page.set_content(f'<style>body{{margin:0;background:{background}}}svg{{display:block;width:100vw;height:100vh}}</style>{svg}')
            page.screenshot(path=str(STATIC / name), omit_background=True)
        Image.open(STATIC / "favicon-512.png").save(STATIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
        page.close()
        page = browser.new_page(viewport={"width": 420, "height": 160}, device_scale_factor=2)
        page.goto((STATIC / "favicon.svg").as_uri())
        page.goto((STATIC / "favicon-32.png").as_uri())
        page.close()
        page = browser.new_page(viewport={"width": 420, "height": 160}, device_scale_factor=2)
        page.set_content('<body style="background:#eee;font:14px Arial;display:flex;gap:30px;align-items:center">'
                         + ''.join(f'<div>{size}px<br><img width="{size}" height="{size}" src="data:image/{"svg+xml" if name.endswith("svg") else "png"};base64,{b64encode((STATIC / name).read_bytes()).decode()}"></div>'
                                   for size, name in [(96, "favicon.svg"), (32, "favicon-32.png"), (16, "favicon-32.png")]))
        page.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)")
        output = ROOT / "private" / "design" / "favicon_check.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output))
        browser.close()


if __name__ == "__main__":
    main()
