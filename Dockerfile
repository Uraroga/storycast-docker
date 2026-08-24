FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STORYCAST_ROOT=/app
WORKDIR /app
COPY storycast/ /app/storycast/
COPY tests/ /app/tests/
ENTRYPOINT ["python", "-m", "storycast"]
CMD ["status"]
