# Keep the validated Python major/minor version.
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Preserve the dependency layer when only application code changes.
COPY requirements.txt ./
RUN pip install \
    --no-cache-dir \
    -r requirements.txt

# Copy only runtime modules and initialization scripts.
# Configuration is injected at runtime; persistent databases live in /data.
COPY app/ ./app/
COPY scripts/__init__.py scripts/init_demo_db.py scripts/init_knowledge.py ./scripts/
COPY data/knowledge/ ./data/knowledge/

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
