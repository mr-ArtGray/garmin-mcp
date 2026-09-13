FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY README.md ./

RUN uv sync --frozen --no-dev

ENV GARMIN_MCP_TRANSPORT=streamable-http
ENV GARMIN_MCP_HOST=0.0.0.0
ENV GARMIN_MCP_PORT=8000

EXPOSE 8000

CMD ["uv", "run", "garmin-mcp"]
