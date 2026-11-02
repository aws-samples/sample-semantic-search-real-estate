import datetime
import html
import json
import logging
import math
import os
import re
import time

import boto3
import folium
import pandas as pd
import streamlit as st
from botocore.client import Config
from langchain_core.prompts import PromptTemplate
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from streamlit_folium import folium_static

# =============================================================================
# CONFIGURATION
# =============================================================================

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)
DEBUG = os.getenv("SEMANTIC_SEARCH_DEBUG", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
COGNITO_REGION = os.getenv("COGNITO_REGION", AWS_REGION)
OPENSEARCH_REGION = AWS_REGION
INDEX_NAME = os.getenv("INDEX_NAME", "real-estate-properties")
PLACE_INDEX_NAME = os.getenv("PLACE_INDEX_NAME", "real-estate-geocoding")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID")
COGNITO_APP_CLIENT_ID = os.getenv("COGNITO_APP_CLIENT_ID")
COGNITO_IDENTITY_POOL_ID = os.getenv("COGNITO_IDENTITY_POOL_ID")
OPENSEARCH_ENDPOINT = os.getenv("OPENSEARCH_ENDPOINT")

SYNTHETIC_DATA_NOTICE = (
    "All bundled property records are synthetic demonstration data and are not "
    "for property identification or valuation decisions."
)
MAX_QUERY_CHARS = 500

if not all(
    [
        COGNITO_USER_POOL_ID,
        COGNITO_APP_CLIENT_ID,
        COGNITO_IDENTITY_POOL_ID,
        OPENSEARCH_ENDPOINT,
        PLACE_INDEX_NAME,
    ]
):
    LOGGER.error("Required runtime configuration is incomplete")
    st.error("The application is not configured. Contact the sample operator.")
    st.stop()

# =============================================================================
# COGNITO AUTHENTICATION
# =============================================================================


def authenticate_with_cognito(username, password):
    """Authenticate a user with the sample's Cognito user pool."""
    try:
        client = boto3.client("cognito-idp", region_name=COGNITO_REGION)
        # If this moves to SRP, also drop user_password=True from the user pool
        # client in backend/infrastructure/real_estate_search_stack.py.
        response = client.initiate_auth(
            ClientId=COGNITO_APP_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
        result = response["AuthenticationResult"]
        return {
            "success": True,
            "access_token": result["AccessToken"],
            "id_token": result["IdToken"],
            "refresh_token": result.get("RefreshToken"),
        }
    except Exception:
        LOGGER.exception("Cognito authentication failed")
        return {"success": False}


def get_cognito_credentials(id_token):
    """Exchange an ID token for temporary Cognito identity credentials."""
    try:
        client = boto3.client("cognito-identity", region_name=COGNITO_REGION)
        provider = (
            f"cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
        )
        identity = client.get_id(
            IdentityPoolId=COGNITO_IDENTITY_POOL_ID,
            Logins={provider: id_token},
        )
        response = client.get_credentials_for_identity(
            IdentityId=identity["IdentityId"],
            Logins={provider: id_token},
        )
        credentials = response["Credentials"]
        return {
            "access_key": credentials["AccessKeyId"],
            "secret_key": credentials["SecretKey"],
            "token": credentials["SessionToken"],
            "region": COGNITO_REGION,
            "expiration": credentials["Expiration"],
        }
    except Exception:
        LOGGER.exception("Cognito credential exchange failed")
        st.error("Unable to start an authenticated session. Please sign in again.")
        return None


def show_synthetic_data_notice():
    """Keep the demonstration-data limitation visible on every app view."""
    st.warning(SYNTHETIC_DATA_NOTICE)


def show_login_form():
    """Display the Cognito login form without credential hints or defaults."""
    st.title("🏠 Real Estate Search")
    show_synthetic_data_notice()
    st.subheader("🔐 Login Required")
    with st.form("login_form"):
        username = st.text_input("Username", value="")
        password = st.text_input("Password", value="", type="password")
        submit_button = st.form_submit_button("🚀 Login")
        if submit_button:
            if not username or not password:
                st.error("Enter both username and password.")
                return
            with st.spinner("Authenticating..."):
                result = authenticate_with_cognito(username, password)
            if not result["success"]:
                st.error("Invalid username or password.")
                return
            st.session_state["authenticated"] = True
            st.session_state["username"] = username
            st.session_state["id_token"] = result["id_token"]
            st.session_state["access_token"] = result["access_token"]
            st.success("Login successful.")
            st.rerun()


def logout():
    """Clear the local authenticated session."""
    for key in ["authenticated", "username", "id_token", "access_token", "aws_credentials"]:
        st.session_state.pop(key, None)
    st.rerun()


# =============================================================================
# AWS SERVICE CLIENTS
# =============================================================================

def get_aws_credentials():
    """Get AWS credentials from Cognito session with caching"""
    if 'id_token' not in st.session_state:
        return None
    
    # Check if we have cached credentials that are still valid
    if 'aws_credentials' in st.session_state:
        creds = st.session_state['aws_credentials']
        import datetime
        if creds.get('expiration') and datetime.datetime.now(datetime.timezone.utc) < (creds['expiration'] - datetime.timedelta(minutes=5)):
            return creds
    
    # Get fresh credentials
    creds = get_cognito_credentials(st.session_state['id_token'])
    if creds:
        st.session_state['aws_credentials'] = creds
    
    return creds

def create_boto3_session():
    """Create a boto3 session using Cognito credentials"""
    creds = get_aws_credentials()
    if not creds:
        return None
    
    return boto3.Session(
        aws_access_key_id=creds['access_key'],
        aws_secret_access_key=creds['secret_key'],
        aws_session_token=creds['token'],
        region_name=creds['region']
    )

def get_bedrock_client():
    """Get Bedrock client using Cognito credentials"""
    session = create_boto3_session()
    if not session:
        return None
    
    bedrock_config = Config(connect_timeout=120, read_timeout=120, retries={'max_attempts': 0})
    return session.client('bedrock-runtime', config=bedrock_config)

def get_location_client():
    """Get Location Services client using Cognito credentials"""
    session = create_boto3_session()
    if not session:
        return None
    
    return session.client('location')

def generate_query_embedding(query_text):
    """Generate embedding for search query using Bedrock"""
    try:
        bedrock_client = get_bedrock_client()
        if not bedrock_client:
            return None
        
        # Titan Text Embeddings V2: 1024 dims, normalized. Must match the index
        # mapping and the ingestion script (setup_opensearch_index.py).
        request_body = {
            "inputText": query_text,
            "dimensions": 1024,
            "normalize": True
        }
        
        response = bedrock_client.invoke_model(
            modelId=EMBEDDING_MODEL_ID,
            body=json.dumps(request_body),
            contentType="application/json"
        )
        
        response_body = json.loads(response['body'].read())
        return response_body['embedding']
        
    except Exception as e:
        st.error(f"Error generating query embedding: operation failed")
        return None

def run_neural_search(geo_coded_lat, geo_coded_long, search_property_radius, query_text, max_results=20):
    """Execute neural search using embeddings"""
    opensearch_client = create_opensearch_client()
    if not opensearch_client:
        st.error("OpenSearch client not available")
        return None
    
    try:
        # Generate embedding for the query
        query_embedding = generate_query_embedding(query_text)
        if not query_embedding:
            st.error("Could not generate query embedding")
            return None
        
        # Neural search query using proper KNN syntax
        query_body = {
            "size": max_results,
            "query": {
                "bool": {
                    "must": [
                        {
                            "knn": {
                                "property_embedding": {
                                    "vector": query_embedding,
                                    "k": max_results * 3
                                }
                            }
                        }
                    ],
                    "filter": [
                        {
                            "geo_distance": {
                                "distance": search_property_radius,
                                "propertyCoordinates": {
                                    "lat": geo_coded_lat,
                                    "lon": geo_coded_long
                                }
                            }
                        }
                    ]
                }
            },
            "_source": [
                "propertyId", "propertyName", "propertyType", 
                "propertyAddress", "propertyFeatures", "propertyCoordinates",
                "propertyPrice", "propertyBedrooms", "propertyBathrooms"
            ]
        }
        
        if DEBUG:
            LOGGER.debug("Generated neural-search request")
        
        resp = opensearch_client.search(index=INDEX_NAME, body=query_body)
        return resp
        
    except Exception as e:
        st.error(f"Error executing neural search: operation failed")
        if DEBUG:
            LOGGER.exception("Search operation failed")
        return None

def run_hybrid_search(geo_coded_lat, geo_coded_long, search_property_radius, search_property_type, search_property_features, query_text, max_results=20):
    """Execute hybrid search combining traditional + neural"""
    opensearch_client = create_opensearch_client()
    if not opensearch_client:
        st.error("OpenSearch client not available")
        return None
    
    try:
        # Convert features array to string if needed
        if isinstance(search_property_features, list):
            search_property_features = " ".join(search_property_features)
            
        # Generate embedding for the query
        query_embedding = generate_query_embedding(query_text)
        if not query_embedding:
            st.warning("Could not generate embeddings, falling back to traditional search")
            return run_opensearch_geospatial_query(geo_coded_lat, geo_coded_long, search_property_radius, search_property_type, search_property_features)
        
        # Hybrid search query combining traditional and neural
        query_body = {
            "size": max_results,
            "query": {
                "bool": {
                    "should": [
                        # Traditional keyword search
                        {
                            "bool": {
                                "should": [
                                    _property_type_query(
                                        search_property_type,
                                        boost=1.5,
                                    ),
                                    {
                                        "match": {
                                            "propertyFeatures": {
                                                "query": search_property_features,
                                                "boost": 2.0
                                            }
                                        }
                                    },
                                    {
                                        "multi_match": {
                                            "query": query_text,
                                            "fields": ["propertyName^2", "propertyAddress", "propertyFeatures"],
                                            "boost": 1.0
                                        }
                                    }
                                ]
                            }
                        },
                        # Neural semantic search
                        {
                            "knn": {
                                "property_embedding": {
                                    "vector": query_embedding,
                                    "k": max_results * 3,
                                    "boost": 2.5
                                }
                            }
                        }
                    ],
                    "filter": [
                        {
                            "geo_distance": {
                                "distance": search_property_radius,
                                "propertyCoordinates": {
                                    "lat": geo_coded_lat,
                                    "lon": geo_coded_long
                                }
                            }
                        }
                    ],
                    "minimum_should_match": 1
                }
            },
            "_source": [
                "propertyId", "propertyName", "propertyType", 
                "propertyAddress", "propertyFeatures", "propertyCoordinates",
                "propertyPrice", "propertyBedrooms", "propertyBathrooms"
            ]
        }
        
        if DEBUG:
            LOGGER.debug("Generated hybrid-search request")
        
        resp = opensearch_client.search(index=INDEX_NAME, body=query_body)
        return resp
        
    except Exception as e:
        st.error(f"Error executing hybrid search: operation failed")
        if DEBUG:
            LOGGER.exception("Search operation failed")
        return None

def create_opensearch_client():
    """Create OpenSearch client with Cognito authentication"""
    if not st.session_state.get('authenticated', False):
        return None
    
    creds = get_aws_credentials()
    if not creds:
        return None
    
    # Create AWS4Auth for OpenSearch
    awsauth = AWS4Auth(
        creds['access_key'],
        creds['secret_key'],
        OPENSEARCH_REGION,
        'es',
        session_token=creds['token']
    )
    
    # Extract hostname from endpoint URL
    endpoint_host = OPENSEARCH_ENDPOINT.replace('https://', '').replace('http://', '')
    
    # Create OpenSearch client
    return OpenSearch(
        hosts=[{'host': endpoint_host, 'port': 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
        max_retries=3,
        retry_on_timeout=True
    )

# =============================================================================
# BUSINESS LOGIC
# =============================================================================

# Bedrock model configuration
# Amazon Nova 2 Lite via cross-region inference (CRIS) profile. The Converse API
# is model-agnostic, so this also works with Claude/Titan without body changes.
MODEL_ID = 'us.amazon.nova-2-lite-v1:0'
# Titan Text Embeddings V2 (1024-dim, normalized) — must match the index mapping
# created by setup_opensearch_index.py.
EMBEDDING_MODEL_ID = 'amazon.titan-embed-text-v2:0'

# User prompt template for entity extraction
USER_PROMPT_TEMPLATE = """You are a real estate assistant. Your users ask questions about finding properties near a specific location.

For the prompt below:
<prompt>
{question}
</prompt>

Extract the following information:
- Property type (Single Family Residence, Townhouse, Multi Family, Condos)
- Address (whatever is available, assume USA if country not mentioned)
- Radius as a string with unit suffix (default "6mi" if not specified, e.g. "10mi", "25km")
- Property features (default to 'at least 1 bedroom' if not specified)

Return ONLY a single JSON object with keys: search_property_type, search_property_address, search_property_radius, search_property_features. Do not include any explanation, markdown code fences, or text before or after the JSON.
"""


def _extract_json(text):
    """Pull the first JSON object out of an LLM response that may include
    surrounding prose or markdown code fences."""
    if not text:
        return "{}"
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        return brace.group(0)
    return text.strip()


ALLOWED_PROPERTY_TYPES = ("Single Family Residence", "Townhouse", "Multi Family", "Condos")
DEFAULT_RADIUS = "6mi"
MIN_RADIUS = 0.1
MAX_RADIUS = {"mi": 100.0, "km": 160.0}
MAX_FEATURES = 10
MAX_FEATURE_CHARS = 100
_RADIUS_PATTERN = re.compile(
    r"(\d{1,6}(?:\.\d{1,6})?)\s*(mi|miles?|km|kilometers?)?", re.IGNORECASE
)
_PROPERTY_TYPES_BY_LOWER = {name.lower(): name for name in ALLOWED_PROPERTY_TYPES}


def _normalize_radius(value):
    """Return a safe geo_distance string such as "10mi" or "25km".

    OpenSearch geo_distance needs a string with units; without a unit it defaults
    to meters. LLMs may return a bare number, so bare numbers mean miles. Model
    output is untrusted, so non-finite, non-positive, boolean, wrong-type, or
    unparseable values fall back to the documented default. Finite values are
    clamped to a sample-sized range.
    """
    unit = "mi"
    if isinstance(value, bool):
        return DEFAULT_RADIUS
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str) and len(value) <= 32:
        match = _RADIUS_PATTERN.fullmatch(value.strip())
        if not match:
            return DEFAULT_RADIUS
        number = float(match.group(1))
        if (match.group(2) or "mi").lower().startswith("k"):
            unit = "km"
    else:
        return DEFAULT_RADIUS
    try:
        number = float(number)
    except (OverflowError, ValueError):
        return DEFAULT_RADIUS
    if not math.isfinite(number) or number <= 0:
        return DEFAULT_RADIUS
    number = min(max(number, MIN_RADIUS), MAX_RADIUS[unit])
    return f"{number:g}{unit}"


def _canonical_property_type(item):
    """Return the canonical property type for a string, or None."""
    if isinstance(item, str) and len(item) <= 64:
        return _PROPERTY_TYPES_BY_LOWER.get(" ".join(item.split()).lower())
    return None


def _validate_property_type(value):
    """Return an allowed type (or list of types). Bad input means no restriction."""
    if isinstance(value, str):
        return _canonical_property_type(value) or list(ALLOWED_PROPERTY_TYPES)
    if isinstance(value, list):
        found = []
        for item in value[:20]:
            canonical = _canonical_property_type(item)
            if canonical and canonical not in found:
                found.append(canonical)
        return found or list(ALLOWED_PROPERTY_TYPES)
    return list(ALLOWED_PROPERTY_TYPES)


def _clean_feature(item):
    """Collapse whitespace and cap the length of one feature string."""
    if not isinstance(item, str):
        return ""
    return " ".join(item.split())[:MAX_FEATURE_CHARS].strip()


def _validate_features(value):
    """Return short feature text or a short list of it. Anything else becomes []."""
    if isinstance(value, str):
        return _clean_feature(value) or []
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value[: MAX_FEATURES * 5]:
        text = _clean_feature(item)
        if text:
            cleaned.append(text)
        if len(cleaned) == MAX_FEATURES:
            break
    return cleaned


def _property_type_query(search_property_type, boost=1.5):
    """Build a valid keyword query for one or more property types."""
    if isinstance(search_property_type, list):
        return {
            "terms": {
                "propertyType": search_property_type,
                "boost": boost,
            }
        }
    return {
        "match": {
            "propertyType": {
                "query": search_property_type,
                "boost": boost,
            }
        }
    }


SEARCH_METHOD_LABELS = {
    "traditional": "Traditional",
    "semantic": "AI Semantic",
    "hybrid": "Hybrid",
}


def _run_comparison_method(method, operation):
    """Run one comparison operation and classify its actual outcome."""
    start_time = time.time()
    try:
        result = operation()
    except Exception:
        LOGGER.exception("%s comparison search failed", SEARCH_METHOD_LABELS[method])
        result = None
    elapsed = time.time() - start_time
    if result is None:
        return None, elapsed, f"❌ {SEARCH_METHOD_LABELS[method]} search failed: operation failed"
    return result, elapsed, None


def _successful_searches(search_results):
    """Return only methods that produced an OpenSearch response."""
    return [
        method
        for method in SEARCH_METHOD_LABELS
        if search_results.get(method) is not None
    ]


def _comparison_completion(search_errors):
    """Build the final status without leaving the running state visible."""
    if not search_errors:
        return "success", "All search methods completed successfully."
    failed_methods = ", ".join(
        SEARCH_METHOD_LABELS[method] for method in SEARCH_METHOD_LABELS
        if method in search_errors
    )
    return (
        "warning",
        f"Comparison completed with failures: {failed_methods}. "
        "The summary uses successful methods only.",
    )


def _build_summary_results(search_results, successful_searches):
    """Keep method provenance while excluding failed comparison methods."""
    return {
        method: [
            hit["_source"]
            for hit in search_results[method].get("hits", {}).get("hits", [])[:3]
        ]
        for method in successful_searches
    }


def extract_entities(question):
    """Extract entities from user question using Bedrock"""
    bedrock_client = get_bedrock_client()
    if not bedrock_client:
        st.error("Unable to access Bedrock service")
        return "{}"
    
    prompt_template = PromptTemplate(template=USER_PROMPT_TEMPLATE, input_variables=["question"])
    prompt = prompt_template.format(question=question)

    try:
        response = bedrock_client.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 3000, "temperature": 0.7}
        )
        raw_text = response['output']['message']['content'][0]['text']
        return _extract_json(raw_text)
    except Exception as e:
        st.error(f"Error in entity extraction: operation failed")
        return "{}"

def invoke_aws_loc_service(address):
    """Geocode address using AWS Location Services"""
    location_client = get_location_client()
    if not location_client:
        st.error("Unable to access Location Services")
        return None

    try:
        response = location_client.search_place_index_for_text(
            IndexName=PLACE_INDEX_NAME,
            Text=address
        )

        if response['Results']:
            place = response['Results'][0]['Place']
            longitude, latitude = place['Geometry']['Point']
            
            return {
                'address': address,
                'longitude': longitude,
                'latitude': latitude,
                'label': place.get('Label', ''),
                'country': place.get('Country', '')
            }
        return None
    except Exception as e:
        st.error(f"Geocoding error: operation failed")
        return None

def run_opensearch_geospatial_query(geo_coded_lat, geo_coded_long, search_property_radius, search_property_type, search_property_features):
    """Execute hybrid geospatial search using OpenSearch"""
    opensearch_client = create_opensearch_client()
    if not opensearch_client:
        st.error("OpenSearch client not available")
        return None
    
    try:
        # Convert features array to string if needed
        if isinstance(search_property_features, list):
            search_property_features = " ".join(search_property_features)
            
        query_body = {
            "query": {
                "bool": {
                    "must": [
                        _property_type_query(
                            search_property_type,
                            boost=1.5,
                        )
                    ],
                    "should": [
                        {
                            "match": {
                                "propertyFeatures": {
                                    "query": search_property_features,
                                    "boost": 3.0
                                }
                            }
                        }
                    ],
                    "filter": [
                        {
                            "geo_distance": {
                                "distance": search_property_radius, 
                                "propertyCoordinates": {
                                    "lat": geo_coded_lat, 
                                    "lon": geo_coded_long
                                }
                            }
                        }
                    ]
                }
            },
            "_source": [
                "propertyId", "propertyName", "propertyType", 
                "propertyAddress", "propertyFeatures", "propertyCoordinates"
            ]
        }
        
        if DEBUG:
            LOGGER.debug("Generated geospatial search request")
        
        resp = opensearch_client.search(index=INDEX_NAME, body=query_body)
        return resp
        
    except Exception as e:
        st.error(f"Error executing OpenSearch query: operation failed")
        if DEBUG:
            LOGGER.exception("Search operation failed")
        return None

def run_fallback_search(geo_coded_lat, geo_coded_long, search_property_radius, search_property_features, query_text):
    """Execute a more lenient search when strict criteria fail"""
    opensearch_client = create_opensearch_client()
    if not opensearch_client:
        st.error("OpenSearch client not available")
        return None
    
    try:
        # Convert features array to string if needed
        if isinstance(search_property_features, list):
            search_property_features = " ".join(search_property_features)
            
        # More lenient query - search across all property types
        query_body = {
            "query": {
                "bool": {
                    "should": [
                        # Match any property type
                        {
                            "terms": {
                                "propertyType": ["Single Family Residence", "Condos", "Townhouse", "Multi Family"],
                                "boost": 1.0
                            }
                        },
                        # Feature matching
                        {
                            "match": {
                                "propertyFeatures": {
                                    "query": search_property_features,
                                    "boost": 2.0
                                }
                            }
                        },
                        # Text matching on the original query
                        {
                            "multi_match": {
                                "query": query_text,
                                "fields": ["propertyName^2", "propertyAddress", "propertyFeatures"],
                                "boost": 1.5
                            }
                        }
                    ],
                    "filter": [
                        {
                            "geo_distance": {
                                "distance": search_property_radius,
                                "propertyCoordinates": {
                                    "lat": geo_coded_lat,
                                    "lon": geo_coded_long
                                }
                            }
                        }
                    ],
                    "minimum_should_match": 1
                }
            },
            "sort": [
                "_score",  # Sort by relevance first
                {
                    "_geo_distance": {
                        "propertyCoordinates": {
                            "lat": geo_coded_lat,
                            "lon": geo_coded_long
                        },
                        "order": "asc",
                        "unit": "mi"
                    }
                }
            ],
            "_source": [
                "propertyId", "propertyName", "propertyType", 
                "propertyAddress", "propertyFeatures", "propertyCoordinates"
            ]
        }
        
        if DEBUG:
            LOGGER.debug("Generated fallback-search request")
        
        resp = opensearch_client.search(index=INDEX_NAME, body=query_body)
        return resp
        
    except Exception as e:
        st.error(f"Error executing fallback search: operation failed")
        if DEBUG:
            LOGGER.exception("Search operation failed")
        return None

def run_basic_geo_search(geo_coded_lat, geo_coded_long, search_property_radius):
    """Execute a very basic geographic search to test if any data exists"""
    opensearch_client = create_opensearch_client()
    if not opensearch_client:
        st.error("OpenSearch client not available")
        return None
    
    try:
        # Very basic query - just geographic filter
        query_body = {
            "query": {
                "bool": {
                    "filter": [
                        {
                            "geo_distance": {
                                "distance": search_property_radius,
                                "propertyCoordinates": {
                                    "lat": geo_coded_lat,
                                    "lon": geo_coded_long
                                }
                            }
                        }
                    ]
                }
            },
            "size": 10,
            "_source": [
                "propertyId", "propertyName", "propertyType", 
                "propertyAddress", "propertyFeatures", "propertyCoordinates"
            ]
        }
        
        if DEBUG:
            LOGGER.debug("Generated basic geospatial request")
        
        resp = opensearch_client.search(index=INDEX_NAME, body=query_body)
        return resp
        
    except Exception as e:
        st.error(f"Error executing basic geo search: operation failed")
        if DEBUG:
            LOGGER.exception("Search operation failed")
        return None

def _show_model_text(text):
    """Render model output as plain text, never as Markdown.

    Indexed records reach the prompt, so a record could steer the model into
    emitting a link or image. st.write would render that Markdown for every
    user who sees the summary; st.text displays it as inert text.
    """
    st.text(text if isinstance(text, str) else str(text))


def run_geospatial_rag(question, context):
    """Generate response using RAG with OpenSearch results"""
    bedrock_client = get_bedrock_client()
    if not bedrock_client:
        return "Unable to access Bedrock service"
    
    user_recommendation_template = """
Human: You are a Real estate realtor helping find properties. Answer based on the context: <context>{context}</context>

Rules:
- Stay in character as a real estate advisor
- If unsure, say "Sorry, I didn't understand that. Could you repeat the question?"
- For irrelevant questions, say "Sorry, I don't know."
- Answer in plain text. Do not use Markdown, links, or images.

Question: <question>{question}</question>
Assistant:
    """

    recommendation_template = PromptTemplate(template=user_recommendation_template, input_variables=["context", "question"])
    prompt = recommendation_template.format(question=question, context=context)
    
    try:
        response = bedrock_client.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 4096, "temperature": 0.7}
        )
        return response['output']['message']['content'][0]['text']
    except Exception as e:
        st.error(f"Error in RAG generation: operation failed")
        return "I apologize, but I'm having trouble generating a response right now."

