# Socratic AI Data Generation Pipeline

This project generates validated Socratic tutor-student conversations with
OpenAI-compatible language-model APIs. The pipeline randomly selects a
domain, topic, and conversation configuration for each attempt, validates the
model response, and saves accepted records as JSON.

## Project layout

```text
Data_generation_pipeline/
├── src/                        # Core Python pipeline package
│   ├── __init__.py
│   ├── model.py                # Model API communication & retry handling
│   ├── pipeline.py             # Multi-threaded pipeline coordinator & CLI entry point
│   ├── prompt_generator.py     # Prompt sampling & template population
│   ├── telemetry.py            # Quota telemetry, logging, and summary reports
│   ├── validator.py            # Sanitization, JSON raw_decode, invariants & leakage checks
│   └── worker.py               # Worker loop, pacing & circuit breaking
├── models/
│   └── models.json             # Model configurations & worker runtime state
├── scripts/                    # Utility and dataset analysis scripts
│   ├── count_convos.py         # Count conversation totals per folder
│   ├── data_distribution.py    # Compute dataset distribution statistics
│   └── generate_distribution_graphs.py # Generate publication figures
├── dataset_generation_prompt.md # Conversation-generation prompt template
├── data/                       # Generated conversations and logs
├── tests/                      # Automated unit test suite
├── requirements.txt            # Python package dependencies
├── .env.example                # Environment-variable template
└── .env                        # Local API keys; do not commit this file
```

## Requirements

- Python 3.9 or newer
- API access to at least one model configured in `models/models.json`

The code currently needs these third-party Python packages (listed in `requirements.txt`):

- `openai`
- `python-dotenv`
- `pytest`

## Installation

Run every command below from the repository root, the directory containing
`README.md`, `src/`, `models/`, and `data/`.

```bash
cd "/path/to/Data_generation_pipeline"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

## Configure environment variables

Create the local environment file from the provided template:

```bash
cp .env.example .env
```

Open `.env` and replace the empty values with your own keys:

```dotenv
NVIDIA_API_KEY=your_nvidia_key
OPENROUTER_API_KEY=your_openrouter_key
GOOGLE_API_KEY=your_google_key
DATASET_GENERATOR_ID=your_worker_name
```

`DATASET_GENERATOR_ID` identifies the worker that writes dataset files. It may
contain only letters, numbers, `_`, and `-`, for example `worker_01`.

The current `models/models.json` entries reference the three provider variables
above. `GROQ_API_KEY` is included in `.env.example` for compatibility but is
not currently used by the configured models. You may leave it empty unless you
add a Groq model entry to `models/models.json`.

Never replace the `api_key` values in `models/models.json` with secret strings.
They are environment-variable names, such as `NVIDIA_API_KEY`, and the
program resolves them from `.env` at runtime. Keep `.env` private.

## Model configuration

The pipeline accepts a JSON configuration file containing a list of model entries. The primary configuration is `models/models.json`:

Each model entry includes configuration and runtime fields such as
`wait_1_min`, `last_use`, `expired`, and `consecutive_failures`.
Models are loaded directly into an array and one thread owns each model for the
duration of a run. Do not run two processes against the same configuration file
at the same time. After all model threads finish, the main process writes the
updated model state back to the input file.

## Run the pipeline

The working directory matters. Start the command from
`Data_generation_pipeline/`, not from `Data_generation_pipeline/src/`.
Generated data and logs use the `data/` directory by default. Pass the model
configuration path and desired number of attempts explicitly:

```bash
python -m src.pipeline models/models.json 50
```

The requested iteration count is the total number of attempts. If it is not
divisible by the number of models, it is rounded up to the next divisible
number and split evenly. For example, 50 attempts across 3 models becomes 51
attempts, or 17 per model.

One thread runs per model. Models with `"wait_1_min": true` (e.g. Google models)
wait 60 seconds after each request to avoid RPM/TPM rate limits; models with
`"wait_1_min": false` proceed immediately. A model is expired and its worker
stops after three consecutive API-call failures.

Logs are written beneath `data/logs/`. Every log filename incorporates both the
generator ID (`DATASET_GENERATOR_ID`) and the model JSON configuration filename supplied
in the CLI input. The overall run log is named:
`data/logs/<generator_id>.<models_file>.log` (e.g. `data/logs/worker_01.models.json.log`).
Individual model logs include the generator ID, model JSON filename, and the model name:
`data/logs/<generator_id>.<models_file>.<provider_model>.log`
(e.g. `data/logs/worker_01.models.json.NVIDIA_nvidia_nemotron-3-super-120b-a12b.log`).
Each model log ends with a `MODEL_SUMMARY` containing that model's attempts, saved and
failed counts, acceptance rate, average tokens, turn range, expiry state, and
failure categories.

Failed API calls also include a stable `error_code` in the model log, such as
`HTTP_404`, `HTTP_429`, `HTTP_500`, `TIMEOUT`,
`API_CONNECTION_ERROR`, or `NO_RESPONSE`. Successful calls use no error code.
Separate provider configuration files can still be run in parallel, but use a
different `DATASET_GENERATOR_ID` for each worker so generated files and logs do
not conflict during dataset merging.

For a short connectivity or setup check, use one attempt:

```bash
python -m src.pipeline models/models.json 1
```

For example, to write a small test run to a separate directory:

```bash
python -m src.pipeline models/models.json 5 --data-dir data/test_run
```

Do not run `cd src` before starting the pipeline. Doing so changes the
meaning of the config path, `data/`, and the prompt-template path, and can
cause files to be created in the wrong location or the models file not to be
found.

## Generated files

Accepted conversations are written beneath `data/`, grouped by provider and
model. Each worker file contains at most 15 records before a new set file is
created:

```text
data/
├── logs/
│   ├── worker_01.models.json.log
│   └── worker_01.models.json.NVIDIA_nvidia_nemotron-3-super-120b-a12b.log
├── NVIDIA_nvidia_nemotron-3-super-120b-a12b/
│   └── worker_01-set[0].json
└── rejected_responses/
    └── worker_01-NVIDIA_nvidia_nemotron-3-super-120b-a12b.txt
