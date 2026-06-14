FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt* pyproject.toml* ./
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || pip install --no-cache-dir . 2>/dev/null || true
COPY . .
LABEL org.opencontainers.image.source="https://github.com/mafzalkalwardev/google-voice-dispatch-agent"
CMD ["python", "-c", "print('google-voice-dispatch-agent image ready')"]
