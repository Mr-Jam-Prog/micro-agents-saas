"""
Chain Selector - Sélectionne la meilleure chaîne d'agents pour une intention
Utilise un graphe local d'agents avec indexation O(1) et scoring dynamique.
"""

import asyncio
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
import logging
from enum import Enum
import heapq
from datetime import datetime

from ..types import Intent, DecisionContext, AgentChain
from ..exceptions import NoChainAvailableError

logger = logging.getLogger(__name__)


class AgentHealth(Enum):
    """État de santé d'un agent."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class AgentMetadata:
    """Métadonnées d'un agent pour la sélection."""
    agent_id: str
    name: str
    description: str
    version: str
    capabilities: List[str]  # Types d'intentions supportées
    input_contracts: List[Dict[str, Any]]  # Formats d'entrée acceptés
    output_contracts: List[Dict[str, Any]]  # Formats de sortie produits
    cost_per_execution: float
    estimated_success_rate: float  # 0.0-1.0
    avg_execution_time_seconds: float
    dependencies: List[str]  # Autres agents requis
    compatibility_constraints: Dict[str, Any]
    health: AgentHealth = AgentHealth.UNKNOWN
    last_seen: Optional[datetime] = None
    execution_count: int = 0
    success_count: int = 0


@dataclass
class Edge:
    """Arête dans le graphe d'agents."""
    source_agent: str
    target_agent: str
    compatibility_score: float = 1.0
    avg_transfer_latency: float = 0.0
    required_transformations: List[Dict] = field(default_factory=list)