def display_pretty_table(results):
    """Display search results in tabular format"""
    if not results or 'hits' not in results:
        st.write("No results to display")
        return
        
    table_data = []
    for hit in results['hits']['hits']:
        source = hit['_source']
        table_data.append([
            source.get('propertyId', 'N/A'),
            source.get('propertyName', 'N/A'),            
            source.get('propertyAddress', 'N/A'),
            source.get('propertyType', 'N/A'),
        ])

    if table_data:
        df = pd.DataFrame(table_data, columns=["ID", "Property Name", "Address", "Type"])
        st.header("Search Results")
        st.table(df)
    else:
        st.write("No properties found matching your criteria.")

_JS_TEMPLATE_UNSAFE = {ord("`"): "&#96;", ord("\\"): "&#92;", ord("$"): "&#36;"}


def _escape_map_text(value):
    """Escape record text for Folium.

    Folium puts tooltip text unescaped inside a JavaScript template literal, so
    backtick, backslash, and dollar sign become HTML character references after
    html.escape.
    """
    return html.escape(str(value)).translate(_JS_TEMPLATE_UNSAFE)


def display_map(search_location, data):
    """Display properties on interactive map"""
    if not data or 'hits' not in data:
        st.write("No data available for map display")
        return
        
    points = []
    for hit in data['hits']['hits']:
        source = hit['_source']
        coords = source.get('propertyCoordinates', '')
        
        if coords:
            try:
                # propertyCoordinates is a geo_point dict {"lat":..,"lon":..} in the
                # current index mapping, but may be a legacy "lat,lon" string. Handle both.
                if isinstance(coords, dict):
                    latitude = float(coords.get('lat'))
                    longitude = float(coords.get('lon'))
                else:
                    parts = coords.split(',')
                    latitude = float(parts[0].strip())
                    longitude = float(parts[1].strip())
                
                points.append({
                    "name": _escape_map_text(source.get('propertyId', 'Unknown')),
                    "tooltip": (
                        f"{_escape_map_text(source.get('propertyType', 'N/A'))} : "
                        f"{_escape_map_text(source.get('propertyName', 'N/A'))} : "
                        f"{_escape_map_text(source.get('propertyAddress', 'N/A'))}"
                    ),
                    "latitude": latitude,
                    "longitude": longitude
                })
            except (ValueError, IndexError):
                continue

    if not points:
        st.write("No properties found with valid coordinates.")
        return

    # Calculate center point
    center_lat = sum(point['latitude'] for point in points) / len(points)
    center_lon = sum(point['longitude'] for point in points) / len(points)

    # Create map
    m = folium.Map(location=[center_lat, center_lon], zoom_start=13)

    # Add markers
    for point in points:
        folium.Marker(
            location=[point['latitude'], point['longitude']],
            popup=point['name'],
            tooltip=point['tooltip']
        ).add_to(m)

    st.header(f"Properties near: {search_location}")
    folium_static(m)

