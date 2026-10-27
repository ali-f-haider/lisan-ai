FROM public.ecr.aws/docker/library/python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg git libegl1 libgles2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# the face check (face_unit.py): MediaPipe without its optional extras (its other needs are pinned in requirements.txt, so numpy stays 1.26)
RUN pip install --no-cache-dir --no-deps mediapipe==1.2.0

COPY backend/ .

RUN mkdir -p /data/uploads /data/outputs

ENV DATA_DIR=/data
ENV WHISPER_DEVICE=cpu
ENV WHISPER_COMPUTE=int8
ENV OMP_NUM_THREADS=4
ENV MKL_NUM_THREADS=4
ENV MPLCONFIGDIR=/tmp/matplotlib
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]