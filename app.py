from __future__ import annotations

import hmac
import io
import os
import zipfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from utils.config_loader import (
    append_catalog_entry,
    find_cross_section_conflicts,
    is_catalog_entry_active,
    load_config,
    next_available_key,
    normalize_key_fragment,
    save_config,
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


CONFIG_PATH = Path(__file__).parent / "config" / "config.yaml"
CONFIG = load_config(CONFIG_PATH)
AWQP_HOME_URL = "https://agsci.colostate.edu/waterquality/"
AWQP_LOGO_URL = (
    "https://agsci.colostate.edu/waterquality/wp-content/uploads/sites/160/2024/05/"
    "AWQP_horizontalhighres.png"
)


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
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in tables.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
    buffer.seek(0)
    return buffer.getvalue()


def dated_filename(prefix: str, extension: str, filename_date: date | None = None) -> str:
    return f"{prefix}_{(filename_date or date.today()).strftime('%Y-%m-%d')}.{extension}"


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


def render_catalog_table(title: str, entries: dict[str, dict]) -> None:
    rows = [
        {
            "Key": key,
            "ID": value["id"],
            "Label": value["label"],
            "Active": is_catalog_entry_active(value),
        }
        for key, value in entries.items()
    ]
    st.subheader(title)
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_catalog_editor(
    title: str,
    config: dict,
    config_path: Path,
    *,
    section_name: str,
) -> None:
    st.subheader(title)

    editable_rows = []
    system_rows = []
    for key, value in config[section_name].items():
        row = {
            "Key": key,
            "ID": value["id"],
            "Label": value["label"],
            "Active": is_catalog_entry_active(value),
        }
        if section_name == "treatments" and key == "blank":
            system_rows.append(row)
        else:
            editable_rows.append(row)

    if not editable_rows:
        st.info(f"No editable {section_name} entries are currently available.")
        return

    edited_frame = st.data_editor(
        pd.DataFrame(editable_rows),
        width="stretch",
        hide_index=True,
        disabled=["Key"],
        column_config={
            "Key": st.column_config.TextColumn("Key"),
            "ID": st.column_config.TextColumn("ID"),
            "Label": st.column_config.TextColumn("Label"),
            "Active": st.column_config.CheckboxColumn("Active"),
        },
        key=f"{section_name}_catalog_editor",
    )

    if system_rows:
        st.caption("System row")
        st.dataframe(pd.DataFrame(system_rows), width="stretch", hide_index=True)

    if st.button(
        f"Save {title.lower()} table changes",
        type="primary",
        key=f"save_{section_name}_table",
    ):
        proposed_entries = edited_frame.to_dict("records")
        candidate_config = {
            "locations": {key: dict(value) for key, value in config["locations"].items()},
            "treatments": {key: dict(value) for key, value in config["treatments"].items()},
        }
        for row in proposed_entries:
            entry_id = "" if pd.isna(row["ID"]) else str(row["ID"]).strip()
            entry_label = "" if pd.isna(row["Label"]) else str(row["Label"]).strip()
            candidate_config[section_name][str(row["Key"])] = {
                "id": entry_id,
                "label": entry_label,
                **({} if bool(row["Active"]) else {"active": False}),
            }

        errors: list[str] = []

        for row in proposed_entries:
            entry_id = "" if pd.isna(row["ID"]) else str(row["ID"]).strip()
            entry_label = "" if pd.isna(row["Label"]) else str(row["Label"]).strip()
            row_errors = validate_catalog_entry(
                candidate_config,
                section_name=section_name,
                entry_id=entry_id,
                label=entry_label,
                existing_key=str(row["Key"]),
            )
            row_errors.extend(
                update_catalog_status_errors(
                    candidate_config,
                    section_name=section_name,
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

        for row in proposed_entries:
            entry_id = "" if pd.isna(row["ID"]) else str(row["ID"]).strip()
            entry_label = "" if pd.isna(row["Label"]) else str(row["Label"]).strip()
            update_catalog_entry(
                config,
                section_name=section_name,
                entry_key=str(row["Key"]),
                entry_id=entry_id,
                label=entry_label,
                active=bool(row["Active"]),
            )

        save_config(config_path, config)
        st.success(f"{title} table saved.")
        st.rerun()


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
    if count_active_catalog_entries(config[section_name], exclude_key=entry_key) == 0:
        errors.append(f"At least one active {section_name} entry is required.")
    return errors


def render_admin_page(config: dict, config_path: Path) -> None:
    st.header("Label Editor")
    st.markdown(
        """
        Use this page to add, correct, and retire canonical locations and treatments.

        Add a location by itself, or save a location and its first treatment together.
        Inactive entries are hidden from normal users but remain in the catalog for editor review.
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

    catalog_manager_tab, current_catalog_tab = st.tabs(["Catalog Manager", "Catalog Tables"])

    with catalog_manager_tab:
        st.subheader("Add Location and Optional Treatment")
        st.caption(
            "Use one save to add a new location by itself, or add a location and its first "
            "treatment together. You can also attach a new treatment to an existing location context."
        )

        active_location_keys = get_active_catalog_keys(config["locations"])
        with st.form("catalog_manager_add_form"):
            location_context = st.selectbox(
                "Location context",
                options=["__new__"] + active_location_keys,
                format_func=lambda key: (
                    "Add new location"
                    if key == "__new__"
                    else config["locations"][key]["label"]
                ),
                help="Pick an existing location when you only need to add a treatment for that site.",
            )

            if location_context == "__new__":
                location_id = st.text_input(
                    "Location ID code (example: FF)",
                    placeholder="FF",
                    help=(
                        "Short code used in sample IDs, such as `FF` for Fruita Fertilizer. "
                        "Letters, numbers, and underscores only."
                    ),
                )
                location_label = st.text_input(
                    "Location label (example: Fruita Fertilizer)",
                    placeholder="Fruita Fertilizer",
                    help="Full location name shown to users in the app.",
                )
                if location_id.strip():
                    suggested_key = next_available_key(
                        config["locations"],
                        normalize_key_fragment(location_id),
                    )
                    st.caption(f"New location key preview: `{suggested_key}`")
            else:
                location_id = ""
                location_label = ""
                st.caption(
                    f"Selected location: `{config['locations'][location_context]['label']}`. "
                    "Add a treatment below if this site needs one."
                )

            treatment_id = st.text_input(
                "Treatment ID code (optional, example: F1)",
                placeholder="F1",
                help=(
                    "Short treatment code used in sample IDs, such as `F1` for Fruita F1. "
                    "Leave blank if the location does not need a new treatment."
                ),
            )
            treatment_label = st.text_input(
                "Treatment label (optional, example: Fruita F1)",
                placeholder="Fruita F1",
                help="Full treatment name shown to users in the app.",
            )

            if treatment_label.strip():
                suggested_key = next_available_key(
                    config["treatments"],
                    normalize_key_fragment(treatment_label),
                )
                st.caption(f"New treatment key preview: `{suggested_key}`")

            save_catalog_additions = st.form_submit_button(
                "Save catalog changes",
                type="primary",
            )

        if save_catalog_additions:
            adding_location = location_context == "__new__"
            adding_treatment = bool(treatment_id.strip() or treatment_label.strip())
            errors: list[str] = []

            if not adding_location and not adding_treatment:
                errors.append("Choose `Add new location` or enter a treatment before saving.")
            if adding_location:
                errors.extend(
                    validate_catalog_entry(
                        config,
                        section_name="locations",
                        entry_id=location_id,
                        label=location_label,
                    )
                )
            if adding_treatment:
                errors.extend(
                    validate_catalog_entry(
                        config,
                        section_name="treatments",
                        entry_id=treatment_id,
                        label=treatment_label,
                    )
                )

            if errors:
                for error in dict.fromkeys(errors):
                    st.error(error)
            else:
                messages: list[str] = []
                location_context_label = (
                    location_label.strip()
                    if adding_location
                    else config["locations"][location_context]["label"]
                )
                if adding_location:
                    entry_key = append_catalog_entry(
                        config,
                        section_name="locations",
                        entry_id=location_id,
                        label=location_label,
                    )
                    messages.append(
                        f"Location `{location_label.strip()}` added as `{entry_key}`."
                    )
                if adding_treatment:
                    entry_key = append_catalog_entry(
                        config,
                        section_name="treatments",
                        entry_id=treatment_id,
                        label=treatment_label,
                    )
                    messages.append(
                        f"Treatment `{treatment_label.strip()}` added as `{entry_key}` "
                        f"for `{location_context_label}`."
                    )

                save_config(config_path, config)
                st.success(" ".join(messages))
                st.rerun()

    with current_catalog_tab:
        st.caption(
            "Edit existing locations and treatments directly in these tables, then save the section you changed."
        )
        render_catalog_editor("Locations", config, config_path, section_name="locations")
        st.divider()
        render_catalog_editor("Treatments", config, config_path, section_name="treatments")


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
            2. Choose a location, treatment, event type, event number, and sample method.
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
            - Add canonical locations and treatments.
            - Fix typos in IDs or user-facing labels.
            - Mark old entries inactive so they disappear from normal selection lists without deleting catalog history.
            - Review and edit the full location and treatment tables in one place.
            - This page is protected by a shared password set in Streamlit secrets or the app environment.
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

    st.download_button(
        "Download combined Excel workbook",
        data=workbook_bytes(combined_tables),
        file_name=dated_filename("awqp_season_lists", "xlsx"),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
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
ACTIVE_TREATMENT_KEYS = get_active_catalog_keys(CONFIG["treatments"])
DEFAULT_TREATMENT_KEYS = (
    ["blank"] if "blank" in ACTIVE_TREATMENT_KEYS else ACTIVE_TREATMENT_KEYS[:1]
)


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
    if not ACTIVE_LOCATION_KEYS or not ACTIVE_TREATMENT_KEYS:
        missing_sections: list[str] = []
        if not ACTIVE_LOCATION_KEYS:
            missing_sections.append("locations")
        if not ACTIVE_TREATMENT_KEYS:
            missing_sections.append("treatments")
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

        with st.form("add_group_form", clear_on_submit=False):
            c1, c2, c3 = st.columns(3)
            with c1:
                location_key = st.selectbox(
                    "Location",
                    options=ACTIVE_LOCATION_KEYS,
                    format_func=lambda key: CONFIG["locations"][key]["label"],
                )
                treatment_keys = st.multiselect(
                    "Treatment(s)",
                    options=ACTIVE_TREATMENT_KEYS,
                    default=DEFAULT_TREATMENT_KEYS,
                    format_func=lambda key: CONFIG["treatments"][key]["label"],
                    help="Select one or more treatments. Multiple selections create rows for each treatment.",
                )
                event_type_key = st.selectbox(
                    "Event type (i.e., Point/Inflow/Outflow)",
                    options=list(CONFIG["event_types"].keys()),
                    format_func=lambda key: CONFIG["event_types"][key]["label"],
                )
            with c2:
                method_keys = st.multiselect(
                    "Sample method(s)",
                    options=list(CONFIG["sample_methods"].keys()),
                    default=["GB"],
                    format_func=lambda key: CONFIG["sample_methods"][key]["label"],
                    help="Select one or more methods. Multiple selections create rows for each method.",
                )
                event_number = st.selectbox(
                    "Event number",
                    options=CONFIG["event_numbers"],
                    help="Non-storm events use 01-0X. Storm events use S1-SX.",
                )
                st.caption(
                    f"Irr/Str will be set to `{default_irr_str(event_number)}` from the event number."
                )
            with c3:
                include_duplicates = st.checkbox(
                    "Include field duplicate",
                    help="When checked, this sample group generates normal rows plus matching duplicate rows.",
                )
                analyte_keys = st.multiselect(
                    "Analytes",
                    options=list(CONFIG["analytes"].keys()),
                    default=CONFIG["default_analytes"],
                    format_func=lambda key: CONFIG["analytes"][key]["label"],
                )
                custom_comment = st.text_input(
                    "Custom comment (optional)",
                    help="If provided, this replaces the analyte's default comment for every generated row in this sample group.",
                )

            submitted = st.form_submit_button("Add group", type="primary")

        if submitted:
            if not treatment_keys:
                st.error("Choose at least one treatment before adding the sample group.")
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

            st.download_button(
                "Download Excel workbook",
                data=workbook_bytes(tables),
                file_name=dated_filename("awqp_label_outputs", "xlsx", collection_date),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
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
