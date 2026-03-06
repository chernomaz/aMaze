FROM amaze/mcp-base:dev

COPY src/main.py /mcp/main.py

ENV MCP_SERVER_NAME=calculator-mcp
ENV MCP_SERVER_HOST=mcp-calculator
ENV MCP_SERVER_PORT=8090

CMD ["fastapi", "run", "main.py", "--port", "8090", "--host", "0.0.0.0"]
