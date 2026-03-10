"""
Redis Cache Handler pour MicroAgents Platform

Ce module implémente un système de cache multi-niveaux sophistiqué avec:
- Caching mémoire, Redis et disque
- Stratégies d'invalidation avancées
- Verrous distribués
- Synchronisation Pub/Sub
- Pré-chargement du cache
- Monitoring de performance
- Optimisation mémoire
- Support cluster
- Mécanismes de failover
- Sécurité (chiffrement, TLS)
"""

import asyncio
import hashlib
import json
import logging
import pickle
import time
import zlib
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, TypeVar
from dataclasses import dataclass, field
from pathlib import Path

import redis.asyncio as redis
import msgpack
import nacl.secret
import nacl.utils
from pydantic import BaseModel, Field, validator
from prometheus_client import Counter, Gauge, Histogram, Summary

from microagents.utils.serialization.serializers import json_serializer

logger = logging.getLogger(__name__)

# Métriques Prometheus
CACHE_HITS = Counter('cache_hits_total', 'Total cache hits', ['level', 'namespace'])
CACHE_MISSES = Counter('cache_misses_total', 'Total cache misses', ['level', 'namespace'])
CACHE_EVICTIONS = Counter('cache_evictions_total', 'Total cache evictions', ['level', 'namespace'])
CACHE_SIZE = Gauge('cache_size_bytes', 'Cache size in bytes', ['level', 'namespace'])
CACHE_LATENCY = Histogram('cache_operation_latency_seconds', 'Cache operation latency', ['operation', 'level'])
CACHE_LOCK_WAIT_TIME = Histogram('cache_lock_wait_seconds', 'Time waiting for cache lock')
DISTRIBUTED_LOCK_ACQUISITIONS = Counter('distributed_lock_acquisitions_total', 'Total distributed lock acquisitions')
DISTRIBUTED_LOCK_TIMEOUTS = Counter('distributed_lock_timeouts_total', 'Total distributed lock timeouts')

T = TypeVar('T')


class CacheLevel(Enum):
    """Niveaux de cache"""
    MEMORY = "memory"
    REDIS = "redis"
    DISK = "disk"


class CacheStrategy(Enum):
    """Stratégies de cache"""
    WRITE_THROUGH = "write_through"
    WRITE_BEHIND = "write_behind"
    CACHE_ASIDE = "cache_aside"
    READ_THROUGH = "read_through"
    REFRESH_AHEAD = "refresh_ahead"


class InvalidationStrategy(Enum):
    """Stratégies d'invalidation"""
    TIME_BASED = "time_based"
    EVENT_BASED = "event_based"
    VERSION_BASED = "version_based"
    MANUAL = "manual"


class SerializationFormat(Enum):
    """Formats de sérialisation"""
    JSON = "json"
    MSGPACK = "msgpack"
    PICKLE = "pickle"
    COMPRESSED_JSON = "compressed_json"


@dataclass
class CacheStats:
    """Statistiques de cache"""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size_bytes: int = 0
    avg_hit_rate: float = 0.0
    last_updated: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            'hits': self.hits,
            'misses': self.misses,
            'evictions': self.evictions,
            'size_bytes': self.size_bytes,
            'avg_hit_rate': self.avg_hit_rate,
            'last_updated': self.last_updated.isoformat()
        }


@dataclass
class CacheEntry:
    """Entrée de cache"""
    key: str
    value: Any
    created_at: datetime
    expires_at: Optional[datetime]
    access_count: int = 0
    last_accessed: datetime = field(default_factory=datetime.utcnow)
    version: str = "1.0"
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_expired(self) -> bool:
        """Vérifie si l'entrée est expirée"""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at
    
    def age(self) -> timedelta:
        """Âge de l'entrée"""
        return datetime.utcnow() - self.created_at
    
    def touch(self) -> None:
        """Met à jour le dernier accès"""
        self.last_accessed = datetime.utcnow()
        self.access_count += 1


class CacheConfig(BaseModel):
    """Configuration du cache"""
    # Redis
    redis_url: str = Field("redis://localhost:6379", description="URL Redis")
    redis_cluster_mode: bool = Field(False, description="Mode cluster Redis")
    redis_max_connections: int = Field(100, description="Connexions Redis max")
    redis_health_check_interval: int = Field(30, description="Intervalle vérification santé")
    
    # Sécurité
    enable_encryption: bool = Field(True, description="Activer le chiffrement")
    encryption_key: Optional[str] = Field(None, description="Clé de chiffrement")
    enable_tls: bool = Field(False, description="Activer TLS pour Redis")
    tls_cert_path: Optional[str] = Field(None, description="Chemin certificat TLS")
    
    # Niveaux de cache
    enable_memory_cache: bool = Field(True, description="Activer cache mémoire")
    enable_redis_cache: bool = Field(True, description="Activer cache Redis")
    enable_disk_cache: bool = Field(False, description="Activer cache disque")
    
    # Stratégies
    default_strategy: CacheStrategy = Field(CacheStrategy.WRITE_THROUGH, description="Stratégie par défaut")
    default_ttl: int = Field(3600, description="TTL par défaut en secondes")
    
    # Optimisation mémoire
    max_memory_size_mb: int = Field(100, description="Taille max mémoire cache (MB)")
    max_redis_memory_mb: int = Field(1000, description="Taille max Redis cache (MB)")
    max_disk_size_mb: int = Field(5000, description="Taille max disque cache (MB)")
    
    # Éviction
    eviction_policy: str = Field("lru", description="Politique d'éviction")
    eviction_check_interval: int = Field(60, description="Intervalle vérification éviction")
    
    # Performance
    compression_threshold: int = Field(1024, description="Seuil compression (bytes)")
    batch_operations: bool = Field(True, description="Activer opérations batch")
    pipeline_operations: bool = Field(True, description="Activer pipeline Redis")
    
    # Monitoring
    enable_stats: bool = Field(True, description="Activer statistiques")
    stats_interval: int = Field(300, description="Intervalle collecte stats")
    
    # Failover
    enable_failover: bool = Field(True, description="Activer failover")
    failover_timeout: int = Field(5000, description="Timeout failover (ms)")
    circuit_breaker_threshold: int = Field(5, description="Seuil circuit breaker")
    
    class Config:
        """Configuration Pydantic"""
        use_enum_values = True


class CacheInterface(ABC):
    """Interface de cache abstraite"""
    
    @abstractmethod
    async def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        """Récupère une valeur du cache"""
        pass
    
    @abstractmethod
    async def set(
        self,
        key: str,
        value: Any,
        namespace: str = "default",
        ttl: Optional[int] = None,
        tags: Optional[List[str]] = None
    ) -> bool:
        """Définit une valeur dans le cache"""
        pass
    
    @abstractmethod
    async def delete(self, key: str, namespace: str = "default") -> bool:
        """Supprime une valeur du cache"""
        pass
    
    @abstractmethod
    async def exists(self, key: str, namespace: str = "default") -> bool:
        """Vérifie si une clé existe"""
        pass
    
    @abstractmethod
    async def clear(self, namespace: Optional[str] = None) -> int:
        """Vide le cache"""
        pass
    
    @abstractmethod
    async def get_stats(self) -> CacheStats:
        """Récupère les statistiques"""
        pass


