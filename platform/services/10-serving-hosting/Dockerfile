# Reference image — builds the generic wrapper with an empty pipeline
# registry (see src/serving/main.py). A real usecase copies this Dockerfile
# into its own repo and replaces the final COPY + CMD to point at its own
# entrypoint module, one that registers real pipelines instead of none.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "serving.main:app", "--host", "0.0.0.0", "--port", "8000"]
