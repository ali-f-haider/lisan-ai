"""Which design of the public home page is served: "classic" (the original) or "studio" (the serif, square-cornered look).

landing.html holds both designs. The text, flags and sections are shared; each design has its own stylesheet, and only one is
switched on. The admin page stores the choice (Home Page Design); the server calls apply_look() on every request to flip the
page to "studio". Anything unexpected (an unknown value, a landing.html without the markers) gives the classic page, so a
wrong setting or an edit to landing.html can never leave the home page broken or blank.
"""
import re

LOOKS = ("classic", "studio")
DEFAULT_LOOK = "classic"

_STYLE_SWAPS = (
    ('<style id="look-classic">', '<style id="look-classic" media="not all">'),
    ('<style id="look-studio" media="not all">', '<style id="look-studio">'),
)


def clean_look(value):
    """A known design name, or "" when the value is missing or unknown."""
    value = str(value or "").strip().lower()
    return value if value in LOOKS else ""


def choose_look(setting=None, requested=None):
    """The design to serve. ?look= in the address (used to preview a design) wins over the saved setting."""
    return clean_look(requested) or clean_look(setting) or DEFAULT_LOOK


def _take_data_attr(html, tag_start, attr, data_attr):
    """In the first tag that starts with tag_start, put the value of data_attr into attr. Cosmetic only: a miss changes nothing."""
    m = re.search(re.escape(tag_start) + r"[^>]*>", html)
    if not m:
        return html
    tag = m.group(0)
    d = re.search(r'\s' + re.escape(data_attr) + r'="([^"]*)"', tag)
    a = re.search(r'(\s' + re.escape(attr) + r'=")[^"]*"', tag)
    if not d or not a:
        return html
    new_tag = tag[:a.start()] + a.group(1) + d.group(1) + '"' + tag[a.end():]
    return html[:m.start()] + new_tag + html[m.end():]


def apply_look(html, look):
    """The page for the chosen design. "classic" returns the page untouched."""
    if look != "studio":
        return html
    if any(html.count(old) != 1 for old, _ in _STYLE_SWAPS) or "<html " not in html:
        return html
    for old, new in _STYLE_SWAPS:
        html = html.replace(old, new, 1)
    html = html.replace("<html ", '<html data-look="studio" ', 1)
    html = _take_data_attr(html, '<link id="look-fonts"', "href", "data-studio-href")
    html = _take_data_attr(html, '<meta name="theme-color"', "content", "data-studio-content")
    return html


def site_css_for(look, base_css, studio_css):
    """The shared stylesheet of the public pages (Login, Pricing, Help, Terms, Privacy) for the chosen design.

    Classic is site.css as it is. Studio is site.css followed by site_studio.css, which only restyles it. An @import line
    must come first in a stylesheet, so those lines of the studio file are moved to the very top. An empty or missing studio
    file gives the classic sheet, never a broken one."""
    if look != "studio" or not (studio_css or "").strip():
        return base_css
    # the address of a font file may itself contain ";" (wght@400;700), so an import is read as url(...) up to its own ";"
    imp = r"^@import\s+url\([^)]*\)[^;\n]*;[ \t]*\r?\n?"
    imports = re.findall(imp, studio_css, flags=re.M)
    rest = re.sub(imp, "", studio_css, flags=re.M)
    return "".join(imports) + base_css.rstrip() + "\n\n" + rest
