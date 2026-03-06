FROM amaze/agent-base:dev

COPY src/main.py /agent/main.py

CMD ["fastapi", "run", "main.py", "--port", "8090", "--host", "0.0.0.0"]