def execute_enhanced_search(user_query, search_options):
    """Execute enhanced search with multiple modes"""
    
    st.markdown("---")
    st.markdown("### 🔍 Search Results")
    
    # Show search configuration
    with st.expander("🔧 Search Configuration Used"):
        st.json({
            "Query": user_query,
            "Search Mode": search_options['mode'],
            "Vector Search": search_options['vector_search'],
            "Geo Boost": search_options['geo_boost'],
            "Semantic Weight": search_options['semantic_weight'],
            "Max Results": search_options['max_results']
        })
    
    mode = search_options['mode']
    
    if mode == "📊 Compare All":
        execute_comparison_search(user_query, search_options)
    elif mode == "🔤 Traditional + Geo":
        execute_traditional_search(user_query, search_options)
    elif mode == "🧠 AI Semantic":
        execute_semantic_search(user_query, search_options)
    elif mode == "🔄 Hybrid Search":
        execute_hybrid_search(user_query, search_options)

def execute_comparison_search(user_query, search_options):
    """Execute all search types and show real comparison"""
    
    st.subheader("📊 Search Method Comparison")
    comparison_status = st.empty()
    comparison_status.info("Running all search algorithms to compare actual results...")
    
    # Initialize results storage
    search_results = {}
    search_times = {}
    search_errors = set()
    
    # Step 1: Extract entities and geocode (common for all searches)
    with st.spinner("🧠 Analyzing query and geocoding..."):
        try:
            extracted_entities_JSON = json.loads(extract_entities(user_query))
            search_property_type = _validate_property_type(extracted_entities_JSON["search_property_type"])
            search_property_address = extracted_entities_JSON["search_property_address"]
            search_property_radius = _normalize_radius(extracted_entities_JSON["search_property_radius"])
            search_property_features = _validate_features(extracted_entities_JSON["search_property_features"])

            if not search_property_address:
                comparison_status.error("Comparison stopped during query analysis.")
                st.error("Please mention a valid geographical location for comparison.")
                return

            aws_location_service_result = invoke_aws_loc_service(search_property_address)
            if not aws_location_service_result:
                comparison_status.error("Comparison stopped during geocoding.")
                st.error("Could not geocode the provided address for comparison.")
                return

            geo_coded_lat = aws_location_service_result['latitude']
            geo_coded_long = aws_location_service_result['longitude']
            
        except Exception:
            LOGGER.exception("Comparison query analysis failed")
            comparison_status.error("Comparison stopped during query analysis.")
            st.error("Error in query analysis: operation failed")
            return

    # Step 2: Run all search methods
    st.markdown("### 🔄 Search Method Status")

    operations = (
        (
            "traditional",
            "Running traditional search...",
            lambda: run_opensearch_geospatial_query(
                geo_coded_lat,
                geo_coded_long,
                search_property_radius,
                search_property_type,
                search_property_features,
            ),
        ),
        (
            "semantic",
            "Running AI semantic search...",
            lambda: run_neural_search(
                geo_coded_lat,
                geo_coded_long,
                search_property_radius,
                user_query,
                search_options["max_results"],
            ),
        ),
        (
            "hybrid",
            "Running hybrid search...",
            lambda: run_hybrid_search(
                geo_coded_lat,
                geo_coded_long,
                search_property_radius,
                search_property_type,
                search_property_features,
                user_query,
                search_options["max_results"],
            ),
        ),
    )
    for method, spinner_text, operation in operations:
        with st.spinner(spinner_text):
            result, elapsed, error_message = _run_comparison_method(
                method, operation
            )
        search_times[method] = elapsed
        if error_message:
            search_errors.add(method)
            st.error(error_message)
        else:
            search_results[method] = result
            st.success(f"✅ {SEARCH_METHOD_LABELS[method]} search completed")

    completion_type, completion_message = _comparison_completion(search_errors)
    getattr(comparison_status, completion_type)(completion_message)
    
    # Step 3: Display results in tabs
    st.markdown("---")
    st.markdown("### 📊 Comparison Results")
    
    # Create tabs for different search methods
    tab1, tab2, tab3, tab4 = st.tabs([
        "🔤 Traditional", 
        "🧠 AI Semantic", 
        "🔄 Hybrid",
        "📈 Performance Analysis"
    ])
    
    with tab1:
        st.markdown("**Traditional Keyword + Geospatial Search**")
        if 'traditional' in search_results and search_results['traditional']:
            results = search_results['traditional']
            hits = results.get('hits', {}).get('hits', [])
            st.info(f"Found {len(hits)} properties using traditional keyword matching")
            
            if hits:
                display_pretty_table(results)
                
                # Show search methodology
                with st.expander("🔍 Traditional Search Details"):
                    st.markdown("**Search Method:**")
                    st.write("• BM25 keyword matching on property type and features")
                    st.write("• Geographic distance filtering")
                    st.write("• Boost scoring based on field importance")
                    st.write(f"• Query time: {search_times.get('traditional', 0):.3f} seconds")
            else:
                st.warning("No results found with traditional search")
        elif 'traditional' in search_errors:
            st.error("Traditional search could not be completed.")
    
    with tab2:
        st.markdown("**AI Semantic Search**")
        if 'semantic' in search_results and search_results['semantic']:
            results = search_results['semantic']
            hits = results.get('hits', {}).get('hits', [])
            st.info(f"Found {len(hits)} properties using AI semantic understanding")
            
            if hits:
                display_pretty_table(results)
                
                # Show semantic insights
                with st.expander("🧠 AI Semantic Analysis"):
                    st.markdown("**Search Method:**")
                    st.write("• Neural embeddings using Amazon Bedrock Titan")
                    st.write("• Cosine similarity matching (1024 dimensions)")
                    st.write("• Semantic understanding of concepts and intent")
                    st.write(f"• Query time: {search_times.get('semantic', 0):.3f} seconds")
                    
                    # Show top similarity scores
                    st.markdown("**Top Semantic Matches:**")
                    for i, hit in enumerate(hits[:3]):
                        score = hit.get('_score', 0)
                        property_name = hit['_source'].get('propertyName', 'Unknown')
                        st.write(f"{i+1}. {property_name} (Similarity: {score:.3f})")
            else:
                st.warning("No results found with semantic search")
        elif 'semantic' in search_errors:
            st.error("AI semantic search could not be completed.")
    
    with tab3:
        st.markdown("**Hybrid Search (Traditional + AI)**")
        if 'hybrid' in search_results and search_results['hybrid']:
            results = search_results['hybrid']
            hits = results.get('hits', {}).get('hits', [])
            st.info(f"Found {len(hits)} properties using hybrid approach")
            
            if hits:
                display_pretty_table(results)
                
                # Show hybrid insights
                with st.expander("🔄 Hybrid Search Analysis"):
                    st.markdown("**Search Method:**")
                    st.write("• Combined traditional BM25 + neural embeddings")
                    st.write("• Weighted scoring (40% traditional + 60% AI)")
                    st.write("• Geographic filtering applied to both methods")
                    st.write(f"• Query time: {search_times.get('hybrid', 0):.3f} seconds")
                    
                    # Show scoring breakdown
                    st.markdown("**Combined Relevance Scoring:**")
                    for i, hit in enumerate(hits[:3]):
                        score = hit.get('_score', 0)
                        property_name = hit['_source'].get('propertyName', 'Unknown')
                        st.write(f"{i+1}. {property_name} (Combined Score: {score:.3f})")
            else:
                st.warning("No results found with hybrid search")
        elif 'hybrid' in search_errors:
            st.error("Hybrid search could not be completed.")
    
    with tab4:
        st.markdown("**Performance Analysis & Comparison**")
        
        # Performance metrics
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.markdown("**🔤 Traditional Search**")
            if 'traditional' in search_results and search_results['traditional']:
                trad_count = len(search_results['traditional'].get('hits', {}).get('hits', []))
                trad_time = search_times.get('traditional', 0)
                st.metric("Results Found", trad_count)
                st.metric("Query Time", f"{trad_time:.3f}s")
            else:
                st.metric("Results Found", "Error")
                st.metric("Query Time", "N/A")
        
        with col2:
            st.markdown("**🧠 AI Semantic**")
            if 'semantic' in search_results and search_results['semantic']:
                sem_count = len(search_results['semantic'].get('hits', {}).get('hits', []))
                sem_time = search_times.get('semantic', 0)
                st.metric("Results Found", sem_count)
                st.metric("Query Time", f"{sem_time:.3f}s")
                
                # Average similarity score
                hits = search_results['semantic'].get('hits', {}).get('hits', [])
                if hits:
                    avg_score = sum(hit.get('_score', 0) for hit in hits) / len(hits)
                    st.metric("Avg Similarity", f"{avg_score:.3f}")
            else:
                st.metric("Results Found", "Error")
                st.metric("Query Time", "N/A")
        
        with col3:
            st.markdown("**🔄 Hybrid Search**")
            if 'hybrid' in search_results and search_results['hybrid']:
                hyb_count = len(search_results['hybrid'].get('hits', {}).get('hits', []))
                hyb_time = search_times.get('hybrid', 0)
                st.metric("Results Found", hyb_count)
                st.metric("Query Time", f"{hyb_time:.3f}s")
                
                # Average combined score
                hits = search_results['hybrid'].get('hits', {}).get('hits', [])
                if hits:
                    avg_score = sum(hit.get('_score', 0) for hit in hits) / len(hits)
                    st.metric("Avg Score", f"{avg_score:.3f}")
            else:
                st.metric("Results Found", "Error")
                st.metric("Query Time", "N/A")
        
        # Comparison insights
        st.markdown("---")
        st.markdown("**🎯 Search Method Comparison:**")
        
        successful_searches = _successful_searches(search_results)
        
        if successful_searches:
            # Find best performing method
            result_counts = {}
            for method in successful_searches:
                result_counts[method] = len(search_results[method].get('hits', {}).get('hits', []))
            
            best_method = max(result_counts, key=result_counts.get)
            fastest_method = min(successful_searches, key=lambda x: search_times.get(x, float('inf')))
            
            st.write(f"• **Most Results**: {best_method.title()} ({result_counts[best_method]} properties)")
            st.write(f"• **Fastest**: {fastest_method.title()} ({search_times.get(fastest_method, 0):.3f}s)")
            
            # Unique results analysis
            if len(successful_searches) > 1:
                st.markdown("**Result Overlap Analysis:**")
                # This would require more complex analysis of property IDs across results
                st.write("• Each search method may find different relevant properties")
                st.write("• Hybrid search typically provides the best balance of relevance and coverage")
        else:
            st.warning("No successful searches to compare")
    
    # Step 4: Generate AI summary of comparison
    if successful_searches:
        with st.spinner("✨ Generating AI comparison summary..."):
            try:
                # Keep method provenance in the summary context and exclude failures.
                summary_results = _build_summary_results(
                    search_results, successful_searches
                )
                
                context = json.dumps(summary_results)
                comparison_query = f"Compare the successful search results for: {user_query}"
                rag_summary = run_geospatial_rag(comparison_query, context)
                
                st.markdown("---")
                if search_errors:
                    st.subheader("🤖 AI Partial Comparison Summary")
                    st.caption(
                        "Generated only from successful methods: "
                        + ", ".join(method.title() for method in successful_searches)
                    )
                else:
                    st.subheader("🤖 AI Comparison Summary")
                _show_model_text(rag_summary)
                
            except Exception as e:
                st.error(f"Error generating comparison summary: operation failed")

