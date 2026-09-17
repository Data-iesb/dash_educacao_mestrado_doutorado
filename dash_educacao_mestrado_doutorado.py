"""Dashboard de Mestrado e Doutorado (CAPES) — versão completa.

Abas: Panorama Geral, Explorador de Programas,
Mapa & Conceito CAPES, Ranking.
"""

import os
import json
import math
import time

import dash
from dash import Input, Output, State, dcc, html, callback_context, ALL
import pandas as pd
import plotly.graph_objects as go
import requests
import trino

try:
    from .loading_components import educ_page_loading
except ImportError:
    from loading_components import educ_page_loading

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO / CONEXÃO
# ═══════════════════════════════════════════════════════════════════════════

TRINO_HOST = os.getenv("TRINO_HOST") or "trino.dataiesb.com"
TRINO_PORT = int(os.getenv("TRINO_PORT") or "443")
TRINO_USER = os.getenv("TRINO_USER") or "admin"
TRINO_PASSWORD = os.getenv("TRINO_PASSWORD")
TRINO_CATALOG = os.getenv("TRINO_CATALOG") or "seaweedfs"
TRINO_SCHEMA = os.getenv("TRINO_SCHEMA") or "raw"
TBL_PROGRAMAS = os.getenv("TBL_PROGRAMAS") or "seaweedfs.raw.capes_sucupira_programas_pos"

# Modo de conexão:
#  - Se TRINO_PASSWORD estiver definido → Trino público (HTTPS + BasicAuth).
#  - Caso contrário → Trino in-cluster (HTTP, sem auth), padrão no EKS
#    (trino.trino.svc.cluster.local:8080). TRINO_HTTP_SCHEME pode forçar o scheme.
TRINO_HTTP_SCHEME = os.getenv("TRINO_HTTP_SCHEME") or ("https" if TRINO_PASSWORD else "http")


def _trino_query(sql: str, max_retries: int = 3) -> pd.DataFrame:
    for tentativa in range(1, max_retries + 1):
        try:
            conn_kwargs = dict(
                host=TRINO_HOST,
                port=TRINO_PORT,
                user=TRINO_USER,
                catalog=TRINO_CATALOG,
                schema=TRINO_SCHEMA,
                http_scheme=TRINO_HTTP_SCHEME,
            )
            if TRINO_PASSWORD:
                conn_kwargs["auth"] = trino.auth.BasicAuthentication(TRINO_USER, TRINO_PASSWORD)
            conn = trino.dbapi.connect(**conn_kwargs)
            cur = conn.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            conn.close()
            return pd.DataFrame(rows, columns=cols)
        except Exception as e:
            print(f"[TRINO] Tentativa {tentativa}/{max_retries} falhou: {e}", flush=True)
            if tentativa == max_retries:
                raise
            time.sleep(5 * tentativa)


def _clean_text(series):
    if series.empty:
        return series
    return series.fillna("").astype(str).str.strip()


def _load_programas() -> pd.DataFrame:
    query = f"""
        SELECT
            CAST(an_base AS VARCHAR) AS an_base,
            TRIM(nm_grande_area_conhecimento) AS nm_grande_area_conhecimento,
            TRIM(nm_area_conhecimento) AS nm_area_conhecimento,
            TRIM(nm_area_basica) AS nm_area_basica,
            TRIM(nm_subarea_conhecimento) AS nm_subarea_conhecimento,
            TRIM(nm_especialidade) AS nm_especialidade,
            TRIM(nm_area_avaliacao) AS nm_area_avaliacao,
            TRIM(nm_entidade_ensino) AS nm_entidade_ensino,
            TRIM(sg_entidade_ensino) AS sg_entidade_ensino,
            TRIM(nm_regiao) AS nm_regiao,
            TRIM(sg_uf_programa) AS sg_uf_programa,
            TRIM(nm_municipio_programa_ies) AS nm_municipio_programa_ies,
            TRIM(nm_modalidade_programa) AS nm_modalidade_programa,
            TRIM(nm_programa_ies) AS nm_programa_ies,
            TRIM(cd_programa_ies) AS cd_programa_ies,
            TRIM(nm_grau_programa) AS nm_grau_programa,
            TRIM(cd_conceito_programa) AS cd_conceito_programa,
            TRIM(ds_situacao_programa) AS ds_situacao_programa,
            TRIM(ds_dependencia_administrativa) AS ds_dependencia_administrativa,
            TRIM(ds_organizacao_academica) AS ds_organizacao_academica,
            TRIM(in_rede) AS in_rede,
            TRIM(sg_entidade_ensino_rede) AS sg_entidade_ensino_rede
        FROM {TBL_PROGRAMAS}
    """
    df = _trino_query(query)
    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = _clean_text(df[col])
    df["an_base"] = pd.to_numeric(df["an_base"], errors="coerce").fillna(0).astype(int)
    df["cd_conceito_programa"] = pd.to_numeric(df["cd_conceito_programa"], errors="coerce")
    return df


def _fmt_mil(value):
    try:
        return f"{int(float(value)):,}".replace(",", ".")
    except Exception:
        return "0"


def _fmt_pct(value, casas=1):
    try:
        return f"{float(value):.{casas}f}%".replace(".", ",")
    except Exception:
        return "0%"


def _fmt_conceito(value):
    try:
        if pd.isna(value):
            return "-"
        v = float(value)
        return str(int(v)) if v == int(v) else f"{v:.1f}"
    except Exception:
        return "-"


# ═══════════════════════════════════════════════════════════════════════════
# CARREGAMENTO DE DADOS (uma vez, em memória)
# ═══════════════════════════════════════════════════════════════════════════

print("[CAPES] Carregando programas de pós-graduação...", flush=True)
_t0 = time.time()
df_programas = _load_programas()
print(f"[CAPES] {len(df_programas)} linhas carregadas em {time.time()-_t0:.0f}s", flush=True)

ANOS_DISPONIVEIS = sorted(df_programas["an_base"].dropna().unique().tolist())
ANO_PADRAO = ANOS_DISPONIVEIS[-1] if ANOS_DISPONIVEIS else 2024
ANO_ANTERIOR = ANOS_DISPONIVEIS[-2] if len(ANOS_DISPONIVEIS) > 1 else None

REGIOES = ["Todas"] + sorted(df_programas["nm_regiao"].dropna().unique().tolist())
UFS = ["Todas"] + sorted(df_programas["sg_uf_programa"].dropna().unique().tolist())
MODALIDADES = ["Todas"] + sorted(df_programas["nm_modalidade_programa"].dropna().unique().tolist())
GRAUS = ["Todos"] + sorted(df_programas["nm_grau_programa"].dropna().unique().tolist())
AREAS = ["Todas"] + sorted(df_programas["nm_grande_area_conhecimento"].dropna().unique().tolist())
SITUACOES = ["Todas"] + sorted(df_programas["ds_situacao_programa"].dropna().unique().tolist())

# ── Mapeamento sigla UF → código IBGE (para o mapa coroplético) ─────────────
UF_PARA_CODIGO_IBGE = {
    "RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16", "TO": "17",
    "MA": "21", "PI": "22", "CE": "23", "RN": "24", "PB": "25", "PE": "26", "AL": "27",
    "SE": "28", "BA": "29", "MG": "31", "ES": "32", "RJ": "33", "SP": "35",
    "PR": "41", "SC": "42", "RS": "43", "MS": "50", "MT": "51", "GO": "52", "DF": "53",
}

