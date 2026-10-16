"""The public site has two designs (Classic and Studio) that the admin can switch between.

landing.html carries both: the text and flags are shared, each design has its own stylesheet and only one is switched on.
The page and server code are read as text where a database or a browser would be needed."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import landing_look as look

PAGE = (ROOT / "landing.html").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
ADMIN = (ROOT / "admin.html").read_text(encoding="utf-8")
SITE_CSS = (ROOT / "site.css").read_text(encoding="utf-8")
STUDIO_CSS = (ROOT / "site_studio.css").read_text(encoding="utf-8")
SHARED_PAGES = ("login.html", "pricing.html", "help.html", "terms.html", "privacy.html")


def _sheet(name):
    m = re.search(r'<style id="look-%s"[^>]*>(.*?)</style>' % name, PAGE, re.S)
    assert m, name
    return m.group(1)


def _classes(css):
    css = re.sub(r'url\("[^"]*"\)', "", css)
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\{[^{}]*\}", "{}", css)
    return set(re.findall(r"\.([A-Za-z_][\w-]*)", css))


class Choosing(unittest.TestCase):
    def test_unknown_or_missing_values_are_not_a_design(self):
        for v in (None, "", "  ", "new", "Studio2", 3, {}):
            self.assertEqual(look.clean_look(v), "")

    def test_names_are_case_and_space_tolerant(self):
        self.assertEqual(look.clean_look(" Studio "), "studio")
        self.assertEqual(look.clean_look("CLASSIC"), "classic")

    def test_classic_is_the_default_for_anything_unclear(self):
        self.assertEqual(look.choose_look(), "classic")
        self.assertEqual(look.choose_look("nonsense", "alsonot"), "classic")

    def test_saved_setting_is_used(self):
        self.assertEqual(look.choose_look("studio"), "studio")
        self.assertEqual(look.choose_look("classic"), "classic")

    def test_a_preview_request_wins_over_the_setting_but_only_if_valid(self):
        self.assertEqual(look.choose_look("classic", "studio"), "studio")
        self.assertEqual(look.choose_look("studio", "classic"), "classic")
        self.assertEqual(look.choose_look("studio", "garbage"), "studio")


class Applying(unittest.TestCase):
    def test_classic_page_is_returned_untouched(self):
        self.assertEqual(look.apply_look(PAGE, "classic"), PAGE)
        self.assertEqual(look.apply_look(PAGE, ""), PAGE)
        self.assertEqual(look.apply_look(PAGE, "whatever"), PAGE)

    def test_studio_turns_one_stylesheet_off_and_the_other_on(self):
        out = look.apply_look(PAGE, "studio")
        self.assertIn('<style id="look-classic" media="not all">', out)
        self.assertIn('<style id="look-studio">', out)
        self.assertNotIn('<style id="look-studio" media="not all">', out)
        self.assertIn('<html data-look="studio" ', out)

    def test_studio_uses_its_own_fonts_and_theme_colour(self):
        out = look.apply_look(PAGE, "studio")
        fonts = re.search(r'<link id="look-fonts"[^>]*>', out).group(0)
        self.assertIn("Young+Serif", re.search(r'\shref="([^"]*)"', fonts).group(1))
        self.assertIn('content="#08090a"', re.search(r'<meta name="theme-color"[^>]*>', out).group(0))
        classic_fonts = re.search(r'<link id="look-fonts"[^>]*>', PAGE).group(0)
        self.assertNotIn("Young+Serif", re.search(r'\shref="([^"]*)"', classic_fonts).group(1))

    def test_only_those_things_change(self):
        out = look.apply_look(PAGE, "studio")
        # undo the swaps and the page is the original again
        back = out.replace('<style id="look-classic" media="not all">', '<style id="look-classic">')
        back = back.replace('<style id="look-studio">', '<style id="look-studio" media="not all">')
        back = back.replace('<html data-look="studio" ', "<html ", 1)
        back = re.sub(r'(<link id="look-fonts" href=")[^"]*"', lambda m: m.group(1) + re.search(
            r'<link id="look-fonts" href="([^"]*)"', PAGE).group(1) + '"', back, count=1)
        back = re.sub(r'(<meta name="theme-color" content=")[^"]*"', r'\g<1>#06080d"', back, count=1)
        self.assertEqual(back, PAGE)

    def test_a_page_without_the_markers_stays_classic(self):
        for broken in (PAGE.replace('<style id="look-studio" media="not all">', "<style>"),
                       PAGE.replace('<style id="look-classic">', "<style>"),
                       "<html lang='en'><body>old page</body></html>"):
            self.assertEqual(look.apply_look(broken, "studio"), broken)

    def test_applying_twice_changes_nothing_more(self):
        once = look.apply_look(PAGE, "studio")
        self.assertEqual(look.apply_look(once, "studio"), once)


class OnePageTwoDesigns(unittest.TestCase):
    def test_both_stylesheets_exist_once_and_classic_is_on_by_default(self):
        self.assertEqual(PAGE.count('<style id="look-classic">'), 1)
        self.assertEqual(PAGE.count('<style id="look-studio" media="not all">'), 1)
        self.assertLess(PAGE.index('id="look-classic"'), PAGE.index('id="look-studio"'))

    def test_each_design_hides_the_other_designs_pieces(self):
        self.assertIn(".only-studio{display:none!important}", _sheet("classic"))
        self.assertIn(".only-classic{display:none!important}", _sheet("studio"))
        self.assertNotIn("only-classic", _sheet("classic"))
        self.assertNotIn("only-studio", _sheet("studio"))

    def test_the_designs_differ_only_in_the_hero_pieces_that_are_not_shared(self):
        a, b = _classes(_sheet("classic")), _classes(_sheet("studio"))
        self.assertEqual(a - b, {"a", "cap", "caps", "e", "fade", "only-studio", "stage", "stage-top", "tag"},
                         "a rule was added to only one design: add it to both (see the note above the studio stylesheet)")
        self.assertEqual(b - a, {"clip", "dub", "hero-row", "mu", "on", "only-classic", "src", "tl", "tl-head", "tl-lane",
                                 "tl-nm", "tl-ruler", "tl-scroll", "tl-ticks", "tl-trk"})

    def test_text_and_flags_exist_once_and_are_shared(self):
        for code in ("en", "es", "fr", "pt", "it", "de", "nl", "pl"):
            self.assertEqual(PAGE.count('data-source-language="%s"' % code), 1, code)
        self.assertEqual(PAGE.count("<h1>"), 1)
        self.assertEqual(PAGE.count('class="proof"'), 1)

    def test_each_design_has_its_own_piece_of_the_hero(self):
        self.assertEqual(len(re.findall(r'class="stage only-classic"', PAGE)), 1)
        self.assertEqual(len(re.findall(r'class="tl-scroll only-studio"', PAGE)), 1)
        self.assertIn('id="tl"', PAGE)

    def test_the_timeline_script_exists_and_stops_when_hidden_or_reduced_motion(self):
        js = PAGE[PAGE.index("Hero timeline"):]
        js = js[:js.index("Videos: shown only")]
        self.assertIn("reduce", js)
        self.assertIn("IntersectionObserver", js)
        self.assertIn("doc.hidden", js)


class ServerAndAdmin(unittest.TestCase):
    def test_home_route_applies_the_chosen_design(self):
        m = re.search(r'@app\.get\("/"\)\s*def landing\(request: Request\):(.*?)\n\n\ndef ', MAIN, re.S)
        self.assertTrue(m, "landing() must take the request (for ?look=)")
        body = m.group(1)
        self.assertIn("landing_look.apply_look(html, landing_look.choose_look(", body)
        self.assertIn('_cfg.get("landingStyle")', body)
        self.assertIn('request.query_params.get("look")', body)
        self.assertEqual(body.count("_get_pricing_config()"), 1, "one database read per visit, not two")
        self.assertIn("no-cache", body)

    def test_the_setting_defaults_to_classic_and_is_read_from_its_column(self):
        self.assertRegex(MAIN, r'"landingStyle": "classic",')
        self.assertRegex(MAIN, r'"landingStyle": landing_look\.clean_look\(row\.get\("landing_style"\)\) or defaults\["landingStyle"\]')

    def test_saving_it_has_its_own_request_and_only_when_changed(self):
        i = MAIN.index('if "landingStyle" in config:')
        block = MAIN[i - 200:i + 1500]
        self.assertIn('"landing_style": _want_look', block)
        self.assertIn('!= (_get_pricing_config().get("landingStyle") or landing_look.DEFAULT_LOOK)', block)
        self.assertIn("ALTER TABLE pricing_config ADD COLUMN IF NOT EXISTS landing_style text", block)
        self.assertIn("failed_settings.append", block)
        # the main save body must not carry the new column (a missing column would break every admin save)
        main_body = MAIN[MAIN.index('body = json.dumps({\n            "id": "singleton",\n            "free_credits"'):]
        main_body = main_body[:main_body.index("}).encode")]
        self.assertNotIn("landing_style", main_body)

    def test_an_old_admin_page_without_the_field_cannot_reset_the_design(self):
        self.assertIn('if "landingStyle" in config:', MAIN)

    def test_admin_page_has_the_switch_with_both_designs_and_previews(self):
        self.assertRegex(ADMIN, r'<select id="landingStyle"')
        self.assertIn('<option value="classic">', ADMIN)
        self.assertIn('<option value="studio">', ADMIN)
        self.assertIn('href="/?look=classic"', ADMIN)
        self.assertIn('href="/?look=studio"', ADMIN)

    def test_admin_page_loads_and_sends_the_choice(self):
        self.assertIn("document.getElementById('landingStyle').value = (r.data.landingStyle === 'studio') ? 'studio' : 'classic';", ADMIN)
        self.assertIn("landingStyle: document.getElementById('landingStyle')?.value === 'studio' ? 'studio' : 'classic'", ADMIN)


class SharedPagesFollowTheSwitch(unittest.TestCase):
    """Login, Pricing, Help, Terms and Privacy share /site.css; Studio adds site_studio.css to it, so the page files stay as they are."""

    def test_every_shared_page_loads_the_shared_stylesheet(self):
        for name in SHARED_PAGES:
            with self.subTest(page=name):
                self.assertIn('<link rel="stylesheet" href="/site.css">', (ROOT / name).read_text(encoding="utf-8"))

    def test_classic_is_the_shared_stylesheet_untouched(self):
        self.assertEqual(look.site_css_for("classic", SITE_CSS, STUDIO_CSS), SITE_CSS)
        self.assertEqual(look.site_css_for("", SITE_CSS, STUDIO_CSS), SITE_CSS)

    def test_studio_is_the_shared_stylesheet_plus_the_studio_one(self):
        out = look.site_css_for("studio", SITE_CSS, STUDIO_CSS)
        self.assertIn(SITE_CSS.rstrip(), out)
        self.assertTrue(out.index(SITE_CSS.rstrip()) < out.index(":root{\n  --bg:#08090a"), "studio rules must come after site.css to win")
        self.assertIn("Young+Serif", out)

    def test_a_font_import_comes_first_even_though_its_address_contains_semicolons(self):
        out = look.site_css_for("studio", SITE_CSS, STUDIO_CSS)
        self.assertTrue(out.startswith("@import url("), out[:60])
        self.assertEqual(out.count("@import"), 1)
        first_line = out.split("\n", 1)[0]
        self.assertTrue(first_line.rstrip().endswith(");"), first_line)
        self.assertIn("wght@400;700", first_line)
        self.assertNotIn("@import", out[len(first_line):])

    def test_missing_or_empty_studio_file_gives_the_classic_sheet(self):
        for empty in ("", "   \n", None):
            self.assertEqual(look.site_css_for("studio", SITE_CSS, empty), SITE_CSS)

    def test_both_files_are_balanced_css(self):
        for name, css in (("site.css", SITE_CSS), ("site_studio.css", STUDIO_CSS)):
            body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
            body = re.sub(r'"[^"]*"', "", body)
            self.assertEqual(body.count("{"), body.count("}"), name)

    def test_studio_rules_beat_a_pages_own_style_block(self):
        # page <style> blocks come after the link, so a studio rule needs a more specific selector to win
        for line in STUDIO_CSS.splitlines():
            if line.startswith(("body.site .card{", "body.site .plan-card{", "body.site .primary{")):
                self.assertTrue(line.startswith("body.site"))
        for sel in ("body.site .card{", "body.site .primary{", "body.site .plan-card{", "body.site .pbadge{", "body.site input{"):
            self.assertIn(sel, STUDIO_CSS)

    def test_studio_covers_the_light_and_arabic_versions_of_the_site(self):
        self.assertIn("html.light{", STUDIO_CSS)
        self.assertIn('html[data-lang="ar"]{', STUDIO_CSS)
        self.assertIn("@media (max-width:600px)", STUDIO_CSS)

    def test_studio_uses_the_same_colours_and_fonts_as_the_home_page_studio_design(self):
        home = _sheet("studio")
        for token in ("#08090a", "#e6b85c", "Young Serif", "IBM Plex Mono", "IBM Plex Sans"):
            self.assertIn(token, home, token)
            self.assertIn(token, STUDIO_CSS, token)

    def test_no_round_corners_or_glows_are_left_in_the_studio_sheet(self):
        for m in re.finditer(r"border-radius:\s*([^;}]+)", STUDIO_CSS):
            self.assertIn(m.group(1).strip(), ("0", "4px", "6px"), m.group(0))
        self.assertNotIn("radial-gradient", STUDIO_CSS)
        self.assertNotIn("box-shadow:0 ", STUDIO_CSS)

    def test_the_site_css_route_applies_it_and_falls_back_safely(self):
        m = re.search(r'@app\.get\("/site\.css"\)\s*def site_css\(\):(.*?)\n\n@app', MAIN, re.S)
        self.assertTrue(m)
        body = m.group(1)
        self.assertIn("landing_look.site_css_for(look,", body)
        self.assertIn('(_get_pricing_config() or {}).get("landingStyle")', body)
        self.assertIn("except Exception", body)
        self.assertIn('return FileResponse(BASE_DIR / "site.css"', body)
        self.assertIn("_NO_CACHE_HEADERS", body)

    def test_admin_text_says_the_switch_covers_the_whole_public_site(self):
        i = ADMIN.index("Home Page Design")
        self.assertIn("Login, Pricing, Help, Terms and Privacy", ADMIN[i:i + 900])


if __name__ == "__main__":
    unittest.main()
