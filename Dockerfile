FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
CMD ["sh","-c","uvicorn app.web:app --host 0.0.0.0 --port ${PORT:-8000}"]
