FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e . && pip install --no-cache-dir uvicorn

COPY . .

EXPOSE 9090

CMD ["python", "run.py"]
