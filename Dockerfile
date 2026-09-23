FROM python:3.12.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home p05

COPY P05_Migration.py P05_Profilage_source.py ./
USER 10001:10001
CMD ["python", "P05_Migration.py", "--mode", "migrate", "--csv", "/data/healthcare_dataset.csv", "--report", "/tmp/P05_Controle_migration.json"]