# ── GeoJSON dos estados do Brasil (com cache local) ─────────────────────────
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
_UF_GEOJSON_CACHE = os.path.join(_CACHE_DIR, "brazil_uf.geojson")
_UF_GEOJSON_URLS = [
    "https://raw.githubusercontent.com/codeforamerica/click_that_hood/master/public/data/brazil-states.geojson",
    "https://raw.githubusercontent.com/tbrugz/geodata/master/geojson/br_states.geojson",
    "https://servicodados.ibge.gov.br/api/v3/malhas/BR?formato=application/vnd.geo+json&qualidade=minima&resolucao=2",
]

try:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    if os.path.exists(_UF_GEOJSON_CACHE):
        with open(_UF_GEOJSON_CACHE, encoding="utf-8") as _f:
            geojson_ufs = json.load(_f)
        print(f"[CAPES] GeoJSON de UFs carregado do cache: {len(geojson_ufs.get('features', []))} estados", flush=True)
    else:
        geojson_ufs = {"type": "FeatureCollection", "features": []}
        _ultimo_erro = None
        for _url in _UF_GEOJSON_URLS:
            try:
                _resp = requests.get(_url, timeout=60)
                _resp.raise_for_status()
                _dados = _resp.json()
                if _dados.get("features"):
                    geojson_ufs = _dados
                    with open(_UF_GEOJSON_CACHE, "w", encoding="utf-8") as _f:
                        json.dump(geojson_ufs, _f)
                    print(f"[CAPES] GeoJSON de UFs baixado: {len(geojson_ufs['features'])} estados", flush=True)
                    break
            except Exception as _e:
                _ultimo_erro = _e
                continue
        else:
            print(f"[CAPES] Aviso: nenhuma URL de GeoJSON de UFs funcionou (último erro: {_ultimo_erro}). "
                  "O mapa terá fallback em barras.", flush=True)
except Exception as _e:
    print(f"[CAPES] Aviso: não foi possível carregar GeoJSON de UFs ({_e}). O mapa terá fallback em barras.", flush=True)
    geojson_ufs = {"type": "FeatureCollection", "features": []}

# ═══════════════════════════════════════════════════════════════════════════
# IDENTIDADE VISUAL
# ═══════════════════════════════════════════════════════════════════════════

COR_HEADER = "#0F2F4C"
COR_HEADER_2 = "#1B4C78"
COR_AZUL = "#2F6FAD"
COR_AZUL_CLARO = "#5B9BD5"
COR_VERDE = "#2F8159"
COR_ROXO = "#6A3DB8"
COR_LARANJA = "#D97706"
COR_DOURADO = "#B7862C"
COR_VERMELHO = "#C0392B"
COR_CINZA = "#4B5563"
COR_FUNDO = "#F3F6F9"
COR_BORDA = "#E2E8F0"

CORES_REGIAO = {
    "Norte": "#2F8159", "Nordeste": "#D97706", "Centro-Oeste": "#B7862C",
    "Sudeste": "#2F6FAD", "Sul": "#6A3DB8",
}

# Escala de cor por conceito CAPES (3 a 7)
_CORES_CONCEITO = {3: COR_LARANJA, 4: COR_AZUL, 5: COR_VERDE, 6: COR_ROXO, 7: COR_DOURADO}


def _cor_conceito(valor):
    try:
        v = int(round(float(valor)))
    except Exception:
        return COR_CINZA
    return _CORES_CONCEITO.get(v, COR_CINZA)


PAGE_SIZE_EXPLORADOR = 20


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS DE UI
# ═══════════════════════════════════════════════════════════════════════════

def _layout_base():
    return dict(
        margin=dict(l=40, r=20, t=10, b=40),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        font=dict(family="Inter, sans-serif", size=12, color="#1f2937"),
        xaxis=dict(showgrid=False, linecolor="#dfe7ee"),
        yaxis=dict(gridcolor="#edf2f7", linecolor="#dfe7ee"),
        hoverlabel=dict(bgcolor="#fff", bordercolor="#cbd5e1", font_size=12, font_color="#111827"),
        legend=dict(orientation="h", y=-0.18),
    )


def _kpi(valor, label, cor, icone=None, delta=None):
    """Card de KPI, com ícone e variação percentual opcional (Task de Panorama)."""
    filhos = [
        html.Div([
            html.Span(icone, style={"fontSize": 20, "marginRight": 8}) if icone else None,
            html.Span(valor, style={"fontSize": 28, "fontWeight": 700}),
        ], style={"color": "#fff", "display": "flex", "alignItems": "center"}),
        html.Div(label, style={
            "fontSize": 11, "fontWeight": 600, "color": "#ffffffcc",
            "textTransform": "uppercase", "letterSpacing": "0.05em", "marginTop": 2,
        }),
    ]
    if delta is not None:
        seta = "▲" if delta >= 0 else "▼"
        cor_delta = "#BBF7D0" if delta >= 0 else "#FECACA"
        filhos.append(html.Div(
            f"{seta} {abs(delta):.1f}% vs {ANO_ANTERIOR}",
            style={"fontSize": 11, "fontWeight": 600, "color": cor_delta, "marginTop": 4},
        ))
    return html.Div(
        filhos,
        className="kpi-card",
        style={"backgroundColor": cor, "borderRadius": 10, "padding": "16px 20px", "flex": 1, "minWidth": 170},
    )


def _card(children, shadow=False, titulo=None, icone=None, class_name=""):
    corpo = []
    if titulo:
        corpo.append(html.Div([
            html.Span(icone, style={"marginRight": 8}) if icone else None,
            html.Span(titulo, style={"fontSize": 13, "fontWeight": 700, "color": "#374151"}),
        ], style={"marginBottom": 10, "display": "flex", "alignItems": "center"}))
    corpo.extend(children if isinstance(children, list) else [children])
    style = {
        "backgroundColor": "#fff", "borderRadius": 10, "padding": "20px 24px",
        "border": "1px solid " + COR_BORDA, "flex": 1,
    }
    if shadow:
        style["boxShadow"] = "0 2px 10px rgba(15,23,42,0.06)"
        style["border"] = "none"
    classes = " ".join(filter(None, ["card-hover", class_name]))
    return html.Div(corpo, className=classes, style=style)


def _titulo(texto):
    return html.P(texto, style={"fontSize": 13, "fontWeight": 600, "color": "#374151", "margin": "0 0 8px 0"})


def _nota(texto, cor="#718096"):
    return html.P(texto, style={"fontSize": 11, "color": cor, "fontStyle": "italic", "margin": "4px 0 0 0"})


def _badge(texto, cor_bg, cor_fg="#fff"):
    return html.Span(texto, style={
        "backgroundColor": cor_bg, "color": cor_fg, "borderRadius": 999,
        "padding": "3px 10px", "fontSize": 11, "fontWeight": 600, "whiteSpace": "nowrap",
        "display": "inline-block",
    })


def _badge_situacao(situacao):
    s = (situacao or "").upper()
    if "FUNCIONAMENTO" in s:
        return _badge("Em Funcionamento", COR_VERDE)
    if "DESCONTINUAD" in s or "EXTINT" in s:
        return _badge(situacao or "-", COR_VERMELHO)
    return _badge(situacao or "-", COR_CINZA)


def _badge_conceito(valor):
    return _badge(_fmt_conceito(valor), _cor_conceito(valor))


def _filter_label(label, dropdown_id, options, value, width=180, multi=False, clearable=False):
    return html.Div(
        [
            html.Label(label, style={"fontSize": 11, "fontWeight": 600, "color": "#475569", "marginBottom": 4, "display": "block"}),
            dcc.Dropdown(
                id=dropdown_id,
                options=[{"label": o, "value": o} for o in options],
                value=value,
                clearable=clearable,
                multi=multi,
                className="filter-dropdown",
                style={"width": width, "fontSize": 13, "fontFamily": "Inter, sans-serif"},
            ),
        ], style={"position": "relative", "zIndex": 1000})


