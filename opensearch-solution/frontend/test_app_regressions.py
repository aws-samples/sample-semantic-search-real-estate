#!/usr/bin/env python3
"""Focused regressions for live frontend fixes."""

from __future__ import annotations

import inspect
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.update(
    {
        "AWS_REGION": "us-east-1",
        "OPENSEARCH_ENDPOINT": "https://search.invalid",
        "COGNITO_USER_POOL_ID": "us-east-1_PLACEHOLDER",
        "COGNITO_APP_CLIENT_ID": "PLACEHOLDERCLIENT",
        "COGNITO_IDENTITY_POOL_ID": "us-east-1:00000000-0000-0000-0000-000000000000",
        "PLACE_INDEX_NAME": "placeholder-index",
    }
)

import app
from streamlit.testing.v1 import AppTest


class CapturingOpenSearchClient:
    def __init__(self) -> None:
        self.body = None

    def search(self, *, index, body):
        self.body = body
        return {"hits": {"hits": []}}


class QueryShapeTest(unittest.TestCase):
    def test_prompt_template_uses_langchain_core(self) -> None:
        self.assertEqual(app.PromptTemplate.__module__, "langchain_core.prompts.prompt")

    def test_scalar_property_type_uses_match(self) -> None:
        query = app._property_type_query("Townhouse")
        self.assertEqual(query["match"]["propertyType"]["query"], "Townhouse")

    def test_list_property_types_use_terms(self) -> None:
        values = ["Townhouse", "Condos"]
        query = app._property_type_query(values)
        self.assertEqual(query["terms"]["propertyType"], values)

    def test_traditional_query_normalizes_scalar_and_list_types(self) -> None:
        for value, query_kind in (
            ("Townhouse", "match"),
            (["Townhouse", "Condos"], "terms"),
        ):
            with self.subTest(value=value), patch.object(
                app, "create_opensearch_client"
            ) as create_client:
                client = CapturingOpenSearchClient()
                create_client.return_value = client
                app.run_opensearch_geospatial_query(
                    33.0, -96.0, "10mi", value, "synthetic"
                )
                clause = client.body["query"]["bool"]["must"][0]
                self.assertIn(query_kind, clause)

    def test_hybrid_query_normalizes_scalar_and_list_types(self) -> None:
        for value, query_kind in (
            ("Townhouse", "match"),
            (["Townhouse", "Condos"], "terms"),
        ):
            with self.subTest(value=value), patch.object(
                app, "create_opensearch_client"
            ) as create_client, patch.object(
                app, "generate_query_embedding", return_value=[0.0]
            ):
                client = CapturingOpenSearchClient()
                create_client.return_value = client
                app.run_hybrid_search(
                    33.0, -96.0, "10mi", value, "synthetic", "query"
                )
                clause = client.body["query"]["bool"]["should"][0]["bool"][
                    "should"
                ][0]
                self.assertIn(query_kind, clause)


def _find_values(node, key):
    """Collect every value stored under `key` anywhere in a nested query body."""
    found = []
    if isinstance(node, dict):
        for name, value in node.items():
            if name == key:
                found.append(value)
            found.extend(_find_values(value, key))
    elif isinstance(node, list):
        for value in node:
            found.extend(_find_values(value, key))
    return found


class RadiusValidationTest(unittest.TestCase):
    def test_valid_values_are_normalized(self) -> None:
        for value, expected in (
            ("10mi", "10mi"),
            ("25km", "25km"),
            (6, "6mi"),
            (6.5, "6.5mi"),
            ("  7  ", "7mi"),
            ("10 miles", "10mi"),
            ("5 Kilometers", "5km"),
        ):
            with self.subTest(value=value):
                self.assertEqual(app._normalize_radius(value), expected)

    def test_large_and_small_values_are_clamped(self) -> None:
        for value, expected in (
            (5000, "100mi"),
            ("9999km", "160km"),
            (1e30, "100mi"),
            (0.001, "0.1mi"),
        ):
            with self.subTest(value=value):
                self.assertEqual(app._normalize_radius(value), expected)

    def test_bad_values_fall_back_to_default(self) -> None:
        json_values = json.loads("[NaN, Infinity, -Infinity, -5, 0, true]")
        bad_values = json_values + [
            "nanmi",
            "-3mi",
            "1e999",
            "abc",
            "",
            None,
            [1],
            {"a": 1},
            10**400,
            "9" * 40,
            "10ft",
            "500m",
        ]
        for value in bad_values:
            with self.subTest(value=repr(value)[:40]):
                self.assertEqual(app._normalize_radius(value), app.DEFAULT_RADIUS)


