# HomeKit companion for the Glass Kiln Controller. Built on the board:
#   cd ~/ArduinoApps/glass-kiln-controller/homekit && docker compose up -d --build
FROM python:3.13-slim
WORKDIR /opt/hap
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hap_kiln.py .
CMD ["python", "-u", "hap_kiln.py"]
