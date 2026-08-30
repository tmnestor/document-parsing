"""YAML loading utilities for ground truth, layout registries, and generation config.

All loaders fail fast with diagnostic errors per CLAUDE.md requirements.
"""

import os
from pathlib import Path

import yaml


def load_ground_truth(path: Path) -> dict:
    """Load a ground truth YAML file.

    Args:
        path: Path to the YAML file.

    Returns:
        Dict mapping case IDs to entry dicts.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If YAML cannot be parsed.
    """
    if not path.exists():
        msg = (
            "Ground truth file not found.\n"
            f"  What:     {path} does not exist.\n"
            f"  Where:    {path.resolve()} -> referenced from "
            "config/generation_config.yml -> document_types.<type>.ground_truth\n"
            "  Expected: a YAML mapping of case ids to entries, e.g.\n"
            "              CASE001:\n"
            "                DOCUMENT_TYPE: INVOICE\n"
            "  Recover:  create the file, or correct the path under "
            "document_types in config/generation_config.yml."
        )
        raise FileNotFoundError(msg)

    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = (
            "Ground truth file is not valid YAML.\n"
            f"  What:     {exc}\n"
            f"  Where:    {path.resolve()}\n"
            "  Expected: parseable YAML — check indentation, colons and quoting, e.g.\n"
            "              CASE001:\n"
            '                SUPPLIER_NAME: "Coastal Plumbing"\n'
            "  Recover:  fix the syntax error at the line named above."
        )
        raise ValueError(msg) from exc

    if not isinstance(data, dict):
        msg = (
            "Ground truth file has the wrong top-level shape.\n"
            f"  What:     expected a mapping, got {type(data).__name__}.\n"
            f"  Where:    {path.resolve()} -> document root\n"
            "  Expected: each top-level key is a case id, e.g.\n"
            "              CASE001:\n"
            "                DOCUMENT_TYPE: INVOICE\n"
            "  Recover:  wrap the entries in a top-level mapping keyed by case id."
        )
        raise ValueError(msg)

    return data


def load_layout_registry(path: Path) -> dict:
    """Load a layout registry YAML file.

    Args:
        path: Path to the layout YAML file.

    Returns:
        Dict mapping layout IDs to layout config dicts.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If YAML cannot be parsed.
    """
    if not path.exists():
        msg = (
            "Layout registry not found.\n"
            f"  What:     {path} does not exist.\n"
            f"  Where:    {path.resolve()} -> referenced from "
            "config/generation_config.yml -> document_types.<type>.layouts\n"
            "  Expected: a YAML file under config/layouts/, e.g.\n"
            "              layouts:\n"
            "                acme_standard:\n"
            "                  body: [...]\n"
            "  Recover:  create the file under config/layouts/, or correct the "
            "path under document_types in config/generation_config.yml."
        )
        raise FileNotFoundError(msg)

    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = (
            "Layout registry is not valid YAML.\n"
            f"  What:     {exc}\n"
            f"  Where:    {path.resolve()}\n"
            "  Expected: parseable YAML — check indentation, colons and merge keys, e.g.\n"
            "              layouts:\n"
            "                acme_standard:\n"
            "                  body: [...]\n"
            "  Recover:  fix the syntax error at the line named above."
        )
        raise ValueError(msg) from exc

    if not isinstance(data, dict):
        msg = (
            "Layout registry has the wrong top-level shape.\n"
            f"  What:     expected a mapping, got {type(data).__name__}.\n"
            f"  Where:    {path.resolve()} -> document root\n"
            "  Expected: a mapping of layout ids to layout dicts, optionally "
            "wrapped in a single 'layouts:' key, e.g.\n"
            "              layouts:\n"
            "                acme_standard:\n"
            "                  body: [...]\n"
            "  Recover:  wrap the layouts in a top-level mapping."
        )
        raise ValueError(msg)

    # If the YAML has a top-level "layouts:" wrapper, extract the inner dict.
    # Underscore-prefixed siblings are anchor definitions (e.g. `_bank_base`,
    # `_cba`) used for de-duplication via YAML merge keys — they are not
    # layouts and must not surface in the registry. Any other sibling is a
    # mis-indented layout and must fail fast rather than be silently
    # swallowed as a bogus "layout id".
    if "layouts" in data and isinstance(data["layouts"], dict):
        stray = sorted(k for k in data if k != "layouts" and not str(k).startswith("_"))
        if stray:
            msg = (
                f"Unexpected top-level key(s) {stray} in {path.resolve()}.\n"
                f"  What:     only 'layouts:' and underscore-prefixed anchor "
                f"definitions may sit at the top level.\n"
                f"  Where:    {path.resolve()}\n"
                f"  Expected: layouts:\\n  <layout_id>: ...   plus optional "
                f"_anchor: &anchor blocks.\n"
                f"  Recover:  indent {stray} under 'layouts:', or rename to "
                f"'_{stray[0]}' if it is an anchor definition."
            )
            raise ValueError(msg)
        return data["layouts"]

    return data