def execute_traditional_search(user_query, search_options):
    """Execute traditional keyword + geo search"""
    
    st.subheader("🔤 Traditional + Geospatial Search")
    st.info("Using keyword matching + geographic distance filtering")
    
    try:
        # Step 1: Extract entities and geocode
        with st.spinner("🧠 Analyzing query and geocoding..."):
            extracted_entities_JSON = json.loads(extract_entities(user_query))
            search_property_type = _validate_property_type(extracted_entities_JSON["search_property_type"])
            search_property_address = extracted_entities_JSON["search_property_address"]
            search_property_radius = _normalize_radius(extracted_entities_JSON["search_property_radius"])
            search_property_features = _validate_features(extracted_entities_JSON["search_property_features"])

            if not search_property_address:
                st.error("Please mention a valid geographical location.")
                return

            aws_location_service_result = invoke_aws_loc_service(search_property_address)
            if not aws_location_service_result:
                st.error("Could not geocode the provided address.")
                return

            geo_coded_lat = aws_location_service_result['latitude']
            geo_coded_long = aws_location_service_result['longitude']
        
        # Display analysis results
        ner_data = {
            'Property Type': [search_property_type],
            'Address': [search_property_address],
            'Distance': [search_property_radius],
            'Features': [search_property_features]
        }
        df = pd.DataFrame(ner_data)
        st.subheader("🎯 Query Analysis")
        st.table(df)
        
        # Step 2: Execute traditional search
        with st.spinner(f"🔍 Searching {search_property_type}..."):
            results = run_opensearch_geospatial_query(
                geo_coded_lat, geo_coded_long, search_property_radius,
                search_property_type, search_property_features
            )
        
        if results and results.get('hits', {}).get('total', {}).get('value', 0) > 0:
            st.success(f"✅ Found {len(results['hits']['hits'])} {search_property_type} properties!")
            
            # Display results
            display_pretty_table(results)
            display_map(search_property_address, results)
            
            # Generate AI summary
            context = json.dumps([hit['_source'] for hit in results['hits']['hits']])
            rag_summary = run_geospatial_rag(user_query, context)
            
            st.subheader("🤖 AI Summary")
            _show_model_text(rag_summary)
        else:
            st.warning(f"No {search_property_type} properties found. Trying fallback search...")
            
            # Try fallback search
            fallback_results = run_fallback_search(
                geo_coded_lat, geo_coded_long, search_property_radius,
                search_property_features, user_query
            )
            
            if fallback_results and fallback_results.get('hits', {}).get('total', {}).get('value', 0) > 0:
                st.success(f"✅ Found {len(fallback_results['hits']['hits'])} properties with broader search!")
                
                # Display results
                display_pretty_table(fallback_results)
                display_map(search_property_address, fallback_results)
                
                # Generate AI summary
                context = json.dumps([hit['_source'] for hit in fallback_results['hits']['hits']])
                rag_summary = run_geospatial_rag(user_query, context)
                
                st.subheader("🤖 AI Summary")
                _show_model_text(rag_summary)
            else:
                st.error("No properties found matching your criteria.")
                
    except Exception as e:
        st.error(f"Traditional search error: operation failed")
        if DEBUG:
            LOGGER.exception("Search operation failed")

