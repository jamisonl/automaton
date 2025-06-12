import json
import os
from pathlib import Path
from typing import Dict, List, Optional


def get_config_dir() -> Path:
    if os.name == "nt":
        config_dir = Path(os.environ.get("APPDATA", "")) / "automaton"
    else:
        config_dir = Path.home() / ".config" / "automaton"

    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_file() -> Path:
    return get_config_dir() / "config.json"


def get_default_models() -> List[str]:
    return [
        "gemini-2.5-flash-preview-05-20",
        "gemini-2.5-pro-preview-06-05",
    ]


def load_config() -> Dict:
    config_file = get_config_file()
    default_config = {
        "model_name": "gemini-2.5-flash-preview-05-20",
        "api_key": os.environ.get("GEMINI_API_KEY", ""),
        "temperature": 0,
        "max_tokens": 65535,
    }

    if not config_file.exists():
        save_config(default_config)
        return default_config

    try:
        with open(config_file, "r") as f:
            config = json.load(f)

        for key, value in default_config.items():
            if key not in config:
                config[key] = value

        return config
    except (json.JSONDecodeError, FileNotFoundError):
        save_config(default_config)
        return default_config


def save_config(config: Dict) -> None:
    config_file = get_config_file()
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)


def get_model_name() -> str:
    config = load_config()
    return config.get("model_name", "gemini-2.5-flash-preview-05-20")


def set_model_name(model_name: str) -> None:
    config = load_config()
    config["model_name"] = model_name
    save_config(config)


def get_api_key() -> str:
    config = load_config()
    api_key = config.get("api_key", "") or os.environ.get("GEMINI_API_KEY", "")
    return api_key


def fetch_available_models() -> List[str]:
    api_key = get_api_key()
    if not api_key:
        return get_default_models()

    try:
        import requests

        url = "https://generativelanguage.googleapis.com/v1beta/models"
        headers = {"x-goog-api-key": api_key}

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()
        models = data.get("models", [])

        gemini_models = []
        for model in models:
            model_name = model.get("name", "")
            supported_methods = model.get("supportedGenerationMethods", [])

            if (
                "gemini" in model_name.lower()
                and "generateContent" in supported_methods
            ):

                clean_name = model_name.replace("models/", "")
                gemini_models.append(clean_name)

        if gemini_models:

            gemini_models.sort(key=lambda x: ("latest" in x, x), reverse=True)
            return gemini_models
        else:
            return get_default_models()

    except Exception as e:
        print(f"Error fetching models: {e}")
        return get_default_models()


def get_model_config() -> Dict:
    config = load_config()
    return {
        "model_name": config.get("model_name", "gemini-2.5-flash-preview-05-20"),
        "api_key": get_api_key(),
        "temperature": config.get("temperature", 0),
        "max_tokens": config.get("max_tokens", 65535),
    }
