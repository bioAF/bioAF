# Minimal local runner for test_author_execution_linux.py, not the production image.
FROM postgres:16
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap python3 ca-certificates && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /work /outputs && chown nobody:nogroup /work /outputs
RUN ln -s /usr/bin/python3 /usr/local/bin/python
USER nobody