def _loading(children, tipo="circle"):
    return dcc.Loading(children=children, type=tipo, color=COR_AZUL)


def _aplicar_filtros(df, ano=None, regiao="Todas", uf="Todas", modalidade="Todas",
                      grau="Todos", area="Todas", situacao="Todas", busca=None):
    df2 = df
    if ano is not None:
        df2 = df2[df2["an_base"] == int(ano)]
    if regiao and regiao != "Todas":
        df2 = df2[df2["nm_regiao"] == regiao]
    if uf and uf != "Todas":
        df2 = df2[df2["sg_uf_programa"] == uf]
    if modalidade and modalidade != "Todas":
        df2 = df2[df2["nm_modalidade_programa"] == modalidade]
    if grau and grau != "Todos":
        df2 = df2[df2["nm_grau_programa"] == grau]
    if area and area != "Todas":
        df2 = df2[df2["nm_grande_area_conhecimento"] == area]
    if situacao and situacao != "Todas":
        df2 = df2[df2["ds_situacao_programa"] == situacao]
    if busca:
        termo = busca.strip().lower()
        mask = (
            df2["nm_programa_ies"].str.lower().str.contains(termo, na=False)
            | df2["nm_entidade_ensino"].str.lower().str.contains(termo, na=False)
            | df2["sg_entidade_ensino"].str.lower().str.contains(termo, na=False)
        )
        df2 = df2[mask]
    return df2


def _dedup_programa(df):
    """Uma linha por cd_programa_ies (conceito/situação são atributos do programa,
    não do grau — evita contar/mediar em dobro quando há mestrado + doutorado)."""
    return df.drop_duplicates(subset="cd_programa_ies", keep="first")


# ═══════════════════════════════════════════════════════════════════════════
# ABA 1 — PANORAMA GERAL
# ═══════════════════════════════════════════════════════════════════════════

def _aba_panorama():
    df = _aplicar_filtros(df_programas, ano=ANO_PADRAO)
    dedup = _dedup_programa(df)

    total_programas = dedup["cd_programa_ies"].nunique()
    total_ies = dedup["nm_entidade_ensino"].nunique()
    total_areas = dedup["nm_grande_area_conhecimento"].nunique()
    conceito_medio = dedup["cd_conceito_programa"].mean()

    delta_programas = None
    if ANO_ANTERIOR:
        df_ant = _aplicar_filtros(df_programas, ano=ANO_ANTERIOR)
        n_ant = df_ant["cd_programa_ies"].nunique()
        if n_ant:
            delta_programas = (total_programas - n_ant) / n_ant * 100

    regiao_df = (
        df.groupby("nm_regiao", dropna=False)["cd_programa_ies"]
        .nunique().sort_values(ascending=False).reset_index(name="programas")
    )
    fig_regiao = go.Figure(go.Bar(
        x=regiao_df["programas"], y=regiao_df["nm_regiao"], orientation="h",
        marker_color=[CORES_REGIAO.get(r, COR_AZUL) for r in regiao_df["nm_regiao"]],
        text=regiao_df["programas"], textposition="outside",
        hovertemplate="<b>%{y}</b><br>Programas: %{x:,.0f}<extra></extra>",
    ))
    fig_regiao.update_layout(**_layout_base(), height=260, xaxis_title="Programas", showlegend=False)

    modalidade_df = df.groupby("nm_modalidade_programa", dropna=False)["cd_programa_ies"].nunique().reset_index(name="programas")
    fig_modalidade = go.Figure(go.Pie(
        labels=modalidade_df["nm_modalidade_programa"], values=modalidade_df["programas"], hole=0.55,
        marker_colors=[COR_AZUL, COR_VERDE, COR_ROXO, COR_LARANJA],
        hovertemplate="<b>%{label}</b><br>%{value:,.0f} programas (%{percent})<extra></extra>",
    ))
    fig_modalidade.update_layout(margin=dict(l=0, r=0, t=10, b=10), height=260,
                                  legend=dict(orientation="h", y=-0.15),
                                  annotations=[dict(text=f"{total_programas}<br>programas", showarrow=False, font_size=13)])

    area_df = (
        df.groupby("nm_grande_area_conhecimento", dropna=False)["cd_programa_ies"]
        .nunique().sort_values(ascending=False).reset_index(name="programas").head(8)
    )
    fig_area = go.Figure(go.Bar(
        x=area_df["nm_grande_area_conhecimento"], y=area_df["programas"], marker_color=COR_AZUL,
        hovertemplate="<b>%{x}</b><br>Programas: %{y:,.0f}<extra></extra>",
    ))
    fig_area.update_layout(**_layout_base(), height=260, yaxis_title="Programas")

    conceito_df = dedup["cd_conceito_programa"].dropna()
    conceito_contagem = conceito_df.value_counts().sort_index()
    fig_conceito = go.Figure(go.Bar(
        x=[str(int(c)) for c in conceito_contagem.index], y=conceito_contagem.values,
        marker_color=[_cor_conceito(c) for c in conceito_contagem.index],
        text=conceito_contagem.values, textposition="outside",
        hovertemplate="Conceito <b>%{x}</b><br>Programas: %{y:,.0f}<extra></extra>",
    ))
    fig_conceito.update_layout(**_layout_base(), height=260, xaxis_title="Conceito CAPES", showlegend=False)

    top_area = area_df.iloc[0]["nm_grande_area_conhecimento"] if not area_df.empty else "-"
    top_regiao = regiao_df.iloc[0]["nm_regiao"] if not regiao_df.empty else "-"

    return html.Div([
        html.Div([
            _kpi(_fmt_mil(total_programas), f"Programas ({ANO_PADRAO})", COR_AZUL, "🎓", delta_programas),
            _kpi(_fmt_mil(total_ies), "Instituições", COR_VERDE, "🏛️"),
            _kpi(_fmt_conceito(conceito_medio), "Conceito Médio", COR_DOURADO, "⭐"),
        ], style={"display": "flex", "gap": 12, "flexWrap": "wrap", "marginBottom": 16}),

        html.Div([
            _badge(f"🌎 Região líder: {top_regiao}", COR_HEADER_2),
            _badge(f"🔎 Maior área: {top_area}", COR_HEADER_2),
            _badge(f"📅 {len(ANOS_DISPONIVEIS)} anos de série histórica", COR_HEADER_2),
            _badge(f"{total_areas} grandes áreas do conhecimento", COR_HEADER_2),
        ], style={"display": "flex", "gap": 8, "flexWrap": "wrap", "marginBottom": 20}),

        html.Div([
            _card([_loading(dcc.Graph(figure=fig_regiao, config={"displayModeBar": False}))],
                  shadow=True, titulo="Programas por Região", icone="🌎"),
            _card([_loading(dcc.Graph(figure=fig_modalidade, config={"displayModeBar": False}))],
                  shadow=True, titulo="Distribuição por Modalidade", icone="🧩"),
        ], style={"display": "flex", "gap": 12, "marginBottom": 12}),

        html.Div([
            _card([_loading(dcc.Graph(figure=fig_area, config={"displayModeBar": False}))],
                  shadow=True, titulo="Áreas com mais Programas", icone="📚"),
            _card([_loading(dcc.Graph(figure=fig_conceito, config={"displayModeBar": False}))],
                  shadow=True, titulo="Distribuição de Conceito CAPES", icone="⭐"),
        ], style={"display": "flex", "gap": 12, "marginBottom": 12}),

        html.Div(style={"height": 8}),
        _legenda_conceito(),
    ])