```

The command prints a combined run summary containing attempts, accepted
records, failure categories, token counts, turn counts, domain coverage, and
model usage. The pipeline also updates failure and usage counters in the input
models file after all workers finish, so review that file before resetting or
copying it.

## Dataset format and validation rules

The prompt generator randomly selects a domain, topic, and conversation
parameters for each attempt from `dataset_generation_prompt.md`. A valid record
contains:

- `config`: the selected Socratic, student, hint, mistake, verbosity, and
  difficulty settings
- `domain`, `topic`, and `problem`
- `conversation`: 12-18 alternating student and tutor turns

### Strict Acceptance Checks
Every generated payload is validated against strict structural rules before saving:
1. **Turn Count**: Total turns must be an even number between 12 and 18.
2. **Turn Alternation**: Speaker must strictly alternate between `student` and `tutor` starting with `student`.
3. **Turn Numbering**: Turn numbers must sequentially match the turn index `1 .. N` with no skips or duplicates.
4. **Tutor Tags**: Every tutor turn must contain non-empty `reasoning`, a valid `state` (`Diagnosis`, `Support`, `Synthesis`, `Closing`), and `response`.
5. **Config & Parameter Leakage**: Dialogue turns and tutor reasoning traces are strictly scanned for accidental leakage of system prompt hyperparameters (e.g. `socratic_level`, `hint_frequency`, `student_level`), persona directives (e.g. `beginner student`), or difficulty constraints (`maintain the easy difficulty`).
6. **Pedagogical Ending Sequence**:
   - The tutor turn before the final student explanation (`conversation[-3]`) must be in the `Synthesis` state.
   - The final student turn (`conversation[-2]`) must provide their synthesis explanation.
   - The final tutor turn (`conversation[-1]`) must be in the `Closing` state.

Malformed or non-conforming responses are rejected, categorized in logs, and saved under `data/rejected_responses/`.

## Utility Scripts & Tools

The pipeline includes built-in tools and helper scripts for dataset auditing, cleanup, and analysis:

- **Validate & sanitize existing dataset files (offline audit/clean)**:
  ```bash
  # Dry-run audit (reports invalid or leaked conversations):
  python -m src.validator --data-dir data

  # Permanently remove invalid or leaked records from disk:
  python -m src.validator --data-dir data --delete-invalid
  ```
- **Count conversations**:
  ```bash
  python scripts/count_convos.py
  ```
- **Compute parameter and domain distribution**:
  ```bash
  python scripts/data_distribution.py
  ```
- **Generate publication figures**:
  ```bash
  python scripts/generate_distribution_graphs.py
  ```

## Run tests

From the repository root, with the virtual environment activated:

```bash
pytest
# or using unittest
python -m unittest discover -s tests -p 'test_*.py' -v
```

The tests use mocks and local temporary files; they do not call an API.

## Troubleshooting

- `models file not found`: confirm the shell is in the repository root and
  that `models/models.json` exists.
- `Authentication error (401)`: check the relevant key in `.env` and confirm
  that the matching `api_key` name in `models/models.json` is spelled correctly.
- `DATASET_GENERATOR_ID must contain only letters...`: use only letters,
  numbers, `_`, or `-`.
- `No models found`: confirm the selected JSON file contains a non-empty array.
- Repeated model failures: after three consecutive API-call failures without a
  response, that model is marked expired and its worker stops. Check the
  model-specific log and runtime fields in the input JSON file.
- `Not found (404)`: verify that the model name is still supported by the
  provider and that its URL in `models/models.json` is correct.
