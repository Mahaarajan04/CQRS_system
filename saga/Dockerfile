FROM python:3.12-slim
WORKDIR /service
COPY saga/requirements.txt saga/
RUN pip install --no-cache-dir -r saga/requirements.txt
# orchestrator.py imports events.py via sys.path "../write-api" — preserve layout
COPY write-api/events.py write-api/
COPY saga/ saga/
WORKDIR /service/saga
CMD ["python", "-u", "saga_worker.py"]
