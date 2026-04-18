# AWQP Label Generator

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://awqp-label-maker.streamlit.app)

**Live App**  
[https://awqp-label-maker.streamlit.app](https://awqp-label-maker.streamlit.app)

**Author**  
[A.J. Brown](https://sites.google.com/view/ansleyjbrown)  
Agricultural Data Scientist  
Ansley.Brown@colostate.edu

## Overview
This repository contains a Streamlit application for generating standardized water sample labels for the [CSU Agricultural Water Quality Program (AWQP)](https://agsci.colostate.edu/waterquality/). The tool is designed for internal staff and students to generate valid sample IDs and export-ready tables without relying on Excel copy-paste workflows or institutional memory.

The application allows users to input sampling metadata through a structured form and automatically generates formatted labels plus downloadable output files as either a multi-sheet Excel workbook or a ZIP bundle of CSVs.

The frontend uses human-readable names for locations, treatments, and sampling choices. The app then converts those selections into the correct backend sample ID codes automatically, so users do not need to memorize or interpret AWQP label syntax.

This tool is intended to:

- Eliminate common human-error points caused by manual copy-paste, find-and-replace, and hand-built IDs
- Save hours of repetitive setup work each field season
- Make sample ID generation accessible to students and staff who do not already know the AWQP label code system
- Standardize outputs for internal use, SharePoint event tracking, and ALS chain-of-custody preparation

---

## Key Features

- Structured form-based input for:
  - Location
  - One or more treatments
  - Event number
  - Event type (inflow, outflow, point)
  - One or more sample methods
  - One or more analytes
  - Duplicate status
  - Optional custom comment
- Automatic label generation following AWQP standards
- Single Excel workbook export with multiple sheets for all generated outputs
- ZIP export containing the same outputs as separate CSV files
- Controlled vocabularies to prevent invalid entries
- Optional fields handled cleanly without breaking label format
- Batch-oriented workflow so one user can build many sample groups in a single session
- Built-in guide view for new users
- Password-protected admin page for adding locations and treatments
- Human-readable frontend with hidden backend ID translation

---

## Label Format

Labels follow this structure:

Location - Treatment - Event # - Event Type - Method - Analyte - Duplicate

Rules:
- Fields may be omitted if not applicable
- Order is always preserved
- Example:

AV-CT2-01-OT-ISC-4  
AV-CT2-01-OT-ISC-4-D (duplicate)

Lab blank format:
BK-Location-Event#-Analyte

---

## Repository Structure

```
awqp-label-maker/
├── app.py
├── config/config.yaml
├── utils/config_loader.py
├── utils/label_builder.py
├── requirements.txt
└── README.md
```

---

## Configuration

All domain-specific definitions are stored in `config/config.yaml`, including:

- Canonical locations and IDs
- Canonical treatments and IDs
- Analyte codes and ALS requirements
- Event types
- Sample methods
- Duplicate flags

This design allows easy updates without modifying application code while keeping the user-facing interface readable and consistent.

The app also includes an `Admin` page for adding locations and treatments from the UI. That page is protected by a single shared password supplied through Streamlit secrets using either `admin_password` or `AWQP_ADMIN_PASSWORD`, or through the `AWQP_ADMIN_PASSWORD` environment variable.

---

## Getting Started

### 1. Clone the repository

```
git clone https://github.com/yourusername/awqp-label-generator.git
cd awqp-label-generator
```

### 2. Create a virtual environment

```
python -m venv venv
source venv/bin/activate  # macOS/Linux
venv\Scripts\activate   # Windows
```

Conda also works well:

```bash
conda create -n awqp-label-maker python=3.11
conda activate awqp-label-maker
```

### 3. Install dependencies

```
pip install -r requirements.txt
```

### 4. Run the app

```
streamlit run app.py
```

To enable the admin page locally, create `.streamlit/secrets.toml` with either of these forms:

```toml
admin_password = "your-shared-password"
```

```toml
AWQP_ADMIN_PASSWORD = "your-shared-password"
```

For Streamlit Community Cloud, add the same TOML content in your app's `Settings` -> `Secrets` panel.

---

## Usage

1. Open the app in your browser
2. Use the sidebar `Guide` view if you need instructions before building outputs
3. Add one or more sample groups to the batch
4. Review the generated `Labels`, `Event`, and `For ALS Lab COC` tables
5. Download either a single Excel workbook containing all output sheets or a ZIP bundle of CSV files

### Why This Matters

The existing label workflow is systematic, but it has historically required a small number of people who understand the AWQP ID conventions well enough to build labels correctly in Excel. This app removes that knowledge barrier by turning the code system into guided inputs and fixed rules. The result is faster preparation, fewer formatting mistakes, and a process that can be used reliably by a broader group of staff and students.

### How Row Generation Works

- Each selected treatment is combined with each selected sample method
- Each analyte is then generated for every treatment/method combination
- Example: `2 treatments x 2 methods x 4 analytes = 16 sample rows`

### Current Output Rules

- `Labels`: includes every generated row plus a printable `Label` column
- `Event`: includes every generated row and mirrors the workbook's event-list schema
- `For ALS Lab COC`: same schema as `Event`, but excludes in-house analytes such as `4`, `13`, and `14`
- `Lab blank`: optional toggle; defaults to analytes `1`, `2`, and `10`, which matches the provided example workbook
- `Custom comment`: if supplied, replaces the analyte's default comment for every generated row in that sample group

### Notes

- Dates export as `MM/DD/YYYY` strings rather than Excel serial numbers
- The app is driven by [config/config.yaml](/Users/ajbrown/Documents/GitHub/awqp-label-maker/config/config.yaml); update that file to change IDs, analytes, or defaults without touching the app logic

### Example Event Output

The `Event` export mirrors the tab used for season event tracking. A typical output looks like this:

| Sample ID | Irr/Str | Date | Analysis | Analyses Code | Perserved | Volume | Comment |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `NHC-ROAD-01-GB-1` | `1` | `04/15/2025` | `NO3+NO2/OP*/TDS` | `A,B,G` | `No` | `500` | |
| `NHC-ROAD-01-GB-2` | `1` | `04/15/2025` | `TKN/TP` | `C,D` | `Sulfuric` | `250` | |
| `NHC-ROAD-01-GB-4` | `1` | `04/15/2025` | `TSS` | `E` | `No` | `125` | |
| `NHC-ROAD-01-GB-10` | `1` | `04/15/2025` | `Heavy Metal` | `H` | `Nitric` | `125` | `Arsenic, Se, Fe` |

This is the same basic structure used by the workbook example, but generated automatically from guided inputs instead of manual spreadsheet editing.

---

## Design Philosophy

This tool prioritizes:

- **Data integrity**: No free-text fields for critical identifiers
- **Consistency**: All labels follow a deterministic format
- **Simplicity**: Minimal training required for field staff
- **Extensibility**: Schema-driven design enables easy updates

---

## Future Enhancements

- Schema management page for adding new locations, treatments, methods, analytes, and related code mappings from within the app
- Backend validation for schema edits so new entries are checked for duplicate names, duplicate IDs, incompatible combinations, and conflicts with existing canonical rules
- Stronger compatibility checks so user-entered schema additions cannot silently break label construction or output formats
- Multi-user logging and audit trail
- Barcode or QR code generation
- Integration with laboratory information systems (LIMS)
- Deployment to cloud (Streamlit Cloud or internal server)

---

## License

Internal use. Add license as appropriate.
