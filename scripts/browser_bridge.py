"""Test-only stdio transport for a real FastAPI TestClient when sandbox loopback is unavailable.

No endpoint is mocked. Only the HTTP socket transport is replaced, like the backend test suite.
"""
import base64
import json
import sys

from fastapi.testclient import TestClient

from labagent.config import ROOT, Settings
from labagent.main import create_app
from labagent.models import uid


def main():
    settings = Settings(data_dir=ROOT / "test-results" / "browser-data" / uid())
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as client:
        for line in sys.stdin:
            request = None
            try:
                request = json.loads(line)
                response = client.request(request["method"], request["path"],
                                          headers=request.get("headers", {}),
                                          content=base64.b64decode(request.get("body", "")))
                headers = {key: value for key, value in response.headers.items()
                           if key not in {"content-length", "content-encoding", "transfer-encoding", "connection"}}
                reply = {"id": request["id"], "status": response.status_code, "headers": headers,
                         "body": base64.b64encode(response.content).decode()}
            except Exception as exc:
                reply = {"id": request.get("id") if isinstance(request, dict) else None,
                         "error": type(exc).__name__ + ": " + str(exc)}
            print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main()
