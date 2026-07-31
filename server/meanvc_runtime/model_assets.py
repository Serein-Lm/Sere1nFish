"""Download and validate the pinned MeanVC runtime model assets."""

from __future__ import annotations

import os
from pathlib import Path

import gdown
from huggingface_hub import hf_hub_download

MODEL_REPOSITORY = "ASLP-lab/MeanVC"
MODEL_FILES = ("fastu2++.pt", "meanvc_200ms.pt", "vocos.pt")
SPEAKER_CHECKPOINT_ID = "1-aE1NfzpRCLxA4GUxX9ITI3F9LlbtEGP"
MINIMUM_FILE_BYTES = 1024 * 1024


def _require_asset(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < MINIMUM_FILE_BYTES:
        raise RuntimeError(f"MeanVC model asset is incomplete: {path}")


def ensure_model_assets(model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    for filename in MODEL_FILES:
        destination = model_dir / filename
        if not destination.is_file() or destination.stat().st_size < MINIMUM_FILE_BYTES:
            hf_hub_download(
                repo_id=MODEL_REPOSITORY,
                filename=filename,
                local_dir=model_dir,
            )
        _require_asset(destination)

    speaker_checkpoint = model_dir / "wavlm_large_finetune.pth"
    if not speaker_checkpoint.is_file() or speaker_checkpoint.stat().st_size < MINIMUM_FILE_BYTES:
        temporary = speaker_checkpoint.with_suffix(".download")
        temporary.unlink(missing_ok=True)
        result = gdown.download(
            id=SPEAKER_CHECKPOINT_ID,
            output=str(temporary),
            quiet=False,
        )
        if not result:
            raise RuntimeError("Unable to download the MeanVC speaker checkpoint")
        temporary.replace(speaker_checkpoint)
    _require_asset(speaker_checkpoint)


def main() -> None:
    model_dir = Path(os.getenv("MEANVC_MODEL_DIR", "/models")).resolve()
    ensure_model_assets(model_dir)


if __name__ == "__main__":
    main()
