FROM python:3.12-slim
WORKDIR /app
COPY deploy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin afrand
COPY panel /app/panel
COPY templates /app/templates
COPY static /app/static
COPY demo /app/demo
COPY run.py /app/run.py
RUN mkdir /app/instance && chown afrand:afrand /app/instance
USER afrand
EXPOSE 5057
CMD ["gunicorn", "--bind", "0.0.0.0:5057", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "run:app"]