def execute_semantic_search(user_query, search_options):
    """Execute AI semantic search - ACTUAL IMPLEMENTATION"""
    
    st.subheader("🧠 AI Semantic Search Results")
    st.info("Using neural embeddings to understand semantic meaning")
    
    try:
        # Step 1: Named Entity Extraction
        with st.spinner("🧠 Analyzing query with AI..."):
            extracted_entities_JSON = json.loads(extract_entities(user_query))
            search_property_type = _validate_property_type(extracted_entities_JSON["search_property_type"])
            search_property_address = extracted_entities_JSON["search_property_address"]
            search_property_radius = _normalize_radius(extracted_entities_JSON["search_property_radius"])
            search_property_features = _validate_features(extracted_entities_JSON["search_property_features"])

        # Display NER results
        ner_data = {
            'Property Type': [search_property_type],
            'Address': [search_property_address],
            'Distance': [search_property_radius],
            'Features': [search_property_features]
        }
        df = pd.DataFrame(ner_data)
        st.subheader("🎯 Query Analysis")
        st.table(df)
        
        # Step 2: Geocoding
        if not search_property_address:
            st.error("Please mention a valid geographical location.")
            return

        with st.spinner("🌍 Geocoding location..."):
            aws_location_service_result = invoke_aws_loc_service(search_property_address)
            if not aws_location_service_result:
                st.error("Could not geocode the provided address.")
                return

        geo_coded_lat = aws_location_service_result['latitude']
        geo_coded_long = aws_location_service_result['longitude']
        
        # Display geocoding results
        geocoding_data = {
            'Address': [search_property_address],
            'Latitude': [geo_coded_lat],
            'Longitude': [geo_coded_long]
        }
        df = pd.DataFrame(geocoding_data)
        st.subheader("📍 Geocoding Results")
        st.table(df)

        # Step 3: Neural Search
        with st.spinner("🤖 Performing AI semantic search..."):
            results = run_neural_search(
                geo_coded_lat, geo_coded_long, search_property_radius, 
                user_query, search_options['max_results']
            )
        
        if not results:
            st.error("Error executing neural search query.")
            return

        # Check if results found
        if not results.get('hits', {}).get('hits'):
            st.warning("No properties found matching your semantic criteria. Try expanding your search radius or using different terms.")
            return

        # Step 4: Display results
        display_pretty_table(results)
        display_map(search_property_address, results)
        
        # Show semantic insights
        with st.expander("🧠 AI Semantic Analysis"):
            st.markdown("**How AI understood your query:**")
            st.write(f"• Generated neural embedding from: '{user_query}'")
            st.write(f"• Semantic similarity matching using cosine distance")
            st.write(f"• Found {len(results.get('hits', {}).get('hits', []))} semantically similar properties")
            st.write(f"• AI can understand concepts like 'luxury' → 'high-end', 'premium'")
            
            # Show top similarity scores
            hits = results.get('hits', {}).get('hits', [])
            if hits:
                st.markdown("**Top Semantic Matches:**")
                for i, hit in enumerate(hits[:3]):
                    score = hit.get('_score', 0)
                    property_name = hit['_source'].get('propertyName', 'Unknown')
                    st.write(f"{i+1}. {property_name} (Similarity: {score:.3f})")

        # Step 5: Generate AI summary
        with st.spinner("✨ Generating AI summary..."):
            context = json.dumps([hit['_source'] for hit in results['hits']['hits']])
            rag_summary = run_geospatial_rag(user_query, context)
            
            st.subheader("🤖 AI Summary")
            _show_model_text(rag_summary)
        
    except Exception as e:
        st.error(f"Semantic search error: operation failed")
        if DEBUG:
            LOGGER.exception("Search operation failed")

