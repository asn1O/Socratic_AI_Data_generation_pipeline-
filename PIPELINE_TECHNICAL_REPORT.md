# Socratic AI Data Generation Pipeline: Architecture, Methodology & Technical Specification

---

## 1. Executive Summary & Purpose

The **Socratic AI Data Generation Pipeline** is an automated, high-throughput, multi-threaded data synthesis framework designed to generate scientifically grounded, multi-turn Socratic tutoring dialogues. Traditional conversational datasets often suffer from "direct answer leakage" (where an AI tutor prematurely reveals answers), stylistic uniformity, and lack of pedagogical structure. To overcome these limitations, this pipeline enforces strict theoretical scaffolding based on four formal tutoring states, samples across a 6-dimensional orthogonal hyperparameter grid ($3^6 = 729$ unique pedagogical profiles), covers 12 diverse academic and cognitive domains, and employs strict programmatic validation before committing conversations to storage.

The system interfaces with heterogeneous Large Language Model (LLM) providers (including Google AI Studio, NVIDIA NIM, and OpenRouter) concurrently, providing automated rate-limiting, error circuit-breaking, JSON extraction with reasoning-block isolation, sharded persistence, and statistical validation tools.

---

## 2. High-Level System Architecture

The pipeline follows a modular, decoupled architecture operating in five primary stages:

```
+---------------------------------------------------------------------------------------+
|                                1. Configuration & Setup                               |
|   - models/models.json (Model metadata, endpoints, state)                             |
|   - .env (Provider API keys, DATASET_GENERATOR_ID)                                    |
+-------------------------------------------+-------------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
|                            2. Prompt Synthesis & Sampling                             |
|   - src/prompt_generator.py: Samples 6 parameters + 1 Domain + 1 Topic               |
|   - dataset_generation_prompt.md: Hydrates template with definitions & constraints    |
+-------------------------------------------+-------------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
|                    3. Multi-Threaded Execution & Inference Engine                     |
|   - src/pipeline.py: ThreadPoolExecutor (1 thread per model)                          |
|   - src/model.py: OpenAI-compatible API client, timeout handling, token accounting    |
|   - Rate Limiter: 60s cooldown for throttled models (wait_1_min flag)                 |
|   - Circuit Breaker: Auto-expiry after 3 consecutive failures                         |
+-------------------------------------------+-------------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
|                       4. Parsing, Sanitization & Strict Validation                    |
|   - Regex-based stripping: <think>, <thought>, <scratchpad>, markdown fences          |
|   - Raw JSON streaming decoder (json.JSONDecoder.raw_decode)                          |
|   - Structural Validation Invariants:                                                 |
|     * Total turns: 12-18 (Even number)                                                |
|     * Strict turn alternation: Student -> Tutor -> Student ...                        |
|     * Sequential turn numbering: 1..N                                                 |
|     * Tutor turn schema: {reasoning, state, response}                                 |
|     * Ending Invariants: Tutor[-3]=Synthesis -> Student[-2]=Explanation -> Tutor[-1]=Closing |
+---------------------+-------------------------------------+---------------------------+
                      |                                     |
              [Valid Payload]                       [Malformed Payload]
                      v                                     v
+---------------------------------------+   +---------------------------------------+
|       5a. Sharded Data Persistence    |   |     5b. Rejected Response Archive     |
| - data/<Provider_Model>/              |   | - data/rejected_responses/            |
|   <WorkerID>-set[N].json (15 convos)  |   |   <WorkerID>-<Provider_Model>.txt     |
+---------------------------------------+   +---------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
|                       6. Auditing, Telemetry & Analytics Suite                        |
|   - scripts/count_convos.py (Dataset volume tallying)                                 |
|   - scripts/data_distribution.py (Chi-square goodness-of-fit uniformity analysis)     |
|   - scripts/generate_distribution_graphs.py (300 DPI publication-grade figures)      |
+---------------------------------------------------------------------------------------+
```

---

## 3. Pedagogical Foundation & State Transition Mechanics

### 3.1 The Four Socratic States

At the core of the pipeline is a finite state machine governing the tutor's pedagogical behavior. In every tutor turn, the model is required to produce an internal meta-cognitive `reasoning` trace and classify its turn into exactly one of four mutually exclusive states:

