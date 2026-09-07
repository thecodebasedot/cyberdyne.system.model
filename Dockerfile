FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY cyberdyne ./cyberdyne
COPY scenarios ./scenarios
RUN pip install --no-cache-dir -e .
EXPOSE 8080
ENV CYBERDYNE_SCENARIO=patrol
CMD ["sh", "-c", "cyberdyne run -s $CYBERDYNE_SCENARIO -d 8080 -r 1.0"]
