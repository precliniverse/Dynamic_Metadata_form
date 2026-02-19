"""
Tests for Dynamic Metadata Form — server.py
============================================
Run with:  pytest tests/test_server.py -v

Covers:
  - TemplateResolver (unit)
  - MappingStrategy  (unit)
  - FastAPI endpoints (integration via TestClient + respx mocking)
"""

import json
import pytest
import respx
import httpx
from fastapi.testclient import TestClient

# ── Import the app and helpers ──────────────────────────────────────────────
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from server import app, TemplateResolver, MappingStrategy, load_schema


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """FastAPI test client (synchronous)."""
    return TestClient(app)


@pytest.fixture(scope="module")
def schema():
    """Loaded schema (cached)."""
    return load_schema()


# ════════════════════════════════════════════════════════════════════════════
# 1. TemplateResolver unit tests
# ════════════════════════════════════════════════════════════════════════════

class TestTemplateResolver:

    def test_simple_key(self):
        assert TemplateResolver.resolve("{{symbol}}", {"symbol": "Apoe"}) == "Apoe"

    def test_nested_key(self):
        data = {"given-names": "Marie", "family-names": "Curie"}
        assert TemplateResolver.resolve("{{given-names}} {{family-names}}", data) == "Marie Curie"

    def test_array_index(self):
        data = {"institution-name": ["Institut Pasteur", "CNRS"]}
        assert TemplateResolver.resolve("{{institution-name.[0]}}", data) == "Institut Pasteur"

    def test_array_index_out_of_bounds(self):
        data = {"institution-name": []}
        assert TemplateResolver.resolve("{{institution-name.[0]}}", data) == ""

    def test_fallback_or(self):
        data = {"b": "fallback_value"}
        assert TemplateResolver.resolve("{{a || b}}", data) == "fallback_value"

    def test_fallback_first_wins(self):
        data = {"a": "first", "b": "second"}
        assert TemplateResolver.resolve("{{a || b}}", data) == "first"

    def test_no_template(self):
        assert TemplateResolver.resolve("plain string", {}) == "plain string"

    def test_empty_template(self):
        assert TemplateResolver.resolve("", {}) == ""

    def test_missing_key_returns_empty(self):
        assert TemplateResolver.resolve("{{nonexistent}}", {}) == ""

    def test_filtered_array(self):
        data = {
            "names": [
                {"types": ["label"], "value": "Label Name"},
                {"types": ["ror_display"], "value": "Display Name"},
            ]
        }
        result = TemplateResolver.resolve("{{names.[?types=ror_display].value}}", data)
        assert result == "Display Name"

    def test_filtered_array_no_match(self):
        data = {"names": [{"types": ["label"], "value": "Label Name"}]}
        result = TemplateResolver.resolve("{{names.[?types=ror_display].value}}", data)
        assert result == ""

    def test_nested_dot_path(self):
        data = {"ensembl": {"gene": "ENSMUSG00000002985"}}
        result = TemplateResolver.resolve("{{ensembl.gene}}", data)
        assert result == "ENSMUSG00000002985"

    def test_none_data_value(self):
        data = {"key": None}
        assert TemplateResolver.resolve("{{key}}", data) == ""


# ════════════════════════════════════════════════════════════════════════════
# 2. MappingStrategy unit tests
# ════════════════════════════════════════════════════════════════════════════