| State | Pedagogical Objective | Behavioral Rules & Constraints |
| :--- | :--- | :--- |
| **`Diagnosis`** | Assess mental models and root misconceptions | Open-ended, non-leading inquiry. Asks *how* or *why* the student approached a step; avoids giving hints or yes/no prompts. |
| **`Support`** | Scaffold self-correction | Minimal, non-explicit hints (analogies, partial mechanisms, narrowing options). Never reveals the solution directly. |
| **`Synthesis`** | Trigger conceptual generalization | Executed **only after** the student derives the correct solution. Forces the student to articulate the universal concept/rule in their own words. The tutor **must never** state the rule first. |
| **`Closing`** | Conversational wrap-up | Reserved strictly for the terminal turn. Warm reinforcement acknowledging the student's generalization. Introduces no new questions or content. |

### 3.2 The Synthesis Loop

A fundamental pedagogical innovation in this pipeline is the **Synthesis Loop**:
1. When the student reaches the correct answer with sound reasoning, the tutor enters the `Synthesis` state, asking the student to abstract the core principle to general cases.
2. If the student's synthesis response is **satisfactory** (accurate, self-formulated, generalized beyond the problem's specific numbers), the tutor moves directly to `Closing`, completing the conversation.
3. If the student's synthesis response is **unsatisfactory** (vague, memorized, or incomplete), the tutor must diagnose the remaining gap, transition back to `Support` or `Diagnosis`, resolve the ambiguity, and subsequently re-trigger a reframed `Synthesis` inquiry.

### 3.3 Mandatory Structural Ending Sequence

To guarantee uniform quality for downstream fine-tuning, every conversation must conclude with a deterministic three-turn tail sequence:
- **Turn $N-2$ (Tutor):** Must be in state `Synthesis`.
- **Turn $N-1$ (Student):** Must provide the satisfactory general conceptual explanation.
- **Turn $N$ (Tutor):** Must be in state `Closing`.

---

## 4. Combinatorial Configuration & Domain Diversity

To ensure robustness against demographic, behavioral, and domain shift, each dialogue generation run dynamically samples a condition vector from a 6-dimensional configuration space and a 12-domain academic taxonomy.

### 4.1 Orthogonal Configuration Dimensions ($3^6 = 729$ Profiles)

| Parameter | Levels | Pedagogical Impact on Dialogue |
| :--- | :--- | :--- |
| **`socratic_level`** | `low`, `medium`, `high` | Governs directness. High requires $\ge 90\%$ pure questions; Low allows direct conceptual statements in $\ge 50\%$ of turns. |
| **`student_level`** | `beginner`, `intermediate`, `advanced` | Governs cognitive readiness. Beginners exhibit conceptual confusion; Intermediates have procedural missteps; Advanced students experience minor edge-case slips. |
| **`hint_frequency`** | `low`, `medium`, `high` | Governs scaffolding rate. Low provides hints only after prior failed attempts; High introduces scaffolding from initial confusion. |
| **`mistake_frequency`** | `low`, `medium`, `high` | Governs error prevalence. Low has at most 1 explicit error; Medium has exactly 2 distinct errors; High has 3+ recurring misconceptions. |
| **`verbosity`** | `low`, `medium`, `high` | Controls turn length: Low (5–20 words/turn), Medium (20–45 words/turn), High (45–90 words/turn). Keeps dialogues realistic and spoken. |
| **`difficulty`** | `easy`, `medium`, `hard` | Problem complexity: Easy (1–2 reasoning steps), Medium (3–4 steps), Hard (5+ steps with plausible distractors). |

### 4.2 Academic & Cognitive Domain Coverage (12 Domains)

The prompt generator samples uniformly across 12 distinct subject domains, each containing 4–8 curated subtopics:
1. **Quantitative Aptitude:** Ratio & proportion, percentages, time & work, probability, permutations.
2. **Logic & Puzzles:** Blood relations, direction sense, syllogisms, seating arrangements, Venn diagrams.
3. **Physics:** Newton's laws, series/parallel circuits, buoyancy & density, levers & pulleys, heat transfer.
4. **Chemistry:** Balancing chemical equations, acids & bases, phase changes, stoichiometry.
5. **Biology:** Photosynthesis, Punnett squares/genetics, circulatory systems, food webs, cell structures.
6. **Economics & Finance:** Supply & demand, simple vs. compound interest, inflation, opportunity cost, elasticity.
7. **Statistics & Data:** Mean/median/mode, probability distributions, correlation vs. causation, sampling bias.
8. **Language & Grammar:** Parts of speech, clauses, active/passive voice, common grammatical fallacies.
9. **History & Social Studies:** Cause-and-effect reasoning, comparing governance systems, primary sources.
10. **Ethics & Critical Thinking:** Logical fallacies, trolley-problem dilemmas, premise-conclusion structures.
11. **Everyday Reasoning:** Unit conversions, recipe scaling, trip budgeting, loan comparisons.
12. **Computer Science (Light):** Tracing algorithms, binary representation, boolean logic, Big-O intuitions.

