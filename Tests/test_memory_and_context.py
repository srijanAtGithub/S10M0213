import numpy as np


# memory_and_context.py — pure-logic checks
# (functions copied verbatim — see module docstring note at top of file)
def _parse_preference_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()

        if not line:
            continue
        if line.startswith("#"):
            continue
        if set(line) <= set("-_* "):
            continue

        for marker in ("-", "*", "•", "–"):
            if line.startswith(marker):
                line = line[len(marker):].strip()
                break

        if line:
            lines.append(line)

    return lines


# --- copied verbatim from memory_and_context.py ---
def _cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q     = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    return (matrix / norms) @ q


def test_parse_preference_lines_strips_markers_and_skips_noise():
    text = """
# Header — should be skipped
Plain preference line
- Hyphen bullet
* Star bullet
• Round bullet
- En-dash bullet
---

Another plain line
""".strip()

    result = _parse_preference_lines(text)

    assert result == [
        "Plain preference line",
        "Hyphen bullet",
        "Star bullet",
        "Round bullet",
        "En-dash bullet",
        "Another plain line",
    ]


def test_parse_preference_lines_empty_input_returns_empty_list():
    assert _parse_preference_lines("") == []
    assert _parse_preference_lines("   \n\n   ") == []


def test_cosine_similarity_known_values():
    query  = np.array([1.0, 0.0])
    matrix = np.array([
        [1.0, 0.0],   # identical direction -> ~1.0
        [0.0, 1.0],   # orthogonal          -> ~0.0
        [1.0, 1.0],   # 45 degrees          -> ~0.7071
    ])

    scores = _cosine_similarity(query, matrix)

    assert np.isclose(scores[0], 1.0, atol=1e-6)
    assert np.isclose(scores[1], 0.0, atol=1e-6)
    assert np.isclose(scores[2], 1 / np.sqrt(2), atol=1e-6)