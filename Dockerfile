FROM python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STORYCAST_ROOT=/app
WORKDIR /app
COPY storycast/ /app/storycast/
COPY tests/ /app/tests/
ENTRYPOINT ["python", "-m", "storycast"]
CMD ["status"]
