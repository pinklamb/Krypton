"""Coupling hot spots: files that many others lean on, or that lean on many others (prototype).

Two kinds of edge, both between scanned files:
  import edges      from resolved `import` / `require` / Python imports (engine/imports.py)
  namespace edges   from a shared global bag such as `Racer.world = {...}` in one file and
                    `Racer.world.create()` in another, with the load order held together by
                    <script> tags. An import-only view sees none of these.
"""

from __future__ import annotations

from collections import Counter
from itertools import chain
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
        edges |= _edges_of(user, members, shared, providers)
    return edges


def _edges_of(user: str, members: Collection[Member], shared: set[str], providers: Mapping[Member, set[str]]) -> set[tuple[str, str]]:
    """(user, provider) for each shared-namespace member the file reads, except itself."""
    owners = chain.from_iterable(providers.get(key, ()) for key in members if key[0] in shared)
    return {(user, provider) for provider in owners if provider != user}


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
    links = coupling_links(names, edge_sets)
    out, into = Counter(source for source, _ in links), Counter(target for _, target in links)
    return {name: out[name] + into[name] for name in names}


def coupling_links(names: set[str], edge_sets: Iterable[Collection[tuple[str, str]]]) -> set[tuple[str, str]]:
    """Distinct edges between two different scanned files, whichever edge set they came from."""
    candidates = chain.from_iterable(edge_sets)
    return {edge for edge in candidates if edge[0] != edge[1] and edge[0] in names and edge[1] in names}


def partners(links: Collection[tuple[str, str]]) -> dict[str, set[str]]:
    """For each file, the files it is linked to in either direction."""
    result: dict[str, set[str]] = {}
    for source, target in links:
        result.setdefault(source, set()).add(target)
        result.setdefault(target, set()).add(source)
    return result

