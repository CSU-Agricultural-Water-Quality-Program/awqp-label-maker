from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from utils.label_builder import ALS_COLUMNS, EVENT_COLUMNS, LABEL_COLUMNS


TABLE_SCHEMAS = {
    "Labels": LABEL_COLUMNS,
    "Event": EVENT_COLUMNS,
    "For ALS Lab COC": ALS_COLUMNS,
}


@dataclass
class LoadedTable:
    table_name: str
    source_name: str
    source_label: str
    frame: pd.DataFrame


def _normalize_column_name(name: object) -> str:
    return " ".join(str(name).strip().lower().split())


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.dropna(axis=0, how="all").dropna(axis=1, how="all").copy()
    unnamed_columns = [
        column
        for column in cleaned.columns
        if _normalize_column_name(column).startswith("unnamed:")
    ]
    if unnamed_columns:
        cleaned = cleaned.drop(columns=unnamed_columns)
    return cleaned


def classify_columns(columns: list[object]) -> str | None:
    normalized_columns = [_normalize_column_name(column) for column in columns]
    for table_name, schema in TABLE_SCHEMAS.items():
        if normalized_columns == [_normalize_column_name(column) for column in schema]:
            return table_name
    return None


def _coerce_to_schema(frame: pd.DataFrame, schema: list[str]) -> pd.DataFrame:
    normalized_to_original = {
        _normalize_column_name(column): column for column in frame.columns
    }
    ordered = {}
    for column in schema:
        ordered[column] = frame[normalized_to_original[_normalize_column_name(column)]]
    return pd.DataFrame(ordered)


def load_tables_from_upload(uploaded_file) -> tuple[list[LoadedTable], list[str]]:
    loaded_tables: list[LoadedTable] = []
    skipped_items: list[str] = []
    suffix = Path(uploaded_file.name).suffix.lower()

    if suffix == ".csv":
        frame = _prepare_frame(pd.read_csv(uploaded_file))
        table_name = classify_columns(frame.columns.tolist())
        if table_name is None:
            skipped_items.append(
                f"{uploaded_file.name}: columns do not match Labels, Event, or For ALS Lab COC."
            )
            return loaded_tables, skipped_items
        loaded_tables.append(
            LoadedTable(
                table_name=table_name,
                source_name=uploaded_file.name,
                source_label=uploaded_file.name,
                frame=_coerce_to_schema(frame, TABLE_SCHEMAS[table_name]),
            )
        )
        return loaded_tables, skipped_items

    if suffix not in {".xlsx", ".xlsm"}:
        skipped_items.append(
            f"{uploaded_file.name}: unsupported file type. Upload CSV, XLSX, or XLSM files."
        )
        return loaded_tables, skipped_items

    workbook = pd.ExcelFile(uploaded_file)
    for sheet_name in workbook.sheet_names:
        frame = _prepare_frame(pd.read_excel(workbook, sheet_name=sheet_name))
        if frame.empty:
            continue
        table_name = classify_columns(frame.columns.tolist())
        if table_name is None:
            skipped_items.append(
                f"{uploaded_file.name} [{sheet_name}]: columns do not match Labels, Event, or For ALS Lab COC."
            )
            continue
        loaded_tables.append(
            LoadedTable(
                table_name=table_name,
                source_name=uploaded_file.name,
                source_label=f"{uploaded_file.name} [{sheet_name}]",
                frame=_coerce_to_schema(frame, TABLE_SCHEMAS[table_name]),
            )
        )

    return loaded_tables, skipped_items


def append_uploaded_tables(uploaded_files) -> tuple[dict[str, pd.DataFrame], dict[str, list[str]], list[str]]:
    grouped_frames: dict[str, list[pd.DataFrame]] = {name: [] for name in TABLE_SCHEMAS}
    table_sources: dict[str, list[str]] = {name: [] for name in TABLE_SCHEMAS}
    skipped_items: list[str] = []

    for uploaded_file in uploaded_files:
        loaded_tables, skipped = load_tables_from_upload(uploaded_file)
        skipped_items.extend(skipped)
        for table in loaded_tables:
            grouped_frames[table.table_name].append(table.frame)
            table_sources[table.table_name].append(table.source_label)

    combined_tables: dict[str, pd.DataFrame] = {}
    for table_name, frames in grouped_frames.items():
        if not frames:
            continue
        combined_tables[table_name] = pd.concat(frames, ignore_index=True)

    return combined_tables, table_sources, skipped_items
