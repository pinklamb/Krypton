"""Small spreadsheet engine with formulas, dependency tracking, and CSV support."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FormulaError(Exception):
    """A formula syntax or evaluation error suitable for display in a cell."""


TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z_0-9]*)|(<=|>=|<>|=|<|>)|([+*/^(),:!-]))")
CELL = re.compile(r"[A-Z]+[1-9][0-9]*\Z")


def column_number(label: str) -> int:
    result = 0
    for char in label:
        result = result * 26 + ord(char) - 64
    return result


def cell_address(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Za-z]+)([1-9][0-9]*)", text)
    if not match:
        raise FormulaError(f"invalid cell reference: {text}")
    return int(match.group(2)) - 1, column_number(match.group(1).upper()) - 1


def tokenize(source: str) -> list[str]:
    tokens, position = [], 0
    while position < len(source):
        token, position = next_token(source, position)
        if token is None:
            break
        tokens.append(token)
    tokens.append("<end>")
    return tokens


def next_token(source: str, position: int) -> tuple[str | None, int]:
    if source[position:].isspace():
        return None, position
    match = TOKEN.match(source, position)
    if match is None:
        raise FormulaError(f"unexpected character at {position + 1}")
    token = next(value for value in match.groups() if value is not None)
    return token, match.end()


class Parser:
    """Recursive descent parser that produces tuples and exposes referenced cells."""

    PRECEDENCE = {"=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1, "+": 2, "-": 2, "*": 3, "/": 3, "^": 4}

    def __init__(self, source: str):
        self.tokens = tokenize(source)
        self.position = 0
        self.dependencies: set[str] = set()

    def parse(self) -> tuple[Any, set[str]]:
        tree = self.expression()
        if self.peek() != "<end>":
            raise FormulaError(f"unexpected token: {self.peek()}")
        return tree, self.dependencies

    def peek(self) -> str:
        return self.tokens[self.position]

    def take(self) -> str:
        token = self.peek()
        self.position += 1
        return token

    def expression(self, minimum: int = 0) -> tuple:
        left = self.prefix()
        while self.peek() in self.PRECEDENCE and self.PRECEDENCE[self.peek()] >= minimum:
            operator = self.take()
            precedence = self.PRECEDENCE[operator]
            right = self.expression(precedence + (0 if operator == "^" else 1))
            left = ("binary", operator, left, right)
        return left

    def prefix(self) -> tuple:
        token = self.take()
        if token in ("+", "-"):
            return ("unary", token, self.expression(5))
        if token == "(":
            tree = self.expression()
            self.expect(")")
            return tree
        if token[0].isdigit():
            return ("number", float(token))
        if token == "<end>":
            raise FormulaError("expected an expression")
        return self.parse_name(token)

    def parse_name(self, token: str) -> tuple:
        name = token.upper()
        if self.peek() == "(":
            return self.parse_call(name)
        if CELL.fullmatch(name):
            return self.parse_reference(name)
        if name in ("TRUE", "FALSE"):
            return ("boolean", name == "TRUE")
        raise FormulaError(f"unknown name: {token}")

    def parse_call(self, name: str) -> tuple:
        self.take()
        arguments = []
        if self.peek() != ")":
            arguments.append(self.expression())
            while self.peek() == ",":
                self.take()
                arguments.append(self.expression())
        self.expect(")")
        return ("call", name, arguments)

    def parse_reference(self, name: str) -> tuple:
        self.dependencies.add(name)
        if self.peek() != ":":
            return ("cell", name)
        self.take()
        endpoint = self.take().upper()
        if not CELL.fullmatch(endpoint):
            raise FormulaError("range endpoints must be cell references")
        self.dependencies.update(ranges(name, endpoint))
        return ("range", name, endpoint)

    def expect(self, expected: str) -> None:
        if self.take() != expected:
            raise FormulaError(f"expected {expected}")


def formula_tree(source: str) -> tuple[tuple, set[str]]:
    return Parser(source[1:]).parse()


def ranges(start: str, end: str) -> list[str]:
    row_a, col_a = cell_address(start)
    row_b, col_b = cell_address(end)
    top, bottom = sorted((row_a, row_b))
    left, right = sorted((col_a, col_b))
    cells = []
    for row in range(top, bottom + 1):
        for col in range(left, right + 1):
            cells.append(f"{column_label(col + 1)}{row + 1}")
    return cells


def column_label(number: int) -> str:
    label = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(65 + remainder) + label
    return label


def flatten(values: list[Any]) -> list[Any]:
    result = []
    for value in values:
        result.extend(value if isinstance(value, list) else [value])
    return result


def number(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise FormulaError(f"not a number: {value}") from error


def evaluate_tree(tree: tuple, workbook: "Workbook") -> Any:
    kind = tree[0]
    if kind == "number" or kind == "boolean":
        return tree[1]
    if kind == "cell":
        return workbook.value(tree[1])
    if kind == "range":
        return [workbook.value(cell) for cell in ranges(tree[1], tree[2])]
    if kind == "unary":
        value = number(evaluate_tree(tree[2], workbook))
        return value if tree[1] == "+" else -value
    if kind == "binary":
        return evaluate_binary(tree, workbook)
    if kind == "call":
        return evaluate_call(tree, workbook)
    raise FormulaError("invalid expression")


def evaluate_binary(tree: tuple, workbook: "Workbook") -> Any:
    left = evaluate_tree(tree[2], workbook)
    right = evaluate_tree(tree[3], workbook)
    operator = tree[1]
    if operator in ("=", "<>", "<", ">", "<=", ">="):
        return {"=": lambda: left == right, "<>": lambda: left != right, "<": lambda: left < right, ">": lambda: left > right, "<=": lambda: left <= right, ">=": lambda: left >= right}[operator]()
    a, b = number(left), number(right)
    if operator == "+":
        return a + b
    if operator == "-":
        return a - b
    if operator == "*":
        return a * b
    if operator == "/":
        if b == 0:
            raise FormulaError("division by zero")
        return a / b
    if operator == "^":
        return a**b
    raise FormulaError(f"unknown operator: {operator}")


def evaluate_call(tree: tuple, workbook: "Workbook") -> Any:
    name, arguments = tree[1], tree[2]
    if name == "IF":
        if len(arguments) not in (2, 3):
            raise FormulaError("IF expects 2 or 3 arguments")
        branch = 1 if evaluate_tree(arguments[0], workbook) else 2
        return evaluate_tree(arguments[branch], workbook) if branch < len(arguments) else False
    values = flatten([evaluate_tree(arg, workbook) for arg in arguments])
    functions = {"SUM": sum_values, "AVERAGE": average_values, "MIN": minimum_value, "MAX": maximum_value, "COUNT": count_values}
    function = functions.get(name)
    if function is None:
        raise FormulaError(f"unknown function: {name}")
    return function(values)


def sum_values(values: list[Any]) -> float:
    return sum(number(value) for value in values)


def average_values(values: list[Any]) -> float:
    numbers = [number(value) for value in values if value not in (None, "")]
    if not numbers:
        raise FormulaError("AVERAGE has no values")
    return sum(numbers) / len(numbers)


def minimum_value(values: list[Any]) -> float:
    return min((number(value) for value in values), default=0)


def maximum_value(values: list[Any]) -> float:
    return max((number(value) for value in values), default=0)


def count_values(values: list[Any]) -> int:
    return sum(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values)


def dependency_map(cells: dict[str, "Cell"]) -> dict[str, set[str]]:
    formulas = filter(lambda item: item[1].tree, cells.items())
    return {address: set(cell.dependencies or ()) for address, cell in formulas}


@dataclass
class Cell:
    raw: str
    tree: tuple | None = None
    dependencies: set[str] | None = None


class Workbook:
    """Grid of source strings with lazy formula evaluation and cycle reporting."""

    def __init__(self, rows: list[list[str]]):
        self.cells: dict[str, Cell] = {}
        self.cache: dict[str, Any] = {}
        self.active: set[str] = set()
        for row_index, row in enumerate(rows):
            for col_index, value in enumerate(row):
                address = self.address(row_index, col_index)
                self.cells[address] = self.parse_cell(address, value)

    def parse_cell(self, address: str, value: str) -> Cell:
        cell = Cell(value)
        if not value.startswith("="):
            return cell
        try:
            cell.tree, cell.dependencies = formula_tree(value)
        except FormulaError as error:
            self.cache[address] = f"#PARSE! {error}"
        return cell

    @staticmethod
    def address(row: int, col: int) -> str:
        label, value = "", col + 1
        while value:
            value, remainder = divmod(value - 1, 26)
            label = chr(65 + remainder) + label
        return f"{label}{row + 1}"

    @property
    def dependencies(self) -> dict[str, set[str]]:
        return dependency_map(self.cells)

    def value(self, address: str) -> Any:
        key = address.upper()
        if key in self.cache:
            return self.cache[key]
        if key in self.active:
            raise FormulaError("circular reference")
        cell = self.cells.get(key)
        if cell is None:
            return 0
        if not cell.raw.startswith("="):
            return self.parse_literal(cell.raw)
        if cell.tree is None:
            return self.cache.get(key, "#PARSE! invalid formula")
        self.active.add(key)
        try:
            value = evaluate_tree(cell.tree, self)
            self.cache[key] = value
            return value
        except FormulaError as error:
            message = f"#CYCLE! {error}" if "circular" in str(error) else f"#VALUE! {error}"
            self.cache[key] = message
            return message
        finally:
            self.active.remove(key)

    @staticmethod
    def parse_literal(value: str) -> Any:
        if not value:
            return ""
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value

    def values(self) -> list[list[str]]:
        if not self.cells:
            return []
        positions = {cell_address(address) for address in self.cells}
        rows = max(row for row, _ in positions) + 1
        cols = max(col for _, col in positions) + 1
        return [[self.display(self.value(self.address(row, col))) for col in range(cols)] for row in range(rows)]

    @staticmethod
    def display(value: Any) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)


def read_csv(path: Path) -> list[list[str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        return [row for row in csv.reader(source)]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as target:
        csv.writer(target).writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate formulas in a CSV spreadsheet")
    parser.add_argument("input", type=Path, help="input CSV containing values and formulas")
    parser.add_argument("-o", "--output", type=Path, help="output CSV (defaults to stdout)")
    parser.add_argument("--dependencies", action="store_true", help="print formula dependency graph as JSON")
    args = parser.parse_args(argv)
    try:
        workbook = Workbook(read_csv(args.input))
        if args.dependencies:
            import json
            rendered = json.dumps({key: sorted(value) for key, value in workbook.dependencies.items()}, indent=2)
            print(rendered)
            return 0
        result = workbook.values()
        if args.output:
            write_csv(args.output, result)
        else:
            csv.writer(sys.stdout).writerows(result)
        return 0
    except (OSError, csv.Error) as error:
        print(f"sheet: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
