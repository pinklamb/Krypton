# Mini spreadsheet engine (Argon build)

`sheet_engine.py` implements a CSV backed spreadsheet with cell references, ranges, arithmetic and comparisons, `SUM`, `AVERAGE`, `MIN`, `MAX`, `COUNT`, and lazy `IF`. Formula dependencies are exposed as a graph. Circular references and formula errors render as cell error strings.

Run from this folder:

```bash
python sheet_engine.py input.csv -o values.csv
python sheet_engine.py input.csv --dependencies
```

The dependency graph reports direct references (range dependencies are expanded during evaluation). The Argon session output and history are retained in `logs/`.
