FROM python:3.11-slim

# Ставимо ffmpeg (відео/аудіо), шрифти, OpenGL залежності та espeak-ng (TTS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fontconfig fonts-dejavu-core libgl1 libglib2.0-0 espeak-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Спочатку залежності, щоб кеш працював краще
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Далі весь код/ресурси
COPY . .

# За замовчуванням просто показуємо help; запускатимемо через docker run ... python main.py ...
CMD ["python", "main.py", "--help"]
