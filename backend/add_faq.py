import re
from pathlib import Path

p = Path("help.html")
t = p.read_text(encoding="utf-8")

# Find the last English FAQ to copy its exact HTML wrapper (e.g., <details> or <div class="faq">)
match = re.search(r'(<(?:details|div)[^>]*>.*?Are my uploaded files and generated videos saved on your servers\?.*?</(?:details|div)>)', t, re.DOTALL)

if match:
    template = match.group(1)
    # Swap question
    new_faq = re.sub(r'Are my uploaded files.*?\?', 'Will the resolution or quality of my video be affected?', template)
    # Swap answer
    new_faq = re.sub(r'<p>.*?</p>', '<p>No. The original video stream is copied unchanged; only the audio track is replaced (or mixed) with the dubbed Arabic audio. Resolution, bitrate, and visual quality stay exactly as your source file.</p>', new_faq, flags=re.DOTALL)
    
    t = t.replace(match.group(1), match.group(1) + "\n" + new_faq)
    p.write_text(t, encoding="utf-8")
    print("✅ Added resolution FAQ to English section.")
else:
    print("⚠️ Could not auto-detect HTML structure. Please add it manually.")