class PropertyTypeValidationTest(unittest.TestCase):
    def test_allowed_values_are_kept_in_canonical_form(self) -> None:
        self.assertEqual(app._validate_property_type("Townhouse"), "Townhouse")
        self.assertEqual(app._validate_property_type("  condos "), "Condos")
        self.assertEqual(
            app._validate_property_type(["Townhouse", "Castle", "townhouse", "Condos"]),
            ["Townhouse", "Condos"],
        )

    def test_bad_values_mean_no_type_restriction(self) -> None:
        allowed = list(app.ALLOWED_PROPERTY_TYPES)
        for value in (
            "Castle",
            "x" * 10000,
            ["Castle"],
            [],
            {"a": 1},
            5,
            None,
            float("nan"),
            [["Townhouse"]],
            [{"a": 1}],
        ):
            with self.subTest(value=repr(value)[:40]):
                self.assertEqual(app._validate_property_type(value), allowed)


class FeatureValidationTest(unittest.TestCase):
    def test_string_is_cleaned_and_capped(self) -> None:
        self.assertEqual(app._validate_features("pool,  garage\n"), "pool, garage")
        self.assertEqual(
            len(app._validate_features("a" * 5000)), app.MAX_FEATURE_CHARS
        )

    def test_list_keeps_short_strings_only(self) -> None:
        self.assertEqual(
            app._validate_features(["pool", 5, None, {"a": 1}, ["x"], "garage"]),
            ["pool", "garage"],
        )
        self.assertEqual(
            len(app._validate_features([f"feature {i}" for i in range(500)])),
            app.MAX_FEATURES,
        )
        capped = app._validate_features(["y" * 9999])
        self.assertEqual(len(capped), 1)
        self.assertEqual(len(capped[0]), app.MAX_FEATURE_CHARS)

    def test_other_types_become_empty_list(self) -> None:
        for value in ({"a": 1}, 5, None, float("nan"), "", "   ", [], [None]):
            with self.subTest(value=repr(value)):
                self.assertEqual(app._validate_features(value), [])

    def test_result_still_joins_into_query_text(self) -> None:
        self.assertEqual(" ".join(app._validate_features(["a", "b"])), "a b")


class SanitizedQueryShapeTest(unittest.TestCase):
    def _sanitized(self):
        bad_type = {"a": 1}
        bad_radius = float("nan")
        bad_features = ["pool", {"b": 2}]
        return (
            app._validate_property_type(bad_type),
            app._normalize_radius(bad_radius),
            app._validate_features(bad_features),
        )

    def _assert_safe(self, body) -> None:
        distances = _find_values(body, "distance")
        self.assertTrue(distances)
        self.assertEqual(set(distances), {app.DEFAULT_RADIUS})
        self.assertIn(list(app.ALLOWED_PROPERTY_TYPES), _find_values(body, "propertyType"))

    def test_traditional_query_receives_only_safe_values(self) -> None:
        property_type, radius, features = self._sanitized()
        with patch.object(app, "create_opensearch_client") as create_client:
            client = CapturingOpenSearchClient()
            create_client.return_value = client
            app.run_opensearch_geospatial_query(33.0, -96.0, radius, property_type, features)
        self._assert_safe(client.body)

    def test_hybrid_query_receives_only_safe_values(self) -> None:
        property_type, radius, features = self._sanitized()
        with patch.object(app, "create_opensearch_client") as create_client, patch.object(
            app, "generate_query_embedding", return_value=[0.0]
        ):
            client = CapturingOpenSearchClient()
            create_client.return_value = client
            app.run_hybrid_search(33.0, -96.0, radius, property_type, features, "query")
        self._assert_safe(client.body)


class ValidationWiringTest(unittest.TestCase):
    def test_every_live_search_path_validates_model_output(self) -> None:
        for function in (
            app.execute_comparison_search,
            app.execute_traditional_search,
            app.execute_semantic_search,
            app.execute_hybrid_search,
        ):
            source = inspect.getsource(function)
            with self.subTest(function=function.__name__):
                self.assertIn("_validate_property_type(", source)
                self.assertIn("_normalize_radius(", source)
                self.assertIn("_validate_features(", source)
                self.assertNotIn(
                    '= extracted_entities_JSON["search_property_type"]', source
                )
                self.assertNotIn(
                    '= extracted_entities_JSON["search_property_features"]', source
                )


class SearchInputBoundTest(unittest.TestCase):
    def test_search_text_input_has_a_length_limit(self) -> None:
        self.assertEqual(app.MAX_QUERY_CHARS, 500)
        test_app = AppTest.from_file(str(Path(__file__).with_name("app.py")))
        test_app.session_state["authenticated"] = True
        test_app.session_state["username"] = "sample-user"
        test_app.run()
        self.assertEqual(
            test_app.text_input(key="search_query").max_chars, app.MAX_QUERY_CHARS
        )


