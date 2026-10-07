FROM python:3.13-alpine
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}"
WORKDIR /app
COPY . /app
RUN chmod +x /app/run.sh
ENV PORT=8099
EXPOSE 8099
CMD ["/app/run.sh"]
