FROM python:3.13-alpine
WORKDIR /app
COPY standalone /app
ENV PORT=8099
EXPOSE 8099
CMD ["python","/app/server.py"]
