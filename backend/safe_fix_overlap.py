import re
from pathlib import Path

ep = Path("eleven_service.py")
text = ep.read_text(encoding="utf-8")

def fix_overlap(code, list_name):
    # Captures the EXACT indentation of the original line
    pattern = re.compile(r'^([ \t]*)allowed_end = ' + list_name + r'\[i \+ 1\]\["start"\] - 0\.005 if i \+ 1 < len\(' + list_name + r'\) else final_duration', re.MULTILINE)
    
    def replacer(match):
        indent = match.group(1) # The exact spaces/tabs from your file
        return (
            f'{indent}# PATCHED: global overlap check\n'
            f'{indent}allowed_end = final_duration\n'
            f'{indent}for _j in range(len({list_name})):\n'
            f'{indent}    if _j == i: continue\n'
            f'{indent}    _other_start = {list_name}[_j]["start"]\n'
            f'{indent}    if _other_start > item["start"] and _other_start - 0.005 < allowed_end:\n'
            f'{indent}        allowed_end = _other_start - 0.005'
        )
    
    new_code, count = pattern.subn(replacer, code)
    return new_code, count

text, c1 = fix_overlap(text, "generated_files")
text, c2 = fix_overlap(text, "items")

print(f"Patched {c1} generated_files, {c2} items")
if c1 + c2 == 0:
    print("WARNING: No matches found. The file might already be patched or the code changed.")
else:
    ep.write_text(text, encoding="utf-8")
    print("File saved successfully.")