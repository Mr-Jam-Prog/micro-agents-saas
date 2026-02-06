FROM python:3.12-slim

WORKDIR /app
COPY . /app

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
