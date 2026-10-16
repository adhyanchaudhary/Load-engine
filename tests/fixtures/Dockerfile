# Operator provisions/reviews the base locally before building. No MCP builds.
FROM python:3.12-alpine
COPY http_fixture.py /app/http_fixture.py
USER 65534:65534
ENTRYPOINT ["python", "-B", "/app/http_fixture.py"]
