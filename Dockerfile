FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
       build-essential \
       python3-dev \
       meson \
       ninja-build \
       gfortran \
       libopenblas-dev \
       liblapack-dev \
       pkg-config \
       libnss3 \
       libatk1.0-0 \
       libatk-bridge2.0-0 \
       libcups2 \
       libxss1 \
       libx11-xcb1 \
       libxcomposite1 \
       libxrandr2 \
       libxdamage1 \
       libgbm1 \
       libasound2 \
       libpangocairo-1.0-0 \
       libpangoft2-1.0-0 \
       libgtk-3-0 \
       wget \
 && rm -rf /var/lib/apt/lists/*

# --- step 2: 設定工作目錄 ---
WORKDIR /app

# --- step 3: 複製 requirements 並安裝 Python 套件 ---
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt


RUN python -m playwright install

# 6. 複製專案程式碼
COPY . .

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "-k", "uvicorn.workers.UvicornWorker", "app:app"]