class TestMappingStrategy:

    def test_obo_ontology_basic(self):
        hit = {
            "label": "Mus musculus",
            "obo_id": "NCBITaxon:10090",
            "iri": "http://purl.obolibrary.org/obo/NCBITaxon_10090",
            "description": ["House mouse"],
        }
        config = {"strategy": "obo_ontology", "scheme": "NCBITaxon"}
        result = MappingStrategy.obo_ontology(hit, config)
        assert result["label"] == "Mus musculus"
        assert result["sublabel"] == "NCBITaxon:10090"
        assert result["id"] == "http://purl.obolibrary.org/obo/NCBITaxon_10090"
        assert result["scheme"] == "NCBITaxon"

    def test_obo_ontology_missing_fields(self):
        hit = {}
        config = {"strategy": "obo_ontology", "scheme": "EFO"}
        result = MappingStrategy.obo_ontology(hit, config)
        assert result["label"] == "?"
        assert result["sublabel"] == ""
        assert result["id"] == ""
        assert result["scheme"] == "EFO"

    def test_flat_object(self):
        hit = {"symbol": "Apoe", "name": "apolipoprotein E", "_id": "11287"}
        config = {
            "strategy": "flat_object",
            "label": "{{symbol}}",
            "sublabel": "{{name}}",
            "id": "https://identifiers.org/ncbigene:{{_id}}",
            "scheme": "GeneID",
        }
        result = MappingStrategy.flat_object(hit, config)
        assert result["label"] == "Apoe"
        assert result["sublabel"] == "apolipoprotein E"
        assert result["id"] == "https://identifiers.org/ncbigene:11287"
        assert result["scheme"] == "GeneID"

    def test_nested_object(self):
        hit = {
            "given-names": "Marie",
            "family-names": "Curie",
            "institution-name": ["Institut Pasteur"],
            "orcid-id": "0000-0001-2345-6789",
        }
        config = {
            "strategy": "nested_object",
            "label": "{{given-names}} {{family-names}}",
            "sublabel": "{{institution-name.[0]}}",
            "id": "https://orcid.org/{{orcid-id}}",
            "scheme": "ORCID",
        }
        result = MappingStrategy.nested_object(hit, config)
        assert result["label"] == "Marie Curie"
        assert result["sublabel"] == "Institut Pasteur"
        assert result["id"] == "https://orcid.org/0000-0001-2345-6789"

    def test_resolve_xrefs_with_condition(self):
        hit = {"_id": "11287", "MGI": "88057", "RGD": None}
        xrefs_config = {
            "ncbi_gene": {"id": "{{_id}}", "uri": "https://identifiers.org/ncbigene:{{_id}}", "label": "NCBI Gene"},
            "mgi": {"condition": "{{MGI}}", "id": "{{MGI}}", "uri": "https://identifiers.org/mgi:{{MGI}}", "label": "MGI"},
            "rgd": {"condition": "{{RGD}}", "id": "{{RGD}}", "uri": "https://identifiers.org/rgd:{{RGD}}", "label": "RGD"},
        }
        result = MappingStrategy._resolve_xrefs(hit, xrefs_config)
        assert "ncbi_gene" in result
        assert "mgi" in result
        assert "rgd" not in result  # RGD is None, condition fails
        assert result["mgi"]["id"] == "88057"

    def test_array_find_strategy(self):
        hit = {
            "names": [
                {"types": ["label"], "value": "ROR Label"},
                {"types": ["ror_display"], "value": "ROR Display"},
            ],
            "addresses": [{"city": "Paris"}],
            "country": {"country_name": "France"},
            "id": "https://ror.org/abc123",
        }
        config = {
            "strategy": "array_find",
            "label": "{{names.[?types=ror_display].value || names.[?types=label].value}}",
            "sublabel": "{{addresses.[0].city}}, {{country.country_name}}",
            "id": "{{id}}",
            "scheme": "ROR",
        }
        result = MappingStrategy.array_find(hit, config)
        assert result["label"] == "ROR Display"
        assert result["sublabel"] == "Paris, France"
        assert result["id"] == "https://ror.org/abc123"


# ════════════════════════════════════════════════════════════════════════════
# 3. FastAPI endpoint tests
# ════════════════════════════════════════════════════════════════════════════

class TestSchemaEndpoints:

    def test_get_schema_returns_200(self, client):
        r = client.get("/api/schema")
        assert r.status_code == 200
        data = r.json()
        assert "version" in data
        assert "apis" in data
        assert "sections" in data
        assert "vocabularies" in data

    def test_schema_has_required_apis(self, client):
        r = client.get("/api/schema")
        apis = r.json()["apis"]
        for expected in ["orcid", "ror", "ols_ncbitaxon", "ols_efo", "ols_chebi", "ols_doid", "ols_uberon", "ols_obi", "mygene"]:
            assert expected in apis, f"Missing API: {expected}"

    def test_schema_has_spdx_vocabulary(self, client):
        r = client.get("/api/schema")
        vocabs = r.json()["vocabularies"]
        assert "spdx_licenses" in vocabs
        licenses = vocabs["spdx_licenses"]["items"]
        assert any(l["value"] == "CC-BY-4.0" for l in licenses)
        assert any(l["value"] == "CC0-1.0" for l in licenses)

    def test_schema_has_new_sections(self, client):
        r = client.get("/api/schema")
        section_ids = [s["id"] for s in r.json()["sections"]]
        for expected in ["license", "related_identifiers", "funding", "assay", "cohort"]:
            assert expected in section_ids, f"Missing section: {expected}"

    def test_unknown_api_returns_404(self, client):
        r = client.get("/api/search/nonexistent_api?q=test")
        assert r.status_code == 404
        assert "error" in r.json()

    def test_search_missing_q_returns_422(self, client):
        r = client.get("/api/search/ols_ncbitaxon")
        assert r.status_code == 422  # FastAPI validation error

    def test_serve_index(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]


