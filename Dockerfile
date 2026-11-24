FROM python:3.11-slim
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY . .
RUN pip install --no-deps --no-build-isolation . && useradd --uid 10001 --create-home labagent \
    && mkdir -p /app/data && chown -R labagent:labagent /app
USER labagent
ENV LABAGENT_HOST=0.0.0.0 LABAGENT_PORT=8001 LABAGENT_LOCAL_ONLY=false
EXPOSE 8001
HEALTHCHECK --interval=15s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/api/health',timeout=5)"
CMD ["python", "-m", "labagent"]
