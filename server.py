#!/usr/bin/env python3
"""Local LoRA Studio: prepare captioned datasets and ComfyUI training workflows."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
PROJECT_ROOT = APP_ROOT / "projects"

# All paths configurable via environment variables.
# Defaults assume a typical ComfyUI + ai-toolkit layout under $HOME.
HOME = Path(os.environ.get("HOME", str(Path.home())))
DATASET_ROOT = Path(os.environ.get("LORA_STUDIO_DATASET_ROOT", str(HOME / "lora_training/datasets")))
OUTPUT_ROOT = Path(os.environ.get("LORA_STUDIO_OUTPUT_ROOT", str(HOME / "lora_training/output")))
WORKFLOW_TEMPLATE = Path(os.environ.get("LORA_STUDIO_WORKFLOW_TEMPLATE", str(HOME / "lora_training/flux_lora_train.json")))
COMFY_WORKFLOWS = Path(os.environ.get("LORA_STUDIO_COMFY_WORKFLOWS", str(HOME / "ComfyUI/user/default/workflows")))
OLLAMA_URL = os.environ.get("LORA_STUDIO_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
VISION_MODEL = os.environ.get("LORA_STUDIO_VISION_MODEL", "llama3.2-vision:11b")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024

# Uploaded training material may be personal. Keep all newly written data private.
os.umask(0o077)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60]


def clean_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]+", "", value.strip())
    if len(token) < 3:
        raise ValueError("Trigger word must contain at least 3 letters or numbers.")
    return token[:40]


def project_dir(slug: str) -> Path:
    path = PROJECT_ROOT / slugify(slug)
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError("Project not found.")
    return path


def read_config(path: Path) -> dict:
    return json.loads((path / "project.json").read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def image_records(path: Path) -> list[dict]:
    records = []
    images = path / "images"
    captions = path / "captions"
    for image in sorted(images.iterdir()) if images.exists() else []:
        if image.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        caption_file = captions / f"{image.stem}.txt"
        records.append(
            {
                "name": image.name,
                "caption": caption_file.read_text(encoding="utf-8") if caption_file.exists() else "",
                "captioned": caption_file.exists() and bool(caption_file.read_text(encoding="utf-8").strip()),
                "url": f"/api/projects/{path.name}/images/{image.name}",
            }
        )
    return records


def project_payload(path: Path) -> dict:
    config = read_config(path)
    config["images"] = image_records(path)
    return config


def caption_prompt(trigger: str) -> str:
    return (
        "Write one accurate training caption for this image. "
        f"Use the unique subject token {trigger} exactly once when referring to the primary subject. "
        "Describe observable appearance, hair, clothing, pose, facial expression, camera framing, "
        "background, lighting, and composition in natural language. "
        "Do not guess identity, camera settings, ethnicity, age, intent, or events not visible. "
        "Do not add commentary or headings. Return only the caption."
    )


def request_caption(image_path: Path, trigger: str) -> str:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    request_body = {
        "model": VISION_MODEL,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": caption_prompt(trigger),
                "images": [encoded],
            }
        ],
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=420) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Caption model unavailable: {error}") from error
    caption = result.get("message", {}).get("content", "").strip()
    if not caption:
        raise RuntimeError("The caption model returned an empty response.")
    return caption


def export_project(path: Path) -> dict:
    config = read_config(path)
    records = image_records(path)
    missing = [record["name"] for record in records if not record["captioned"]]
    if not records:
        raise ValueError("Add images before exporting.")
    if missing:
        raise ValueError("Every image needs a reviewed caption before export.")

    dataset_dir = DATASET_ROOT / path.name
    output_dir = OUTPUT_ROOT / path.name
    dataset_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    dataset_dir.chmod(0o700)
    output_dir.chmod(0o700)
    for record in records:
        image = path / "images" / record["name"]
        caption = path / "captions" / f"{image.stem}.txt"
        shutil.copy2(image, dataset_dir / image.name)
        shutil.copy2(caption, dataset_dir / caption.name)
        (dataset_dir / image.name).chmod(0o600)
        (dataset_dir / caption.name).chmod(0o600)

    workflow = json.loads(WORKFLOW_TEMPLATE.read_text(encoding="utf-8"))
    for node in workflow.get("nodes", []):
        if node.get("type") == "TrainDatasetAdd":
            values = node["widgets_values"]
            values[3] = str(dataset_dir)
            values[4] = config["trigger_word"]
        elif node.get("type") == "InitFluxLoRATraining":
            values = node["widgets_values"]
            values[0] = f"{path.name}_lora"
            values[1] = f"{output_dir}/"
    workflow_path = COMFY_WORKFLOWS / f"lora_studio_{path.name}_train.json"
    write_json(workflow_path, workflow)

    config["exported_at"] = datetime.now().isoformat(timespec="seconds")
    config["dataset_dir"] = str(dataset_dir)
    config["workflow_path"] = str(workflow_path)
    config["output_dir"] = str(output_dir)
    write_json(path / "project.json", config)
    return {
        "dataset_dir": str(dataset_dir),
        "workflow_path": str(workflow_path),
        "output_dir": str(output_dir),
        "images": len(records),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_ROOT), **kwargs)

    def log_message(self, format: str, *args) -> None:
        sys.stdout.write(f"[lora-studio] {format % args}\n")

    def json_response(self, payload: dict | list, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def error_response(self, error: Exception, status: int = 400) -> None:
        self.json_response({"error": str(error)}, status)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parts = [part for part in unquote(urlparse(self.path).path).split("/") if part]
        try:
            if parts == ["api", "projects"]:
                projects = [
                    project_payload(path)
                    for path in sorted(PROJECT_ROOT.iterdir())
                    if (path / "project.json").exists()
                ]
                self.json_response(projects)
                return
            if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                self.json_response(project_payload(project_dir(parts[2])))
                return
            if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "images":
                image = project_dir(parts[2]) / "images" / Path(parts[4]).name
                if image.suffix.lower() not in ALLOWED_EXTENSIONS or not image.exists():
                    raise FileNotFoundError("Image not found.")
                body = image.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(image.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        except FileNotFoundError as error:
            self.error_response(error, HTTPStatus.NOT_FOUND)
            return
        super().do_GET()

    def do_POST(self) -> None:
        parts = [part for part in unquote(urlparse(self.path).path).split("/") if part]
        try:
            payload = self.read_json()
            if parts == ["api", "projects"]:
                name = payload.get("name", "").strip()
                slug = slugify(name)
                if not slug:
                    raise ValueError("Enter a project name.")
                trigger = clean_token(payload.get("trigger_word", ""))
                path = PROJECT_ROOT / slug
                if path.exists():
                    raise ValueError("A project with this name already exists.")
                (path / "images").mkdir(parents=True, mode=0o700)
                (path / "captions").mkdir(mode=0o700)
                path.chmod(0o700)
                write_json(
                    path / "project.json",
                    {
                        "name": name,
                        "slug": slug,
                        "trigger_word": trigger,
                        "model": VISION_MODEL,
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                    },
                )
                self.json_response(project_payload(path), HTTPStatus.CREATED)
                return
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "upload":
                path = project_dir(parts[2])
                stored = []
                for file in payload.get("files", []):
                    name = Path(file.get("name", "")).name
                    extension = Path(name).suffix.lower()
                    if extension not in ALLOWED_EXTENSIONS:
                        raise ValueError(f"{name}: only JPG, PNG, or WEBP files are accepted.")
                    raw = file.get("data", "").split(",", 1)[-1]
                    content = base64.b64decode(raw, validate=True)
                    if len(content) > MAX_UPLOAD_BYTES:
                        raise ValueError(f"{name}: image is over the 30 MB limit.")
                    target = path / "images" / name
                    count = 1
                    while target.exists():
                        target = path / "images" / f"{Path(name).stem}_{count}{extension}"
                        count += 1
                    target.write_bytes(content)
                    target.chmod(0o600)
                    stored.append(target.name)
                self.json_response({"stored": stored, "project": project_payload(path)})
                return
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "caption":
                path = project_dir(parts[2])
                image = path / "images" / Path(payload.get("name", "")).name
                if not image.exists():
                    raise FileNotFoundError("Image not found.")
                caption = request_caption(image, read_config(path)["trigger_word"])
                (path / "captions" / f"{image.stem}.txt").write_text(caption + "\n", encoding="utf-8")
                self.json_response({"name": image.name, "caption": caption})
                return
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "save-caption":
                path = project_dir(parts[2])
                image = path / "images" / Path(payload.get("name", "")).name
                caption = payload.get("caption", "").strip()
                if not image.exists() or not caption:
                    raise ValueError("Image and caption are required.")
                (path / "captions" / f"{image.stem}.txt").write_text(caption + "\n", encoding="utf-8")
                self.json_response({"saved": True})
                return
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "export":
                self.json_response(export_project(project_dir(parts[2])))
                return
            self.error_response(ValueError("Unknown action."), HTTPStatus.NOT_FOUND)
        except FileNotFoundError as error:
            self.error_response(error, HTTPStatus.NOT_FOUND)
        except Exception as error:
            self.error_response(error)


def main() -> None:
    host = os.environ.get("LORA_STUDIO_HOST", "127.0.0.1")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("LORA_STUDIO_PORT", "4173"))
    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
    PROJECT_ROOT.chmod(0o700)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"LoRA Studio running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
