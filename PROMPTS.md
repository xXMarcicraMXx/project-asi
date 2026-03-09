# ASI Prompt Reference

Every prompt in the system — where it lives, what it controls, and how to change it.

---

## Prompt Architecture Overview

The system has two categories of prompt:

| Category | Location | Changes cached by Anthropic? | Purpose |
|---|---|---|---|
| **System prompts** | `agents/*.py` (hardcoded strings) | YES — fully static | Define agent persona and permanent behaviour rules |
| **User message fragments** | `config/*.yaml` + assembled at runtime | NO — dynamic per call | Inject regional context, task content, and format instructions |

**Critical rule:** System prompts are **never modified at runtime**. All variable content (topic, region, draft text, sources) goes into the user message only. This is what enables Anthropic prompt caching, which cuts repeated-call costs by ~90%.

---

## 1. Agent System Prompts

These are hardcoded strings in each agent file. They define *who the agent is* and *what rules it always follows* — not the specific task for any given call.

---

### 1.1 ResearchAgent System Prompt

**File:** `agents/research_agent.py`
**Model:** Claude Haiku (fast, cheap — parsing task)
**Cached:** Yes

```
You are a research analyst for a news organisation.

Your job is to read a set of provided news articles and extract structured
information. You do not write prose. You output only structured data.

Rules:
- Extract only facts, quotes, and data points that appear explicitly in the
  provided articles. Do not invent or infer.
- Preserve direct quotes exactly as written. Do not paraphrase.
- Identify conflicting perspectives if different sources disagree.
- Output must be valid JSON matching the ResearchBrief schema.
- If a field has no relevant content, return an empty list — never null.
```

**What it controls:** Ensures the research step produces structured JSON, never invents facts, and preserves exact quotes.

**How to change it:**
- Edit the string in `agents/research_agent.py`
- Keep rules factual and output-format focused
- Do NOT add topic, region, or article content here — those go in the user message
- After any change, re-test that the agent still returns valid ResearchBrief JSON

---

### 1.2 WriterAgent System Prompt

**File:** `agents/writer_agent.py`
**Model:** Claude Sonnet (full creative capacity)
**Cached:** Yes

```
You are a professional journalist and editor.

You write formal, well-structured articles based on provided research briefs.
You adapt your editorial voice and perspective to the regional context given
in each request. You do not fabricate statistics, quotes, or sources.

Rules:
- Follow the format instructions provided in each request exactly.
- Adopt the editorial voice described in each request — do not default to a
  neutral or American news voice unless explicitly instructed.
- Cite sources inline using [Source Name] notation.
- Every claim must be traceable to the research brief. Do not extrapolate.
- Output raw markdown only. No preamble, no explanation, no sign-off.
```

**What it controls:** The permanent writing rules that apply regardless of region or topic — no fabrication, proper citation, markdown-only output.

**How to change it:**
- Edit the string in `agents/writer_agent.py`
- Keep permanent rules here; keep everything variable (voice, format, word count) in the user message
- Test changes by running a full pipeline and checking output quality
- Strengthening the "no fabrication" rule is always safe; loosening it is not

---

### 1.3 EditorAgent System Prompt

**File:** `agents/editor_agent.py`
**Model:** Claude Sonnet
**Cached:** Yes

```
You are a senior editor at a news organisation.

You evaluate article drafts against a checklist of editorial criteria.
You return a structured verdict — never free-form feedback.

Rules:
- Evaluate the draft against every criterion in the provided list.
- If all criteria pass: return {"status": "approve", "feedback": "Approved."}
- If any criterion fails: return {"status": "revise", "feedback": "<specific,
  actionable instruction for the writer — one paragraph>"}
- Feedback must name the specific criterion that failed and explain what to fix.
- Output must be valid JSON. No preamble, no prose outside the JSON object.
```

**What it controls:** Forces structured JSON output from the editor and ensures feedback is always actionable, not vague.

**How to change it:**
- Edit the string in `agents/editor_agent.py`
- The JSON schema (`status` + `feedback`) must stay consistent with `EditorVerdict` in `orchestrator/job_model.py`
- If you change the output schema here, update `EditorVerdict` in `job_model.py` to match
- Never make feedback optional — the writer loop depends on it

---

## 2. User Message Templates

These are assembled at runtime in each agent's `run()` method. They inject the variable content — topic, region, articles, drafts — that changes with every call.

---

### 2.1 ResearchAgent User Message

**Assembled in:** `agents/research_agent.py`
**Template:**

```
Topic: {topic}

Articles to analyse:

{for each article}
--- SOURCE: {article.source_name} ---
Title: {article.title}
URL: {article.url}
Published: {article.published_at}

{article.body}

{end for}

Return a ResearchBrief JSON object.
```

**What it controls:** Passes the raw article text to the agent for extraction.

**How to change it:**
- The template lives in the `_build_user_message()` method of `ResearchAgent`
- Add or remove article fields to include more or less context per source
- Keep the "Return a ResearchBrief JSON object" instruction — the agent depends on it

---

### 2.2 WriterAgent User Message