def execute_hybrid_search(user_query, search_options):
    """Execute hybrid search - ACTUAL IMPLEMENTATION"""
    
    st.subheader("🔄 Hybrid Search Results")
    st.info("Combining traditional keywords + AI semantic understanding")
    
    try:
        # Step 1: Named Entity Extraction
        with st.spinner("🧠 Analyzing query..."):
            extracted_entities_JSON = json.loads(extract_entities(user_query))
            search_property_type = _validate_property_type(extracted_entities_JSON["search_property_type"])
            search_property_address = extracted_entities_JSON["search_property_address"]
            search_property_radius = _normalize_radius(extracted_entities_JSON["search_property_radius"])
            search_property_features = _validate_features(extracted_entities_JSON["search_property_features"])

        # Display NER results
        ner_data = {
            'Property Type': [search_property_type],
            'Address': [search_property_address],
            'Distance': [search_property_radius],
            'Features': [search_property_features]
        }
        df = pd.DataFrame(ner_data)
        st.subheader("🎯 Query Analysis")
        st.table(df)
        
        # Step 2: Geocoding
        if not search_property_address:
            st.error("Please mention a valid geographical location.")
            return

        with st.spinner("🌍 Geocoding location..."):
            aws_location_service_result = invoke_aws_loc_service(search_property_address)
            if not aws_location_service_result:
                st.error("Could not geocode the provided address.")
                return

        geo_coded_lat = aws_location_service_result['latitude']
        geo_coded_long = aws_location_service_result['longitude']

        # Step 3: Hybrid Search
        with st.spinner("🔄 Performing hybrid search (Traditional + AI)..."):
            results = run_hybrid_search(
                geo_coded_lat, geo_coded_long, search_property_radius,
                search_property_type, search_property_features, user_query,
                search_options['max_results']
            )
        
        if not results:
            st.error("Error executing hybrid search query.")
            return

        # Check if results found
        if not results.get('hits', {}).get('hits'):
            st.warning("No properties found matching your criteria. Try expanding your search radius.")
            return

        # Step 4: Display results
        display_pretty_table(results)
        display_map(search_property_address, results)
        
        # Show hybrid insights
        with st.expander("🔄 Hybrid Search Analysis"):
            st.markdown("**Search Strategy Used:**")
            st.write("• **Traditional Component**: BM25 keyword matching on property type and features")
            st.write("• **AI Component**: Neural embedding similarity for semantic understanding")
            st.write("• **Geographic Component**: Distance-based filtering and boost")
            st.write("• **Combined Scoring**: Weighted combination of all relevance signals")
            
            # Show scoring breakdown
            hits = results.get('hits', {}).get('hits', [])
            if hits:
                st.markdown("**Relevance Scoring:**")
                for i, hit in enumerate(hits[:3]):
                    score = hit.get('_score', 0)
                    property_name = hit['_source'].get('propertyName', 'Unknown')
                    st.write(f"{i+1}. {property_name} (Combined Score: {score:.3f})")

        # Step 5: Generate AI summary
        with st.spinner("✨ Generating AI summary..."):
            context = json.dumps([hit['_source'] for hit in results['hits']['hits']])
            rag_summary = run_geospatial_rag(user_query, context)
            
            st.subheader("🤖 AI Summary")
            _show_model_text(rag_summary)
        
    except Exception as e:
        st.error(f"Hybrid search error: operation failed")
        if DEBUG:
            LOGGER.exception("Search operation failed")

