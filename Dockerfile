FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY onboarding ./onboarding
COPY seed_missions.py start.sh ./
RUN chmod +x start.sh

# Colloquy stores its SQLite DB here; mount a volume to persist it.
ENV COLLOQUY_DB=/data/colloquy.db
VOLUME /data

EXPOSE 8080
CMD ["sh", "start.sh"]
