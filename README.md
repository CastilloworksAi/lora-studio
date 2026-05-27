# LoRA Studio

A local web app for preparing Flux LoRA training datasets. You drop in images,
a local vision model writes draft captions, you review and edit them, then
click Export. It writes a paired dataset folder and a configured
ComfyUI FluxTrainer workflow you can queue directly.

No cloud calls. No telemetry. Everything stays on `127.0.0.1`.

## Why this exists

The standard ComfyUI dataset prep flow is:

1. Move images into a folder by hand.
2. Open a captioning workflow, change the path, queue it for each image, copy
   the output into `.txt` files, rename them to match image filenames.
3. Open the training workflow, change three node values to point at your
   dataset and trigger word.
4. Queue training.

That is 30+ clicks across three workflows and a file manager. This tool turns
the whole prep step into: create project, drop images, click Analyze, edit
captions, click Export.

The training itself still happens in ComfyUI — this is the prep stage only.

## What you get

```
[Sidebar: projects]      [Main: image grid]
                         ┌──────────────────────────────┐
+ New subject            │ filename.jpg                 │
                         │ ┌──────────────────────────┐ │
my-subject               │ │  thumbnail               │ │
  subject01 · 18 images  │ └──────────────────────────┘ │
                         │ [caption textarea]           │
                         │ [ Analyze ] [ Save caption ] │
                         └──────────────────────────────┘
                         [ Analyze all images ] [ Export for training ]
```

Four-step progress bar across the top: Add → Caption → Review → Export.

## Requirements

