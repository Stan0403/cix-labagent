"""Offline character n-gram TF-IDF retrieval; no embedding API or fake citations."""
import hashlib
import math
import re
import threading
from collections import Counter
from pathlib import Path


def tokens(text: str) -> Counter:
    terms = re.findall(r"[a-z0-9_]+", text.lower())
    # English words and URLs must not contribute arbitrary letter bigrams.
    for segment in re.findall(r"[\u3400-\u9fff]+", text):
        terms += [segment[i:i + 2] for i in range(max(0, len(segment) - 1))]
        if len(segment) == 1:
            terms.append(segment)
    return Counter(terms)


class Knowledge:
    def __init__(self, directory: Path):
        self.directory = directory
        self._lock = threading.RLock()
        directory.mkdir(parents=True, exist_ok=True)
        self.chunks = []
        self.index()

    def index(self):
        with self._lock:
            return self._index()

    def _index(self):
        chunks = []
        for path in sorted(self.directory.glob("*")):
            if path.suffix.lower() not in {".md", ".txt"} or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            # Bounded overlapping chunks preserve traceable line numbers.
            lines = text.splitlines()
            for start in range(0, len(lines), 14):
                content = "\n".join(lines[start:start + 20]).strip()
                if content:
                    chunks.append({"id": hashlib.sha256((path.name + str(start)).encode()).hexdigest()[:12],
                                   "source": path.name, "line": start + 1, "text": content,
                                   "tokens": tokens(content)})
        document_frequency = Counter()
        for chunk in chunks:
            document_frequency.update(chunk["tokens"].keys())
        self.idf = {t: math.log((1 + len(chunks)) / (1 + n)) + 1
                    for t, n in document_frequency.items()}
        for chunk in chunks:
            chunk["vector"] = {t: (1 + math.log(n)) * self.idf[t] for t, n in chunk["tokens"].items()}
            chunk["norm"] = math.sqrt(sum(v * v for v in chunk["vector"].values()))
        self.chunks = chunks
        return len(chunks)

    def search(self, query: str, limit=4, sources=None):
        with self._lock:
            return self._search(query, limit, sources)

    def _search(self, query: str, limit=4, sources=None):
        query_counts = tokens(query)
        vector = {t: (1 + math.log(n)) * self.idf[t] for t, n in query_counts.items() if t in self.idf}
        norm = math.sqrt(sum(v * v for v in vector.values()))
        if not norm:
            return []
        scored = []
        for chunk in self.chunks:
            if sources is not None and chunk["source"] not in sources:
                continue
            score = sum(v * chunk["vector"].get(t, 0) for t, v in vector.items()) / (norm * chunk["norm"])
            if score >= 0.04:
                scored.append({k: chunk[k] for k in ("id", "source", "line", "text")} | {"score": round(score, 4)})
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]

    def ingest(self, filename: str, text: str):
        name = Path(filename.replace("\\", "/")).name
        if Path(name).suffix.lower() not in {".md", ".txt"}:
            raise ValueError("知识库当前仅接收 UTF-8 .md/.txt，请先从 PDF/Word 导出文本")
        # A generated name prevents path traversal and accidental source overwrite.
        destination = self.directory / (hashlib.sha256(text.encode()).hexdigest()[:12] + "_" + name)
        with self._lock:
            destination.write_text(text, encoding="utf-8")
            self.index()
        return {"source": destination.name, "chunks": len(self.chunks)}
