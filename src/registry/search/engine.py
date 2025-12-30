"""
Moteur de Recherche Avancé pour le Registre d'Agents MicroAgents

Ce module fournit un moteur de recherche complet avec:
- Recherche plein texte sur documentation et métadonnées
- Filtrage avancé par capacités et caractéristiques
- Tri personnalisable par ROI, popularité, performance
- Recherche à facettes
- Calcul de pertinence avec scoring personnalisé
- Suggestions de complétion automatique
- Correction orthographique
- Expansion de synonymes
- Personnalisation basée sur l'historique
- Intégration ElasticSearch/OpenSearch
"""

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from dataclasses import dataclass, field
from functools import lru_cache

import aiohttp
import numpy as np
from elasticsearch import AsyncElasticsearch, NotFoundError
from elasticsearch.helpers import async_bulk
from fastapi import HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from src.registry.storage.models import (
    Agent, AgentVersion, AgentMetric, AgentCategory, AgentTag,
    SearchHistory, UserPreference, AgentDependency
)
from src.utils.serialization.serializers import json_serializer
from src.core.suites.base import AgentCapability

logger = logging.getLogger(__name__)


class SearchOperator(Enum):
    """Opérateurs de recherche supportés"""
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    NEAR = "NEAR"
    PHRASE = '"'
    WILDCARD = "*"
    FUZZY = "~"


class SortField(Enum):
    """Champs de tri disponibles"""
    ROI = "roi_score"
    POPULARITY = "download_count"
    PERFORMANCE = "avg_execution_time"
    RECENCY = "created_at"
    RELEVANCE = "_score"
    COMPLEXITY = "complexity_score"
    RATING = "average_rating"
    COST_SAVINGS = "estimated_savings"


class SortOrder(Enum):
    """Ordres de tri"""
    ASC = "asc"
    DESC = "desc"


class FacetType(Enum):
    """Types de facettes"""
    CATEGORY = "category"
    TAG = "tags"
    COMPLEXITY = "complexity"
    CLOUD_PROVIDER = "cloud_provider"
    ROI_TIER = "roi_tier"
    STATUS = "status"


@dataclass
class SearchResult:
    """Résultat de recherche"""
    agent_id: str
    agent_name: str
    version: str
    relevance_score: float
    highlights: Dict[str, List[str]]
    metadata: Dict[str, Any]
    explanation: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            'agent_id': self.agent_id,
            'agent_name': self.agent_name,
            'version': self.version,
            'relevance_score': round(self.relevance_score, 4),
            'highlights': self.highlights,
            'metadata': self.metadata,
            'explanation': self.explanation
        }


@dataclass
class FacetCount:
    """Compteur de facette"""
    value: str
    count: int
    selected: bool = False


@dataclass
class SearchResponse:
    """Réponse de recherche complète"""
    results: List[SearchResult]
    total: int
    page: int
    page_size: int
    total_pages: int
    facets: Dict[str, List[FacetCount]]
    suggestions: List[str]
    corrected_query: Optional[str] = None
    search_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    execution_time_ms: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            'search_id': self.search_id,
            'results': [r.to_dict() for r in self.results],
            'total': self.total,
            'page': self.page,
            'page_size': self.page_size,
            'total_pages': self.total_pages,
            'facets': {
                facet: [
                    {'value': f.value, 'count': f.count, 'selected': f.selected}
                    for f in facets
                ]
                for facet, facets in self.facets.items()
            },
            'suggestions': self.suggestions,
            'corrected_query': self.corrected_query,
            'execution_time_ms': round(self.execution_time_ms, 2)
        }


class SearchQuery(BaseModel):
    """Requête de recherche"""
    q: Optional[str] = Field(None, description="Termes de recherche")
    filters: Dict[str, List[str]] = Field(default_factory=dict)
    sort_by: SortField = SortField.RELEVANCE
    sort_order: SortOrder = SortOrder.DESC
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    fields: List[str] = Field(default_factory=lambda: ["name", "description", "documentation"])
    boost_fields: Dict[str, float] = Field(default_factory=dict)
    enable_facets: bool = True
    enable_suggestions: bool = True
    enable_spellcheck: bool = True
    enable_synonyms: bool = True
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    
    @validator('page_size')
    def validate_page_size(cls, v):
        if v > 100:
            raise ValueError("Page size cannot exceed 100")
        return v
    
    @validator('boost_fields')
    def validate_boost_fields(cls, v):
        for field_name, boost in v.items():
            if boost < 0.1 or boost > 10:
                raise ValueError(f"Boost value for {field_name} must be between 0.1 and 10")
        return v


