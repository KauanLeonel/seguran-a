FROM python:3.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt \
    && groupadd --gid 10001 campus \
    && useradd --uid 10001 --gid campus --no-create-home campus
COPY app.py commerce.py database.py vault.py wsgi.py generate_users.py migrate_database.py style.css ./
COPY deploy/gunicorn.conf.py ./gunicorn.conf.py
USER 10001:10001
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:create_application()"]