| Component | Version |
|---|---|
| Python | 3.10+ (standard library only, no `pip install`) |
| [Ollama](https://ollama.com) | Any current version, with a vision model |
| [ComfyUI](https://github.com/comfyanonymous/ComfyUI) | Any recent version |
| [ComfyUI-FluxTrainer](https://github.com/kijai/ComfyUI-FluxTrainer) | For training |
| A Flux LoRA training workflow JSON | Used as the export template (see below) |
| Disk | ~50 MB per project + your image dataset |

Default vision model: `llama3.2-vision:11b` (about 7.9 GB on disk, 8 GB VRAM).

Smaller alternative: `minicpm-v` (2.7 GB, 4 GB VRAM, faster, slightly less
descriptive captions). Set `LORA_STUDIO_VISION_MODEL=minicpm-v` to use it.

## Install

```bash
git clone https://github.com/CastilloworksAi/lora-studio.git
cd lora-studio
ollama pull llama3.2-vision:11b   # one-time, ~8 GB download
ollama serve &                     # if not already running
./start.sh
```

Open <http://127.0.0.1:4173>.

## Configuration

All paths are environment variables. Defaults assume a `$HOME/lora_training/`
and `$HOME/ComfyUI/` layout.

| Variable | Default | Purpose |
|---|---|---|
| `LORA_STUDIO_DATASET_ROOT` | `$HOME/lora_training/datasets` | Exported datasets are written here |
| `LORA_STUDIO_OUTPUT_ROOT` | `$HOME/lora_training/output` | Trained LoRAs will land here |
| `LORA_STUDIO_WORKFLOW_TEMPLATE` | `$HOME/lora_training/flux_lora_train.json` | FluxTrainer workflow used as the export template |
| `LORA_STUDIO_COMFY_WORKFLOWS` | `$HOME/ComfyUI/user/default/workflows` | Where the customized workflow is saved |
| `LORA_STUDIO_OLLAMA_URL` | `http://127.0.0.1:11434/api/chat` | Ollama chat endpoint |
| `LORA_STUDIO_VISION_MODEL` | `llama3.2-vision:11b` | Ollama vision model |
| `LORA_STUDIO_HOST` | `127.0.0.1` | Bind address |
| `LORA_STUDIO_PORT` | `4173` | Bind port |

Example with a different model and a custom workflow template:

```bash
export LORA_STUDIO_VISION_MODEL=minicpm-v
export LORA_STUDIO_WORKFLOW_TEMPLATE=/data/my_custom_flux_trainer.json
./start.sh
```

## Workflow

### 1. Create a project

Click **+ New subject**. Enter:
- **Subject name** — e.g. `portrait-style-01` or `my-cat`. Used for the project
  folder slug.
- **Trigger word** — a unique token that activates this LoRA in prompts later.
  Must be at least 3 alphanumeric characters. Good choices: `jcstyle01`,
  `ohwx2026`, `tokABC`. Bad choices: `person`, `woman`, `cat` (the base model
  already knows these words).

### 2. Add images

Drag JPG / PNG / WEBP files into the dropzone, or click to browse.
Per-file limit: 30 MB. Recommended dataset size:

| Use case | Image count |
|---|---|
| One person, single style | 20–30 |
| One person, varied (outfits, lighting, angles) | 30–50 |
| Art style | 40–80 |

Files are written with `chmod 600` and stored at
`projects/<slug>/images/<filename>`.

### 3. Caption

Click **Analyze all images**. The server sends each image to Ollama with this
prompt:

> Write one accurate training caption for this image. Use the unique subject
> token `<trigger>` exactly once when referring to the primary subject.
> Describe observable appearance, hair, clothing, pose, facial expression,
> camera framing, background, lighting, and composition in natural language.
> Do not guess identity, camera settings, ethnicity, age, intent, or events
> not visible. Do not add commentary or headings. Return only the caption.

Captions are saved to `projects/<slug>/captions/<filename>.txt`.

Speed on an RTX 5080 with `llama3.2-vision:11b`: roughly 15–25 seconds per
image, longer for the first one (model load). 30 images: ~10 minutes total.

### 4. Review

Captions are the **single most important variable** in LoRA training quality.
Always read every caption before exporting.

Good caption (concrete, observational):

> ohwx01 woman with shoulder-length brown hair tied back, wearing a black
> wool coat, standing on a city sidewalk at dusk, three-quarter view, soft
> overhead street lighting, shallow depth of field.

Bad caption (guesses, generic, missing trigger):

> A beautiful young woman in nice clothes walking around the city.

Click the textarea, edit, click **Save caption**.

### 5. Export

Click **Export for training**. The server:

1. Verifies every image has a non-empty caption.
2. Copies images + caption `.txt` files into
   `$LORA_STUDIO_DATASET_ROOT/<slug>/`.
3. Loads the FluxTrainer workflow template.
4. Patches the `TrainDatasetAdd` nodes (sets dataset path + trigger word) and
   the `InitFluxLoRATraining` node (sets LoRA name + output path).
5. Writes the customized workflow to
   `$LORA_STUDIO_COMFY_WORKFLOWS/lora_studio_<slug>_train.json`.

Open ComfyUI, load the new workflow, press Queue. Training begins.

## HTTP API

For automation / scripting. All endpoints are JSON. All bodies are JSON
unless otherwise noted.

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/api/projects` | — | array of all projects with image lists |
| `GET` | `/api/projects/<slug>` | — | one project + image list |
| `GET` | `/api/projects/<slug>/images/<file>` | — | raw image bytes |
| `POST` | `/api/projects` | `{ name, trigger_word }` | created project |
| `POST` | `/api/projects/<slug>/upload` | `{ files: [{ name, data }] }` | upload result; `data` is base64 |
| `POST` | `/api/projects/<slug>/caption` | `{ name }` | `{ name, caption }` |
| `POST` | `/api/projects/<slug>/save-caption` | `{ name, caption }` | `{ saved: true }` |
| `POST` | `/api/projects/<slug>/export` | `{}` | `{ dataset_dir, workflow_path, output_dir, images }` |

Example:

```bash
curl -s -X POST http://127.0.0.1:4173/api/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"luna","trigger_word":"lunatok01"}'
```

## File layout

```
lora-studio/
├── server.py              # Single-file Python HTTP server (~320 LOC)
├── start.sh               # cd to script dir, exec server.py
├── static/                # Frontend (vanilla JS, no build step)
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── LICENSE                # MIT
├── README.md
└── projects/              # Created on first run, gitignored
    └── <slug>/
        ├── project.json   # name, slug, trigger_word, model, timestamps
        ├── images/
        │   └── *.jpg
        └── captions/
            └── *.txt
```

After clicking Export:

- Dataset (paired images + captions): `$LORA_STUDIO_DATASET_ROOT/<slug>/`
- Customized workflow: `$LORA_STUDIO_COMFY_WORKFLOWS/lora_studio_<slug>_train.json`
- Trained LoRA (after you actually run training): `$LORA_STUDIO_OUTPUT_ROOT/<slug>/<slug>_lora.safetensors`

## Privacy and security

- The server binds to `127.0.0.1` by default. Do not change to `0.0.0.0`
  unless you understand the consequences — there is no authentication.
- `umask 077` is set globally before any file write. New files: `chmod 600`.
  New directories: `chmod 700`.
- Captions are generated by your local Ollama instance. No image data leaves
  the machine.
- No analytics, no error reporting, no auto-update checks.
- The `projects/` directory contains your training images. It is gitignored
  by default. If you fork this repo, do not commit it.

## Troubleshooting

**"Caption model unavailable: HTTPConnectionPool"**
Ollama isn't running. `ollama serve &` and refresh.

**"Caption model unavailable: 404"**
The configured vision model isn't pulled. `ollama pull llama3.2-vision:11b`.

**Captions are tiny / blank / repetitive**
Switch model: try `minicpm-v` or `llava:13b`. Some models are stronger at
following the prompt format than others.

**"Workflow template not found"**
Set `LORA_STUDIO_WORKFLOW_TEMPLATE` to point at your FluxTrainer workflow JSON,
or place one at `$HOME/lora_training/flux_lora_train.json`.

**Export says "Every image needs a reviewed caption"**
At least one image has no caption file or has an empty caption. Look for
images without the green "captioned" indicator and run Analyze + Save.

**Port 4173 in use**
Pass a different port: `./start.sh 5000` or set `LORA_STUDIO_PORT=5000`.

## What this is NOT

- Not a training engine. It only prepares datasets and workflows.
- Not a model server. It calls out to Ollama for captions.
- Not multi-user. Single-user, single-machine, no auth.
- Not a face detector. It does not crop, align, or filter images.
- Not for SDXL LoRAs (the workflow template assumes FluxTrainer). You can
  point it at an SDXL trainer workflow JSON and patch `server.py`'s
  `export_project` to match that workflow's node names — but it's not
  out-of-the-box.

## Related repos

- [comfyui-workflows](https://github.com/CastilloworksAi/comfyui-workflows) —
  the ComfyUI workflow collection this tool plugs into (including the
  FluxTrainer workflow used as the default export template)
- [lora-training-guide](https://github.com/CastilloworksAi/lora-training-guide) —
  end-to-end LoRA training walkthrough
- [ai-workstation](https://github.com/CastilloworksAi/ai-workstation) —
  scripts for running this stack on Ubuntu + RTX 5080

## License

MIT.
