# Extending the renderers

Each renderer is a table of rule functions, one per node kind. Add or replace
a rule with `register_html(kind, fn)` / `register_text(kind, fn)`: `fn(node,
ctx)` returns a string, and `ctx.render(node)`, `ctx.render_children(node)` and
`ctx.options` are available. `unregister_html(kind)` / `unregister_text(kind)`
restore the built-in rule. Rules are global: they apply to every render.

```python
from docpipe import register_html

register_html("quote", lambda node, ctx: f'<aside class="note">{ctx.render_children(node)}</aside>')
```

## Caching

`render(..., cache=RenderCache())` reuses earlier output for the same source,
format and options.
