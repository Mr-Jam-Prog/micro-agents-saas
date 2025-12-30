"""
Utilitaires divers pour le système d'agents.
"""

import asyncio
import json
import pickle
import hashlib
import inspect
import random
import string
import time
import uuid
from datetime import datetime, timedelta
from typing import (
    Any, Dict, List, Optional, Union, Callable, Type, TypeVar,
    get_type_hints, cast
)
from enum import Enum
from functools import lru_cache
from contextlib import asynccontextmanager, contextmanager

import msgpack
import orjson
import structlog
from pydantic import BaseModel

from .exceptions import SerializationError


T = TypeVar('T')
ModelT = TypeVar('ModelT', bound=BaseModel)


class SerializationFormat(Enum):
    """Formats de sérialisation supportés"""
    JSON = "json"
    MESSAGEPACK = "msgpack"
    PICKLE = "pickle"
    ORJSON = "orjson"


class Serializer:
    """Utilitaire de sérialisation pour le caching et la communication"""
    
    def __init__(self, default_format: SerializationFormat = SerializationFormat.JSON):
        self.default_format = default_format
        self.logger = structlog.get_logger(__name__)
    
    def serialize(
        self,
        data: Any,
        format: Optional[SerializationFormat] = None,
        **kwargs
    ) -> bytes:
        """
        Sérialise des données au format spécifié.
        
        Args:
            data: Données à sérialiser
            format: Format de sérialisation
            **kwargs: Options spécifiques au format
            
        Returns:
            Données sérialisées
        """
        fmt = format or self.default_format
        
        try:
            if fmt == SerializationFormat.JSON:
                return json.dumps(data, **kwargs).encode('utf-8')
            
            elif fmt == SerializationFormat.ORJSON:
                options = kwargs.get('options', 0)
                return orjson.dumps(data, option=options)
            
            elif fmt == SerializationFormat.MESSAGEPACK:
                return msgpack.packb(data, **kwargs)
            
            elif fmt == SerializationFormat.PICKLE:
                protocol = kwargs.get('protocol', pickle.HIGHEST_PROTOCOL)
                return pickle.dumps(data, protocol=protocol)
            
            else:
                raise ValueError(f"Format non supporté: {fmt}")
                
        except Exception as e:
            self.logger.error(
                "serialization_failed",
                format=fmt.value,
                error=str(e),
                data_type=type(data).__name__
            )
            raise SerializationError(
                f"Échec de la sérialisation au format {fmt}",
                data_type=type(data).__name__
            ) from e
    
    def deserialize(
        self,
        data: bytes,
        format: Optional[SerializationFormat] = None,
        target_type: Optional[Type[T]] = None,
        **kwargs
    ) -> T:
        """
        Désérialise des données.
        
        Args:
            data: Données sérialisées
            format: Format de sérialisation
            target_type: Type cible pour la désérialisation
            **kwargs: Options spécifiques au format
            
        Returns:
            Données désérialisées
        """
        if not data:
            raise ValueError("Données vides")
        
        fmt = format or self.default_format
        
        try:
            if fmt == SerializationFormat.JSON:
                result = json.loads(data.decode('utf-8'), **kwargs)
            
            elif fmt == SerializationFormat.ORJSON:
                result = orjson.loads(data)
            
            elif fmt == SerializationFormat.MESSAGEPACK:
                result = msgpack.unpackb(data, **kwargs)
            
            elif fmt == SerializationFormat.PICKLE:
                result = pickle.loads(data, **kwargs)
            
            else:
                raise ValueError(f"Format non supporté: {fmt}")
            
            # Conversion vers le type cible si spécifié
            if target_type:
                result = self._convert_to_type(result, target_type)
            
            return result
            
        except Exception as e:
            self.logger.error(
                "deserialization_failed",
                format=fmt.value,
                error=str(e),
                data_length=len(data)
            )
            raise SerializationError(
                f"Échec de la désérialisation au format {fmt}",
                data_type=target_type.__name__ if target_type else "unknown"
            ) from e
    
    def _convert_to_type(self, data: Any, target_type: Type[T]) -> T:
        """Convertit des données vers un type spécifique"""
        # Si c'est déjà le bon type
        if isinstance(data, target_type):
            return data
        
        # Conversion spéciale pour les modèles Pydantic
        if inspect.isclass(target_type) and issubclass(target_type, BaseModel):
            if isinstance(data, dict):
                return target_type(**data)
            else:
                # Tentative de conversion via dict()
                try:
                    return target_type(**dict(data))
                except Exception as e:
                    raise SerializationError(
                        f"Impossible de convertir en {target_type.__name__}",
                        data_type=type(data).__name__
                    ) from e
        
        # Conversion de base
        try:
            return target_type(data)
        except (TypeError, ValueError) as e:
            raise SerializationError(
                f"Impossible de convertir en {target_type.__name__}",
                data_type=type(data).__name__
            ) from e
    
    def generate_cache_key(
        self,
        prefix: str,
        *args,
        **kwargs
    ) -> str:
        """
        Génère une clé de cache unique.
        
        Args:
            prefix: Préfixe de la clé
            *args: Arguments positionnels
            **kwargs: Arguments nommés
            
        Returns:
            Clé de cache
        """
        # Créer une représentation sérialisable
        key_data = {
            "prefix": prefix,
            "args": args,
            "kwargs": kwargs,
            "timestamp": time.time()
        }
        
        # Sérialiser et hasher
        serialized = self.serialize(key_data, format=SerializationFormat.JSON)
        hash_obj = hashlib.sha256(serialized)
        
        return f"{prefix}:{hash_obj.hexdigest()[:16]}"