@dataclass
class ChainCandidate:
    """Candidat de chaîne avec score."""
    agents: List[str]
    total_cost: float
    expected_value: float
    compatibility_score: float
    health_score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentRegistry:
    """Registre d'agents dynamique avec indexation O(1)."""
    
    def __init__(self):
        self._agents: Dict[str, AgentMetadata] = {}
        self._index_by_intent: Dict[str, Set[str]] = {}
        self._index_by_capability: Dict[str, Set[str]] = {}
        self._edges: Dict[Tuple[str, str], Edge] = {}
        self._agent_graph: Dict[str, Set[str]] = {}  # adjacency list
        
    def register_agent(self, agent: AgentMetadata) -> None:
        """Enregistre un agent dans le registre."""
        self._agents[agent.agent_id] = agent
        
        # Indexation par intention
        for capability in agent.capabilities:
            if capability not in self._index_by_intent:
                self._index_by_intent[capability] = set()
            self._index_by_intent[capability].add(agent.agent_id)
            
        # Indexation par capacité (plus générique)
        for capability in agent.capabilities:
            if capability not in self._index_by_capability:
                self._index_by_capability[capability] = set()
            self._index_by_capability[capability].add(agent.agent_id)
        
        # Initialiser le graphe
        self._agent_graph[agent.agent_id] = set()
        
        logger.info(f"Registered agent: {agent.agent_id} with capabilities: {agent.capabilities}")
    
    def unregister_agent(self, agent_id: str) -> None:
        """Désenregistre un agent."""
        if agent_id not in self._agents:
            return
            
        agent = self._agents[agent_id]
        
        # Nettoyer les index
        for capability in agent.capabilities:
            if capability in self._index_by_intent:
                self._index_by_intent[capability].discard(agent_id)
                if not self._index_by_intent[capability]:
                    del self._index_by_intent[capability]
                    
            if capability in self._index_by_capability:
                self._index_by_capability[capability].discard(agent_id)
                if not self._index_by_capability[capability]:
                    del self._index_by_capability[capability]
        
        # Nettoyer les arêtes
        edges_to_remove = [
            (source, target) for (source, target) in self._edges
            if source == agent_id or target == agent_id
        ]
        for edge in edges_to_remove:
            del self._edges[edge]
        
        # Nettoyer le graphe
        del self._agent_graph[agent_id]
        for neighbors in self._agent_graph.values():
            neighbors.discard(agent_id)
        
        del self._agents[agent_id]
        logger.info(f"Unregistered agent: {agent_id}")
    
    def add_edge(self, edge: Edge) -> None:
        """Ajoute une arête entre deux agents."""
        if edge.source_agent not in self._agents or edge.target_agent not in self._agents:
            raise ValueError(f"Invalid agents: {edge.source_agent} -> {edge.target_agent}")
            
        self._edges[(edge.source_agent, edge.target_agent)] = edge
        self._agent_graph[edge.source_agent].add(edge.target_agent)
        logger.debug(f"Added edge: {edge.source_agent} -> {edge.target_agent}")
    
    def get_agents_by_intent(self, intent_type: str) -> List[AgentMetadata]:
        """Récupère les agents qui supportent une intention spécifique."""
        agent_ids = self._index_by_intent.get(intent_type, set())
        return [self._agents[agent_id] for agent_id in agent_ids if agent_id in self._agents]
    
    def get_agents_by_capability(self, capability: str) -> List[AgentMetadata]:
        """Récupère les agents avec une capacité spécifique."""
        agent_ids = self._index_by_capability.get(capability, set())
        return [self._agents[agent_id] for agent_id in agent_ids if agent_id in self._agents]
    
    def get_agent(self, agent_id: str) -> Optional[AgentMetadata]:
        """Récupère un agent par son ID."""
        return self._agents.get(agent_id)
    
    def get_compatible_agents(self, source_agent: str) -> List[Tuple[AgentMetadata, Edge]]:
        """Récupère les agents compatibles avec un agent source."""
        compatible = []
        for target_agent in self._agent_graph.get(source_agent, []):
            edge = self._edges.get((source_agent, target_agent))
            if edge:
                agent = self.get_agent(target_agent)
                if agent:
                    compatible.append((agent, edge))
        return compatible
    
    def update_agent_health(self, agent_id: str, health: AgentHealth) -> None:
        """Met à jour la santé d'un agent."""
        if agent_id in self._agents:
            self._agents[agent_id].health = health
            self._agents[agent_id].last_seen = datetime.utcnow()
    
    def record_execution(self, agent_id: str, success: bool) -> None:
        """Enregistre une exécution d'agent."""
        if agent_id in self._agents:
            self._agents[agent_id].execution_count += 1
            if success:
                self._agents[agent_id].success_count += 1
            
            # Mettre à jour le taux de succès estimé
            total = self._agents[agent_id].execution_count
            successes = self._agents[agent_id].success_count
            self._agents[agent_id].estimated_success_rate = successes / total if total > 0 else 0.5
    
    def get_registry_stats(self) -> Dict[str, Any]:
        """Retourne des statistiques sur le registre."""
        total_agents = len(self._agents)
        healthy_agents = sum(1 for a in self._agents.values() if a.health == AgentHealth.HEALTHY)
        
        return {
            "total_agents": total_agents,
            "healthy_agents": healthy_agents,
            "unhealthy_agents": total_agents - healthy_agents,
            "intent_types": len(self._index_by_intent),
            "total_edges": len(self._edges),
            "avg_connections_per_agent": len(self._edges) / total_agents if total_agents > 0 else 0
        }


