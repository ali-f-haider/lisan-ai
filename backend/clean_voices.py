import json
import urllib.request
from config import ELEVENLABS_API_KEY as KEY

req = urllib.request.Request("https://api.elevenlabs.io/v1/voices?page_size=100",
                             headers={"xi-api-key": KEY})
voices = json.load(urllib.request.urlopen(req, timeout=30)).get("voices", [])
targets = [v for v in voices if (v.get("name") or "").startswith(("Cloned_", "Custom_"))]
print(f"Found {len(targets)} app-created voice(s) out of {len(voices)} total.")
for v in targets:
    d = urllib.request.Request(f"https://api.elevenlabs.io/v1/voices/{v['voice_id']}",
                               method="DELETE", headers={"xi-api-key": KEY})
    urllib.request.urlopen(d, timeout=30).read()
    print("deleted:", v["name"])
print("Done.")