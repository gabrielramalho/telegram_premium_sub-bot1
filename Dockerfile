FROM python:3.11-slim

# Evita prompts interativos
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py /app/bot.py

# Variáveis serão definidas no painel do Render
CMD ["python", "bot.py"]
