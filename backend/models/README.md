# models/face_landmarker.task

Used by `face_unit.py` (the face check: who speaks on screen).

- What: Google MediaPipe Face Landmarker bundle (BlazeFace short-range face detector, Face Mesh V2, Blendshape V2), float16.
- Source: https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
  (downloaded 2026-10-28, 3,758,596 bytes).
- Licence: Apache License, Version 2.0 (stated on each model card:
  https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf ,
  https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Model%20Card%20%28Short%20Range%29.pdf ,
  https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf ).
  Out of scope per the cards: life-critical decisions and identifying people; the face check does neither.
- The Python package `mediapipe` (1.2.0, Apache 2.0) is installed in the Dockerfile.
