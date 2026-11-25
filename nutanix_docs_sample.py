#!/usr/bin/env python3
"""Build a local, searchable copy of public Nutanix documentation for an AI agent.

Sample code, provided as is and unsupported. It downloads public product
guides from the Nutanix Support Portal anonymously (no sign-in), plus the
public knowledge-base (KB) articles those guides link to, and writes them as
text passages an agent can search.

It uses the endpoints the Support Portal website itself calls. They are not
a published, versioned API and may change without notice.

It can also download the published OpenAPI files for the Nutanix v4 APIs
from developers.nutanix.com (``api-specs``), which an agent can read
directly to write API calls and tests.

Steps:

  1. download  -> passages.jsonl (one JSON object per passage: url, title,
                  product, version, text). Plain data: load it into any
                  vector store or search engine your agent already uses.
  api-specs    -> api-specs/<area>-<version>.yaml (OpenAPI), optionally
                  with Postman collections.
  2. index     -> optional: a local Chroma collection built from
                  passages.jsonl with any sentence-transformers model.

Examples:

  pip install httpx beautifulsoup4
  python nutanix_docs_sample.py products
  python nutanix_docs_sample.py download --product "Nutanix Kubernetes Platform" --out nkp
  python nutanix_docs_sample.py api-specs --out nkp

  pip install chromadb sentence-transformers
  python nutanix_docs_sample.py index --out nkp --model <embedding model>
  python nutanix_docs_sample.py search --out nkp --model <embedding model> "upgrade prerequisites"

Be considerate: requests are spaced (``--gap``, default half a second), and
an unchanged guide is skipped on the next run (the portal's document date is
compared), so a re-run downloads only what changed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

PORTAL = "https://portal.nutanix.com"
GUIDE_URL = PORTAL + "/page/documents/details?targetId="
KB_URL = PORTAL + "/kb/"
KB_REF = re.compile(r"\bKB[- ]?(\d{4,6})\b")
SKIP_TITLE = re.compile(r"API Reference|This Document Has Been Removed", re.I)
PASSAGE_CHARS = 1500
DEVELOPERS = "https://developers.nutanix.com"
GA_VERSION = re.compile(r"v(\d+)\.(\d+)")  # v4.4 yes; v4.0.b2 (preview) no


class Portal:
    def __init__(self, gap: float) -> None:
        self.http = httpx.Client(
            base_url=PORTAL, timeout=60, follow_redirects=True,
            headers={"User-Agent": "nutanix-docs-sample/1.0", "Accept": "*/*"},
        )
        self.gap, self.last = gap, 0.0

    def get(self, path: str, follow_redirects: bool = True) -> httpx.Response | None:
        for attempt in range(4):
            wait = self.gap - (time.monotonic() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            try:
                r = self.http.get(path, follow_redirects=follow_redirects)
            except httpx.HTTPError:
                r = None
            # Only "busy" answers are retried. The portal answers 500 for a KB
            # article it will not serve publicly; that is final, not a retry.
            if r is not None and r.status_code not in (429, 502, 503, 504):
                return r
            time.sleep(10 * (attempt + 1))
        return None

    def products(self) -> list[str]:
        r = self.get("/api/v1/documents/distincts?fieldName=softwareType")
        data = r.json() if r is not None and r.status_code == 200 else []
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [])
        return sorted({str(v).strip() for v in data if str(v).strip()}, key=str.casefold)

    def guides(self, product: str) -> list[dict]:
        r = self.get(f"/api/v1/documents?softwareType={quote(product)}&documentPermission=public&limit=5000")
        return r.json() if r is not None and r.status_code == 200 else []

    def guide_html(self, page_view: str) -> str:
        r = self.get(f"/api/v1/documents/{page_view}/html")
        return r.text if r is not None and r.status_code == 200 else ""

    def kb(self, number: str) -> dict:
        # /kb/<number> redirects to the article's internal id (targetId=...).
        r = self.get(f"/kb/{number}", follow_redirects=False)
        found = re.search(r"targetId=([A-Za-z0-9]+)", r.headers.get("location", "")) if r is not None else None
        if not found:
            return {}
        r = self.get(f"/api/v1/articles/{found.group(1)}")
        if r is None or r.status_code != 200:
            return {}
        art = r.json()
        # Only articles the portal marks for the public knowledge base.
        if not isinstance(art, dict) or art.get("isVisibleInPkb") is not True or art.get("isDeleted"):
            return {}
        return art


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for junk in soup.find_all(["script", "style", "noscript"]):
        junk.decompose()
    for tag in soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br"]):
        tag.append("\n")
    text = re.sub(r"[ \t]+", " ", soup.get_text(" "))
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def passages(text: str) -> list[str]:
    """Split on paragraphs into passages of about PASSAGE_CHARS characters."""
    out, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) > PASSAGE_CHARS:
            out.append(cur.strip())
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        out.append(cur.strip())
    return [p for p in out if len(p) > 40]


def latest(docs: list[dict], versions: int) -> list[dict]:
    """The newest ``versions`` releases of each guide, public documents only."""
    families: dict[str, list[dict]] = {}
    for d in docs:
        if not d.get("portalFullPageView") or SKIP_TITLE.search(d.get("title") or ""):
            continue
        if str(d.get("documentPermission") or "").lower() != "public":
            continue
        families.setdefault(d.get("topic") or d.get("title"), []).append(d)

    def vkey(v):
        return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", str(v or "0"))]

    chosen = []
    for fam in families.values():
        fam.sort(key=lambda d: (vkey(d.get("softwareVersion")), d.get("DC_Date") or ""), reverse=True)
        seen: list = []
        for d in fam:
            if d.get("softwareVersion") not in seen:
                seen.append(d.get("softwareVersion"))
                chosen.append(d)
            if len(seen) >= versions:
                break
    return chosen


def download(args) -> None:
    out = Path(args.out)
    docs_dir = out / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    portal = Portal(args.gap)
    products = args.product or portal.products()
    kb_refs: set[str] = set()

    for product in products:
        chosen = latest(portal.guides(product), args.versions)[: args.limit or None]
        print(f"{product}: {len(chosen)} guides", flush=True)
        for d in chosen:
            doc_id = re.sub(r"[^A-Za-z0-9._-]", "_", d.get("id") or d["portalFullPageView"].split(":")[0])
            path = docs_dir / f"{doc_id}.json"
            if path.exists() and json.loads(path.read_text()).get("date") == d.get("DC_Date"):
                record = json.loads(path.read_text())  # unchanged since the last run
            else:
                text = html_to_text(portal.guide_html(d["portalFullPageView"]))
                if not text:
                    continue
                record = {
                    "url": GUIDE_URL + d["portalFullPageView"].split(":")[0],
                    "title": d.get("title"), "product": product,
                    "version": d.get("softwareVersion"), "date": d.get("DC_Date"), "text": text,
                }
                path.write_text(json.dumps(record))
            kb_refs |= set(KB_REF.findall(record["text"]))

    print(f"KB articles linked from these guides: {len(kb_refs)}", flush=True)
    for n in sorted(kb_refs, key=int):
        path = docs_dir / f"KB-{n}.json"
        if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
            continue  # KB articles carry no change date: recheck weekly
        art = portal.kb(n)
        if not art:
            path.unlink(missing_ok=True)  # no longer public: drop it
            continue
        sections = [(label, html_to_text(str(art.get(field) or ""))) for label, field in (
            ("Summary", "summary"), ("Description", "description"),
            ("Solution", "solution"), ("Additional Info", "additionalInfo"))]
        text = "\n\n".join(f"{label}\n\n{body}" for label, body in sections if body)
        path.write_text(json.dumps({
            "url": KB_URL + n, "title": f"KB-{n} {art.get('title') or ''}".strip(),
            "product": "KB", "version": "", "date": art.get("lastModifiedDate"), "text": text,
        }))

    count = 0
    with (out / "passages.jsonl").open("w", encoding="utf-8") as fh:
        for path in sorted(docs_dir.glob("*.json")):
            rec = json.loads(path.read_text())
            for i, chunk in enumerate(passages(rec["text"])):
                fh.write(json.dumps({**{k: rec[k] for k in ("url", "title", "product", "version")},
                                     "id": f"{path.stem}#{i}", "text": chunk}) + "\n")
                count += 1
    print(f"wrote {count} passages to {out / 'passages.jsonl'}")


def api_specs(args) -> None:
    """The published OpenAPI files for every Nutanix v4 API area."""
    out = Path(args.out) / "api-specs"
    out.mkdir(parents=True, exist_ok=True)
    http = httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": "nutanix-docs-sample/1.0"})
    namespaces = http.get(f"{DEVELOPERS}/api/v1/namespaces/").json().get("namespaces", [])
    names = args.api or sorted(n["name"] for n in namespaces if n.get("name"))
    saved = []
    for name in names:
        rows = http.get(f"{DEVELOPERS}/api/v1/namespaces/{quote(name)}/versions/").json().get("versions", [])
        ga = sorted(
            (r for r in rows if GA_VERSION.fullmatch(str(r.get("version")))),
            key=lambda r: tuple(int(x) for x in GA_VERSION.fullmatch(r["version"]).groups()),
            reverse=True,
        )[: args.versions]
        for row in ga:
            version = row["version"]
            spec = http.get(row.get("link") or f"{DEVELOPERS}/api/v1/namespaces/{name}/versions/{version}/yaml")
            if spec.status_code != 200:
                print(f"{name} {version}: not published ({spec.status_code})", flush=True)
                continue
            (out / f"{name}-{version}.yaml").write_bytes(spec.content)
            saved.append(f"{name}-{version}.yaml")
            if args.postman and row.get("postmanCollectionLink"):
                coll = http.get(row["postmanCollectionLink"])
                if coll.status_code == 200:
                    (out / f"{name}-{version}.postman.json").write_bytes(coll.content)
            print(f"{name} {version}", flush=True)
            time.sleep(args.gap)
    print(f"wrote {len(saved)} OpenAPI files to {out}")


def _collection(args):
    import chromadb
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    client = chromadb.PersistentClient(path=str(Path(args.out) / "chroma"))
    return client.get_or_create_collection(
        "nutanix_docs", embedding_function=SentenceTransformerEmbeddingFunction(model_name=args.model),
        metadata={"hnsw:space": "cosine"},
    )


def index(args) -> None:
    rows = [json.loads(line) for line in (Path(args.out) / "passages.jsonl").open(encoding="utf-8")]
    col = _collection(args)
    for i in range(0, len(rows), 256):
        batch = rows[i : i + 256]
        col.upsert(
            ids=[r["id"] for r in batch], documents=[r["text"] for r in batch],
            metadatas=[{k: r[k] or "" for k in ("url", "title", "product", "version")} for r in batch],
        )
        print(f"  {min(i + 256, len(rows))}/{len(rows)}", flush=True)


def search(args) -> None:
    res = _collection(args).query(query_texts=[args.query], n_results=args.n)
    for doc, meta in zip(res["documents"][0], res["metadatas"][0], strict=True):
        print(f"\n{meta['title']}  {meta['url']}\n{doc[:400]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("products", help="list the product names the portal files guides under")
    d = sub.add_parser("download", help="download public guides and their linked KB articles")
    d.add_argument("--product", action="append", help="product name (repeatable); default: every product")
    d.add_argument("--versions", type=int, default=1, help="newest releases of each guide to keep")
    d.add_argument("--limit", type=int, default=0, help="at most this many guides per product (testing)")
    d.add_argument("--gap", type=float, default=0.5, help="seconds between requests")
    d.add_argument("--out", default="nutanix-docs")
    a = sub.add_parser("api-specs", help="download the Nutanix v4 API OpenAPI files")
    a.add_argument("--api", action="append", help="API area, e.g. vmm or clustermgmt (repeatable); default: all")
    a.add_argument("--versions", type=int, default=1, help="newest released versions of each API to keep")
    a.add_argument("--postman", action="store_true", help="also download each version's Postman collection")
    a.add_argument("--gap", type=float, default=0.5, help="seconds between downloads")
    a.add_argument("--out", default="nutanix-docs")
    for name in ("index", "search"):
        p = sub.add_parser(name)
        p.add_argument("--out", default="nutanix-docs")
        p.add_argument("--model", required=True, help="a sentence-transformers embedding model")
    sub.choices["search"].add_argument("query")
    sub.choices["search"].add_argument("-n", type=int, default=5)
    args = ap.parse_args()
    if args.cmd == "products":
        print("\n".join(Portal(0.5).products()))
    else:
        {"download": download, "api-specs": api_specs, "index": index, "search": search}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
