FROM python:3.12-slim

# Install uv
RUN pip install uv --quiet

WORKDIR /app
ENV PYTHONPATH=/app

# Copy dependency manifests first (layer-cached until they change)
COPY pyproject.toml uv.lock* ./

# Install production deps into the system Python (no separate venv needed in Docker)
RUN uv pip install --system --no-cache -r pyproject.toml

COPY . .

EXPOSE 8000

CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "8000"]
