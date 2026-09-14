"""Componentes de carregamento do Dashboard de Mestrado e Doutorado."""
from dash import dcc, html


def educ_spinner():
    return html.Div(
        [
            html.Div(className="educ-spinner__ring"),
            html.P("Carregando...")
        ],
        className="educ-spinner",
        role="status",
        **{"aria-label": "Carregando dados do painel"},
    )


def educ_page_loading(children, **kwargs):
    try:
        import inspect
        params = inspect.signature(dcc.Loading).parameters
        loading_kwargs = {}
        if "custom_spinner" in params:
            loading_kwargs["custom_spinner"] = educ_spinner()
        if "overlay_style" in params:
            loading_kwargs["overlay_style"] = {"visibility": "visible"}
        if "delay_show" in params:
            loading_kwargs["delay_show"] = 0
        if "delay_hide" in params:
            loading_kwargs["delay_hide"] = 500
        if not loading_kwargs.get("custom_spinner"):
            loading_kwargs["type"] = "default"
            loading_kwargs["color"] = "#0f4c81"
        return dcc.Loading(children, **loading_kwargs, **kwargs)
    except Exception:
        return children
