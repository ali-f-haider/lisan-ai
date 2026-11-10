"""Human sounds in Text to voice (1.97.1, Ali): (LAUGH), (YAWN), (CRY) ... typed by hand or picked from a list, in the first text box
or in an Arabic line. The voice gets its own sound tags; the AI translates only the words and each sound goes back to its place; the
subtitles never show a sound. The page formats the boxes itself (names in the speaker's color, sounds as a mark)."""
import re
import unittest
from pathlib import Path
from unittest import mock

import longdub_service as ld
import text_voice as tv
from test_text_voice import Base, UID, translated

ROOT = Path(__file__).resolve().parents[1]


class VoiceTags(unittest.TestCase):
    def test_names_and_variants_become_the_voices_tags(self):
        self.assertEqual(ld._sound_tags("مرحبا (LAUGH) يا (clear_throat) صديقي (Laughing) (sighs)"),
                         "مرحبا [laugh] يا [clear throat] صديقي [laugh] [sigh]")
        self.assertEqual(ld._sound_tags("(MAYBE) (١)"), "(MAYBE) (١)")         # anything else in round brackets stays

    def test_a_sound_typed_in_square_brackets_still_works(self):
        self.assertEqual(ld._sound_tags(ld._protect_brackets("[sound sad] هذا [laugh] [قل]")), "[sound sad] هذا [laugh] (قل)")

    def test_a_sound_is_never_read_as_a_word_in_the_rescue_copies(self):
        for _n, t in ld._empty_audio_texts("[sound sad] هذا [laugh] مضحك"):
            self.assertIsNone(re.search(r"(?<!\[)laugh", t), t)        # kept as the tag [laugh] or dropped, never a bare word

    def test_the_text_sent_to_the_voice_has_the_tags(self):
        sent = []
        with mock.patch("inworld_service.synthesize", lambda v, t, k, language="ar", **kw: sent.append(t) or b"mp3"):
            ld._tts_with_retry("v", "هَذَا مُضْحِكٌ (LAUGH) جِدًّا.")
        self.assertIn("[laugh]", sent[0])


class Placement(unittest.TestCase):
    def test_the_sounds_are_taken_out_and_put_back_at_the_same_place(self):
        clean, sounds = tv._split_sounds("Hello there (LAUGH) how are you? (sighs)")
        self.assertEqual(clean, "Hello there how are you?")
        self.assertEqual(tv._put_sounds("مرحبًا هناك كيف حالك؟", sounds), "مرحبًا هناك (LAUGH) كيف حالك؟ (SIGH)")
        self.assertEqual(tv._put_sounds("مرحبًا", []), "مرحبًا")

    def test_the_subtitles_never_show_a_sound(self):
        self.assertEqual(tv._no_sounds("مرحبا (LAUGH) يا صديقي (YAWN)"), "مرحبا يا صديقي")


class Prepare(Base):
    def test_the_ai_gets_only_the_words_and_the_sounds_come_back(self):
        job, _ = tv.new_job(UID, "Sara: Trace the call (LAUGH) now.\nAdam: (YAWN)", "en")
        tv.set_speakers(job, [{"id": "s1", "gender": "female"}, {"id": "s2", "gender": "male"}])
        seen = []

        def tr(job_id, items, key, source_language="en", gender=None, previous=None):
            seen.extend(it["text"] for it in items)
            return translated(job_id, items, key)
        with mock.patch("gemini_service.translate_script_lines", tr), mock.patch("text_voice._ask_addressees"), \
                mock.patch("longdub_service._check_tashkeel"):
            ok, err = tv.start_prepare(tv.load(job["id"]))
            self.assertTrue(ok, err)
            job = self.wait(job["id"])
        self.assertNotIn("(LAUGH)", " ".join(seen))
        self.assertEqual(job["lines"][0]["arabic"], "تَرْجَمَةُ l1 (LAUGH)")       # 3 of 4 words before it: after 2 of 2
        self.assertEqual(job["lines"][1]["arabic"], "(YAWN)")          # a line that is only a sound stays only the sound


class Page(unittest.TestCase):
    def test_the_page_knows_the_same_sounds_and_formats_its_boxes(self):
        html = (ROOT / "text_voice.html").read_text(encoding="utf-8")
        listed = re.search(r"var SOUNDS = \[([^\]]*)\]", html).group(1)
        self.assertEqual([x.strip().strip('"') for x in listed.split(",")], list(ld.SOUNDS))
        for k in ld.SOUNDS:
            self.assertIn(f'"{k}":' if " " in k else f"{k}:", html)          # an English and an Arabic name for each
        self.assertIn('richBox($("tvText"), { names: true });', html)
        self.assertIn('<div id="tvText" dir="auto"></div>', html)
        self.assertIn("richBox(t, { oneLine: true });", html)
        self.assertIn('class="tv-addsound" data-sound=', html)


if __name__ == "__main__":
    unittest.main()