def _legenda_conceito():
    itens = [(3, "Mínimo p/ mestrado"), (4, "Consolidado"), (5, "Referência nacional"),
             (6, "Excelência internacional"), (7, "Excelência internacional (máximo)")]
    return html.Div([
        html.Span("Escala de Conceito CAPES: ", style={"fontSize": 11, "fontWeight": 600, "color": "#64748b"}),
        *[html.Span([_badge(str(n), _cor_conceito(n)), html.Span(f" {desc}   ", style={"fontSize": 11, "color": "#64748b", "marginRight": 10})])
          for n, desc in itens],
    ], style={"padding": "10px 4px", "display": "flex", "flexWrap": "wrap", "alignItems": "center", "gap": 4})


def _atualizar_ufs_por_regiao(regiao, df=None):
    base = df if df is not None else df_programas
    if regiao and regiao != "Todas":
        ufs = sorted(base[base["nm_regiao"] == regiao]["sg_uf_programa"].dropna().unique().tolist())
    else:
        ufs = sorted(base["sg_uf_programa"].dropna().unique().tolist())
    return [{"label": "Todas", "value": "Todas"}] + [{"label": u, "value": u} for u in ufs]


# ═══════════════════════════════════════════════════════════════════════════
# ABA 3 — EXPLORADOR DE PROGRAMAS (com paginação e drill-down)
# ═══════════════════════════════════════════════════════════════════════════

def _aba_explorador_layout():
    return html.Div([
        dcc.Store(id="expl-dados", data=None),
        dcc.Store(id="expl-pagina", data=0),
        dcc.Store(id="expl-sort", data={"col": "conceito", "asc": False}),
        dcc.Store(id="expl-selecionado", data=None),

        _card([
            _titulo("Filtros de Pesquisa"),
            html.Div([
                _filter_label("Ano", "expl-ano", sorted(ANOS_DISPONIVEIS, reverse=True), ANO_PADRAO, 110),
                _filter_label("Região", "expl-regiao", REGIOES, "Todas", 160),
                _filter_label("UF", "expl-uf", UFS, "Todas", 110),
                _filter_label("Modalidade", "expl-modalidade", MODALIDADES, "Todas", 170),
                _filter_label("Grau", "expl-grau", GRAUS, "Todos", 260),
                _filter_label("Grande Área", "expl-area", AREAS, "Todas", 200),
                _filter_label("Situação", "expl-situacao", SITUACOES, "Todas", 170),
                html.Div([
                    html.Label("Buscar (programa / IES)", style={"fontSize": 11, "fontWeight": 600, "color": "#475569", "marginBottom": 4, "display": "block"}),
                    dcc.Input(id="expl-busca", type="text", placeholder="Digite para buscar...", debounce=True,
                              value="", style={"width": 220, "fontSize": 13, "padding": "6px 10px",
                                                "borderRadius": 4, "border": "1px solid #cbd5e0", "boxSizing": "border-box"}),
                ]),
            ], style={"display": "flex", "gap": 14, "flexWrap": "wrap", "alignItems": "flex-end"}),
        ], shadow=True, class_name="filters-card"),

        html.Div(style={"height": 16}),
        html.Div([
            html.Div(id="expl-kpis", style={"display": "flex", "gap": 12, "flexWrap": "wrap", "flex": 1}),
            html.Button("⬇ Exportar CSV", id="expl-btn-export", n_clicks=0, style={
                "backgroundColor": COR_VERDE, "color": "#fff", "border": "none", "borderRadius": 6,
                "padding": "10px 18px", "fontSize": 12, "fontWeight": 600, "cursor": "pointer", "height": 40,
            }),
            dcc.Download(id="expl-download"),
        ], style={"display": "flex", "gap": 12, "alignItems": "center", "marginBottom": 12}),

        _loading(html.Div(id="expl-tabela-container")),
        html.Div(style={"height": 16}),
        html.Div(id="expl-detalhe-container"),
    ])


# ═══════════════════════════════════════════════════════════════════════════
# ABA 4 — MAPA & CONCEITO CAPES
# ═══════════════════════════════════════════════════════════════════════════

def _aba_mapa_layout():
    return html.Div([
        _card([
            _titulo("Filtros do Mapa"),
            html.Div([
                _filter_label("Ano", "mapa-ano", sorted(ANOS_DISPONIVEIS, reverse=True), ANO_PADRAO, 110),
                _filter_label("Indicador", "mapa-indicador",
                              ["Programas", "Instituições", "Conceito Médio", "Mestrado", "Doutorado"],
                              "Programas", 190),
            ], style={"display": "flex", "gap": 14, "flexWrap": "wrap", "alignItems": "flex-end"}),
        ], shadow=True, class_name="filters-card"),
        html.Div(style={"height": 16}),
        html.Div([
            _card([_loading(html.Div(id="mapa-figura"))], shadow=True),
            html.Div(id="mapa-top5", style={"width": 260, "flexShrink": 0}),
        ], style={"display": "flex", "gap": 12, "alignItems": "flex-start"}),
    ])


# ═══════════════════════════════════════════════════════════════════════════
# ABA 5 — RANKING
# ═══════════════════════════════════════════════════════════════════════════

def _aba_ranking():
    return html.Div([
        _card([
            html.Div([
                dcc.RadioItems(
                    id="rank-tipo",
                    options=[
                        {"label": " Por UF", "value": "uf"},
                        {"label": " Por Instituição (IES)", "value": "ies"},
                        {"label": " Por Área de Avaliação", "value": "area"},
                    ],
                    value="uf", inline=True,
                    inputStyle={"marginRight": 6, "marginLeft": 14},
                    style={"fontSize": 13, "fontWeight": 600, "color": "#374151"},
                ),
            ], style={"marginBottom": 14}),
            html.Div([
                _filter_label("Ano", "rank-ano", sorted(ANOS_DISPONIVEIS, reverse=True), ANO_PADRAO, 120),
                _filter_label("Região", "rank-regiao", REGIOES, "Todas", 170),
                _filter_label("Grau", "rank-grau", GRAUS, "Todos", 260),
                _filter_label("Exibir", "rank-topn", ["Top 10", "Top 20", "Top 50", "Todos"], "Top 10", 130),
            ], style={"display": "flex", "gap": 12, "flexWrap": "wrap", "alignItems": "flex-end"}),
        ], shadow=True, class_name="filters-card"),
        html.Div(style={"height": 16}),
        _loading(html.Div(id="rank-container")),
    ])


# ═══════════════════════════════════════════════════════════════════════════
# APP / LAYOUT GERAL
# ═══════════════════════════════════════════════════════════════════════════

app = dash.Dash(
    __name__,
    title="Mestrado e Doutorado — CAPES",
    suppress_callback_exceptions=True,
    url_base_pathname=os.environ.get("DASH_PREFIX", "/mestrado-doutorado/"),
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1.0"}],
)
server = app.server

