# nutanix-docs-rag

Build a local, searchable copy of public Nutanix documentation for your own AI agent.

The script downloads public product guides from the Nutanix Support Portal anonymously (no sign-in),
together with the public knowledge-base (KB) articles those guides link to. It writes them as plain-text
passages that you can load into any vector store or search engine your agent already uses.

It also downloads the published OpenAPI files for the Nutanix v4 APIs from developers.nutanix.com, which
an agent can read directly to write API calls and tests.

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

## API reference (OpenAPI)

```bash
# The newest released version of every v4 API area (about 18 files)
python nutanix_docs_sample.py api-specs --out nkp

# Only some areas, the two newest versions, plus Postman collections
python nutanix_docs_sample.py api-specs --api vmm --api clustermgmt --versions 2 --postman --out nkp
```

The files are written to `nkp/api-specs/<area>-<version>.yaml`, for example `vmm-v4.3.yaml` (virtual
machines) or `clustermgmt-v4.4.yaml` (clusters). Only released versions are downloaded; API areas that so
far exist only as previews (currently storage and tenancy) are skipped. The full set is about 21 MB of
OpenAPI, or about 110 MB with the Postman collections. Give your agent the files for the areas your tests
cover rather than all of them.

These files are a published source, unlike the portal endpoints used for the guides.

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
| `--product NAME` | every product | `download`: repeat to download several products |
| `--api AREA` | every API area | `api-specs`: repeat to choose areas, e.g. `vmm`, `clustermgmt` |
| `--postman` | off | `api-specs`: also download Postman collections |
| `--versions N` | 1 | How many of the newest releases of each guide or API to keep |
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

- Developer documentation and examples: https://www.nutanix.dev
