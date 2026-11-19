# reef-embed: the embedding service the worker and search ask for vectors.
#
# Deployed by hand, not from git: `railway up deploy/embed --service
# reef-embed`. See docs/runbook.md, "Model services".
#
# The model is EmbeddingGemma 300m (Gemma terms of use), as Ollama's library
# serves it: the build the retrieval experiment of 18 November 2026 measured.
# It is pulled at build time, so the service needs no volume and starts with
# the model in place.

FROM ollama/ollama:0.40.14

# The model is re-created under its own name with a thread limit.
# llama.cpp otherwise starts one thread per core of the host (48 on
# Railway's), far more than the service may use, and the threads stall each
# other: an 11-token request took over a minute. The name stays the same,
# because reef chooses how to phrase a query by it.
RUN OLLAMA_HOST=127.0.0.1:11434 sh -c \
    'ollama serve & pid=$!; \
     until ollama list >/dev/null 2>&1; do sleep 1; done; \
     ollama pull embeddinggemma:300m && \
     printf "FROM embeddinggemma:300m\nPARAMETER num_thread 8\n" > /tmp/Modelfile && \
     ollama create embeddinggemma:300m -f /tmp/Modelfile && kill $pid'

# Listens on IPv6 and IPv4 for Railway's private network. The model stays
# loaded, so no search waits for it to load, and only it is ever loaded.
ENV OLLAMA_HOST=[::]:11434 \
    OLLAMA_KEEP_ALIVE=-1 \
    OLLAMA_MAX_LOADED_MODELS=1 \
    OLLAMA_NUM_PARALLEL=4

CMD ["serve"]
