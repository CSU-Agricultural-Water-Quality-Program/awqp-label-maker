from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from utils.config_loader import load_config
from utils.label_builder import (
    add_group_to_plan,
    build_output_tables,
    empty_plan,
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


def render_guide() -> None:
    st.header("Guide")
    st.markdown(
        """
        Use this app to build AWQP sample IDs and exports from human-readable selections.

        **Basic workflow**
        1. Choose a location.
        2. Choose one or more treatments.
        3. Choose an event type, event number, and one or more sample methods.
        4. Choose the analytes to generate.
        5. Add the sample group, review the outputs, and download the CSVs.

        **How row counts work**
        - Every selected treatment is combined with every selected sample method.
        - Every analyte is then generated for each of those combinations.
        - Example: `2 treatments x 2 methods x 4 analytes = 16 rows`.

        **Outputs**
        - `Labels`: printable label rows with the label text column.
        - `Event`: event-list rows for AWQP tracking.
        - `For ALS Lab COC`: same core rows, excluding in-house analytes such as TSS, pH, and EC.
        - Download: one Excel workbook with one sheet per output table, or a ZIP bundle of CSV files.

        **Comments**
        - Some analytes include a default comment, such as heavy metals.
        - `Custom comment` lets you replace that default comment for all rows in the sample group.

        **Lab blank**
        - Enable `Include lab blank rows` in the sidebar when you need the blank included in the export set.

        **Season list builder**
        - Use the `Season List Builder` page to upload older CSV or Excel exports.
        - The app will recognize `Labels`, `Event`, and `For ALS Lab COC` tables, append matching rows, and let you download a fresh combined workbook or CSV ZIP.
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
            st.dataframe(frame, use_container_width=True, hide_index=True)
            st.caption("Sources: " + ", ".join(table_sources[name]))

    st.download_button(
        "Download combined Excel workbook",
        data=workbook_bytes(combined_tables),
        file_name="awqp_season_lists.xlsx",
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
        "View",
        options=["Label Builder", "Season List Builder", "Guide"],
        label_visibility="collapsed",
    )
    st.divider()
    collection_date = date.today()
    include_lab_blank = True
    blank_context = list(CONFIG["locations"].keys())[0]
    if page == "Label Builder":
        st.header("Session Options")
        collection_date = st.date_input("Collection date", value=date.today())
        include_lab_blank = st.checkbox("Include lab blank rows", value=True)
        blank_context = st.selectbox(
            "Lab blank location context",
            options=list(CONFIG["locations"].keys()),
            format_func=lambda key: CONFIG["locations"][key]["label"],
            help="Used to build blank IDs like BK-NHC-01-1.",
        )
        st.divider()
        st.write("Current batch")
        st.metric("Sample groups", len(st.session_state.sample_plan["groups"]))

if page == "Guide":
    render_guide()
elif page == "Season List Builder":
    render_season_list_builder()
else:
    st.subheader("Add Sample Group")

    with st.form("add_group_form", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            location_key = st.selectbox(
                "Location",
                options=list(CONFIG["locations"].keys()),
                format_func=lambda key: CONFIG["locations"][key]["label"],
            )
            treatment_keys = st.multiselect(
                "Treatment(s)",
                options=list(CONFIG["treatments"].keys()),
                default=["blank"],
                format_func=lambda key: CONFIG["treatments"][key]["label"],
                help="Select one or more treatments. Multiple selections create rows for each treatment.",
            )
            event_type_key = st.selectbox(
                "Event type",
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
            irrigation_or_storm = st.text_input(
                "Irr/Str value",
                value=default_irr_str(event_number),
                help="Defaults to the numeric part of the event number. Adjust if needed.",
            )
        with c3:
            duplicate_key = st.selectbox(
                "Duplicate",
                options=list(CONFIG["duplicates"].keys()),
                format_func=lambda key: CONFIG["duplicates"][key]["label"],
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
                irrigation_or_storm=irrigation_or_storm,
                duplicate_key=duplicate_key,
                analyte_keys=analyte_keys,
                custom_comment=custom_comment,
            )
            st.success("Sample group added.")

    groups = st.session_state.sample_plan["groups"]
    if groups:
        st.subheader("Sample Groups in Batch")
        for index, group in enumerate(groups):
            combination_count = len(group["treatment_keys"]) * len(group["method_keys"])
            projected_row_count = combination_count * len(group["analyte_keys"])
            summary = (
                f"{CONFIG['locations'][group['location_key']]['label']} | "
                f"{combination_count} treatment/method combination(s) | "
                f"{len(group['analyte_keys'])} analytes | "
                f"{projected_row_count} generated sample row(s)"
                f" | duplicate: {CONFIG['duplicates'][group['duplicate_key']]['label']}"
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

        tables = build_output_tables(
            st.session_state.sample_plan,
            CONFIG,
            collection_date=collection_date,
            include_lab_blank=include_lab_blank,
            blank_location_key=blank_context,
        )

        st.subheader("Outputs")
        tabs = st.tabs(list(tables.keys()))
        for tab, (name, frame) in zip(tabs, tables.items()):
            with tab:
                st.dataframe(frame, use_container_width=True, hide_index=True)

        st.download_button(
            "Download Excel workbook",
            data=workbook_bytes(tables),
            file_name="awqp_label_outputs.xlsx",
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
