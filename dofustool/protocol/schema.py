"""Schéma des messages, lu dans le dofus.proto obfusqué de dofus-sqlite (aligné sur le build du jeu).

Sert à l'exploration : `describe` rend la structure d'un corps de message avec ses valeurs
numériques, et masque toute chaîne ou suite d'octets (PLAN.md §3.3). Sans schéma fiable
pour un champ, sa valeur n'est pas interprétée.
"""
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .wire import I32, I64, LEN, VARINT, WireError, iter_fields, read_varint, to_signed

DOFUS_PROTO = Path(__file__).resolve().parents[2] / "data" / "static" / "dofus.proto"

_VARINT_TYPES = {"int32", "int64", "uint32", "uint64", "sint32", "sint64", "bool"}
_FIXED_TYPES = {"fixed32": (I32, "<I"), "sfixed32": (I32, "<i"), "float": (I32, "<f"),
                "fixed64": (I64, "<Q"), "sfixed64": (I64, "<q"), "double": (I64, "<d")}  # fmt: skip
_OPAQUE_TYPES = {"string", "bytes"}

_FIELD = re.compile(r"^(?:(repeated|optional) )?([\w.]+) (\w+) = (\d+);$")
_MAP = re.compile(r"^map<(\w+), ?([\w.]+)> (\w+) = (\d+);$")


@dataclass(slots=True)
class Field:
    number: int
    name: str
    type: str
    repeated: bool = False
    map_key: str | None = None  # renseigné pour map<clé, type>


@dataclass(slots=True)
class MessageType:
    name: str
    parent: "MessageType | None" = None
    fields: dict[int, Field] = field(default_factory=dict)
    nested: dict[str, "MessageType"] = field(default_factory=dict)

    def resolve(self, type_name: str, top: dict[str, "MessageType"]) -> "MessageType | None":
        scope: MessageType | None = self
        first, *rest = type_name.split(".")
        found = None
        while scope is not None and found is None:
            found = scope.nested.get(first) or (scope if scope.name == first else None)
            scope = scope.parent
        found = found or top.get(first)
        for part in rest:
            if found is None:
                return None
            found = found.nested.get(part)
        return found


class Schema:
    def __init__(self, messages: dict[str, MessageType]) -> None:
        self.messages = messages

    @classmethod
    def load(cls, path: Path = DOFUS_PROTO) -> "Schema":
        return cls.parse(path.read_text(encoding="utf-8"))

    @classmethod
    def parse(cls, text: str) -> "Schema":
        top: dict[str, MessageType] = {}
        stack: list[MessageType | None] = []
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("message ") and line.endswith("{"):
                parent = stack[-1] if stack else None
                msg = MessageType(line.split()[1], parent)
                (parent.nested if parent else top)[msg.name] = msg
                stack.append(msg)
            elif line.endswith("{"):
                stack.append(stack[-1] if stack else None)  # oneof : champs rattachés au message parent
            elif line == "}":
                if stack:
                    stack.pop()
            elif stack and stack[-1] is not None:
                if m := _FIELD.match(line):
                    label, type_name, name, number = m.groups()
                    stack[-1].fields[int(number)] = Field(int(number), name, type_name, label == "repeated")
                elif m := _MAP.match(line):
                    key_type, type_name, name, number = m.groups()
                    stack[-1].fields[int(number)] = Field(int(number), name, type_name, True, key_type)
        return cls(top)

    def describe(self, key: str, body: bytes) -> tuple[object, int]:
        """Renvoie (structure expurgée, nombre de champs en désaccord avec le schéma)."""
        msg = self.messages.get(key)
        if msg is None:
            return f"<pas de schéma, {len(body)} o>", 1
        counter = [0]
        return self._message(msg, body, counter), counter[0]

    def _message(self, msg: MessageType, body: bytes, mismatches: list[int]) -> object:
        try:
            wire_fields = list(iter_fields(body))
        except WireError:
            mismatches[0] += 1
            return f"<illisible, {len(body)} o>"
        out: dict[int, object] = {}
        for number, wire_type, value in wire_fields:
            spec = msg.fields.get(number)
            if spec is None:
                mismatches[0] += 1
                decoded: list[object] = [f"<champ hors schéma, type de fil {wire_type}>"]
            elif spec.map_key is not None:
                entry = MessageType("", msg, {1: Field(1, "k", spec.map_key), 2: Field(2, "v", spec.type)})
                decoded = [self._message(entry, value, mismatches)] if wire_type == LEN else self._bad(mismatches)
            else:
                decoded = self._value(msg, spec, wire_type, value, mismatches)
            if spec is not None and spec.repeated:
                out.setdefault(number, []).extend(decoded)
            else:
                out[number] = decoded[-1] if decoded else None
        return out

    @staticmethod
    def _bad(mismatches: list[int]) -> list[object]:
        mismatches[0] += 1
        return ["<type de fil inattendu>"]

    def _value(self, msg: MessageType, spec: Field, wire_type: int, value, mismatches: list[int]) -> list[object]:
        kind = spec.type
        if kind in _OPAQUE_TYPES:
            return [f"<{kind} {len(value)} o>"] if wire_type == LEN else self._bad(mismatches)
        if kind in _VARINT_TYPES:
            if wire_type == VARINT:
                return [_scalar(kind, value)]
            if wire_type == LEN and spec.repeated:  # champ répété « packed »
                values, pos = [], 0
                try:
                    while pos < len(value):
                        v, pos = read_varint(value, pos)
                        values.append(_scalar(kind, v))
                except WireError:
                    return self._bad(mismatches)
                return values
            return self._bad(mismatches)
        if kind in _FIXED_TYPES:
            expected, fmt = _FIXED_TYPES[kind]
            size = struct.calcsize(fmt)
            if wire_type == expected:
                return [struct.unpack(fmt, value.to_bytes(size, "little"))[0]]
            if wire_type == LEN and spec.repeated and len(value) % size == 0:
                return [x[0] for x in struct.iter_unpack(fmt, value)]
            return self._bad(mismatches)
        target = msg.resolve(kind, self.messages)
        # protodec rend les énumérations comme des messages vides : sur le fil, ce sont des varints.
        if target is None or (not target.fields and wire_type != LEN):
            if wire_type == VARINT:
                return [to_signed(value)]
            if wire_type == LEN and spec.repeated and target is None:
                return self._value(msg, Field(spec.number, spec.name, "int64", True), wire_type, value, mismatches)
            return self._bad(mismatches)
        if not target.fields and wire_type == LEN and spec.repeated and value:
            # énumération répétée « packed » (un vrai message vide aurait une longueur nulle)
            return self._value(msg, Field(spec.number, spec.name, "int64", True), wire_type, value, mismatches)
        return [self._message(target, value, mismatches)] if wire_type == LEN else self._bad(mismatches)


def _scalar(kind: str, value: int) -> int | bool:
    if kind == "bool":
        return bool(value)
    if kind.startswith("sint"):
        return (value >> 1) ^ -(value & 1)
    if kind.startswith("uint"):
        return value
    return to_signed(value)


def render(value: object, max_items: int = 4, depth: int = 0) -> str:
    """Rendu compact sur une ligne ; les longues listes sont abrégées avec leur longueur."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {render(v, max_items, depth + 1)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        shown = ", ".join(render(v, max_items, depth + 1) for v in value[:max_items])
        more = f", … ({len(value)} au total)" if len(value) > max_items else ""
        return f"[{shown}{more}]"
    return str(value)
