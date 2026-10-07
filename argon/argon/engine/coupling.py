"""Coupling hot spots: files that many others lean on, or that lean on many others (prototype).

Two kinds of edge, both between scanned files:
  import edges      from resolved `import` / `require` / Python imports (engine/imports.py)
  namespace edges   from a shared global bag such as `Racer.world = {...}` in one file and
                    `Racer.world.create()` in another, with the load order held together by
                    <script> tags. An import-only view sees none of these.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping

Member = tuple[str, str]  # (namespace, member), e.g. ("Racer", "world")


def namespace_edges(
    defs: Mapping[str, Collection[Member]],
    refs: Mapping[str, Collection[Member]],
    min_files: int,
) -> set[tuple[str, str]]:
    """(user file, providing file) edges for shared-global members.

    A name is a shared namespace when at least `min_files` files assign members on it at top
    level. A reference `Namespace.member` then points at every file that assigns that member.
    """
    providers = _providers(defs)
    shared = _shared_namespaces(defs, min_files)
    edges: set[tuple[str, str]] = set()
    for user, members in refs.items():
        keys = [key for key in members if key[0] in shared]
        edges.update((user, provider) for key in keys for provider in providers.get(key, ()) if provider != user)
    return edges


def _providers(defs: Mapping[str, Collection[Member]]) -> dict[Member, set[str]]:
    """Which files assign each `Namespace.member`."""
    providers: dict[Member, set[str]] = {}
    for filename, members in defs.items():
        for key in members:
            providers.setdefault(key, set()).add(filename)
    return providers


def _shared_namespaces(defs: Mapping[str, Collection[Member]], min_files: int) -> set[str]:
    """Namespaces that at least `min_files` different files assign members on."""
    files_per_namespace: dict[str, set[str]] = {}
    for filename, members in defs.items():
        for namespace, _ in members:
            files_per_namespace.setdefault(namespace, set()).add(filename)
    return {name for name, files in files_per_namespace.items() if len(files) >= min_files}


def coupling_weights(files: Iterable[str], *edge_sets: Collection[tuple[str, str]]) -> dict[str, int]:
    """Per file: distinct files it depends on plus distinct files that depend on it."""
    names = set(files)
    links = _links(names, edge_sets)
    out, into = Counter(source for source, _ in links), Counter(target for _, target in links)
    return {name: out[name] + into[name] for name in names}


def _links(names: set[str], edge_sets: Iterable[Collection[tuple[str, str]]]) -> set[tuple[str, str]]:
    """Distinct edges between two different scanned files, whichever edge set they came from."""
    links: set[tuple[str, str]] = set()
    for edges in edge_sets:
        links.update(edge for edge in edges if edge[0] != edge[1] and edge[0] in names and edge[1] in names)
    return links
