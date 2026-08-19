# AGENTS.md

When you need to call tools from the shell, **use this rubric**: 

- Find Files: `fd`
- Find Text: `rg` (ripgrep)
- Find Code Structure (TS/TSX): `ast-grep`
  - **Default to TypeScript:**  
    - `.ts` → `ast-grep --lang ts -p '<pattern>'`  
    - `.tsx` (React) → `ast-grep --lang tsx -p '<pattern>'`
  - For other languages, set `--lang` appropriately (e.g., `--lang rust`).
- Select among matches: pipe to `fzf`
- JSON: `jq`
- YAML/XML: `yq`

If ast-grep is available avoid tools `rg` or `grep` unless a plain‑text search is explicitly requested.

&nbsp;

There is already a JSON encoder/decoder at the level of asyncpg in db.py. So don't try to come up with your own json encoding when you need to persist objects in the db.