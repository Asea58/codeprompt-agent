"""Load and validate config + templates. All paths are relative to project root."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")
TEMPLATES_DIR = os.path.join(ROOT, "templates")
SUBJECTS_DIR = os.path.join(CONFIG_DIR, "subjects")
OUTPUT_DIR = os.path.join(ROOT, "output")


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_llm_config():
    """Return the active provider's config dict (with provider name injected)."""
    cfg = _read_json(os.path.join(CONFIG_DIR, "llm.json"))
    provider = cfg["provider"]
    providers = cfg["providers"]
    if provider not in providers:
        raise ValueError(
            f"llm.json: provider '{provider}' 不在 providers 中。可选: {list(providers)}"
        )
    p = dict(providers[provider])
    p["provider_name"] = provider
    return p


def load_reasons():
    """Return the reasons mapping table (drops _comment keys)."""
    data = _read_json(os.path.join(CONFIG_DIR, "reasons.json"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_reason(reason_key):
    reasons = load_reasons()
    if reason_key not in reasons:
        raise ValueError(
            f"未知的 reason '{reason_key}'。可选: {list(reasons)}"
        )
    return reasons[reason_key]


def list_subjects():
    if not os.path.isdir(SUBJECTS_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(SUBJECTS_DIR)
        if f.endswith(".json")
    )


def load_subject(subject):
    path = os.path.join(SUBJECTS_DIR, f"{subject}.json")
    if not os.path.isfile(path):
        raise ValueError(
            f"未知的 subject '{subject}'。可选: {list_subjects()}"
        )
    return _read_json(path)


def load_template(name):
    """name without extension, e.g. 'system_prompt' -> templates/system_prompt.md"""
    return _read_text(os.path.join(TEMPLATES_DIR, f"{name}.md"))


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR
