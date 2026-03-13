"""
Endpoints REST pour la gestion des agents.
Gère l'exécution, le suivi, le monitoring et l'administration des 1400 micro-agents.
"""

import asyncio
import uuid
import json
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, AsyncGenerator
from enum import Enum
from collections import defaultdict

from fastapi import (
    APIRouter, 
    Depends, 
    HTTPException, 
    Query, 
    Body, 
    BackgroundTasks,
    Header,
    Response,
    status
)
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.websockets import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, validator, ValidationError
from redis.asyncio import Redis
import orjson

from microagents.core.base.agent import AgentResult, ExecutionContext
from microagents.core.base.registry import AgentRegistry
from microagents.api.dependencies.deps import (
    get_current_user,
    get_agent_registry,
    get_redis_client,
    get_db_session,
    rate_limit
)
from microagents.api.middleware.auth import require_permission
from microagents.api.middleware.analytics import track_api_usage
from microagents.api.middleware.caching import cache_response, invalidate_cache
from microagents.api.middleware.logging import log_operation
from microagents.core.agents.validators.configuration_validator import ConfigurationValidator
from microagents.core.agents.validators.security_validator import SecurityValidator
from microagents.monitoring.metrics.collector import MetricsCollector
from microagents.utils.concurrency.manager import ConcurrencyManager

# Modèles Pydantic
class ExecutionPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"

class AgentConfigUpdate(BaseModel):
    """Modèle pour la mise à jour de configuration d'agent."""
    config: Dict[str, Any] = Field(..., description="Configuration mise à jour")
    version: str = Field("1.0.0", description="Version de la configuration")
    notes: Optional[str] = Field(None, description="Notes sur la modification")

class AgentExecutionRequest(BaseModel):
    """Modèle pour l'exécution d'un agent."""
    agent_id: str = Field(..., description="ID de l'agent à exécuter")
    context: Dict[str, Any] = Field(default_factory=dict, description="Contexte d'exécution")
    priority: ExecutionPriority = Field(ExecutionPriority.NORMAL, description="Priorité d'exécution")
    timeout: int = Field(300, ge=1, le=3600, description="Timeout en secondes")
    stream_output: bool = Field(False, description="Streamer la sortie en temps réel")
    tags: List[str] = Field(default_factory=list, description="Tags pour le suivi")

class BatchExecutionRequest(BaseModel):
    """Modèle pour l'exécution par lot d'agents."""
    executions: List[AgentExecutionRequest] = Field(..., min_items=1, max_items=100)
    parallel: bool = Field(True, description="Exécution parallèle")
    fail_fast: bool = Field(False, description="Échoue rapidement à la première erreur")
    batch_id: str = Field(default_factory=lambda: f"batch-{uuid.uuid4().hex[:8]}")

class AgentRegistration(BaseModel):
    """Modèle pour l'enregistrement d'un nouvel agent."""
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=500)
    agent_type: str = Field(..., description="Type d'agent (detector, optimizer, etc.)")
    config_schema: Dict[str, Any] = Field(..., description="Schéma de configuration JSON")
    default_config: Dict[str, Any] = Field(default_factory=dict)
    capabilities: List[str] = Field(default_factory=list)
    requirements: Dict[str, str] = Field(default_factory=dict)
    category: str = Field(..., description="Catégorie métier (cost, security, incident)")

class AgentFilter(BaseModel):
    """Modèle pour le filtrage des agents."""
    agent_type: Optional[str] = None
    category: Optional[str] = None
    capability: Optional[str] = None
    tags: Optional[List[str]] = None
    min_performance: Optional[float] = Field(None, ge=0.0, le=1.0)
    status: Optional[str] = Field(None, regex="^(active|inactive|deprecated)$")
    search_query: Optional[str] = None

class ABTestRequest(BaseModel):
    """Modèle pour les tests A/B d'agents."""
    variant_a: AgentExecutionRequest
    variant_b: AgentExecutionRequest
    test_name: str = Field(..., description="Nom du test A/B")
    sample_size: int = Field(100, ge=10, le=10000)
    metrics: List[str] = Field(["accuracy", "latency", "cost"])
    target_improvement: float = Field(0.1, ge=0.01, le=1.0)

class AgentTemplate(BaseModel):
    """Modèle pour les templates d'agent."""
    name: str
    description: str
    agent_type: str
    template_config: Dict[str, Any]
    variables: Dict[str, str] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)

class AgentExportRequest(BaseModel):
    """Modèle pour l'export d'agents."""
    agent_ids: List[str]
    include_config: bool = True
    include_history: bool = False
    include_metrics: bool = False
    format: str = Field("json", regex="^(json|yaml|csv)$")

# Router principal
router = APIRouter(prefix="/agents", tags=["agents"])

# Cache pour les résultats d'exécution
execution_cache: Dict[str, Dict[str, Any]] = {}

# File d'attente des exécutions prioritaires
execution_queue: Dict[ExecutionPriority, List[Dict[str, Any]]] = {
    priority: [] for priority in ExecutionPriority
}

# ============================================================================
# Endpoints d'exécution
# ============================================================================

@router.post("/execute", response_model=AgentResult)
@track_api_usage("agent_execution")
@log_operation("agent_execute")
@require_permission("agents:execute")
async def execute_agent(
    request: AgentExecutionRequest,
    background_tasks: BackgroundTasks,
    registry: AgentRegistry = Depends(get_agent_registry),
    redis: Redis = Depends(get_redis_client),
    x_request_id: Optional[str] = Header(None),
    user: dict = Depends(get_current_user)
) -> AgentResult:
    """
    Exécute un agent unique avec contexte.
    
    Args:
        request: Requête d'exécution d'agent
        background_tasks: Tâches en arrière-plan FastAPI
        registry: Registry des agents
        redis: Client Redis
        x_request_id: ID de requête pour le tracking
        user: Utilisateur authentifié
    
    Returns:
        AgentResult: Résultat de l'exécution
    """
    execution_id = f"exec-{uuid.uuid4().hex}"
    
    # Validation de sécurité
    security_validator = SecurityValidator()
    if not await security_validator.validate_execution_context(request.context):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Contexte d'exécution non sécurisé"
        )
    
    # Création du contexte d'exécution
    context = ExecutionContext(
        execution_id=execution_id,
        user_id=user["id"],
        tenant_id=user.get("tenant_id"),
        request_id=x_request_id,
        priority=request.priority,
        timeout=request.timeout,
        tags=request.tags,
        metadata={
            "endpoint": "execute_agent",
            "user_email": user.get("email"),
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    # Track coût et métriques
    metrics = MetricsCollector()
    start_time = time.time()
    
    try:
        # Récupération de l'agent
        agent = await registry.get_agent(request.agent_id)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent {request.agent_id} non trouvé"
            )
        
        # Vérification des permissions
        if not await _check_agent_permissions(agent, user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permissions insuffisantes pour exécuter cet agent"
            )
        
        # Ajout à la file d'attente si priorité haute
        if request.priority in [ExecutionPriority.HIGH, ExecutionPriority.CRITICAL]:
            await _add_to_priority_queue(request, execution_id, context)
        
        # Exécution
        if request.stream_output:
            # Retourne immédiatement pour streaming
            background_tasks.add_task(
                _execute_agent_async,
                agent,
                request.context,
                context,
                execution_id,
                redis
            )
            
            return AgentResult(
                execution_id=execution_id,
                status="queued",
                message="Exécution démarrée en streaming",
                data={"stream_url": f"/agents/stream/{execution_id}"},
                metadata={"streaming": True}
            )
        
        # Exécution synchrone
        result = await agent.execute(request.context, context)
        
        # Calcul des métriques
        execution_time = time.time() - start_time
        await metrics.track_execution(
            agent_id=request.agent_id,
            execution_id=execution_id,
            duration=execution_time,
            success=result.success,
            user_id=user["id"]
        )
        
        # Cache le résultat
        cache_key = f"agent_result:{execution_id}"
        await redis.setex(
            cache_key,
            300,  # 5 minutes
            orjson.dumps(result.dict())
        )
        
        # Audit log
        await _log_audit(
            action="agent_execution",
            user_id=user["id"],
            resource_id=request.agent_id,
            details={
                "execution_id": execution_id,
                "duration": execution_time,
                "success": result.success
            }
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        # Track erreur
        await metrics.track_error(
            agent_id=request.agent_id,
            execution_id=execution_id,
            error_type=type(e).__name__,
            user_id=user["id"]
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de l'exécution: {str(e)}"
        )


@router.post("/execute/batch", response_model=Dict[str, Any])
@track_api_usage("batch_execution")
@log_operation("batch_execute")
@require_permission("agents:execute:batch")
async def execute_batch(
    request: BatchExecutionRequest,
    registry: AgentRegistry = Depends(get_agent_registry),
    redis: Redis = Depends(get_redis_client),
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Exécute plusieurs agents en batch.
    
    Args:
        request: Requête d'exécution batch
        registry: Registry des agents
        redis: Client Redis
        user: Utilisateur authentifié
    
    Returns:
        Dict: Résultats du batch
    """
    batch_id = request.batch_id
    results = []
    errors = []
    
    # Gestionnaire de concurrence
    concurrency_manager = ConcurrencyManager(max_concurrent=10)
    
    async def _execute_single(req: AgentExecutionRequest) -> Optional[Dict[str, Any]]:
        """Exécute un agent individuel."""
        try:
            agent = await registry.get_agent(req.agent_id)
            if not agent:
                return {
                    "agent_id": req.agent_id,
                    "status": "error",
                    "error": "Agent non trouvé"
                }
            
            context = ExecutionContext(
                execution_id=f"batch-{batch_id}-{uuid.uuid4().hex[:8]}",
                user_id=user["id"],
                priority=req.priority,
                tags=req.tags + ["batch", batch_id]
            )
            
            result = await agent.execute(req.context, context)
            return {
                "agent_id": req.agent_id,
                "status": "success",
                "result": result.dict()
            }
        except Exception as e:
            if request.fail_fast:
                raise
            return {
                "agent_id": req.agent_id,
                "status": "error",
                "error": str(e)
            }
    
    try:
        # Exécution parallèle ou séquentielle
        if request.parallel:
            tasks = [_execute_single(req) for req in request.executions]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            for req in request.executions:
                result = await _execute_single(req)
                results.append(result)
                if request.fail_fast and result.get("status") == "error":
                    break
        
        # Calcul des statistiques
        stats = _calculate_batch_stats(results)
        
        # Stockage des résultats
        batch_result = {
            "batch_id": batch_id,
            "timestamp": datetime.utcnow().isoformat(),
            "total_executions": len(request.executions),
            "successful": stats["successful"],
            "failed": stats["failed"],
            "results": results,
            "summary": stats
        }
        
        # Cache
        cache_key = f"batch_result:{batch_id}"
        await redis.setex(
            cache_key,
            3600,  # 1 heure
            orjson.dumps(batch_result)
        )
        
        # Webhook notification
        if stats["failed"] == 0 or not request.fail_fast:
            await _send_webhook_notification(
                "batch_completed",
                {"batch_id": batch_id, "stats": stats}
            )
        
        return batch_result
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur batch: {str(e)}"
        )


@router.get("/stream/{execution_id}")
async def stream_execution_result(
    execution_id: str,
    registry: AgentRegistry = Depends(get_agent_registry),
    redis: Redis = Depends(get_redis_client)
) -> StreamingResponse:
    """
    Stream les résultats d'une exécution en temps réel.
    
    Args:
        execution_id: ID de l'exécution
        registry: Registry des agents
        redis: Client Redis
    
    Returns:
        StreamingResponse: Flux de données
    """
    async def event_generator():
        """Générateur d'événements SSE."""
        cache_key = f"agent_stream:{execution_id}"
        
        while True:
            # Vérifie le cache Redis pour les nouvelles données
            stream_data = await redis.get(cache_key)
            
            if stream_data:
                data = orjson.loads(stream_data)
                yield f"data: {json.dumps(data)}\n\n"
                
                if data.get("status") in ["completed", "failed"]:
                    break
            
            await asyncio.sleep(0.1)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )


# ============================================================================
# Endpoints de recherche et filtrage
# ============================================================================

@router.post("/search", response_model=Dict[str, Any])
@cache_response(ttl=60)  # Cache 1 minute
async def search_agents(
    filter: AgentFilter = Body(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    registry: AgentRegistry = Depends(get_agent_registry),
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Recherche et filtre les agents.
    
    Args:
        filter: Critères de filtrage
        page: Numéro de page
        page_size: Taille de la page
        registry: Registry des agents
        user: Utilisateur authentifié
    
    Returns:
        Dict: Résultats paginés
    """
    try:
        # Construction de la requête
        query_filters = {}
        
        if filter.agent_type:
            query_filters["agent_type"] = filter.agent_type
        if filter.category:
            query_filters["category"] = filter.category
        if filter.capability:
            query_filters["capabilities"] = {"$in": [filter.capability]}
        if filter.tags:
            query_filters["tags"] = {"$all": filter.tags}
        if filter.status:
            query_filters["status"] = filter.status
        
        # Recherche textuelle
        if filter.search_query:
            query_filters["$text"] = {"$search": filter.search_query}
        
        # Récupération des agents
        agents = await registry.search_agents(
            filters=query_filters,
            skip=(page - 1) * page_size,
            limit=page_size
        )
        
        # Filtrage par performance
        if filter.min_performance:
            agents = [
                agent for agent in agents
                if await _get_agent_performance(agent.id) >= filter.min_performance
            ]
        
        # Statistiques
        total_count = await registry.count_agents(query_filters)
        
        return {
            "agents": [agent.to_dict() for agent in agents],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total_count,
                "total_pages": (total_count + page_size - 1) // page_size
            },
            "filters": filter.dict(exclude_none=True)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de recherche: {str(e)}"
        )


@router.get("/recommendations", response_model=List[Dict[str, Any]])
async def get_recommendations(
    context: Dict[str, Any] = Body(...),
    limit: int = Query(5, ge=1, le=20),
    registry: AgentRegistry = Depends(get_agent_registry),
    user: dict = Depends(get_current_user)
) -> List[Dict[str, Any]]:
    """
    Recommande des agents basés sur le contexte.
    
    Args:
        context: Contexte pour les recommandations
        limit: Nombre maximum de recommandations
        registry: Registry des agents
        user: Utilisateur authentifié
    
    Returns:
        List: Agents recommandés
    """
    try:
        # Analyse du contexte pour déterminer les besoins
        required_capabilities = _analyze_context_for_capabilities(context)
        
        # Recherche des agents correspondants
        agents = await registry.find_agents_by_capabilities(
            capabilities=required_capabilities,
            limit=limit
        )
        
        # Tri par performance
        scored_agents = []
        for agent in agents:
            score = await _calculate_recommendation_score(agent, context, user)
            scored_agents.append({
                "agent": agent.to_dict(),
                "score": score,
                "reasons": _get_recommendation_reasons(agent, context)
            })
        
        # Tri décroissant par score
        scored_agents.sort(key=lambda x: x["score"], reverse=True)
        
        return scored_agents[:limit]
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de recommandation: {str(e)}"
        )


# ============================================================================
# Endpoints d'enregistrement et configuration
# ============================================================================

@router.post("/register", status_code=status.HTTP_201_CREATED)
@require_permission("agents:register")
async def register_agent(
    registration: AgentRegistration,
    registry: AgentRegistry = Depends(get_agent_registry),
    user: dict = Depends(get_current_user),
    db = Depends(get_db_session)
) -> Dict[str, Any]:
    """
    Enregistre un nouvel agent.
    
    Args:
        registration: Données d'enregistrement
        registry: Registry des agents
        user: Utilisateur authentifié
        db: Session de base de données
    
    Returns:
        Dict: Agent enregistré
    """
    try:
        # Validation du schéma de configuration
        config_validator = ConfigurationValidator()
        validation_result = await config_validator.validate_schema(
            registration.config_schema
        )
        
        if not validation_result.valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Schéma de configuration invalide",
                    "errors": validation_result.errors
                }
            )
        
        # Vérification des doublons
        existing_agent = await registry.get_agent_by_name(registration.name)
        if existing_agent:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Un agent avec le nom '{registration.name}' existe déjà"
            )
        
        # Création de l'agent
        agent_id = f"agent-{uuid.uuid4().hex[:12]}"
        
        agent_data = {
            "id": agent_id,
            "name": registration.name,
            "description": registration.description,
            "agent_type": registration.agent_type,
            "config_schema": registration.config_schema,
            "default_config": registration.default_config,
            "capabilities": registration.capabilities,
            "requirements": registration.requirements,
            "category": registration.category,
            "status": "active",
            "created_by": user["id"],
            "created_at": datetime.utcnow(),
            "version": "1.0.0",
            "tags": []
        }
        
        # Enregistrement
        agent = await registry.register_agent(agent_data)
        
        # Audit log
        await _log_audit(
            action="agent_registration",
            user_id=user["id"],
            resource_id=agent_id,
            details={
                "agent_name": registration.name,
                "agent_type": registration.agent_type,
                "category": registration.category
            }
        )
        
        return {
            "message": "Agent enregistré avec succès",
            "agent_id": agent_id,
            "agent": agent.to_dict()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur d'enregistrement: {str(e)}"
        )


@router.put("/{agent_id}/config")
@require_permission("agents:configure")
async def update_agent_config(
    agent_id: str,
    config_update: AgentConfigUpdate,
    registry: AgentRegistry = Depends(get_agent_registry),
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Met à jour la configuration d'un agent.
    
    Args:
        agent_id: ID de l'agent
        config_update: Configuration mise à jour
        registry: Registry des agents
        user: Utilisateur authentifié
    
    Returns:
        Dict: Résultat de la mise à jour
    """
    try:
        # Récupération de l'agent
        agent = await registry.get_agent(agent_id)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent non trouvé"
            )
        
        # Validation de la configuration
        config_validator = ConfigurationValidator()
        validation_result = await config_validator.validate_config(
            config_update.config,
            agent.config_schema
        )
        
        if not validation_result.valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Configuration invalide",
                    "errors": validation_result.errors
                }
            )
        
        # Mise à jour
        updated_agent = await registry.update_agent_config(
            agent_id=agent_id,
            new_config=config_update.config,
            version=config_update.version,
            updated_by=user["id"]
        )
        
        # Invalidation du cache
        await invalidate_cache(f"agent:{agent_id}")
        
        # Historique des changements
        await _log_config_change(
            agent_id=agent_id,
            old_config=agent.config,
            new_config=config_update.config,
            user_id=user["id"],
            notes=config_update.notes
        )
        
        return {
            "message": "Configuration mise à jour",
            "agent_id": agent_id,
            "version": config_update.version,
            "updated_at": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de mise à jour: {str(e)}"
        )


# ============================================================================
# Endpoints d'historique et métriques
# ============================================================================

@router.get("/{agent_id}/history")
async def get_execution_history(
    agent_id: str,
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    status_filter: Optional[str] = Query(None, regex="^(success|failed|all)$"),
    redis: Redis = Depends(get_redis_client),
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Récupère l'historique d'exécution d'un agent.
    
    Args:
        agent_id: ID de l'agent
        start_date: Date de début
        end_date: Date de fin
        limit: Limite de résultats
        status_filter: Filtre par statut
        redis: Client Redis
        user: Utilisateur authentifié
    
    Returns:
        Dict: Historique d'exécution
    """
    try:
        # Construction de la requête
        query = {"agent_id": agent_id}
        
        if start_date:
            query["timestamp"] = {"$gte": start_date}
        if end_date:
            if "timestamp" in query:
                query["timestamp"]["$lte"] = end_date
            else:
                query["timestamp"] = {"$lte": end_date}
        
        if status_filter and status_filter != "all":
            query["status"] = status_filter
        
        # Récupération depuis Redis/DB
        history_key = f"agent_history:{agent_id}"
        
        # Pour la démo, simulation de données
        # En production, utiliser une base de données temporelle
        history_data = await redis.zrangebyscore(
            history_key,
            min=start_date.timestamp() if start_date else "-inf",
            max=end_date.timestamp() if end_date else "+inf",
            start=0,
            num=limit,
            withscores=True
        )
        
        executions = []
        for data, score in history_data:
            execution = orjson.loads(data)
            executions.append(execution)
        
        # Statistiques
        stats = _calculate_history_statistics(executions)
        
        return {
            "agent_id": agent_id,
            "executions": executions[:limit],
            "statistics": stats,
            "time_range": {
                "start": start_date.isoformat() if start_date else None,
                "end": end_date.isoformat() if end_date else None
            },
            "total": len(executions)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de récupération d'historique: {str(e)}"
        )


@router.get("/{agent_id}/metrics")
@cache_response(ttl=30)
async def get_agent_metrics(
    agent_id: str,
    period: str = Query("24h", regex="^(1h|24h|7d|30d|90d)$"),
    metrics_collector: MetricsCollector = Depends(),
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Récupère les métriques de performance d'un agent.
    
    Args:
        agent_id: ID de l'agent
        period: Période d'analyse
        metrics_collector: Collecteur de métriques
        user: Utilisateur authentifié
    
    Returns:
        Dict: Métriques de performance
    """
    try:
        # Conversion de la période
        period_map = {
            "1h": timedelta(hours=1),
            "24h": timedelta(days=1),
            "7d": timedelta(days=7),
            "30d": timedelta(days=30),
            "90d": timedelta(days=90)
        }
        
        time_delta = period_map[period]
        start_time = datetime.utcnow() - time_delta
        
        # Récupération des métriques
        metrics = await metrics_collector.get_agent_metrics(
            agent_id=agent_id,
            start_time=start_time,
            end_time=datetime.utcnow()
        )
        
        # Calcul des tendances
        trends = await _calculate_performance_trends(agent_id, period)
        
        # Comparaison avec les pairs
        benchmarks = await _get_agent_benchmarks(agent_id)
        
        return {
            "agent_id": agent_id,
            "period": period,
            "metrics": metrics,
            "trends": trends,
            "benchmarks": benchmarks,
            "summary": _generate_metrics_summary(metrics)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de récupération de métriques: {str(e)}"
        )


# ============================================================================
# Endpoints A/B Testing
# ============================================================================

@router.post("/ab-test")
@require_permission("agents:abtest")
async def run_ab_test(
    test_request: A/BTestRequest,
    registry: AgentRegistry = Depends(get_agent_registry),
    redis: Redis = Depends(get_redis_client),
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Exécute un test A/B entre deux agents.
    
    Args:
        test_request: Configuration du test A/B
        registry: Registry des agents
        redis: Client Redis
        user: Utilisateur authentifié
    
    Returns:
        Dict: Résultats du test A/B
    """
    try:
        test_id = f"abtest-{uuid.uuid4().hex[:8]}"
        
        # Validation des agents
        agent_a = await registry.get_agent(test_request.variant_a.agent_id)
        agent_b = await registry.get_agent(test_request.variant_b.agent_id)
        
        if not agent_a or not agent_b:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Un ou plusieurs agents non trouvés"
            )
        
        # Exécution des tests
        results_a = []
        results_b = []
        
        for i in range(test_request.sample_size):
            # Exécution aléatoire alternée
            if i % 2 == 0:
                result = await _execute_test_variant(
                    agent_a, test_request.variant_a, f"{test_id}-a-{i}", user
                )
                results_a.append(result)
            else:
                result = await _execute_test_variant(
                    agent_b, test_request.variant_b, f"{test_id}-b-{i}", user
                )
                results_b.append(result)
            
            # Pause pour éviter la surcharge
            await asyncio.sleep(0.01)
        
        # Analyse des résultats
        analysis = await _analyze_ab_test_results(
            results_a, results_b, test_request.metrics
        )
        
        # Décision statistique
        decision = await _make_ab_test_decision(analysis, test_request.target_improvement)
        
        # Stockage des résultats
        test_result = {
            "test_id": test_id,
            "test_name": test_request.test_name,
            "timestamp": datetime.utcnow().isoformat(),
            "variant_a": test_request.variant_a.agent_id,
            "variant_b": test_request.variant_b.agent_id,
            "sample_size": test_request.sample_size,
            "results_a": results_a,
            "results_b": results_b,
            "analysis": analysis,
            "decision": decision,
            "confidence": analysis.get("confidence", 0)
        }
        
        # Cache
        cache_key = f"ab_test:{test_id}"
        await redis.setex(
            cache_key,
            86400,  # 24 heures
            orjson.dumps(test_result)
        )
        
        return test_result
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de test A/B: {str(e)}"
        )


@router.get("/ab-test/{test_id}/results")
async def get_ab_test_results(
    test_id: str,
    redis: Redis = Depends(get_redis_client),
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Récupère les résultats d'un test A/B.
    
    Args:
        test_id: ID du test
        redis: Client Redis
        user: Utilisateur authentifié
    
    Returns:
        Dict: Résultats du test
    """
    try:
        cache_key = f"ab_test:{test_id}"
        result_data = await redis.get(cache_key)
        
        if not result_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test A/B non trouvé ou expiré"
            )
        
        return orjson.loads(result_data)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de récupération: {str(e)}"
        )


# ============================================================================
# Endpoints de templating
# ============================================================================

@router.post("/templates")
@require_permission("agents:templates:create")
async def create_agent_template(
    template: AgentTemplate,
    redis: Redis = Depends(get_redis_client),
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Crée un template d'agent.
    
    Args:
        template: Données du template
        redis: Client Redis
        user: Utilisateur authentifié
    
    Returns:
        Dict: Template créé
    """
    try:
        template_id = f"template-{uuid.uuid4().hex[:8]}"
        
        template_data = {
            "id": template_id,
            **template.dict(),
            "created_by": user["id"],
            "created_at": datetime.utcnow().isoformat(),
            "usage_count": 0
        }
        
        # Stockage
        template_key = f"agent_template:{template_id}"
        await redis.hset(
            template_key,
            mapping=template_data
        )
        
        # Indexation
        await redis.sadd("agent_templates:index", template_id)
        
        return {
            "message": "Template créé",
            "template_id": template_id,
            "template": template_data
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de création de template: {str(e)}"
        )


@router.post("/templates/{template_id}/instantiate")
async def instantiate_template(
    template_id: str,
    variables: Dict[str, Any] = Body(...),
    registry: AgentRegistry = Depends(get_agent_registry),
    redis: Redis = Depends(get_redis_client),
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Instancie un template en un agent réel.
    
    Args:
        template_id: ID du template
        variables: Variables de substitution
        registry: Registry des agents
        redis: Client Redis
        user: Utilisateur authentifié
    
    Returns:
        Dict: Agent instancié
    """
    try:
        # Récupération du template
        template_key = f"agent_template:{template_id}"
        template_data = await redis.hgetall(template_key)
        
        if not template_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template non trouvé"
            )
        
        # Application des variables
        agent_config = template_data.get("template_config", {})
        agent_config = _apply_template_variables(agent_config, variables)
        
        # Création de l'agent
        registration = AgentRegistration(
            name=f"{template_data['name']}-{uuid.uuid4().hex[:4]}",
            description=template_data['description'],
            agent_type=template_data['agent_type'],
            config_schema={},  # À définir selon le template
            default_config=agent_config,
            capabilities=json.loads(template_data.get('capabilities', '[]')),
            category=template_data['category']
        )
        
        # Enregistrement de l'agent
        result = await register_agent(
            registration=registration,
            registry=registry,
            user=user
        )
        
        # Mise à jour du compteur d'utilisation
        await redis.hincrby(template_key, "usage_count", 1)
        
        return {
            "message": "Template instancié avec succès",
            "template_id": template_id,
            "agent_id": result["agent_id"],
            "variables_applied": variables
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur d'instanciation: {str(e)}"
        )


# ============================================================================
# Endpoints d'import/export
# ============================================================================

@router.post("/export")
@require_permission("agents:export")
async def export_agents(
    export_request: AgentExportRequest,
    registry: AgentRegistry = Depends(get_agent_registry),
    redis: Redis = Depends(get_redis_client),
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Exporte des agents dans différents formats.
    
    Args:
        export_request: Configuration de l'export
        registry: Registry des agents
        redis: Client Redis
        user: Utilisateur authentifié
    
    Returns:
        Dict: Données exportées
    """
    try:
        export_id = f"export-{uuid.uuid4().hex[:8]}"
        export_data = []
        
        for agent_id in export_request.agent_ids:
            agent = await registry.get_agent(agent_id)
            if not agent:
                continue
            
            agent_dict = agent.to_dict()
            
            if export_request.include_config:
                agent_dict["config"] = agent.config
            
            if export_request.include_history:
                history = await get_execution_history(
                    agent_id=agent_id,
                    limit=100,
                    redis=redis,
                    user=user
                )
                agent_dict["execution_history"] = history.get("executions", [])
            
            if export_request.include_metrics:
                metrics = await get_agent_metrics(
                    agent_id=agent_id,
                    period="30d",
                    user=user
                )
                agent_dict["performance_metrics"] = metrics
            
            export_data.append(agent_dict)
        
        # Formatage
        if export_request.format == "json":
            content = orjson.dumps(export_data, option=orjson.OPT_INDENT_2)
            content_type = "application/json"
        elif export_request.format == "yaml":
            import yaml
            content = yaml.dump(export_data, default_flow_style=False)
            content_type = "application/x-yaml"
        else:  # csv
            import csv
            import io
            
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=export_data[0].keys() if export_data else [])
            writer.writeheader()
            writer.writerows(export_data)
            content = output.getvalue()
            content_type = "text/csv"
        
        # Stockage temporaire
        export_key = f"agent_export:{export_id}"
        await redis.setex(
            export_key,
            3600,  # 1 heure
            content.encode() if isinstance(content, str) else content
        )
        
        return {
            "export_id": export_id,
            "format": export_request.format,
            "agent_count": len(export_data),
            "download_url": f"/agents/export/{export_id}/download",
            "expires_at": (datetime.utcnow() + timedelta(hours=1)).isoformat()
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur d'export: {str(e)}"
        )


@router.get("/export/{export_id}/download")
async def download_export(
    export_id: str,
    redis: Redis = Depends(get_redis_client),
    user: dict = Depends(get_current_user)
) -> Response:
    """
    Télécharge un export précédemment généré.
    
    Args:
        export_id: ID de l'export
        redis: Client Redis
        user: Utilisateur authentifié
    
    Returns:
        Response: Fichier d'export
    """
    try:
        export_key = f"agent_export:{export_id}"
        export_data = await redis.get(export_key)
        
        if not export_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Export non trouvé ou expiré"
            )
        
        # Détection du format
        try:
            orjson.loads(export_data)
            content_type = "application/json"
            filename = f"agents-export-{export_id}.json"
        except:
            content_type = "text/plain"
            filename = f"agents-export-{export_id}.txt"
        
        return Response(
            content=export_data,
            media_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de téléchargement: {str(e)}"
        )


@router.post("/import")
@require_permission("agents:import")
async def import_agents(
    file: bytes = Body(...),
    format: str = Query("json", regex="^(json|yaml)$"),
    registry: AgentRegistry = Depends(get_agent_registry),
    user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Importe des agents depuis un fichier.
    
    Args:
        file: Fichier d'import
        format: Format du fichier
        registry: Registry des agents
        user: Utilisateur authentifié
    
    Returns:
        Dict: Résultat de l'import
    """
    try:
        # Parsing du fichier
        if format == "json":
            import_data = orjson.loads(file)
        else:  # yaml
            import yaml
            import_data = yaml.safe_load(file)
        
        results = {
            "successful": [],
            "failed": [],
            "skipped": []
        }
        
        for agent_data in import_data:
            try:
                # Validation des données
                registration = AgentRegistration(**agent_data)
                
                # Enregistrement
                result = await register_agent(
                    registration=registration,
                    registry=registry,
                    user=user
                )
                
                results["successful"].append({
                    "agent_id": result["agent_id"],
                    "name": registration.name
                })
                
            except ValidationError as e:
                results["failed"].append({
                    "name": agent_data.get("name", "unknown"),
                    "error": str(e)
                })
            except HTTPException as e:
                if e.status_code == 409:  # Conflit - déjà existant
                    results["skipped"].append({
                        "name": agent_data.get("name", "unknown"),
                        "reason": "Déjà existant"
                    })
                else:
                    results["failed"].append({
                        "name": agent_data.get("name", "unknown"),
                        "error": e.detail
                    })
            except Exception as e:
                results["failed"].append({
                    "name": agent_data.get("name", "unknown"),
                    "error": str(e)
                })
        
        return {
            "message": "Import terminé",
            "results": results,
            "summary": {
                "total": len(import_data),
                "successful": len(results["successful"]),
                "failed": len(results["failed"]),
                "skipped": len(results["skipped"])
            }
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur d'import: {str(e)}"
        )


# ============================================================================
# Fonctions auxiliaires
# ============================================================================

async def _execute_agent_async(
    agent,
    context: Dict[str, Any],
    execution_context: ExecutionContext,
    execution_id: str,
    redis: Redis
) -> None:
    """Exécute un agent de manière asynchrone."""
    try:
        result = await agent.execute(context, execution_context)
        
        # Stockage pour streaming
        stream_key = f"agent_stream:{execution_id}"
        await redis.setex(
            stream_key,
            300,
            orjson.dumps({
                "execution_id": execution_id,
                "status": "completed",
                "result": result.dict()
            })
        )
        
    except Exception as e:
        stream_key = f"agent_stream:{execution_id}"
        await redis.setex(
            stream_key,
            300,
            orjson.dumps({
                "execution_id": execution_id,
                "status": "failed",
                "error": str(e)
            })
        )


async def _add_to_priority_queue(
    request: AgentExecutionRequest,
    execution_id: str,
    context: ExecutionContext
) -> None:
    """Ajoute une exécution à la file d'attente prioritaire."""
    queue_item = {
        "execution_id": execution_id,
        "request": request.dict(),
        "context": context,
        "queued_at": datetime.utcnow()
    }
    
    execution_queue[request.priority].append(queue_item)


async def _check_agent_permissions(agent, user: dict) -> bool:
    """Vérifie les permissions d'exécution pour un agent."""
    # Implémentation basique - à étendre selon les besoins
    user_roles = user.get("roles", [])
    
    if "admin" in user_roles:
        return True
    
    agent_categories = agent.get("categories", [])
    user_permissions = user.get("permissions", {})
    
    for category in agent_categories:
        if user_permissions.get(f"agents:{category}:execute", False):
            return True
    
    return False


async def _log_audit(
    action: str,
    user_id: str,
    resource_id: str,
    details: Dict[str, Any]
) -> None:
    """Journalise une action d'audit."""
    audit_log = {
        "action": action,
        "user_id": user_id,
        "resource_id": resource_id,
        "details": details,
        "timestamp": datetime.utcnow().isoformat(),
        "ip_address": None,  # À remplir par le middleware
        "user_agent": None   # À remplir par le middleware
    }
    
    # En production, envoyer vers un service d'audit dédié
    print(f"[AUDIT] {audit_log}")


async def _send_webhook_notification(event: str, data: Dict[str, Any]) -> None:
    """Envoie une notification webhook."""
    # Implémentation basique - à étendre
    webhook_urls = {
        "batch_completed": "https://hooks.example.com/batch",
        "agent_error": "https://hooks.example.com/error",
    }
    
    if event in webhook_urls:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(
                webhook_urls[event],
                json={
                    "event": event,
                    "data": data,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )


def _calculate_batch_stats(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calcule les statistiques d'un batch."""
    successful = sum(1 for r in results if r.get("status") == "success")
    failed = len(results) - successful
    
    return {
        "successful": successful,
        "failed": failed,
        "success_rate": successful / len(results) if results else 0,
        "avg_execution_time": 0,  # À calculer avec les métriques réelles
        "total_cost": 0  # À calculer avec le tracking de coût
    }


def _calculate_history_statistics(executions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calcule les statistiques d'historique."""
    if not executions:
        return {}
    
    successful = sum(1 for e in executions if e.get("status") == "success")
    durations = [e.get("duration", 0) for e in executions if e.get("duration")]
    
    return {
        "total_executions": len(executions),
        "successful": successful,
        "failed": len(executions) - successful,
        "success_rate": successful / len(executions),
        "avg_duration": sum(durations) / len(durations) if durations else 0,
        "min_duration": min(durations) if durations else 0,
        "max_duration": max(durations) if durations else 0,
        "last_execution": executions[0].get("timestamp") if executions else None
    }


async def _get_agent_performance(agent_id: str) -> float:
    """Récupère la performance d'un agent."""
    # Implémentation simplifiée
    return 0.85  # À remplacer par une vraie métrique


def _analyze_context_for_capabilities(context: Dict[str, Any]) -> List[str]:
    """Analyse le contexte pour déterminer les capacités nécessaires."""
    capabilities = []
    
    if "cost" in str(context).lower():
        capabilities.extend(["cost_optimization", "budget_analysis"])
    
    if "security" in str(context).lower():
        capabilities.extend(["security_scanning", "vulnerability_detection"])
    
    if "incident" in str(context).lower():
        capabilities.extend(["incident_response", "root_cause_analysis"])
    
    return capabilities


async def _calculate_recommendation_score(
    agent,
    context: Dict[str, Any],
    user: dict
) -> float:
    """Calcule un score de recommandation pour un agent."""
    score = 0.0
    
    # Performance historique
    performance = await _get_agent_performance(agent.id)
    score += performance * 0.4
    
    # Adéquation avec le contexte
    context_match = _calculate_context_match(agent, context)
    score += context_match * 0.3
    
    # Popularité/utilisation
    usage_score = await _get_agent_usage_score(agent.id)
    score += usage_score * 0.2
    
    # Préférences utilisateur
    user_preference = await _get_user_preference_score(agent.id, user["id"])
    score += user_preference * 0.1
    
    return min(score, 1.0)


def _get_recommendation_reasons(agent, context: Dict[str, Any]) -> List[str]:
    """Génère des raisons pour la recommandation."""
    reasons = []
    
    if agent.get("category") in str(context).lower():
        reasons.append(f"Spécialisé en {agent.get('category')}")
    
    if agent.get("capabilities"):
        reasons.append(f"Capacités: {', '.join(agent.get('capabilities', [])[:3])}")
    
    return reasons


async def _execute_test_variant(agent, request: AgentExecutionRequest, test_id: str, user: dict):
    """Exécute une variante de test A/B."""
    context = ExecutionContext(
        execution_id=test_id,
        user_id=user["id"],
        priority=request.priority,
        tags=request.tags + ["ab_test"]
    )
    
    result = await agent.execute(request.context, context)
    return result.dict()


async def _analyze_ab_test_results(results_a, results_b, metrics: List[str]) -> Dict[str, Any]:
    """Analyse les résultats d'un test A/B."""
    analysis = {}
    
    for metric in metrics:
        if metric == "accuracy":
            acc_a = sum(r.get("accuracy", 0) for r in results_a) / len(results_a)
            acc_b = sum(r.get("accuracy", 0) for r in results_b) / len(results_b)
            analysis["accuracy"] = {
                "variant_a": acc_a,
                "variant_b": acc_b,
                "difference": acc_b - acc_a,
                "improvement": (acc_b - acc_a) / acc_a if acc_a > 0 else 0
            }
        elif metric == "latency":
            lat_a = sum(r.get("duration", 0) for r in results_a) / len(results_a)
            lat_b = sum(r.get("duration", 0) for r in results_b) / len(results_b)
            analysis["latency"] = {
                "variant_a": lat_a,
                "variant_b": lat_b,
                "difference": lat_b - lat_a,
                "improvement": (lat_a - lat_b) / lat_a if lat_a > 0 else 0  # négatif = amélioration
            }
        elif metric == "cost":
            cost_a = sum(r.get("cost", 0) for r in results_a) / len(results_a)
            cost_b = sum(r.get("cost", 0) for r in results_b) / len(results_b)
            analysis["cost"] = {
                "variant_a": cost_a,
                "variant_b": cost_b,
                "difference": cost_b - cost_a,
                "improvement": (cost_a - cost_b) / cost_a if cost_a > 0 else 0
            }
    
    # Calcul de la confiance statistique (simplifié)
    analysis["confidence"] = 0.95  # À remplacer par un calcul réel
    
    return analysis


async def _make_ab_test_decision(analysis: Dict[str, Any], target_improvement: float) -> Dict[str, Any]:
    """Prend une décision basée sur les résultats du test A/B."""
    improvement = analysis.get("accuracy", {}).get("improvement", 0)
    confidence = analysis.get("confidence", 0)
    
    if confidence < 0.9:
        decision = "inconclusive"
        reason = "Confiance statistique insuffisante"
    elif improvement >= target_improvement:
        decision = "choose_b"
        reason = f"Amélioration de {improvement:.1%} ≥ cible de {target_improvement:.1%}"
    else:
        decision = "choose_a"
        reason = f"Amélioration de {improvement:.1%} < cible de {target_improvement:.1%}"
    
    return {
        "decision": decision,
        "reason": reason,
        "confidence": confidence,
        "improvement": improvement
    }


def _apply_template_variables(template_config: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
    """Applique des variables à un template de configuration."""
    import json
    
    config_str = json.dumps(template_config)
    
    for key, value in variables.items():
        placeholder = f"${{variables.{key}}}"
        config_str = config_str.replace(placeholder, str(value))
    
    return json.loads(config_str)


async def _log_config_change(
    agent_id: str,
    old_config: Dict[str, Any],
    new_config: Dict[str, Any],
    user_id: str,
    notes: Optional[str] = None
) -> None:
    """Journalise un changement de configuration."""
    change_log = {
        "agent_id": agent_id,
        "old_config": old_config,
        "new_config": new_config,
        "changed_by": user_id,
        "timestamp": datetime.utcnow().isoformat(),
        "notes": notes
    }
    
    # En production, stocker dans une base de données d'audit
    print(f"[CONFIG_CHANGE] {change_log}")


async def _calculate_performance_trends(agent_id: str, period: str) -> Dict[str, Any]:
    """Calcule les tendances de performance."""
    # Implémentation simplifiée
    return {
        "trend": "stable",
        "change_percentage": 0.0,
        "period_comparison": {}
    }


async def _get_agent_benchmarks(agent_id: str) -> Dict[str, Any]:
    """Récupère les benchmarks pour un agent."""
    # Implémentation simplifiée
    return {
        "category_average": 0.85,
        "top_performers": 0.95,
        "position": "above_average"
    }


def _generate_metrics_summary(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Génère un résumé des métriques."""
    return {
        "health_score": metrics.get("success_rate", 0) * 100,
        "performance_level": "excellent" if metrics.get("success_rate", 0) > 0.95 else "good",
        "recommendations": []
    }


def _calculate_context_match(agent, context: Dict[str, Any]) -> float:
    """Calcule l'adéquation entre un agent et un contexte."""
    match_score = 0.0
    
    agent_capabilities = set(agent.get("capabilities", []))
    context_capabilities = set(_analyze_context_for_capabilities(context))
    
    if context_capabilities:
        match_score = len(agent_capabilities.intersection(context_capabilities)) / len(context_capabilities)
    
    return match_score


async def _get_agent_usage_score(agent_id: str) -> float:
    """Calcule un score d'utilisation pour un agent."""
    # Implémentation simplifiée
    return 0.7


async def _get_user_preference_score(agent_id: str, user_id: str) -> float:
    """Calcule un score de préférence utilisateur."""
    # Implémentation simplifiée
    return 0.5


# ============================================================================
# WebSocket pour les mises à jour en temps réel
# ============================================================================

@router.websocket("/ws/updates")
async def websocket_updates(
    websocket: WebSocket,
    token: str = Query(...),
    registry: AgentRegistry = Depends(get_agent_registry)
):
    """
    WebSocket pour les mises à jour en temps réel des agents.
    """
    await websocket.accept()
    
    try:
        # Authentification (simplifiée)
        # En production, utiliser un vrai système d'authentification
        
        while True:
            # Attente de messages du client
            data = await websocket.receive_json()
            
            message_type = data.get("type")
            
            if message_type == "subscribe":
                # Abonnement à des agents spécifiques
                agent_ids = data.get("agent_ids", [])
                
                # Envoi des mises à jour périodiques
                for agent_id in agent_ids:
                    agent = await registry.get_agent(agent_id)
                    if agent:
                        await websocket.send_json({
                            "type": "agent_update",
                            "agent_id": agent_id,
                            "status": "active",
                            "metrics": await _get_agent_performance(agent_id),
                            "timestamp": datetime.utcnow().isoformat()
                        })
            
            elif message_type == "unsubscribe":
                # Désabonnement
                break
            
            elif message_type == "ping":
                # Keep-alive
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": datetime.utcnow().isoformat()
                })
    
    except WebSocketDisconnect:
        print("Client WebSocket déconnecté")
    except Exception as e:
        print(f"Erreur WebSocket: {str(e)}")
        await websocket.close(code=1011)


# ============================================================================
# Route de santé
# ============================================================================

@router.get("/health")
async def agents_health(
    registry: AgentRegistry = Depends(get_agent_registry),
    redis: Redis = Depends(get_redis_client)
) -> Dict[str, Any]:
    """
    Vérifie la santé du système d'agents.
    
    Returns:
        Dict: État de santé
    """
    try:
        # Vérification du registry
        agent_count = await registry.count_agents({"status": "active"})
        
        # Vérification Redis
        await redis.ping()
        
        # Vérification de la file d'attente
        queue_status = {
            priority: len(queue)
            for priority, queue in execution_queue.items()
        }
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "agents": {
                "active": agent_count,
                "total": await registry.count_agents({})
            },
            "queue": queue_status,
            "cache": {
                "executions": len(execution_cache),
                "size": "N/A"
            }
        }
        
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }