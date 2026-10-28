FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY evidano_social_scraper ./evidano_social_scraper
USER nobody
EXPOSE 3000
CMD ["python", "-m", "evidano_social_scraper"]
