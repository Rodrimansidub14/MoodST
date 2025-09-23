FROM python:3.11-slim

WORKDIR /app
COPY mcp/remote/server.py /app/server.py

RUN pip install fastapi uvicorn

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
EXPOSE 8080