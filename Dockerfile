FROM python:3.12-slim

# New models need a recent CLI (claude-opus-5-5 needs 2.1.280 or later);
# rebuild with --no-cache to pick up the latest, or pin CLAUDE_CODE_VERSION.
ARG CLAUDE_CODE_VERSION=latest
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && rm -rf /var/lib/apt/lists/* /root/.npm

RUN useradd -m runner && mkdir /home/runner/work && chown runner /home/runner/work
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# The CLI keeps sessions and settings in ~/.claude; mount the home to keep --resume working.
USER runner
ENV HOME=/home/runner
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
