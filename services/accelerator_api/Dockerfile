FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt pyproject.toml ./
COPY ado2gh ./ado2gh
COPY services/accelerator_api ./services/accelerator_api
COPY migration.yaml ./migration.yaml

RUN pip install --no-cache-dir -e ".[api,postgres,redis]"

ENV ADO2GH_CONFIG=/app/migration.yaml
EXPOSE 8080

CMD ["uvicorn", "services.accelerator_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
