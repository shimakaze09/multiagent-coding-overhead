# Extending the renderers

Subclass `HtmlRenderer` (or `TextRenderer`) and override `visit_<kind>(self,
node)` for the node kinds you want to change. Use `self.render_children(node)`
for the rendered children and `self.options` for the options. Pass the class
to `docpipe.render(source, fmt, options, renderer=MyRenderer)`.

```python
class Admonitions(HtmlRenderer):
    def visit_quote(self, node):
        return f'<aside class="note">{self.render_children(node)}</aside>'
```

## Deprecated: rule functions (docpipe 1.x)

`register_html(kind, fn)` / `register_text(kind, fn)` still work and emit a
`DeprecationWarning`. `fn(node, ctx)` returns a string; `ctx.render_children(node)`
and `ctx.options` are available. A registered rule applies to every renderer,
except that a subclass's own `visit_<kind>` method takes precedence over it.
`unregister_html(kind)` / `unregister_text(kind)` remove a rule. docpipe itself
never uses the deprecated functions.

## Caching

`render(..., cache=RenderCache())` reuses earlier output only when the source,
format, every option, the renderer class and the registered rules are all
the same.