class ChainSelector:
    """
    Sélecteur de chaîne d'agents dynamique.
    
    Responsabilités:
    1. Sélectionner une chaîne optimale basée sur l'intention
    2. Prendre en compte ROI, disponibilité, compatibilité
    3. Utiliser un graphe local (pas global)
    4. Indexation O(1) pour les recherches
    """
    
    def __init__(
        self,
        registry: AgentRegistry,
        health_checker = None,
        max_chain_length: int = 5,
        min_compatibility_score: float = 0.7
    ):
        """
        Initialise le ChainSelector.
        
        Args:
            registry: Registre d'agents
            health_checker: Vérificateur de santé d'agents (optionnel)
            max_chain_length: Longueur maximale de chaîne
            min_compatibility_score: Score de compatibilité minimum
        """
        self.registry = registry
        self.health_checker = health_checker
        self.max_chain_length = max_chain_length
        self.min_compatibility_score = min_compatibility_score
        self.logger = logger
        
        # Cache des chaînes par intention
        self._chain_cache: Dict[str, List[ChainCandidate]] = {}
        self._cache_ttl_seconds = 300  # 5 minutes
        
    async def select(
        self,
        intent: Intent,
        context: DecisionContext
    ) -> AgentChain:
        """
        Sélectionne la meilleure chaîne d'agents.
        
        Args:
            intent: Intention canonique
            context: Contexte décisionnel
            
        Returns:
            AgentChain: Chaîne d'agents sélectionnée
            
        Raises:
            NoChainAvailableError: Si aucune chaîne valide n'est trouvée
        """
        start_time = datetime.utcnow()
        
        self.logger.info(
            "chain_selection_started",
            intent_type=intent.type,
            intent_priority=intent.priority,
            context_id=id(context)
        )
        
        try:
            # 1. Vérifier la santé des agents
            if self.health_checker:
                await self._check_agent_health()
            
            # 2. Récupérer ou générer les chaînes candidates
            cache_key = f"{intent.type}:{intent.priority}"
            candidates = self._chain_cache.get(cache_key)
            
            if not candidates:
                candidates = await self._generate_candidates(intent, context)
                if candidates:
                    self._chain_cache[cache_key] = candidates
            
            if not candidates:
                self.logger.error(
                    "no_chains_found",
                    intent_type=intent.type,
                    available_agents=len(self.registry._agents)
                )
                raise NoChainAvailableError(
                    f"No valid chains found for intent: {intent.type}. "
                    f"Available agents: {len(self.registry._agents)}"
                )
            
            # 3. Filtrer les chaînes selon le contexte
            filtered_candidates = await self._filter_candidates(candidates, context)
            
            if not filtered_candidates:
                self.logger.warning(
                    "all_chains_filtered_out",
                    intent_type=intent.type,
                    initial_candidates=len(candidates)
                )
                raise NoChainAvailableError(
                    f"All chains filtered out for intent: {intent.type}. "
                    "Check agent availability or compatibility constraints."
                )
            
            # 4. Sélectionner la meilleure chaîne
            best_candidate = await self._select_best_candidate(filtered_candidates, intent, context)
            
            # 5. Valider la chaîne sélectionnée
            await self._validate_chain(best_candidate)
            
            # 6. Créer l'AgentChain
            agent_chain = AgentChain(
                agents=best_candidate.agents,
                expected_value=best_candidate.expected_value,
                metadata={
                    "selection_timestamp": datetime.utcnow().isoformat(),
                    "selection_duration": (datetime.utcnow() - start_time).total_seconds(),
                    "candidate_count": len(filtered_candidates),
                    "compatibility_score": best_candidate.compatibility_score,
                    "total_cost": best_candidate.total_cost,
                    "health_score": best_candidate.health_score
                }
            )
            
            self.logger.info(
                "chain_selected",
                intent_type=intent.type,
                selected_chain=best_candidate.agents,
                chain_length=len(best_candidate.agents),
                expected_value=best_candidate.expected_value,
                selection_duration=(datetime.utcnow() - start_time).total_seconds()
            )
            
            return agent_chain
            
        except NoChainAvailableError:
            raise
        except Exception as e:
            self.logger.error(
                "chain_selection_failed",
                intent_type=intent.type,
                error=str(e),
                error_type=e.__class__.__name__
            )
            raise NoChainAvailableError(f"Chain selection failed: {str(e)}")
    
    async def _generate_candidates(
        self,
        intent: Intent,
        context: DecisionContext
    ) -> List[ChainCandidate]:
        """Génère les chaînes candidates pour une intention."""
        candidates = []
        
        # Récupérer les agents qui peuvent traiter cette intention
        starting_agents = self.registry.get_agents_by_intent(intent.type)
        
        if not starting_agents:
            self.logger.warning(
                "no_starting_agents",
                intent_type=intent.type,
                registry_intents=list(self.registry._index_by_intent.keys())
            )
            return candidates
        
        # Générer des chaînes à partir de chaque agent de départ
        for start_agent in starting_agents:
            # Chaîne à agent unique
            chain = [start_agent.agent_id]
            candidate = await self._evaluate_chain(chain, intent, context)
            if candidate:
                candidates.append(candidate)
            
            # Chaînes plus longues (DFS limitée)
            await self._dfs_generate_chains(
                current_chain=chain,
                depth=1,
                candidates=candidates,
                intent=intent,
                context=context,
                visited=set(chain)
            )
        
        # Trier par valeur attendue
        candidates.sort(key=lambda c: c.expected_value, reverse=True)
        
        return candidates
    
    async def _dfs_generate_chains(
        self,
        current_chain: List[str],
        depth: int,
        candidates: List[ChainCandidate],
        intent: Intent,
        context: DecisionContext,
        visited: Set[str]
    ):
        """Génère des chaînes par DFS limitée en profondeur."""
        if depth >= self.max_chain_length:
            return
        
        last_agent = current_chain[-1]
        
        # Récupérer les agents compatibles
        compatible_agents = self.registry.get_compatible_agents(last_agent)
        
        for next_agent, edge in compatible_agents:
            if next_agent.agent_id in visited:
                continue
                
            if edge.compatibility_score < self.min_compatibility_score:
                continue
            
            # Nouvelle chaîne
            new_chain = current_chain + [next_agent.agent_id]
            
            # Évaluer cette chaîne
            candidate = await self._evaluate_chain(new_chain, intent, context)
            if candidate:
                candidates.append(candidate)
            
            # Explorer récursivement
            await self._dfs_generate_chains(
                current_chain=new_chain,
                depth=depth + 1,
                candidates=candidates,
                intent=intent,
                context=context,
                visited=visited | {next_agent.agent_id}
            )
    
    async def _evaluate_chain(
        self,
        agent_chain: List[str],
        intent: Intent,
        context: DecisionContext
    ) -> Optional[ChainCandidate]:
        """Évalue une chaîne d'agents et retourne un candidat."""
        if not agent_chain:
            return None
        
        # Vérifier que tous les agents existent
        agents_metadata = []
        for agent_id in agent_chain:
            agent = self.registry.get_agent(agent_id)
            if not agent:
                return None
            agents_metadata.append(agent)
        
        # Calculer le coût total
        total_cost = sum(agent.cost_per_execution for agent in agents_metadata)
        
        # Calculer le score de compatibilité
        compatibility_score = self._calculate_compatibility_score(agent_chain)
        
        # Calculer le score de santé
        health_score = self._calculate_health_score(agents_metadata)
        
        # Calculer le taux de succès estimé
        estimated_success = self._calculate_estimated_success(agents_metadata)
        
        # Calculer la valeur attendue
        business_value = context.business_value.get("expected_roi", 1.0)
        expected_value = (business_value * estimated_success) - total_cost
        
        # Vérifier les contraintes minimales
        if compatibility_score < self.min_compatibility_score:
            return None
        
        if health_score < 0.5:  # Au moins la moitié des agents en santé
            return None
        
        return ChainCandidate(
            agents=agent_chain,
            total_cost=total_cost,
            expected_value=expected_value,
            compatibility_score=compatibility_score,
            health_score=health_score,
            metadata={
                "estimated_success": estimated_success,
                "business_value": business_value,
                "agent_count": len(agent_chain),
                "agent_types": [agent.name for agent in agents_metadata]
            }
        )
    
    def _calculate_compatibility_score(self, agent_chain: List[str]) -> float:
        """Calcule le score de compatibilité d'une chaîne."""
        if len(agent_chain) < 2:
            return 1.0
        
        total_score = 0.0
        valid_edges = 0
        
        for i in range(len(agent_chain) - 1):
            source = agent_chain[i]
            target = agent_chain[i + 1]
            
            edge = self.registry._edges.get((source, target))
            if edge:
                total_score += edge.compatibility_score
                valid_edges += 1
        
        if valid_edges == 0:
            return 0.0
        
        return total_score / valid_edges
    
    def _calculate_health_score(self, agents_metadata: List[AgentMetadata]) -> float:
        """Calcule le score de santé d'une liste d'agents."""
        if not agents_metadata:
            return 0.0
        
        health_values = {
            AgentHealth.HEALTHY: 1.0,
            AgentHealth.DEGRADED: 0.7,
            AgentHealth.UNHEALTHY: 0.3,
            AgentHealth.UNKNOWN: 0.5
        }
        
        total_score = sum(health_values.get(agent.health, 0.5) for agent in agents_metadata)
        return total_score / len(agents_metadata)
    
    def _calculate_estimated_success(self, agents_metadata: List[AgentMetadata]) -> float:
        """Calcule le taux de succès estimé d'une chaîne."""
        if not agents_metadata:
            return 0.0
        
        # Produit des taux de succès individuels (indépendance supposée)
        success_product = 1.0
        for agent in agents_metadata:
            success_product *= agent.estimated_success_rate
        
        # Ajuster pour la longueur de chaîne (plus longue = plus de risques)
        length_penalty = 0.95 ** (len(agents_metadata) - 1)
        
        return success_product * length_penalty
    
    async def _filter_candidates(
        self,
        candidates: List[ChainCandidate],
        context: DecisionContext
    ) -> List[ChainCandidate]:
        """Filtre les candidats selon le contexte."""
        filtered = []
        
        for candidate in candidates:
            # Vérifier les contraintes de compliance
            if not self._check_compliance_constraints(candidate, context):
                continue
            
            # Vérifier les contraintes de budget
            if not self._check_budget_constraints(candidate, context):
                continue
            
            # Vérifier la disponibilité des agents
            if not await self._check_agent_availability(candidate):
                continue
            
            filtered.append(candidate)
        
        return filtered
    
    def _check_compliance_constraints(
        self,
        candidate: ChainCandidate,
        context: DecisionContext
    ) -> bool:
        """Vérifie les contraintes de compliance."""
        constraints = context.compliance_constraints
        
        # Vérifier chaque agent dans la chaîne
        for agent_id in candidate.agents:
            agent = self.registry.get_agent(agent_id)
            if not agent:
                return False
            
            # Vérifier les contraintes de compatibilité
            agent_constraints = agent.compatibility_constraints
            for constraint_key, constraint_value in constraints.items():
                if constraint_key in agent_constraints:
                    agent_value = agent_constraints[constraint_key]
                    if isinstance(constraint_value, list):
                        if not any(v in agent_value for v in constraint_value):
                            return False
                    elif agent_value != constraint_value:
                        return False
        
        return True
    
    def _check_budget_constraints(
        self,
        candidate: ChainCandidate,
        context: DecisionContext
    ) -> bool:
        """Vérifie les contraintes de budget."""
        client_prefs = context.client_preferences
        budget = client_prefs.get("budget_constraints", {})
        
        if not budget:
            return True
        
        monthly_limit = budget.get("monthly_limit")
        if monthly_limit and candidate.total_cost > monthly_limit * 0.1:  # Max 10% du budget mensuel
            return False
        
        return True
    
    async def _check_agent_availability(self, candidate: ChainCandidate) -> bool:
        """Vérifie la disponibilité des agents."""
        for agent_id in candidate.agents:
            agent = self.registry.get_agent(agent_id)
            if not agent:
                return False
            
            # Vérifier la santé
            if agent.health in [AgentHealth.UNHEALTHY, AgentHealth.UNKNOWN]:
                # Essayer une vérification de santé en temps réel
                if self.health_checker:
                    health = await self.health_checker.check_health(agent_id)
                    if health in [AgentHealth.UNHEALTHY, AgentHealth.UNKNOWN]:
                        return False
                else:
                    return False
        
        return True
    
    async def _select_best_candidate(
        self,
        candidates: List[ChainCandidate],
        intent: Intent,
        context: DecisionContext
    ) -> ChainCandidate:
        """Sélectionne le meilleur candidat."""
        if not candidates:
            raise NoChainAvailableError("No candidates available")
        
        # Pondération des critères
        weights = {
            "expected_value": 0.4,
            "compatibility_score": 0.3,
            "health_score": 0.2,
            "inverse_length": 0.1  # Préférer les chaînes plus courtes
        }
        
        best_score = -float('inf')
        best_candidate = None
        
        for candidate in candidates:
            # Score normalisé de la valeur attendue
            max_value = max(c.expected_value for c in candidates)
            min_value = min(c.expected_value for c in candidates)
            if max_value > min_value:
                value_score = (candidate.expected_value - min_value) / (max_value - min_value)
            else:
                value_score = 1.0
            
            # Score de compatibilité
            compat_score = candidate.compatibility_score
            
            # Score de santé
            health_score = candidate.health_score
            
            # Score de longueur (préférer plus court)
            max_length = max(len(c.agents) for c in candidates)
            min_length = min(len(c.agents) for c in candidates)
            if max_length > min_length:
                length_score = 1 - ((len(candidate.agents) - min_length) / (max_length - min_length))
            else:
                length_score = 1.0
            
            # Score total pondéré
            total_score = (
                weights["expected_value"] * value_score +
                weights["compatibility_score"] * compat_score +
                weights["health_score"] * health_score +
                weights["inverse_length"] * length_score
            )
            
            if total_score > best_score:
                best_score = total_score
                best_candidate = candidate
        
        return best_candidate
    
    async def _validate_chain(self, candidate: ChainCandidate) -> None:
        """Valide une chaîne d'agents."""
        # Vérifier que tous les agents existent
        for agent_id in candidate.agents:
            agent = self.registry.get_agent(agent_id)
            if not agent:
                raise NoChainAvailableError(f"Agent not found: {agent_id}")
            
            # Vérifier la santé
            if agent.health == AgentHealth.UNHEALTHY:
                raise NoChainAvailableError(f"Agent unhealthy: {agent_id}")
    
    async def _check_agent_health(self) -> None:
        """Vérifie la santé des agents."""
        if not self.health_checker:
            return
        
        for agent_id in list(self.registry._agents.keys()):
            try:
                health = await self.health_checker.check_health(agent_id)
                self.registry.update_agent_health(agent_id, health)
            except Exception as e:
                self.logger.warning(f"Health check failed for {agent_id}: {e}")
    
    def clear_cache(self) -> None:
        """Vide le cache des chaînes."""
        self._chain_cache.clear()
        self.logger.info("Chain cache cleared")
    
    def get_selector_stats(self) -> Dict[str, Any]:
        """Retourne des statistiques sur le sélecteur."""
        return {
            "cache_size": len(self._chain_cache),
            "max_chain_length": self.max_chain_length,
            "min_compatibility_score": self.min_compatibility_score,
            "registry_stats": self.registry.get_registry_stats()
        }