---

## 5. Detailed Component Breakdown & Implementation

### 5.1 Dynamic Prompt Engine (`src/prompt_generator.py`)

The prompt generator class `DatasetPromptGenerator` dynamically reads `dataset_generation_prompt.md` and performs runtime placeholder hydration:
- Randomly draws one value and associated definition from each of the 6 configuration parameters.
- Randomly samples a domain and corresponding subtopic.
- Injects placeholders: `<<SOCRATIC_LEVEL>>`, `<<STUDENT_LEVEL_DEF>>`, `<<DOMAIN>>`, `<<TOPIC>>`, etc.
- Produces two artifacts: the hydrated system/user prompt string and an initialized JSON metadata stub containing `config`, `domain`, and `topic`.

### 5.2 Model Interface & API Abstraction (`src/model.py`)

The `Model` dataclass encapsulates communication with OpenAI-compatible endpoints:
- **Universal Client Adapter:** Leverages `openai.OpenAI(base_url=self.url, api_key=...)` to seamlessly call diverse backends (Google AI Studio, Google AI Studio, NVIDIA NIM, OpenRouter, Groq, local vLLM instances).
- **Runtime State Accounting:**
  - `no_of_replies`: Tracks successful completions.
  - `avg_tokens_used`: Computes running token usage averages:
$\mu_{new} = \frac{(\mu_{old} \times N) + \text{tokens}}{N + 1}$
  - `last_use`: ISO-8601 UTC timestamp of last invocation.
  - `consecutive_failures`: Failure tally for circuit-breaking.
  - `expired`: Boolean flag indicating if the model has been disabled.
- **Timeout Management:** Configured with an extensive 20-minute safety threshold (`REQUEST_TIMEOUT_SECONDS = 1200`) to accommodate frontier reasoning models generating lengthy multi-turn conversations.
- **Categorized Exception Handling:** Intercepts `RateLimitError` (`HTTP_429`), `AuthenticationError` (`HTTP_401`), `APIStatusError` (`HTTP_404`, `HTTP_500`), `APIConnectionError`, and `APITimeoutError`.

### 5.3 Multi-Threaded Pipeline Orchestration (`src/pipeline.py`)

The entry-point orchestration script manages parallel worker execution and lifecycle synchronization:
- **Workload Partitioning (`normalize_iterations`):**
  Calculates the total attempts required to evenly distribute generation across all active models in `models/models.json`:
  `per_model = ceil(iterations / M)`; `total_iterations = per_model * M`.
- **Thread Pool Architecture:** Launches a `concurrent.futures.ThreadPoolExecutor(max_workers=len(models))` allocating exactly one dedicated worker thread per configured model.
- **Graceful Shutdown & Signal Trapping:** Binds a `threading.Event()` (`stop_event`) to `KeyboardInterrupt` (`SIGINT`). When triggered, workers finish or safely abort their active iteration, model states are persisted to disk, and no corrupted files are written.
- **Rate-Limit Throttling:** For models with `"wait_1_min": true` (e.g., Google AI Studio free tiers with strict Requests-Per-Minute quotas), workers execute `stop_event.wait(timeout=60)` between calls.
- **Circuit Breaker:** If a model records 3 consecutive API failures (`consecutive_failures >= 3`), it is automatically flagged as `expired = True`, logged via `MODEL_EXPIRED`, and its worker thread cleanly terminates without stalling the overall pipeline.

### 5.4 JSON Sanitization & Parsing Engine

LLMs frequently wrap JSON in markdown blocks or output visible reasoning traces. To ensure robust extraction:
1. **Reasoning Block Pruning:**
   ```python
   _THOUGHT_BLOCK_PATTERN = re.compile(
       r"(?is)<(think|thought|scratchpad|reflection)\b[^>]*>.*?</\1>"
   )
   ```
   Strips `<think>...</think>` (DeepSeek/Gemma/Qwen style) tags before JSON extraction, preventing internal reasoning from corrupting the parser.
