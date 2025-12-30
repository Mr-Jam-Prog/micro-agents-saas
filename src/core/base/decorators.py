"""
Décorateurs utilitaires pour les agents.
"""

import asyncio
import functools
import inspect
import time
from typing import (
    Any, Callable, Dict, List, Optional, Type, TypeVar, 
    Union, get_type_hints, cast
)
from functools import wraps

import structlog
from pydantic import BaseModel, ValidationError

from .exceptions import ValidationError as AgentValidationError
from .types import AgentContext


F = TypeVar('F', bound=Callable[..., Any])
T = TypeVar('T')


def validate_input(schema: Optional[Type[BaseModel]] = None):
    """
    Décorateur pour valider automatiquement les entrées d'un agent.
    
    Args:
        schema: Schéma Pydantic optionnel pour validation personnalisée
    """
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(self, context: AgentContext, *args, **kwargs):
            # Validation automatique du contexte
            if not isinstance(context, AgentContext):
                raise AgentValidationError(
                    "Le contexte doit être une instance de AgentContext",
                    field="context"
                )
            
            # Validation avec schéma personnalisé si fourni
            if schema:
                try:
                    validated_data = schema(**context.dict())
                    # Mettre à jour le contexte avec les valeurs validées
                    for field in schema.__fields__:
                        if hasattr(validated_data, field):
                            setattr(context, field, getattr(validated_data, field))
                except ValidationError as e:
                    raise AgentValidationError(
                        f"Validation des entrées échouée: {e}",
                        field="context"
                    ) from e
            
            # Validation des arguments supplémentaires
            sig = inspect.signature(func)
            bound_args = sig.bind(self, context, *args, **kwargs)
            bound_args.apply_defaults()
            
            # Valider les arguments avec les type hints
            type_hints = get_type_hints(func)
            for param_name, param_value in bound_args.arguments.items():
                if param_name in type_hints:
                    expected_type = type_hints[param_name]
                    if not isinstance(param_value, expected_type):
                        # Tentative de conversion
                        try:
                            if expected_type == AgentContext:
                                bound_args.arguments[param_name] = AgentContext(**param_value)
                            else:
                                bound_args.arguments[param_name] = expected_type(param_value)
                        except (TypeError, ValueError):
                            raise AgentValidationError(
                                f"Type invalide pour {param_name}: "
                                f"attendait {expected_type}, reçu {type(param_value)}",
                                field=param_name
                            )
            
            return await func(*bound_args.args, **bound_args.kwargs)
        
        return cast(F, wrapper)
    
    return decorator


def validate_output(schema: Optional[Type[BaseModel]] = None):
    """
    Décorateur pour valider automatiquement les sorties d'un agent.
    
    Args:
        schema: Schéma Pydantic optionnel pour validation personnalisée
    """
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            
            # Validation avec schéma personnalisé si fourni
            if schema:
                if isinstance(result, dict):
                    try:
                        validated_result = schema(**result)
                        return validated_result
                    except ValidationError as e:
                        raise AgentValidationError(
                            f"Validation des sorties échouée: {e}",
                            field="result"
                        ) from e
                elif not isinstance(result, schema):
                    try:
                        # Tentative de conversion
                        if isinstance(result, BaseModel):
                            result_dict = result.dict()
                        else:
                            result_dict = dict(result)
                        
                        validated_result = schema(**result_dict)
                        return validated_result
                    except (ValidationError, TypeError) as e:
                        raise AgentValidationError(
                            f"Validation des sorties échouée: {e}",
                            field="result"
                        ) from e
            
            return result
        
        return cast(F, wrapper)
    
    return decorator


def with_retry(
    max_attempts: int = 3,
    delay: float = 0.1,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    logger: Optional[structlog.BoundLogger] = None
):
    """
    Décorateur pour ajouter des retries automatiques à une méthode.
    
    Args:
        max_attempts: Nombre maximum de tentatives
        delay: Délai initial entre les tentatives
        backoff: Facteur de backoff exponentiel
        exceptions: Exceptions à retry
        logger: Logger pour enregistrer les retries
    """
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    if attempt > 1 and logger:
                        logger.warning(
                            "retry_attempt",
                            function=func.__name__,
                            attempt=attempt,
                            max_attempts=max_attempts
                        )
                    
                    return await func(*args, **kwargs)
                    
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts:
                        break
                    
                    # Calculer le délai avec backoff exponentiel
                    wait_time = delay * (backoff ** (attempt - 1))
                    await asyncio.sleep(wait_time)
            
            # Si on arrive ici, toutes les tentatives ont échoué
            if logger:
                logger.error(
                    "retry_exhausted",
                    function=func.__name__,
                    max_attempts=max_attempts,
                    error=str(last_exception)
                )
            
            raise last_exception
        
        return cast(F, wrapper)
    
    return decorator


def circuit_breaker(
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
    excluded_exceptions: tuple = (),
    state_storage: Optional[Dict[str, Any]] = None
):
    """
    Décorateur pour implémenter le pattern circuit breaker.
    
    Args:
        failure_threshold: Nombre d'échecs avant ouverture
        recovery_timeout: Temps avant tentative de récupération
        excluded_exceptions: Exceptions à ignorer
        state_storage: Stockage partagé pour l'état du circuit
    """
    if state_storage is None:
        state_storage = {}
    
    def decorator(func: F) -> F:
        func_name = func.__name__
        state_key = f"circuit_breaker:{func_name}"
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            state = state_storage.get(state_key, {
                "state": "closed",  # closed, open, half_open
                "failure_count": 0,
                "last_failure_time": 0
            })
            
            # Vérifier si le circuit est ouvert
            if state["state"] == "open":
                current_time = time.time()
                if current_time - state["last_failure_time"] > recovery_timeout:
                    # Passer en half_open pour tester
                    state["state"] = "half_open"
                    state["failure_count"] = 0
                else:
                    from .exceptions import CircuitOpenError
                    raise CircuitOpenError(
                        agent_name=func_name,
                        tenant_id="unknown"
                    )
            
            try:
                result = await func(*args, **kwargs)
                
                # Réinitialiser en cas de succès
                if state["state"] == "half_open":
                    state["state"] = "closed"
                    state["failure_count"] = 0
                
                state_storage[state_key] = state
                return result
                
            except Exception as e:
                # Ignorer les exceptions exclues
                if isinstance(e, excluded_exceptions):
                    raise
                
                state["failure_count"] += 1
                state["last_failure_time"] = time.time()
                
                # Vérifier si on doit ouvrir le circuit
                if (state["failure_count"] >= failure_threshold and 
                    state["state"] != "open"):
                    state["state"] = "open"
                
                state_storage[state_key] = state
                raise
        
        return cast(F, wrapper)
    
    return decorator


def timed_execution(metric_name: Optional[str] = None):
    """
    Décorateur pour mesurer le temps d'exécution.
    
    Args:
        metric_name: Nom de la métrique (par défaut: nom de la fonction)
    """
    def decorator(func: F) -> F:
        metric = metric_name or func.__name__
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            
            try:
                result = await func(*args, **kwargs)
                execution_time = time.time() - start_time
                
                # Ici on pourrait enregistrer la métrique
                # dans un système de métriques centralisé
                if hasattr(args[0], 'logger') and args[0].logger:
                    args[0].logger.debug(
                        "execution_timed",
                        function=func.__name__,
                        execution_time=execution_time,
                        metric=metric
                    )
                
                return result
            except Exception as e:
                execution_time = time.time() - start_time
                
                if hasattr(args[0], 'logger') and args[0].logger:
                    args[0].logger.error(
                        "execution_failed_timed",
                        function=func.__name__,
                        execution_time=execution_time,
                        error=str(e),
                        metric=metric
                    )
                
                raise
        
        return cast(F, wrapper)
    
    return decorator


def cache_result(
    ttl: int = 300,
    key_func: Optional[Callable[..., str]] = None,
    ignore_args: List[int] = None,
    ignore_kwargs: List[str] = None
):
    """
    Décorateur pour mettre en cache les résultats d'une fonction.
    
    Args:
        ttl: Time to live en secondes
        key_func: Fonction pour générer la clé de cache
        ignore_args: Index des arguments à ignorer
        ignore_kwargs: Noms des kwargs à ignorer
    """
    cache_store = {}
    
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Générer la clé de cache
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # Clé par défaut basée sur les arguments
                key_parts = []
                
                # Ajouter les arguments
                for i, arg in enumerate(args):
                    if ignore_args and i in ignore_args:
                        continue
                    key_parts.append(repr(arg))
                
                # Ajouter les kwargs
                for k, v in sorted(kwargs.items()):
                    if ignore_kwargs and k in ignore_kwargs:
                        continue
                    key_parts.append(f"{k}={repr(v)}")
                
                cache_key = f"{func.__name__}:{hash(tuple(key_parts))}"
            
            # Vérifier le cache
            if cache_key in cache_store:
                timestamp, result = cache_store[cache_key]
                if time.time() - timestamp < ttl:
                    return result
                else:
                    # Cache expiré
                    del cache_store[cache_key]
            
            # Exécuter la fonction
            result = await func(*args, **kwargs)
            
            # Mettre en cache
            cache_store[cache_key] = (time.time(), result)
            
            # Nettoyer le cache si nécessaire
            if len(cache_store) > 1000:  # Limite arbitraire
                # Supprimer les entrées les plus anciennes
                oldest_keys = sorted(
                    cache_store.keys(),
                    key=lambda k: cache_store[k][0]
                )[:200]
                for key in oldest_keys:
                    del cache_store[key]
            
            return result
        
        return cast(F, wrapper)
    
    return decorator


def rate_limit(
    limit: int = 100,
    period: int = 60,  # secondes
    key_func: Optional[Callable[..., str]] = None
):
    """
    Décorateur pour limiter le taux d'appels.
    
    Args:
        limit: Nombre maximum d'appels par période
        period: Période en secondes
        key_func: Fonction pour générer la clé de rate limiting
    """
    from collections import defaultdict, deque
    
    rate_store = defaultdict(deque)
    
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Générer la clé de rate limiting
            if key_func:
                rate_key = key_func(*args, **kwargs)
            else:
                # Par défaut, utiliser le nom de la fonction
                rate_key = func.__name__
            
            current_time = time.time()
            calls = rate_store[rate_key]
            
            # Supprimer les appels hors période
            while calls and current_time - calls[0] > period:
                calls.popleft()
            
            # Vérifier la limite
            if len(calls) >= limit:
                from .exceptions import RateLimitExceededError
                raise RateLimitExceededError(limit, period)
            
            # Ajouter l'appel courant
            calls.append(current_time)
            
            return await func(*args, **kwargs)
        
        return cast(F, wrapper)
    
    return decorator


def with_timeout(timeout: float):
    """
    Décorateur pour ajouter un timeout à une fonction asynchrone.
    
    Args:
        timeout: Timeout en secondes
    """
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                from .exceptions import TimeoutError
                raise TimeoutError(timeout)
        
        return cast(F, wrapper)
    
    return decorator