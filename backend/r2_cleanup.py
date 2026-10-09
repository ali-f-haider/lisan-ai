"""Remove old finished-file copies from the R2 backup bucket (maintenance tool; the app also does this by itself).

Run it from the backend folder. It only looks at finished outputs at the top level of the bucket (names ending in
_final_dubbed.mp3, _final_dubbed_video.mp4, _final_lipsync.mp4, _final_voices.m4a, _final_effects.m4a,
_final_corrections.m4a). Database snapshots, register copies and every folder are never touched.

  python r2_cleanup.py --older-than-days 31          shows what WOULD be deleted (deletes nothing)
  python r2_cleanup.py --older-than-days 31 --yes    deletes it
  python r2_cleanup.py --older-than-days 0 --yes     deletes every finished-file copy

It needs the four R2 settings (account, key id, secret, bucket) in the environment, for example:
  railway run python r2_cleanup.py --older-than-days 31
"""
import argparse
import sys

import r2_backup


def main(argv=None):
    ap = argparse.ArgumentParser(description="Delete old finished-file copies from R2 (dry run unless --yes).")
    ap.add_argument("--older-than-days", type=float, required=True,
                    help="delete copies older than this many days (0 = all finished-file copies)")
    ap.add_argument("--yes", action="store_true", help="really delete (without it nothing is deleted)")
    args = ap.parse_args(argv)
    if args.older_than_days < 0:
        print("The number of days cannot be negative.")
        return 2
    plan = r2_backup.purge_old_final_outputs(args.older_than_days, dry_run=True, max_deletes=0)
    if plan.get("error") and not plan.get("found"):
        print("Could not read R2:", plan["error"])
        return 1
    print(f"Read from R2: {plan['found']} finished-file copies older than {args.older_than_days:g} days "
          f"({plan['bytes'] / 1e9:.2f} GB); {plan['kept']} newer ones stay.")
    if plan["found"]:
        print(f"Oldest {plan['oldest']}, newest of those {plan['newest']}.")
        print("First names:", ", ".join(plan["names"]))
    if not args.yes:
        print("Dry run: nothing was deleted. Add --yes to delete these.")
        return 0
    if not plan["found"]:
        return 0
    done = r2_backup.purge_old_final_outputs(args.older_than_days, max_deletes=10 ** 9)
    print(f"Deleted {done['deleted']} of {done['found']} ({done['failed']} failed).")
    return 1 if done.get("error") or done["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
