# Reading scanned documents for Reef: options, costs and data terms

Research date: 21 November 2026. All pages were read on that date. No sign-ups, no personal data entered, no documents sent anywhere.

Scope: scans only (photos, image-only PDFs: medical letters, insurance policies, house plans). Text PDFs stay local. Privacy outranks cost.

## How to read this report

- Links go to the vendor page I read. Where a page was summarised by my fetch tool rather than read in full, I say so.
- Where I found no official statement, I write "not stated" instead of guessing.
- Items marked **(my estimate)** are not from a vendor. They are my own arithmetic or judgment and need a test on real documents.

## Short answer

- Best hosted route for privacy: **Gemini on Vertex AI, pinned to the EU multi-region**. It is the only Gemini route with a documented EU processing guarantee. The cost is about 10% extra and a Google Cloud project to set up.
- The Gemini API (AI Studio, paid tier) does not train on your data, but I found no EU residency promise. Its terms say data "may be stored transiently or cached in any country".
- Mistral OCR is an EU company, hosts in the EEA by default, and is a cheap purpose-built "page to text" API. It reportedly logs API calls for 30 days unless you get zero data retention (ZDR); I did not confirm that on its own pages.
- Anthropic offers no EU inference option today (only "US" or "global"). OpenAI offers EU processing, but only after contacting sales, and images need extra approval.
- A local CPU path is realistic for clean printed letters (Tesseract, PaddleOCR, docTR). It is weak for handwriting and house plans. Small vision-language models on CPU are slow and not proven enough to be the default.

---

## 1. Gemini API (Google AI Studio, paid tier)

### 1.1 Models and prices

