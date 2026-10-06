"""Where each spec item lives and which tests prove it. The build checks every reference still exists."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpecRow:
    ident: str
    title: str
    status: str  # built | amended | switch | phase1
    where: str  # module(s) that implement it
    tests: tuple[str, ...]


ROWS = (
    SpecRow("A1", "Return padding", "built", "engine/collectors.py, engine/ast_counters.py",
            ("test_a1_unreachable_returns_add_nothing", "test_a1_reworded_returns_count_once", "test_a1_outputs_capped_at_branches_plus_one")),
    SpecRow("A2", "Optional calls (?.) are free", "switch", "engine/branches.py (count_optional_calls, default off)",
            ("test_a2_optional_calls_switch",)),
    SpecRow("A3", "Self-stamped @generated markers", "built", "engine/run.py",
            ("test_a3_generated_marker_does_not_skip", "test_a3_generated_path_is_skipped")),
    SpecRow("A4", "Line packing", "amended", "engine/lines.py, engine/collectors.py",
            ("test_a4_packing_keeps_effective_lines", "test_a4_packed_statements_count_each", "test_a4_ordinary_lines_count_once",
             "test_a4_long_lines_count_by_width", "test_a4_comments_and_docstrings_are_not_effective")),
    SpecRow("A5", "Ratio dilution", "built", "engine/rules.py", ("test_a5_dilution_is_weighted",)),
    SpecRow("A6", "Closures reset depth", "built", "engine/scopes.py", ("test_nested_flag_fires_through_closures", "test_spec_fixture")),
    SpecRow("A7", "Short-circuit control flow", "built", "engine/branches.py, engine/nesting.py", ("test_spec_fixture",)),
    SpecRow("A8", "Imports the counter can't see", "built", "engine/imports.py, cli.py (tsconfig)",
            ("test_a8_python_absolute_imports", "test_a8_python_submodule_import", "test_a8_ts_alias", "test_a8_ts_base_url", "test_tsconfig_aliases_are_discovered")),
    SpecRow("A9", "Fragmentation dilution", "built", "engine/rules.py", ("test_a9_isolated_files_do_not_dilute_fragmentation",)),
    SpecRow("A10", "Test-name camouflage", "built", "engine/config.py, engine/ast_counters.py", ("test_test_asserts_count_outside_test_paths",)),
    SpecRow("A11", "Nesting gates nothing", "built", "engine/run.py (rating_nesting), calibration.py", ("test_fit_thresholds_writes_calibrated_module",)),
    SpecRow("B1", "File-level branches squared", "built", "engine/run.py (per-function ratings)", ("test_per_function_scores",)),
    SpecRow("B2", "Overshoot saturates", "built", "engine/rules.py", ("test_overshoot_caps_at_thirty",)),
    SpecRow("B3", "Tax overflow", "built", "engine/scopes.py", ("test_deep_callbacks_saturate",)),
    SpecRow("B4", "Depth off-by-one", "built", "engine/ast_counters.py", ("test_spec_fixture",)),
    SpecRow("B5", "Base complexity unstated", "built", "engine/scopes.py (no +1 base)", ("test_stdlib_sample_matches_radon",)),
    SpecRow("B6", "Banker's rounding", "built", "engine/rules.py", ("test_round_half_up",)),
    SpecRow("B7", "Generators score as single-output", "built", "engine/collectors.py", ("test_a1_yield_counts_as_output",)),
    SpecRow("B8", "Divide-by-zero and empty runs", "built", "engine/rules.py", ("test_no_findings_scores_100", "test_empty_run_is_success")),
    SpecRow("B9", "Tests explode the count", "built", "engine/ast_counters.py", ("test_test_asserts_count_outside_test_paths",)),
    SpecRow("B10", "One node kind, two meanings", "built", "engine/branches.py", ("test_b10_match_guard_is_not_counted_but_comprehension_filter_is",)),
    SpecRow("C1", "Missing-node blindness", "built", "engine/languages.py", ("test_c1_missing_nodes_are_errors",)),
    SpecRow("C2", "Error recovery hides code", "built", "engine/languages.py, engine/run.py",
            ("test_c2_fail_closed_above_error_limit", "test_c2_small_errors_are_scored_with_a_warning_flag")),
    SpecRow("C3", "Non-UTF-8 crash", "built", "engine/languages.py", ("test_c3_bad_bytes_do_not_raise",)),
    SpecRow("C4", "Huge files", "built", "engine/run.py", ("test_c4_oversize_is_flagged_not_parsed",)),
    SpecRow("C5", "Symlink loops", "built", "cli.py", ("test_symlink_leaving_the_root_is_dropped",)),
    SpecRow("D1-D4", "Phase 1 infrastructure (webhook, queue, poster, DB)", "phase1", "not built in the MVP", ()),
)
