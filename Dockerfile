FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ src/
COPY tasks/ tasks/
COPY config.yaml .

# Create data dir
RUN mkdir -p data

EXPOSE 8767

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8767", "--log-level", "info"]
