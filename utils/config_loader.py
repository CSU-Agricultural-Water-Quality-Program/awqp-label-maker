from __future__ import annotations

import re
from pathlib import Path

import yaml


def load_config(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def save_config(path: str | Path, config: dict) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=False)


def is_catalog_entry_active(entry: dict) -> bool:
    return entry.get("active", True)


def normalize_value(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def normalize_key_fragment(value: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().upper()).strip("_")
    return key or "ENTRY"


def next_available_key(section: dict[str, dict], base_key: str) -> str:
    if base_key not in section:
        return base_key

    suffix = 2
    while f"{base_key}_{suffix}" in section:
        suffix += 1
    return f"{base_key}_{suffix}"


def find_cross_section_conflicts(config: dict) -> list[str]:
    conflicts: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()

    for location_key, location in config["locations"].items():
        for treatment_key, treatment in config["treatments"].items():
            if normalize_value(location["id"]) == normalize_value(treatment["id"]):
                conflict = (
                    "ID",
                    location["id"],
                    location_key,
                    treatment_key,
                )
                if conflict not in seen:
                    conflicts.append(
                        f"Legacy ID overlap: location `{location_key}` and treatment "
                        f"`{treatment_key}` both use `{location['id']}`."
                    )
                    seen.add(conflict)
            if normalize_value(location["label"]) == normalize_value(treatment["label"]):
                conflict = (
                    "label",
                    location["label"],
                    location_key,
                    treatment_key,
                )
                if conflict not in seen:
                    conflicts.append(
                        f"Legacy label overlap: location `{location_key}` and treatment "
                        f"`{treatment_key}` are both named `{location['label']}`."
                    )
                    seen.add(conflict)

    return conflicts


def validate_catalog_entry(
    config: dict,
    *,
    section_name: str,
    entry_id: str,
    label: str,
    existing_key: str | None = None,
) -> list[str]:
    errors: list[str] = []
    clean_id = entry_id.strip()
    clean_label = label.strip()

    if not clean_id:
        errors.append("ID code is required.")
    elif not re.fullmatch(r"[A-Za-z0-9_]+", clean_id):
        errors.append("ID code may only contain letters, numbers, and underscores.")

    if not clean_label:
        errors.append("Display label is required.")

    if errors:
        return errors

    normalized_id = normalize_value(clean_id)
    normalized_label = normalize_value(clean_label)

    same_section = config[section_name]
    other_section_name = "treatments" if section_name == "locations" else "locations"
    other_section = config[other_section_name]

    for section_key, existing in same_section.items():
        if section_key == existing_key:
            continue
        if normalized_id == normalize_value(existing["id"]):
            errors.append(
                f"{section_name[:-1].capitalize()} ID `{clean_id}` already exists as `{section_key}`."
            )
        if normalized_label == normalize_value(existing["label"]):
            errors.append(
                f"{section_name[:-1].capitalize()} label `{clean_label}` already exists as `{section_key}`."
            )

    for existing_key, existing in other_section.items():
        if normalized_id == normalize_value(existing["id"]):
            errors.append(
                f"ID `{clean_id}` already exists in {other_section_name} as `{existing_key}`."
            )
        if normalized_label == normalize_value(existing["label"]):
            errors.append(
                f"Label `{clean_label}` already exists in {other_section_name} as `{existing_key}`."
            )

    return errors


def append_catalog_entry(
    config: dict,
    *,
    section_name: str,
    entry_id: str,
    label: str,
) -> str:
    section = config[section_name]
    base_key = normalize_key_fragment(entry_id if section_name == "locations" else label)
    entry_key = next_available_key(section, base_key)
    section[entry_key] = {"id": entry_id.strip(), "label": label.strip()}
    return entry_key


def update_catalog_entry(
    config: dict,
    *,
    section_name: str,
    entry_key: str,
    entry_id: str,
    label: str,
    active: bool,
) -> None:
    updated_entry = dict(config[section_name][entry_key])
    updated_entry["id"] = entry_id.strip()
    updated_entry["label"] = label.strip()
    if active:
        updated_entry.pop("active", None)
    else:
        updated_entry["active"] = False
    config[section_name][entry_key] = updated_entry
