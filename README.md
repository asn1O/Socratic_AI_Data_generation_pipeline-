# Socratic AI Data Generation Pipeline

This project generates validated Socratic tutor-student conversations with
OpenAI-compatible language-model APIs. The pipeline randomly selects a
domain, topic, and conversation configuration for each attempt, validates the
model response, and saves accepted records as JSON.

## Project layout

```text
Data_generation_pipeline/
├── code/
│   ├── model.json          # Model endpoints, quota state, and API-key variable names
│   └── pipeline.py         # Pipeline entry point
├── data/                   # Generated conversations and logs
├── tests/                  # Unit tests
└── .env                    # Local API keys; do not commit this file
```

## Requirements

- Python 3.9 or newer
- API access to at least one model configured in `code/model.json`

The code currently needs these third-party Python packages:

- `openai`
- `python-dotenv`

## Installation

Run every command below from the repository root, the directory containing
`README.md`, `code/`, and `data/`.

```bash
cd "/path/to/Data_generation_pipeline"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install openai python-dotenv
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

The current `code/model.json` entries reference the three provider variables
above. `GROQ_API_KEY` is included in `.env.example` for compatibility but is
not currently used by the configured models. You may leave it empty unless you
add a Groq model entry to `code/model.json`.

Never replace the `api_key` values in `code/model.json` with secret strings.
They are environment-variable names, such as `NVIDIA_API_KEY`, and the
program resolves them from `.env` at runtime. Keep `.env` private.

## Run the pipeline

The working directory matters. Start the command from
`Data_generation_pipeline/`, not from `Data_generation_pipeline/code/`.
The default relative paths are:

- models: `code/model.json`
- generated data and logs: `data/`

With the virtual environment activated, run:

```bash
python -m code.pipeline --iterations 50
```

For a short connectivity or setup check, use one attempt:

```bash
python -m code.pipeline --iterations 1
```

Available options:

```text
--iterations N       Number of model attempts; default: 50
--models-path PATH   Model configuration JSON; default: code/model.json
--data-dir PATH      Output and log directory; default: data
```

For example, to write a small test run to a separate directory:

```bash
python -m code.pipeline --iterations 5 --data-dir data/test_run
```

Do not run `cd code` before starting the pipeline. Doing so changes the
meaning of `code/model.json`, `data/`, and the prompt-template path, and can
cause files to be created in the wrong location or the models file not to be
found.

## Generated files

Accepted conversations are written beneath `data/`, grouped by provider and
model. Each worker file contains at most 15 records before a new set file is
created:

```text
data/
├── pipeline.log
├── NVIDIA_nvidia_nemotron-3-super-120b-a12b/
│   └── worker_01-set[0].json
└── rejected_responses/
		└── NVIDIA_nvidia_nemotron-3-super-120b-a12b.txt
```

The command prints a run summary containing attempts, accepted records,
failure categories, token counts, turn counts, domain coverage, and model
usage. The pipeline also updates quota and usage counters in
`code/model.json`, so review that file before resetting or copying it.

## Run tests

From the repository root, with the virtual environment activated:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

The tests use mocks and local temporary files; they do not call an API.

## Troubleshooting

- `models file not found`: confirm the shell is in the repository root and
  that `code/model.json` exists.
- `Authentication error (401)`: check the relevant key in `.env` and confirm
  that the matching `api_key` name in `code/model.json` is spelled correctly.
- `DATASET_GENERATOR_ID must contain only letters...`: use only letters,
  numbers, `_`, or `-`.
- `No available model`: a model may be rate-limited, exhausted, expired, or
  have too many consecutive failures. Check the counters in `code/model.json`
  and the details in `data/pipeline.log`.
- `Not found (404)`: verify that the model name is still supported by the
  provider and that its URL in `code/model.json` is correct.
