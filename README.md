# Socratic AI Data Generation Pipeline

This project generates validated Socratic tutor-student conversations with
OpenAI-compatible language-model APIs. The pipeline randomly selects a
domain, topic, and conversation configuration for each attempt, validates the
model response, and saves accepted records as JSON.

## Project layout

```text
Data_generation_pipeline/
├── src/
│   ├── model.json          # Combined model list and runtime state
│   ├── count_convos.py     # Script to count conversations generated
│   ├── pipeline.py         # Threaded pipeline entry point
│   └── prompt_generator.py # Prompt sampling and template population
├── dataset_generation_prompt.md # Conversation-generation prompt template
├── data/                   # Generated conversations and logs
├── tests/                  # Unit tests
├── requirements.txt        # Python package dependencies
├── .env.example            # Environment-variable template
└── .env                    # Local API keys; do not commit this file
```

## Requirements

- Python 3.9 or newer
- API access to at least one model configured in `src/model.json`

The code currently needs these third-party Python packages (listed in `requirements.txt`):

- `openai`
- `python-dotenv`
- `pytest`

## Installation

Run every command below from the repository root, the directory containing
`README.md`, `src/`, and `data/`.

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

The current `src/model.json` entries reference the three provider variables
above. `GROQ_API_KEY` is included in `.env.example` for compatibility but is
not currently used by the configured models. You may leave it empty unless you
add a Groq model entry to `src/model.json`.

Never replace the `api_key` values in `src/model.json` with secret strings.
They are environment-variable names, such as `NVIDIA_API_KEY`, and the
program resolves them from `.env` at runtime. Keep `.env` private.

## Model configuration

The pipeline accepts a JSON configuration file containing a list of model entries. The primary configuration is `src/model.json`:

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
python -m src.pipeline src/model.json 50
```

The requested iteration count is the total number of attempts. If it is not
divisible by the number of models, it is rounded up to the next divisible
number and split evenly. For example, 50 attempts across 3 models becomes 51
attempts, or 17 per model.

One thread runs per model. Models with `"wait_1_min": true` (e.g. Google models)
wait 60 seconds after each request to avoid RPM/TPM rate limits; models with
`"wait_1_min": false` proceed immediately. A model is expired and its worker
stops after three consecutive API-call failures.

Logs are written beneath `data/logs/`. The overall log is named after the
generator ID, for example `data/logs/worker_01.log`. Individual model logs
include both the generator ID and model name, for example
`data/logs/worker_01.NVIDIA_nvidia_nemotron-3-super-120b-a12b.log`. Each model
log ends with a `MODEL_SUMMARY` containing that model's attempts, saved and
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
python -m src.pipeline src/model.json 1
```

For example, to write a small test run to a separate directory:

```bash
python -m src.pipeline src/model.json 5 --data-dir data/test_run
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
│   ├── worker_01.log
│   └── worker_01.NVIDIA_nvidia_nemotron-3-super-120b-a12b.log
├── NVIDIA_nvidia_nemotron-3-super-120b-a12b/
│   └── worker_01-set[0].json
└── rejected_responses/
    └── worker_01-NVIDIA_nvidia_nemotron-3-super-120b-a12b.txt
```

The command prints a combined run summary containing attempts, accepted
records, failure categories, token counts, turn counts, domain coverage, and
model usage. The pipeline also updates quota and usage counters in the input
models file after all workers finish, so review that file before resetting or
copying it.

## Dataset format

The prompt generator randomly selects a domain, topic, and conversation
parameters for each attempt from `dataset_generation_prompt.md`. A valid record
contains:

- `config`: the selected Socratic, student, hint, mistake, verbosity, and
  difficulty settings
- `domain`, `topic`, and `problem`
- `conversation`: 12-18 alternating student and tutor turns

Tutor turns contain separate `reasoning`, `state`, and `response` fields. The
final tutor state is always `Closing`; malformed or structurally invalid model
responses are rejected and saved under `data/rejected_responses/`.

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
  that `src/model.json` exists.
- `Authentication error (401)`: check the relevant key in `.env` and confirm
  that the matching `api_key` name in `src/model.json` is spelled correctly.
- `DATASET_GENERATOR_ID must contain only letters...`: use only letters,
  numbers, `_`, or `-`.
- `No models found`: confirm the selected JSON file contains a non-empty array.
- Repeated model failures: after three consecutive API-call failures without a
  response, that model is marked expired and its worker stops. Check the
  model-specific log and runtime fields in the input JSON file.
- `Not found (404)`: verify that the model name is still supported by the
  provider and that its URL in `src/model.json` is correct.
