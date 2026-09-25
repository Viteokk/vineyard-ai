# vineyard-ai: from GeoTIFF tiles to routes, measurements and the web map (CPU only).
#   docker build -t vineyard-ai .
#   docker run --rm -v /path/to/package:/raw -v $(pwd)/out:/app/out -v $(pwd)/web:/app/web vineyard-ai \
#       sh -c "python scripts/setup_data.py && python -m pipeline.run --all"
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 VINEYARD_RAW=/raw OMP_NUM_THREADS=4
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock.txt requirements.txt ./
# torch/ultralytics are only needed for training and the optional YOLO inference; the classical pipeline runs without them
RUN pip install --no-cache-dir $(grep -viE "^(torch|torchvision|ultralytics)" requirements.lock.txt)
COPY config.py ./
COPY pipeline ./pipeline
COPY scripts ./scripts
COPY train ./train
COPY web/index.html ./web/index.html
CMD ["python", "-m", "pipeline.run", "--all"]
