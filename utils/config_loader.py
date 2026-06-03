from __future__ import annotations

import json
import re
from pathlib import Path


def load_config(path: str | Path) -> dict:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        if config_path.suffix.lower() == ".json":
            return json.load(handle)
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "PyYAML is required to load YAML configs. Convert the catalog to JSON or install PyYAML."
            ) from exc
        return yaml.safe_load(handle)


def save_config(path: str | Path, config: dict) -> None:
    config_path = Path(path)
    with config_path.open("w", encoding="utf-8") as handle:
        if config_path.suffix.lower() == ".json":
            json.dump(config, handle, indent=2)
            handle.write("\n")
            return
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "PyYAML is required to save YAML configs. Save to JSON or install PyYAML."
            ) from exc
        yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=False)


def is_catalog_entry_active(entry: dict) -> bool:
    return entry.get("active", True)


def is_catalog_entry_legacy_only(entry: dict) -> bool:
    return entry.get("legacy_only", False)


def location_allows_blank_treatment(entry: dict) -> bool:
    return entry.get("allow_blank_treatment", True)


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


def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value not in seen:
            unique_values.append(value)
            seen.add(value)
    return unique_values


def parse_list_field(value: str) -> list[str]:
    if not value.strip():
        return []
    return unique_preserving_order(
        [item.strip() for item in value.split(",") if item.strip()]
    )


def serialize_list_field(values: list[str]) -> str:
    return ", ".join(values)


def get_entry_list_field(entry: dict, field_name: str) -> list[str]:
    value = entry.get(field_name, [])
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return parse_list_field(value)
    return []


def get_treatment_parent_location(entry: dict) -> str:
    return str(entry.get("parent_location", "")).strip()


def get_treatment_r_label(entry: dict) -> str:
    return str(entry.get("r_label", entry.get("label", ""))).strip()


def get_treatment_group(entry: dict) -> str:
    return str(entry.get("treatment_group", "")).strip()


def get_entry_parser_tokens(entry: dict) -> list[str]:
    base_tokens = [str(entry.get("id", "")).strip()]
    base_tokens.extend(get_entry_list_field(entry, "aliases"))
    base_tokens.extend(get_entry_list_field(entry, "legacy_aliases"))
    return [token for token in unique_preserving_order(base_tokens) if token]


def get_location_children(config: dict, location_key: str, *, active_only: bool = False) -> list[str]:
    treatment_keys: list[str] = []
    for treatment_key, treatment in config["treatments"].items():
        if treatment_key == "blank":
            continue
        if active_only and not is_catalog_entry_active(treatment):
            continue
        if get_treatment_parent_location(treatment) == location_key:
            treatment_keys.append(treatment_key)
    return treatment_keys


