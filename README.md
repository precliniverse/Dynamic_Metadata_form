# 🧪 Dynamic Metadata Form

A **No-Code**, schema-driven metadata entry system designed for FAIR research data. This tool generates **DataCite 4.5** compliant metadata with zero business logic in the backend.

---

## 🚀 Overview

The **Dynamic Metadata Form** is a flexible web application that allows researchers to register datasets with rich, structured biological metadata. 

### Key Features
- **No-Code Architecture**: 100% of the UI, API integrations, and mapping logic is defined in a single `schema.json`.
- **DataCite 4.5 Compliant**: Automatically maps complex biological entities (genes, strains, molecules) to DataCite subjects and technical descriptions.
- **Dynamic UI**: Sections and fields appear or disappear based on user input.
- **Ontology Integrated**: Direct search in NCBI Taxonomy, OLS (ChEBI, EFO, DOID, UBERON), ROR, and ORCID.

---

## 🏗️ How it Works

The system consists of two parts:
1.  **`server.py` (The Engine)**: A generic Python server that serves as a proxy for APIs and handles template resolution. It contains no project-specific information.
2.  **`schema.json` (The Brain)**: Defines everything:
    -   **APIs**: How to search and map external data.
    -   **Sections**: The logical categories of the form.
    -   **Fields**: The input elements (text, select, api_search, etc.).
    -   **Conditions**: When to show/hide sections.
    -   **Output Mapping**: How $values are transformed into the final JSON payload.

---

## 🛠️ Customization Guide

### 1. Adding Categories (Sections)
To add a new category, add an entry to the `sections` array in `schema.json`:

```json
{
  "id": "new_category",
  "title": "My New Section",
  "icon": "⭐",
  "fields": [
    {
      "id": "my_field",
      "label": "My New Field",
      "type": "text"
    }
  ]
}
```

### 2. Conditional Forms
You can make sections or fields dynamic using the `condition` property.

#### Example: Section based on Checkbox
Show a section only if "GENE" is selected in a checkbox group:
```json
"condition": {
  "type": "checkbox_includes",
  "field_id": "intervention_types",
  "value": "GENE"
}
```

#### Example: Section based on Organism
The `organism_trigger` type uses triggers defined in the `organism_presets` vocabulary:
```json
"condition": {
  "type": "organism_trigger",
  "trigger": "strain_search"
}
```

---

## 🧬 Case Study: Adding an Organism with specific Ontology

To add a new organism that uses a specific ontology (e.g., NCBITaxon) for background metadata:

1.  **Register the API** in `apis`:
    ```json
    "ols_ncbitaxon": {
      "url": "https://www.ebi.ac.uk/ols4/api/select",
      "query_param": "q",
      "extra_params": { "ontology": "ncbitaxon", "rows": 10 },
      "result_path": "response.docs",
      "mapper": { "strategy": "obo_ontology", "scheme": "NCBITaxon" }
    }
    ```
2.  **Add it to the Section**:
    ```json
    {
      "id": "organism_search",
      "type": "api_search",
      "api": "ols_ncbitaxon",
      "label": "Search for Organism",
      "output": {
        "path": "subjects",
        "mode": "append",
        "tpl": {
          "subject": "$label",
          "subjectScheme": "NCBITaxon",
          "valueURI": "$id"
        }
      }
    }
    ```

---

## 📦 Local Setup

1.  **Install dependencies**:
    ```bash
    pip install fastapi httpx uvicorn
    ```
2.  **Run the server**:
    ```bash
    python server.py
    ```
3.  **Access the form**: Open `http://localhost:8000` in your browser.