# Subdirectories of the dataset root, derived rather than configured separately
# so a new vintage is one edit and everything moves together.
#
# The export is deliberately NOT here. `generate` and `serialise` write working
# data that belongs to this repository's run; the export is a hand-off aimed at
# the scoring repository, and its destination is named by whoever asks for it
# (`export --target`). Deriving an `exports_dir` gave that one question two
# owners -- and since build_corpus.sh always passed `--target`, the configured
# answer was never the one used. A YAML key nobody reads is a lie in a file
# whose whole job is to state what a run does.
_DERIVED_FROM_ROOT = {"output_dir": "output", "derived_dir": "derived"}

# Keys that once existed and would now be silently ignored. Failing on them is
# the difference between "this does nothing" and "this does something you
# cannot see".
_RETIRED_KEYS = {
    "exports_dir": (
        "the export's destination is named per run by 'export --target' "
        "(and 'degrade --out'), so a configured directory here would never be read"
    ),
}


def resolve_data_dir(value: str, config_path: Path) -> Path:
    """Turn a configured data directory into an absolute path.

    **Generated corpora belong outside the working tree.** A run writes hundreds
    of megabytes of images, and keeping that inside the repository leaves only
    `.gitignore` between it and the history -- one `git add -f`, or a careless
    edit to that file, is enough. Nothing here forbids an in-repo path; that is
    the operator's call, and it is visible in the YAML either way. The shipped
    default is a sibling directory.

    Two expansions make a portable location expressible in YAML, which a literal
    relative path cannot be:

    * `${VAR}` and `$VAR` from the environment, so several checkouts on a shared
      machine can point at one store without editing tracked config;
    * `~`, for the obvious reason.

    A relative path resolves against the REPOSITORY ROOT -- the parent of the
    config file's directory -- not the working directory, so `../corpus-data`
    means the same thing however the command was invoked.

    Args:
        value: The configured path, before expansion.
        config_path: Path to generation_config.yml, used to locate the root.

    Returns:
        An absolute path. It is not created here; the caller creates it when it
        writes.
    """
    expanded = Path(os.path.expandvars(str(value))).expanduser()
    if expanded.is_absolute():
        return expanded
    root = config_path.resolve().parent.parent
    return (root / expanded).resolve()


def load_generation_config(path: Path) -> dict:
    """Load the master generation config.

    Args:
        path: Path to generation_config.yml.

    Returns:
        Config dict with `output_dir`, `derived_dir`, `ground_truth_dir`, and
        a `document_types` mapping, each entry naming its `ground_truth`,
        `layouts` and `output_subdir`.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If YAML cannot be parsed or required keys missing.
    """
    if not path.exists():
        msg = (
            "Generation config not found.\n"
            f"  What:     {path} does not exist.\n"
            f"  Where:    {path.resolve()}\n"
            "  Expected: config/generation_config.yml declaring every required key, e.g.\n"
            "              output_dir: output\n"
            "              derived_dir: derived\n"
            "              ground_truth_dir: ground_truth\n"
            "              document_types: {...}\n"
            "  Recover:  create config/generation_config.yml with the keys above."
        )
        raise FileNotFoundError(msg)

    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = (
            "Generation config is not valid YAML.\n"
            f"  What:     {exc}\n"
            f"  Where:    {path.resolve()}\n"
            "  Expected: parseable YAML — check indentation, colons and quoting, e.g.\n"
            "              output_dir: output\n"
            "  Recover:  fix the syntax error at the line named above."
        )
        raise ValueError(msg) from exc

    required_keys = ["dataset_root", "ground_truth_dir", "document_types"]
    for key in required_keys:
        if key not in data:
            msg = (
                "Generation config is missing a required key.\n"
                f"  What:     '{key}' is not declared.\n"
                f"  Where:    {path.resolve()} -> {key}\n"
                f"  Expected: every key of {required_keys} is required — none has a "
                f"Python default, e.g.\n"
                f"              {key}: <value>\n"
                f"  Recover:  add '{key}:' at the top level of "
                "config/generation_config.yml."
            )
            raise ValueError(msg)

    for key, why in _RETIRED_KEYS.items():
        if key in data:
            msg = (
                "Generation config declares a key that is no longer read.\n"
                f"  What:     '{key}' has been retired — {why}.\n"
                f"  Where:    {path.resolve()} -> {key}\n"
                "  Expected: the key absent, e.g.\n"
                "              dataset_root: ../document-parsing-data/synthetic_data_2026-08-25\n"
                "              ground_truth_dir: ground_truth\n"
                f"  Recover:  delete the '{key}:' line from "
                "config/generation_config.yml and pass the destination on the "
                "command line instead:\n"
                "              python -m generators.pipeline export --target <dir>"
            )
            raise ValueError(msg)

    # Resolved here, once, so every command sees the same absolute paths and no
    # caller has to remember to expand them. `ground_truth_dir` is deliberately
    # NOT resolved: it is tracked input living beside the config, not output.
    #
    # Both writable directories hang off one dated root, so starting a new
    # vintage is a single edit and images and transcripts cannot end up
    # describing different runs. An explicitly configured `output_dir` or
    # `derived_dir` still wins, for the case where one of them must live
    # somewhere else entirely.
    root = resolve_data_dir(data["dataset_root"], path)
    data["dataset_root"] = root
    for key, leaf in _DERIVED_FROM_ROOT.items():
        data[key] = resolve_data_dir(data[key], path) if key in data else root / leaf

    return data
