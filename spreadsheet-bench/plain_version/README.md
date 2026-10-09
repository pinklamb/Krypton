# Plain Python mini spreadsheet

This implementation is intentionally standalone and does not import Argon. It provides a recursive-descent formula parser, formula dependency graph, DFS cycle reporting, lazy cell evaluation with memoization, CSV input/output, and a CLI.

## Run

```sh
python mini_sheet.py input.csv --cell C1 --cycles -o values.csv
```

CSV coordinates start at `A1`; a cell beginning with `=` is a formula. Supported syntax includes numeric and quoted text literals, cell references and rectangular ranges, `+ - * / ^`, comparisons, parentheses, and function calls. Functions: `SUM`, `AVERAGE`, `MIN`, `MAX`, `COUNT`, `IF`, `AND`, `OR`, and `NOT`. Blank cells evaluate to zero. Circular references and formula errors are reported in evaluated CSV output as `#ERROR: ...`.

Use `--show-formulas` with `-o` to preserve formulas instead of writing computed results. `--cell` may be repeated to print individual values; `--cycles` prints the cycles found in the dependency graph.
