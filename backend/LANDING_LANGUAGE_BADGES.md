# Floating source-language icons — 13 October 2026

The landing introduction now shows eight compact cards with crisp inline vector flag icons for English, Spanish, French, Portuguese, Italian, German, Dutch and Polish. Blue and gold borders, dark surfaces and soft shadows match the existing page. On wide screens four cards float gently on each side of the introduction; at smaller widths they form a three-column or two-column grid below the actions. Names follow the English/Arabic page selection. The animation pauses while hovering over the introduction and is disabled by the existing reduced-motion setting.

## Changed files

- `landing.html`: one scoped CSS block, a `hero-intro` wrapper and the six-card semantic list with a bilingual accessible heading. All icons are inline SVG: no new font, image, script, external request or dependency. The cards are informational, do not steal keyboard focus, and decorative flags are hidden from assistive technology. Existing scripts, links and business behavior are unchanged.
- `tests/test_landing_languages.py`: three offline tests compare the displayed languages and both sets of names with the real supported-language registry, check the list's bilingual accessible heading and check that icons need no network resources or keyboard interaction.
- `LANDING_LANGUAGE_BADGES.md`: this report.

The change is already saved in the project folder. Existing mixed line endings in `landing.html` are preserved byte-for-byte on unchanged lines. New tests/report use LF. `config.py` is unchanged. No commit, push, deployment, paid request or audio processing was performed.

## Verification

The former six-card checks are historical. The eight-card proposal requires a fresh local visual check at widths 1440, 1180, 1024, 768, 390 and 320 pixels, in English/Arabic and reduced motion, before release. No eight-card visual result is claimed here.

Required complete discovery result (`python -m unittest discover -s tests -v`):

```text
Ran 1421 tests in 829.712s

FAILED (failures=1, errors=1, skipped=1)
```

The remaining failure/error, if shown above, are the previously recorded Windows concurrent segment-write failure (`WinError 5`) and sandbox Git checkout access error. Their tests and implementation were not changed. The full log is `landing-language-full-suite.log` in workspace outputs; browser checks are in `landing-language-browser.log`.

## Publishing

Run yourself in Windows Command Prompt. These paths include only this visual batch and its test/report. The commit uses the current complete `landing.html`, so review it if there is other pending work inside that file.

```bat
cd /d "C:\Users\Ali Haider\Desktop\ai-dubbing-app\backend"
git --no-pager diff --check -- landing.html tests/test_landing_languages.py LANDING_LANGUAGE_BADGES.md
git --no-pager add -- landing.html tests/test_landing_languages.py LANDING_LANGUAGE_BADGES.md
git --no-pager commit --only -m "Add floating source-language icons to the landing page" -- landing.html tests/test_landing_languages.py LANDING_LANGUAGE_BADGES.md
git --no-pager push origin main
```

After Railway deploys, check the top of the landing page in both English and Arabic. The cards sit beside the introduction on desktop and below its buttons on small screens. The floating motion is gentle; visitors requesting reduced motion see static cards.

## Delivery / Claude addendum

`Landing-language-badges.diff` is the complete unified diff against this batch's local before snapshot; `Landing-language-badges.zip` contains the three full changed files under `backend/`. The changes are already on disk: do not reapply the patch to the current local file. Previous full-file landing-page snapshots are older and must not overwrite it. The main Claude return handoff is updated with this batch and its new file hashes.
