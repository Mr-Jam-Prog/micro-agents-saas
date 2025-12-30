"""
CFO Dashboard Generator - MicroAgents Platform

Ce module génère des dashboards financiers interactifs pour les CFO avec:
- Sorties multi-formats (HTML, PDF, Excel, PowerPoint)
- Mises à jour en temps réel
- Graphiques interactifs
- Résumés exécutifs
- Analyse comparative
- Visualisations de prévisions
- Cartes thermiques de risque
- Graphiques waterfall ROI
- Fonctionnalités d'export avec branding
"""

import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import UUID, uuid4

import pandas as pd
import plotly.graph_objects as go
import plotly.subplots as sp
import plotly.express as px
from plotly.graph_objs import Figure
from jinja2 import Environment, FileSystemLoader
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, KeepTogether
)
from reportlab.pdfgen import canvas
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from openpyxl import Workbook
from openpyxl.chart import (
    LineChart, BarChart, Reference, ScatterChart,
    PieChart, RadarChart, AreaChart
)
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.drawing.image import Image as XLImage

from src.core.business_value.calculator import ROICalculator, SavingsCalculator
from src.core.business_value.forecast.cost_forecaster import CostForecaster
from src.core.business_value.forecast.roi_forecaster import ROIForecaster
from src.core.business_value.forecast.revenue_forecaster import RevenueForecaster
from src.utils.concurrency.manager import AsyncTaskManager

logger = logging.getLogger(__name__)


class OutputFormat(str, Enum):
    """Formats de sortie supportés"""
    HTML = "html"
    PDF = "pdf"
    EXCEL = "excel"
    POWERPOINT = "powerpoint"
    JSON = "json"
    DASH = "dash"  # Dashboard interactif Flask/Dash


class TimeRange(str, Enum):
    """Périodes temporelles"""
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    YTD = "ytd"  # Year to date
    CUSTOM = "custom"


class ChartType(str, Enum):
    """Types de graphiques"""
    LINE = "line"
    BAR = "bar"
    SCATTER = "scatter"
    PIE = "pie"
    HEATMAP = "heatmap"
    WATERFALL = "waterfall"
    RADAR = "radar"
    AREA = "area"
    BOX = "box"
    VIOLIN = "violin"
    SUNBURST = "sunburst"
    TREEMAP = "treemap"


class IndustryBenchmark(str, Enum):
    """Benchmarks industriels"""
    SAAS_AVERAGE = "saas_average"
    ENTERPRISE_SOFTWARE = "enterprise_software"
    CLOUD_INFRA = "cloud_infrastructure"
    FINTECH = "fintech"
    HEALTHCARE_TECH = "healthcare_tech"
    ECOMMERCE = "ecommerce"
    CUSTOM = "custom"