**Assembled in:** `agents/writer_agent.py`
**Template:**

```
EDITORIAL VOICE:
{region_config.editorial_voice}

FORMAT INSTRUCTIONS:
{content_type_config.writer_instructions}
Minimum words: {content_type_config.output.min_words}
Maximum words: {content_type_config.output.max_words}

{if rag_context}
ADDITIONAL PERSONA CONTEXT (from editorial archive):
{rag_context}
{end if}

{if editor_feedback}
EDITOR FEEDBACK FROM PREVIOUS DRAFT (address every point):
{editor_feedback}
{end if}

RESEARCH BRIEF:
Topic: {brief.topic}
Key facts:
{for each fact in brief.key_facts}- {fact}
Data points:
{for each point in brief.data_points}- {point}
Direct quotes:
{for each quote in brief.direct_quotes}- "{quote}"
Conflicting perspectives:
{for each p in brief.conflicting_perspectives}- {p}

Write the article now.
```

**What it controls:** The complete writing task — voice, format, word limits, RAG persona context, editor feedback (on revision), and the research content.

**How to change it:**

| What you want to change | Where to change it |
|---|---|
| Editorial voice (EU, LATAM, etc.) | `config/regions/{region}.yaml` → `editorial_voice` field |
| Format rules (headers, citation style) | `config/content_types/journal_article.yaml` → `writer_instructions` |
| Word count limits | `config/content_types/journal_article.yaml` → `output.min_words` / `output.max_words` |
| RAG persona depth | Seed better documents into Pinecone via `ingestion/run_ingestion.py` |
| How editor feedback is surfaced | Edit `_build_user_message()` in `agents/writer_agent.py` |

---

### 2.3 EditorAgent User Message

**Assembled in:** `agents/editor_agent.py`
**Template:**

```
EDITORIAL CRITERIA:
{for each criterion in content_type_config.editor_criteria}
- {criterion}
{end for}

ARTICLE DRAFT (iteration {iteration}):
{draft.headline}

{draft.body}

Evaluate the draft against every criterion above and return your JSON verdict.
```

**What it controls:** What the editor checks for and which draft it is reviewing.

**How to change it:**

| What you want to change | Where to change it |
|---|---|
| What the editor checks for | `config/content_types/journal_article.yaml` → `editor_criteria` list |
| Add a new quality gate | Append a new criterion to the `editor_criteria` list in the YAML |
| Remove a quality gate | Delete the criterion from the YAML list |
| Change how the draft is presented | Edit `_build_user_message()` in `agents/editor_agent.py` |

---

## 3. Config-Driven Prompt Fragments

These YAML fields are not prompts by themselves — they are injected into user messages at runtime. Changing them requires no code changes.

---

### 3.1 `editorial_voice`

**File:** `config/regions/{region_id}.yaml`
**Injected into:** WriterAgent user message (Section 2.2 above)

The most important lever for regional differentiation. Defines the journalist persona, cultural lens, tone, and what to avoid.

**How to change it:** Edit the `editorial_voice` block in the relevant region YAML. Changes take effect on the next pipeline run — no restart needed. After changes, run a side-by-side comparison of the same topic across regions to verify differentiation is real.

---

### 3.2 `writer_instructions`

**File:** `config/content_types/journal_article.yaml`
**Injected into:** WriterAgent user message

Format rules that apply to all regions for this content type — structure, header style, citation notation.

**How to change it:** Edit `writer_instructions` in the content type YAML. To create a new format entirely (e.g., daily brief), create a new YAML file in `config/content_types/` — zero code changes required.

---

### 3.3 `editor_criteria`

**File:** `config/content_types/journal_article.yaml`
**Injected into:** EditorAgent user message

The checklist the editor uses to approve or reject a draft. Each item becomes one evaluation gate.

**How to change it:** Add, edit, or remove items from the `editor_criteria` list in the YAML. Be specific — vague criteria produce vague feedback. Good criteria are testable: "Word count is between 600 and 1200 words" is better than "Article is the right length."

---

## 4. Quick Reference — What to Touch for Common Changes

| Goal | File to edit |
|---|---|
| Strengthen EU regional voice | `config/regions/europe.yaml` → `editorial_voice` |
| Add a new region | Create `config/regions/newregion.yaml` — no code changes |
| Change article word limits | `config/content_types/journal_article.yaml` → `output` |
| Add a new editor quality gate | `config/content_types/journal_article.yaml` → `editor_criteria` |
| Change the writing format rules | `config/content_types/journal_article.yaml` → `writer_instructions` |
| Change the LLM model used for writing | `config/settings.yaml` → `models.writer` |
| Change the LLM model used for parsing | `config/settings.yaml` → `models.parser` |
| Change what the ResearchAgent extracts | `agents/research_agent.py` → system prompt |
| Change the editor output schema | `agents/editor_agent.py` → system prompt AND `orchestrator/job_model.py` → `EditorVerdict` |
| Add depth to a regional persona | Seed a new persona doc into Pinecone via `ingestion/run_ingestion.py` |
