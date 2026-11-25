# nutanix-docs-rag

Build a local, searchable copy of public Nutanix documentation for your own AI agent.

The script downloads public product guides from the Nutanix Support Portal anonymously (no sign-in),
together with the public knowledge-base (KB) articles those guides link to. It writes them as plain-text
passages that you can load into any vector store or search engine your agent already uses.

This is sample code, provided as is and without support. It uses the endpoints the Support Portal website
itself calls. Those are not a published, versioned API and may change without notice.

## Quick start

```bash
pip install -r requirements.txt

# See which product names the portal files guides under
python nutanix_docs_sample.py products

# Download the latest guide for a product, plus the KB articles it links to
python nutanix_docs_sample.py download --product "Nutanix Kubernetes Platform" --out nkp
```

The download writes `nkp/passages.jsonl`. It holds one JSON object per passage:

```json
{"id": "...#0", "url": "https://portal.nutanix.com/...", "title": "...", "product": "...", "version": "...", "text": "..."}
```

Every passage keeps its source URL, so your agent can cite where an answer came from.

## Optional: a ready-to-search local index

If you don't already have a vector store, the script can build a local [Chroma](https://www.trychroma.com/)
index with any [sentence-transformers](https://www.sbert.net/) embedding model:

```bash
pip install chromadb sentence-transformers
python nutanix_docs_sample.py index  --out nkp --model ibm-granite/granite-embedding-small-english-r2
python nutanix_docs_sample.py search --out nkp --model ibm-granite/granite-embedding-small-english-r2 "upgrade prerequisites"
```

Use the same model for `index` and `search`.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--product NAME` | every product | Repeat to download several products |
| `--versions N` | 1 | How many of the newest releases of each guide to keep |
| `--limit N` | none | At most N guides per product (useful for a quick test) |
| `--gap SECONDS` | 0.5 | Pause between requests to the portal |
| `--out DIR` | `nutanix-docs` | Where the download and the index are written |

## Keeping it fresh

Run `download` again whenever you like. A guide whose portal date hasn't changed is skipped. KB articles
carry no change date, so they are rechecked weekly. An article that is no longer public is dropped.
Then re-run `index`.

## Only public content

Only documents the portal marks as public are downloaded, and only KB articles marked for the public
knowledge base. Nothing is sent to the portal except the page requests themselves.

## Also useful for agents

- Nutanix v4 API specifications (OpenAPI): https://developers.nutanix.com
- Developer documentation and examples: https://www.nutanix.dev