class MemoryCache(CacheInterface):
    """Cache en mémoire avec LRU"""
    
    def __init__(self, max_size_mb: int = 100):
        """
        Initialise le cache mémoire.
        
        Args:
            max_size_mb: Taille maximum en MB
        """
        self.cache: Dict[str, CacheEntry] = {}
        self.namespaces: Dict[str, List[str]] = {}
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.current_size_bytes = 0
        self.stats = CacheStats()
        self.lock = asyncio.Lock()
        
        # Index pour éviction LRU
        self.access_order: List[str] = []
        
        logger.info(f"MemoryCache initialisé (max: {max_size_mb}MB)")
    
    async def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        """Récupère une valeur du cache"""
        full_key = self._make_key(key, namespace)
        
        async with self.lock:
            if full_key not in self.cache:
                CACHE_MISSES.labels(level=CacheLevel.MEMORY.value, namespace=namespace).inc()
                self.stats.misses += 1
                return None
            
            entry = self.cache[full_key]
            
            if entry.is_expired():
                # Nettoyage asynchrone
                asyncio.create_task(self._cleanup_expired(full_key, namespace))
                CACHE_MISSES.labels(level=CacheLevel.MEMORY.value, namespace=namespace).inc()
                self.stats.misses += 1
                return None
            
            # Mise à jour LRU
            if full_key in self.access_order:
                self.access_order.remove(full_key)
            self.access_order.append(full_key)
            
            entry.touch()
            CACHE_HITS.labels(level=CacheLevel.MEMORY.value, namespace=namespace).inc()
            self.stats.hits += 1
            
            return entry.value
    
    async def set(
        self,
        key: str,
        value: Any,
        namespace: str = "default",
        ttl: Optional[int] = None,
        tags: Optional[List[str]] = None
    ) -> bool:
        """Définit une valeur dans le cache"""
        full_key = self._make_key(key, namespace)
        value_size = self._estimate_size(value)
        
        async with self.lock:
            # Vérification de la taille
            if value_size > self.max_size_bytes:
                logger.warning(f"Valeur trop grande pour le cache: {value_size} bytes")
                return False
            
            # Éviction si nécessaire
            await self._evict_if_needed(value_size)
            
            # Création de l'entrée
            expires_at = None
            if ttl:
                expires_at = datetime.utcnow() + timedelta(seconds=ttl)
            
            entry = CacheEntry(
                key=full_key,
                value=value,
                created_at=datetime.utcnow(),
                expires_at=expires_at,
                tags=tags or []
            )
            
            # Mise à jour du cache
            if full_key in self.cache:
                old_entry = self.cache[full_key]
                self.current_size_bytes -= self._estimate_size(old_entry.value)
            
            self.cache[full_key] = entry
            self.current_size_bytes += value_size
            
            # Mise à jour de l'ordre d'accès
            if full_key in self.access_order:
                self.access_order.remove(full_key)
            self.access_order.append(full_key)
            
            # Mise à jour du namespace
            if namespace not in self.namespaces:
                self.namespaces[namespace] = []
            if key not in self.namespaces[namespace]:
                self.namespaces[namespace].append(key)
            
            CACHE_SIZE.labels(level=CacheLevel.MEMORY.value, namespace=namespace).set(
                self.current_size_bytes
            )
            
            return True
    
    async def delete(self, key: str, namespace: str = "default") -> bool:
        """Supprime une valeur du cache"""
        full_key = self._make_key(key, namespace)
        
        async with self.lock:
            if full_key not in self.cache:
                return False
            
            # Libération mémoire
            entry = self.cache[full_key]
            value_size = self._estimate_size(entry.value)
            self.current_size_bytes -= value_size
            
            # Suppression
            del self.cache[full_key]
            
            # Mise à jour des index
            if full_key in self.access_order:
                self.access_order.remove(full_key)
            
            if namespace in self.namespaces and key in self.namespaces[namespace]:
                self.namespaces[namespace].remove(key)
            
            CACHE_EVICTIONS.labels(level=CacheLevel.MEMORY.value, namespace=namespace).inc()
            self.stats.evictions += 1
            
            return True
    
    async def exists(self, key: str, namespace: str = "default") -> bool:
        """Vérifie si une clé existe"""
        full_key = self._make_key(key, namespace)
        
        async with self.lock:
            if full_key not in self.cache:
                return False
            
            entry = self.cache[full_key]
            return not entry.is_expired()
    
    async def clear(self, namespace: Optional[str] = None) -> int:
        """Vide le cache"""
        async with self.lock:
            if namespace is None:
                # Nettoyage complet
                count = len(self.cache)
                self.cache.clear()
                self.namespaces.clear()
                self.access_order.clear()
                self.current_size_bytes = 0
                return count
            else:
                # Nettoyage par namespace
                if namespace not in self.namespaces:
                    return 0
                
                count = 0
                for key in list(self.namespaces[namespace]):
                    full_key = self._make_key(key, namespace)
                    if full_key in self.cache:
                        entry = self.cache[full_key]
                        value_size = self._estimate_size(entry.value)
                        self.current_size_bytes -= value_size
                        
                        del self.cache[full_key]
                        if full_key in self.access_order:
                            self.access_order.remove(full_key)
                        
                        count += 1
                        CACHE_EVICTIONS.labels(level=CacheLevel.MEMORY.value, namespace=namespace).inc()
                
                self.namespaces[namespace].clear()
                return count
    
    async def get_stats(self) -> CacheStats:
        """Récupère les statistiques"""
        async with self.lock:
            total_operations = self.stats.hits + self.stats.misses
            if total_operations > 0:
                self.stats.avg_hit_rate = self.stats.hits / total_operations
            
            self.stats.size_bytes = self.current_size_bytes
            self.stats.last_updated = datetime.utcnow()
            
            return self.stats
    
    async def _evict_if_needed(self, new_value_size: int) -> None:
        """Éviction LRU si nécessaire"""
        target_size = self.max_size_bytes * 0.8  # Éviction à 80%
        
        while self.current_size_bytes + new_value_size > target_size and self.access_order:
            # Éviction de la clé la moins récemment utilisée
            lru_key = self.access_order.pop(0)
            
            if lru_key in self.cache:
                entry = self.cache[lru_key]
                value_size = self._estimate_size(entry.value)
                self.current_size_bytes -= value_size
                
                # Extraction namespace et key
                parts = lru_key.split(':', 1)
                if len(parts) == 2:
                    namespace, key = parts
                    if namespace in self.namespaces and key in self.namespaces[namespace]:
                        self.namespaces[namespace].remove(key)
                
                del self.cache[lru_key]
                self.stats.evictions += 1
    
    async def _cleanup_expired(self, key: str, namespace: str) -> None:
        """Nettoyage asynchrone des entrées expirées"""
        async with self.lock:
            if key in self.cache:
                entry = self.cache[key]
                if entry.is_expired():
                    await self.delete(key.split(':', 1)[1], namespace)
    
    def _make_key(self, key: str, namespace: str) -> str:
        """Crée une clé complète"""
        return f"{namespace}:{key}"
    
    def _estimate_size(self, value: Any) -> int:
        """Estime la taille d'une valeur"""
        try:
            if isinstance(value, (str, bytes)):
                return len(value)
            elif isinstance(value, (int, float, bool)):
                return 8
            else:
                # Approximation pour les objets complexes
                return len(pickle.dumps(value))
        except:
            return 1024  # Taille par défaut