class CFOChartGenerator:
    """Générateur de graphiques pour le CFO Dashboard"""
    
    def __init__(self, theme: str = "corporate"):
        """
        Initialise le générateur de graphiques.
        
        Args:
            theme: Thème visuel (corporate, dark, light)
        """
        self.theme = theme
        self._setup_themes()
        
        # Couleurs de l'entreprise MicroAgents
        self.brand_colors = {
            'primary': '#4A90E2',    # Bleu principal
            'secondary': '#50E3C2',   # Vert turquoise
            'accent': '#9013FE',      # Violet
            'success': '#7ED321',     # Vert succès
            'warning': '#F5A623',     # Orange
            'danger': '#D0021B',      # Rouge
            'neutral': '#9B9B9B',     # Gris
            'dark': '#4A4A4A',        # Gris foncé
            'light': '#F8F8F8',       # Gris clair
        }
        
        logger.info(f"CFOChartGenerator initialisé avec thème: {theme}")
    
    def _setup_themes(self):
        """Configure les thèmes Plotly"""
        self.corporate_template = go.layout.Template()
        
        # Configuration du layout corporate
        self.corporate_template.layout.update(
            font=dict(family="Arial, sans-serif", size=12, color="#333333"),
            title=dict(font=dict(size=20, color="#4A4A4A")),
            plot_bgcolor="white",
            paper_bgcolor="white",
            hoverlabel=dict(
                bgcolor="white",
                font_size=12,
                font_family="Arial"
            ),
            colorway=[
                '#4A90E2', '#50E3C2', '#9013FE', '#F5A623', '#7ED321',
                '#D0021B', '#9B9B9B', '#FF6B6B', '#4ECDC4', '#FFE66D'
            ]
        )
    
    def generate_roi_trend_chart(
        self,
        roi_data: List[Dict[str, Any]],
        forecast_data: Optional[List[Dict[str, Any]]] = None,
        benchmarks: Optional[Dict[str, List[float]]] = None
    ) -> Figure:
        """
        Génère un graphique de tendance ROI.
        
        Args:
            roi_data: Données ROI historiques
            forecast_data: Données de prévision
            benchmarks: Benchmarks industriels
            
        Returns:
            Figure Plotly
        """
        # Préparation des données
        dates = [item['date'] for item in roi_data]
        roi_values = [float(item['roi_percentage']) for item in roi_data]
        investment = [float(item['total_investment']) for item in roi_data]
        savings = [float(item['total_savings']) for item in roi_data]
        
        fig = sp.make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.1,
            subplot_titles=("Tendance ROI (%)", "Investissement vs Économies"),
            row_heights=[0.6, 0.4]
        )
        
        # Graphique ROI
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=roi_values,
                mode='lines+markers',
                name='ROI Actuel',
                line=dict(color=self.brand_colors['primary'], width=3),
                marker=dict(size=8),
                hovertemplate=(
                    'Date: %{x}<br>' +
                    'ROI: %{y:.1f}%<br>' +
                    'Investissement: $%{customdata[0]:,.0f}<br>' +
                    'Économies: $%{customdata[1]:,.0f}<extra></extra>'
                ),
                customdata=list(zip(investment, savings))
            ),
            row=1, col=1
        )
        
        # Ajout des prévisions
        if forecast_data:
            forecast_dates = [item['date'] for item in forecast_data]
            forecast_roi = [float(item['roi_percentage']) for item in forecast_data]
            
            fig.add_trace(
                go.Scatter(
                    x=forecast_dates,
                    y=forecast_roi,
                    mode='lines',
                    name='Prévision ROI',
                    line=dict(
                        color=self.brand_colors['secondary'],
                        width=2,
                        dash='dash'
                    ),
                    opacity=0.7
                ),
                row=1, col=1
            )
        
        # Ajout des benchmarks
        if benchmarks:
            for benchmark_name, benchmark_values in benchmarks.items():
                if len(benchmark_values) == len(dates):
                    fig.add_trace(
                        go.Scatter(
                            x=dates,
                            y=benchmark_values,
                            mode='lines',
                            name=f'Benchmark {benchmark_name}',
                            line=dict(
                                color=self.brand_colors['neutral'],
                                width=1,
                                dash='dot'
                            ),
                            opacity=0.5
                        ),
                        row=1, col=1
                    )
        
        # Graphique investissement vs économies
        fig.add_trace(
            go.Bar(
                x=dates,
                y=investment,
                name='Investissement',
                marker_color=self.brand_colors['accent'],
                opacity=0.7
            ),
            row=2, col=1
        )
        
        fig.add_trace(
            go.Bar(
                x=dates,
                y=savings,
                name='Économies',
                marker_color=self.brand_colors['success'],
                opacity=0.7
            ),
            row=2, col=1
        )
        
        # Mise en page
        fig.update_layout(
            template=self.corporate_template,
            title=dict(
                text="Tendance ROI - Analyse Historique et Prévision",
                x=0.5,
                xanchor='center'
            ),
            height=700,
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            ),
            hovermode='x unified'
        )
        
        fig.update_xaxes(title_text="Date", row=2, col=1)
        fig.update_yaxes(title_text="ROI (%)", row=1, col=1)
        fig.update_yaxes(title_text="Montant ($)", row=2, col=1)
        
        # Ligne à 100% ROI (seuil de rentabilité)
        fig.add_hline(
            y=100,
            line_dash="dot",
            line_color="red",
            opacity=0.5,
            row=1, col=1,
            annotation_text="Seuil de rentabilité",
            annotation_position="bottom right"
        )
        
        return fig
    
    def generate_cost_savings_breakdown(
        self,
        savings_data: Dict[str, Dict[str, float]],
        time_period: str = "month"
    ) -> Figure:
        """
        Génère une répartition des économies.
        
        Args:
            savings_data: Données d'économies par catégorie
            time_period: Période (month, quarter, year)
            
        Returns:
            Figure Plotly
        """
        # Préparation des données
        categories = list(savings_data.keys())
        subcategories_data = []
        
        for category, subcategories in savings_data.items():
            for subcategory, amount in subcategories.items():
                subcategories_data.append({
                    'category': category,
                    'subcategory': subcategory,
                    'amount': amount
                })
        
        df = pd.DataFrame(subcategories_data)
        
        # Graphique Sunburst
        fig = px.sunburst(
            df,
            path=['category', 'subcategory'],
            values='amount',
            color='amount',
            color_continuous_scale=px.colors.sequential.Plasma,
            title="Répartition des Économies par Catégorie"
        )
        
        fig.update_layout(
            template=self.corporate_template,
            height=600,
            coloraxis_colorbar=dict(
                title="Économies ($)",
                thickness=20
            )
        )
        
        # Ajouter un graphique à barres empilées en sous-graphique
        fig2 = go.Figure()
        
        for category in categories:
            category_data = df[df['category'] == category]
            fig2.add_trace(go.Bar(
                x=[category],
                y=[category_data['amount'].sum()],
                name=category,
                text=[f"${category_data['amount'].sum():,.0f}"],
                textposition='auto',
                marker_color=self._get_category_color(category)
            ))
        
        fig2.update_layout(
            template=self.corporate_template,
            title="Économies Totales par Catégorie",
            barmode='stack',
            height=400,
            showlegend=True
        )
        
        # Combiner les graphiques
        combined_fig = sp.make_subplots(
            rows=1, cols=2,
            subplot_titles=("Répartition Détaillée", "Total par Catégorie"),
            specs=[[{"type": "sunburst"}, {"type": "bar"}]]
        )
        
        combined_fig.add_trace(fig.data[0], row=1, col=1)
        
        for trace in fig2.data:
            combined_fig.add_trace(trace, row=1, col=2)
        
        combined_fig.update_layout(
            template=self.corporate_template,
            title=dict(
                text=f"Analyse des Économies - {time_period.capitalize()}",
                x=0.5,
                xanchor='center'
            ),
            height=500,
            showlegend=True
        )
        
        return combined_fig
    
    def generate_risk_heatmap(
        self,
        risk_matrix: List[List[float]],
        risk_categories: List[str],
        impact_levels: List[str],
        mitigation_data: Optional[Dict[str, float]] = None
    ) -> Figure:
        """
        Génère une carte thermique de risque.
        
        Args:
            risk_matrix: Matrice de risque [probabilité][impact]
            risk_categories: Catégories de risque
            impact_levels: Niveaux d'impact
            mitigation_data: Données d'atténuation
            
        Returns:
            Figure Plotly
        """
        # Création de la heatmap
        fig = go.Figure(data=go.Heatmap(
            z=risk_matrix,
            x=impact_levels,
            y=risk_categories,
            colorscale='RdYlGn_r',  # Rouge (haut risque) -> Vert (bas risque)
            text=[[f"P: {prob*100:.0f}%<br>I: {impact}" 
                   for impact in range(len(impact_levels))] 
                  for prob in range(len(risk_categories))],
            hoverinfo='text',
            colorbar=dict(
                title="Niveau de Risque",
                titleside="right",
                tickmode="array",
                tickvals=[0, 0.5, 1],
                ticktext=["Faible", "Moyen", "Élevé"]
            )
        ))
        
        # Ajout des annotations pour les scores de risque
        for i, category in enumerate(risk_categories):
            for j, impact in enumerate(impact_levels):
                risk_score = risk_matrix[i][j]
                risk_color = "white" if risk_score > 0.7 else "black"
                
                fig.add_annotation(
                    x=j,
                    y=i,
                    text=f"{risk_score:.2f}",
                    showarrow=False,
                    font=dict(color=risk_color, size=10)
                )
        
        # Ajout des données d'atténuation si disponibles
        if mitigation_data:
            mitigation_trace = go.Scatter(
                x=[len(impact_levels) - 0.5] * len(risk_categories),
                y=risk_categories,
                mode='markers',
                marker=dict(
                    size=[mitigation_data.get(cat, 0) * 50 for cat in risk_categories],
                    color=self.brand_colors['secondary'],
                    symbol='square',
                    line=dict(width=2, color='white')
                ),
                name='Atténuation',
                hovertemplate=(
                    'Catégorie: %{y}<br>' +
                    'Niveau d\'atténuation: %{marker.size:.0f}%<extra></extra>'
                )
            )
            
            fig.add_trace(mitigation_trace)
        
        # Mise en page
        fig.update_layout(
            template=self.corporate_template,
            title=dict(
                text="Carte Thermique des Risques - Matrice Probabilité/Impact",
                x=0.5,
                xanchor='center'
            ),
            height=500,
            xaxis=dict(title="Impact"),
            yaxis=dict(title="Catégorie de Risque"),
            showlegend=True if mitigation_data else False
        )
        
        # Ajouter des zones de risque
        fig.add_shape(
            type="rect",
            x0=-0.5, y0=-0.5,
            x1=len(impact_levels)-0.5, y1=len(risk_categories)-0.5,
            line=dict(color="black", width=1),
            fillcolor="rgba(0,0,0,0)",
            layer="below"
        )
        
        return fig
    
    def generate_performance_benchmarks(
        self,
        performance_data: Dict[str, Dict[str, float]],
        benchmarks: Dict[str, float],
        categories: List[str]
    ) -> Figure:
        """
        Génère un graphique radar de performance vs benchmarks.
        
        Args:
            performance_data: Données de performance
            benchmarks: Benchmarks industriels
            categories: Catégories à comparer
            
        Returns:
            Figure Plotly
        """
        fig = go.Figure()
        
        # Ajout des données de performance pour chaque période
        for period, scores in performance_data.items():
            fig.add_trace(go.Scatterpolar(
                r=[scores.get(cat, 0) for cat in categories],
                theta=categories,
                fill='toself',
                name=period,
                line_color=self._get_period_color(period)
            ))
        
        # Ajout des benchmarks
        fig.add_trace(go.Scatterpolar(
            r=[benchmarks.get(cat, 0) for cat in categories],
            theta=categories,
            name='Benchmark Industriel',
            line=dict(
                color=self.brand_colors['warning'],
                width=3,
                dash='dash'
            ),
            marker=dict(size=8)
        ))
        
        # Mise en page
        fig.update_layout(
            template=self.corporate_template,
            title=dict(
                text="Performance vs Benchmarks Industriels",
                x=0.5,
                xanchor='center'
            ),
            polar=dict(
                radialaxis=dict(
                    visible=True,
                    range=[0, 100],
                    tickfont=dict(size=10)
                ),
                angularaxis=dict(
                    tickfont=dict(size=11),
                    rotation=90
                )
            ),
            height=600,
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            )
        )
        
        # Ajouter un graphique à barres comparatif
        fig2 = go.Figure()
        
        for i, category in enumerate(categories):
            fig2.add_trace(go.Bar(
                x=[category],
                y=[performance_data.get('current', {}).get(category, 0)],
                name='Actuel',
                marker_color=self.brand_colors['primary'],
                width=0.4,
                offset=-0.2
            ))
            
            fig2.add_trace(go.Bar(
                x=[category],
                y=[benchmarks.get(category, 0)],
                name='Benchmark',
                marker_color=self.brand_colors['neutral'],
                width=0.4,
                offset=0.2
            ))
        
        fig2.update_layout(
            template=self.corporate_template,
            title="Comparaison Détaillée par Catégorie",
            barmode='group',
            height=400,
            showlegend=True
        )
        
        # Combiner les graphiques
        combined_fig = sp.make_subplots(
            rows=2, cols=1,
            subplot_titles=("Analyse Radar", "Comparaison par Catégorie"),
            vertical_spacing=0.15
        )
        
        combined_fig.add_trace(fig.data[0], row=1, col=1)
        if len(fig.data) > 1:
            for trace in fig.data[1:]:
                combined_fig.add_trace(trace, row=1, col=1)
        
        for trace in fig2.data:
            combined_fig.add_trace(trace, row=2, col=1)
        
        combined_fig.update_layout(
            template=self.corporate_template,
            height=900,
            showlegend=True
        )
        
        return combined_fig
    
    def generate_investment_vs_return(
        self,
        investment_data: List[Dict[str, Any]],
        roi_data: List[Dict[str, Any]]
    ) -> Figure:
        """
        Génère un graphique investissement vs retour.
        
        Args:
            investment_data: Données d'investissement
            roi_data: Données de retour
            
        Returns:
            Figure Plotly
        """
        # Préparation des données
        dates = [item['date'] for item in investment_data]
        investments = [float(item['amount']) for item in investment_data]
        cumulative_investment = pd.Series(investments).cumsum().tolist()
        
        returns = [float(item['return']) for item in roi_data]
        cumulative_return = pd.Series(returns).cumsum().tolist()
        
        # Calcul du ratio investissement/retour
        ratio = [r/i if i > 0 else 0 for r, i in zip(cumulative_return, cumulative_investment)]
        
        fig = sp.make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                "Investissement Cumulé vs Retour",
                "Ratio Investissement/Retour",
                "Investissement par Période",
                "Retour par Période"
            ),
            specs=[
                [{"colspan": 2}, None],
                [{}, {}]
            ],
            vertical_spacing=0.15,
            horizontal_spacing=0.15
        )
        
        # Graphique cumulé
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=cumulative_investment,
                mode='lines',
                name='Investissement Cumulé',
                line=dict(color=self.brand_colors['accent'], width=3),
                fill='tozeroy',
                fillcolor='rgba(144, 19, 254, 0.1)'
            ),
            row=1, col=1
        )
        
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=cumulative_return,
                mode='lines',
                name='Retour Cumulé',
                line=dict(color=self.brand_colors['success'], width=3),
                fill='tonexty',
                fillcolor='rgba(126, 211, 33, 0.1)'
            ),
            row=1, col=1
        )
        
        # Graphique ratio
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=ratio,
                mode='lines+markers',
                name='Ratio I/R',
                line=dict(color=self.brand_colors['warning'], width=2),
                marker=dict(size=6)
            ),
            row=2, col=1
        )
        
        # Ligne à 1.0 (seuil de rentabilité)
        fig.add_hline(
            y=1.0,
            line_dash="dot",
            line_color="red",
            opacity=0.5,
            row=2, col=1,
            annotation_text="Seuil de rentabilité",
            annotation_position="bottom right"
        )
        
        # Graphique investissement par période
        fig.add_trace(
            go.Bar(
                x=dates,
                y=investments,
                name='Investissement',
                marker_color=self.brand_colors['accent'],
                opacity=0.7
            ),
            row=2, col=2
        )
        
        # Graphique retour par période
        fig.add_trace(
            go.Bar(
                x=dates,
                y=returns,
                name='Retour',
                marker_color=self.brand_colors['success'],
                opacity=0.7
            ),
            row=2, col=2
        )
        
        # Mise en page
        fig.update_layout(
            template=self.corporate_template,
            title=dict(
                text="Analyse Investissement vs Retour sur Investissement",
                x=0.5,
                xanchor='center'
            ),
            height=800,
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            )
        )
        
        fig.update_xaxes(title_text="Date", row=1, col=1)
        fig.update_yaxes(title_text="Montant Cumulé ($)", row=1, col=1)
        fig.update_yaxes(title_text="Ratio", row=2, col=1)
        fig.update_yaxes(title_text="Montant ($)", row=2, col=2)
        
        return fig
    
    def generate_payback_period_chart(
        self,
        investment_data: List[float],
        cash_flow_data: List[float],
        discount_rate: float = 0.1
    ) -> Figure:
        """
        Génère un graphique de période de récupération.
        
        Args:
            investment_data: Investissements par période
            cash_flow_data: Flux de trésorerie par période
            discount_rate: Taux d'actualisation
            
        Returns:
            Figure Plotly
        """
        # Calculs financiers
        cumulative_investment = pd.Series(investment_data).cumsum().tolist()
        cumulative_cash_flow = pd.Series(cash_flow_data).cumsum().tolist()
        
        # Calcul de la période de récupération
        payback_period = None
        for i, (inv, cf) in enumerate(zip(cumulative_investment, cumulative_cash_flow)):
            if cf >= inv:
                payback_period = i
                break
        
        # Calcul de la VAN
        npv = 0
        npv_by_period = []
        for i, cf in enumerate(cash_flow_data):
            discounted_cf = cf / ((1 + discount_rate) ** i)
            npv += discounted_cf
            npv_by_period.append(npv)
        
        fig = sp.make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                "Période de Récupération",
                "Flux de Trésorerie Cumulés",
                "VAN par Période",
                "Analyse de Sensibilité"
            ),
            vertical_spacing=0.15,
            horizontal_spacing=0.15
        )
        
        # Graphique période de récupération
        periods = list(range(len(investment_data)))
        
        fig.add_trace(
            go.Scatter(
                x=periods,
                y=cumulative_investment,
                mode='lines',
                name='Investissement Cumulé',
                line=dict(color=self.brand_colors['accent'], width=3)
            ),
            row=1, col=1
        )
        
        fig.add_trace(
            go.Scatter(
                x=periods,
                y=cumulative_cash_flow,
                mode='lines',
                name='Flux Cumulés',
                line=dict(color=self.brand_colors['success'], width=3)
            ),
            row=1, col=1
        )
        
        # Marquer la période de récupération
        if payback_period is not None:
            fig.add_vline(
                x=payback_period,
                line_dash="dash",
                line_color="red",
                opacity=0.7,
                row=1, col=1,
                annotation_text=f"Récupération: Période {payback_period}",
                annotation_position="top right"
            )
            
            fig.add_trace(
                go.Scatter(
                    x=[payback_period],
                    y=[cumulative_investment[payback_period]],
                    mode='markers',
                    marker=dict(
                        color='red',
                        size=15,
                        symbol='x'
                    ),
                    name='Point de Récupération'
                ),
                row=1, col=1
            )
        
        # Graphique VAN
        fig.add_trace(
            go.Scatter(
                x=periods,
                y=npv_by_period,
                mode='lines+markers',
                name='VAN',
                line=dict(color=self.brand_colors['primary'], width=2)
            ),
            row=2, col=1
        )
        
        # Ligne à 0 (seuil de rentabilité VAN)
        fig.add_hline(
            y=0,
            line_dash="dot",
            line_color="black",
            opacity=0.5,
            row=2, col=1
        )
        
        # Analyse de sensibilité
        sensitivity_rates = [0.05, 0.1, 0.15, 0.2]
        sensitivity_data = []
        
        for rate in sensitivity_rates:
            npv_sensitivity = 0
            npv_by_period_sens = []
            for i, cf in enumerate(cash_flow_data):
                discounted_cf = cf / ((1 + rate) ** i)
                npv_sensitivity += discounted_cf
                npv_by_period_sens.append(npv_sensitivity)
            sensitivity_data.append(npv_by_period_sens)
        
        for i, (rate, data) in enumerate(zip(sensitivity_rates, sensitivity_data)):
            fig.add_trace(
                go.Scatter(
                    x=periods,
                    y=data,
                    mode='lines',
                    name=f'Taux {rate*100:.0f}%',
                    line=dict(
                        color=self._get_sensitivity_color(i),
                        width=1,
                        dash='dot' if i > 0 else 'solid'
                    ),
                    opacity=0.7
                ),
                row=2, col=2
            )
        
        # Mise en page
        fig.update_layout(
            template=self.corporate_template,
            title=dict(
                text=f"Analyse de Récupération - VAN: ${npv:,.0f}",
                x=0.5,
                xanchor='center'
            ),
            height=800,
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            )
        )
        
        fig.update_xaxes(title_text="Période", row=1, col=1)
        fig.update_yaxes(title_text="Montant Cumulé ($)", row=1, col=1)
        fig.update_xaxes(title_text="Période", row=2, col=1)
        fig.update_yaxes(title_text="VAN ($)", row=2, col=1)
        fig.update_xaxes(title_text="Période", row=2, col=2)
        fig.update_yaxes(title_text="VAN ($)", row=2, col=2)
        
        return fig
    
    def generate_monte_carlo_simulation(
        self,
        simulation_results: List[List[float]],
        confidence_intervals: Dict[str, float]
    ) -> Figure:
        """
        Génère des visualisations de simulation Monte Carlo.
        
        Args:
            simulation_results: Résultats des simulations
            confidence_intervals: Intervalles de confiance
            
        Returns:
            Figure Plotly
        """
        # Préparation des données
        n_simulations = len(simulation_results)
        n_periods = len(simulation_results[0]) if simulation_results else 0
        
        # Calcul des statistiques
        simulation_array = np.array(simulation_results)
        mean_trajectory = simulation_array.mean(axis=0)
        std_trajectory = simulation_array.std(axis=0)
        
        # Percentiles
        percentiles = {
            'p5': np.percentile(simulation_array, 5, axis=0),
            'p25': np.percentile(simulation_array, 25, axis=0),
            'p75': np.percentile(simulation_array, 75, axis=0),
            'p95': np.percentile(simulation_array, 95, axis=0)
        }
        
        fig = sp.make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                "Trajectoires de Simulation",
                "Distribution des Résultats Finaux",
                "Intervalles de Confiance",
                "Analyse de Risque"
            ),
            vertical_spacing=0.15,
            horizontal_spacing=0.15
        )
        
        # Trajectoires de simulation (échantillon)
        sample_indices = np.random.choice(
            n_simulations, 
            size=min(50, n_simulations), 
            replace=False
        )
        
        for idx in sample_indices:
            fig.add_trace(
                go.Scatter(
                    x=list(range(n_periods)),
                    y=simulation_results[idx],
                    mode='lines',
                    name=f'Sim {idx}',
                    line=dict(color='rgba(74, 144, 226, 0.1)', width=1),
                    showlegend=False
                ),
                row=1, col=1
            )
        
        # Moyenne
        fig.add_trace(
            go.Scatter(
                x=list(range(n_periods)),
                y=mean_trajectory,
                mode='lines',
                name='Moyenne',
                line=dict(color='black', width=3)
            ),
            row=1, col=1
        )
        
        # Distribution des résultats finaux
        final_results = simulation_array[:, -1] if n_periods > 0 else []
        
        fig.add_trace(
            go.Histogram(
                x=final_results,
                nbinsx=50,
                name='Distribution',
                marker_color=self.brand_colors['primary'],
                opacity=0.7
            ),
            row=1, col=2
        )
        
        # Ajouter une courbe de densité
        if len(final_results) > 1:
            from scipy.stats import gaussian_kde
            kde = gaussian_kde(final_results)
            x_range = np.linspace(min(final_results), max(final_results), 100)
            y_kde = kde(x_range)
            
            fig.add_trace(
                go.Scatter(
                    x=x_range,
                    y=y_kde * len(final_results) * (max(final_results) - min(final_results)) / 50,
                    mode='lines',
                    name='Densité',
                    line=dict(color='red', width=2),
                    yaxis='y2'
                ),
                row=1, col=2
            )
            
            fig.update_layout(
                yaxis2=dict(
                    title="Densité",
                    overlaying="y",
                    side="right"
                )
            )
        
        # Intervalles de confiance
        periods = list(range(n_periods))
        
        fig.add_trace(
            go.Scatter(
                x=periods,
                y=mean_trajectory,
                mode='lines',
                name='Moyenne',
                line=dict(color=self.brand_colors['primary'], width=2)
            ),
            row=2, col=1
        )
        
        # Intervalle de confiance 95%
        fig.add_trace(
            go.Scatter(
                x=periods + periods[::-1],
                y=list(percentiles['p95']) + list(percentiles['p5'][::-1]),
                fill='toself',
                fillcolor='rgba(74, 144, 226, 0.2)',
                line=dict(color='rgba(255, 255, 255, 0)'),
                name='IC 95%',
                showlegend=True
            ),
            row=2, col=1
        )
        
        # Intervalle de confiance 50%
        fig.add_trace(
            go.Scatter(
                x=periods + periods[::-1],
                y=list(percentiles['p75']) + list(percentiles['p25'][::-1]),
                fill='toself',
                fillcolor='rgba(74, 144, 226, 0.4)',
                line=dict(color='rgba(255, 255, 255, 0)'),
                name='IC 50%',
                showlegend=True
            ),
            row=2, col=1
        )
        
        # Analyse de risque
        risk_thresholds = [0, 100000, 200000, 300000]
        risk_colors = ['green', 'yellow', 'orange', 'red']
        
        for threshold, color in zip(risk_thresholds, risk_colors):
            fig.add_hline(
                y=threshold,
                line_dash="dot",
                line_color=color,
                opacity=0.5,
                row=2, col=1,
                annotation_text=f"Seuil {threshold}",
                annotation_position="right"
            )
        
        # Calcul du VaR (Value at Risk)
        var_95 = np.percentile(final_results, 5) if len(final_results) > 0 else 0
        var_99 = np.percentile(final_results, 1) if len(final_results) > 0 else 0
        
        fig.add_vline(
            x=var_95,
            line_dash="dash",
            line_color="red",
            opacity=0.7,
            row=1, col=2,
            annotation_text=f"VaR 95%: {var_95:,.0f}",
            annotation_position="top"
        )
        
        # Mise en page
        fig.update_layout(
            template=self.corporate_template,
            title=dict(
                text=f"Simulation Monte Carlo - {n_simulations} Scénarios",
                x=0.5,
                xanchor='center'
            ),
            height=800,
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            )
        )
        
        fig.update_xaxes(title_text="Période", row=1, col=1)
        fig.update_yaxes(title_text="Valeur ($)", row=1, col=1)
        fig.update_xaxes(title_text="Valeur Finale ($)", row=1, col=2)
        fig.update_yaxes(title_text="Fréquence", row=1, col=2)
        fig.update_xaxes(title_text="Période", row=2, col=1)
        fig.update_yaxes(title_text="Valeur ($)", row=2, col=1)
        
        return fig
    
    def generate_roi_waterfall_chart(
        self,
        components: Dict[str, float],
        start_value: float = 0,
        end_value: float = None
    ) -> Figure:
        """
        Génère un graphique waterfall ROI.
        
        Args:
            components: Composants du ROI avec valeurs
            start_value: Valeur de départ
            end_value: Valeur finale
            
        Returns:
            Figure Plotly
        """
        # Calcul des valeurs cumulatives
        categories = list(components.keys())
        values = list(components.values())
        
        cumulative = start_value
        y_start = []
        y_end = []
        
        for value in values:
            y_start.append(cumulative)
            cumulative += value
            y_end.append(cumulative)
        
        # Couleurs basées sur le signe des valeurs
        colors = []
        for value in values:
            if value >= 0:
                colors.append(self.brand_colors['success'])
            else:
                colors.append(self.brand_colors['danger'])
        
        fig = go.Figure()
        
        # Barres waterfall
        fig.add_trace(go.Waterfall(
            name="ROI",
            orientation="v",
            measure=["absolute"] + ["relative"] * (len(categories) - 1),
            x=["Départ"] + categories,
            textposition="outside",
            text=[f"${start_value:+,.0f}"] + [f"${v:+,.0f}" for v in values],
            y=[start_value] + values,
            connector={"line": {"color": "rgb(63, 63, 63)"}},
            increasing={"marker": {"color": self.brand_colors['success']}},
            decreasing={"marker": {"color": self.brand_colors['danger']}},
            totals={"marker": {"color": self.brand_colors['primary']}}
        ))
        
        # Ligne de connexion
        for i in range(len(categories) - 1):
            fig.add_shape(
                type="line",
                x0=i + 0.5, y0=y_end[i],
                x1=i + 1.5, y1=y_start[i + 1],
                line=dict(color="gray", width=1, dash="dot")
            )
        
        # Valeur finale
        if end_value is not None:
            fig.add_annotation(
                x=len(categories),
                y=end_value,
                text=f"Total: ${end_value:,.0f}",
                showarrow=True,
                arrowhead=2,
                ax=0,
                ay=-40,
                bgcolor="white",
                bordercolor="black",
                borderwidth=1
            )
        
        # Mise en page
        fig.update_layout(
            template=self.corporate_template,
            title=dict(
                text="Analyse Waterfall du ROI - Contribution par Composant",
                x=0.5,
                xanchor='center'
            ),
            height=600,
            showlegend=False,
            xaxis=dict(title="Composants"),
            yaxis=dict(title="Valeur ($)")
        )
        
        return fig
    
    def _get_category_color(self, category: str) -> str:
        """Retourne une couleur basée sur la catégorie"""
        color_map = {
            'infrastructure': self.brand_colors['primary'],
            'personnel': self.brand_colors['secondary'],
            'software': self.brand_colors['accent'],
            'maintenance': self.brand_colors['neutral'],
            'training': self.brand_colors['warning'],
            'other': self.brand_colors['dark']
        }
        return color_map.get(category.lower(), self.brand_colors['primary'])
    
    def _get_period_color(self, period: str) -> str:
        """Retourne une couleur basée sur la période"""
        color_map = {
            'current': self.brand_colors['primary'],
            'previous': self.brand_colors['neutral'],
            'q1': '#FF6B6B',
            'q2': '#4ECDC4',
            'q3': '#FFE66D',
            'q4': '#95E1D3'
        }
        return color_map.get(period.lower(), self.brand_colors['primary'])
    
    def _get_sensitivity_color(self, index: int) -> str:
        """Retourne une couleur pour l'analyse de sensibilité"""
        colors = [
            self.brand_colors['primary'],
            self.brand_colors['secondary'],
            self.brand_colors['accent'],
            self.brand_colors['warning'],
            self.brand_colors['danger']
        ]
        return colors[index % len(colors)]