def analyze_query_complexity(query):
    """Analyze query to determine best search strategy"""
    
    query_lower = query.lower()
    
    # Simple heuristics for demo
    has_location = any(word in query_lower for word in ['in', 'near', 'around', 'tx', 'ca', 'ny', 'fl'])
    has_property_type = any(word in query_lower for word in ['house', 'condo', 'townhouse', 'apartment'])
    has_features = any(word in query_lower for word in ['luxury', 'modern', 'pool', 'garage', 'kitchen'])
    has_semantic_terms = any(word in query_lower for word in ['dream', 'perfect', 'ideal', 'cozy', 'spacious'])
    
    complexity_score = sum([has_location, has_property_type, has_features, has_semantic_terms])
    
    if complexity_score >= 3:
        strategy = "Hybrid Search (Traditional + AI)"
        reasoning = "Complex query with multiple criteria - hybrid approach would provide best results"
    elif has_semantic_terms:
        strategy = "AI Semantic Search"
        reasoning = "Query contains subjective/semantic terms that AI can better understand"
    elif has_location and has_property_type:
        strategy = "Traditional + Geospatial"
        reasoning = "Clear location and property type - traditional search is sufficient"
    else:
        strategy = "Traditional + Geospatial"
        reasoning = "Simple query - traditional keyword matching is appropriate"
    
    return {
        "query": query,
        "complexity_score": complexity_score,
        "has_location": has_location,
        "has_property_type": has_property_type,
        "has_features": has_features,
        "has_semantic_terms": has_semantic_terms,
        "recommended_strategy": strategy,
        "reasoning": reasoning
    }

