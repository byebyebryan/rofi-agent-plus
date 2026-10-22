"""Offline conformance checks for the vendored public Host Mesh bundle."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

from rofi_agent_plus.host_mesh import HostMeshError, local_only_mesh, parse_mesh
from rofi_agent_plus.wire import WireError, decode_document

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = {
    "host-mesh-v1": ROOT / "contracts" / "host-mesh-v1",
}
UPSTREAM = {
    "host-mesh-v1": "https://github.com/byebyebryan/rofi-ssh-plus",
}
HEX_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)


class SchemaError(ValueError):
    """A structural Draft 2020-12 feature used by the checked bundles."""


class LocalSchemaValidator:
    """Small offline validator for the bundle's deliberately finite subset.

    The accepted schemas use local ``$ref``, ``$defs``, ``type``, ``const``,
    ``enum``, ``required``, ``properties``, ``propertyNames``,
    ``additionalProperties``, ``items``, ``anyOf``, bounds, and ``pattern``.
    Implementing those constructs here keeps the default gate independent of
    a runtime or network schema dependency while still testing the published
    structural contract rather than merely loading JSON.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.documents: dict[Path, object] = {}

    def _document(self, path: Path) -> object:
        path = path.resolve()
        if path not in self.documents:
            self.documents[path] = json.loads(path.read_text(encoding="utf-8"))
        return self.documents[path]

    def _resolve(self, ref: str, current: Path, document: object) -> tuple[object, Path, object]:
        if ref.startswith("#"):
            target = document
            fragment = ref[1:]
            if fragment:
                for part in fragment.lstrip("/").split("/"):
                    if not isinstance(target, Mapping) or part not in target:
                        raise SchemaError(f"unresolved local reference {ref}")
                    target = target[part]
            return target, current, document
        name, separator, fragment = ref.partition("#")
        target_path = (current.parent / name).resolve()
        root_document = self._document(target_path)
        target = root_document
        if separator and fragment:
            for part in fragment.lstrip("/").split("/"):
                if not isinstance(target, Mapping) or part not in target:
                    raise SchemaError(f"unresolved reference {ref}")
                target = target[part]
        return target, target_path, root_document

    @staticmethod
    def _type_matches(value: object, expected: str) -> bool:
        if expected == "object":
            return isinstance(value, Mapping)
        if expected == "array":
            return isinstance(value, list)
        if expected == "string":
            return isinstance(value, str)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "boolean":
            return isinstance(value, bool)
        if expected == "null":
            return value is None
        if expected == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        return False

    def validate(self, value: object, schema_name: str) -> None:
        path = (self.root / schema_name).resolve()
        self._validate(value, self._document(path), path, self._document(path))

    def _validate(self, value: object, schema: object, current: Path, document: object) -> None:
        if not isinstance(schema, Mapping):
            raise SchemaError("schema node is not an object")
        if "$ref" in schema:
            target, target_path, root_document = self._resolve(
                str(schema["$ref"]), current, document
            )
            self._validate(value, target, target_path, root_document)
            return
        if "anyOf" in schema:
            failures: list[Exception] = []
            for option in schema["anyOf"]:
                try:
                    self._validate(value, option, current, document)
                except SchemaError as error:
                    failures.append(error)
                else:
                    break
            else:
                raise SchemaError("no anyOf branch matched") from failures[-1]
        if "type" in schema:
            expected = schema["type"]
            if not isinstance(expected, str) or not self._type_matches(value, expected):
                raise SchemaError(f"expected {expected}")
        if "const" in schema and value != schema["const"]:
            raise SchemaError("const mismatch")
        if "enum" in schema and value not in schema["enum"]:
            raise SchemaError("enum mismatch")
        if isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                raise SchemaError("string is too short")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise SchemaError("string is too long")
            if "pattern" in schema and re.search(str(schema["pattern"]), value) is None:
                raise SchemaError("string pattern mismatch")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise SchemaError("number is below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                raise SchemaError("number is above maximum")
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                raise SchemaError("array is too short")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                raise SchemaError("array is too long")
            if "items" in schema:
                for item in value:
                    self._validate(item, schema["items"], current, document)
        if isinstance(value, Mapping):
            required = schema.get("required", [])
            if any(name not in value for name in required):
                raise SchemaError("required property is missing")
            properties = schema.get("properties", {})
            for name, child in properties.items():
                if name in value:
                    self._validate(value[name], child, current, document)
            if "propertyNames" in schema:
                for name in value:
                    self._validate(name, schema["propertyNames"], current, document)
            additional = schema.get("additionalProperties")
            if isinstance(additional, Mapping):
                for name, child in value.items():
                    if name not in properties:
                        self._validate(child, additional, current, document)
            elif additional is False:
                unknown = set(value) - set(properties)
                if unknown:
                    raise SchemaError("unknown property")


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _lookup(value: object, dotted: str) -> object:
    for part in dotted.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise AssertionError(f"fixture path does not exist: {dotted}")
        value = value[part]
    return value


class ContractBundleTest(unittest.TestCase):
    def test_manifest_coverage_digest_and_released_provenance(self) -> None:
        for name, directory in CONTRACTS.items():
            with self.subTest(contract=name):
                raw = (directory / "SHA256SUMS").read_bytes()
                self.assertTrue(raw.endswith(b"\n"))
                entries: list[tuple[str, str]] = []
                for line in raw.splitlines():
                    digest, relative = line.decode("ascii").split("  ", 1)
                    self.assertRegex(digest, r"^[0-9a-f]{64}$")
                    self.assertNotEqual("SHA256SUMS", relative)
                    entries.append((relative, digest))
                names = [relative for relative, _digest in entries]
                self.assertEqual(names, sorted(names, key=lambda item: item.encode()))
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(
                    set(names),
                    {
                        path.relative_to(directory).as_posix()
                        for path in directory.rglob("*")
                        if path.is_file()
                        and path not in {directory / "SHA256SUMS", directory / "SOURCE.json"}
                    },
                )
                for relative, digest in entries:
                    self.assertEqual(
                        digest, hashlib.sha256((directory / relative).read_bytes()).hexdigest()
                    )
                source = _json(directory / "SOURCE.json")
                self.assertIsInstance(source, dict)
                self.assertEqual(
                    set(source),
                    {
                        "contract",
                        "upstreamRepository",
                        "sourceState",
                        "sourceCommit",
                        "bundleDigest",
                    },
                )
                self.assertEqual(name, source["contract"])
                self.assertEqual(UPSTREAM[name], source["upstreamRepository"])
                self.assertEqual("released", source["sourceState"])
                self.assertRegex(source["sourceCommit"], r"^[0-9a-f]{40}$")
                self.assertEqual(
                    f"sha256:{hashlib.sha256(raw).hexdigest()}",
                    source["bundleDigest"],
                )
                self.assertRegex(source["bundleDigest"], HEX_DIGEST)

    def test_complete_bundle_has_no_private_fixture_names(self) -> None:
        for directory in CONTRACTS.values():
            for path in directory.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(directory).as_posix()
                    self.assertNotIn("history-migration", relative)
                    self.assertNotIn("tests/", relative)

    def test_draft_schema_subset_validates_every_indexed_document(self) -> None:
        for name, directory in CONTRACTS.items():
            validator = LocalSchemaValidator(directory)
            index = _json(directory / "fixtures/index.json")
            self.assertIsInstance(index, dict)
            assert isinstance(index, dict)
            self.assertEqual(name, index["contract"])
            for case in index["cases"]:
                with self.subTest(contract=name, case=case["name"]):
                    data = (directory / "fixtures" / case["fixture"]).read_bytes()
                    if case["kind"] == "raw":
                        with self.assertRaises(WireError):
                            decode_document(data, limit=1 << 20)
                        continue
                    document = decode_document(data, limit=1 << 20)
                    validator.validate(document, case["schema"])
                    self.assertEqual(
                        "nonzero" if "/error" in case["schema"] else "zero",
                        case["expectedExit"],
                    )
            for supporting in index.get("supportingFixtures", []):
                for response in supporting.get("responses", []):
                    with self.subTest(
                        contract=name, fixture=supporting["fixture"], path=response["path"]
                    ):
                        document = _lookup(
                            _json(directory / "fixtures" / supporting["fixture"]), response["path"]
                        )
                        validator.validate(document, response["schema"])

    def test_every_fixture_is_indexed_exactly_once(self) -> None:
        for name, directory in CONTRACTS.items():
            with self.subTest(contract=name):
                index = _json(directory / "fixtures/index.json")
                assert isinstance(index, dict)
                references = [case["fixture"] for case in index["cases"]]
                references.extend(
                    supporting["fixture"] for supporting in index["supportingFixtures"]
                )
                expected = {
                    path.relative_to(directory / "fixtures").as_posix()
                    for path in (directory / "fixtures").rglob("*")
                    # Host Mesh's README is bundle documentation rather than
                    # a fixture; Tmux indexes its analogous file explicitly.
                    if path.is_file()
                    and path.name != "index.json"
                    and (name == "tmux-session-v1" or path.name != "README.md")
                }
                self.assertEqual(expected, set(references))
                self.assertEqual(len(expected), len(references))

    def test_known_bool_and_integer_schema_types_are_not_cross_coerced(self) -> None:
        validator = LocalSchemaValidator(CONTRACTS["host-mesh-v1"])
        for value in (True, False, 1.0):
            with self.assertRaises(SchemaError):
                validator._validate(value, {"type": "integer"}, Path("inline"), {})
        for value in (0, 1):
            with self.assertRaises(SchemaError):
                validator._validate(value, {"type": "boolean"}, Path("inline"), {})
        for value in (True, 1.0):
            inventory = _json(CONTRACTS["host-mesh-v1"] / "fixtures/valid/list-local-only.json")
            inventory["schemaVersion"] = value
            with self.assertRaises(HostMeshError):
                parse_mesh(inventory)

    def test_wire_rejects_float_overflow_as_nonfinite(self) -> None:
        with self.assertRaises(WireError):
            decode_document(b'{"value":1e999}\n', limit=1 << 20)

    def test_host_identity_semantics_include_id_alias_and_route_ownership(self) -> None:
        mesh = _json(CONTRACTS["host-mesh-v1"] / "fixtures/valid/list-multi-route.json")
        assert isinstance(mesh, dict)
        invalid = json.loads(json.dumps(mesh))
        invalid["hosts"][1]["id"] = "alpha.example"
        with self.assertRaisesRegex(HostMeshError, "ambiguous"):
            parse_mesh(invalid)

        route_collision = json.loads(json.dumps(mesh))
        route_collision["hosts"][1]["routes"][0]["destination"] = "alpha.example"
        with self.assertRaisesRegex(HostMeshError, "ambiguous"):
            parse_mesh(route_collision)

    def test_unknown_nested_fields_remain_extensible(self) -> None:
        mesh = _json(CONTRACTS["host-mesh-v1"] / "fixtures/valid/list-local-only.json")
        mesh["extension"] = {"nested": ["accepted"]}
        mesh["hosts"][0]["extension"] = {"nested": {"field": "accepted"}}
        parse_mesh(mesh)

    def test_local_only_identity_matches_public_fallback_rules(self) -> None:
        for hostname, expected in (
            ("Alpha.Example", "alpha"),
            ("work-station", "work-station"),
            ("invalid hostname", "localhost"),
        ):
            with self.subTest(hostname=hostname):
                with (
                    patch("rofi_agent_plus.host_mesh.socket.gethostname", return_value=hostname),
                    patch("rofi_agent_plus.host_mesh.socket.getfqdn", return_value=hostname),
                ):
                    local = local_only_mesh().local
                self.assertEqual(local.host_id, expected)
                self.assertEqual(local.routes, ())

    def test_sync_script_is_executable_and_bundle_sources_are_public_only(self) -> None:
        mode = (ROOT / "scripts/check-contract-sync").stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)
        self.assertNotIn(
            "/home/",
            " ".join(
                path.name for directory in CONTRACTS.values() for path in directory.rglob("*")
            ),
        )


if __name__ == "__main__":
    unittest.main()
