# Copilot Fix Prompt (`build_fix_prompt` in `src/healing/copilot_fixer.py`)

The exact instruction sent to GitHub Copilot for every broken file,
extracted from the code so you can review or tune it without reading Python.

---

## The template

```text
The following Python file has errors. Fix ALL errors and return the
complete corrected file content. Return ONLY the file content — no markdown
fences, no explanation, no commentary.

File: {file_path}
Error at line {error['line']}: {error['message']}

Current file content:
```python
{file_content}
```

Return the complete corrected file content.
```

## Why each line is there

| Line | Purpose |
|---|---|
| `Fix ALL errors` | One pass per file; no partial fixes |
| `Return ONLY the file content ... no commentary` | Makes the reply safe to write straight to disk |
| `no markdown fences` | Avoids ```` ```python ```` wrappers (a fallback strips them anyway) |
| `File:` + `Error at line:` | Tells the model exactly where the detector fired |
| Full current content | The model rewrites the whole file, not a fragment |

## Rules for changing this prompt

1. **Never add example fixes** (e.g. showing a broken line plus its fixed
   version). Examples bias the model into narrow copy-paste repairs instead
   of fixing what is actually wrong.
2. Keep the "return only content" contract — downstream code
   (`accept_fix`) assumes raw Python.
3. If you change what counts as "fixed", keep `accept_fix` in sync
   (it re-verifies with `ast.parse` for `.py` or `terraform validate` for `.tf`).