# Factory et fonctions utilitaires
def create_chain_selector(
    registry: Optional[AgentRegistry] = None,
    health_checker = None,
    max_chain_length: int = 5
) -> ChainSelector:
    """Factory pour créer un ChainSelector."""
    if registry is None:
        registry = AgentRegistry()
    
    return ChainSelector(
        registry=registry,
        health_checker=health_checker,
        max_chain_length=max_chain_length
    )


# Singleton pour utilisation facile
_chain_selector_instance = None

def get_chain_selector() -> ChainSelector:
    """Obtient l'instance singleton du ChainSelector."""
    global _chain_selector_instance
    if _chain_selector_instance is None:
        _chain_selector_instance = create_chain_selector()
    return _chain_selector_instance


# Exemple de health checker simple
class SimpleHealthChecker:
    """Vérificateur de santé simple pour les tests."""
    
    async def check_health(self, agent_id: str) -> AgentHealth:
        """Vérifie la santé d'un agent."""
        # Simulation: 90% healthy, 5% degraded, 5% unhealthy
        import random
        rand = random.random()
        if rand < 0.9:
            return AgentHealth.HEALTHY
        elif rand < 0.95:
            return AgentHealth.DEGRADED
        else:
            return AgentHealth.UNHEALTHY


# Tests unitaires intégrés
if __name__ == "__main__":
    import asyncio
    
    async def test_chain_selector():
        """Test basique du ChainSelector."""
        # Créer un registre avec des agents de test
        registry = AgentRegistry()
        
        # Ajouter des agents
        agents = [
            AgentMetadata(
                agent_id="cost_analyzer",
                name="Cost Analyzer",
                description="Analyse les coûts AWS",
                version="1.0.0",
                capabilities=["COST_OPTIMIZATION"],
                input_contracts=[{"format": "text", "language": "fr"}],
                output_contracts=[{"format": "json", "schema": "cost_report"}],
                cost_per_execution=10.0,
                estimated_success_rate=0.9,
                avg_execution_time_seconds=30.0,
                dependencies=[],
                compatibility_constraints={"environment": "production"},
                health=AgentHealth.HEALTHY
            ),
            AgentMetadata(
                agent_id="cost_recommender",
                name="Cost Recommender",
                description="Recommande des optimisations de coût",
                version="1.0.0",
                capabilities=["COST_OPTIMIZATION"],
                input_contracts=[{"format": "json", "schema": "cost_report"}],
                output_contracts=[{"format": "json", "schema": "recommendations"}],
                cost_per_execution=15.0,
                estimated_success_rate=0.85,
                avg_execution_time_seconds=45.0,
                dependencies=[],
                compatibility_constraints={"environment": "production"},
                health=AgentHealth.HEALTHY
            ),
            AgentMetadata(
                agent_id="cost_executor",
                name="Cost Executor",
                description="Exécute les optimisations de coût",
                version="1.0.0",
                capabilities=["COST_OPTIMIZATION"],
                input_contracts=[{"format": "json", "schema": "recommendations"}],
                output_contracts=[{"format": "json", "schema": "execution_report"}],
                cost_per_execution=25.0,
                estimated_success_rate=0.8,
                avg_execution_time_seconds=60.0,
                dependencies=[],
                compatibility_constraints={"environment": "production"},
                health=AgentHealth.HEALTHY
            )
        ]
        
        for agent in agents:
            registry.register_agent(agent)
        
        # Ajouter des arêtes de compatibilité
        registry.add_edge(Edge(
            source_agent="cost_analyzer",
            target_agent="cost_recommender",
            compatibility_score=0.9
        ))
        registry.add_edge(Edge(
            source_agent="cost_recommender",
            target_agent="cost_executor",
            compatibility_score=0.85
        ))
        
        # Créer le sélecteur
        selector = ChainSelector(registry)
        
        # Créer un intent et un contexte de test
        from ..types import Intent, DecisionContext
        
        test_intent = Intent(
            type="COST_OPTIMIZATION",
            priority=8,
            success_metrics={"cost_reduction": ">20%"}
        )
        
        test_context = DecisionContext(
            client_preferences={
                "automation_preference": "high",
                "budget_constraints": {"monthly_limit": 10000}
            },
            business_value={"expected_roi": 5.0},
            system_state={},
            compliance_constraints={"environment": "production"},
            decision_history=[],
            environment={"environment": "production"},
            intent=test_intent.dict(),
            metadata={}
        )
        
        # Tester la sélection
        try:
            chain = await selector.select(test_intent, test_context)
            print(f"✓ Chaîne sélectionnée: {chain.agents}")
            print(f"  Valeur attendue: {chain.expected_value}")
            print(f"  Métadonnées: {chain.metadata}")
            
            # Vérifier que la chaîne est valide
            assert len(chain.agents) > 0
            assert chain.expected_value > 0
            
            print("✓ Tous les tests passent")
            
        except NoChainAvailableError as e:
            print(f"✗ Aucune chaîne disponible: {e}")
        
        # Tester avec un intent inconnu
        unknown_intent = Intent(
            type="UNKNOWN_INTENT",
            priority=5,
            success_metrics={}
        )
        
        try:
            chain = await selector.select(unknown_intent, test_context)
            print("✗ ERROR: Should have raised NoChainAvailableError")
        except NoChainAvailableError:
            print("✓ Correctly raised NoChainAvailableError for unknown intent")
        
        print("\n✓ Tests complétés avec succès")
    
    asyncio.run(test_chain_selector())