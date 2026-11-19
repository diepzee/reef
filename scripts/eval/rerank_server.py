"""A reranking service on this machine, for measuring search. Not for production.

Run as::

    uv run --with sentence-transformers python scripts/eval/rerank_server.py

It serves ``POST /v1/rerank`` on ``127.0.0.1:8787`` with the same model the
experiment of 18 November 2026 measured, so the product's reranking can be
measured end to end::

    export REEF_RERANK_URL=http://127.0.0.1:8787
    REEF_RERANK_SECONDS=60 python scripts/eval/run.py --mode rerank

It listens on this machine only. Its weights are downloaded once from
Hugging Face. No text leaves the machine.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: The models it can serve, by short name.
MODELS = {"bge-m3": "BAAI/bge-reranker-v2-m3"}


def handler_for(encoder) -> type[BaseHTTPRequestHandler]:
    """Build a request handler around one loaded cross-encoder.

    :param encoder: a ``sentence_transformers.CrossEncoder``
    :returns: the handler class
    """

    class Handler(BaseHTTPRequestHandler):
        """Answers the rerank protocol, and nothing else."""

        def do_POST(self) -> None:
            """Score each document against the query."""
            if self.path != "/v1/rerank":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            documents = body["documents"]
            scores = encoder.predict(
                [(body["query"], text) for text in documents],
                batch_size=16,
                show_progress_bar=False,
            )
            answer = json.dumps(
                {
                    "results": [
                        {"index": i, "relevance_score": float(score)}
                        for i, score in enumerate(scores)
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def log_message(self, format: str, *args: object) -> None:
            """Log nothing: requests carry engram text."""

    return Handler


def main() -> None:
    """Load the model and serve until stopped."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", choices=sorted(MODELS), default="bge-m3")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    from sentence_transformers import CrossEncoder

    encoder = CrossEncoder(MODELS[args.model], max_length=512)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(encoder))
    print(f"reranking on http://127.0.0.1:{args.port}/v1/rerank", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
