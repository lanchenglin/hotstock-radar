FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOTSTOCK_DATA_DIR=/app/data
COPY requirements.txt requirements-data.txt ./
ARG WITH_PUBLIC_DATA=0
RUN pip install --no-cache-dir -r requirements.txt && if [ "$WITH_PUBLIC_DATA" = "1" ]; then pip install --no-cache-dir -r requirements-data.txt; fi
COPY hotstock ./hotstock
COPY run.py ./
RUN useradd --uid 10001 --create-home radar && mkdir -p /app/data && chown -R radar:radar /app
USER radar
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=3)" || exit 1
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8765"]
