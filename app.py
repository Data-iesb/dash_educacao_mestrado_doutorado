"""Entry-point WSGI — Dashboard de Mestrado e Doutorado."""
try:
    from .dash_educacao_mestrado_doutorado import server  # noqa: F401
except ImportError:
    from dash_educacao_mestrado_doutorado import server  # noqa: F401

if __name__ == "__main__":
    try:
        from .dash_educacao_mestrado_doutorado import app
    except ImportError:
        from dash_educacao_mestrado_doutorado import app
    app.run(debug=False, host="0.0.0.0", port=8050)