app.index_string = """<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <style>
            * { font-family: 'Inter', sans-serif; box-sizing: border-box; }
            body { margin: 0; background-color: #F3F6F9; }
            ::-webkit-scrollbar { width: 8px; height: 8px; }
            ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 8px; }
            ::-webkit-scrollbar-track { background: transparent; }
            .card-hover { transition: box-shadow .18s ease, transform .18s ease; }
            .card-hover:hover { box-shadow: 0 8px 20px rgba(15,23,42,0.10); transform: translateY(-1px); }
            .kpi-card { transition: transform .15s ease; }
            .kpi-card:hover { transform: translateY(-2px); }
            table.tbl-capes tbody tr { transition: background-color .12s ease; }
            table.tbl-capes tbody tr:hover { background-color: #f0f7ff !important; }
            button { transition: filter .15s ease, transform .1s ease; }
            button:hover:not(:disabled) { filter: brightness(1.08); }
            button:active:not(:disabled) { transform: scale(0.98); }
            .dash-tab { transition: color .15s ease; }
            .filter-dropdown { position: relative; z-index: 1000; }
            .filter-dropdown .Select-menu-outer { z-index: 9999 !important; }
            .filters-card { position: relative; z-index: 10000; overflow: visible !important; }
            .filters-card .Select-menu-outer { z-index: 10001 !important; }
            .filter-dropdown .Select-option {
                white-space: normal;
                line-height: 1.2;
                height: auto;
                min-height: 38px;
                padding: 8px 12px;
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""

_tab_style = {"padding": "12px 18px", "fontWeight": 600, "fontSize": 13, "color": "#64748b", "border": "none"}
_tab_style_ativa = {**_tab_style, "color": COR_AZUL, "borderBottom": f"3px solid {COR_AZUL}", "backgroundColor": "#fff"}

layout = html.Div(
    className="app-shell",
    style={"fontFamily": "Inter, sans-serif", "backgroundColor": COR_FUNDO, "minHeight": "100vh"},
    children=[
        html.Div(
            [
                html.Div(
                    [
                        html.P("CAPES / PÓS-GRADUAÇÃO STRICTO SENSU", style={"fontSize": 10, "color": "#B9D5F4", "margin": "0 0 4px 0", "letterSpacing": "0.14em", "fontWeight": 700}),
                        html.H1("🎓 Mestrado e Doutorado no Brasil", style={"fontSize": 26, "fontWeight": 800, "color": "#fff", "margin": 0}),
                        html.P("Panorama completo dos programas de pós-graduação — Plataforma Sucupira / CAPES",
                               style={"fontSize": 12, "color": "#CFE2F7", "margin": "6px 0 0 0"}),
                    ],
                    style={"flex": 1},
                ),
                html.Div(
                    [
                        html.P(_fmt_mil(df_programas["cd_programa_ies"].nunique()), style={"fontSize": 30, "fontWeight": 800, "color": "#fff", "margin": 0, "textAlign": "center"}),
                        html.P("Programas na base", style={"fontSize": 10, "color": "#D9EAFE", "margin": 0, "textAlign": "center", "letterSpacing": "0.05em"}),
                    ],
                    style={"backgroundColor": "rgba(255,255,255,0.12)", "borderRadius": 10, "padding": "12px 20px", "backdropFilter": "blur(4px)"},
                ),
            ],
            style={
                "background": f"linear-gradient(135deg, {COR_HEADER} 0%, {COR_HEADER_2} 100%)",
                "padding": "26px 32px", "display": "flex", "alignItems": "center", "justifyContent": "space-between",
            },
        ),
        html.Div(
            style={"padding": "0 32px", "backgroundColor": "#fff", "borderBottom": f"1px solid {COR_BORDA}"},
            children=[
                dcc.Tabs(
                    id="tabs-principal",
                    value="aba-panorama",
                    children=[
                        dcc.Tab(label="📊 Panorama Geral", value="aba-panorama", style=_tab_style, selected_style=_tab_style_ativa),
                        dcc.Tab(label="🔎 Explorador de Programas", value="aba-explorador", style=_tab_style, selected_style=_tab_style_ativa),
                        dcc.Tab(label="🗺️ Mapa & Conceito", value="aba-mapa", style=_tab_style, selected_style=_tab_style_ativa),
                        dcc.Tab(label="🏆 Ranking", value="aba-ranking", style=_tab_style, selected_style=_tab_style_ativa),
                    ],
                )
            ],
        ),
        html.Div(id="conteudo-principal", style={"padding": "24px 32px"}),
        html.Div([
            html.Span("Fonte: CAPES — Plataforma Sucupira. ", style={"fontWeight": 600}),
            html.Span("Conceitos 6 e 7 indicam padrão de excelência internacional. "
                       "Dados sujeitos a atualização pela CAPES."),
        ], style={"padding": "16px 32px 28px 32px", "fontSize": 11, "color": "#94a3b8", "textAlign": "center"}),
    ],
)

app.layout = educ_page_loading(layout)

app.clientside_callback(
    """
    function(cdPrograma) {
        if (cdPrograma) {
            setTimeout(function() {
                var detalhe = document.getElementById('expl-detalhe-container');
                if (detalhe) {
                    detalhe.scrollIntoView({behavior: 'smooth', block: 'start'});
                }
            }, 150);
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("expl-detalhe-container", "style"),
    Input("expl-selecionado", "data"),
)


# ═══════════════════════════════════════════════════════════════════════════
# ROTEAMENTO DE ABAS
# ═══════════════════════════════════════════════════════════════════════════

@app.callback(Output("conteudo-principal", "children"), Input("tabs-principal", "value"))
def render_conteudo(aba):
    if aba == "aba-panorama":
        return _aba_panorama()
    if aba == "aba-explorador":
        return _aba_explorador_layout()
    if aba == "aba-mapa":
        return _aba_mapa_layout()
    if aba == "aba-ranking":
        return _aba_ranking()
    return html.Div()


# ═══════════════════════════════════════════════════════════════════════════
# CALLBACKS — EXPLORADOR DE PROGRAMAS
# ═══════════════════════════════════════════════════════════════════════════

@app.callback(Output("expl-uf", "options"), Output("expl-uf", "value"), Input("expl-regiao", "value"))
def expl_atualizar_ufs(regiao):
    return _atualizar_ufs_por_regiao(regiao), "Todas"


_EXPL_COLS_TEXTO = ["cd_programa_ies", "programa", "ies", "sigla_ies", "uf", "regiao",
                    "municipio", "area", "area_avaliacao", "graus", "modalidade", "situacao"]


@app.callback(
    Output("expl-dados", "data"),
    Input("expl-ano", "value"),
    Input("expl-regiao", "value"),
    Input("expl-uf", "value"),
    Input("expl-modalidade", "value"),
    Input("expl-grau", "value"),
    Input("expl-area", "value"),
    Input("expl-situacao", "value"),
    Input("expl-busca", "value"),
)
def expl_buscar_dados(ano, regiao, uf, modalidade, grau, area, situacao, busca):
    df = _aplicar_filtros(df_programas, ano=ano, regiao=regiao, uf=uf, modalidade=modalidade,
                           grau=grau, area=area, situacao=situacao, busca=busca)
    if df.empty:
        return json.dumps({"vazio": True, "registros": []})

    tabela = (
        df.groupby("cd_programa_ies", dropna=False)
        .agg(
            programa=("nm_programa_ies", "first"),
            ies=("nm_entidade_ensino", "first"),
            sigla_ies=("sg_entidade_ensino", "first"),
            uf=("sg_uf_programa", "first"),
            regiao=("nm_regiao", "first"),
            municipio=("nm_municipio_programa_ies", "first"),
            area=("nm_grande_area_conhecimento", "first"),
            area_avaliacao=("nm_area_avaliacao", "first"),
            modalidade=("nm_modalidade_programa", "first"),
            situacao=("ds_situacao_programa", "first"),
            conceito=("cd_conceito_programa", "first"),
            dependencia=("ds_dependencia_administrativa", "first"),
            organizacao=("ds_organizacao_academica", "first"),
        )
        .reset_index()
    )
    graus = df.groupby("cd_programa_ies")["nm_grau_programa"].apply(
        lambda s: ", ".join(sorted(set(s.dropna())))
    ).reset_index(name="graus")
    tabela = pd.merge(tabela, graus, on="cd_programa_ies", how="left")

    for col in _EXPL_COLS_TEXTO:
        if col in tabela.columns:
            tabela[col] = tabela[col].fillna("-")

    return json.dumps({"vazio": False, "registros": tabela.to_dict(orient="records")})


@app.callback(
    Output("expl-pagina", "data"),
    Input("expl-dados", "data"),
    Input({"type": "expl-btn-pag-ant", "sufixo": ALL}, "n_clicks"),
    Input({"type": "expl-btn-pag-prox", "sufixo": ALL}, "n_clicks"),
    State("expl-pagina", "data"),
    prevent_initial_call=True,
)
def expl_atualizar_pagina(dados_json, n_ant_list, n_prox_list, pagina_atual):
    ctx = callback_context
    if not ctx.triggered:
        return dash.no_update
    trig = ctx.triggered[0]["prop_id"]
    if trig == "expl-dados":
        return 0
    pagina_atual = pagina_atual or 0
    if isinstance(trig, str):
        return dash.no_update
    try:
        trig_type = json.loads(trig) if isinstance(trig, str) else trig
    except Exception:
        trig_type = trig
    if isinstance(trig_type, dict) and trig_type.get("type") == "expl-btn-pag-ant":
        return max(0, pagina_atual - 1)
    if isinstance(trig_type, dict) and trig_type.get("type") == "expl-btn-pag-prox":
        return pagina_atual + 1
    return dash.no_update


@app.callback(
    Output("expl-sort", "data"),
    Input({"type": "expl-th-sort", "col": ALL}, "n_clicks"),
    State("expl-sort", "data"),
    prevent_initial_call=True,
)
def expl_atualizar_sort(n_clicks_list, sort_state):
    ctx = callback_context
    if not ctx.triggered or not any(n for n in n_clicks_list if n):
        return dash.no_update
    trig_id = ctx.triggered[0]["prop_id"].split(".")[0]
    try:
        col = json.loads(trig_id).get("col")
    except Exception:
        return dash.no_update
    if not sort_state:
        sort_state = {"col": "conceito", "asc": False}
    if sort_state.get("col") == col:
        return {"col": col, "asc": not sort_state.get("asc", False)}
    return {"col": col, "asc": True}


@app.callback(
    Output("expl-selecionado", "data"),
    Input({"type": "expl-btn-sel", "cd": ALL}, "n_clicks"),
    Input("expl-dados", "data"),
    prevent_initial_call=True,
)
def expl_capturar_selecao(n_clicks_list, dados_json):
    ctx = callback_context
    if not ctx.triggered:
        return dash.no_update
    trig_id = ctx.triggered[0]["prop_id"].split(".")[0]
    if trig_id == "expl-dados":
        return None
    if not any(n for n in n_clicks_list if n):
        return dash.no_update
    try:
        return json.loads(trig_id).get("cd")
    except Exception:
        return dash.no_update


def _expl_th_sort(label, col_key, sort_col, sort_asc, align="left"):
    arrow = (" ↑" if sort_asc else " ↓") if sort_col == col_key else ""
    return html.Th(
        label + arrow, id={"type": "expl-th-sort", "col": col_key},
        style={"padding": "10px", "textAlign": align, "fontSize": 11, "borderBottom": f"2px solid {COR_BORDA}",
               "cursor": "pointer", "userSelect": "none", "color": COR_AZUL if sort_col == col_key else "#4a5568"},
    )


@app.callback(
    Output("expl-tabela-container", "children"),
    Output("expl-kpis", "children"),
    Input("expl-dados", "data"),
    Input("expl-sort", "data"),
    Input("expl-pagina", "data"),
    Input("expl-selecionado", "data"),
)
def expl_renderizar_tabela(dados_json, sort_state, pagina_atual, selecionado_cd):
    if not dados_json:
        df_inicial = _aplicar_filtros(df_programas, ano=ANO_PADRAO)
        if df_inicial.empty:
            return html.P("Nenhum programa encontrado para o ano padrão.", style={"color": COR_VERMELHO}), html.Div()

        tabela_inicial = (
            df_inicial.groupby("cd_programa_ies", dropna=False)
            .agg(
                programa=("nm_programa_ies", "first"),
                ies=("nm_entidade_ensino", "first"),
                sigla_ies=("sg_entidade_ensino", "first"),
                uf=("sg_uf_programa", "first"),
                regiao=("nm_regiao", "first"),
                municipio=("nm_municipio_programa_ies", "first"),
                area=("nm_grande_area_conhecimento", "first"),
                area_avaliacao=("nm_area_avaliacao", "first"),
                modalidade=("nm_modalidade_programa", "first"),
                situacao=("ds_situacao_programa", "first"),
                conceito=("cd_conceito_programa", "first"),
                dependencia=("ds_dependencia_administrativa", "first"),
                organizacao=("ds_organizacao_academica", "first"),
            )
            .reset_index()
        )
        graus_inicial = df_inicial.groupby("cd_programa_ies")["nm_grau_programa"].apply(
            lambda s: ", ".join(sorted(set(s.dropna())))
        ).reset_index(name="graus")
        tabela_inicial = pd.merge(tabela_inicial, graus_inicial, on="cd_programa_ies", how="left")
        for col in _EXPL_COLS_TEXTO:
            if col in tabela_inicial.columns:
                tabela_inicial[col] = tabela_inicial[col].fillna("-")
        dados_json = json.dumps({"vazio": False, "registros": tabela_inicial.to_dict(orient="records")})

    dados = json.loads(dados_json)
    if dados.get("vazio") or not dados.get("registros"):
        kpi_zero = _kpi("0", "Programas encontrados", COR_CINZA, "🔎")
        return html.P("Nenhum programa encontrado para a combinação de filtros.", style={"color": COR_VERMELHO}), [kpi_zero]

    tabela = pd.DataFrame(dados["registros"])

    sort_state = sort_state or {"col": "conceito", "asc": False}
    sort_col = sort_state.get("col", "conceito")
    sort_asc = sort_state.get("asc", False)
    sortable = {"programa", "ies", "uf", "conceito", "graus"}
    if sort_col not in sortable:
        sort_col = "conceito"
    tabela = tabela.sort_values(by=sort_col, ascending=sort_asc, na_position="last")

    n_total = len(tabela)
    n_ies = tabela["ies"].nunique()
    conceito_medio = pd.to_numeric(tabela["conceito"], errors="coerce").mean()

    total_paginas = max(1, math.ceil(n_total / PAGE_SIZE_EXPLORADOR))
    pagina_atual = max(0, min(int(pagina_atual or 0), total_paginas - 1))
    ini = pagina_atual * PAGE_SIZE_EXPLORADOR
    fim = ini + PAGE_SIZE_EXPLORADOR
    pagina_df = tabela.iloc[ini:fim]

    kpis = [
        _kpi(_fmt_mil(n_total), "Programas encontrados", COR_AZUL, "🎓"),
        _kpi(_fmt_mil(n_ies), "Instituições", COR_VERDE, "🏛️"),
        _kpi(_fmt_conceito(conceito_medio), "Conceito Médio", COR_DOURADO, "⭐"),
    ]

    header = html.Tr([
        html.Th("Ação", style={"padding": "10px", "fontSize": 11, "borderBottom": f"2px solid {COR_BORDA}"}),
        _expl_th_sort("Programa", "programa", sort_col, sort_asc),
        _expl_th_sort("IES", "ies", sort_col, sort_asc),
        _expl_th_sort("UF", "uf", sort_col, sort_asc, "center"),
        html.Th("Graus", style={"padding": "10px", "fontSize": 11, "borderBottom": f"2px solid {COR_BORDA}"}),
        _expl_th_sort("Conceito", "conceito", sort_col, sort_asc, "center"),
        html.Th("Situação", style={"padding": "10px", "fontSize": 11, "borderBottom": f"2px solid {COR_BORDA}"}),
    ])

    rows = []
    for _, r in pagina_df.iterrows():
        cd = str(r["cd_programa_ies"])
        is_sel = str(selecionado_cd) == cd
        bg = "#ebf8ff" if is_sel else "#ffffff"
        btn = html.Button(
            "✓ Selecionado" if is_sel else "Ver detalhes",
            id={"type": "expl-btn-sel", "cd": cd}, n_clicks=0,
            style={"backgroundColor": COR_VERDE if is_sel else COR_AZUL, "color": "#fff", "border": "none",
                   "borderRadius": 4, "padding": "6px 12px", "fontSize": 11, "fontWeight": 600, "cursor": "pointer"},
        )
        rows.append(html.Tr([
            html.Td(btn, style={"padding": "8px", "backgroundColor": bg, "borderBottom": "1px solid #f0f0f0"}),
            html.Td(r["programa"], style={"padding": "8px", "fontSize": 12, "fontWeight": 600, "backgroundColor": bg, "borderBottom": "1px solid #f0f0f0"}),
            html.Td(f"{r['ies']} ({r['sigla_ies']})" if r["sigla_ies"] not in ("-", "") else r["ies"],
                    style={"padding": "8px", "fontSize": 12, "backgroundColor": bg, "borderBottom": "1px solid #f0f0f0"}),
            html.Td(r["uf"], style={"padding": "8px", "fontSize": 12, "textAlign": "center", "backgroundColor": bg, "borderBottom": "1px solid #f0f0f0"}),
            html.Td(r["graus"], style={"padding": "8px", "fontSize": 11, "color": "#4a5568", "backgroundColor": bg, "borderBottom": "1px solid #f0f0f0"}),
            html.Td(_badge_conceito(r["conceito"]), style={"padding": "8px", "textAlign": "center", "backgroundColor": bg, "borderBottom": "1px solid #f0f0f0"}),
            html.Td(_badge_situacao(r["situacao"]), style={"padding": "8px", "backgroundColor": bg, "borderBottom": "1px solid #f0f0f0"}),
        ]))

    def _paginacao(sufixo):
        return html.Div([
            html.Button("← Anterior", id={"type": "expl-btn-pag-ant", "sufixo": sufixo}, n_clicks=0, disabled=(pagina_atual <= 0),
                        style={"backgroundColor": "#edf2f7" if pagina_atual <= 0 else COR_AZUL,
                               "color": "#a0aec0" if pagina_atual <= 0 else "#fff", "border": "none",
                               "borderRadius": 4, "padding": "6px 14px", "fontSize": 12, "fontWeight": 600,
                               "cursor": "not-allowed" if pagina_atual <= 0 else "pointer"}),
            html.Span(f"Página {pagina_atual + 1} de {total_paginas} · exibindo {ini + 1}–{min(fim, n_total)} de {n_total}",
                      style={"fontSize": 12, "color": "#4a5568", "margin": "0 12px"}),
            html.Button("Próxima →", id={"type": "expl-btn-pag-prox", "sufixo": sufixo}, n_clicks=0, disabled=(pagina_atual >= total_paginas - 1),
                        style={"backgroundColor": "#edf2f7" if pagina_atual >= total_paginas - 1 else COR_AZUL,
                               "color": "#a0aec0" if pagina_atual >= total_paginas - 1 else "#fff", "border": "none",
                               "borderRadius": 4, "padding": "6px 14px", "fontSize": 12, "fontWeight": 600,
                               "cursor": "not-allowed" if pagina_atual >= total_paginas - 1 else "pointer"}),
        ], style={"display": "flex", "alignItems": "center", "margin": "10px 0", "flexWrap": "wrap"})

    tabela_html = html.Table([html.Thead(header), html.Tbody(rows)],
                             className="tbl-capes", style={"width": "100%", "borderCollapse": "collapse"})

    return html.Div([_paginacao("topo"), tabela_html, _paginacao("rodape")]), kpis


@app.callback(
    Output("expl-detalhe-container", "children"),
    Input("expl-selecionado", "data"),
    Input("expl-ano", "value"),
)
def expl_renderizar_detalhe(cd_programa, ano):
    if not cd_programa:
        return _card([
            html.P("Selecione um programa na tabela acima para ver todos os detalhes "
                   "(graus ofertados, dependência administrativa, organização acadêmica).",
                   style={"textAlign": "center", "color": "#718096", "fontStyle": "italic", "margin": "12px 0"})
        ])

    df = df_programas[
        (df_programas["cd_programa_ies"].astype(str) == str(cd_programa))
        & (df_programas["an_base"] == int(ano))
    ]
    if df.empty:
        return _card([html.P("Programa não encontrado para o ano selecionado.", style={"color": COR_VERMELHO})])

    primeiro = df.iloc[0]
    linhas_grau = []
    for _, r in df.iterrows():
        linhas_grau.append(html.Tr([
            html.Td(r["nm_grau_programa"], style={"padding": "8px", "fontSize": 12, "fontWeight": 600}),
            html.Td(_badge_situacao(r["ds_situacao_programa"]), style={"padding": "8px"}),
            html.Td(_badge_conceito(r["cd_conceito_programa"]), style={"padding": "8px", "textAlign": "center"}),
            html.Td(r["nm_modalidade_programa"], style={"padding": "8px", "fontSize": 12}),
        ]))

    return _card([
        html.Div([
            html.H3(f"{primeiro['nm_programa_ies']}", style={"fontSize": 18, "fontWeight": 700, "color": COR_HEADER, "margin": 0}),
            html.P(f"{primeiro['nm_entidade_ensino']} ({primeiro['sg_entidade_ensino']}) · "
                   f"{primeiro['nm_municipio_programa_ies']}/{primeiro['sg_uf_programa']} · "
                   f"Código: {cd_programa}",
                   style={"fontSize": 12, "color": "#718096", "margin": "4px 0 0 0"}),
        ]),
        html.Hr(style={"margin": "16px 0", "border": "none", "borderTop": f"1px solid {COR_BORDA}"}),
        html.Div([
            html.Div([
                _titulo("Classificação Institucional"),
                html.Ul([
                    html.Li(f"Área de Avaliação: {primeiro['nm_area_avaliacao']}"),
                    html.Li(f"Grande Área: {primeiro['nm_grande_area_conhecimento']}"),
                    html.Li(f"Dependência Administrativa: {primeiro['ds_dependencia_administrativa']}"),
                    html.Li(f"Organização Acadêmica: {primeiro['ds_organizacao_academica']}"),
                    html.Li(f"Programa em Rede: {'Sim' if str(primeiro.get('in_rede', '')).upper() in ('SIM', 'S', 'TRUE', '1') else 'Não'}"),
                ], style={"fontSize": 13, "lineHeight": "1.8", "color": "#2d3748", "paddingLeft": 20}),
            ], style={"flex": 1, "backgroundColor": "#f7fafc", "padding": 12, "borderRadius": 6}),
            html.Div([
                _titulo("Graus Ofertados"),
                html.Table([
                    html.Thead(html.Tr([
                        html.Th("Grau", style={"padding": "8px", "fontSize": 11, "borderBottom": f"2px solid {COR_BORDA}"}),
                        html.Th("Situação", style={"padding": "8px", "fontSize": 11, "borderBottom": f"2px solid {COR_BORDA}"}),
                        html.Th("Conceito", style={"padding": "8px", "fontSize": 11, "borderBottom": f"2px solid {COR_BORDA}", "textAlign": "center"}),
                        html.Th("Modalidade", style={"padding": "8px", "fontSize": 11, "borderBottom": f"2px solid {COR_BORDA}"}),
                    ])),
                    html.Tbody(linhas_grau),
                ], style={"width": "100%", "borderCollapse": "collapse"}),
            ], style={"flex": 1.4}),
        ], style={"display": "flex", "gap": 16, "flexWrap": "wrap", "marginTop": 12}),
    ], shadow=True)


@app.callback(
    Output("expl-download", "data"),
    Input("expl-btn-export", "n_clicks"),
    State("expl-dados", "data"),
    prevent_initial_call=True,
)
def expl_exportar_csv(n_clicks, dados_json):
    if not n_clicks or not dados_json:
        return dash.no_update
    dados = json.loads(dados_json)
    if dados.get("vazio") or not dados.get("registros"):
        return dash.no_update
    df = pd.DataFrame(dados["registros"])
    return dcc.send_data_frame(df.to_csv, "programas_capes.csv", index=False, encoding="utf-8-sig")


# ═══════════════════════════════════════════════════════════════════════════
# CALLBACKS — MAPA & CONCEITO
# ═══════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("mapa-figura", "children"),
    Output("mapa-top5", "children"),
    Input("mapa-ano", "value"),
    Input("mapa-indicador", "value"),
)
def mapa_atualizar(ano, indicador):
    df = _aplicar_filtros(df_programas, ano=ano)
    dedup = _dedup_programa(df)

    if indicador == "Programas":
        agrup = df.groupby("sg_uf_programa")["cd_programa_ies"].nunique().reset_index(name="valor")
    elif indicador == "Instituições":
        agrup = df.groupby("sg_uf_programa")["nm_entidade_ensino"].nunique().reset_index(name="valor")
    elif indicador == "Conceito Médio":
        agrup = dedup.groupby("sg_uf_programa")["cd_conceito_programa"].mean().reset_index(name="valor")
    elif indicador == "Mestrado":
        agrup = df[df["nm_grau_programa"].str.upper() == "MESTRADO"].groupby("sg_uf_programa")["cd_programa_ies"].nunique().reset_index(name="valor")
    else:  # Doutorado
        agrup = df[df["nm_grau_programa"].str.upper() == "DOUTORADO"].groupby("sg_uf_programa")["cd_programa_ies"].nunique().reset_index(name="valor")

    agrup["codigo"] = agrup["sg_uf_programa"].map(UF_PARA_CODIGO_IBGE)
    agrup = agrup.dropna(subset=["codigo"])

    if geojson_ufs.get("features"):
        fig = go.Figure(go.Choropleth(
            geojson=geojson_ufs,
            locations=agrup["codigo"],
            z=agrup["valor"],
            featureidkey="properties.codigo_ibg",
            colorscale="Blues",
            marker_line_color="#fff",
            marker_line_width=0.6,
            text=agrup["sg_uf_programa"],
            hovertemplate="<b>%{text}</b><br>" + indicador + ": %{z:,.1f}<extra></extra>",
            colorbar=dict(title=indicador, thickness=14),
        ))
        fig.update_geos(fitbounds="locations", visible=False, projection_type="mercator")
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=440,
                           paper_bgcolor="#fff", font=dict(family="Inter, sans-serif"))
    else:
        agrup_ord = agrup.sort_values("valor", ascending=True)
        fig = go.Figure(go.Bar(
            x=agrup_ord["valor"], y=agrup_ord["sg_uf_programa"], orientation="h", marker_color=COR_AZUL,
            hovertemplate="<b>%{y}</b><br>" + indicador + ": %{x:,.1f}<extra></extra>",
        ))
        fig.update_layout(**_layout_base(), height=560, xaxis_title=indicador)

    top5 = agrup.sort_values("valor", ascending=False).head(5).reset_index(drop=True)
    medalhas = ["🥇", "🥈", "🥉", "4º", "5º"]
    itens_top5 = []
    for i, r in top5.iterrows():
        valor_fmt = f"{r['valor']:.2f}" if indicador == "Conceito Médio" else _fmt_mil(r["valor"])
        itens_top5.append(html.Div([
            html.Span(medalhas[i], style={"fontSize": 16, "width": 28, "display": "inline-block"}),
            html.Span(r["sg_uf_programa"], style={"fontWeight": 700, "flex": 1}),
            html.Span(valor_fmt, style={"fontWeight": 700, "color": COR_AZUL}),
        ], style={"display": "flex", "alignItems": "center", "padding": "8px 4px",
                  "borderBottom": f"1px solid {COR_BORDA}"}))

    top5_card = _card(itens_top5, shadow=True, titulo=f"Top 5 UFs — {indicador}", icone="🏆")

    return dcc.Graph(figure=fig, config={"displayModeBar": False}), top5_card