class AsyncRateLimiter:
    """Rate limiter asynchrone"""
    
    def __init__(self, rate: int, per: float):
        """
        Args:
            rate: Nombre de requêtes autorisées
            per: Période en secondes
        """
        self.rate = rate
        self.per = per
        self.tokens = rate
        self.last_update = time.time()
        self._lock = asyncio.Lock()
    
    async def acquire(self, tokens: int = 1) -> bool:
        """
        Acquiert des tokens.
        
        Args:
            tokens: Nombre de tokens à acquérir
            
        Returns:
            True si les tokens ont été acquis, False sinon
        """
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            
            # Ajouter de nouveaux tokens basés sur le temps écoulé
            self.tokens += elapsed * (self.rate / self.per)
            self.tokens = min(self.tokens, self.rate)
            self.last_update = now
            
            # Vérifier si on a assez de tokens
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            
            return False
    
    async def wait(self, tokens: int = 1) -> None:
        """
        Attend jusqu'à ce que les tokens soient disponibles.
        
        Args:
            tokens: Nombre de tokens à attendre
        """
        while not await self.acquire(tokens):
            await asyncio.sleep(0.1)


class AsyncSemaphoreWithPriority:
    """Sémaphore asynchrone avec support de priorité"""
    
    def __init__(self, value: int):
        self._value = value
        self._waiters: List[asyncio.Future] = []
        self._priority_waiters: List[asyncio.Future] = []
    
    async def acquire(self, priority: bool = False) -> None:
        """Acquiert le sémaphore avec option de priorité"""
        fut = asyncio.get_event_loop().create_future()
        
        if priority:
            self._priority_waiters.append(fut)
        else:
            self._waiters.append(fut)
        
        try:
            await fut
        except Exception:
            # Nettoyer en cas d'exception
            if priority and fut in self._priority_waiters:
                self._priority_waiters.remove(fut)
            elif fut in self._waiters:
                self._waiters.remove(fut)
            raise
    
    def release(self) -> None:
        """Relâche le sémaphore"""
        # Priorité aux waiters prioritaires
        if self._priority_waiters:
            fut = self._priority_waiters.pop(0)
            fut.set_result(None)
        elif self._waiters:
            fut = self._waiters.pop(0)
            fut.set_result(None)
        else:
            self._value += 1
    
    @property
    def value(self) -> int:
        """Valeur actuelle du sémaphore"""
        return self._value
    
    @asynccontextmanager
    async def context(self, priority: bool = False):
        """Context manager pour le sémaphore"""
        await self.acquire(priority=priority)
        try:
            yield
        finally:
            self.release()


@lru_cache(maxsize=128)
def generate_id(prefix: str = "id", length: int = 8) -> str:
    """
    Génère un ID unique avec préfixe.
    
    Args:
        prefix: Préfixe de l'ID
        length: Longueur de la partie aléatoire
        
    Returns:
        ID généré
    """
    random_part = ''.join(
        random.choices(string.ascii_lowercase + string.digits, k=length)
    )
    return f"{prefix}_{random_part}"


def format_duration(seconds: float) -> str:
    """Formate une durée en secondes en chaîne lisible"""
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds // 60
        remaining = seconds % 60
        return f"{minutes:.0f}m {remaining:.0f}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours:.0f}h {minutes:.0f}m"


def deep_merge(dict1: Dict, dict2: Dict) -> Dict:
    """
    Fusionne récursivement deux dictionnaires.
    
    Args:
        dict1: Premier dictionnaire
        dict2: Deuxième dictionnaire
        
    Returns:
        Dictionnaire fusionné
    """
    result = dict1.copy()
    
    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    
    return result


def get_class_hierarchy(cls: Type) -> List[Type]:
    """
    Retourne la hiérarchie d'héritage d'une classe.
    
    Args:
        cls: Classe à analyser
        
    Returns:
        Liste des classes parentes
    """
    hierarchy = []
    
    for base in cls.__bases__:
        if base is not object:
            hierarchy.extend(get_class_hierarchy(base))
    
    hierarchy.append(cls)
    return hierarchy


def is_async_function(func: Callable) -> bool:
    """Vérifie si une fonction est asynchrone"""
    return asyncio.iscoroutinefunction(func) or (
        hasattr(func, '__call__') and asyncio.iscoroutinefunction(func.__call__)
    )


class ExpiringDict:
    """Dictionnaire avec expiration automatique des entrées"""
    
    def __init__(self, default_ttl: float = 300.0):
        self._data: Dict[str, tuple] = {}
        self._timestamps: Dict[str, float] = {}
        self.default_ttl = default_ttl
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Définit une valeur avec TTL"""
        ttl = ttl or self.default_ttl
        self._data[key] = value
        self._timestamps[key] = time.time() + ttl
    
    def get(self, key: str, default: Any = None) -> Any:
        """Récupère une valeur si elle n'est pas expirée"""
        if key not in self._data:
            return default
        
        if time.time() > self._timestamps[key]:
            # Expiré, nettoyer
            del self._data[key]
            del self._timestamps[key]
            return default
        
        return self._data[key]
    
    def cleanup(self) -> int:
        """Nettoie les entrées expirées et retourne le nombre supprimé"""
        current_time = time.time()
        expired_keys = [
            key for key, expiry in self._timestamps.items()
            if current_time > expiry
        ]
        
        for key in expired_keys:
            del self._data[key]
            del self._timestamps[key]
        
        return len(expired_keys)
    
    def __contains__(self, key: str) -> bool:
        """Vérifie si une clé existe et n'est pas expirée"""
        if key not in self._data:
            return False
        
        if time.time() > self._timestamps[key]:
            # Expiré, nettoyer
            del self._data[key]
            del self._timestamps[key]
            return False
        
        return True
    
    def __len__(self) -> int:
        """Retourne le nombre d'entrées non expirées"""
        self.cleanup()
        return len(self._data)