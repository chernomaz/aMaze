FROM amaze/agent-base:dev

# Install openai client (fastapi already included in base image via agent_runtime)
RUN uv pip install --system "openai>=1.0"

COPY src/main.py /agent/main.py

CMD ["fastapi", "run", "main.py", "--port", "8090", "--host", "0.0.0.0"]