# ═══════════════════════════════════════════════════════════════════════════
# CALLBACKS — RANKING
# ═══════════════════════════════════════════════════════════════════════════

@app.callback(
    Output("rank-container", "children"),
    Input("rank-tipo", "value"),
    Input("rank-ano", "value"),
    Input("rank-regiao", "value"),
    Input("rank-grau", "value"),
    Input("rank-topn", "value"),
)
def atualizar_ranking(tipo, ano, regiao, grau, topn):
    df = _aplicar_filtros(df_programas, ano=ano, regiao=regiao, grau=grau)
    dedup = _dedup_programa(df)

    if tipo == "uf":
        rank = dedup.groupby("sg_uf_programa")["cd_programa_ies"].nunique().reset_index(name="Programas")
        rank.columns = ["Chave", "Programas"]
        titulo_col = "UF"
    elif tipo == "ies":
        rank = dedup.groupby("nm_entidade_ensino")["cd_programa_ies"].nunique().reset_index(name="Programas")
        rank.columns = ["Chave", "Programas"]
        titulo_col = "Instituição"
    else:
        rank = dedup.groupby("nm_area_avaliacao")["cd_programa_ies"].nunique().reset_index(name="Programas")
        rank.columns = ["Chave", "Programas"]
        titulo_col = "Área de Avaliação"

    conceito_map = dedup.groupby(
        {"uf": "sg_uf_programa", "ies": "nm_entidade_ensino", "area": "nm_area_avaliacao"}[tipo]
    )["cd_conceito_programa"].mean()
    rank["Conceito Médio"] = rank["Chave"].map(conceito_map)

    rank = rank.sort_values("Programas", ascending=False).reset_index(drop=True)
    if topn != "Todos":
        n = int(topn.replace("Top ", ""))
        rank = rank.head(n)
    rank.index = rank.index + 1

    medalhas = {1: "🥇", 2: "🥈", 3: "🥉"}
    linhas = []
    for pos, row in rank.iterrows():
        linhas.append(html.Tr([
            html.Td(medalhas.get(pos, pos), style={"padding": "8px 12px", "fontWeight": 700 if pos <= 3 else 400}),
            html.Td(row["Chave"], style={"padding": "8px 12px", "fontWeight": 600}),
            html.Td(_fmt_mil(row["Programas"]), style={"padding": "8px 12px", "textAlign": "right"}),
            html.Td(_badge_conceito(row["Conceito Médio"]), style={"padding": "8px 12px", "textAlign": "center"}),
        ], style={"borderBottom": "1px solid #f1f5f9"}))

    tabela = html.Table([
        html.Thead(html.Tr([
            html.Th("Pos.", style={"padding": "10px 12px", "borderBottom": f"2px solid {COR_BORDA}"}),
            html.Th(titulo_col, style={"padding": "10px 12px", "borderBottom": f"2px solid {COR_BORDA}"}),
            html.Th("Programas", style={"padding": "10px 12px", "borderBottom": f"2px solid {COR_BORDA}", "textAlign": "right"}),
            html.Th("Conceito Médio", style={"padding": "10px 12px", "borderBottom": f"2px solid {COR_BORDA}", "textAlign": "center"}),
        ])),
        html.Tbody(linhas),
    ], className="tbl-capes", style={"width": "100%", "borderCollapse": "collapse"})

    return _card([_titulo(f"Ranking por {titulo_col} — {ano}"), tabela], shadow=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False, use_reloader=False)