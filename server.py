#!/usr/bin/env python3
"""
Metadata Wizard — No-Code Backend
========================================
Moteur générique de mapping API → Frontend.
Toute la logique métier est dans schema.json.

Stratégies universelles :
  - flat_object    : champs directs hit["key"]
  - nested_object  : champs imbriqués hit["key"]["subkey"][0]
  - array_find     : recherche dans tableaux avec filtre
  - obo_ontology   : format OLS standard (label, obo_id, iri)
  - custom         : fonctions enregistrées (extension)
"""
import json
import re
import logging
from pathlib import Path
from typing import Any, Optional
from functools import lru_cache

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse


# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(message)s')
log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / 'schema.json'
SCHEMA_REMOTE_URL = 'https://raw.githubusercontent.com/precliniverse/Dynamic_Metadata_form/refs/heads/main/schema.json'

app = FastAPI(title="Wizard API", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ══════════════════════════════════════════════════════════════════════
# ROUTING - Frontend
# ══════════════════════════════════════════════════════════════════════
@app.get("/")
async def serve_index():
    """Sert le frontend (index.html)."""
    return FileResponse(Path(__file__).parent / 'index.html')


# ══════════════════════════════════════════════════════════════════════
# TEMPLATE RESOLVER — Moteur universel
# ══════════════════════════════════════════════════════════════════════
class TemplateResolver:
    """
    Résout les templates {{key}}, {{key.subkey}}, {{key.[0]}}, {{key || fallback}}
    """
    
    @staticmethod
    def resolve(template: str, data: dict) -> str:
        """Résout un template simple avec fallback."""
        if not template or not isinstance(template, str):
            return str(template) if template else ""
        
        # Pas de template → retour direct
        if '{{' not in template:
            return template
        
        # Extraire toutes les expressions {{...}}
        pattern = r'\{\{([^}]+)\}\}'
        
        def replacer(match):
            expr = match.group(1).strip()
            # Gestion fallback : {{a || b || c}}
            parts = [p.strip() for p in expr.split('||')]
            for part in parts:
                val = TemplateResolver._resolve_path(part, data)
                if val is not None and val != '':
                    return str(val)
            return ''
        
        return re.sub(pattern, replacer, template)
    
    @staticmethod
    def _resolve_path(path: str, data: dict) -> Any:
        """
        Résout un chemin : key, key.subkey, key.[0], key.[?filter=val].result
        """
        if not path:
            return None
        
        # Gérer les filtres tableau : key.[?field=value].result
        if '.[?' in path:
            return TemplateResolver._resolve_filtered_array(path, data)
        
        # Chemin simple : key.subkey.[0]
        parts = path.split('.')
        current = data
        
        for part in parts:
            if current is None:
                return None
            
            # Index tableau : [0], [1]
            if part.startswith('[') and part.endswith(']'):
                idx_str = part[1:-1]
                if not idx_str.isdigit():
                    return None
                idx = int(idx_str)
                if isinstance(current, list) and 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            else:
                # Champ dict
                if isinstance(current, dict):
                    current = current.get(part)
                else:
                    return None
        
        return current
    
    @staticmethod
    def _resolve_filtered_array(path: str, data: dict) -> Any:
        """
        Résout key.[?field=value].result
        Exemple : names.[?types=ror_display].value
        """
        # Extraire : base, condition, result
        match = re.match(r'([^.]+)\.\[\?([^=]+)=([^\]]+)\]\.(.+)', path)
        if not match:
            return None
        
        base, filter_field, filter_value, result_field = match.groups()
        
        # Récupérer le tableau
        arr = TemplateResolver._resolve_path(base, data)
        if not isinstance(arr, list):
            return None
        
        # Filtrer
        for item in arr:
            if not isinstance(item, dict):
                continue
            # Vérifier si le champ contient la valeur (peut être une liste ou string)
            field_val = item.get(filter_field)
            if field_val == filter_value:
                return item.get(result_field)
            if isinstance(field_val, list) and filter_value in field_val:
                return item.get(result_field)
        
        return None


# ══════════════════════════════════════════════════════════════════════
# MAPPING STRATEGIES — 4 stratégies universelles
# ══════════════════════════════════════════════════════════════════════
class MappingStrategy:
    """Stratégies de mapping universelles."""
    
    @staticmethod
    def flat_object(hit: dict, config: dict) -> dict:
        """
        Champs au premier niveau.
        Exemple : {{symbol}}, {{name}}, {{_id}}
        """
        return MappingStrategy._generic_map(hit, config)
    
    @staticmethod
    def nested_object(hit: dict, config: dict) -> dict:
        """
        Champs imbriqués avec notation point.
        Exemple : {{given-names}}, {{institution-name.[0]}}
        """
        return MappingStrategy._generic_map(hit, config)
    
    @staticmethod
    def array_find(hit: dict, config: dict) -> dict:
        """
        Recherche dans tableau avec filtre.
        Exemple : {{names.[?types=ror_display].value}}
        """
        return MappingStrategy._generic_map(hit, config)
    
    @staticmethod
    def obo_ontology(hit: dict, config: dict) -> dict:
        """
        Format OLS standard (pré-défini).
        Utilise : label, obo_id, iri, description
        """
        return {
            'label':    hit.get('label', '?'),
            'sublabel': hit.get('obo_id', ''),
            'id':       hit.get('iri', ''),
            'scheme':   config.get('scheme', 'OBO'),
            'description': hit.get('description', ''),
        }
    
    @staticmethod
    def custom(hit: dict, config: dict) -> dict:
        """
        Fonction custom enregistrée.
        Permet d'étendre le système si nécessaire.
        """
        func_name = config.get('function_name')
        if func_name and func_name in CUSTOM_MAPPERS:
            return CUSTOM_MAPPERS[func_name](hit, config)
        log.warning(f"Custom mapper '{func_name}' not found, using generic")
        return MappingStrategy._generic_map(hit, config)
    
    @staticmethod
    def _generic_map(hit: dict, config: dict) -> dict:
        """
        Mapper générique universel.
        Résout label, sublabel, id, scheme, xrefs depuis le config.
        """
        result = {
            'label':    TemplateResolver.resolve(config.get('label', ''), hit),
            'sublabel': TemplateResolver.resolve(config.get('sublabel', ''), hit),
            'id':       TemplateResolver.resolve(config.get('id', ''), hit),
            'scheme':   config.get('scheme', ''),
        }
        
        # Xrefs (pour MyGene, etc.)
        if 'xrefs' in config:
            result['xrefs'] = MappingStrategy._resolve_xrefs(hit, config['xrefs'])
        
        return result
    
    @staticmethod
    def _resolve_xrefs(hit: dict, xrefs_config: dict) -> dict:
        """
        Résout les identifiants croisés.
        Chaque xref a une condition optionnelle.
        """
        xrefs = {}
        for db, xref_tpl in xrefs_config.items():
            # Vérifier condition (si le champ existe)
            if 'condition' in xref_tpl:
                cond_val = TemplateResolver.resolve(xref_tpl['condition'], hit)
                if not cond_val or cond_val == '':
                    continue
            
            # Résoudre l'xref
            xref_id = TemplateResolver.resolve(xref_tpl.get('id', ''), hit)
            if xref_id and xref_id != '':
                xrefs[db] = {
                    'id':    xref_id,
                    'uri':   TemplateResolver.resolve(xref_tpl.get('uri', ''), hit),
                    'label': xref_tpl.get('label', db.upper()),
                }
        
        return xrefs


# ══════════════════════════════════════════════════════════════════════
# CUSTOM MAPPERS REGISTRY (extensible)
# ══════════════════════════════════════════════════════════════════════
CUSTOM_MAPPERS = {}

def register_custom_mapper(name: str):
    """Décorateur pour enregistrer un mapper custom."""
    def decorator(func):
        CUSTOM_MAPPERS[name] = func
        return func
    return decorator

# Exemple de mapper custom (peut être dans un plugin externe)
@register_custom_mapper("normalize_mgi")
def normalize_mgi_mapper(hit: dict, config: dict) -> dict:
    """Normalise les IDs MGI : MGI:88057 ou 88057 → MGI:88057"""
    mgi_raw = hit.get('MGI', '')
    if isinstance(mgi_raw, list):
        mgi_raw = mgi_raw[0] if mgi_raw else ''
    mgi_raw = str(mgi_raw)
    mgi_id = mgi_raw if mgi_raw.startswith('MGI:') else f'MGI:{mgi_raw}' if mgi_raw else ''
    
    return {
        'label':    hit.get('symbol', '?'),
        'sublabel': hit.get('name', ''),
        'id':       f'https://identifiers.org/mgi:{mgi_id}' if mgi_id else '',
        'scheme':   'MGI',
    }


# ══════════════════════════════════════════════════════════════════════
# SCHEMA LOADER
# ══════════════════════════════════════════════════════════════════════
@lru_cache(maxsize=1)
def load_schema() -> dict:
    """Charge schema.json (avec cache)."""
    try:
        with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Schema loading error: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════
@app.get("/api/schema")
async def get_schema():
    """Retourne le schéma complet."""
    return load_schema()


@app.get("/api/schema/check-update")
async def check_schema_update():
    """Vérifie si une nouvelle version est disponible sur GitHub."""
    schema = load_schema()
    local_version = schema.get('version', '0.0.0')
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(SCHEMA_REMOTE_URL)
            if r.status_code == 200:
                remote_schema = r.json()
                remote_version = remote_schema.get('version', '0.0.0')
                
                if remote_version != local_version:
                    return {
                        'up_to_date': False,
                        'local_version': local_version,
                        'remote_version': remote_version,
                        'changelog': remote_schema.get('meta', {}).get('changelog', ''),
                    }
                return {'up_to_date': True, 'local_version': local_version}
            
            return {'error': f'GitHub responded with {r.status_code}', 'local_version': local_version}
    
    except httpx.TimeoutException:
        return {'error': 'GitHub timeout', 'local_version': local_version}
    except Exception as e:
        return {'error': str(e), 'local_version': local_version}


@app.get("/api/search/{api_key}")
async def search_api(
    api_key: str,
    q: str = Query(..., min_length=1),
    species: Optional[str] = Query(None),
):
    """
    Endpoint de recherche universel.
    Dispatcher vers n'importe quelle API définie dans schema.json.
    """
    schema = load_schema()
    apis = schema.get('apis', {})
    
    if api_key not in apis:
        return JSONResponse(
            status_code=404,
            content={'error': f"API '{api_key}' not found in schema", 'results': []}
        )
    
    api_def = apis[api_key]
    
    try:
        # Construire l'URL avec params
        url = api_def['url']
        params = {api_def['query_param']: q}
        params.update(api_def.get('extra_params', {}))
        
        # Params depuis contexte (ex: species pour MyGene)
        if 'extra_params_from_context' in api_def and species:
            for param_name, ctx_key in api_def['extra_params_from_context'].items():
                if ctx_key == 'organism_taxon_id' or ctx_key == 'organism.taxon_id':
                    params[param_name] = species
        
        # Requête HTTP
        headers = api_def.get('headers', {})
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            method = api_def.get('method', 'GET').upper()
            if method == 'GET':
                response = await client.get(url, params=params, headers=headers)
            else:
                response = await client.post(url, json=params, headers=headers)
        
        if response.status_code != 200:
            log.warning(f"[{api_key}] HTTP {response.status_code}")
            return {'results': [], 'error': f'API returned {response.status_code}'}
        
        data = response.json()
        
        # Extraire les résultats (result_path)
        result_path = api_def.get('result_path', '')
        hits = data
        for key in result_path.split('.'):
            if key:
                hits = hits.get(key, []) if isinstance(hits, dict) else []
        
        if not isinstance(hits, list):
            hits = []
        
        # Limiter les résultats
        limit = api_def.get('result_limit', 10)
        hits = hits[:limit]
        
        # Mapper les résultats
        mapper_config = api_def.get('mapper', {})
        if not mapper_config:
            log.warning(f"[{api_key}] No mapper defined, returning raw hits")
            return {'results': hits, 'total': len(hits)}
        
        strategy = mapper_config.get('strategy', 'flat_object')
        mapper_func = getattr(MappingStrategy, strategy, MappingStrategy.flat_object)
        
        results = [mapper_func(hit, mapper_config) for hit in hits]
        
        return {'results': results, 'total': len(results)}
    
    except httpx.TimeoutException:
        log.error(f"[{api_key}] Timeout")
        return {'results': [], 'error': 'Request timeout'}
    except Exception as e:
        log.error(f"[{api_key}] Error: {e}")
        return {'results': [], 'error': str(e)}


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    print("=" * 70)
    print("Metadata Wizard — No-Code Backend")
    print("=" * 70)
    schema = load_schema()
    print(f"Schema version: {schema.get('version', '?')}")
    print(f"APIs defined: {len(schema.get('apis', {}))}")
    print(f"Custom mappers: {len(CUSTOM_MAPPERS)}")
    print(f"Listening on http://0.0.0.0:8000")
    print("=" * 70)
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