def get_location_treatment_keys(
    config: dict,
    location_key: str,
    *,
    include_blank: bool = True,
    active_only: bool = True,
) -> list[str]:
    treatment_keys: list[str] = []
    if (
        include_blank
        and "blank" in config["treatments"]
        and location_allows_blank_treatment(config["locations"][location_key])
    ):
        treatment_keys.append("blank")

    for treatment_key, treatment in config["treatments"].items():
        if treatment_key == "blank":
            continue
        if active_only and not is_catalog_entry_active(treatment):
            continue
        if get_treatment_parent_location(treatment) == location_key:
            treatment_keys.append(treatment_key)
    return treatment_keys


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
    parent_location: str = "",
    aliases: list[str] | None = None,
    legacy_aliases: list[str] | None = None,
    r_label: str = "",
    treatment_group: str = "",
    legacy_only: bool = False,
) -> list[str]:
    errors: list[str] = []
    clean_id = entry_id.strip()
    clean_label = label.strip()
    clean_parent_location = parent_location.strip()
    clean_r_label = r_label.strip()
    clean_treatment_group = treatment_group.strip()
    aliases = aliases or []
    legacy_aliases = legacy_aliases or []

    requires_id = not (section_name == "treatments" and existing_key == "blank")
    if requires_id and not clean_id:
        errors.append("ID code is required.")
    elif not re.fullmatch(r"[A-Za-z0-9_]+", clean_id):
        errors.append("ID code may only contain letters, numbers, and underscores.")

    if not clean_label:
        errors.append("Display label is required.")

    if section_name == "treatments" and existing_key != "blank" and not legacy_only and not clean_parent_location:
        errors.append("Parent location is required for treatments.")

    if clean_parent_location and clean_parent_location not in config["locations"]:
        errors.append(f"Parent location `{clean_parent_location}` does not exist.")
    elif (
        section_name == "treatments"
        and clean_parent_location
        and not legacy_only
        and not is_catalog_entry_active(config["locations"][clean_parent_location])
    ):
        errors.append("Active treatments must use an active parent location.")

    if section_name == "treatments" and clean_treatment_group and not re.fullmatch(r"[A-Za-z0-9_]+", clean_treatment_group):
        errors.append("Treatment group may only contain letters, numbers, and underscores.")

    if section_name == "treatments" and clean_r_label and not clean_r_label.strip():
        errors.append("R label cannot be blank when provided.")

    for token in [*aliases, *legacy_aliases]:
        if not re.fullmatch(r"[A-Za-z0-9_\\-]+", token):
            errors.append(
                f"Alias `{token}` may only contain letters, numbers, underscores, and hyphens."
            )

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
    parent_location: str = "",
    aliases: list[str] | None = None,
    legacy_aliases: list[str] | None = None,
    r_label: str = "",
    treatment_group: str = "",
    legacy_only: bool = False,
    active: bool = True,
) -> str:
    section = config[section_name]
    base_key = normalize_key_fragment(entry_id or label)
    entry_key = next_available_key(section, base_key)
    entry = {"id": entry_id.strip(), "label": label.strip()}
    aliases = aliases or []
    legacy_aliases = legacy_aliases or []

    if aliases:
        entry["aliases"] = aliases
    if legacy_aliases:
        entry["legacy_aliases"] = legacy_aliases
    if section_name == "treatments" and entry_key != "blank":
        if parent_location.strip():
            entry["parent_location"] = parent_location.strip()
        if r_label.strip():
            entry["r_label"] = r_label.strip()
        if treatment_group.strip():
            entry["treatment_group"] = treatment_group.strip()
    if legacy_only:
        entry["legacy_only"] = True
    if not active:
        entry["active"] = False

    section[entry_key] = entry
    return entry_key


def update_catalog_entry(
    config: dict,
    *,
    section_name: str,
    entry_key: str,
    entry_id: str,
    label: str,
    active: bool,
    parent_location: str = "",
    aliases: list[str] | None = None,
    legacy_aliases: list[str] | None = None,
    r_label: str = "",
    treatment_group: str = "",
    legacy_only: bool = False,
) -> None:
    updated_entry = dict(config[section_name][entry_key])
    updated_entry["id"] = entry_id.strip()
    updated_entry["label"] = label.strip()
    aliases = aliases or []
    legacy_aliases = legacy_aliases or []

    if aliases:
        updated_entry["aliases"] = aliases
    else:
        updated_entry.pop("aliases", None)

    if legacy_aliases:
        updated_entry["legacy_aliases"] = legacy_aliases
    else:
        updated_entry.pop("legacy_aliases", None)

    if section_name == "treatments" and entry_key != "blank":
        if parent_location.strip():
            updated_entry["parent_location"] = parent_location.strip()
        else:
            updated_entry.pop("parent_location", None)

        if r_label.strip():
            updated_entry["r_label"] = r_label.strip()
        else:
            updated_entry.pop("r_label", None)

        if treatment_group.strip():
            updated_entry["treatment_group"] = treatment_group.strip()
        else:
            updated_entry.pop("treatment_group", None)

    if legacy_only:
        updated_entry["legacy_only"] = True
    else:
        updated_entry.pop("legacy_only", None)

    if active:
        updated_entry.pop("active", None)
    else:
        updated_entry["active"] = False
    config[section_name][entry_key] = updated_entry