class CFODashboardGenerator:
    """Générateur principal du Dashboard CFO"""
    
    def __init__(
        self,
        customer_id: UUID,
        time_range: TimeRange = TimeRange.MONTH,
        format: OutputFormat = OutputFormat.HTML
    ):
        """
        Initialise le générateur de dashboard.
        
        Args:
            customer_id: ID du client
            time_range: Période temporelle
            format: Format de sortie
        """
        self.customer_id = customer_id
        self.time_range = time_range
        self.output_format = format
        
        # Initialisation des composants
        self.chart_generator = CFOChartGenerator()
        self.roi_calculator = ROICalculator()
        self.savings_calculator = SavingsCalculator()
        self.cost_forecaster = CostForecaster()
        self.roi_forecaster = ROIForecaster()
        self.revenue_forecaster = RevenueForecaster()
        self.task_manager = AsyncTaskManager()
        
        # Configuration
        self.templates_dir = Path(__file__).parent / "templates"
        self.output_dir = Path(__file__).parent / "output"
        self.output_dir.mkdir(exist_ok=True)
        
        # Données du dashboard
        self.dashboard_data = {}
        self.charts = {}
        
        logger.info(f"CFODashboardGenerator initialisé pour client {customer_id}")
    
    async def generate_dashboard(
        self,
        include_charts: List[ChartType] = None,
        include_benchmarks: bool = True,
        include_forecasts: bool = True
    ) -> Dict[str, Any]:
        """
        Génère le dashboard complet.
        
        Args:
            include_charts: Types de graphiques à inclure
            include_benchmarks: Inclure les benchmarks
            include_forecasts: Inclure les prévisions
            
        Returns:
            Données du dashboard
        """
        logger.info(f"Génération dashboard pour {self.customer_id}")
        
        # Chargement des données
        await self._load_dashboard_data()
        
        # Génération des graphiques
        await self._generate_charts(
            include_charts or list(ChartType),
            include_benchmarks,
            include_forecasts
        )
        
        # Génération du résumé exécutif
        executive_summary = await self._generate_executive_summary()
        
        # Génération du rapport selon le format
        report_path = await self._generate_report()
        
        return {
            'dashboard_id': str(uuid4()),
            'customer_id': str(self.customer_id),
            'generated_at': datetime.utcnow().isoformat(),
            'time_range': self.time_range.value,
            'executive_summary': executive_summary,
            'charts': list(self.charts.keys()),
            'report_path': str(report_path),
            'format': self.output_format.value,
            'download_url': f"/api/dashboard/download/{self.customer_id}/{datetime.utcnow().strftime('%Y%m%d')}"
        }
    
    async def _load_dashboard_data(self):
        """Charge les données pour le dashboard"""
        tasks = [
            self._load_roi_data(),
            self._load_savings_data(),
            self._load_investment_data(),
            self._load_performance_data(),
            self._load_risk_data()
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        self.dashboard_data.update({
            'roi': results[0] if not isinstance(results[0], Exception) else [],
            'savings': results[1] if not isinstance(results[1], Exception) else {},
            'investment': results[2] if not isinstance(results[2], Exception) else [],
            'performance': results[3] if not isinstance(results[3], Exception) else {},
            'risk': results[4] if not isinstance(results[4], Exception) else {}
        })
        
        logger.info("Données dashboard chargées avec succès")
    
    async def _load_roi_data(self) -> List[Dict[str, Any]]:
        """Charge les données ROI"""
        return await self.roi_calculator.get_customer_roi_history(
            self.customer_id,
            self.time_range.value
        )
    
    async def _load_savings_data(self) -> Dict[str, Any]:
        """Charge les données d'économies"""
        return await self.savings_calculator.get_savings_breakdown(
            self.customer_id,
            self.time_range.value
        )
    
    async def _load_investment_data(self) -> List[Dict[str, Any]]:
        """Charge les données d'investissement"""
        # À implémenter selon votre source de données
        return []
    
    async def _load_performance_data(self) -> Dict[str, Any]:
        """Charge les données de performance"""
        # À implémenter selon votre source de données
        return {}
    
    async def _load_risk_data(self) -> Dict[str, Any]:
        """Charge les données de risque"""
        # À implémenter selon votre source de données
        return {}
    
    async def _generate_charts(
        self,
        chart_types: List[ChartType],
        include_benchmarks: bool,
        include_forecasts: bool
    ):
        """Génère les graphiques spécifiés"""
        chart_tasks = []
        
        for chart_type in chart_types:
            if chart_type == ChartType.LINE:
                chart_tasks.append(self._generate_roi_trend_chart(include_forecasts, include_benchmarks))
            elif chart_type == ChartType.WATERFALL:
                chart_tasks.append(self._generate_roi_waterfall_chart())
            elif chart_type == ChartType.HEATMAP:
                chart_tasks.append(self._generate_risk_heatmap())
            elif chart_type == ChartType.RADAR:
                chart_tasks.append(self._generate_performance_benchmarks(include_benchmarks))
            elif chart_type == ChartType.AREA:
                chart_tasks.append(self._generate_investment_vs_return_chart())
            elif chart_type == ChartType.BAR:
                chart_tasks.append(self._generate_savings_breakdown_chart())
        
        # Exécution parallèle des générations de graphiques
        charts = await asyncio.gather(*chart_tasks, return_exceptions=True)
        
        for i, chart_type in enumerate(chart_types):
            if not isinstance(charts[i], Exception):
                self.charts[chart_type.value] = charts[i]
    
    async def _generate_roi_trend_chart(
        self,
        include_forecasts: bool,
        include_benchmarks: bool
    ) -> str:
        """Génère le graphique de tendance ROI"""
        roi_data = self.dashboard_data.get('roi', [])
        
        forecast_data = None
        if include_forecasts:
            forecast_data = await self.roi_forecaster.forecast_roi(
                self.customer_id,
                periods=12
            )
        
        benchmarks = None
        if include_benchmarks:
            benchmarks = await self._get_industry_benchmarks()
        
        fig = self.chart_generator.generate_roi_trend_chart(
            roi_data,
            forecast_data,
            benchmarks
        )
        
        # Sauvegarde du graphique
        if self.output_format == OutputFormat.HTML:
            return fig.to_html(full_html=False, include_plotlyjs='cdn')
        else:
            return self._save_chart_image(fig, 'roi_trend')
    
    async def _generate_roi_waterfall_chart(self) -> str:
        """Génère le graphique waterfall ROI"""
        roi_components = await self.roi_calculator.get_roi_components(
            self.customer_id
        )
        
        fig = self.chart_generator.generate_roi_waterfall_chart(
            components=roi_components,
            start_value=0,
            end_value=roi_components.get('total_roi', 0)
        )
        
        if self.output_format == OutputFormat.HTML:
            return fig.to_html(full_html=False, include_plotlyjs=False)
        else:
            return self._save_chart_image(fig, 'roi_waterfall')
    
    async def _generate_risk_heatmap(self) -> str:
        """Génère la carte thermique de risque"""
        risk_data = self.dashboard_data.get('risk', {})
        
        fig = self.chart_generator.generate_risk_heatmap(
            risk_matrix=risk_data.get('matrix', []),
            risk_categories=risk_data.get('categories', []),
            impact_levels=risk_data.get('impacts', []),
            mitigation_data=risk_data.get('mitigation', {})
        )
        
        if self.output_format == OutputFormat.HTML:
            return fig.to_html(full_html=False, include_plotlyjs=False)
        else:
            return self._save_chart_image(fig, 'risk_heatmap')
    
    async def _generate_performance_benchmarks(self, include_benchmarks: bool) -> str:
        """Génère les benchmarks de performance"""
        performance_data = self.dashboard_data.get('performance', {})
        
        benchmarks = {}
        if include_benchmarks:
            benchmarks = await self._get_performance_benchmarks()
        
        fig = self.chart_generator.generate_performance_benchmarks(
            performance_data,
            benchmarks,
            categories=['cost_savings', 'security', 'performance', 'reliability', 'compliance']
        )
        
        if self.output_format == OutputFormat.HTML:
            return fig.to_html(full_html=False, include_plotlyjs=False)
        else:
            return self._save_chart_image(fig, 'performance_benchmarks')
    
    async def _generate_investment_vs_return_chart(self) -> str:
        """Génère le graphique investissement vs retour"""
        investment_data = self.dashboard_data.get('investment', [])
        roi_data = self.dashboard_data.get('roi', [])
        
        fig = self.chart_generator.generate_investment_vs_return(
            investment_data,
            roi_data
        )
        
        if self.output_format == OutputFormat.HTML:
            return fig.to_html(full_html=False, include_plotlyjs=False)
        else:
            return self._save_chart_image(fig, 'investment_vs_return')
    
    async def _generate_savings_breakdown_chart(self) -> str:
        """Génère la répartition des économies"""
        savings_data = self.dashboard_data.get('savings', {})
        
        fig = self.chart_generator.generate_cost_savings_breakdown(
            savings_data,
            self.time_range.value
        )
        
        if self.output_format == OutputFormat.HTML:
            return fig.to_html(full_html=False, include_plotlyjs=False)
        else:
            return self._save_chart_image(fig, 'savings_breakdown')
    
    async def _generate_executive_summary(self) -> Dict[str, Any]:
        """Génère le résumé exécutif"""
        roi_data = self.dashboard_data.get('roi', [])
        savings_data = self.dashboard_data.get('savings', {})
        
        # Calcul des KPI
        total_savings = sum(
            sum(sub.values()) for sub in savings_data.values()
        ) if savings_data else 0
        
        avg_roi = (
            sum(item['roi_percentage'] for item in roi_data) / len(roi_data)
            if roi_data else 0
        )
        
        # Génération des insights
        insights = await self._generate_insights()
        
        return {
            'period': self.time_range.value,
            'total_savings': total_savings,
            'average_roi': avg_roi,
            'key_achievements': insights.get('achievements', []),
            'recommendations': insights.get('recommendations', []),
            'risk_assessment': insights.get('risks', []),
            'next_quarter_forecast': insights.get('forecast', {}),
            'executive_score': self._calculate_executive_score(insights)
        }
    
    async def _generate_insights(self) -> Dict[str, Any]:
        """Génère des insights basés sur les données"""
        # Analyse des données pour générer des insights
        insights = {
            'achievements': [],
            'recommendations': [],
            'risks': [],
            'forecast': {}
        }
        
        # À implémenter avec logique d'analyse
        return insights
    
    def _calculate_executive_score(self, insights: Dict[str, Any]) -> float:
        """Calcule un score exécutif agrégé"""
        # Logique de calcul de score
        return 85.5  # Score exemple
    
    async def _get_industry_benchmarks(self) -> Dict[str, List[float]]:
        """Récupère les benchmarks industriels"""
        # À implémenter avec source de données benchmarks
        return {
            'saas_average': [120, 125, 130, 135, 140, 145],
            'enterprise_software': [150, 155, 160, 165, 170, 175]
        }
    
    async def _get_performance_benchmarks(self) -> Dict[str, float]:
        """Récupère les benchmarks de performance"""
        # À implémenter avec source de données
        return {
            'cost_savings': 85,
            'security': 92,
            'performance': 88,
            'reliability': 95,
            'compliance': 90
        }
    
    def _save_chart_image(self, fig: Figure, name: str) -> str:
        """
        Sauvegarde un graphique en image.
        
        Args:
            fig: Figure Plotly
            name: Nom du fichier
            
        Returns:
            Chemin du fichier
        """
        import plotly.io as pio
        
        output_path = self.output_dir / f"{name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        if self.output_format == OutputFormat.PDF:
            # Pour PDF, on sauve en PNG puis on intègre
            img_path = f"{output_path}.png"
            pio.write_image(fig, img_path, format='png', width=1200, height=800)
            return img_path
        elif self.output_format == OutputFormat.POWERPOINT:
            img_path = f"{output_path}.png"
            pio.write_image(fig, img_path, format='png', width=1200, height=800)
            return img_path
        else:
            # Pour Excel, on pourrait sauver en base64
            img_bytes = pio.to_image(fig, format='png', width=1200, height=800)
            return base64.b64encode(img_bytes).decode('utf-8')
    
    async def _generate_report(self) -> Path:
        """Génère le rapport dans le format spécifié"""
        if self.output_format == OutputFormat.HTML:
            return await self._generate_html_report()
        elif self.output_format == OutputFormat.PDF:
            return await self._generate_pdf_report()
        elif self.output_format == OutputFormat.EXCEL:
            return await self._generate_excel_report()
        elif self.output_format == OutputFormat.POWERPOINT:
            return await self._generate_powerpoint_report()
        elif self.output_format == OutputFormat.JSON:
            return await self._generate_json_report()
        else:
            raise ValueError(f"Format non supporté: {self.output_format}")
    
    async def _generate_html_report(self) -> Path:
        """Génère un rapport HTML interactif"""
        # Chargement du template
        env = Environment(loader=FileSystemLoader(self.templates_dir))
        template = env.get_template('cfo_dashboard.html')
        
        # Préparation des données
        context = {
            'customer_id': str(self.customer_id),
            'generated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            'time_range': self.time_range.value.capitalize(),
            'executive_summary': await self._generate_executive_summary(),
            'charts': self.charts,
            'brand_colors': self.chart_generator.brand_colors
        }
        
        # Génération du HTML
        html_content = template.render(**context)
        
        # Sauvegarde
        output_path = self.output_dir / f"dashboard_{self.customer_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.html"
        output_path.write_text(html_content)
        
        logger.info(f"Rapport HTML généré: {output_path}")
        return output_path
    
    async def _generate_pdf_report(self) -> Path:
        """Génère un rapport PDF professionnel"""
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
        from reportlab.lib import colors
        
        output_path = self.output_dir / f"dashboard_{self.customer_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        # Création du document
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72
        )
        
        story = []
        styles = getSampleStyleSheet()
        
        # Style personnalisé
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            spaceAfter=30,
            textColor=colors.HexColor('#4A4A4A')
        )
        
        # En-tête
        story.append(Paragraph("CFO Dashboard - MicroAgents Platform", title_style))
        story.append(Paragraph(f"Client: {self.customer_id}", styles['Normal']))
        story.append(Paragraph(f"Période: {self.time_range.value}", styles['Normal']))
        story.append(Paragraph(f"Généré le: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
        story.append(Spacer(1, 20))
        
        # Résumé exécutif
        executive_summary = await self._generate_executive_summary()
        story.append(Paragraph("Résumé Exécutif", styles['Heading2']))
        
        for key, value in executive_summary.items():
            if isinstance(value, (int, float)):
                story.append(Paragraph(f"{key}: {value:,.2f}", styles['Normal']))
            elif isinstance(value, list):
                story.append(Paragraph(f"{key}:", styles['Normal']))
                for item in value:
                    story.append(Paragraph(f"• {item}", styles['Normal']))
            else:
                story.append(Paragraph(f"{key}: {value}", styles['Normal']))
        
        story.append(Spacer(1, 20))
        
        # Graphiques
        story.append(Paragraph("Graphiques", styles['Heading2']))
        
        # Ajout des graphiques en images
        for chart_name, chart_data in self.charts.items():
            if isinstance(chart_data, str) and chart_data.endswith('.png'):
                try:
                    img = Image(chart_data, width=6*inch, height=4*inch)
                    story.append(Paragraph(chart_name.replace('_', ' ').title(), styles['Heading3']))
                    story.append(img)
                    story.append(Spacer(1, 10))
                except Exception as e:
                    logger.error(f"Erreur chargement image {chart_name}: {e}")
        
        # Génération du PDF
        doc.build(story)
        
        logger.info(f"Rapport PDF généré: {output_path}")
        return output_path
    
    async def _generate_excel_report(self) -> Path:
        """Génère un rapport Excel avec graphiques"""
        output_path = self.output_dir / f"dashboard_{self.customer_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        wb = Workbook()
        ws_summary = wb.active
        ws_summary.title = "Résumé"
        
        # Style
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4A90E2", end_color="4A90E2", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        # En-tête
        ws_summary['A1'] = "CFO Dashboard - MicroAgents Platform"
        ws_summary['A1'].font = Font(bold=True, size=16, color="4A4A4A")
        ws_summary.merge_cells('A1:D1')
        
        # Informations
        ws_summary['A3'] = "Client:"
        ws_summary['B3'] = str(self.customer_id)
        ws_summary['A4'] = "Période:"
        ws_summary['B4'] = self.time_range.value
        ws_summary['A5'] = "Généré le:"
        ws_summary['B5'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        
        # Résumé exécutif
        executive_summary = await self._generate_executive_summary()
        row = 7
        
        ws_summary[f'A{row}'] = "Résumé Exécutif"
        ws_summary[f'A{row}'].font = Font(bold=True, size=14)
        row += 1
        
        for key, value in executive_summary.items():
            ws_summary[f'A{row}'] = key.replace('_', ' ').title()
            ws_summary[f'A{row}'].font = Font(bold=True)
            
            if isinstance(value, (int, float)):
                ws_summary[f'B{row}'] = value
                ws_summary[f'B{row}'].number_format = '#,##0.00'
            elif isinstance(value, list):
                ws_summary[f'B{row}'] = ", ".join(str(v) for v in value[:5])
                if len(value) > 5:
                    ws_summary[f'C{row}'] = f"... et {len(value)-5} de plus"
            else:
                ws_summary[f'B{row}'] = str(value)
            
            row += 1
        
        # Feuille de données ROI
        ws_roi = wb.create_sheet("ROI Data")
        roi_data = self.dashboard_data.get('roi', [])
        
        if roi_data:
            headers = list(roi_data[0].keys())
            for col, header in enumerate(headers, 1):
                cell = ws_roi.cell(row=1, column=col, value=header)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_alignment
            
            for row_idx, data in enumerate(roi_data, 2):
                for col_idx, header in enumerate(headers, 1):
                    ws_roi.cell(row=row_idx, column=col_idx, value=data.get(header))
        
        # Sauvegarde
        wb.save(str(output_path))
        
        logger.info(f"Rapport Excel généré: {output_path}")
        return output_path
    
    async def _generate_powerpoint_report(self) -> Path:
        """Génère une présentation PowerPoint"""
        output_path = self.output_dir / f"dashboard_{self.customer_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pptx"
        
        prs = Presentation()
        
        # Slide de titre
        slide_layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(slide_layout)
        title = slide.shapes.title
        subtitle = slide.placeholders[1]
        
        title.text = "CFO Dashboard"
        subtitle.text = f"MicroAgents Platform\nClient: {self.customer_id}\n{datetime.utcnow().strftime('%Y-%m-%d')}"
        
        # Slide résumé exécutif
        slide_layout = prs.slide_layouts[1]
        slide = prs.slides.add_slide(slide_layout)
        title = slide.shapes.title
        title.text = "Résumé Exécutif"
        
        executive_summary = await self._generate_executive_summary()
        content = slide.shapes.placeholders[1]
        
        summary_text = ""
        for key, value in executive_summary.items():
            if isinstance(value, (int, float)):
                summary_text += f"{key.replace('_', ' ').title()}: {value:,.2f}\n"
            elif isinstance(value, list):
                summary_text += f"{key.replace('_', ' ').title()}:\n"
                for item in value[:3]:
                    summary_text += f"• {item}\n"
                if len(value) > 3:
                    summary_text += f"• ... et {len(value)-3} de plus\n"
            else:
                summary_text += f"{key.replace('_', ' ').title()}: {value}\n"
        
        content.text = summary_text
        
        # Slides pour chaque graphique
        for chart_name, chart_path in self.charts.items():
            if isinstance(chart_path, str) and chart_path.endswith('.png'):
                try:
                    slide_layout = prs.slide_layouts[1]
                    slide = prs.slides.add_slide(slide_layout)
                    title = slide.shapes.title
                    title.text = chart_name.replace('_', ' ').title()
                    
                    # Ajout de l'image
                    left = Inches(1)
                    top = Inches(1.5)
                    height = Inches(5)
                    slide.shapes.add_picture(chart_path, left, top, height=height)
                except Exception as e:
                    logger.error(f"Erreur ajout graphique {chart_name}: {e}")
        
        # Slide de conclusion
        slide_layout = prs.slide_layouts[1]
        slide = prs.slides.add_slide(slide_layout)
        title = slide.shapes.title
        title.text = "Recommandations et Next Steps"
        
        content = slide.shapes.placeholders[1]
        content.text = (
            "Recommandations:\n"
            "• Continuer l'optimisation des coûts cloud\n"
            "• Étendre l'automatisation aux nouveaux services\n"
            "• Augmenter la couverture de sécurité\n\n"
            "Next Steps:\n"
            "• Revue trimestrielle des métriques\n"
            "• Audit de sécurité Q2\n"
            "• Formation équipe DevOps"
        )
        
        # Sauvegarde
        prs.save(str(output_path))
        
        logger.info(f"Présentation PowerPoint générée: {output_path}")
        return output_path
    
    async def _generate_json_report(self) -> Path:
        """Génère un rapport JSON structuré"""
        output_path = self.output_dir / f"dashboard_{self.customer_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        
        report_data = {
            'metadata': {
                'customer_id': str(self.customer_id),
                'time_range': self.time_range.value,
                'generated_at': datetime.utcnow().isoformat(),
                'format': 'json'
            },
            'executive_summary': await self._generate_executive_summary(),
            'dashboard_data': self.dashboard_data,
            'charts_metadata': list(self.charts.keys())
        }
        
        # Sauvegarde
        import json
        with open(output_path, 'w') as f:
            json.dump(report_data, f, indent=2, default=str)
        
        logger.info(f"Rapport JSON généré: {output_path}")
        return output_path
    
    def get_dashboard_preview(self) -> Dict[str, Any]:
        """
        Retourne un aperçu du dashboard.
        
        Returns:
            Aperçu avec métadonnées
        """
        return {
            'customer_id': str(self.customer_id),
            'available_charts': list(self.charts.keys()),
            'time_range': self.time_range.value,
            'last_updated': datetime.utcnow().isoformat(),
            'estimated_generation_time': '15 seconds',
            'supported_formats': [fmt.value for fmt in OutputFormat]
        }
    
    async def schedule_regular_report(
        self,
        frequency: str = "weekly",
        recipients: List[str] = None,
        format: OutputFormat = OutputFormat.PDF
    ) -> str:
        """
        Planifie la génération régulière de rapports.
        
        Args:
            frequency: Fréquence (daily, weekly, monthly, quarterly)
            recipients: Liste des destinataires
            format: Format du rapport
            
        Returns:
            ID de la tâche planifiée
        """
        task_id = str(uuid4())
        
        # Configuration de la planification
        schedule_config = {
            'task_id': task_id,
            'customer_id': str(self.customer_id),
            'frequency': frequency,
            'recipients': recipients or [],
            'format': format.value,
            'next_run': self._calculate_next_run(frequency),
            'created_at': datetime.utcnow().isoformat()
        }
        
        # À implémenter: stockage en base de données
        logger.info(f"Rapport planifié: {task_id} pour {frequency}")
        
        return task_id
    
    def _calculate_next_run(self, frequency: str) -> datetime:
        """Calcule la prochaine exécution"""
        now = datetime.utcnow()
        
        if frequency == "daily":
            return now + timedelta(days=1)
        elif frequency == "weekly":
            return now + timedelta(weeks=1)
        elif frequency == "monthly":
            # Premier du mois prochain
            next_month = now.month % 12 + 1
            next_year = now.year + (1 if next_month == 1 else 0)
            return datetime(next_year, next_month, 1, 9, 0)  # 9 AM
        elif frequency == "quarterly":
            # Prochain trimestre
            quarter = (now.month - 1) // 3
            next_quarter = (quarter + 1) % 4
            next_month = next_quarter * 3 + 1
            next_year = now.year + (1 if next_month < now.month else 0)
            return datetime(next_year, next_month, 1, 9, 0)
        else:
            return now + timedelta(days=7)  # Défaut: hebdomadaire


# ==================== UTILITAIRES ====================

class DashboardCache:
    """Cache pour les dashboards générés"""
    
    def __init__(self, max_size: int = 100, ttl_hours: int = 24):
        self.cache = {}
        self.max_size = max_size
        self.ttl = timedelta(hours=ttl_hours)
    
    async def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Récupère un dashboard du cache"""
        if cache_key in self.cache:
            entry = self.cache[cache_key]
            if datetime.utcnow() - entry['timestamp'] < self.ttl:
                return entry['dashboard']
            else:
                del self.cache[cache_key]
        return None
    
    async def set(self, cache_key: str, dashboard: Dict[str, Any]):
        """Stocke un dashboard dans le cache"""
        if len(self.cache) >= self.max_size:
            # Éviction LRU
            oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k]['timestamp'])
            del self.cache[oldest_key]
        
        self.cache[cache_key] = {
            'dashboard': dashboard,
            'timestamp': datetime.utcnow()
        }
    
    def clear(self):
        """Vide le cache"""
        self.cache.clear()


# ==================== FONCTIONS D'EXPORT ====================

async def export_dashboard(
    customer_id: UUID,
    format: OutputFormat = OutputFormat.PDF,
    include_data: bool = True,
    branding_options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Fonction d'export de dashboard.
    
    Args:
        customer_id: ID du client
        format: Format d'export
        include_data: Inclure les données brutes
        branding_options: Options de branding
        
    Returns:
        Informations sur l'export
    """
    generator = CFODashboardGenerator(customer_id, format=format)
    
    # Génération du dashboard
    dashboard_result = await generator.generate_dashboard()
    
    # Application du branding
    if branding_options:
        dashboard_result['branding_applied'] = _apply_branding(
            dashboard_result,
            branding_options
        )
    
    # Ajout des données brutes si demandé
    if include_data:
        dashboard_result['raw_data'] = generator.dashboard_data
    
    return dashboard_result


def _apply_branding(
    dashboard_data: Dict[str, Any],
    branding_options: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Applique le branding aux rapports.
    
    Args:
        dashboard_data: Données du dashboard
        branding_options: Options de branding
        
    Returns:
        Informations de branding appliqué
    """
    applied = {
        'logo_included': branding_options.get('include_logo', True),
        'color_scheme': branding_options.get('color_scheme', 'corporate'),
        'company_name': branding_options.get('company_name', 'MicroAgents'),
        'contact_info': branding_options.get('contact_info', {})
    }
    
    # À implémenter: logique d'application du branding
    return applied


# ==================== EXEMPLE D'UTILISATION ====================

async def main_example():
    """Exemple d'utilisation du CFO Dashboard Generator"""
    import asyncio
    
    # ID client exemple
    customer_id = UUID("123e4567-e89b-12d3-a456-426614174000")
    
    # Création du générateur
    generator = CFODashboardGenerator(
        customer_id=customer_id,
        time_range=TimeRange.QUARTER,
        format=OutputFormat.HTML
    )
    
    # Génération du dashboard
    print("Génération du dashboard CFO...")
    dashboard = await generator.generate_dashboard(
        include_charts=[
            ChartType.LINE,
            ChartType.WATERFALL,
            ChartType.HEATMAP,
            ChartType.RADAR
        ],
        include_benchmarks=True,
        include_forecasts=True
    )
    
    print(f"Dashboard généré: {dashboard['dashboard_id']}")
    print(f"Rapport disponible: {dashboard['report_path']}")
    print(f"Résumé exécutif: {dashboard['executive_summary']}")
    
    # Export PDF
    print("\nExport en PDF...")
    pdf_export = await export_dashboard(
        customer_id,
        format=OutputFormat.PDF,
        include_data=False,
        branding_options={
            'include_logo': True,
            'company_name': 'MicroAgents Inc.',
            'contact_info': {
                'email': 'cfo@microagents.io',
                'phone': '+33 1 23 45 67 89'
            }
        }
    )
    
    print(f"Export PDF généré: {pdf_export['report_path']}")
    
    # Planification de rapport régulier
    task_id = generator.schedule_regular_report(
        frequency="monthly",
        recipients=["cfo@company.com", "finance@company.com"],
        format=OutputFormat.PDF
    )
    
    print(f"\nRapport planifié: {task_id}")


if __name__ == "__main__":
    # Pour tester le module
    asyncio.run(main_example())