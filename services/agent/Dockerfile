FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt pyproject.toml ./
COPY ado2gh ./ado2gh
COPY services/agent ./services/agent

RUN pip install --no-cache-dir -e ".[api,postgres]"

ENV ACCELERATOR_URL=http://accelerator:8080
EXPOSE 8090

CMD ["uvicorn", "services.agent.main:app", "--host", "0.0.0.0", "--port", "8090"]
