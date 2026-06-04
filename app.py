from __future__ import annotations

import hmac
import importlib.util
import io
import os
import zipfile
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from utils.config_loader import (
    append_catalog_entry,
    find_cross_section_conflicts,
    get_entry_list_field,
    get_entry_parser_tokens,
    get_location_treatment_keys,
    get_treatment_group,
    get_treatment_parent_location,
    get_treatment_r_label,
    is_catalog_entry_active,
    is_catalog_entry_legacy_only,
    load_config,
    parse_list_field,
    save_config,
    serialize_list_field,
    update_catalog_entry,
    validate_catalog_entry,
)
from utils.label_builder import (
    add_group_to_plan,
    build_output_tables,
    empty_plan,
    get_group_duplicate_keys,
    remove_group_from_plan,
)
from utils.table_appender import append_uploaded_tables


st.set_page_config(
    page_title="AWQP Label Maker",
    layout="wide",
)


CONFIG_PATH = Path(__file__).parent / "config" / "config.json"
PASSWORD_HELP_PATH = (
    r"D:\OneDrive - Colostate\AWQP_Sharepoint\Water_Quality_Project\Research\Edge of "
    r"Field Monitoring and Data\AWQP Label Maker Tool\Label Edit Password.txt"
)
CONFIG = load_config(CONFIG_PATH)
AWQP_HOME_URL = "https://agsci.colostate.edu/waterquality/"
AWQP_LOGO_URL = (
    "https://agsci.colostate.edu/waterquality/wp-content/uploads/sites/160/2024/05/"
    "AWQP_horizontalhighres.png"
)
ALS_R_EXPORT_HEADER = """# Dictionaries for interpreting sample ID codes
# Generated from the AWQP Label Editor.
# Paste this text over the existing dictionaries in the ALS Data Cleaning Tool.
"""


def default_irr_str(event_number: str) -> str:
    if event_number.startswith("S"):
        return event_number[1:] or event_number
    return str(int(event_number))


def csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8")


