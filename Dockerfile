# Refute runs code it did not write: the scripts the model writes and the
# solution under test. The guard and the sandbox catch mistakes, not attacks.
# Run it here, with no network, when what you are testing came from somewhere
# you do not trust:
#
#   docker build -t refute .
#   docker run --rm --network none -v "$PWD:/work" refute \
#       run /work/statement.md /work/solution.py --no-ai-explain --offline
#
# A run that has to reach the model cannot use --network none. Record the
# model output once with the network on and a cache directory you keep, then
# replay it offline:
#
#   docker run --rm -e REFUTE_API_KEY -e REFUTE_MODEL -e REFUTE_BASE_URL \
#       -v "$PWD:/work" refute run /work/statement.md /work/solution.py \
#       --cache-dir /work/.refute_cache
#   docker run --rm --network none -v "$PWD:/work" refute \
#       run /work/statement.md /work/solution.py --cache-dir /work/.refute_cache --offline
FROM python:3.12-slim

# g++ is here so .cpp targets can be compiled. Drop it to make the image
# smaller if you only ever test Python.
RUN apt-get update \
    && apt-get install --no-install-recommends -y g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /refute
COPY pyproject.toml README.md LICENSE ./
COPY refute ./refute
COPY examples ./examples
COPY bench ./bench
RUN pip install --no-cache-dir .

# Nothing in the image needs to write to it. The sandbox makes its own
# temporary directory per run, and the output goes to the working directory.
RUN useradd --create-home --uid 10001 refute
USER refute
WORKDIR /work

ENTRYPOINT ["refute"]
CMD ["demo"]