class RedisCache(CacheInterface):
    """Cache Redis avec support cluster et sécurité"""
    
    def __init__(
        self,
        redis_url: str,
        max_memory_mb: int = 1000,
        enable_encryption: bool = True,
        encryption_key: Optional[str] = None,
        enable_tls: bool = False,
        cluster_mode: bool = False
    ):
        """
        Initialise le cache Redis.
        
        Args:
            redis_url: URL Redis
            max_memory_mb: Mémoire max en MB
            enable_encryption: Activer le chiffrement
            encryption_key: Clé de chiffrement
            enable_tls: Activer TLS
            cluster_mode: Mode cluster
        """
        self.redis_url = redis_url
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.enable_encryption = enable_encryption
        self.cluster_mode = cluster_mode
        self.stats = CacheStats()
        
        # Configuration Redis
        redis_kwargs = {
            'url': redis_url,
            'max_connections': 100,
            'socket_keepalive': True,
            'health_check_interval': 30,
        }
        
        if enable_tls:
            redis_kwargs['ssl'] = True
            redis_kwargs['ssl_cert_reqs'] = 'required'
        
        # Initialisation client
        if cluster_mode:
            from redis.asyncio import RedisCluster
            self.client = RedisCluster.from_url(redis_url, **redis_kwargs)
        else:
            self.client = redis.from_url(redis_url, **redis_kwargs)
        
        # Chiffrement
        self.encryption_box = None
        if enable_encryption and encryption_key:
            self._setup_encryption(encryption_key)
        
        # Pub/Sub pour synchronisation
        self.pubsub = self.client.pubsub()
        
        # Lock distribué
        self.distributed_lock = DistributedLock(self.client)
        
        logger.info(f"RedisCache initialisé: {redis_url}")
    
    def _setup_encryption(self, key: str) -> None:
        """Configure le chiffrement"""
        try:
            # Dérivation de la clé pour NaCl
            key_hash = hashlib.sha256(key.encode()).digest()
            self.encryption_box = nacl.secret.SecretBox(key_hash)
            logger.info("Chiffrement activé pour RedisCache")
        except Exception as e:
            logger.error(f"Erreur configuration chiffrement: {str(e)}")
            self.enable_encryption = False
    
    async def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        """Récupère une valeur du cache"""
        full_key = self._make_key(key, namespace)
        
        start_time = time.time()
        try:
            # Récupération depuis Redis
            value = await self.client.get(full_key)
            
            if value is None:
                CACHE_MISSES.labels(level=CacheLevel.REDIS.value, namespace=namespace).inc()
                self.stats.misses += 1
                return None
            
            # Déchiffrement si nécessaire
            if self.enable_encryption and self.encryption_box:
                try:
                    value = self.encryption_box.decrypt(value)
                except Exception as e:
                    logger.error(f"Erreur déchiffrement: {str(e)}")
                    return None
            
            # Désérialisation
            result = self._deserialize(value)
            
            CACHE_HITS.labels(level=CacheLevel.REDIS.value, namespace=namespace).inc()
            self.stats.hits += 1
            
            latency = time.time() - start_time
            CACHE_LATENCY.labels(operation='get', level=CacheLevel.REDIS.value).observe(latency)
            
            return result
            
        except Exception as e:
            logger.error(f"Erreur récupération Redis: {str(e)}")
            CACHE_MISSES.labels(level=CacheLevel.REDIS.value, namespace=namespace).inc()
            self.stats.misses += 1
            return None
    
    async def set(
        self,
        key: str,
        value: Any,
        namespace: str = "default",
        ttl: Optional[int] = None,
        tags: Optional[List[str]] = None
    ) -> bool:
        """Définit une valeur dans le cache"""
        full_key = self._make_key(key, namespace)
        
        start_time = time.time()
        try:
            # Sérialisation
            serialized = self._serialize(value)
            
            # Chiffrement si nécessaire
            if self.enable_encryption and self.encryption_box:
                serialized = self.encryption_box.encrypt(serialized)
            
            # Stockage dans Redis
            if ttl:
                await self.client.setex(full_key, ttl, serialized)
            else:
                await self.client.set(full_key, serialized)
            
            # Gestion des tags
            if tags:
                await self._update_tags(full_key, tags)
            
            # Synchronisation Pub/Sub
            await self._publish_invalidation(full_key, "set")
            
            latency = time.time() - start_time
            CACHE_LATENCY.labels(operation='set', level=CacheLevel.REDIS.value).observe(latency)
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur stockage Redis: {str(e)}")
            return False
    
    async def delete(self, key: str, namespace: str = "default") -> bool:
        """Supprime une valeur du cache"""
        full_key = self._make_key(key, namespace)
        
        try:
            # Suppression
            result = await self.client.delete(full_key)
            
            # Nettoyage des tags
            await self._cleanup_tags(full_key)
            
            # Synchronisation Pub/Sub
            await self._publish_invalidation(full_key, "delete")
            
            if result > 0:
                CACHE_EVICTIONS.labels(level=CacheLevel.REDIS.value, namespace=namespace).inc()
                self.stats.evictions += 1
            
            return result > 0
            
        except Exception as e:
            logger.error(f"Erreur suppression Redis: {str(e)}")
            return False
    
    async def exists(self, key: str, namespace: str = "default") -> bool:
        """Vérifie si une clé existe"""
        full_key = self._make_key(key, namespace)
        
        try:
            return await self.client.exists(full_key) > 0
        except Exception as e:
            logger.error(f"Erreur vérification existence: {str(e)}")
            return False
    
    async def clear(self, namespace: Optional[str] = None) -> int:
        """Vide le cache"""
        try:
            if namespace is None:
                # Nettoyage complet
                await self.client.flushdb()
                return -1  # Nombre inconnu
            else:
                # Nettoyage par namespace
                pattern = f"{namespace}:*"
                keys = await self.client.keys(pattern)
                
                if keys:
                    await self.client.delete(*keys)
                
                return len(keys)
                
        except Exception as e:
            logger.error(f"Erreur nettoyage Redis: {str(e)}")
            return 0
    
    async def get_stats(self) -> CacheStats:
        """Récupère les statistiques"""
        try:
            info = await self.client.info('memory')
            
            # Calcul du hit rate
            stats_info = await self.client.info('stats')
            hits = int(stats_info.get('keyspace_hits', 0))
            misses = int(stats_info.get('keyspace_misses', 0))
            total = hits + misses
            
            self.stats.hits = hits
            self.stats.misses = misses
            self.stats.size_bytes = int(info.get('used_memory', 0))
            
            if total > 0:
                self.stats.avg_hit_rate = hits / total
            
            self.stats.last_updated = datetime.utcnow()
            
            return self.stats
            
        except Exception as e:
            logger.error(f"Erreur récupération stats Redis: {str(e)}")
            return self.stats
    
    async def _update_tags(self, key: str, tags: List[str]) -> None:
        """Met à jour les tags pour une clé"""
        try:
            pipeline = self.client.pipeline()
            
            for tag in tags:
                tag_key = f"tag:{tag}"
                pipeline.sadd(tag_key, key)
                pipeline.expire(tag_key, 86400)  # 24h
            
            await pipeline.execute()
        except Exception as e:
            logger.error(f"Erreur mise à jour tags: {str(e)}")
    
    async def _cleanup_tags(self, key: str) -> None:
        """Nettoie les tags pour une clé"""
        try:
            # Pattern matching pour trouver tous les tags contenant cette clé
            # (Implémentation simplifiée - pourrait être optimisée)
            pass
        except Exception as e:
            logger.error(f"Erreur nettoyage tags: {str(e)}")
    
    async def _publish_invalidation(self, key: str, action: str) -> None:
        """Publie une invalidation via Pub/Sub"""
        try:
            channel = f"cache:invalidation:{key}"
            message = {
                'key': key,
                'action': action,
                'timestamp': datetime.utcnow().isoformat(),
                'source': 'redis_cache'
            }
            
            await self.client.publish(channel, json.dumps(message))
        except Exception as e:
            logger.error(f"Erreur publication invalidation: {str(e)}")
    
    def _make_key(self, key: str, namespace: str) -> str:
        """Crée une clé Redis"""
        return f"cache:{namespace}:{key}"
    
    def _serialize(self, value: Any) -> bytes:
        """Sérialise une valeur"""
        try:
            # Utilisation de msgpack pour l'efficacité
            return msgpack.packb(value, use_bin_type=True)
        except:
            # Fallback sur JSON
            return json.dumps(value, default=json_serializer).encode()
    
    def _deserialize(self, data: bytes) -> Any:
        """Désérialise des données"""
        try:
            # Essai msgpack d'abord
            return msgpack.unpackb(data, raw=False)
        except:
            # Fallback sur JSON
            return json.loads(data.decode())
    
    async def subscribe_invalidations(
        self,
        callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """S'abonne aux invalidations"""
        try:
            await self.pubsub.subscribe("cache:invalidation:*")
            
            async for message in self.pubsub.listen():
                if message['type'] == 'message':
                    try:
                        data = json.loads(message['data'])
                        callback(data)
                    except Exception as e:
                        logger.error(f"Erreur traitement message: {str(e)}")
                        
        except Exception as e:
            logger.error(f"Erreur abonnement Pub/Sub: {str(e)}")


class DiskCache(CacheInterface):
    """Cache sur disque avec compression et chiffrement"""
    
    def __init__(
        self,
        cache_dir: str = "/tmp/microagents_cache",
        max_size_mb: int = 5000,
        enable_compression: bool = True,
        enable_encryption: bool = True,
        encryption_key: Optional[str] = None
    ):
        """
        Initialise le cache disque.
        
        Args:
            cache_dir: Répertoire de cache
            max_size_mb: Taille max en MB
            enable_compression: Activer la compression
            enable_encryption: Activer le chiffrement
            encryption_key: Clé de chiffrement
        """
        self.cache_dir = Path(cache_dir)
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.enable_compression = enable_compression
        self.enable_encryption = enable_encryption
        self.stats = CacheStats()
        self.lock = asyncio.Lock()
        
        # Création du répertoire
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Chiffrement
        self.encryption_box = None
        if enable_encryption and encryption_key:
            self._setup_encryption(encryption_key)
        
        # Index des fichiers
        self.file_index: Dict[str, Path] = {}
        self._load_index()
        
        logger.info(f"DiskCache initialisé: {cache_dir}")
    
    def _setup_encryption(self, key: str) -> None:
        """Configure le chiffrement"""
        try:
            key_hash = hashlib.sha256(key.encode()).digest()
            self.encryption_box = nacl.secret.SecretBox(key_hash)
            logger.info("Chiffrement activé pour DiskCache")
        except Exception as e:
            logger.error(f"Erreur configuration chiffrement: {str(e)}")
            self.enable_encryption = False
    
    def _load_index(self) -> None:
        """Charge l'index depuis le disque"""
        try:
            index_file = self.cache_dir / "index.json"
            if index_file.exists():
                with open(index_file, 'r') as f:
                    self.file_index = json.load(f)
        except Exception as e:
            logger.error(f"Erreur chargement index: {str(e)}")
            self.file_index = {}
    
    def _save_index(self) -> None:
        """Sauvegarde l'index sur le disque"""
        try:
            index_file = self.cache_dir / "index.json"
            with open(index_file, 'w') as f:
                json.dump(self.file_index, f)
        except Exception as e:
            logger.error(f"Erreur sauvegarde index: {str(e)}")
    
    async def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        """Récupère une valeur du cache"""
        file_path = self._get_file_path(key, namespace)
        
        async with self.lock:
            if not file_path.exists():
                CACHE_MISSES.labels(level=CacheLevel.DISK.value, namespace=namespace).inc()
                self.stats.misses += 1
                return None
            
            try:
                # Lecture du fichier
                data = file_path.read_bytes()
                
                # Déchiffrement si nécessaire
                if self.enable_encryption and self.encryption_box:
                    data = self.encryption_box.decrypt(data)
                
                # Décompression si nécessaire
                if self.enable_compression:
                    data = zlib.decompress(data)
                
                # Désérialisation
                value = pickle.loads(data)
                
                # Vérification expiration
                metadata_path = self._get_metadata_path(key, namespace)
                if metadata_path.exists():
                    with open(metadata_path, 'r') as f:
                        metadata = json.load(f)
                    
                    expires_at = metadata.get('expires_at')
                    if expires_at:
                        expires_dt = datetime.fromisoformat(expires_at)
                        if datetime.utcnow() > expires_dt:
                            await self.delete(key, namespace)
                            CACHE_MISSES.labels(level=CacheLevel.DISK.value, namespace=namespace).inc()
                            self.stats.misses += 1
                            return None
                
                CACHE_HITS.labels(level=CacheLevel.DISK.value, namespace=namespace).inc()
                self.stats.hits += 1
                
                return value
                
            except Exception as e:
                logger.error(f"Erreur lecture cache disque: {str(e)}")
                CACHE_MISSES.labels(level=CacheLevel.DISK.value, namespace=namespace).inc()
                self.stats.misses += 1
                return None
    
    async def set(
        self,
        key: str,
        value: Any,
        namespace: str = "default",
        ttl: Optional[int] = None,
        tags: Optional[List[str]] = None
    ) -> bool:
        """Définit une valeur dans le cache"""
        file_path = self._get_file_path(key, namespace)
        metadata_path = self._get_metadata_path(key, namespace)
        
        async with self.lock:
            try:
                # Sérialisation
                data = pickle.dumps(value)
                
                # Compression si nécessaire
                if self.enable_compression and len(data) > 1024:
                    data = zlib.compress(data, level=6)
                
                # Chiffrement si nécessaire
                if self.enable_encryption and self.encryption_box:
                    data = self.encryption_box.encrypt(data)
                
                # Écriture du fichier
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(data)
                
                # Métadonnées
                metadata = {
                    'created_at': datetime.utcnow().isoformat(),
                    'size': len(data),
                    'tags': tags or []
                }
                
                if ttl:
                    expires_at = datetime.utcnow() + timedelta(seconds=ttl)
                    metadata['expires_at'] = expires_at.isoformat()
                
                with open(metadata_path, 'w') as f:
                    json.dump(metadata, f)
                
                # Mise à jour index
                self.file_index[f"{namespace}:{key}"] = str(file_path)
                self._save_index()
                
                # Vérification taille
                await self._check_size_limit()
                
                CACHE_SIZE.labels(level=CacheLevel.DISK.value, namespace=namespace).set(
                    self._get_total_size()
                )
                
                return True
                
            except Exception as e:
                logger.error(f"Erreur écriture cache disque: {str(e)}")
                return False
    
    async def delete(self, key: str, namespace: str = "default") -> bool:
        """Supprime une valeur du cache"""
        file_path = self._get_file_path(key, namespace)
        metadata_path = self._get_metadata_path(key, namespace)
        
        async with self.lock:
            try:
                # Suppression fichiers
                if file_path.exists():
                    file_path.unlink()
                
                if metadata_path.exists():
                    metadata_path.unlink()
                
                # Mise à jour index
                cache_key = f"{namespace}:{key}"
                if cache_key in self.file_index:
                    del self.file_index[cache_key]
                    self._save_index()
                
                CACHE_EVICTIONS.labels(level=CacheLevel.DISK.value, namespace=namespace).inc()
                self.stats.evictions += 1
                
                return True
                
            except Exception as e:
                logger.error(f"Erreur suppression cache disque: {str(e)}")
                return False
    
    async def exists(self, key: str, namespace: str = "default") -> bool:
        """Vérifie si une clé existe"""
        file_path = self._get_file_path(key, namespace)
        return file_path.exists()
    
    async def clear(self, namespace: Optional[str] = None) -> int:
        """Vide le cache"""
        async with self.lock:
            count = 0
            
            if namespace is None:
                # Suppression complète
                import shutil
                shutil.rmtree(self.cache_dir)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self.file_index.clear()
                self._save_index()
                return -1  # Nombre inconnu
            else:
                # Suppression par namespace
                keys_to_delete = []
                
                for key in list(self.file_index.keys()):
                    if key.startswith(f"{namespace}:"):
                        file_path = Path(self.file_index[key])
                        if file_path.exists():
                            file_path.unlink()
                        
                        metadata_path = self._get_metadata_path_from_key(key)
                        if metadata_path.exists():
                            metadata_path.unlink()
                        
                        keys_to_delete.append(key)
                        count += 1
                
                for key in keys_to_delete:
                    del self.file_index[key]
                
                self._save_index()
                return count
    
    async def get_stats(self) -> CacheStats:
        """Récupère les statistiques"""
        async with self.lock:
            total_size = self._get_total_size()
            
            self.stats.size_bytes = total_size
            self.stats.last_updated = datetime.utcnow()
            
            return self.stats
    
    def _get_file_path(self, key: str, namespace: str) -> Path:
        """Retourne le chemin du fichier de cache"""
        # Hachage pour éviter les chemins trop longs
        key_hash = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / namespace / key_hash[:2] / f"{key_hash}.cache"
    
    def _get_metadata_path(self, key: str, namespace: str) -> Path:
        """Retourne le chemin du fichier de métadonnées"""
        key_hash = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / namespace / key_hash[:2] / f"{key_hash}.meta"
    
    def _get_metadata_path_from_key(self, cache_key: str) -> Path:
        """Retourne le chemin des métadonnées depuis une clé de cache"""
        namespace, key = cache_key.split(':', 1)
        return self._get_metadata_path(key, namespace)
    
    def _get_total_size(self) -> int:
        """Calcule la taille totale du cache"""
        total = 0
        for file_path_str in self.file_index.values():
            file_path = Path(file_path_str)
            if file_path.exists():
                total += file_path.stat().st_size
        return total
    
    async def _check_size_limit(self) -> None:
        """Vérifie et applique la limite de taille"""
        total_size = self._get_total_size()
        
        if total_size > self.max_size_bytes:
            await self._evict_oldest()
    
    async def _evict_oldest(self) -> None:
        """Éviction des fichiers les plus anciens"""
        try:
            # Récupération des âges des fichiers
            file_ages = []
            
            for cache_key, file_path_str in self.file_index.items():
                file_path = Path(file_path_str)
                if file_path.exists():
                    mtime = file_path.stat().st_mtime
                    file_ages.append((cache_key, file_path, mtime))
            
            # Tri par ancienneté
            file_ages.sort(key=lambda x: x[2])
            
            # Éviction jusqu'à atteindre 70% de la limite
            target_size = self.max_size_bytes * 0.7
            current_size = self._get_total_size()
            
            evicted = 0
            for cache_key, file_path, _ in file_ages:
                if current_size <= target_size:
                    break
                
                file_size = file_path.stat().st_size
                file_path.unlink()
                
                # Suppression métadonnées
                metadata_path = self._get_metadata_path_from_key(cache_key)
                if metadata_path.exists():
                    metadata_path.unlink()
                
                # Mise à jour index
                if cache_key in self.file_index:
                    del self.file_index[cache_key]
                
                current_size -= file_size
                evicted += 1
                self.stats.evictions += 1
            
            if evicted > 0:
                self._save_index()
                logger.info(f"Éviction disque: {evicted} fichiers")
                
        except Exception as e:
            logger.error(f"Erreur éviction disque: {str(e)}")


class DistributedLock:
    """Verrou distribué avec Redis"""
    
    def __init__(self, redis_client: redis.Redis, lock_prefix: str = "lock:"):
        """
        Initialise le verrou distribué.
        
        Args:
            redis_client: Client Redis
            lock_prefix: Préfixe pour les clés de verrou
        """
        self.client = redis_client
        self.lock_prefix = lock_prefix
    
    @asynccontextmanager
    async def acquire(
        self,
        lock_name: str,
        timeout: int = 10,
        expire: int = 30
    ):
        """
        Acquiert un verrou distribué.
        
        Args:
            lock_name: Nom du verrou
            timeout: Timeout d'acquisition en secondes
            expire: Expiration du verrou en secondes
            
        Yields:
            True si le verrou est acquis
        """
        lock_key = f"{self.lock_prefix}{lock_name}"
        lock_value = str(uuid.uuid4())
        start_time = time.time()
        
        try:
            # Tentative d'acquisition avec backoff exponentiel
            acquired = False
            wait_time = 0.1  # 100ms initial
            
            while time.time() - start_time < timeout:
                # Essai d'acquisition
                result = await self.client.set(
                    lock_key,
                    lock_value,
                    ex=expire,
                    nx=True  # Set if Not eXists
                )
                
                if result:
                    acquired = True
                    DISTRIBUTED_LOCK_ACQUISITIONS.inc()
                    break
                
                # Backoff exponentiel
                await asyncio.sleep(wait_time)
                wait_time = min(wait_time * 2, 1.0)  # Max 1 seconde
            
            if not acquired:
                DISTRIBUTED_LOCK_TIMEOUTS.inc()
                raise TimeoutError(f"Impossible d'acquérir le verrou: {lock_name}")
            
            # Suivi du temps d'attente
            wait_duration = time.time() - start_time
            CACHE_LOCK_WAIT_TIME.observe(wait_duration)
            
            try:
                yield True
            finally:
                # Libération du verrou
                await self._release_lock(lock_key, lock_value)
                
        except Exception as e:
            logger.error(f"Erreur verrou distribué: {str(e)}")
            raise
    
    async def _release_lock(self, lock_key: str, lock_value: str) -> None:
        """Libère un verrou"""
        try:
            # Script Lua pour libération atomique
            release_script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            
            await self.client.eval(release_script, 1, lock_key, lock_value)
        except Exception as e:
            logger.error(f"Erreur libération verrou: {str(e)}")


class MultiLevelCache(CacheInterface):
    """Cache multi-niveaux avec stratégies avancées"""
    
    def __init__(self, config: CacheConfig):
        """
        Initialise le cache multi-niveaux.
        
        Args:
            config: Configuration du cache
        """
        self.config = config
        self.strategy = config.default_strategy
        self.circuit_breaker = CircuitBreaker(config.circuit_breaker_threshold)
        
        # Initialisation des niveaux de cache
        self.levels: Dict[CacheLevel, CacheInterface] = {}
        
        # Cache mémoire
        if config.enable_memory_cache:
            self.levels[CacheLevel.MEMORY] = MemoryCache(config.max_memory_size_mb)
        
        # Cache Redis
        if config.enable_redis_cache:
            try:
                self.levels[CacheLevel.REDIS] = RedisCache(
                    redis_url=config.redis_url,
                    max_memory_mb=config.max_redis_memory_mb,
                    enable_encryption=config.enable_encryption,
                    encryption_key=config.encryption_key,
                    enable_tls=config.enable_tls,
                    cluster_mode=config.redis_cluster_mode
                )
            except Exception as e:
                logger.error(f"Erreur initialisation RedisCache: {str(e)}")
                if config.enable_failover:
                    logger.warning("Redis désactivé - failover activé")
        
        # Cache disque
        if config.enable_disk_cache:
            self.levels[CacheLevel.DISK] = DiskCache(
                cache_dir="/tmp/microagents_cache",
                max_size_mb=config.max_disk_size_mb,
                enable_compression=True,
                enable_encryption=config.enable_encryption,
                encryption_key=config.encryption_key
            )
        
        # Ordre des niveaux (du plus rapide au plus lent)
        self.level_order = [
            CacheLevel.MEMORY,
            CacheLevel.REDIS,
            CacheLevel.DISK
        ]
        
        # Filtrage des niveaux activés
        self.level_order = [
            level for level in self.level_order 
            if level in self.levels
        ]
        
        # Invalidation
        self.invalidation_queue = asyncio.Queue()
        self.invalidation_worker_task = None
        
        # Pré-chargement
        self.warming_up = False
        
        # Monitoring
        self.monitoring_task = None
        
        logger.info(f"MultiLevelCache initialisé avec {len(self.levels)} niveaux")
    
    async def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        """Récupère une valeur du cache (read-through)"""
        # Essai de récupération depuis chaque niveau
        for level in self.level_order:
            cache = self.levels.get(level)
            if cache is None:
                continue
            
            try:
                with self.circuit_breaker:
                    value = await cache.get(key, namespace)
                    
                    if value is not None:
                        # Remplissage des niveaux supérieurs (cache hierarchy)
                        await self._backfill_higher_levels(key, value, namespace, level)
                        return value
                        
            except Exception as e:
                logger.error(f"Erreur niveau {level}: {str(e)}")
                continue
        
        return None
    
    async def set(
        self,
        key: str,
        value: Any,
        namespace: str = "default",
        ttl: Optional[int] = None,
        tags: Optional[List[str]] = None
    ) -> bool:
        """Définit une valeur dans le cache"""
        ttl = ttl or self.config.default_ttl
        
        # Application de la stratégie
        if self.strategy == CacheStrategy.WRITE_THROUGH:
            return await self._write_through(key, value, namespace, ttl, tags)
        elif self.strategy == CacheStrategy.WRITE_BEHIND:
            return await self._write_behind(key, value, namespace, ttl, tags)
        elif self.strategy == CacheStrategy.CACHE_ASIDE:
            return await self._cache_aside(key, value, namespace, ttl, tags)
        else:
            # Par défaut: write-through
            return await self._write_through(key, value, namespace, ttl, tags)
    
    async def delete(self, key: str, namespace: str = "default") -> bool:
        """Supprime une valeur du cache"""
        success = True
        
        # Suppression de tous les niveaux
        for level in self.level_order:
            cache = self.levels.get(level)
            if cache is None:
                continue
            
            try:
                with self.circuit_breaker:
                    if not await cache.delete(key, namespace):
                        success = False
            except Exception as e:
                logger.error(f"Erreur suppression niveau {level}: {str(e)}")
                success = False
        
        # Invalidation distribuée
        await self._distribute_invalidation(key, namespace, "delete")
        
        return success
    
    async def exists(self, key: str, namespace: str = "default") -> bool:
        """Vérifie si une clé existe"""
        # Vérification niveau mémoire d'abord (le plus rapide)
        if CacheLevel.MEMORY in self.levels:
            try:
                with self.circuit_breaker:
                    if await self.levels[CacheLevel.MEMORY].exists(key, namespace):
                        return True
            except:
                pass
        
        # Vérification autres niveaux
        for level in self.level_order:
            if level == CacheLevel.MEMORY:
                continue
            
            cache = self.levels.get(level)
            if cache is None:
                continue
            
            try:
                with self.circuit_breaker:
                    if await cache.exists(key, namespace):
                        return True
            except Exception as e:
                logger.error(f"Erreur vérification niveau {level}: {str(e)}")
                continue
        
        return False
    
    async def clear(self, namespace: Optional[str] = None) -> int:
        """Vide le cache"""
        total = 0
        
        for level in self.level_order:
            cache = self.levels.get(level)
            if cache is None:
                continue
            
            try:
                with self.circuit_breaker:
                    count = await cache.clear(namespace)
                    if count > 0:
                        total += count
            except Exception as e:
                logger.error(f"Erreur nettoyage niveau {level}: {str(e)}")
        
        return total
    
    async def get_stats(self) -> Dict[CacheLevel, CacheStats]:
        """Récupère les statistiques par niveau"""
        stats = {}
        
        for level, cache in self.levels.items():
            try:
                stats[level] = await cache.get_stats()
            except Exception as e:
                logger.error(f"Erreur stats niveau {level}: {str(e)}")
        
        return stats
    
    # ==================== STRATÉGIES D'ÉCRITURE ====================
    
    async def _write_through(
        self,
        key: str,
        value: Any,
        namespace: str,
        ttl: int,
        tags: Optional[List[str]]
    ) -> bool:
        """Write-through: écrit dans tous les niveaux"""
        success = True
        
        for level in self.level_order:
            cache = self.levels.get(level)
            if cache is None:
                continue
            
            try:
                with self.circuit_breaker:
                    if not await cache.set(key, value, namespace, ttl, tags):
                        success = False
            except Exception as e:
                logger.error(f"Erreur write-through niveau {level}: {str(e)}")
                success = False
        
        # Invalidation distribuée
        if success:
            await self._distribute_invalidation(key, namespace, "set")
        
        return success
    
    async def _write_behind(
        self,
        key: str,
        value: Any,
        namespace: str,
        ttl: int,
        tags: Optional[List[str]]
    ) -> bool:
        """Write-behind: écrit en mémoire immédiatement, autres niveaux en async"""
        # Écriture immédiate en mémoire
        memory_cache = self.levels.get(CacheLevel.MEMORY)
        if memory_cache:
            try:
                await memory_cache.set(key, value, namespace, ttl, tags)
            except Exception as e:
                logger.error(f"Erreur write-behind mémoire: {str(e)}")
        
        # Écriture async pour autres niveaux
        asyncio.create_task(
            self._async_write_behind(key, value, namespace, ttl, tags)
        )
        
        return True
    
    async def _async_write_behind(
        self,
        key: str,
        value: Any,
        namespace: str,
        ttl: int,
        tags: Optional[List[str]]
    ) -> None:
        """Écriture asynchrone pour write-behind"""
        for level in self.level_order:
            if level == CacheLevel.MEMORY:
                continue
            
            cache = self.levels.get(level)
            if cache is None:
                continue
            
            try:
                with self.circuit_breaker:
                    await cache.set(key, value, namespace, ttl, tags)
            except Exception as e:
                logger.error(f"Erreur write-behind async niveau {level}: {str(e)}")
        
        # Invalidation distribuée
        await self._distribute_invalidation(key, namespace, "set")
    
    async def _cache_aside(
        self,
        key: str,
        value: Any,
        namespace: str,
        ttl: int,
        tags: Optional[List[str]]
    ) -> bool:
        """Cache-aside: écrit seulement en mémoire, chargement à la demande"""
        # Écriture en mémoire seulement
        memory_cache = self.levels.get(CacheLevel.MEMORY)
        if memory_cache:
            try:
                return await memory_cache.set(key, value, namespace, ttl, tags)
            except Exception as e:
                logger.error(f"Erreur cache-aside: {str(e)}")
        
        return False
    
    # ==================== FONCTIONNALITÉS AVANCÉES ====================
    
    async def _backfill_higher_levels(
        self,
        key: str,
        value: Any,
        namespace: str,
        source_level: CacheLevel
    ) -> None:
        """Remplit les niveaux supérieurs avec la valeur trouvée"""
        source_index = self.level_order.index(source_level)
        
        for i in range(source_index):
            higher_level = self.level_order[i]
            cache = self.levels.get(higher_level)
            
            if cache is None:
                continue
            
            try:
                # Récupération TTL depuis le niveau source si possible
                ttl = None
                if isinstance(cache, RedisCache):
                    # Essai de récupération TTL depuis Redis
                    try:
                        full_key = cache._make_key(key, namespace)
                        ttl = await cache.client.ttl(full_key)
                        if ttl < 0:
                            ttl = None
                    except:
                        pass
                
                await cache.set(key, value, namespace, ttl)
            except Exception as e:
                logger.error(f"Erreur backfill niveau {higher_level}: {str(e)}")
    
    async def _distribute_invalidation(
        self,
        key: str,
        namespace: str,
        action: str
    ) -> None:
        """Distribue l'invalidation à tous les nœuds"""
        # Mise en file d'attente pour traitement asynchrone
        await self.invalidation_queue.put({
            'key': key,
            'namespace': namespace,
            'action': action,
            'timestamp': datetime.utcnow()
        })
    
    async def _invalidation_worker(self) -> None:
        """Travailleur de traitement des invalidations"""
        while True:
            try:
                invalidation = await self.invalidation_queue.get()
                
                # Publication sur tous les canaux Redis disponibles
                for level, cache in self.levels.items():
                    if isinstance(cache, RedisCache):
                        await cache._publish_invalidation(
                            cache._make_key(invalidation['key'], invalidation['namespace']),
                            invalidation['action']
                        )
                
                self.invalidation_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur travailleur invalidation: {str(e)}")
                await asyncio.sleep(1)
    
    async def warm_cache(self, keys: List[Tuple[str, str]]) -> None:
        """Pré-charge le cache avec les clés spécifiées"""
        self.warming_up = True
        
        try:
            # Chargement par lots
            batch_size = 100
            for i in range(0, len(keys), batch_size):
                batch = keys[i:i + batch_size]
                
                # Chargement asynchrone
                tasks = []
                for key, namespace in batch:
                    task = self.get(key, namespace)
                    tasks.append(task)
                
                await asyncio.gather(*tasks, return_exceptions=True)
                
                logger.info(f"Pré-chargement: {i + len(batch)}/{len(keys)}")
                
        finally:
            self.warming_up = False
            logger.info("Pré-chargement terminé")
    
    async def refresh_ahead(self, key: str, namespace: str = "default") -> bool:
        """Rafraîchissement anticipé du cache"""
        try:
            # Logique de rafraîchissement
            # À implémenter selon votre logique métier
            return True
        except Exception as e:
            logger.error(f"Erreur refresh-ahead: {str(e)}")
            return False
    
    async def invalidate_by_tags(self, tags: List[str]) -> int:
        """Invalide le cache par tags"""
        count = 0
        
        # Implémentation simplifiée
        # Pour une implémentation complète, maintenir un index tag->clés
        for level, cache in self.levels.items():
            if isinstance(cache, RedisCache):
                try:
                    # Récupération des clés par tag
                    for tag in tags:
                        tag_key = f"tag:{tag}"
                        keys = await cache.client.smembers(tag_key)
                        
                        if keys:
                            await cache.client.delete(*keys)
                            count += len(keys)
                except Exception as e:
                    logger.error(f"Erreur invalidation par tags niveau {level}: {str(e)}")
        
        return count
    
    async def start_monitoring(self) -> None:
        """Démarre le monitoring du cache"""
        if self.monitoring_task:
            return
        
        self.monitoring_task = asyncio.create_task(self._monitoring_loop())
    
    async def _monitoring_loop(self) -> None:
        """Boucle de monitoring"""
        while True:
            try:
                # Collecte des stats
                stats = await self.get_stats()
                
                # Logging des stats
                for level, stat in stats.items():
                    logger.info(
                        f"Cache {level}: "
                        f"hit_rate={stat.avg_hit_rate:.2%} "
                        f"size={stat.size_bytes / 1024 / 1024:.1f}MB"
                    )
                
                # Vérification santé
                for level, cache in self.levels.items():
                    if isinstance(cache, RedisCache):
                        try:
                            await cache.client.ping()
                        except:
                            logger.warning(f"Cache {level} non répondant")
                
                await asyncio.sleep(self.config.stats_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur monitoring: {str(e)}")
                await asyncio.sleep(60)
    
    async def close(self) -> None:
        """Ferme proprement le cache"""
        # Arrêt monitoring
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        
        # Arrêt travailleur invalidation
        if self.invalidation_worker_task:
            self.invalidation_worker_task.cancel()
            try:
                await self.invalidation_worker_task
            except asyncio.CancelledError:
                pass
        
        # Fermeture Redis
        redis_cache = self.levels.get(CacheLevel.REDIS)
        if redis_cache:
            await redis_cache.client.close()


class CircuitBreaker:
    """Circuit breaker pour gestion des pannes"""
    
    def __init__(self, failure_threshold: int = 5):
        """
        Initialise le circuit breaker.
        
        Args:
            failure_threshold: Seuil de défaillance
        """
        self.failure_threshold = failure_threshold
        self.failure_count = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.last_failure_time = None
        self.reset_timeout = 60  # 60 secondes
        
    def __enter__(self):
        """Vérifie l'état du circuit breaker"""
        if self.state == "OPEN":
            # Vérifie si le timeout de reset est écoulé
            if (self.last_failure_time and 
                (datetime.utcnow() - self.last_failure_time).total_seconds() > self.reset_timeout):
                self.state = "HALF_OPEN"
            else:
                raise Exception("Circuit breaker OPEN")
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Met à jour l'état du circuit breaker"""
        if exc_type is not None:
            # Échec
            self.failure_count += 1
            self.last_failure_time = datetime.utcnow()
            
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
        else:
            # Succès
            if self.state == "HALF_OPEN":
                self.state = "CLOSED"
            self.failure_count = max(0, self.failure_count - 1)


# Décorateurs utilitaires
def cached(
    ttl: int = 3600,
    namespace: str = "default",
    cache_level: CacheLevel = CacheLevel.MEMORY,
    key_prefix: str = "func",
    invalidate_on_args: Optional[List[int]] = None
):
    """
    Décorateur pour mettre en cache les résultats de fonction.
    
    Args:
        ttl: Time to live en secondes
        namespace: Namespace du cache
        cache_level: Niveau de cache
        key_prefix: Préfixe pour la clé de cache
        invalidate_on_args: Positions d'arguments à inclure dans la clé
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Génération de la clé de cache
            cache_key_parts = [key_prefix, func.__name__]
            
            if invalidate_on_args:
                for pos in invalidate_on_args:
                    if pos < len(args):
                        cache_key_parts.append(str(args[pos]))
            
            # Ajout des kwargs
            for key, value in sorted(kwargs.items()):
                cache_key_parts.append(f"{key}={value}")
            
            cache_key = hashlib.md5(":".join(cache_key_parts).encode()).hexdigest()
            
            # Récupération depuis le cache
            cache = get_cache()  # Fonction singleton à implémenter
            if cache:
                cached_value = await cache.get(cache_key, namespace)
                if cached_value is not None:
                    return cached_value
            
            # Exécution de la fonction
            result = await func(*args, **kwargs)
            
            # Mise en cache
            if cache:
                await cache.set(cache_key, result, namespace, ttl)
            
            return result
        
        return wrapper
    
    return decorator


def cache_invalidate(
    namespace: str = "default",
    key_prefix: str = "func",
    invalidate_on_args: Optional[List[int]] = None
):
    """
    Décorateur pour invalider le cache après exécution.
    
    Args:
        namespace: Namespace du cache
        key_prefix: Préfixe pour la clé de cache
        invalidate_on_args: Positions d'arguments à inclure dans la clé
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Exécution de la fonction
            result = await func(*args, **kwargs)
            
            # Génération de la clé de cache
            cache_key_parts = [key_prefix, func.__name__]
            
            if invalidate_on_args:
                for pos in invalidate_on_args:
                    if pos < len(args):
                        cache_key_parts.append(str(args[pos]))
            
            cache_key = hashlib.md5(":".join(cache_key_parts).encode()).hexdigest()
            
            # Invalidation du cache
            cache = get_cache()
            if cache:
                await cache.delete(cache_key, namespace)
            
            return result
        
        return wrapper
    
    return decorator


# Singleton pour le cache global
_cache_instance: Optional[MultiLevelCache] = None

def init_cache(config: Optional[CacheConfig] = None) -> MultiLevelCache:
    """
    Initialise le cache global.
    
    Args:
        config: Configuration du cache
        
    Returns:
        Instance MultiLevelCache
    """
    global _cache_instance
    
    if _cache_instance is not None:
        return _cache_instance
    
    if config is None:
        config = CacheConfig()
    
    _cache_instance = MultiLevelCache(config)
    
    # Démarrage monitoring
    asyncio.create_task(_cache_instance.start_monitoring())
    
    # Démarrage travailleur invalidation
    _cache_instance.invalidation_worker_task = asyncio.create_task(
        _cache_instance._invalidation_worker()
    )
    
    return _cache_instance

def get_cache() -> Optional[MultiLevelCache]:
    """
    Retourne l'instance du cache global.
    
    Returns:
        Instance MultiLevelCache ou None
    """
    return _cache_instance


# Exemple d'utilisation
if __name__ == "__main__":
    import asyncio
    
    async def demo():
        # Configuration
        config = CacheConfig(
            redis_url="redis://localhost:6379",
            enable_memory_cache=True,
            enable_redis_cache=True,
            enable_disk_cache=False,
            default_strategy=CacheStrategy.WRITE_THROUGH,
            default_ttl=300
        )
        
        # Initialisation
        cache = init_cache(config)
        
        # Exemples d'utilisation
        await cache.set("user:123", {"name": "John", "age": 30}, "users", ttl=60)
        
        user = await cache.get("user:123", "users")
        print(f"Utilisateur récupéré: {user}")
        
        exists = await cache.exists("user:123", "users")
        print(f"Existe: {exists}")
        
        # Utilisation avec décorateur
        @cached(ttl=300, namespace="calculations")
        async def expensive_calculation(x: int, y: int) -> int:
            await asyncio.sleep(1)  # Simulation calcul coûteux
            return x * y
        
        result = await expensive_calculation(10, 20)
        print(f"Résultat calcul: {result}")
        
        # Récupération stats
        stats = await cache.get_stats()
        for level, stat in stats.items():
            print(f"{level}: {stat.to_dict()}")
        
        # Fermeture propre
        await cache.close()
    
    asyncio.run(demo())