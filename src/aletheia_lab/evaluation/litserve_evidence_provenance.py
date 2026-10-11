"""Persist public verification material for the existing same-capture baseline.

The existing in-toto rule adapter is unchanged. Only public layout, signatures
and verification keys are retained; private ephemeral keys never leave memory.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from aletheia_lab.evaluation.model_load_provenance import verify_chain
from aletheia_lab.evaluation.model_load_retention import encode
from aletheia_lab.filesystem import write_new_file


def sign_bundle(digest: str, directory: Path) -> str:
    library = importlib.import_module("in_toto.verifylib")
    original = library.in_toto_verify
    public: dict[str, Any] = {}

    def capture(layout: Any, keys: dict[str, Any], **kwargs: Any) -> Any:
        public.update(layout=layout.to_dict(), verification_keys=keys)
        return original(layout, keys, **kwargs)

    with patch.object(library, "in_toto_verify", capture):
        status = verify_chain({"receipt-set": digest}, {"receipt-set": digest}, directory)
    write_new_file(directory / "public-verification.json", encode(public).encode())
    return status


def verify_bundle(directory: Path) -> None:
    library = importlib.import_module("in_toto.verifylib")
    metadata = importlib.import_module("in_toto.models.metadata")
    public = json.loads((directory / "public-verification.json").read_bytes())
    layout = metadata.Metablock.from_dict(public["layout"])
    library.in_toto_verify(
        layout,
        public["verification_keys"],
        link_dir_path=str(directory),
        persist_inspection_links=False,
    )