def zip_exports(tables: dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, frame in tables.items():
            filename = name.lower().replace(" ", "_") + ".csv"
            zf.writestr(filename, csv_bytes(frame))
    buffer.seek(0)
    return buffer.getvalue()


def workbook_bytes(tables: dict[str, pd.DataFrame]) -> bytes:
    if importlib.util.find_spec("openpyxl") is None:
        raise ModuleNotFoundError("openpyxl is required for Excel export.")
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in tables.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
    buffer.seek(0)
    return buffer.getvalue()


def excel_export_available() -> bool:
    return importlib.util.find_spec("openpyxl") is not None


def deep_copy_catalog(config: dict) -> dict:
    return {
        **config,
        "locations": {key: dict(value) for key, value in config["locations"].items()},
        "treatments": {key: dict(value) for key, value in config["treatments"].items()},
    }


def dated_filename(prefix: str, extension: str, filename_date: date | None = None) -> str:
    return f"{prefix}_{(filename_date or date.today()).strftime('%Y-%m-%d')}.{extension}"


def format_r_vector(values: list[str]) -> str:
    quoted = ['"' + value.replace('"', '\\"') + '"' for value in values]
    if len(quoted) == 1:
        return quoted[0]
    return "c(" + ", ".join(quoted) + ")"


def build_location_dict_entries(config: dict) -> list[tuple[str, list[str]]]:
    entries: list[tuple[str, list[str]]] = []
    for location_key, location in config["locations"].items():
        tokens = get_entry_parser_tokens(location)
        for treatment_key, treatment in config["treatments"].items():
            if treatment_key == "blank":
                continue
            if get_treatment_parent_location(treatment) != location_key:
                continue
            tokens.extend(get_entry_parser_tokens(treatment))
        tokens = [token for token in dict.fromkeys(tokens) if token]
        if tokens:
            entries.append((location["label"], tokens))
    entries.append(("Method Blank", ["Method Blank"]))
    entries.append(("Lab Control Sample", ["Lab Control Sample"]))
    return entries


def build_treatment_dict_entries(config: dict) -> list[tuple[str, list[str]]]:
    grouped_tokens: dict[str, list[str]] = defaultdict(list)
    for treatment_key, treatment in config["treatments"].items():
        if treatment_key == "blank":
            continue
        grouped_tokens[get_treatment_r_label(treatment)].extend(get_entry_parser_tokens(treatment))

    entries: list[tuple[str, list[str]]] = []
    for label, tokens in grouped_tokens.items():
        cleaned_tokens = [token for token in dict.fromkeys(tokens) if token]
        if label and cleaned_tokens:
            entries.append((label, cleaned_tokens))
    return entries


def build_als_r_dictionaries_text(config: dict) -> str:
    location_lines = [
        f'  "{label}" = {format_r_vector(tokens)}'
        for label, tokens in build_location_dict_entries(config)
    ]
    treatment_lines = [
        f'  "{label}" = {format_r_vector(tokens)}'
        for label, tokens in build_treatment_dict_entries(config)
    ]

    sections = [
        ALS_R_EXPORT_HEADER.strip(),
        "# Keep ARDEC numeric-free until the R parser can safely handle ARDEC 2200.",
        "location.dict <- list(",
        ",\n".join(location_lines),
        ")\n",
        "# Kerbel and AVRC STAR share analytical treatment groups even though their sample-code tokens differ.",
        "trt.dict <- list(",
        ",\n".join(treatment_lines),
        ")",
    ]
    return "\n".join(sections)


def get_secret_value(secret_names: tuple[str, ...], source: Mapping[str, object]) -> str:
    for secret_name in secret_names:
        secret_value = source.get(secret_name, "")
        if isinstance(secret_value, str) and secret_value:
            return secret_value
        if secret_value and not isinstance(secret_value, Mapping):
            return str(secret_value)

    # Allow secrets to be grouped under TOML sections on hosted deployments.
    for nested_value in source.values():
        if isinstance(nested_value, Mapping):
            secret_value = get_secret_value(secret_names, nested_value)
            if secret_value:
                return secret_value

    return ""


def get_admin_password() -> str:
    secret_names = ("admin_password", "awqp_admin_password", "AWQP_ADMIN_PASSWORD")

    try:
        secret_value = get_secret_value(secret_names, st.secrets)
    except Exception:
        secret_value = ""
    if secret_value:
        return secret_value

    for env_name in ("AWQP_ADMIN_PASSWORD", "admin_password", "awqp_admin_password"):
        env_value = os.getenv(env_name, "")
        if env_value:
            return env_value
    return ""


def get_active_catalog_keys(entries: dict[str, dict]) -> list[str]:
    return [key for key, value in entries.items() if is_catalog_entry_active(value)]


def count_active_catalog_entries(entries: dict[str, dict], *, exclude_key: str | None = None) -> int:
    return sum(
        1
        for key, value in entries.items()
        if key != exclude_key and is_catalog_entry_active(value)
    )


def make_location_editor_rows(config: dict) -> list[dict[str, object]]:
    return [
        {
            "Key": key,
            "ID": entry["id"],
            "Label": entry["label"],
            "Aliases": serialize_list_field(get_entry_list_field(entry, "aliases")),
            "Legacy Aliases": serialize_list_field(get_entry_list_field(entry, "legacy_aliases")),
            "Allow Blank": entry.get("allow_blank_treatment", True),
            "Active": is_catalog_entry_active(entry),
            "Legacy Only": is_catalog_entry_legacy_only(entry),
        }
        for key, entry in config["locations"].items()
    ]


def make_treatment_editor_rows(config: dict) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    editable_rows: list[dict[str, object]] = []
    system_rows: list[dict[str, object]] = []
    for key, entry in config["treatments"].items():
        row = {
            "Key": key,
            "Parent Location": get_treatment_parent_location(entry),
            "ID": entry["id"],
            "Label": entry["label"],
            "Treatment Group": get_treatment_group(entry),
            "R Label": get_treatment_r_label(entry),
            "Aliases": serialize_list_field(get_entry_list_field(entry, "aliases")),
            "Legacy Aliases": serialize_list_field(get_entry_list_field(entry, "legacy_aliases")),
            "Active": is_catalog_entry_active(entry),
            "Legacy Only": is_catalog_entry_legacy_only(entry),
        }
        if key == "blank":
            system_rows.append(row)
        else:
            editable_rows.append(row)
    return editable_rows, system_rows


def make_new_treatment_seed_rows() -> list[dict[str, object]]:
    return [
        {
            "ID": "",
            "Label": "",
            "Treatment Group": "",
            "R Label": "",
            "Aliases": "",
            "Legacy Aliases": "",
            "Legacy Only": False,
            "Active": True,
        }
    ]


def normalize_new_treatment_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized_rows: list[dict[str, object]] = []
    for row in rows:
        normalized_row = {
            "ID": "" if pd.isna(row.get("ID", "")) else str(row.get("ID", "")).strip(),
            "Label": "" if pd.isna(row.get("Label", "")) else str(row.get("Label", "")).strip(),
            "Treatment Group": (
                ""
                if pd.isna(row.get("Treatment Group", ""))
                else str(row.get("Treatment Group", "")).strip()
            ),
            "R Label": (
                "" if pd.isna(row.get("R Label", "")) else str(row.get("R Label", "")).strip()
            ),
            "Aliases": "" if pd.isna(row.get("Aliases", "")) else str(row.get("Aliases", "")).strip(),
            "Legacy Aliases": (
                ""
                if pd.isna(row.get("Legacy Aliases", ""))
                else str(row.get("Legacy Aliases", "")).strip()
            ),
            "Legacy Only": bool(row.get("Legacy Only", False)),
            "Active": bool(row.get("Active", True)),
        }
        has_values = any(
            normalized_row[field]
            for field in ("ID", "Label", "Treatment Group", "R Label", "Aliases", "Legacy Aliases")
        )
        if has_values:
            normalized_rows.append(normalized_row)
    return normalized_rows


def apply_location_row(target_config: dict, row: dict[str, object]) -> None:
    aliases = parse_list_field("" if pd.isna(row["Aliases"]) else str(row["Aliases"]))
    legacy_aliases = parse_list_field(
        "" if pd.isna(row["Legacy Aliases"]) else str(row["Legacy Aliases"])
    )
    update_catalog_entry(
        target_config,
        section_name="locations",
        entry_key=str(row["Key"]),
        entry_id="" if pd.isna(row["ID"]) else str(row["ID"]).strip(),
        label="" if pd.isna(row["Label"]) else str(row["Label"]).strip(),
        active=bool(row["Active"]),
        aliases=aliases,
        legacy_aliases=legacy_aliases,
        legacy_only=bool(row["Legacy Only"]),
    )
    if bool(row["Allow Blank"]):
        target_config["locations"][str(row["Key"])].pop("allow_blank_treatment", None)
    else:
        target_config["locations"][str(row["Key"])]["allow_blank_treatment"] = False


def apply_treatment_row(target_config: dict, row: dict[str, object]) -> None:
    aliases = parse_list_field("" if pd.isna(row["Aliases"]) else str(row["Aliases"]))
    legacy_aliases = parse_list_field(
        "" if pd.isna(row["Legacy Aliases"]) else str(row["Legacy Aliases"])
    )
    update_catalog_entry(
        target_config,
        section_name="treatments",
        entry_key=str(row["Key"]),
        entry_id="" if pd.isna(row["ID"]) else str(row["ID"]).strip(),
        label="" if pd.isna(row["Label"]) else str(row["Label"]).strip(),
        active=bool(row["Active"]),
        parent_location="" if pd.isna(row["Parent Location"]) else str(row["Parent Location"]).strip(),
        aliases=aliases,
        legacy_aliases=legacy_aliases,
        r_label="" if pd.isna(row["R Label"]) else str(row["R Label"]).strip(),
        treatment_group="" if pd.isna(row["Treatment Group"]) else str(row["Treatment Group"]).strip(),
        legacy_only=bool(row["Legacy Only"]),
    )


def render_location_catalog_editor(config: dict, config_path: Path) -> None:
    st.subheader("Locations")
    edited_frame = st.data_editor(
        pd.DataFrame(make_location_editor_rows(config)),
        width="stretch",
        hide_index=True,
        disabled=["Key"],
        column_config={
            "Key": st.column_config.TextColumn("Key"),
            "ID": st.column_config.TextColumn("ID"),
            "Label": st.column_config.TextColumn("Label"),
            "Aliases": st.column_config.TextColumn("Aliases"),
            "Legacy Aliases": st.column_config.TextColumn("Legacy Aliases"),
            "Allow Blank": st.column_config.CheckboxColumn("Allow No treatment"),
            "Active": st.column_config.CheckboxColumn("Active"),
            "Legacy Only": st.column_config.CheckboxColumn("Legacy Only"),
        },
        key="locations_catalog_editor",
    )

    if st.button("Save locations table changes", type="primary", key="save_locations_table"):
        proposed_rows = edited_frame.to_dict("records")
        candidate_config = deep_copy_catalog(config)
        for row in proposed_rows:
            apply_location_row(candidate_config, row)

        errors: list[str] = []
        for row in proposed_rows:
            row_errors = validate_catalog_entry(
                candidate_config,
                section_name="locations",
                entry_id="" if pd.isna(row["ID"]) else str(row["ID"]).strip(),
                label="" if pd.isna(row["Label"]) else str(row["Label"]).strip(),
                existing_key=str(row["Key"]),
                aliases=parse_list_field("" if pd.isna(row["Aliases"]) else str(row["Aliases"])),
                legacy_aliases=parse_list_field(
                    "" if pd.isna(row["Legacy Aliases"]) else str(row["Legacy Aliases"])
                ),
                legacy_only=bool(row["Legacy Only"]),
            )
            row_errors.extend(
                update_catalog_status_errors(
                    candidate_config,
                    section_name="locations",
                    entry_key=str(row["Key"]),
                    active=bool(row["Active"]),
                )
            )
            for error in row_errors:
                errors.append(f"{row['Key']}: {error}")

        if errors:
            for error in dict.fromkeys(errors):
                st.error(error)
            return

        for row in proposed_rows:
            apply_location_row(config, row)

        save_config(config_path, config)
        st.success("Locations table saved.")
        st.rerun()


def render_treatment_catalog_editor(config: dict, config_path: Path) -> None:
    st.subheader("Treatments")
    editable_rows, system_rows = make_treatment_editor_rows(config)
    edited_frame = st.data_editor(
        pd.DataFrame(editable_rows),
        width="stretch",
        hide_index=True,
        disabled=["Key"],
        column_config={
            "Key": st.column_config.TextColumn("Key"),
            "Parent Location": st.column_config.SelectboxColumn(
                "Parent Location",
                options=list(config["locations"].keys()),
            ),
            "ID": st.column_config.TextColumn("ID"),
            "Label": st.column_config.TextColumn("Label"),
            "Treatment Group": st.column_config.TextColumn("Treatment Group"),
            "R Label": st.column_config.TextColumn("R Label"),
            "Aliases": st.column_config.TextColumn("Aliases"),
            "Legacy Aliases": st.column_config.TextColumn("Legacy Aliases"),
            "Active": st.column_config.CheckboxColumn("Active"),
            "Legacy Only": st.column_config.CheckboxColumn("Legacy Only"),
        },
        key="treatments_catalog_editor",
    )

    if system_rows:
        st.caption("System row")
        st.dataframe(pd.DataFrame(system_rows), width="stretch", hide_index=True)

    if st.button("Save treatments table changes", type="primary", key="save_treatments_table"):
        proposed_rows = edited_frame.to_dict("records")
        candidate_config = deep_copy_catalog(config)
        for row in proposed_rows:
            apply_treatment_row(candidate_config, row)

        errors: list[str] = []
        for row in proposed_rows:
            row_errors = validate_catalog_entry(
                candidate_config,
                section_name="treatments",
                entry_id="" if pd.isna(row["ID"]) else str(row["ID"]).strip(),
                label="" if pd.isna(row["Label"]) else str(row["Label"]).strip(),
                existing_key=str(row["Key"]),
                parent_location="" if pd.isna(row["Parent Location"]) else str(row["Parent Location"]).strip(),
                aliases=parse_list_field("" if pd.isna(row["Aliases"]) else str(row["Aliases"])),
                legacy_aliases=parse_list_field(
                    "" if pd.isna(row["Legacy Aliases"]) else str(row["Legacy Aliases"])
                ),
                r_label="" if pd.isna(row["R Label"]) else str(row["R Label"]).strip(),
                treatment_group="" if pd.isna(row["Treatment Group"]) else str(row["Treatment Group"]).strip(),
                legacy_only=bool(row["Legacy Only"]),
            )
            row_errors.extend(
                update_catalog_status_errors(
                    candidate_config,
                    section_name="treatments",
                    entry_key=str(row["Key"]),
                    active=bool(row["Active"]),
                )
            )
            for error in row_errors:
                errors.append(f"{row['Key']}: {error}")

        if errors:
            for error in dict.fromkeys(errors):
                st.error(error)
            return

        for row in proposed_rows:
            apply_treatment_row(config, row)

        save_config(config_path, config)
        st.success("Treatments table saved.")
        st.rerun()


def render_catalog_editor(
    title: str,
    config: dict,
    config_path: Path,
    *,
    section_name: str,
) -> None:
    if section_name == "locations":
        render_location_catalog_editor(config, config_path)
        return
    render_treatment_catalog_editor(config, config_path)


def render_als_dictionary_export() -> None:
    r_dictionary_text = build_als_r_dictionaries_text(CONFIG)
    st.subheader("ALS Data Cleaning Tool Dictionaries")
    st.markdown(
        """
        Use this when labels, locations, or treatments change in the Label Editor.

        1. Copy the R text below.
        2. Paste it over the existing `location.dict` and `trt.dict` objects in the ALS Data Cleaning Tool.
        3. Save that R script before running the cleaning workflow so both tools stay compatible.
        """
    )
    st.text_area(
        "R dictionary text to paste into the ALS Data Cleaning Tool",
        value=r_dictionary_text,
        height=900,
    )
    st.download_button(
        "Download R dictionaries",
        data=r_dictionary_text,
        file_name="als_data_cleaning_tool_dicts.R",
        mime="text/x-r-source",
    )


def update_catalog_status_errors(
    config: dict,
    *,
    section_name: str,
    entry_key: str,
    active: bool,
) -> list[str]:
    if active:
        return []

    errors: list[str] = []
    if section_name == "treatments" and entry_key == "blank":
        errors.append("The `No treatment` entry must stay active.")
    if section_name == "locations":
        active_children = [
            child_key
            for child_key, child in config["treatments"].items()
            if child_key != "blank"
            and get_treatment_parent_location(child) == entry_key
            and is_catalog_entry_active(child)
        ]
        if active_children:
            errors.append(
                "Deactivate or reassign active child treatments first: "
                + ", ".join(active_children)
                + "."
            )
    if count_active_catalog_entries(config[section_name], exclude_key=entry_key) == 0:
        errors.append(f"At least one active {section_name} entry is required.")
    return errors


def render_admin_page(config: dict, config_path: Path) -> None:
    st.header("Label Editor")
    st.markdown(
        """
        Use this page to manage canonical locations and their child treatments.

        Active entries are available in the label builder. Inactive and legacy-only entries remain visible here for historical compatibility and R export generation.
        """
    )

    admin_password = get_admin_password()
    if not admin_password:
        st.error(
            "Label Editor access is disabled. Set `admin_password` or `AWQP_ADMIN_PASSWORD` in "
            "Streamlit secrets, or set "
            "`AWQP_ADMIN_PASSWORD` in the environment."
        )
        return

    legacy_conflicts = find_cross_section_conflicts(config)
    if legacy_conflicts:
        st.warning(
            "Legacy location/treatment overlaps already exist in the catalog. New entries "
            "are blocked from creating additional overlaps."
        )
        for conflict in legacy_conflicts:
            st.caption(conflict)

    if not st.session_state.get("admin_authenticated", False):
        st.info("This page is protected by a shared password.")
        st.caption(
            "AWQP users can find the shared password in the SharePoint text file at "
            f"`{PASSWORD_HELP_PATH}`."
        )
        with st.form("admin_login_form"):
            shared_password = st.text_input("Shared Label Editor password", type="password")
            unlock = st.form_submit_button("Unlock Label Editor", type="primary")

        if unlock:
            if hmac.compare_digest(shared_password, admin_password):
                st.session_state.admin_authenticated = True
                st.rerun()
            st.error("Incorrect password.")
        return

    auth_cols = st.columns([5, 1])
    auth_cols[0].success("Label Editor unlocked for this browser session.")
    if auth_cols[1].button("Log out"):
        st.session_state.admin_authenticated = False
        st.rerun()

    current_catalog_tab, catalog_manager_tab, als_export_tab = st.tabs(
        ["Label Editor", "New Entry", "ALS R Dicts"]
    )

    with current_catalog_tab:
        st.caption(
            "Edit the canonical catalog directly here. Treatments now belong to parent locations, and only active entries appear in the label builder."
        )
        render_catalog_editor("Locations", config, config_path, section_name="locations")
        st.divider()
        render_catalog_editor("Treatments", config, config_path, section_name="treatments")

    with catalog_manager_tab:
        st.caption(
            "Add a new location with its treatments, or add a treatment to an existing location."
        )
        add_location_tab, add_treatment_tab = st.tabs(
            ["Add Location + Treatments", "Add Treatment to Existing Location"]
        )

        with add_location_tab:
            st.caption(
                "Use this when creating a new site. If the site has treatments, add them here at the same time."
            )
            with st.form("add_location_form"):
                location_id = st.text_input("Location ID code")
                location_label = st.text_input("Location label")
                location_aliases = st.text_input("Aliases (comma-separated, optional)")
                location_legacy_aliases = st.text_input("Legacy aliases (comma-separated, optional)")
                site_has_no_treatments = st.checkbox(
                    "Site has no treatments",
                    value=True,
                    help="Leave this checked for sites that should use only `No treatment` in the Label Builder.",
                )
                allow_blank_treatment = False
                location_treatment_rows: list[dict[str, object]] = []
                if not site_has_no_treatments:
                    allow_blank_treatment = st.checkbox(
                        "Also allow `No treatment` for this site",
                        value=False,
                        help="Use this for sites that have explicit treatments but still sometimes need a `No treatment` option.",
                    )
                    st.caption(
                        "Add one or more treatments for this new location. Leave unused rows blank."
                    )
                    location_treatment_rows = st.data_editor(
                        pd.DataFrame(make_new_treatment_seed_rows()),
                        width="stretch",
                        hide_index=True,
                        num_rows="dynamic",
                        column_config={
                            "ID": st.column_config.TextColumn("Treatment ID"),
                            "Label": st.column_config.TextColumn("Treatment Label"),
                            "Treatment Group": st.column_config.TextColumn("Treatment Group"),
                            "R Label": st.column_config.TextColumn("R Label"),
                            "Aliases": st.column_config.TextColumn("Aliases"),
                            "Legacy Aliases": st.column_config.TextColumn("Legacy Aliases"),
                            "Legacy Only": st.column_config.CheckboxColumn("Legacy Only"),
                            "Active": st.column_config.CheckboxColumn("Active"),
                        },
                        key="new_location_treatments_editor",
                    ).to_dict("records")
                legacy_only = st.checkbox("Legacy only", value=False)
                active = st.checkbox("Active", value=not legacy_only)
                save_location = st.form_submit_button("Save location", type="primary")

            if save_location:
                aliases = parse_list_field(location_aliases)
                legacy_aliases = parse_list_field(location_legacy_aliases)
                treatment_rows = normalize_new_treatment_rows(location_treatment_rows)
                errors = validate_catalog_entry(
                    config,
                    section_name="locations",
                    entry_id=location_id,
                    label=location_label,
                    aliases=aliases,
                    legacy_aliases=legacy_aliases,
                    legacy_only=legacy_only,
                )
                if not site_has_no_treatments and not treatment_rows:
                    errors.append(
                        "Add at least one treatment for this site, or check `Site has no treatments`."
                    )
                if errors:
                    for error in dict.fromkeys(errors):
                        st.error(error)
                else:
                    candidate_config = deep_copy_catalog(config)
                    entry_key = append_catalog_entry(
                        candidate_config,
                        section_name="locations",
                        entry_id=location_id,
                        label=location_label,
                        aliases=aliases,
                        legacy_aliases=legacy_aliases,
                        legacy_only=legacy_only,
                        active=active,
                    )
                    if not site_has_no_treatments and not allow_blank_treatment:
                        candidate_config["locations"][entry_key]["allow_blank_treatment"] = False

                    treatment_errors: list[str] = []
                    for row in treatment_rows:
                        row_errors = validate_catalog_entry(
                            candidate_config,
                            section_name="treatments",
                            entry_id=row["ID"],
                            label=row["Label"],
                            parent_location=entry_key,
                            aliases=parse_list_field(row["Aliases"]),
                            legacy_aliases=parse_list_field(row["Legacy Aliases"]),
                            r_label=row["R Label"],
                            treatment_group=row["Treatment Group"],
                            legacy_only=bool(row["Legacy Only"]),
                        )
                        for error in row_errors:
                            treatment_errors.append(f"{row['ID'] or row['Label'] or 'New treatment'}: {error}")

                    if treatment_errors:
                        for error in dict.fromkeys(treatment_errors):
                            st.error(error)
                    else:
                        for row in treatment_rows:
                            append_catalog_entry(
                                candidate_config,
                                section_name="treatments",
                                entry_id=row["ID"],
                                label=row["Label"],
                                parent_location=entry_key,
                                aliases=parse_list_field(row["Aliases"]),
                                legacy_aliases=parse_list_field(row["Legacy Aliases"]),
                                r_label=row["R Label"],
                                treatment_group=row["Treatment Group"],
                                legacy_only=bool(row["Legacy Only"]),
                                active=bool(row["Active"]),
                            )

                        save_config(config_path, candidate_config)
                        treatment_count = len(treatment_rows)
                        if treatment_count:
                            st.success(
                                f"Location `{location_label.strip()}` added as `{entry_key}` with {treatment_count} treatment(s)."
                            )
                        else:
                            st.success(
                                f"Location `{location_label.strip()}` added as `{entry_key}`."
                            )
                    st.rerun()

        with add_treatment_tab:
            st.caption("Use this when adding a treatment to a location that already exists.")
            location_options = list(config["locations"].keys())
            with st.form("add_treatment_form"):
                parent_location = st.selectbox(
                    "Parent location",
                    options=location_options,
                    format_func=lambda key: f"{config['locations'][key]['label']} ({key})",
                )
                treatment_id = st.text_input("Treatment ID code")
                treatment_label = st.text_input("Treatment label")
                treatment_group = st.text_input("Treatment group (optional)")
                r_label = st.text_input("R label (optional)")
                treatment_aliases = st.text_input("Aliases (comma-separated, optional)")
                treatment_legacy_aliases = st.text_input("Legacy aliases (comma-separated, optional)")
                legacy_only = st.checkbox("Legacy only", value=False, key="new_treatment_legacy_only")
                active = st.checkbox("Active", value=not legacy_only, key="new_treatment_active")
                save_treatment = st.form_submit_button("Save treatment", type="primary")

            if save_treatment:
                aliases = parse_list_field(treatment_aliases)
                legacy_aliases = parse_list_field(treatment_legacy_aliases)
                errors = validate_catalog_entry(
                    config,
                    section_name="treatments",
                    entry_id=treatment_id,
                    label=treatment_label,
                    parent_location=parent_location,
                    aliases=aliases,
                    legacy_aliases=legacy_aliases,
                    r_label=r_label,
                    treatment_group=treatment_group,
                    legacy_only=legacy_only,
                )
                if errors:
                    for error in dict.fromkeys(errors):
                        st.error(error)
                else:
                    entry_key = append_catalog_entry(
                        config,
                        section_name="treatments",
                        entry_id=treatment_id,
                        label=treatment_label,
                        parent_location=parent_location,
                        aliases=aliases,
                        legacy_aliases=legacy_aliases,
                        r_label=r_label,
                        treatment_group=treatment_group,
                        legacy_only=legacy_only,
                        active=active,
                    )
                    save_config(config_path, config)
                    st.success(
                        f"Treatment `{treatment_label.strip()}` added as `{entry_key}` for `{config['locations'][parent_location]['label']}`."
                    )
                    st.rerun()

    with als_export_tab:
        render_als_dictionary_export()


def render_guide() -> None:
    st.header("Guide")
    label_tab, season_tab, admin_tab = st.tabs(
        ["Label Builder", "Season List Builder", "Label Editor"]
    )

    with label_tab:
        st.markdown(
            """
            **Basic workflow**
            1. Complete the sidebar session options.
            2. Choose a location first, then choose from only the treatments assigned to that location.
            3. Choose the analytes to generate.
            4. Check `Include field duplicate` when the sample group has duplicates.
            5. Add the sample group, review the outputs, and download either an Excel workbook or a CSV ZIP.

            **How row counts work**
            - Every selected treatment is combined with every selected sample method.
            - Every analyte is generated for each treatment/method combination.
            - If field duplicates are checked, the app generates the normal rows and matching duplicate rows.
            - Example: `2 treatments x 2 methods x 4 analytes = 16 rows`, or `32 rows` with duplicates.

            **Outputs**
            - `Labels`: printable label rows with the label text column.
            - `Event`: event-list rows for AWQP tracking.
            - `For ALS Lab COC`: same core rows, excluding in-house analytes such as TSS, pH, and EC.
            - Preview each output table in the app before downloading.

            **Comments and lab blanks**
            - Some analytes include a default comment, such as heavy metals.
            - `Custom comment` replaces that default comment for all rows in the sample group.
            - Lab blank rows are controlled by the sidebar session options.
            """
        )

    with season_tab:
        st.markdown(
            """
            **Season list builder**
            - Upload older CSV or Excel exports from this app.
            - The app recognizes `Labels`, `Event`, and `For ALS Lab COC` tables.
            - Matching rows are appended in upload order.
            - This page does not deduplicate anything automatically.
            - Download a fresh combined Excel workbook or CSV ZIP after reviewing the combined tables.
            """
        )

    with admin_tab:
        st.markdown(
            """
            **Label Editor**
            - Manage canonical locations and location-scoped treatments.
            - Set parent locations, treatment groups, aliases, and legacy-only flags.
            - Mark old entries inactive so they disappear from normal selection lists without deleting catalog history.
            - Export live R dictionaries for the ALS Data Cleaning Tool from the current catalog.
            - This page is protected by a shared password set in Streamlit secrets or the app environment.
            - AWQP users can find the shared password in `D:\OneDrive - Colostate\AWQP_Sharepoint\Water_Quality_Project\Research\Edge of Field Monitoring and Data\AWQP Label Maker Tool\Label Edit Password.txt`.
            - Regular users do not need this password.
            """
        )


def render_season_list_builder() -> None:
    st.header("Season List Builder")
    st.markdown(
        """
        Upload past AWQP exports and this page will append matching tables into a single combined season file.

        Use this for:
        - Season-long `Event` tracking sheets
        - Combined `For ALS Lab COC` lists
        - Rebuilding `Labels` exports when older batches need to be brought together

        Supported uploads:
        - CSV files exported from this app
        - Excel workbooks (`.xlsx` or `.xlsm`) containing `Labels`, `Event`, or `For ALS Lab COC` sheets

        Rows are appended in upload order. This page does not deduplicate anything automatically.
        """
    )

    uploaded_files = st.file_uploader(
        "Upload old exports",
        type=["csv", "xlsx", "xlsm"],
        accept_multiple_files=True,
        help="You can mix CSV files and Excel workbooks. Matching tables will be appended together.",
    )

    if not uploaded_files:
        st.info("Upload one or more exports to build a combined season workbook.")
        return

    combined_tables, table_sources, skipped_items = append_uploaded_tables(uploaded_files)

    if skipped_items:
        for item in skipped_items:
            st.warning(item)

    if not combined_tables:
        st.error("No recognizable AWQP tables were found in the uploaded files.")
        return

    st.subheader("Combined Outputs")
    for table_name, frame in combined_tables.items():
        source_count = len(table_sources[table_name])
        st.caption(
            f"{table_name}: {len(frame)} row(s) appended from {source_count} uploaded table(s)."
        )

    tabs = st.tabs(list(combined_tables.keys()))
    for tab, (name, frame) in zip(tabs, combined_tables.items()):
        with tab:
            st.dataframe(frame, width="stretch", hide_index=True)
            st.caption("Sources: " + ", ".join(table_sources[name]))

    if excel_export_available():
        st.download_button(
            "Download combined Excel workbook",
            data=workbook_bytes(combined_tables),
            file_name=dated_filename("awqp_season_lists", "xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.warning("Excel export is unavailable because `openpyxl` is not installed in this environment.")
    st.download_button(
        "Download combined ZIP of CSVs",
        data=zip_exports(combined_tables),
        file_name="awqp_season_lists_csv.zip",
        mime="application/zip",
    )


if "sample_plan" not in st.session_state:
    st.session_state.sample_plan = empty_plan()
if "page" not in st.session_state:
    st.session_state.page = "Label Builder"
if "page_redirect" in st.session_state:
    if st.session_state.page_redirect == "Admin":
        st.session_state.page = "Label Editor"
    else:
        st.session_state.page = st.session_state.page_redirect
    del st.session_state["page_redirect"]
if "admin_authenticated" not in st.session_state:
    st.session_state.admin_authenticated = False

ACTIVE_LOCATION_KEYS = get_active_catalog_keys(CONFIG["locations"])
ACTIVE_TREATMENT_KEYS = [
    key
    for key, value in CONFIG["treatments"].items()
    if key != "blank" and is_catalog_entry_active(value)
]


st.title("AWQP Label Maker")
st.caption(
    "Build labels, ALS chain-of-custody rows, and event-list rows from canonical AWQP naming rules."
)

with st.sidebar:
    st.markdown(
        f"""
        <a href="{AWQP_HOME_URL}" target="_blank">
          <img src="{AWQP_LOGO_URL}" alt="AWQP logo" style="width: 100%; height: auto; margin-bottom: 0.5rem;">
        </a>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "Created by A.J. Brown, Agricultural Data Scientist  \n"
        "Ansley.Brown@colostate.edu"
    )
    st.divider()
    page = st.radio(
        "Pages",
        options=["Label Builder", "Season List Builder", "Label Editor", "Guide"],
        key="page",
    )
    st.divider()
    collection_date = None
    include_lab_blank = False
    blank_context = None
    if page == "Label Builder":
        st.header("Session Options")
        collection_date = st.date_input("Collection date", value=None)
        lab_blank_choice = st.radio(
            "Include lab blank rows",
            options=["Yes", "No"],
            index=None,
            horizontal=True,
        )
        include_lab_blank = lab_blank_choice == "Yes"
        if include_lab_blank and ACTIVE_LOCATION_KEYS:
            blank_context = st.selectbox(
                "Lab blank location context",
                options=ACTIVE_LOCATION_KEYS,
                index=None,
                placeholder="Choose a location",
                format_func=lambda key: CONFIG["locations"][key]["label"],
                help="Used to build blank IDs like BK-NHC-01-1.",
            )
        elif include_lab_blank:
            st.error("No active locations are available. Use Label Editor to reactivate at least one location.")
        st.divider()
        st.write("Current batch")
        st.metric("Sample groups", len(st.session_state.sample_plan["groups"]))

if page == "Guide":
    render_guide()
elif page == "Season List Builder":
    render_season_list_builder()
elif page == "Label Editor":
    render_admin_page(CONFIG, CONFIG_PATH)
else:
    if not ACTIVE_LOCATION_KEYS:
        missing_sections: list[str] = []
        if not ACTIVE_LOCATION_KEYS:
            missing_sections.append("locations")
        st.error(
            "New sample groups cannot be added because there are no active "
            + " and ".join(missing_sections)
            + ". Use Label Editor to reactivate the catalog."
        )
    else:
        header_cols = st.columns([6, 1])
        header_cols[0].subheader("Add Sample Group")
        if header_cols[1].button("Guide"):
            st.session_state.page_redirect = "Guide"
            st.rerun()

        c1, c2, c3 = st.columns(3)
        with c1:
            location_key = st.selectbox(
                "Location",
                options=ACTIVE_LOCATION_KEYS,
                format_func=lambda key: CONFIG["locations"][key]["label"],
                key="builder_location_key",
            )
            location_treatment_keys = get_location_treatment_keys(CONFIG, location_key)
            default_treatment_keys = (
                ["blank"]
                if "blank" in location_treatment_keys
                else location_treatment_keys[:1]
            )
            current_treatment_selection = st.session_state.get("builder_treatment_keys", [])
            normalized_treatments = [
                key for key in current_treatment_selection if key in location_treatment_keys
            ]
            if not normalized_treatments:
                normalized_treatments = default_treatment_keys
            if normalized_treatments != current_treatment_selection:
                st.session_state.builder_treatment_keys = normalized_treatments
            treatment_keys = st.multiselect(
                "Treatment(s)",
                options=location_treatment_keys,
                format_func=lambda key: CONFIG["treatments"][key]["label"],
                help="Only treatments assigned to the selected location are shown here.",
                key="builder_treatment_keys",
            )
            event_type_key = st.selectbox(
                "Event type (i.e., Point/Inflow/Outflow)",
                options=list(CONFIG["event_types"].keys()),
                format_func=lambda key: CONFIG["event_types"][key]["label"],
                key="builder_event_type_key",
            )
        with c2:
            if "builder_method_keys" not in st.session_state:
                st.session_state.builder_method_keys = ["GB"]
            method_keys = st.multiselect(
                "Sample method(s)",
                options=list(CONFIG["sample_methods"].keys()),
                format_func=lambda key: CONFIG["sample_methods"][key]["label"],
                help="Select one or more methods. Multiple selections create rows for each method.",
                key="builder_method_keys",
            )
            event_number = st.selectbox(
                "Event number",
                options=CONFIG["event_numbers"],
                help="Non-storm events use 01-0X. Storm events use S1-SX.",
                key="builder_event_number",
            )
            st.caption(
                f"Irr/Str will be set to `{default_irr_str(event_number)}` from the event number."
            )
        with c3:
            include_duplicates = st.checkbox(
                "Include field duplicate",
                help="When checked, this sample group generates normal rows plus matching duplicate rows.",
                key="builder_include_duplicates",
            )
            if "builder_analyte_keys" not in st.session_state:
                st.session_state.builder_analyte_keys = CONFIG["default_analytes"]
            analyte_keys = st.multiselect(
                "Analytes",
                options=list(CONFIG["analytes"].keys()),
                format_func=lambda key: CONFIG["analytes"][key]["label"],
                key="builder_analyte_keys",
            )
            custom_comment = st.text_input(
                "Custom comment (optional)",
                help="If provided, this replaces the analyte's default comment for every generated row in this sample group.",
                key="builder_custom_comment",
            )

        submitted = st.button("Add group", type="primary", key="builder_add_group")

        if submitted:
            valid_treatment_keys = set(get_location_treatment_keys(CONFIG, location_key))
            if not treatment_keys:
                st.error("Choose at least one treatment before adding the sample group.")
            elif any(treatment_key not in valid_treatment_keys for treatment_key in treatment_keys):
                st.error("One or more selected treatments do not belong to the chosen location.")
            elif not method_keys:
                st.error("Choose at least one sample method before adding the sample group.")
            elif not analyte_keys:
                st.error("Choose at least one analyte before adding the sample group.")
            else:
                add_group_to_plan(
                    st.session_state.sample_plan,
                    config=CONFIG,
                    location_key=location_key,
                    treatment_keys=treatment_keys,
                    event_type_key=event_type_key,
                    method_keys=method_keys,
                    event_number=event_number,
                    irrigation_or_storm=default_irr_str(event_number),
                    include_duplicates=include_duplicates,
                    analyte_keys=analyte_keys,
                    custom_comment=custom_comment,
                )
                st.success("Sample group added.")

    groups = st.session_state.sample_plan["groups"]
    if groups:
        st.subheader("Sample Groups in Batch")
        for index, group in enumerate(groups):
            combination_count = len(group["treatment_keys"]) * len(group["method_keys"])
            duplicate_count = len(get_group_duplicate_keys(group, CONFIG))
            projected_row_count = combination_count * len(group["analyte_keys"]) * duplicate_count
            duplicate_label = "yes" if duplicate_count > 1 else "no"
            summary = (
                f"{CONFIG['locations'][group['location_key']]['label']} | "
                f"{combination_count} treatment/method combination(s) | "
                f"{len(group['analyte_keys'])} analytes | "
                f"{projected_row_count} generated sample row(s)"
                f" | field duplicate: {duplicate_label}"
            )
            cols = st.columns([6, 1])
            cols[0].write(summary)
            cols[0].caption(
                "Treatments: "
                + ", ".join(CONFIG["treatments"][key]["label"] for key in group["treatment_keys"])
                + " | Methods: "
                + ", ".join(CONFIG["sample_methods"][key]["label"] for key in group["method_keys"])
            )
            if cols[1].button("Remove", key=f"remove-{index}"):
                remove_group_from_plan(st.session_state.sample_plan, index)
                st.rerun()

        session_errors = []
        if collection_date is None:
            session_errors.append("Choose a collection date in Session Options.")
        if lab_blank_choice is None:
            session_errors.append("Choose whether to include lab blank rows in Session Options.")
        if include_lab_blank and blank_context is None:
            session_errors.append("Choose a lab blank location context in Session Options.")

        if session_errors:
            st.warning("Complete Session Options before generating outputs.")
            for error in session_errors:
                st.caption(error)
        else:
            tables = build_output_tables(
                st.session_state.sample_plan,
                CONFIG,
                collection_date=collection_date,
                include_lab_blank=include_lab_blank,
                blank_location_key=blank_context or ACTIVE_LOCATION_KEYS[0],
            )

            st.subheader("Outputs")
            tabs = st.tabs(list(tables.keys()))
            for tab, (name, frame) in zip(tabs, tables.items()):
                with tab:
                    st.dataframe(frame, width="stretch", hide_index=True)

            if excel_export_available():
                st.download_button(
                    "Download Excel workbook",
                    data=workbook_bytes(tables),
                    file_name=dated_filename("awqp_label_outputs", "xlsx", collection_date),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            else:
                st.warning("Excel export is unavailable because `openpyxl` is not installed in this environment.")
            st.download_button(
                "Download ZIP of CSVs",
                data=zip_exports(tables),
                file_name="awqp_label_outputs_csv.zip",
                mime="application/zip",
            )
    else:
        st.info("Add a sample group to start building outputs.")

    with st.expander("Notes and assumptions"):
        st.markdown(
            """
            - `Event` output matches the workbook's `Event` tab schema.
            - `For ALS Lab COC` excludes analytes marked `exclude_from_als`.
            - Label cells append preservative text on a second line when required.
            - Lab blank rows default to analytes `1`, `2`, and `10`, matching the example workbook.
            """
        )