def main():
    """Main Streamlit application"""
    
    # Check if user is authenticated
    if not st.session_state.get('authenticated', False):
        show_login_form()
        return
    
    # Show logout button in sidebar
    with st.sidebar:
        st.write(f"👤 Logged in as: {st.session_state.get('username', 'Unknown')}")
        if st.button("🚪 Logout"):
            logout()
    
    st.title("🏠 Real Estate AI-Powered Search")
    show_synthetic_data_notice()
    st.subheader("Multiple Search Modes • Powered by OpenSearch and Amazon Bedrock")
    
    # Search Configuration Section
    st.markdown("### 🔍 Search Configuration")
    
    # Main search input and search button
    col1, col2, col3 = st.columns([4, 2, 1])
    
    with col1:
        # Use example query if set, otherwise use empty string
        default_value = st.session_state.get("example_query", "")
        user_query = st.text_input(
            "What are you looking for?", 
            value=default_value,
            placeholder="Find me a luxury townhouse in Frisco, TX with modern amenities",
            max_chars=MAX_QUERY_CHARS,
            key="search_query"
        )
        
        # Clear example query after using it
        if st.session_state.get("example_query", ""):
            st.session_state.example_query = ""
    
    with col2:
        search_mode = st.selectbox(
            "Search Mode:",
            [
                "🔤 Traditional + Geo", 
                "🧠 AI Semantic", 
                "🔄 Hybrid Search",
                "📊 Compare All"
            ],
            key="search_mode"
        )
    
    with col3:
        # Add some spacing to align with the input field
        st.markdown("<br>", unsafe_allow_html=True)
        search_button_clicked = st.button("🔍 Search", type="primary", use_container_width=True)
    
    # Advanced options (collapsible)
    with st.expander("🔧 Advanced Search Options"):
        col_a, col_b = st.columns(2)
        
        with col_a:
            enable_vector_search = st.checkbox("Enable Vector Similarity", value=True, help="Use AI embeddings for semantic similarity")
            enable_geo_boost = st.checkbox("Boost by Distance", value=True, help="Prioritize closer properties")
        
        with col_b:
            semantic_weight = st.slider("AI Semantic Weight", 0.0, 1.0, 0.7, help="How much to weight AI understanding vs keywords")
            max_results = st.slider("Max Results", 5, 50, 20, help="Maximum number of properties to return")

    
    # About section with search mode explanations
    with st.expander("About Search Modes & This Application"):
        st.markdown("""
        ### 🔍 Search Mode Explanations:
        
        - **🔤 Traditional + Geo**: Keyword matching + geographic distance (your current method)
        - **🧠 AI Semantic**: Uses AI to understand meaning and context of your query
        - **🔄 Hybrid Search**: Combines traditional keywords with AI semantic understanding
        - **📊 Compare All**: Shows results from all search methods side-by-side
        
        ### 🏗️ Technical Architecture:
        - **Named Entity Recognition** using Amazon Bedrock
        - **Geocoding** with AWS Location Services  
        - **Multiple Search Engines** with OpenSearch (lexical + geospatial + semantic)
        - **AI Response Generation** using Amazon Bedrock
        """)
        

    # Example prompts
    with st.expander("💡 Example Queries"):
        st.markdown("""
        **🏠 Property Type Searches:**
        - "Townhouse in Frisco, TX with good schools"
        - "2-bedroom apartments in Seattle under $500k"
        - "Find single family homes near Apple campus in California"
        """)
        
        # with example_col2:
        #     st.markdown("""
        #     **🎯 Feature-Based Searches:**
        #     - "Modern kitchen with granite countertops"
        #     - "Waterfront property with private dock"
        #     - "Family-friendly neighborhood near schools"
        #     """)

    # Search execution - always show button, execute when clicked with query
    if search_button_clicked:
        if user_query.strip():
            search_options = {
                'mode': search_mode,
                'vector_search': enable_vector_search,
                'geo_boost': enable_geo_boost,
                'semantic_weight': semantic_weight,
                'max_results': max_results,
            }
            
            # Execute the enhanced search
            execute_enhanced_search(user_query, search_options)
        else:
            st.warning("⚠️ Please enter a search query before clicking Search!")

if __name__ == "__main__":
    main()
