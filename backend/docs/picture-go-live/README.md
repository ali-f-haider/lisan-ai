# Picture analysis: turning it on

v1.82.78 wires the picture unit into the project flow, but ships it switched OFF
(`IMAGE_UNIT` defaults to off). While it is off: no checkbox, no charge, no request, no change to any old or new project.

## Before turning it on
1. Run the real comparison on a hand-labelled clip: `python tools/picture_eval.py --live` (needs `GEMINI_API_KEY` in the environment only).
   Read errors, coverage, fixed / broken lines and the measured cost, not only the net score.
2. Decide the picture weight from that result (`IMAGE_PICTURE_WEIGHT`, 1 or 2; 1 until the same-line comparison supports 2).
3. Approve the price (`PICTURE_FLAT_CREDITS` 4, `PICTURE_CREDITS_PER_MIN` 3.0 are the proposal) and the partial-refund rounding (up to a whole credit, never above what was paid).
4. Read the Arabic wording in `privacy.html.patch` and `help.html.patch`.
5. Check the paid tier / retention terms of the processing service before the privacy text goes live.

## Turning it on
1. Apply the three patches in this folder (`git apply docs/picture-go-live/<name>.patch`; they are the help, privacy and assistant texts).
2. Set `IMAGE_UNIT=1` in Railway. Optional: `IMAGE_CUT_ROOM=1` (shot cuts bound a line's fitting room; leave off until listened to on real videos).
3. `tests/test_picture_wiring.py::PageTests::test_public_wording_goes_live_together_with_the_switch` fails if the switch is on and the texts are missing, or the texts are on and the switch is off.

## Turning it off again
Set `IMAGE_UNIT=0`. Projects that already paid keep their advice; nothing new is requested; appearance advice is hidden everywhere.
