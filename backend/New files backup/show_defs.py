import inspect, importlib

for m in ("whisper_service", "gemini_service", "eleven_service",
          "ffmpeg_utils", "media_paths", "lipsync_service", "tts_service"):
    try:
        mod = importlib.import_module(m)
    except Exception as e:
        print("==", m, "IMPORT ERROR:", e)
        continue
    print("==", m)
    for name, obj in sorted(vars(mod).items()):
        if inspect.isfunction(obj) and getattr(obj, "__module__", "") == m:
            try:
                print("   def", name + str(inspect.signature(obj)))
            except Exception:
                print("   def", name + "(...)")