class TestSearchEndpointMocked:
    """Tests for /api/search/{api_key} with mocked external HTTP calls."""

    @respx.mock
    def test_ols_ncbitaxon_search(self, client):
        """Mock OLS4 response and verify normalized output shape."""
        mock_response = {
            "response": {
                "docs": [
                    {
                        "label": "Mus musculus",
                        "obo_id": "NCBITaxon:10090",
                        "iri": "http://purl.obolibrary.org/obo/NCBITaxon_10090",
                        "description": ["House mouse"],
                    }
                ]
            }
        }
        respx.get("https://www.ebi.ac.uk/ols4/api/select").mock(
            return_value=httpx.Response(200, json=mock_response)
        )
        r = client.get("/api/search/ols_ncbitaxon?q=mouse")
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["label"] == "Mus musculus"
        assert result["scheme"] == "NCBITaxon"
        assert "NCBITaxon_10090" in result["id"]

    @respx.mock
    def test_orcid_search(self, client):
        """Mock ORCID API and verify nested_object mapping."""
        mock_response = {
            "expanded-result": [
                {
                    "given-names": "Marie",
                    "family-names": "Curie",
                    "institution-name": ["Institut Pasteur"],
                    "orcid-id": "0000-0001-2345-6789",
                }
            ]
        }
        respx.get("https://pub.orcid.org/v3.0/expanded-search").mock(
            return_value=httpx.Response(200, json=mock_response)
        )
        r = client.get("/api/search/orcid?q=Marie+Curie")
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["label"] == "Marie Curie"
        assert result["id"] == "https://orcid.org/0000-0001-2345-6789"
        assert result["scheme"] == "ORCID"

    @respx.mock
    def test_external_api_timeout_returns_empty(self, client):
        """Verify graceful handling of timeout."""
        respx.get("https://www.ebi.ac.uk/ols4/api/select").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        r = client.get("/api/search/ols_chebi?q=tamoxifen")
        assert r.status_code == 200
        data = r.json()
        assert data["results"] == []
        assert "error" in data

    @respx.mock
    def test_mygene_with_species_param(self, client):
        """Verify species context param is forwarded to MyGene."""
        mock_response = {
            "hits": [
                {
                    "_id": "11287",
                    "symbol": "Apoe",
                    "name": "apolipoprotein E",
                    "taxid": 10090,
                    "MGI": "88057",
                    "ensembl": {"gene": "ENSMUSG00000002985"},
                }
            ]
        }
        route = respx.get("https://mygene.info/v3/query").mock(
            return_value=httpx.Response(200, json=mock_response)
        )
        r = client.get("/api/search/mygene?q=Apoe&species=10090")
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["label"] == "Apoe"
        assert "xrefs" in result
        assert "mgi" in result["xrefs"]
        assert "ensembl" in result["xrefs"]
        # Verify species was forwarded
        assert "species=10090" in str(route.calls[0].request.url)

    @respx.mock
    def test_impc_search(self, client):
        """Mock IMPC Solr response (pipeline core) and verify mapping."""
        mock_response = {
            "response": {
                "docs": [
                    {
                        "procedure_name": "Urinalysis",
                        "parameter_name": "Urine dilution",
                        "pipeline_name": "JAX Pipeline",
                        "parameter_id": 6854,
                        "parameter_stable_id": "JAX_URI_016_001"
                    }
                ]
            }
        }
        respx.get("https://www.ebi.ac.uk/mi/impc/solr/pipeline/select").mock(
            return_value=httpx.Response(200, json=mock_response)
        )
        # Search for "Urinalysis"
        r = client.get("/api/search/impc?q=Urinalysis")
        assert r.status_code == 200
        data = r.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        
        # Verify mapper strategy (pipeline core)
        assert result["label"] == "Urinalysis - Urine dilution"
        assert result["sublabel"] == "JAX Pipeline"
        assert result["id"] == "https://www.mousephenotype.org/impress/ParameterInfo?parameterId=6854"
        assert result["scheme"] == "IMPReSS"
