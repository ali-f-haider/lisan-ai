"""Checks for the findings of the live quality check of 9 October 2026 (QA-01, 02, 03, 05, 06, 07).
They read the pages and the server code, so wording and numbers cannot drift apart again."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def visible_text(html):
    html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def lum(h):
    h = h.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class RetentionIsDescribedOnceTests(unittest.TestCase):
    PAGES = ("landing.html", "help.html", "pricing.html", "privacy.html", "dub_long.html")

    def test_no_page_still_says_voices_are_deleted_when_the_job_ends_or_not_saved(self):
        for name in self.PAGES + ("account.html",):
            text = read(name)
            for old in ("deleted when the job ends", "Copied voices are not saved", "copied voices are not saved",
                        "تُحذف الأصوات المنسوخة عند انتهاء المهمة", "ولا تُحفظ الأصوات المنسوخة", "يُحذف عند انتهاء المهمة"):
                self.assertNotIn(old, text, "%s still says: %s" % (name, old))

    def test_the_public_pages_all_give_the_seven_day_rule_in_both_languages(self):
        for name in ("landing.html", "help.html", "privacy.html", "pricing.html"):
            text = visible_text(read(name))
            self.assertTrue("7 days" in text, name)
            self.assertTrue(("7 أيام" in text) or ("٧ أيام" in text), name + " (Arabic)")

    def test_the_numbers_match_what_the_server_does(self):
        service = read("longdub_service.py")
        self.assertIn("< 7 * 86400", service)                                   # voices: 7 days after the last use
        self.assertRegex(service, r'LONGDUB_PROJECT_KEEP_DAYS"\) or 90\)')       # saved project: 90 days
        self.assertRegex(service, r'LONGDUB_PARK_HOURS"\) or 24\)')              # idle project: video leaves after 24 hours
        self.assertIn('"uploading": 24, "estimated": 24, "failed": 24, "cancelled": 24', service)
        privacy = visible_text(read("privacy.html"))
        for must in ("6 hours", "24 hours", "90 days", "7 days", "Finish corrections"):
            self.assertIn(must, privacy)
        for must in ("6 ساعات", "24 ساعة", "90 يوماً", "7 أيام", "إنهاء التصحيحات"):
            self.assertIn(must, privacy)

    def test_the_arabic_help_no_longer_says_a_project_is_kept_seven_days(self):
        self.assertNotIn("ويُحتفظ بالمشروع ٧ أيام", read("help.html"))
        self.assertIn("ويُحتفظ بالمشروع المحفوظ ٩٠ يوماً", read("help.html"))


class LimitsHaveOneSourceTests(unittest.TestCase):
    PAGES = ("help.html", "landing.html")

    def server_limits(self):
        """The numbers main.py's _public_limits() reports, worked out from the constants in the source (importing main here would
        pull in the whole audio stack)."""
        main_src, svc = read("main.py"), read("longdub_service.py")
        num = lambda text, name: int(re.search(r"^%s\s*=\s*(\d+)" % name, text, re.M).group(1))
        body = main_src.split("def _public_limits():")[1].split("@app.get")[0]
        for must in ('"short_min_sec": NO_LIPSYNC_MIN_SEC', '"short_max_sec": NO_LIPSYNC_MAX_SEC', '"short_lipsync_max_sec": LIPSYNC_MAX_SEC',
                     '"short_max_mb": MAX_UPLOAD_MB', '"short_trim_max_mb": MAX_TRIM_UPLOAD_MB', 'longdub_service.speaker_vote_live.ENGINE_MAX_MIN',
                     'longdub_service.MAX_UPLOAD_BYTES // 1048576', 'int(longdub_service.MIN_SEC)'):
            self.assertIn(must, body)
        self.assertIn("MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024", svc)
        return {"short_min_sec": num(main_src, "NO_LIPSYNC_MIN_SEC"), "short_max_sec": num(main_src, "NO_LIPSYNC_MAX_SEC"),
                "short_lipsync_max_sec": num(main_src, "LIPSYNC_MAX_SEC"), "short_max_mb": num(main_src, "MAX_UPLOAD_MB"),
                "short_trim_max_mb": num(main_src, "MAX_TRIM_UPLOAD_MB"), "long_min_sec": int(float(re.search(r"^MIN_SEC\s*=\s*([\d.]+)", svc, re.M).group(1))),
                "long_max_min": num(read("speaker_vote_live.py"), "ENGINE_MAX_MIN"), "long_max_upload_mb": 2 * 1024}

    def test_the_numbers_written_in_the_pages_equal_the_server_limits(self):
        limits = self.server_limits()
        for name in self.PAGES:
            found = re.findall(r'<span data-limit="([a-z_]+)"[^>]*>([^<]+)</span>', read(name))
            self.assertTrue(found, name)
            for key, shown in found:
                self.assertIn(key, limits, "%s uses an unknown limit %s" % (name, key))
                western = shown.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
                self.assertEqual(western, str(limits[key]), "%s shows %s for %s" % (name, shown, key))

    def test_the_limits_are_the_ones_the_upload_checks_use(self):
        limits = self.server_limits()
        main_src = read("main.py")
        # the short dub's own checks use the same constants the page numbers come from
        self.assertIn("dur > NO_LIPSYNC_MAX_SEC", main_src)
        self.assertIn("MAX_TRIM_UPLOAD_MB", main_src.split("def _public_limits")[0])
        self.assertEqual(limits["short_max_sec"], 30)
        self.assertEqual(limits["short_lipsync_max_sec"], 15)

    def test_the_limits_route_is_public_and_the_long_dub_config_carries_them(self):
        main_src = read("main.py")
        self.assertIn('"/api/limits"', main_src.split("PUBLIC_PATHS = frozenset([")[1].split("])")[0])
        self.assertIn('"limits": _public_limits(),', main_src)

    def test_the_long_dub_page_no_longer_calls_the_short_limit_fifteen_seconds(self):
        page = read("dub_long.html")
        self.assertNotIn("normal 15-second limit", page)
        self.assertNotIn("حدّ الـ15 ثانية", page)
        self.assertIn("lim.short_max_sec", page)

    def test_the_help_page_separates_the_short_and_the_long_workflow(self):
        text = visible_text(read("help.html"))
        self.assertIn("Maximum file size for a short dub", text)
        self.assertIn("Dub Long Video takes videos of up to", text)
        self.assertIn("storage space of your plan is a separate limit", text)


class OneLanguageAtATimeTests(unittest.TestCase):
    SHARED = ("login.html", "pricing.html", "help.html", "terms.html", "privacy.html")

    def test_every_shared_page_follows_the_chosen_language(self):
        for name in self.SHARED:
            page = read(name)
            self.assertIn('data-lang="en"', page.split("<body")[0], name)
            self.assertIn("data-title-ar=", page.split("<body")[0], name)
            self.assertIn('<script src="/site.js"></script>', page, name)
            self.assertIn('class="en"', page, name)
            self.assertIn('class="ar"', page, name)

    def test_the_shared_script_sets_language_direction_and_the_switch(self):
        js = read("site.js")
        for must in ('"lisan_lang"', 'root.dir = l === "ar" ? "rtl" : "ltr"', "data-set-lang", "lisan-lang", "/api/limits", "data-title-"):
            self.assertIn(must, js)
        css = read("site.css")
        self.assertIn('html[data-lang="ar"] .en{display:none!important}', css)
        self.assertIn('html[data-lang="en"] .ar{display:none!important}', css)

    def test_pricing_is_not_two_languages_stacked_any_more(self):
        page = read("pricing.html")
        self.assertNotIn("ar2", page)
        self.assertIn('<body class="site">', page)
        self.assertIn('<link rel="stylesheet" href="/site.css">', page)
        self.assertNotIn("#94a3b8", page)

    def test_the_help_page_has_no_private_language_buttons_and_one_title(self):
        page = read("help.html")
        self.assertNotIn("showLang", page)
        self.assertNotIn('class="langs"', page)
        self.assertNotIn("Back to Home / العودة للرئيسية", page)
        self.assertIn('id="secAr" class="ar rtl" lang="ar" dir="rtl"', page)

    def test_every_login_message_has_an_arabic_text(self):
        page = read("login.html")
        messages = set(re.findall(r'msg\("([^"]+)"', page))
        messages |= set(re.findall(r'return "([^"]+)"', page.split("function authMsg")[1].split("function cleanUrl")[0]))
        messages.add("We couldn't log you in just now. Please try again in a moment.")
        table = page.split("var AR_TEXT = ")[1].split("};")[0]
        for text in messages:
            self.assertIn(text, table, text)

    def test_legal_pages_have_an_arabic_version_that_says_which_text_governs(self):
        for name in ("terms.html", "privacy.html"):
            page = read(name)
            self.assertIn('<div class="ar" lang="ar">', page)
            self.assertIn("النص الإنجليزي هو المعتمد", page)
            # same headings in both languages
            self.assertEqual(len(re.findall(r"<h2>", page.split('<div class="ar"')[0])), len(re.findall(r"<h2>", page.split('<div class="ar"')[1])), name)

    def test_the_short_app_step_menu_has_arabic_names(self):
        app = read("app.js")
        block = app.split("var STEPS = [")[1].split("];")[0]
        self.assertEqual(block.count("label:"), block.count(" ar:"))
        self.assertIn("١ · رفع الملف", block)


class ContrastTests(unittest.TestCase):
    def tokens(self, block):
        return dict(re.findall(r"--([a-z0-9]+):(#[0-9a-fA-F]{6})", block))

    def test_text_colours_reach_4_5_to_1_in_both_themes(self):
        css = read("site.css")
        dark = self.tokens(css.split(":root{")[1].split("}")[0])
        light = self.tokens(css.split("html.light{")[1].split("}")[0])
        for theme, t in (("dark", dark), ("light", light)):
            for fg in ("text", "body", "muted", "dim", "gold", "gold2", "ok"):
                for bg in ("bg", "bg2", "card"):
                    self.assertGreaterEqual(contrast(t[fg], t[bg]), 4.5, "%s: %s on %s" % (theme, fg, bg))


class AccountTabsTests(unittest.TestCase):
    def test_the_tabs_are_real_buttons_with_the_tab_pattern(self):
        page = read("account.html")
        self.assertIn('id="acctTabbar" role="tablist"', page)
        tabs = re.findall(r'<button type="button" role="tab" class="acct-tab[^"]*" data-tab="(\w+)"[^>]*aria-selected="(true|false)"[^>]*aria-controls="panel-\1" tabindex="(0|-1)"', page)
        self.assertEqual([t[0] for t in tabs], ["balance", "subscription", "voices", "files", "purchases", "usage", "api"])
        self.assertEqual([t[2] for t in tabs], ["0", "-1", "-1", "-1", "-1", "-1", "-1"])        # one tab stop
        self.assertIsNone(re.search(r'<div class="acct-tab[ "]', page))
        self.assertEqual(len(re.findall(r'role="tabpanel" aria-labelledby="tab\w+"', page)), 7)
        for must in ('"ArrowRight"', '"ArrowLeft"', '"Home"', '"End"', 'aria-selected', "getComputedStyle(document.documentElement).direction"):
            self.assertIn(must, page)
        self.assertIn(".acct-tab:focus-visible", page)

    def test_the_working_file_note_no_longer_says_all_working_files_go_after_six_hours(self):
        page = read("account.html")
        self.assertNotIn("Work-in-progress files (uploads, drafts) are still cleared", page)
        self.assertEqual(page.count("Work-in-progress files of short dubs"), 3)


class HelperKeepsUpTests(unittest.TestCase):
    def test_the_helper_reads_the_whole_help_page_and_still_skips_the_api_entries(self):
        import assistant_service as a
        old = a.BASE_DIR, dict(a._kb)
        a.BASE_DIR, a._kb["mtime"], a._kb["text"] = ROOT, None, ""
        try:
            text = a.help_text()
            full = a._page_text("help.html", None, 10 ** 7)
        finally:
            a.BASE_DIR = old[0]
            a._kb.update(old[1])
        self.assertLessEqual(len(full), 21000)               # the cap no longer cuts the end of the page
        self.assertIn("Are my uploaded files and generated videos saved on your servers?", text)
        self.assertNotIn("/api-docs", text)
        self.assertIn("Finish corrections", text)


if __name__ == "__main__":
    unittest.main()
