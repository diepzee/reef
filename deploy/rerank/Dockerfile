# reef-rerank: the reranker search asks to put its first results in order.
#
# Deployed by hand, not from git: `railway up deploy/rerank --service
# reef-rerank`. See docs/runbook.md, "Model services".
#
# The model is BAAI's own bge-reranker-v2-m3 (Apache 2.0), at a pinned
# revision, converted here to llama.cpp's format at 8-bit. Converting it
# ourselves means the weights come from BAAI and nobody in between.

# --- 1. Convert the official weights -----------------------------------------
FROM ghcr.io/ggml-org/llama.cpp:full-b12464 AS convert

ARG MODEL=BAAI/bge-reranker-v2-m3
ARG REVISION=953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e

# The image's protobuf does not import on its Python 3.14; a newer one does.
RUN pip install --quiet --break-system-packages "protobuf>=6" "huggingface_hub>=0.25"
RUN python3 -c "from huggingface_hub import snapshot_download; \
snapshot_download('${MODEL}', revision='${REVISION}', local_dir='/model', \
allow_patterns=['*.json', 'model.safetensors', 'sentencepiece.bpe.model'])"
RUN python3 /app/convert_hf_to_gguf.py --outtype q8_0 \
    --outfile /reranker.gguf /model

# --- 2. Serve it ---------------------------------------------------------------
FROM ghcr.io/ggml-org/llama.cpp:server-b12464

COPY --from=convert /reranker.gguf /models/reranker.gguf

# One thread per core the service may use. llama.cpp otherwise starts one
# per core of the host, many more than the service is given.
ENV LLAMA_ARG_THREADS=8

# Reranking only. Listens on IPv6 and IPv4 for Railway's private network.
# Eight requests at once, each up to 1,024 tokens: a passage of 2,000
# characters and the query fit.
CMD ["-m", "/models/reranker.gguf", "--reranking", \
     "--host", "::", "--port", "8080", \
     "-c", "8192", "-np", "8", "-b", "8192", "-ub", "1024"]