2. **Markdown Stripping:** Strips leading ````json` and trailing ```` fences.
3. **Stream Decoding via `raw_decode`:**
   Rather than using naive regex brace-matching (which fails when nested objects like `{ "config": { ... } }` exist), the pipeline locates the first opening brace `{` and invokes Python’s standard `json.JSONDecoder().raw_decode(text, idx=start)`. This accurately parses the outermost JSON object and returns the exact boundary index.

### 5.5 Strict Payload Validation Invariants

Once parsed, every conversation must pass strict programmatic verification (`_is_valid_conversation_payload`):
1. **Turn Count Bound:** $\text{len}(\text{conversation}) \in [12, 18]$ and $\text{len}(\text{conversation}) \pmod 2 == 0$.
2. **Turn Alternation:** $\text{turn}_i$ must alternate strictly between `"student"` and `"tutor"`, with $\text{turn}_1$ always being `"student"`.
3. **Turn Numbering:** $\text{turn}_i[\text{"turn"}] \equiv i$ for all $i \in [1, N]$ with no skips or restarts.
4. **Content Integrity:**
   - Student turns must contain non-empty string `"text"`.
   - Tutor turns must contain non-empty string `"reasoning"`.
   - Tutor turns must contain valid `"state"` $\in \{\text{"Diagnosis"}, \text{"Support"}, \text{"Synthesis"}, \text{"Closing"}\}$.
   - Tutor turns must contain non-empty string `"response"`.
   - `"Closing"` state cannot appear before turn $N$.
5. **Pedagogical Terminal Invariant:**
   - $\text{turn}_{N-2}$ (Tutor) $\to$ `state == "Synthesis"`.
   - $\text{turn}_{N-1}$ (Student) $\to$ `speaker == "student"`.
   - $\text{turn}_{N}$ (Tutor) $\to$ `state == "Closing"`.

Any payload violating these rules is immediately rejected, categorized under a stable error label (e.g. `turn_count_out_of_range`, `second_last_not_synthesis`, `malformed_tutor_tags`), and logged.

---

### 5.6 Command-Line Usage & Analysis Commands

The following commands cover dataset auditing, distribution analysis, visualization generation, and pipeline execution.

**Dataset auditing and analysis**
```bash
# 1. Count total validated conversations per model and directory
python scripts/count_convos.py

# 2. Compute parameter & domain distribution metrics (line-by-line format)
python scripts/data_distribution.py

# 3. Compute distribution metrics formatted as Markdown/ASCII tables
python scripts/data_distribution.py --format table

# 4. Display fine-grained subtopic breakdowns under each academic domain
python scripts/data_distribution.py --show-topics

# 5. Analyze conversations from a custom data directory
python scripts/data_distribution.py --data-dir data/test_run

# 6. Generate 300 DPI publication-grade figures and charts (saved to reports/figures/)
python scripts/generate_distribution_graphs.py
```

**Pipeline generation**
```bash
# 1. Standard generation run (e.g., 50 total attempts evenly split across all active models)
python -m src.pipeline models/models.json 50

# 2. Test run writing to an isolated custom output directory
python -m src.pipeline models/models.json 5 --data-dir data/test_run
```

## 6. Storage Model & Distributed Worker Safety

### 6.1 Sharded Output Storage

To prevent giant monolithic JSON files that cause I/O bottlenecks and memory strain during downstream training:
- Accepted conversations are routed to a directory named after provider and model:
  `data/<Provider>_<model_name>/` (e.g. `data/Google_gemini-3.5-flash/`).
- Conversations are written in chunks of at most **15 conversations per file**:
  `<DATASET_GENERATOR_ID>-set[<index>].json`.
- When `set[k]` reaches 15 records, the pipeline increments the index and creates `set[k+1]`.

### 6.2 Multi-Worker Distributed Collision Avoidance

To enable multiple machines or parallel terminal instances to generate data simultaneously into a shared network drive or Git repository without write contention:
- Each environment specifies a unique `DATASET_GENERATOR_ID` in `.env` (e.g. `worker_01`, `asn`, `edw`).
- Filenames incorporate the worker ID, guaranteeing zero file-write lock conflicts.
- Dataset merging is as simple as combining folder contents.

### 6.3 Rejection Diagnostics Archive

Any raw response that fails JSON decoding or structural verification is appended to:
`data/rejected_responses/<DATASET_GENERATOR_ID>-<Provider_Model>.txt`
This preserves full visibility into model non-compliance, enabling prompt tuning and model benchmarking.

---

## 7. Statistical Auditing & Visualization Suite (`scripts/`)

The repository includes a comprehensive post-generation analysis and verification toolkit:

### 7.1 Volume Auditing (`scripts/count_convos.py`)
Scans all model subdirectories in `data/`, parses every `.json` file, validates that it contains a list, and prints total conversation counts per model folder and overall dataset volume.

### 7.2 Distribution & Uniformity Auditing (`scripts/data_distribution.py`)
Analyzes the generated corpus across all 6 configuration dimensions and 12 domains:
- Calculates empirical counts and percentage shares.
- Conducts **Pearson's Chi-Square ($\chi^2$) Goodness-of-Fit Tests** against the ideal theoretical uniform distribution:

$$
\chi^2 = \sum_{i=1}^{k} \frac{(O_i - E_i)^2}{E_i}, \quad p = 1 - F_{\chi^2}(\chi^2; k-1)
$$

- Emits detailed reports verifying that $p > 0.05$, confirming statistical uniformity without sampling bias.

### 7.3 Publication Figure Generation (`scripts/generate_distribution_graphs.py`)
Parses distribution audit logs and automatically renders high-resolution (300 DPI) publication-grade figures in `reports/figures/`:
1. `fig1_config_parameters_balance.png`: 6-parameter subplot grid comparing empirical distributions against uniform baseline targets.
2. `fig2_domain_distribution.png`: 12-domain horizontal bar chart displaying subject representation.
3. `fig3_model_generator_distribution.png`: Model volume output colored by AI provider.
4. `fig4_provider_and_model_hierarchy.png`: Provider market share donut alongside top-8 model volume.
5. `fig5_domain_model_heatmap.png`: Cross-tabulation heatmap of Domain vs. Model to verify generator invariance.
6. `fig6_config_stability_by_model.png`: 100\% stacked bar chart proving individual models did not bias configuration sampling.
7. `fig7_executive_summary_dashboard.png`: All-in-one visual summary dashboard suitable for executive presentations and paper overviews.

---

## 8. Summary of File & Directory Responsibilities

| Path | Primary Function |
| :--- | :--- |
| `src/pipeline.py` | Multi-threaded CLI entry point, execution coordinator, JSON decoder, structural validator, run summary reporting. |
| `src/model.py` | Dataclass representing LLM providers/models, OpenAI API client wrapper, token tracker, sharded JSON file writer. |
| `src/prompt_generator.py` | Stochastic sampling engine for the 6 pedagogical parameters and 12 academic domains; hydrates prompt templates. |
| `dataset_generation_prompt.md` | Formal prompt template containing Socratic state definitions, pedagogical rules, few-shot schema, and output constraints. |
| `models/models.json` | Registry of configured AI models (endpoints, API key env vars, runtime state, reply tallies, token statistics). |
| `scripts/count_convos.py` | Fast utility to count total validated dialogues across all output folders. |
| `scripts/data_distribution.py` | Statistical analysis tool calculating counts, percentages, and $\chi^2$ goodness-of-fit $p$-values. |
| `scripts/generate_distribution_graphs.py` | Generates 300 DPI publication plots (matplotlib/seaborn) based on dataset distribution metrics. |
| `data/` | Main storage directory containing partitioned subfolders per model, logging archives (`data/logs/`), and rejected samples. |
| `reports/` | Contains the formal dataset report (`DATASET_DISTRIBUTION_REPORT.md`) and rendered visualization figures (`reports/figures/`). |
| `tests/` | Unit tests (`test_pipeline.py`, `test_prompt_generator.py`) ensuring parser robustness, error handling, and prompt validity. |

---

## 9. Conclusion & Pedagogical Impact

The Socratic AI Data Generation Pipeline establishes a reproducible, scalable, and verifiable methodology for synthesizing complex pedagogical dialogue datasets. By enforcing internal reasoning traces, strict finite-state transitions, multi-tiered error classification, and statistical uniformity across multidimensional parameter spaces, it provides an end-to-end framework suitable for training frontier Socratic AI agents and conducting foundational research in automated tutoring systems.