class ModelTextRenderingTest(unittest.TestCase):
    PAYLOAD = "See [details](https://attacker.invalid) ![x](https://attacker.invalid/p.png)"

    def test_helper_renders_plain_text_not_markdown(self) -> None:
        with patch.object(app.st, "text") as text, patch.object(
            app.st, "write"
        ) as write, patch.object(app.st, "markdown") as markdown:
            app._show_model_text(self.PAYLOAD)
        text.assert_called_once_with(self.PAYLOAD)
        write.assert_not_called()
        markdown.assert_not_called()

    def test_every_summary_call_site_uses_plain_text_helper(self) -> None:
        source = inspect.getsource(app)
        self.assertNotIn("st.write(rag_summary)", source)
        self.assertNotIn("st.markdown(rag_summary", source)
        self.assertEqual(source.count("_show_model_text(rag_summary)"), 5)


class MapTextEscapingTest(unittest.TestCase):
    PAYLOAD = "<img src=x onerror=alert(1)>"
    ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"

    def test_helper_escapes_html(self) -> None:
        self.assertEqual(app._escape_map_text(self.PAYLOAD), self.ESCAPED)

    def test_helper_leaves_ordinary_text_alone(self) -> None:
        text = "102 Sample Avenue, Fictional Cupertino District"
        self.assertEqual(app._escape_map_text(text), text)

    def test_helper_neutralizes_javascript_template_characters(self) -> None:
        result = app._escape_map_text("a`b${x}c\\u003cd")
        for char in ("`", "$", "\\"):
            self.assertNotIn(char, result)

    def test_rendered_map_does_not_contain_raw_payload(self) -> None:
        field = self.PAYLOAD + "`${alert(2)}\\u003cimg src=y\\u003e"
        data = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "propertyId": field,
                            "propertyType": field,
                            "propertyName": field,
                            "propertyAddress": field,
                            "propertyCoordinates": "33.0,-96.0",
                        }
                    }
                ]
            }
        }
        captured = {}
        with patch.object(
            app, "folium_static", side_effect=lambda m: captured.setdefault("map", m)
        ), patch.object(app.st, "header"):
            app.display_map("Fictional Place", data)
        page = captured["map"].get_root().render()
        self.assertNotIn("<img src=x", page)
        self.assertIn(self.ESCAPED, page)
        self.assertNotIn("`${alert", page)
        self.assertNotIn("\\u003cimg src=y", page)
        self.assertIn("&#92;u003cimg src=y", page)


class ComparisonBookkeepingTest(unittest.TestCase):
    def test_none_and_exception_are_method_specific_failures(self) -> None:
        result, _, message = app._run_comparison_method("traditional", lambda: None)
        self.assertIsNone(result)
        self.assertIn("Traditional search failed", message)

        def fail():
            raise RuntimeError("test failure")

        result, _, message = app._run_comparison_method("hybrid", fail)
        self.assertIsNone(result)
        self.assertIn("Hybrid search failed", message)

    def test_partial_summary_excludes_failure_and_keeps_provenance(self) -> None:
        results = {
            "traditional": None,
            "semantic": {"hits": {"hits": [{"_source": {"propertyId": "FRI001"}}]}},
            "hybrid": {"hits": {"hits": [{"_source": {"propertyId": "FRI002"}}]}},
        }
        successful = app._successful_searches(results)
        self.assertEqual(successful, ["semantic", "hybrid"])
        self.assertEqual(
            app._build_summary_results(results, successful),
            {
                "semantic": [{"propertyId": "FRI001"}],
                "hybrid": [{"propertyId": "FRI002"}],
            },
        )

    def test_final_status_replaces_running_state(self) -> None:
        level, message = app._comparison_completion(set())
        self.assertEqual((level, message), ("success", "All search methods completed successfully."))
        level, message = app._comparison_completion({"traditional"})
        self.assertEqual(level, "warning")
        self.assertIn("summary uses successful methods only", message)


class SyntheticDataNoticeTest(unittest.TestCase):
    def test_notice_is_visible_before_authentication(self) -> None:
        test_app = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run()
        notices = [warning.value for warning in test_app.warning]
        self.assertIn(app.SYNTHETIC_DATA_NOTICE, notices)

    def test_notice_is_visible_after_authentication(self) -> None:
        test_app = AppTest.from_file(str(Path(__file__).with_name("app.py")))
        test_app.session_state["authenticated"] = True
        test_app.session_state["username"] = "sample-user"
        test_app.run()
        notices = [warning.value for warning in test_app.warning]
        self.assertIn(app.SYNTHETIC_DATA_NOTICE, notices)


if __name__ == "__main__":
    unittest.main()