Source: [Gemini API pricing](https://aistudio.google.com/docs/pricing) (the old `ai.google.dev/gemini-api/docs/pricing` URL redirects here; the page shows no publication date). Prices are US dollars per 1 million tokens, Standard tier. The page was summarised by my fetch tool; the same prices appear on the [Vertex pricing page](https://cloud.google.com/vertex-ai/generative-ai/pricing), which I read as raw HTML.

| Model | Input | Output (includes thinking) | Note |
|---|---|---|---|
| Gemini 3.5 Flash-Lite | $0.30 | $2.50 | Cheap option. Text, image, video, audio at one rate. |
| Gemini 3.1 Flash-Lite | $0.25 | $1.50 | Older, cheaper output. |
| Gemini 3.8 Flash | $0.75 until 31 Dec 2026, then $1.50 | $3.75, then $7.50 | Introductory price ends 1 Jan 2027. |
| Gemini 4 Hydrogen | $1.50 | $7.50 | Flash class. |
| Gemini 4 Carbon | $2.00 until 30 Nov 2026, then $4.00 | $10.00, then $20.00 | Pro class. |
| Gemini 4 Neon | $3.00 | $15.00 | Pro class. |

Notes:

- I did not benchmark any of these models for OCR.
- Gemini 2.5 Flash and 2.5 Pro are marked deprecated. The page says 2.5 Flash requests are rerouted to 3.5 Flash-Lite on 1 December 2026.
- Output tokens include "thinking" tokens. For OCR, turn thinking down or off, or the output bill grows.
- The pricing page has no separate PDF price. PDFs use the same input rate as images.

### 1.2 How a page is counted in tokens

- **PDF pages.** About 280 tokens per page at `low`, **560 at `medium` (default)**, 1,120 at `high` on Gemini 3 and later. Gemini 2.5 uses a flat 258. Each page is rendered as an image; text embedded in the PDF is extracted at no extra token charge. Limits: 50 MB and 1,000 pages per file. Source: [Document processing](https://aistudio.google.com/docs/document-processing).
- **Photos.** On Gemini 3 and later the default is 1,120 tokens per image, with 280 / 560 / 1,120 / 2,240 set by `media_resolution`. Older models use 258 tokens per 768 by 768 tile. Sources: [Image understanding](https://aistudio.google.com/docs/image-understanding), [Vertex image understanding](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/image-understanding).
- Fine print (small print on forms, plans) needs `high` resolution. Google says higher settings raise token use and latency.

### 1.3 Cost per scanned page (my estimate)

Assumptions **(my estimate)**: 1,120 input tokens for the page at `high`, plus about 100 tokens of instructions; about 1,200 output tokens for a dense letter; thinking off. Real documents will vary, so measure on a sample.

| Model | Per page | Per 100 pages |
|---|---|---|
| Gemini 3.5 Flash-Lite ($0.30 / $2.50) | about $0.0034 | **about $0.34** |
| Gemini 3.8 Flash, intro price ($0.75 / $3.75) | about $0.0054 | about $0.54 |
| Gemini 3.8 Flash, from 1 Jan 2027 ($1.50 / $7.50) | about $0.011 | about $1.08 |
| Gemini 4 Hydrogen ($1.50 / $7.50) | about $0.011 | about $1.08 |
| Gemini 4 Carbon, to 30 Nov 2026 ($2 / $10) | about $0.014 | about $1.44 |
| Gemini 4 Carbon, from 1 Dec 2026 ($4 / $20) | about $0.029 | about $2.88 |

Output tokens dominate. A mostly empty page (a plan with a few labels) costs much less.

### 1.4 Data use on the paid tier

Primary source: [Gemini API Additional Terms of Service](https://aistudio.google.com/docs/terms) (old URL `ai.google.dev/gemini-api/terms` redirects). Read 21 November 2026, summarised by my fetch tool.

- **Training and product improvement.** The "Paid Services" section says Google does not use your prompts and responses to improve its products. The [pricing page](https://aistudio.google.com/docs/pricing) also marks every paid-tier model "No" for "Used to improve our products".
- **Human review.** The Paid Services section does not mention human review for product improvement. Review for abuse monitoring is covered below.
- **Retention.** The Paid Services section says Google "logs prompts and responses for a limited period of time" for detecting Prohibited Use Policy violations and for legal disclosures, with no number. The [Abuse monitoring page](https://ai.google.dev/gemini-api/docs/usage-policies) gives it: **prompts, context and outputs are kept 55 days**. Flagged content can be read by "authorized Google employees"; the data is used only for policy enforcement, not to train models other than those used for policy enforcement.
- **Separate logs in your project.** The [Data logging and sharing page](https://aistudio.google.com/docs/logs-policy) says logging is on by default for the Interactions API, and for Generate Content calls **in projects created on or after 26 October 2026**. These logs are private to your Google Cloud project, default to 55 days (options 7, 14, 28, 55), and are not used for product improvement unless you share a dataset. You can switch logging off on the Logs page in AI Studio. A new project for Reef falls under the new default, so check this setting.
- **Where data is processed.** The terms say prompts and responses "may be stored transiently or cached in any country in which Google or its agents maintain facilities". I found no EU residency or region-pinning option for the Gemini API in official pages. A developer-forum answer says to use Vertex AI for residency ([forum thread, unofficial](https://discuss.ai.google.dev/t/gemini-api-data-zones/78584)). Treat the Gemini API as not EU-resident.
- **Contract.** Prompts and responses are processed under Google's Data Processing Addendum (stated in the same terms page).
- **EEA users.** The terms say the paid-service data terms apply to users in the EEA, Switzerland and the UK, including on unpaid quota, and that API clients serving these users must use Paid Services only.

### 1.5 Free tier

Confirmed. The [terms page](https://aistudio.google.com/docs/terms) says, for Unpaid Services, that Google uses submitted content and responses to "provide, improve, and develop" its services including machine learning, that "human reviewers may read, annotate, and process your API input and output" (after disconnecting it from your account, API key and project), and "Do not submit sensitive, confidential, or personal information to the Unpaid Services." The [pricing page](https://aistudio.google.com/docs/pricing) marks free-tier use "Yes" for "Used to improve our products". **The free tier is not acceptable for medical letters or insurance policies.** Reef should refuse to run on a key that is not billed.

---

## 2. Gemini on Google Cloud Vertex AI

Google has renamed the product in places ("Agent Platform" in page titles). The docs still live under `vertex-ai/generative-ai`.

### 2.1 Price differences

Source: [Vertex pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing) (read 21 November 2026 as raw HTML, because the summariser could not render it).

- Base prices match the Gemini API: for example Gemini 3.5 Flash-Lite $0.30 in / $2.50 out, Gemini 4 Hydrogen $1.50 / $7.50.
- **Non-global (regional or multi-region) endpoints cost 10% more**: for example Flash-Lite $0.33 / $2.75, Hydrogen $1.65 / $8.25. Global endpoints are standard price.
- So the EU-pinned cost per 100 pages is about $0.37 for Flash-Lite and about $1.19 for Gemini 3.8 Flash from 2027 **(my estimate, from the table in 1.3 plus 10%)**.

### 2.2 Data use, retention, residency

Source: [Data governance](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/data-governance) and [Data residency](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/data-residency), read 21 November 2026 (summarised by my fetch tool).

- **Training.** "Google won't use your data to train or fine-tune any AI/ML models without your prior permission or instruction."
- **Caching.** Implicit caching is on by default for supported Gemini models. In-memory cache is deleted within 24 hours; durable cache within 24 hours. Setting `disableCache` to `true` (needs `roles/aiplatform.admin`) turns all caching off.
- **Abuse-monitoring logging.** Prompts may be logged for abuse detection for customers under the Google Cloud Platform Terms of Service. The page gives no retention period for this.
- **Zero data retention (ZDR) for Google models.** The page lists steps: keep request-response logging off (the default), turn caching off, avoid Grounding with Google Search, and a few other features. For "advanced AI" features it says ZDR "may not be possible". I found no request form that exempts Gemini from abuse-monitoring logging.
- **EU residency.** The residency page lists ML processing in the **EU multi-region** for Gemini 3.8 / 3.7 / 3.6 Flash, **3.5 Flash-Lite**, 3.1 Flash-Lite and 2.5 Flash-Lite and Pro. Single regions are listed for only a few models (3.5 Flash: Germany `europe-west3` and UK `europe-west2`). Stored data stays in the location you choose. **Requests to the global endpoint "don't provide any data residency guarantees".** The part of the page my tool returned did not show Gemini 4 models on the EU table; check the live table before choosing one.
- In practice: use the `eu` multi-region endpoint, not `global`.

### 2.3 Setup effort compared with the Gemini API

- **Gemini API:** create an AI Studio key, enable billing on the project, call with the key. Minutes.
- **Vertex AI:** create a Google Cloud project, link billing, enable the API, create a service account with the Vertex AI User role, and authenticate (key file or workload identity). On Railway that means storing a credential as a secret. Then set the EU endpoint and turn off caching if you want ZDR. About an hour for someone who knows Google Cloud, more otherwise. **(my estimate)**
- Code differences are small with the Google GenAI SDK (client created in Vertex mode with project and location). The cost is the account side, not the code. **(my estimate)**

---

## 3. Alternatives

### 3.1 Google Cloud Document AI and Cloud Vision OCR

- **Price.**
  - Document AI Enterprise Document OCR: free for the first 1,000 pages a month, then **$1.50 per 1,000 pages** ($0.15 per 100). Layout Parser (which chunks) is $10 per 1,000 pages. Source: [Document AI pricing](https://cloud.google.com/document-ai/pricing) (raw HTML, 21 November 2026).
  - Cloud Vision Text Detection and Document Text Detection: free for the first 1,000 units a month, then $1.50 per 1,000 units. Source: [Vision pricing](https://cloud.google.com/vision/pricing).
- **Residency.** Document AI supports `us` and `eu` multi-region locations, fixed when the processor is created. Source: [Document AI regions](https://docs.cloud.google.com/document-ai/docs/regions) (via search summary; I did not open the page in full).
- **Retention and training.** I did not find a Document AI page that states these. Check the Cloud Data Processing Addendum before relying on it. Not verified.
- **Quality.** Text recognition without reasoning. Good for printed text. I did not verify Dutch or handwriting results.
- **Fit.** Cheapest hosted option with an EU location, but it needs the same Google Cloud setup as Vertex.

### 3.2 Mistral OCR API (EU company)

- **Price.** OCR 4.1: **$4 per 1,000 pages** ($0.40 per 100). OCR 3: $2 per 1,000 pages. Source: [Mistral API pricing](https://mistral.ai/pricing/api/). The [pricing FAQ](https://mistral.ai/pricing) says batch processing cuts the price by 50% (it does not say whether that covers OCR). Model card for OCR 3: [docs.mistral.ai](https://docs.mistral.ai/models/ocr-3-25-12). A third-party page claims `mistral-ocr-latest` now points to OCR 4.1; I could not confirm that on Mistral's own changelog.
- **Training.** On Mistral Studio Pay-as-you-go, customers "retain full control" and may opt out of training. The Free mode may use your data unless you opt out. The opt-out does not apply to Labs and Public Preview models. Source: [Do you use my user data to train your models](https://help.mistral.ai/en/articles/347617-do-you-use-my-user-data-to-train-your-models). **Opt out and avoid preview models.**
- **Residency.** "By default, your data is hosted in the European Economic Area (EEA)"; a US endpoint exists but is opt-in. Some features may transfer data temporarily outside the EU, as listed in the Trust Center subprocessors. Source: [Where do you store my data](https://help.mistral.ai/en/articles/347629-can-i-activate-zero-data-retention-zdr) (the page that URL returned).
- **Retention.** A search summary of Mistral's help centre reports **30-day API log retention** and a [zero-data-retention page](https://docs.mistral.ai/admin/monitor-comply/zero-data-retention) that covers stateless calls (not the Files API) and is granted at Mistral's discretion. I did not open those pages in full, so confirm the 30 days and ZDR eligibility before relying on them.
- **Quality.** Mistral says OCR works across 40+ languages ([docs](https://docs.mistral.ai/capabilities/document_ai/basic_ocr/)). I found no independent Dutch letter benchmark. The docs page does not mention handwriting.
- **Setup.** API key, one endpoint, `image_url` or `document_url`. Easiest EU-hosted option.

### 3.3 Anthropic and OpenAI vision models

**Anthropic (Claude API)**

- **Price.** Claude Haiku 5.5 is $0.10 input and $0.50 output per million tokens for prompts up to 100,000 tokens; Sonnet 5.5 is $2 / $10. Source: [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing). A PDF page costs 1,500 to 3,000 text tokens plus image tokens ([PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support)). I did not verify OCR quality on Dutch.
- **Training.** Retained data is "never used for model training without your express permission" ([API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)).
- **Retention.** Two Anthropic pages differ. The [commercial retention article](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data) says inputs and outputs are deleted within 30 days. The [API data retention page](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) says conversation content is not retained by default (except "Covered Models", which require 30-day retention). Flagged content can be kept up to 2 years. ZDR is available by contacting sales and covers PDF support.
- **EU processing.** **Not available.** `inference_geo` accepts only `"us"` or `"global"`, and workspace geo is only `"us"` ([Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency)). US-only costs 1.1 times. Claude is also offered through Google Cloud's Vertex AI and Amazon Bedrock, where the cloud provider sets regional endpoints and prices; I did not check an EU Claude endpoint there.

**OpenAI**

- **Price.** Cheapest small models on the [pricing page](https://developers.openai.com/api/docs/pricing): gpt-5-nano $0.05 / $0.40, gpt-5-mini $0.25 / $2.00. The page does not say which models take images; confirm before use.
- **Training and retention.** API data is "not used to train or improve OpenAI models" unless you opt in; abuse-monitoring logs are kept up to 30 days; ZDR needs OpenAI approval ([Your data](https://developers.openai.com/api/docs/guides/your-data)).
- **EU.** Europe supports in-region storage and processing through `eu.api.openai.com`, but you must contact sales first, a project's region cannot be changed, **image support in these regions needs additional approval**, and there is a 10% uplift for models released on or after 5 March 2026 ([Data residency](https://developers.openai.com/api/docs/guides/data-residency)). That is heavy for a one-person service.

---

## 4. Running it locally on CPU

No source gives a clean, same-hardware CPU benchmark across these tools. Speeds below are mostly **(my estimate)** from the few data points I found; each needs a test on your Railway CPU.

### 4.1 Classic OCR

| Tool | Licence | Rough CPU speed per page | Dutch / French | Letters and forms | Handwriting | House plans |
|---|---|---|---|---|---|---|
| **Tesseract 5** | Apache 2.0 ([repo](https://github.com/tesseract-ocr/tesseract)) | About 0.5 to 2 seconds. One test measured 0.77 s on one invoice image on CPU ([Codesota](https://codesota.com/ocr/paddleocr-vs-tesseract)) | Yes. Dutch (`nld`) and French (`fra`) models exist in `tessdata`, `best` and `fast` ([data files](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html)) | Good on clean 300 dpi print. Weak on skewed, noisy photos; known to struggle with tables ([improving quality](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)) | Poor. A 5,578-prescription test found under 3% exact match ([DEV Community](https://dev.to/kaniel_outis/how-well-can-ocr-read-doctor-handwriting-in-2026-54hn)) | Reads labels only when segmented; scattered rotated text is mostly missed **(my estimate)** |
| **docTR** | Apache 2.0 ([repo](https://github.com/mindee/doctr)) | Not stated on the repo; one benchmark ran it on a GPU only ([ImageToTable](https://imagetotable.ai/references/ocr-latency-benchmark)). A few seconds per page on CPU **(my estimate)** | Repo gives no language list **(unverified for Dutch)** | Text detection plus recognition; no layout or table understanding | Not documented | Finds scattered words better than Tesseract; no understanding of the drawing **(my estimate)** |
| **PaddleOCR 3.x** | Apache 2.0 ([repo](https://github.com/PaddlePaddle/PaddleOCR)) | PP-OCRv6 has 1.5M to 34.5M parameter tiers and reports a 5.2 times CPU speed-up. One single-image test showed 4.85 s on CPU for an older version ([Codesota](https://codesota.com/ocr/paddleocr-vs-tesseract)) | PP-OCRv6 covers 46 Latin-script languages; PP-OCRv5 multilingual covers 109. Dutch and French not named individually | PP-StructureV3 turns pages into Markdown or JSON, with tables | Best of the classic engines in the prescription test ([DEV Community](https://dev.to/kaniel_outis/how-well-can-ocr-read-doctor-handwriting-in-2026-54hn)) | Reads text anywhere on the page; does not interpret the plan **(my estimate)** |

Drawings: none of these tools reads a plan as a plan. They return words and positions. Saying what a label refers to needs a vision-language model.

### 4.2 Small vision-language models (Ollama or llama.cpp)

| Model | Licence | Size | CPU speed | Dutch / French | Letters and forms | Handwriting | House plans |
|---|---|---|---|---|---|---|---|
| **Qwen3-VL** (4B shown) | Apache 2.0 ([model card](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct)) | 2B to 8B class; GGUF builds exist | No reliable figure found. The vision encoder often dominates CPU time. Tens of seconds to minutes per page **(my estimate)** | OCR claimed for 32 languages; Dutch and French not named | Card claims robustness to blur and tilt; no document benchmark on the card. Needs prompt tuning ([Hugging Face OCR blog](https://huggingface.co/blog/ocr-open-models)) | Listed as handwriting-capable in that blog's table | Could describe a plan and read labels; untested |
| **MiniCPM-V 4.5** | Apache 2.0 for code and weights ([model card](https://huggingface.co/openbmb/MiniCPM-V-4_5)) | About 8B | llama.cpp support merged (b6282); Ollama support "merging". No CPU figures | 30+ languages claimed | Card claims leading OCRBench and OmniDocBench results; no scores given | Not documented | Not documented |
| **Granite-Docling-258M** (successor to SmolDocling) | Apache 2.0 ([model card](https://huggingface.co/ibm-granite/granite-docling-258M)) | 258M | One user reported about 6 s per file with llama-server and a bf16 GGUF, versus 35 s through the Docling pipeline; hardware not stated ([discussion](https://huggingface.co/ibm-granite/granite-docling-258M/discussions/37)) | **English only** listed; Japanese, Arabic, Chinese experimental | Built for clean documents and tables | Not listed | Not suited |
| **SmolDocling-256M-preview** | Page lists both Apache 2.0 and CDLA-permissive-2.0 ([model card](https://huggingface.co/ds4sd/SmolDocling-256M-preview)) | 256M | 0.35 s per page on an A100 GPU; no CPU figure | English only | Preview; superseded by Granite-Docling | Not listed | Not suited |
| **olmOCR 2** | Apache 2.0 ([repo](https://github.com/allenai/olmocr)) | 7B; FP8 releases, no GGUF mentioned | **Needs an NVIDIA GPU with 12 GB or more.** Not a CPU option | English-only per the Hugging Face table | olmOCR-Bench 82.4 | Not listed | Not suited |
| **PaddleOCR-VL-1.6** | Apache 2.0 ([repo](https://github.com/PaddlePaddle/PaddleOCR)) | 0.9B | Runs on CPU per a third-party guide; no timing found | 109 to 111 languages | OmniDocBench v1.6 above 96.3% (vendor claim) | Described as suited to handwriting | Document parsing, not drawings |
| **Granite Vision** (general IBM vision model) | Not checked | Not checked | Not checked | Not checked | Not checked | Not checked | Not checked |

Summary:

- On a CPU-only Railway container, a 0.9B to 4B model is feasible but slow. For a few scans a day it works; for a 50-page insurance policy it is likely minutes to tens of minutes **(my estimate)**.
- The tiny document models (Granite-Docling, SmolDocling) are English only, so they do not fit Dutch and French letters.
- olmOCR needs a GPU, so it is out.
- Classic PaddleOCR is the strongest CPU-friendly local candidate for Dutch and French print. PaddleOCR-VL-1.6 is worth a test for handwriting.

---

## Comparison table

Cost is per 100 pages, under the assumptions in 1.3 (my estimate unless the vendor quotes a per-page price). "Trains on data?" refers to the paid or business route.

| Option | Cost per 100 pages | Trains on data? | Retention | EU processing | Quality for Dutch letters | Setup effort |
|---|---|---|---|---|---|---|
| Gemini API, 3.5 Flash-Lite (paid) | about $0.34 | No (paid). Yes on free tier | 55 days abuse monitoring; project logs on by default in projects created from 26 Oct 2026 | No guarantee ("any country") | Likely good; untested | Low |
| Gemini API, 3.8 Flash / Hydrogen | $0.54 now, about $1.08 from 1 Jan 2027 | No (paid) | As above | No guarantee | Likely better on messy scans; untested | Low |
| **Vertex AI, Gemini 3.5 Flash-Lite, EU multi-region** | about $0.37 | No | Cache up to 24 h (can be disabled); abuse-log period not stated; ZDR steps documented | **Yes, EU multi-region (not the global endpoint)** | Likely good; untested | Medium (Google Cloud project, service account) |
| Vertex AI, Gemini 3.8 Flash, EU | about $0.59 now, about $1.19 from 2027 | No | As above | Yes, EU multi-region | Likely better on messy scans; untested | Medium |
| Document AI Enterprise OCR (EU location) | $0.15 (first 1,000 pages a month free) | Not verified | Not verified | EU multi-region available | Good for print; no understanding | Medium |
| Mistral OCR 4.1 | $0.40 (OCR 3: $0.20) | No if you opt out on Pay-as-you-go; avoid preview models | 30 days reported (unverified); ZDR on request | EEA by default | Multilingual claimed; no independent Dutch test found | Low |
| Anthropic Claude Haiku 5.5 / Sonnet 5.5 | Haiku about $0.1 to 0.3, Sonnet about $1 to 2 (my estimate, 1,500 to 3,000 tokens a page) | No | Deleted within 30 days per one page; not retained by default per another; ZDR on request | **No (US or global only)** | Likely good; untested | Low |
| OpenAI gpt-5-nano / mini | Under $0.5 (my estimate) | No by default | Up to 30 days; ZDR on approval | Yes, via sales and extra approval for images | Likely good; untested | High (sales contact) |
| Local: Tesseract 5 | Free (CPU time) | No | None (stays on your server) | Yes (your server) | Good on clean print, poor on photos and handwriting | Low |
| Local: PaddleOCR 3.x | Free | No | None | Yes | Good on print; best classic engine on handwriting | Medium |
| Local: small VLM (Qwen3-VL 4B, PaddleOCR-VL) | Free, but slow (minutes per page on CPU, my estimate) | No | None | Yes | Plausible; untested | Medium to high |

## Recommendation

For layer 1, use **Gemini on Vertex AI pinned to the EU multi-region with caching disabled** (Gemini 3.5 Flash-Lite first, a Flash-class model for messy scans), because it is the only high-quality option with documented EU processing and a no-training promise, at about $0.37 per 100 pages; keep **Mistral OCR** as the simpler EU-hosted alternative if the Google Cloud setup is too heavy, after confirming its 30-day log and ZDR terms. Treat the plain Gemini API as acceptable only if the deployment owner accepts that data may leave the EU and is logged for 55 days, and refuse unbilled (free-tier) keys outright. Yes, ship a **local OCR path (Tesseract with the Dutch and French models, or PaddleOCR) as the default** for deployments without a provider, with the honest limit that it handles clean printed letters but not handwriting or house plans, and test the shortlist on a few of your own scans before committing.

## Gaps to close before building

- No benchmark on real Dutch letters exists in what I found; run 10 to 20 of your own scans through the shortlisted options.
- Confirm on the live Vertex residency table that your chosen model supports the EU multi-region, especially Gemini 4 models.
- Confirm Mistral's current retention and ZDR terms on its own pages.
- Document AI retention and training terms were not found.
- Granite Vision was not researched.