class SearchEngine:
    """Moteur de recherche principal pour les agents"""
    
    def __init__(
        self,
        db_session: Session,
        es_client: Optional[AsyncElasticsearch] = None,
        es_index: str = "microagents-registry"
    ):
        """
        Initialise le moteur de recherche.
        
        Args:
            db_session: Session de base de données
            es_client: Client ElasticSearch optionnel
            es_index: Nom de l'index ElasticSearch
        """
        self.db = db_session
        self.es_client = es_client
        self.es_index = es_index
        self.use_elasticsearch = es_client is not None
        
        # Configuration des poids par défaut pour le scoring
        self.field_weights = {
            "name": 3.0,
            "description": 2.0,
            "documentation": 1.5,
            "tags": 2.5,
            "category": 2.0,
            "capabilities": 1.8,
            "input_types": 1.2,
            "output_types": 1.2
        }
        
        # Dictionnaire de synonymes
        self.synonyms = self._load_synonyms()
        
        # Cache pour les métriques d'agents
        self.agent_metrics_cache = {}
        self.cache_ttl = 300  # 5 minutes
        
        logger.info(f"SearchEngine initialisé (ElasticSearch: {self.use_elasticsearch})")
    
    # ==================== RECHERCHE PRINCIPALE ====================
    
    async def search(self, query: SearchQuery) -> SearchResponse:
        """
        Exécute une recherche d'agents.
        
        Args:
            query: Requête de recherche
            
        Returns:
            Réponse de recherche
        """
        start_time = datetime.utcnow()
        
        try:
            # Correction orthographique
            corrected_query = None
            if query.enable_spellcheck and query.q:
                corrected_query = await self._spell_check(query.q)
                if corrected_query != query.q:
                    logger.info(f"Requête corrigée: '{query.q}' -> '{corrected_query}'")
            
            # Expansion de synonymes
            expanded_query = query.q
            if query.enable_synonyms and query.q:
                expanded_query = self._expand_synonyms(query.q)
            
            # Exécution de la recherche
            if self.use_elasticsearch:
                search_results = await self._elasticsearch_search(query, expanded_query)
            else:
                search_results = await self._database_search(query, expanded_query)
            
            # Personnalisation des résultats
            if query.user_id:
                await self._personalize_results(search_results, query.user_id)
            
            # Calcul des facettes
            facets = {}
            if query.enable_facets:
                facets = await self._compute_facets(search_results, query.filters)
            
            # Génération de suggestions
            suggestions = []
            if query.enable_suggestions and query.q:
                suggestions = await self._generate_suggestions(query.q)
            
            # Création de la réponse
            response = SearchResponse(
                results=search_results,
                total=len(search_results),
                page=query.page,
                page_size=query.page_size,
                total_pages=(len(search_results) + query.page_size - 1) // query.page_size,
                facets=facets,
                suggestions=suggestions,
                corrected_query=corrected_query if corrected_query != query.q else None,
                execution_time_ms=(datetime.utcnow() - start_time).total_seconds() * 1000
            )
            
            # Pagination
            start_idx = (query.page - 1) * query.page_size
            end_idx = start_idx + query.page_size
            response.results = search_results[start_idx:end_idx]
            
            # Enregistrement de l'historique
            await self._record_search_history(query, response)
            
            return response
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Search error: {str(e)}")
    
    async def _elasticsearch_search(
        self,
        query: SearchQuery,
        expanded_query: str
    ) -> List[SearchResult]:
        """Recherche avec ElasticSearch"""
        try:
            # Construction de la requête ES
            es_query = self._build_elasticsearch_query(query, expanded_query)
            
            # Exécution de la recherche
            response = await self.es_client.search(
                index=self.es_index,
                body=es_query,
                from_=(query.page - 1) * query.page_size,
                size=query.page_size
            )
            
            # Traitement des résultats
            results = []
            for hit in response['hits']['hits']:
                result = self._es_hit_to_search_result(hit)
                results.append(result)
            
            return results
            
        except Exception as e:
            logger.error(f"Erreur ElasticSearch: {str(e)}")
            raise
    
    async def _database_search(
        self,
        query: SearchQuery,
        expanded_query: str
    ) -> List[SearchResult]:
        """Recherche avec la base de données SQL"""
        try:
            # Construction de la requête SQL
            db_query = self._build_database_query(query, expanded_query)
            
            # Exécution
            agents = db_query.all()
            
            # Calcul des scores de pertinence
            results = []
            for agent in agents:
                score = self._calculate_relevance_score(agent, expanded_query, query.fields)
                result = self._agent_to_search_result(agent, score)
                results.append(result)
            
            # Tri
            results = self._sort_results(results, query.sort_by, query.sort_order)
            
            return results
            
        except Exception as e:
            logger.error(f"Erreur recherche base de données: {str(e)}")
            raise
    
    # ==================== CONSTRUCTION DE REQUÊTES ====================
    
    def _build_elasticsearch_query(
        self,
        query: SearchQuery,
        expanded_query: str
    ) -> Dict[str, Any]:
        """Construit une requête ElasticSearch"""
        base_query = {
            "query": {
                "bool": {
                    "must": [],
                    "filter": [],
                    "should": [],
                    "must_not": []
                }
            },
            "highlight": {
                "fields": {
                    "name": {"number_of_fragments": 1},
                    "description": {"number_of_fragments": 2},
                    "documentation": {"number_of_fragments": 3, "fragment_size": 150}
                },
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"]
            },
            "aggs": self._build_facet_aggregations() if query.enable_facets else {},
            "explain": True
        }
        
        # Requête plein texte
        if expanded_query:
            bool_query = base_query["query"]["bool"]
            
            # Recherche avec opérateurs booléens
            if self._has_boolean_operators(expanded_query):
                query_string = {
                    "query_string": {
                        "query": expanded_query,
                        "fields": list(query.fields),
                        "default_operator": "AND",
                        "fuzziness": "AUTO",
                        "boost": 1.0
                    }
                }
                bool_query["must"].append(query_string)
            else:
                # Recherche simple avec boosting
                multi_match = {
                    "multi_match": {
                        "query": expanded_query,
                        "fields": [
                            f"{field}^{self.field_weights.get(field, 1.0) * query.boost_fields.get(field, 1.0)}"
                            for field in query.fields
                        ],
                        "type": "best_fields",
                        "fuzziness": "AUTO",
                        "prefix_length": 2
                    }
                }
                bool_query["should"].append(multi_match)
        
        # Filtres
        for field, values in query.filters.items():
            if values:
                if field == "tags":
                    bool_query["filter"].append({
                        "terms": {"tags.keyword": values}
                    })
                elif field == "category":
                    bool_query["filter"].append({
                        "term": {"category": values[0]}
                    })
                elif field == "complexity":
                    bool_query["filter"].append({
                        "range": {"complexity_score": {"gte": float(values[0])}}
                    })
        
        return base_query
    
    def _build_database_query(self, query: SearchQuery, expanded_query: str):
        """Construit une requête SQLAlchemy"""
        from sqlalchemy import func
        
        db_query = self.db.query(Agent).join(AgentVersion).filter(
            AgentVersion.is_latest == True,
            Agent.status == "published"
        )
        
        # Recherche plein texte
        if expanded_query:
            search_terms = self._parse_search_terms(expanded_query)
            
            # Construction des conditions de recherche
            conditions = []
            for term in search_terms:
                if term.startswith('-'):
                    # Exclure le terme
                    term = term[1:]
                    conditions.append(and_(
                        ~Agent.name.ilike(f"%{term}%"),
                        ~Agent.description.ilike(f"%{term}%"),
                        ~Agent.documentation.ilike(f"%{term}%")
                    ))
                else:
                    # Inclure le terme
                    conditions.append(or_(
                        Agent.name.ilike(f"%{term}%"),
                        Agent.description.ilike(f"%{term}%"),
                        Agent.documentation.ilike(f"%{term}%")
                    ))
            
            if conditions:
                db_query = db_query.filter(and_(*conditions))
        
        # Application des filtres
        for field, values in query.filters.items():
            if not values:
                continue
                
            if field == "tags":
                db_query = db_query.join(AgentTag).filter(AgentTag.name.in_(values))
            elif field == "category":
                db_query = db_query.join(AgentCategory).filter(AgentCategory.name.in_(values))
            elif field == "capabilities":
                db_query = db_query.filter(Agent.capabilities.contains(values))
            elif field == "cloud_provider":
                db_query = db_query.filter(Agent.supported_clouds.contains(values))
        
        return db_query
    
    def _build_facet_aggregations(self) -> Dict[str, Any]:
        """Construit les agrégations pour les facettes"""
        return {
            "categories": {
                "terms": {
                    "field": "category.keyword",
                    "size": 20
                }
            },
            "tags": {
                "terms": {
                    "field": "tags.keyword",
                    "size": 50
                }
            },
            "complexity": {
                "range": {
                    "field": "complexity_score",
                    "ranges": [
                        {"to": 3.0, "key": "Beginner"},
                        {"from": 3.0, "to": 7.0, "key": "Intermediate"},
                        {"from": 7.0, "key": "Advanced"}
                    ]
                }
            },
            "roi_tier": {
                "terms": {
                    "field": "roi_tier.keyword",
                    "size": 10
                }
            },
            "cloud_provider": {
                "terms": {
                    "field": "supported_clouds.keyword",
                    "size": 10
                }
            }
        }
    
    # ==================== CALCUL DE PERTINENCE ====================
    
    def _calculate_relevance_score(
        self,
        agent: Agent,
        query: str,
        search_fields: List[str]
    ) -> float:
        """
        Calcule le score de pertinence pour un agent.
        
        Args:
            agent: Agent à scorer
            query: Termes de recherche
            search_fields: Champs à considérer
            
        Returns:
            Score de pertinence
        """
        if not query:
            return 0.0
        
        terms = self._parse_search_terms(query)
        total_score = 0.0
        
        for term in terms:
            if term.startswith('-'):
                continue  # Terme exclu
                
            term_score = 0.0
            
            # Score par champ
            if "name" in search_fields:
                term_score += self._field_score(agent.name, term) * self.field_weights["name"]
            
            if "description" in search_fields:
                term_score += self._field_score(agent.description, term) * self.field_weights["description"]
            
            if "documentation" in search_fields:
                term_score += self._field_score(agent.documentation, term) * self.field_weights["documentation"]
            
            if "tags" in search_fields and agent.tags:
                tag_score = max(
                    self._field_score(tag.name, term)
                    for tag in agent.tags
                )
                term_score += tag_score * self.field_weights["tags"]
            
            total_score += term_score
        
        # Facteurs de boosting supplémentaires
        popularity_boost = np.log1p(agent.download_count or 0) * 0.1
        rating_boost = (agent.average_rating or 0) * 0.2
        roi_boost = (agent.roi_score or 0) * 0.3
        
        final_score = total_score + popularity_boost + rating_boost + roi_boost
        
        return min(final_score, 10.0)  # Normalisation
    
    def _field_score(self, field_content: str, term: str) -> float:
        """Calcule le score pour un champ spécifique"""
        if not field_content:
            return 0.0
        
        content_lower = field_content.lower()
        term_lower = term.lower()
        
        # Score basé sur la fréquence
        frequency = content_lower.count(term_lower) / max(len(content_lower.split()), 1)
        
        # Bonus pour correspondance exacte au début
        position_bonus = 1.0
        if content_lower.startswith(term_lower):
            position_bonus = 2.0
        
        # Bonus pour correspondance complète de mot
        word_bonus = 1.0
        if f" {term_lower} " in f" {content_lower} ":
            word_bonus = 1.5
        
        return frequency * position_bonus * word_bonus
    
    # ==================== TRI DES RÉSULTATS ====================
    
    def _sort_results(
        self,
        results: List[SearchResult],
        sort_by: SortField,
        sort_order: SortOrder
    ) -> List[SearchResult]:
        """Trie les résultats de recherche"""
        reverse = (sort_order == SortOrder.DESC)
        
        if sort_by == SortField.RELEVANCE:
            return sorted(results, key=lambda x: x.relevance_score, reverse=reverse)
        
        elif sort_by == SortField.ROI:
            return sorted(
                results,
                key=lambda x: x.metadata.get('roi_score', 0),
                reverse=reverse
            )
        
        elif sort_by == SortField.POPULARITY:
            return sorted(
                results,
                key=lambda x: x.metadata.get('download_count', 0),
                reverse=reverse
            )
        
        elif sort_by == SortField.PERFORMANCE:
            return sorted(
                results,
                key=lambda x: 1.0 / (x.metadata.get('avg_execution_time', 1) or 1),
                reverse=reverse
            )
        
        elif sort_by == SortField.RECENCY:
            return sorted(
                results,
                key=lambda x: x.metadata.get('created_at', datetime.min),
                reverse=reverse
            )
        
        return results  # Tri par défaut (pertinence)
    
    # ==================== FACETTES ====================
    
    async def _compute_facets(
        self,
        results: List[SearchResult],
        active_filters: Dict[str, List[str]]
    ) -> Dict[str, List[FacetCount]]:
        """Calcule les facettes à partir des résultats"""
        if not results:
            return {}
        
        facets = {
            FacetType.CATEGORY.value: [],
            FacetType.TAG.value: [],
            FacetType.COMPLEXITY.value: [],
            FacetType.CLOUD_PROVIDER.value: [],
            FacetType.ROI_TIER.value: []
        }
        
        # Comptage des catégories
        category_counts = {}
        tag_counts = {}
        cloud_counts = {}
        roi_counts = {}
        complexity_counts = {
            "Beginner": 0,
            "Intermediate": 0,
            "Advanced": 0
        }
        
        for result in results:
            # Catégories
            categories = result.metadata.get('categories', [])
            for category in categories:
                category_counts[category] = category_counts.get(category, 0) + 1
            
            # Tags
            tags = result.metadata.get('tags', [])
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            
            # Cloud providers
            clouds = result.metadata.get('supported_clouds', [])
            for cloud in clouds:
                cloud_counts[cloud] = cloud_counts.get(cloud, 0) + 1
            
            # ROI tiers
            roi_score = result.metadata.get('roi_score', 0)
            roi_tier = self._get_roi_tier(roi_score)
            roi_counts[roi_tier] = roi_counts.get(roi_tier, 0) + 1
            
            # Complexité
            complexity = result.metadata.get('complexity', 'Intermediate')
            complexity_counts[complexity] = complexity_counts.get(complexity, 0) + 1
        
        # Construction des facettes
        facets[FacetType.CATEGORY.value] = [
            FacetCount(
                value=category,
                count=count,
                selected=category in active_filters.get('category', [])
            )
            for category, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True)
            if count > 0
        ][:20]
        
        facets[FacetType.TAG.value] = [
            FacetCount(
                value=tag,
                count=count,
                selected=tag in active_filters.get('tags', [])
            )
            for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
            if count > 0
        ][:30]
        
        facets[FacetType.CLOUD_PROVIDER.value] = [
            FacetCount(
                value=cloud,
                count=count,
                selected=cloud in active_filters.get('cloud_provider', [])
            )
            for cloud, count in sorted(cloud_counts.items(), key=lambda x: x[1], reverse=True)
            if count > 0
        ]
        
        facets[FacetType.ROI_TIER.value] = [
            FacetCount(
                value=tier,
                count=count,
                selected=tier in active_filters.get('roi_tier', [])
            )
            for tier, count in roi_counts.items()
            if count > 0
        ]
        
        facets[FacetType.COMPLEXITY.value] = [
            FacetCount(
                value=complexity,
                count=count,
                selected=str(complexity) in active_filters.get('complexity', [])
            )
            for complexity, count in complexity_counts.items()
            if count > 0
        ]
        
        return facets
    
    def _get_roi_tier(self, roi_score: float) -> str:
        """Détermine le tier ROI basé sur le score"""
        if roi_score >= 8.0:
            return "Excellent (> 8.0)"
        elif roi_score >= 6.0:
            return "Good (6.0-8.0)"
        elif roi_score >= 4.0:
            return "Average (4.0-6.0)"
        else:
            return "Basic (< 4.0)"
    
    # ==================== SUGGESTIONS ET CORRECTIONS ====================
    
    async def _generate_suggestions(self, query: str) -> List[str]:
        """Génère des suggestions de complétion automatique"""
        if len(query) < 2:
            return []
        
        suggestions = set()
        
        # Suggestions basées sur les agents populaires
        popular_agents = self.db.query(Agent).filter(
            Agent.status == "published"
        ).order_by(Agent.download_count.desc()).limit(10).all()
        
        for agent in popular_agents:
            if query.lower() in agent.name.lower():
                suggestions.add(agent.name)
        
        # Suggestions basées sur les tags populaires
        popular_tags = self.db.query(AgentTag).join(Agent).filter(
            Agent.status == "published"
        ).group_by(AgentTag.name).order_by(
            func.count().desc()  # type: ignore
        ).limit(10).all()
        
        for tag in popular_tags:
            if query.lower() in tag.name.lower():
                suggestions.add(tag.name)
        
        # Suggestions basées sur l'historique de recherche
        recent_searches = self.db.query(SearchHistory).filter(
            SearchHistory.query.like(f"%{query}%")
        ).order_by(SearchHistory.searched_at.desc()).limit(5).all()
        
        for history in recent_searches:
            suggestions.add(history.query)
        
        return sorted(list(suggestions))[:5]
    
    async def _spell_check(self, query: str) -> str:
        """Correction orthographique de la requête"""
        if not query or len(query.split()) > 10:
            return query
        
        # Mots courants dans le registre d'agents
        common_words = {
            'cost': ['cost', 'costs', 'costing', 'costed'],
            'security': ['security', 'secure', 'secured'],
            'monitoring': ['monitoring', 'monitor', 'monitored'],
            'optimization': ['optimization', 'optimize', 'optimized'],
            'anomaly': ['anomaly', 'anomalies', 'anomalous'],
            'detection': ['detection', 'detect', 'detected'],
            'aws': ['aws', 'amazon'],
            'azure': ['azure', 'microsoft'],
            'gcp': ['gcp', 'google', 'cloud']
        }
        
        corrected_words = []
        for word in query.split():
            lower_word = word.lower()
            
            # Vérifier si le mot existe dans notre dictionnaire
            for correct_word, variations in common_words.items():
                if lower_word in variations or self._levenshtein_distance(lower_word, correct_word) <= 2:
                    corrected_words.append(correct_word)
                    break
            else:
                corrected_words.append(word)  # Mot inchangé
        
        corrected_query = ' '.join(corrected_words)
        return corrected_query if corrected_query != query else query
    
    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        """Distance de Levenshtein pour la correction orthographique"""
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)
        
        if len(s2) == 0:
            return len(s1)
        
        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        return previous_row[-1]
    
    # ==================== EXPANSION DE SYNONYMES ====================
    
    def _expand_synonyms(self, query: str) -> str:
        """Étend la requête avec des synonymes"""
        if not query:
            return query
        
        expanded_terms = []
        for term in query.split():
            term_lower = term.lower()
            
            # Recherche de synonymes
            synonyms = self.synonyms.get(term_lower, [])
            
            if synonyms and len(term) > 3:  # Éviter les mots courts
                expanded = f"({term} OR {' OR '.join(synonyms)})"
                expanded_terms.append(expanded)
            else:
                expanded_terms.append(term)
        
        return ' '.join(expanded_terms)
    
    def _load_synonyms(self) -> Dict[str, List[str]]:
        """Charge le dictionnaire de synonymes"""
        return {
            "cost": ["price", "expense", "charge", "fee", "expenditure"],
            "optimization": ["improvement", "enhancement", "tuning", "refinement"],
            "security": ["protection", "safety", "defense", "safeguard"],
            "monitoring": ["observation", "surveillance", "tracking", "supervision"],
            "anomaly": ["abnormality", "irregularity", "deviation", "outlier"],
            "detection": ["identification", "discovery", "recognition", "finding"],
            "aws": ["amazon", "amazon web services"],
            "azure": ["microsoft azure"],
            "gcp": ["google cloud", "google cloud platform"],
            "kubernetes": ["k8s", "container orchestration"],
            "docker": ["container", "containerization"],
            "ci/cd": ["continuous integration", "continuous deployment"],
            "devops": ["development operations"],
            "cloud": ["cloud computing", "public cloud"],
            "compliance": ["conformity", "adherence", "observance"],
            "governance": ["management", "control", "oversight"],
            "automation": ["mechanization", "computerization"],
            "analysis": ["examination", "evaluation", "assessment"],
            "report": ["summary", "document", "statement"],
            "alert": ["notification", "warning", "alarm"]
        }
    
    # ==================== PERSONNALISATION ====================
    
    async def _personalize_results(
        self,
        results: List[SearchResult],
        user_id: str
    ) -> None:
        """
        Personnalise les résultats basés sur l'historique utilisateur.
        
        Args:
            results: Résultats à personnaliser
            user_id: ID de l'utilisateur
        """
        if not results:
            return
        
        # Récupération des préférences utilisateur
        preferences = self.db.query(UserPreference).filter_by(
            user_id=user_id
        ).first()
        
        if not preferences:
            return
        
        # Calcul des boosts personnalisés
        category_boosts = preferences.category_boosts or {}
        tag_boosts = preferences.tag_boosts or {}
        
        for result in results:
            personalization_score = 0.0
            
            # Boost par catégorie
            categories = result.metadata.get('categories', [])
            for category in categories:
                boost = category_boosts.get(category, 0.0)
                personalization_score += boost * 0.3
            
            # Boost par tag
            tags = result.metadata.get('tags', [])
            for tag in tags:
                boost = tag_boosts.get(tag, 0.0)
                personalization_score += boost * 0.2
            
            # Application du boost
            result.relevance_score *= (1.0 + personalization_score)
    
    # ==================== INDEXATION ELASTICSEARCH ====================
    
    async def index_agent(self, agent: Agent) -> bool:
        """
        Indexe un agent dans ElasticSearch.
        
        Args:
            agent: Agent à indexer
            
        Returns:
            True si l'indexation a réussi
        """
        if not self.use_elasticsearch:
            return False
        
        try:
            doc = self._agent_to_es_document(agent)
            
            await self.es_client.index(
                index=self.es_index,
                id=agent.id,
                body=doc,
                refresh=True
            )
            
            logger.info(f"Agent indexé: {agent.name} ({agent.id})")
            return True
            
        except Exception as e:
            logger.error(f"Erreur indexation agent {agent.id}: {str(e)}")
            return False
    
    async def bulk_index_agents(self, agents: List[Agent]) -> Dict[str, Any]:
        """
        Indexe plusieurs agents en masse.
        
        Args:
            agents: Liste d'agents à indexer
            
        Returns:
            Statistiques d'indexation
        """
        if not self.use_elasticsearch:
            return {"success": False, "reason": "ElasticSearch not configured"}
        
        try:
            actions = []
            for agent in agents:
                doc = self._agent_to_es_document(agent)
                action = {
                    "_index": self.es_index,
                    "_id": agent.id,
                    "_source": doc
                }
                actions.append(action)
            
            success, failed = await async_bulk(
                self.es_client,
                actions,
                refresh=True
            )
            
            return {
                "success": True,
                "indexed": success,
                "failed": len(failed) if failed else 0,
                "errors": failed
            }
            
        except Exception as e:
            logger.error(f"Erreur indexation en masse: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def delete_agent_index(self, agent_id: str) -> bool:
        """
        Supprime un agent de l'index ElasticSearch.
        
        Args:
            agent_id: ID de l'agent
            
        Returns:
            True si la suppression a réussi
        """
        if not self.use_elasticsearch:
            return False
        
        try:
            await self.es_client.delete(
                index=self.es_index,
                id=agent_id,
                refresh=True
            )
            
            logger.info(f"Agent supprimé de l'index: {agent_id}")
            return True
            
        except NotFoundError:
            logger.warning(f"Agent non trouvé dans l'index: {agent_id}")
            return False
        except Exception as e:
            logger.error(f"Erreur suppression index agent {agent_id}: {str(e)}")
            return False
    
    # ==================== UTILITAIRES ====================
    
    def _agent_to_es_document(self, agent: Agent) -> Dict[str, Any]:
        """Convertit un agent en document ElasticSearch"""
        latest_version = None
        for version in agent.versions:
            if version.is_latest:
                latest_version = version
                break
        
        if not latest_version:
            raise ValueError(f"Aucune version latest pour l'agent {agent.id}")
        
        return {
            "id": agent.id,
            "name": agent.name,
            "description": agent.description,
            "documentation": agent.documentation or "",
            "version": latest_version.version,
            "status": agent.status,
            "created_at": agent.created_at.isoformat() if agent.created_at else None,
            "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
            "categories": [cat.name for cat in agent.categories],
            "tags": [tag.name for tag in agent.tags],
            "supported_clouds": agent.supported_clouds or [],
            "capabilities": agent.capabilities or [],
            "input_types": agent.input_types or [],
            "output_types": agent.output_types or [],
            "complexity": agent.complexity or "Intermediate",
            "complexity_score": float(agent.complexity_score or 5.0),
            "roi_score": float(agent.roi_score or 5.0),
            "roi_tier": self._get_roi_tier(float(agent.roi_score or 5.0)),
            "download_count": agent.download_count or 0,
            "average_rating": float(agent.average_rating or 0.0),
            "review_count": agent.review_count or 0,
            "avg_execution_time": float(agent.avg_execution_time or 0.0),
            "success_rate": float(agent.success_rate or 0.0),
            "author": agent.author or "",
            "organization": agent.organization or "",
            "license": agent.license or "",
            "repository_url": agent.repository_url or "",
            "metadata": agent.metadata or {}
        }
    
    def _es_hit_to_search_result(self, hit: Dict[str, Any]) -> SearchResult:
        """Convertit un hit ElasticSearch en SearchResult"""
        source = hit["_source"]
        highlights = hit.get("highlight", {})
        
        # Extraction des métadonnées
        metadata = {
            "categories": source.get("categories", []),
            "tags": source.get("tags", []),
            "supported_clouds": source.get("supported_clouds", []),
            "capabilities": source.get("capabilities", []),
            "complexity": source.get("complexity", "Intermediate"),
            "complexity_score": source.get("complexity_score", 5.0),
            "roi_score": source.get("roi_score", 5.0),
            "roi_tier": source.get("roi_tier", "Average (4.0-6.0)"),
            "download_count": source.get("download_count", 0),
            "average_rating": source.get("average_rating", 0.0),
            "review_count": source.get("review_count", 0),
            "avg_execution_time": source.get("avg_execution_time", 0.0),
            "success_rate": source.get("success_rate", 0.0),
            "author": source.get("author", ""),
            "organization": source.get("organization", ""),
            "license": source.get("license", ""),
            "repository_url": source.get("repository_url", ""),
            "created_at": datetime.fromisoformat(source["created_at"]) if source.get("created_at") else None,
            "updated_at": datetime.fromisoformat(source["updated_at"]) if source.get("updated_at") else None
        }
        
        return SearchResult(
            agent_id=source["id"],
            agent_name=source["name"],
            version=source.get("version", "1.0.0"),
            relevance_score=hit.get("_score", 0.0),
            highlights=highlights,
            metadata=metadata,
            explanation=hit.get("_explanation")
        )
    
    def _agent_to_search_result(
        self,
        agent: Agent,
        relevance_score: float
    ) -> SearchResult:
        """Convertit un Agent SQLAlchemy en SearchResult"""
        # Extraction des métadonnées
        metadata = {
            "categories": [cat.name for cat in agent.categories],
            "tags": [tag.name for tag in agent.tags],
            "supported_clouds": agent.supported_clouds or [],
            "capabilities": agent.capabilities or [],
            "complexity": agent.complexity or "Intermediate",
            "complexity_score": float(agent.complexity_score or 5.0),
            "roi_score": float(agent.roi_score or 5.0),
            "roi_tier": self._get_roi_tier(float(agent.roi_score or 5.0)),
            "download_count": agent.download_count or 0,
            "average_rating": float(agent.average_rating or 0.0),
            "review_count": agent.review_count or 0,
            "avg_execution_time": float(agent.avg_execution_time or 0.0),
            "success_rate": float(agent.success_rate or 0.0),
            "author": agent.author or "",
            "organization": agent.organization or "",
            "license": agent.license or "",
            "repository_url": agent.repository_url or "",
            "created_at": agent.created_at,
            "updated_at": agent.updated_at
        }
        
        # Génération de highlights (simulé)
        highlights = {}
        
        return SearchResult(
            agent_id=str(agent.id),
            agent_name=agent.name,
            version=next((v.version for v in agent.versions if v.is_latest), "1.0.0"),
            relevance_score=relevance_score,
            highlights=highlights,
            metadata=metadata
        )
    
    def _parse_search_terms(self, query: str) -> List[str]:
        """Parse les termes de recherche en tokens"""
        # Suppression des guillemets et séparation
        query = query.strip('"')
        tokens = []
        
        # Gestion des opérateurs booléens
        in_phrase = False
        current_token = ""
        
        for char in query:
            if char == '"':
                in_phrase = not in_phrase
                if not in_phrase and current_token:
                    tokens.append(current_token)
                    current_token = ""
            elif char == ' ' and not in_phrase:
                if current_token:
                    tokens.append(current_token)
                    current_token = ""
            else:
                current_token += char
        
        if current_token:
            tokens.append(current_token)
        
        return tokens
    
    def _has_boolean_operators(self, query: str) -> bool:
        """Vérifie si la requête contient des opérateurs booléens"""
        operators = ["AND", "OR", "NOT", "NEAR", "(", ")", '"']
        return any(op in query.upper() for op in operators)
    
    async def _record_search_history(
        self,
        query: SearchQuery,
        response: SearchResponse
    ) -> None:
        """Enregistre l'historique de recherche"""
        try:
            if query.user_id:
                history = SearchHistory(
                    user_id=query.user_id,
                    session_id=query.session_id or str(uuid.uuid4()),
                    query=query.q or "",
                    filters=json_serializer(query.filters),
                    results_count=response.total,
                    execution_time_ms=response.execution_time_ms,
                    searched_at=datetime.utcnow()
                )
                
                self.db.add(history)
                self.db.commit()
                
                # Nettoyage de l'ancien historique
                cutoff_date = datetime.utcnow() - timedelta(days=90)
                self.db.query(SearchHistory).filter(
                    SearchHistory.searched_at < cutoff_date
                ).delete()
                self.db.commit()
                
        except Exception as e:
            logger.error(f"Erreur enregistrement historique: {str(e)}")
            self.db.rollback()
    
    # ==================== API AVANCÉE ====================
    
    async def advanced_search(
        self,
        query_string: str,
        field_queries: Dict[str, str] = None,
        range_filters: Dict[str, Dict[str, Any]] = None,
        geo_filters: Dict[str, Any] = None,
        script_scoring: Dict[str, Any] = None
    ) -> SearchResponse:
        """
        Recherche avancée avec fonctionnalités ElasticSearch complètes.
        
        Args:
            query_string: Requête en syntaxe Lucene
            field_queries: Requêtes spécifiques par champ
            range_filters: Filtres de plage
            geo_filters: Filtres géographiques
            script_scoring: Scoring personnalisé par script
            
        Returns:
            Résultats de recherche
        """
        if not self.use_elasticsearch:
            raise HTTPException(
                status_code=400,
                detail="Advanced search requires ElasticSearch"
            )
        
        try:
            es_query = {
                "query": {
                    "bool": {
                        "must": [],
                        "filter": [],
                        "should": [],
                        "must_not": []
                    }
                }
            }
            
            # Query string
            if query_string:
                es_query["query"]["bool"]["must"].append({
                    "query_string": {
                        "query": query_string,
                        "default_field": "_all",
                        "allow_leading_wildcard": True,
                        "fuzziness": "AUTO",
                        "fuzzy_prefix_length": 2,
                        "fuzzy_max_expansions": 50,
                        "phrase_slop": 2,
                        "analyze_wildcard": True,
                        "auto_generate_synonyms_phrase_query": True
                    }
                })
            
            # Field queries
            if field_queries:
                for field, value in field_queries.items():
                    es_query["query"]["bool"]["must"].append({
                        "match": {field: value}
                    })
            
            # Range filters
            if range_filters:
                for field, ranges in range_filters.items():
                    es_query["query"]["bool"]["filter"].append({
                        "range": {field: ranges}
                    })
            
            # Geo filters
            if geo_filters:
                es_query["query"]["bool"]["filter"].append({
                    "geo_distance": geo_filters
                })
            
            # Script scoring
            if script_scoring:
                es_query["query"] = {
                    "script_score": {
                        "query": es_query["query"],
                        "script": {
                            "source": script_scoring.get("source", "1.0"),
                            "params": script_scoring.get("params", {})
                        }
                    }
                }
            
            # Exécution
            response = await self.es_client.search(
                index=self.es_index,
                body=es_query,
                size=100
            )
            
            # Conversion des résultats
            results = []
            for hit in response['hits']['hits']:
                results.append(self._es_hit_to_search_result(hit))
            
            return SearchResponse(
                results=results,
                total=response['hits']['total']['value'],
                page=1,
                page_size=len(results),
                total_pages=1,
                facets={},
                suggestions=[],
                execution_time_ms=response.get('took', 0)
            )
            
        except Exception as e:
            logger.error(f"Erreur recherche avancée: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    async def auto_complete(self, prefix: str, field: str = "name") -> List[str]:
        """
        Suggestions de complétion automatique.
        
        Args:
            prefix: Préfixe à compléter
            field: Champ à utiliser
            
        Returns:
            Liste de suggestions
        """
        if len(prefix) < 2:
            return []
        
        try:
            if self.use_elasticsearch:
                # Utilisation de la completion suggester d'ES
                response = await self.es_client.search(
                    index=self.es_index,
                    body={
                        "suggest": {
                            "agent-suggest": {
                                "prefix": prefix,
                                "completion": {
                                    "field": f"{field}.suggest",
                                    "fuzzy": {
                                        "fuzziness": 1
                                    },
                                    "size": 10
                                }
                            }
                        }
                    }
                )
                
                suggestions = []
                for option in response['suggest']['agent-suggest'][0]['options']:
                    suggestions.append(option['text'])
                
                return suggestions
            else:
                # Fallback sur la base de données
                results = self.db.query(Agent).filter(
                    getattr(Agent, field).ilike(f"{prefix}%"),
                    Agent.status == "published"
                ).order_by(Agent.download_count.desc()).limit(10).all()
                
                return [getattr(agent, field) for agent in results]
                
        except Exception as e:
            logger.error(f"Erreur auto-complétion: {str(e)}")
            return []
    
    async def export_results(
        self,
        search_id: str,
        format: str = "json"
    ) -> Union[str, bytes]:
        """
        Exporte les résultats de recherche.
        
        Args:
            search_id: ID de la recherche
            format: Format d'export (json, csv, excel)
            
        Returns:
            Données exportées
        """
        # Implémentation simplifiée - à compléter selon les besoins
        if format == "json":
            return json_serializer({"search_id": search_id, "export": "not implemented"})
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Format {format} non supporté"
            )


# Singleton pour le moteur de recherche
_search_engine_instance = None

def get_search_engine(
    db_session: Session = None,
    es_client: AsyncElasticsearch = None
) -> SearchEngine:
    """
    Retourne l'instance singleton du moteur de recherche.
    
    Args:
        db_session: Session de base de données
        es_client: Client ElasticSearch
        
    Returns:
        Instance SearchEngine
    """
    global _search_engine_instance
    
    if _search_engine_instance is None:
        if db_session is None:
            raise ValueError("db_session est requis pour initialiser SearchEngine")
        
        _search_engine_instance = SearchEngine(
            db_session=db_session,
            es_client=es_client
        )
    
    return _search_engine_instance


# Exemple d'utilisation
if __name__ == "__main__":
    # Configuration
    import asyncio
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    async def demo():
        # Initialisation
        engine = create_engine("sqlite:///:memory:")
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        
        # Création du moteur de recherche
        search_engine = SearchEngine(db_session=db)
        
        # Exemple de recherche
        query = SearchQuery(
            q="cost optimization aws",
            filters={"tags": ["aws", "cost-saving"]},
            sort_by=SortField.ROI,
            sort_order=SortOrder.DESC,
            page=1,
            page_size=10
        )
        
        # Exécution de la recherche
        results = await search_engine.search(query)
        print(f"Trouvé {results.total} agents")
        
        for result in results.results[:3]:
            print(f"- {result.agent_name} (score: {result.relevance_score})")
    
    asyncio.run(demo())