"""
DSL Validator

Système de validation complet pour le Domain-Specific Language (DSL) MicroAgents.
Validation syntaxique, sémantique, de sécurité, de performance et de conformité.
"""

import re
import ast
import json
import logging
from typing import Dict, List, Optional, Set, Any, Tuple, Union
from enum import Enum
from dataclasses import dataclass, field
from collections import defaultdict, deque
import networkx as nx
from pydantic import BaseModel, Field, validator, ValidationError
import jsonschema
from jsonschema import validate as jsonschema_validate
import yaml

from src.dsl.language.ast import ASTNode, NodeType
from src.dsl.parser.parser import DSLParser
from src.core.compliance.compliance_checker import ComplianceChecker
from src.core.security.compliance_checker import SecurityComplianceChecker
from src.core.business_value.calculator import BusinessValueCalculator
from src.utils.security.utils import validate_security_clearance
from src.utils.validation.validators import validate_resource_limits

logger = logging.getLogger(__name__)


class ValidationLevel(Enum):
    """Niveaux de validation"""
    SYNTAX = "syntax"
    SEMANTIC = "semantic"
    TYPE = "type"
    SECURITY = "security"
    PERFORMANCE = "performance"
    COMPLIANCE = "compliance"
    BEST_PRACTICE = "best_practice"


class ValidationSeverity(Enum):
    """Sévérité des erreurs de validation"""
    ERROR = "error"      # Bloquant
    WARNING = "warning"  # Avertissement
    INFO = "info"        # Information


class ValidationRuleCategory(Enum):
    """Catégories de règles de validation"""
    SYNTAX = "syntax"
    SEMANTIC = "semantic"
    SECURITY = "security"
    PERFORMANCE = "performance"
    COMPLIANCE = "compliance"
    NAMING = "naming"
    RESOURCE = "resource"
    COST = "cost"
    BUSINESS = "business"


@dataclass
class ValidationError:
    """Erreur de validation"""
    rule_id: str
    category: ValidationRuleCategory
    severity: ValidationSeverity
    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    node_type: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    suggestion: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            'rule_id': self.rule_id,
            'category': self.category.value,
            'severity': self.severity.value,
            'message': self.message,
            'line': self.line,
            'column': self.column,
            'node_type': self.node_type,
            'context': self.context,
            'suggestion': self.suggestion
        }


@dataclass
class ValidationResult:
    """Résultat de validation"""
    is_valid: bool
    errors: List[ValidationError]
    warnings: List[ValidationError]
    infos: List[ValidationError]
    validation_time_ms: float
    validated_elements: Set[str] = field(default_factory=set)
    
    def add_error(self, error: ValidationError) -> None:
        """Ajoute une erreur"""
        self.errors.append(error)
        self.is_valid = False
    
    def add_warning(self, warning: ValidationError) -> None:
        """Ajoute un avertissement"""
        self.warnings.append(warning)
    
    def add_info(self, info: ValidationError) -> None:
        """Ajoute une information"""
        self.infos.append(info)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            'is_valid': self.is_valid,
            'error_count': len(self.errors),
            'warning_count': len(self.warnings),
            'info_count': len(self.infos),
            'validation_time_ms': self.validation_time_ms,
            'errors': [e.to_dict() for e in self.errors],
            'warnings': [w.to_dict() for w in self.warnings],
            'infos': [i.to_dict() for i in self.infos],
            'validated_elements': list(self.validated_elements)
        }


class SyntaxValidator:
    """Validateur syntaxique"""
    
    def __init__(self):
        """Initialise le validateur syntaxique"""
        self.parser = DSLParser()
        
        # Expressions régulières pour les conventions de nommage
        self.naming_patterns = {
            'agent_name': re.compile(r'^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*)*$'),
            'variable_name': re.compile(r'^[a-z][a-zA-Z0-9_]*$'),
            'constant_name': re.compile(r'^[A-Z][A-Z0-9_]*$'),
            'function_name': re.compile(r'^[a-z][a-zA-Z0-9_]*$'),
            'class_name': re.compile(r'^[A-Z][a-zA-Z0-9]*$'),
            'file_name': re.compile(r'^[a-z0-9_]+\.(dsl|agent)$'),
            'resource_name': re.compile(r'^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*$')
        }
        
        # Schémas JSON pour validation structurelle
        self.schemas = self._load_schemas()
        
        logger.info("SyntaxValidator initialisé")
    
    def _load_schemas(self) -> Dict[str, Any]:
        """Charge les schémas de validation"""
        return {
            'agent_definition': {
                'type': 'object',
                'required': ['name', 'version', 'description', 'inputs', 'outputs'],
                'properties': {
                    'name': {'type': 'string', 'pattern': self.naming_patterns['agent_name'].pattern},
                    'version': {'type': 'string', 'pattern': r'^\d+\.\d+\.\d+$'},
                    'description': {'type': 'string', 'minLength': 10},
                    'inputs': {
                        'type': 'object',
                        'minProperties': 1,
                        'additionalProperties': {
                            'type': 'object',
                            'required': ['type'],
                            'properties': {
                                'type': {'type': 'string', 'enum': ['string', 'number', 'boolean', 'object', 'array']},
                                'required': {'type': 'boolean'},
                                'default': {},
                                'description': {'type': 'string'}
                            }
                        }
                    },
                    'outputs': {
                        'type': 'object',
                        'minProperties': 1,
                        'additionalProperties': {
                            'type': 'object',
                            'required': ['type'],
                            'properties': {
                                'type': {'type': 'string', 'enum': ['string', 'number', 'boolean', 'object', 'array']},
                                'description': {'type': 'string'}
                            }
                        }
                    }
                }
            },
            'workflow_definition': {
                'type': 'object',
                'required': ['name', 'steps'],
                'properties': {
                    'name': {'type': 'string'},
                    'steps': {
                        'type': 'array',
                        'minItems': 1,
                        'items': {
                            'type': 'object',
                            'required': ['name', 'agent'],
                            'properties': {
                                'name': {'type': 'string'},
                                'agent': {'type': 'string'},
                                'inputs': {'type': 'object'},
                                'depends_on': {'type': 'array', 'items': {'type': 'string'}}
                            }
                        }
                    }
                }
            }
        }
    
    def validate_syntax(self, dsl_code: str) -> List[ValidationError]:
        """
        Valide la syntaxe du code DSL.
        
        Args:
            dsl_code: Code DSL à valider
            
        Returns:
            Erreurs de syntaxe
        """
        errors = []
        
        try:
            # Parsing du code DSL
            ast_tree = self.parser.parse(dsl_code)
            
            # Validation de base de l'AST
            if not ast_tree:
                errors.append(ValidationError(
                    rule_id="SYNTAX_001",
                    category=ValidationRuleCategory.SYNTAX,
                    severity=ValidationSeverity.ERROR,
                    message="Failed to parse DSL code - invalid syntax",
                    line=1,
                    column=1
                ))
                return errors
            
            # Validation structurelle de l'AST
            ast_errors = self._validate_ast_structure(ast_tree)
            errors.extend(ast_errors)
            
            # Validation des conventions de nommage
            naming_errors = self._validate_naming_conventions(ast_tree)
            errors.extend(naming_errors)
            
            # Validation des caractères interdits
            char_errors = self._validate_forbidden_characters(dsl_code)
            errors.extend(char_errors)
            
        except Exception as e:
            errors.append(ValidationError(
                rule_id="SYNTAX_000",
                category=ValidationRuleCategory.SYNTAX,
                severity=ValidationSeverity.ERROR,
                message=f"Syntax validation failed: {str(e)}",
                line=1,
                column=1
            ))
        
        return errors
    
    def _validate_ast_structure(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la structure de l'AST"""
        errors = []
        
        # Récursivité dans l'AST
        stack = [ast_node]
        visited = set()
        
        while stack:
            node = stack.pop()
            node_id = id(node)
            
            if node_id in visited:
                errors.append(ValidationError(
                    rule_id="SYNTAX_002",
                    category=ValidationRuleCategory.SYNTAX,
                    severity=ValidationSeverity.ERROR,
                    message="Circular reference detected in AST",
                    node_type=node.node_type.value
                ))
                continue
            
            visited.add(node_id)
            
            # Validation des propriétés requises selon le type de nœud
            if node.node_type == NodeType.AGENT_DEFINITION:
                if not hasattr(node, 'name') or not node.name:
                    errors.append(ValidationError(
                        rule_id="SYNTAX_003",
                        category=ValidationRuleCategory.SYNTAX,
                        severity=ValidationSeverity.ERROR,
                        message="Agent definition missing required 'name' field",
                        node_type=node.node_type.value
                    ))
            
            elif node.node_type == NodeType.INPUT_DEFINITION:
                if not hasattr(node, 'type') or not node.type:
                    errors.append(ValidationError(
                        rule_id="SYNTAX_004",
                        category=ValidationRuleCategory.SYNTAX,
                        severity=ValidationSeverity.ERROR,
                        message="Input definition missing required 'type' field",
                        node_type=node.node_type.value
                    ))
            
            elif node.node_type == NodeType.OUTPUT_DEFINITION:
                if not hasattr(node, 'type') or not node.type:
                    errors.append(ValidationError(
                        rule_id="SYNTAX_005",
                        category=ValidationRuleCategory.SYNTAX,
                        severity=ValidationSeverity.ERROR,
                        message="Output definition missing required 'type' field",
                        node_type=node.node_type.value
                    ))
            
            # Ajout des enfants à la pile
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors
    
    def _validate_naming_conventions(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les conventions de nommage"""
        errors = []
        stack = [ast_node]
        
        while stack:
            node = stack.pop()
            
            # Validation du nommage selon le type de nœud
            if hasattr(node, 'name') and node.name:
                name = node.name
                
                if node.node_type == NodeType.AGENT_DEFINITION:
                    if not self.naming_patterns['agent_name'].match(name):
                        errors.append(ValidationError(
                            rule_id="NAMING_001",
                            category=ValidationRuleCategory.NAMING,
                            severity=ValidationSeverity.ERROR,
                            message=f"Agent name '{name}' does not follow naming convention. Use: lowercase with optional namespace (e.g., 'monitoring:cost_analyzer')",
                            node_type=node.node_type.value,
                            context={'name': name}
                        ))
                
                elif node.node_type == NodeType.VARIABLE_DEFINITION:
                    if not self.naming_patterns['variable_name'].match(name):
                        errors.append(ValidationError(
                            rule_id="NAMING_002",
                            category=ValidationRuleCategory.NAMING,
                            severity=ValidationSeverity.WARNING,
                            message=f"Variable name '{name}' should be lowercase with underscores",
                            node_type=node.node_type.value,
                            context={'name': name},
                            suggestion=re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())
                        ))
                
                elif node.node_type == NodeType.CONSTANT_DEFINITION:
                    if not self.naming_patterns['constant_name'].match(name):
                        errors.append(ValidationError(
                            rule_id="NAMING_003",
                            category=ValidationRuleCategory.NAMING,
                            severity=ValidationSeverity.WARNING,
                            message=f"Constant name '{name}' should be UPPERCASE with underscores",
                            node_type=node.node_type.value,
                            context={'name': name},
                            suggestion=name.upper().replace(' ', '_')
                        ))
            
            # Validation des références
            if hasattr(node, 'ref_name') and node.ref_name:
                ref_name = node.ref_name
                
                if node.node_type == NodeType.AGENT_REFERENCE:
                    if not self.naming_patterns['agent_name'].match(ref_name):
                        errors.append(ValidationError(
                            rule_id="NAMING_004",
                            category=ValidationRuleCategory.NAMING,
                            severity=ValidationSeverity.ERROR,
                            message=f"Agent reference '{ref_name}' does not follow naming convention",
                            node_type=node.node_type.value,
                            context={'ref_name': ref_name}
                        ))
            
            # Ajout des enfants à la pile
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors
    
    def _validate_forbidden_characters(self, dsl_code: str) -> List[ValidationError]:
        """Valide les caractères interdits"""
        errors = []
        
        # Caractères interdits dans le code DSL
        forbidden_chars = {
            '\t': 'TAB',
            '\r': 'CR',
            '\x00': 'NULL',
            '\x1b': 'ESC'
        }
        
        lines = dsl_code.split('\n')
        for i, line in enumerate(lines, 1):
            for char, description in forbidden_chars.items():
                if char in line:
                    column = line.index(char) + 1
                    errors.append(ValidationError(
                        rule_id="SYNTAX_006",
                        category=ValidationRuleCategory.SYNTAX,
                        severity=ValidationSeverity.ERROR,
                        message=f"Forbidden character detected: {description} (U+{ord(char):04X})",
                        line=i,
                        column=column,
                        context={'character': description}
                    ))
        
        # Validation des encodages
        try:
            dsl_code.encode('utf-8')
        except UnicodeEncodeError as e:
            errors.append(ValidationError(
                rule_id="SYNTAX_007",
                category=ValidationRuleCategory.SYNTAX,
                severity=ValidationSeverity.ERROR,
                message=f"Invalid encoding: {str(e)}",
                line=1,
                column=1
            ))
        
        return errors
    
    def validate_json_structure(self, json_data: Dict[str, Any], schema_type: str) -> List[ValidationError]:
        """
        Valide la structure JSON contre un schéma.
        
        Args:
            json_data: Données JSON à valider
            schema_type: Type de schéma à utiliser
            
        Returns:
            Erreurs de validation
        """
        errors = []
        
        if schema_type not in self.schemas:
            errors.append(ValidationError(
                rule_id="SYNTAX_008",
                category=ValidationRuleCategory.SYNTAX,
                severity=ValidationSeverity.ERROR,
                message=f"Unknown schema type: {schema_type}"
            ))
            return errors
        
        schema = self.schemas[schema_type]
        
        try:
            jsonschema_validate(json_data, schema)
        except jsonschema.ValidationError as e:
            # Extraction des informations d'erreur
            path = '.'.join([str(p) for p in e.path])
            message = e.message
            
            errors.append(ValidationError(
                rule_id="SYNTAX_009",
                category=ValidationRuleCategory.SYNTAX,
                severity=ValidationSeverity.ERROR,
                message=f"JSON validation failed at path '{path}': {message}",
                context={
                    'path': path,
                    'validator': e.validator,
                    'validator_value': e.validator_value
                }
            ))
        
        return errors


class SemanticValidator:
    """Validateur sémantique"""
    
    def __init__(self, registry_client: Optional[Any] = None):
        """
        Initialise le validateur sémantique.
        
        Args:
            registry_client: Client pour accéder au registre d'agents
        """
        self.registry_client = registry_client
        
        # Règles de validation sémantique
        self.rules = self._load_semantic_rules()
        
        # Cache pour les dépendances validées
        self.dependency_cache = {}
        
        logger.info("SemanticValidator initialisé")
    
    def _load_semantic_rules(self) -> Dict[str, Dict[str, Any]]:
        """Charge les règles de validation sémantique"""
        return {
            'agent_existence': {
                'description': 'All referenced agents must exist in the registry',
                'severity': ValidationSeverity.ERROR,
                'validator': self._validate_agent_existence
            },
            'input_output_compatibility': {
                'description': 'Input and output types must be compatible',
                'severity': ValidationSeverity.ERROR,
                'validator': self._validate_io_compatibility
            },
            'required_fields': {
                'description': 'All required fields must be provided',
                'severity': ValidationSeverity.ERROR,
                'validator': self._validate_required_fields
            },
            'circular_dependencies': {
                'description': 'No circular dependencies allowed',
                'severity': ValidationSeverity.ERROR,
                'validator': self._validate_circular_dependencies
            },
            'dependency_resolution': {
                'description': 'All dependencies must be resolvable',
                'severity': ValidationSeverity.ERROR,
                'validator': self._validate_dependency_resolution
            },
            'business_rule_compliance': {
                'description': 'Must comply with business rules',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_business_rules
            },
            'data_flow_consistency': {
                'description': 'Data flow must be consistent',
                'severity': ValidationSeverity.ERROR,
                'validator': self._validate_data_flow
            }
        }
    
    def validate_semantics(self, ast_node: ASTNode) -> List[ValidationError]:
        """
        Valide la sémantique de l'AST.
        
        Args:
            ast_node: Nœud AST racine
            
        Returns:
            Erreurs sémantiques
        """
        errors = []
        
        # Exécution de toutes les règles de validation
        for rule_id, rule_config in self.rules.items():
            try:
                rule_errors = rule_config['validator'](ast_node)
                for error in rule_errors:
                    error.rule_id = rule_id
                    error.category = ValidationRuleCategory.SEMANTIC
                    error.severity = rule_config['severity']
                errors.extend(rule_errors)
            except Exception as e:
                logger.error(f"Error executing semantic rule {rule_id}: {str(e)}")
                errors.append(ValidationError(
                    rule_id=rule_id,
                    category=ValidationRuleCategory.SEMANTIC,
                    severity=ValidationSeverity.ERROR,
                    message=f"Semantic validation rule failed: {str(e)}"
                ))
        
        return errors
    
    def _validate_agent_existence(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide l'existence des agents référencés"""
        errors = []
        
        if not self.registry_client:
            # Pas de client de registre, on ne peut pas valider
            return errors
        
        # Collecte de toutes les références d'agents
        agent_refs = self._collect_agent_references(ast_node)
        
        for ref_name, context in agent_refs:
            try:
                # Vérification dans le registre
                agent_info = self.registry_client.get_agent(ref_name)
                if not agent_info:
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SEMANTIC,
                        severity=ValidationSeverity.ERROR,
                        message=f"Referenced agent '{ref_name}' not found in registry",
                        node_type=context.get('node_type'),
                        line=context.get('line'),
                        column=context.get('column'),
                        context={'agent_name': ref_name}
                    ))
            except Exception as e:
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.SEMANTIC,
                    severity=ValidationSeverity.ERROR,
                    message=f"Failed to check agent '{ref_name}' existence: {str(e)}",
                    node_type=context.get('node_type'),
                    line=context.get('line'),
                    column=context.get('column'),
                    context={'agent_name': ref_name}
                ))
        
        return errors
    
    def _collect_agent_references(self, ast_node: ASTNode) -> List[Tuple[str, Dict[str, Any]]]:
        """Collecte toutes les références d'agents dans l'AST"""
        references = []
        stack = [ast_node]
        
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.AGENT_REFERENCE and hasattr(node, 'ref_name'):
                context = {
                    'node_type': node.node_type.value,
                    'line': getattr(node, 'line', None),
                    'column': getattr(node, 'column', None)
                }
                references.append((node.ref_name, context))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return references
    
    def _validate_io_compatibility(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la compatibilité des entrées/sorties"""
        errors = []
        
        # Analyse des flux de données
        data_flows = self._analyze_data_flows(ast_node)
        
        for flow in data_flows:
            source_type = flow.get('source_type')
            target_type = flow.get('target_type')
            source_name = flow.get('source_name')
            target_name = flow.get('target_name')
            
            if source_type and target_type:
                # Vérification de compatibilité des types
                if not self._are_types_compatible(source_type, target_type):
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SEMANTIC,
                        severity=ValidationSeverity.ERROR,
                        message=f"Type mismatch: {source_type} from '{source_name}' is not compatible with {target_type} expected by '{target_name}'",
                        line=flow.get('line'),
                        column=flow.get('column'),
                        context={
                            'source_type': source_type,
                            'target_type': target_type,
                            'source_name': source_name,
                            'target_name': target_name
                        }
                    ))
        
        return errors
    
    def _analyze_data_flows(self, ast_node: ASTNode) -> List[Dict[str, Any]]:
        """Analyse les flux de données dans l'AST"""
        flows = []
        
        # Cette implémentation analyse les connexions entre agents
        # Dans une vraie implémentation, on analyserait l'AST complet
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.AGENT_CONNECTION:
                # Analyse des connexions entre agents
                if hasattr(node, 'source') and hasattr(node, 'target'):
                    flow = {
                        'source_name': getattr(node.source, 'name', 'unknown'),
                        'target_name': getattr(node.target, 'name', 'unknown'),
                        'source_type': getattr(node, 'source_type', None),
                        'target_type': getattr(node, 'target_type', None),
                        'line': getattr(node, 'line', None),
                        'column': getattr(node, 'column', None)
                    }
                    flows.append(flow)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return flows
    
    def _are_types_compatible(self, source_type: str, target_type: str) -> bool:
        """Vérifie si deux types sont compatibles"""
        # Règles de compatibilité de type
        compatibility_matrix = {
            'string': {'string', 'any'},
            'number': {'number', 'integer', 'float', 'any'},
            'integer': {'integer', 'number', 'any'},
            'float': {'float', 'number', 'any'},
            'boolean': {'boolean', 'any'},
            'object': {'object', 'any'},
            'array': {'array', 'any'},
            'any': {'string', 'number', 'integer', 'float', 'boolean', 'object', 'array', 'any'}
        }
        
        source_type = source_type.lower()
        target_type = target_type.lower()
        
        if source_type not in compatibility_matrix:
            return False
        
        return target_type in compatibility_matrix[source_type]
    
    def _validate_required_fields(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les champs requis"""
        errors = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Validation selon le type de nœud
            if node.node_type == NodeType.AGENT_DEFINITION:
                required_fields = ['name', 'version', 'description']
                for field in required_fields:
                    if not hasattr(node, field) or not getattr(node, field):
                        errors.append(ValidationError(
                            rule_id="",
                            category=ValidationRuleCategory.SEMANTIC,
                            severity=ValidationSeverity.ERROR,
                            message=f"Agent definition missing required field: '{field}'",
                            node_type=node.node_type.value,
                            line=getattr(node, 'line', None),
                            column=getattr(node, 'column', None),
                            context={'field': field}
                        ))
            
            elif node.node_type == NodeType.INPUT_DEFINITION:
                if not hasattr(node, 'name') or not node.name:
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SEMANTIC,
                        severity=ValidationSeverity.ERROR,
                        message="Input definition missing required 'name' field",
                        node_type=node.node_type.value,
                        line=getattr(node, 'line', None),
                        column=getattr(node, 'column', None)
                    ))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors
    
    def _validate_circular_dependencies(self, ast_node: ASTNode) -> List[ValidationError]:
        """Détecte les dépendances circulaires"""
        errors = []
        
        # Construction du graphe de dépendances
        dependency_graph = self._build_dependency_graph(ast_node)
        
        # Détection des cycles
        try:
            cycles = list(nx.simple_cycles(dependency_graph))
            for cycle in cycles:
                if len(cycle) > 1:  # Ignorer les auto-dépendances
                    cycle_str = ' -> '.join(cycle)
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SEMANTIC,
                        severity=ValidationSeverity.ERROR,
                        message=f"Circular dependency detected: {cycle_str}",
                        context={'cycle': cycle}
                    ))
        except Exception as e:
            logger.error(f"Error detecting cycles: {str(e)}")
        
        return errors
    
    def _build_dependency_graph(self, ast_node: ASTNode) -> nx.DiGraph:
        """Construit un graphe de dépendances"""
        graph = nx.DiGraph()
        
        # Collecte des dépendances
        dependencies = self._collect_dependencies(ast_node)
        
        for source, target in dependencies:
            graph.add_edge(source, target)
        
        return graph
    
    def _collect_dependencies(self, ast_node: ASTNode) -> List[Tuple[str, str]]:
        """Collecte toutes les dépendances dans l'AST"""
        dependencies = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.AGENT_DEPENDENCY:
                if hasattr(node, 'source') and hasattr(node, 'target'):
                    source_name = getattr(node.source, 'name', 'unknown')
                    target_name = getattr(node.target, 'name', 'unknown')
                    dependencies.append((source_name, target_name))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return dependencies
    
    def _validate_dependency_resolution(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la résolution des dépendances"""
        errors = []
        
        # Cette validation nécessite un contexte d'exécution
        # Pour l'instant, on valide juste que les dépendances sont définies
        
        return errors
    
    def _validate_business_rules(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la conformité aux règles métier"""
        errors = []
        
        # Règles métier spécifiques
        business_rules = [
            self._validate_agent_specialization,
            self._validate_workflow_complexity,
            self._validate_data_retention,
            self._validate_business_constraints
        ]
        
        for rule_func in business_rules:
            try:
                rule_errors = rule_func(ast_node)
                errors.extend(rule_errors)
            except Exception as e:
                logger.error(f"Error executing business rule: {str(e)}")
        
        return errors
    
    def _validate_agent_specialization(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la spécialisation des agents"""
        errors = []
        
        # Règle: un agent ne doit pas avoir trop de responsabilités
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.AGENT_DEFINITION:
                # Compte des capacités
                capabilities = self._count_agent_capabilities(node)
                if capabilities > 10:
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SEMANTIC,
                        severity=ValidationSeverity.WARNING,
                        message=f"Agent '{getattr(node, 'name', 'unknown')}' has {capabilities} capabilities - consider splitting into specialized agents",
                        node_type=node.node_type.value,
                        line=getattr(node, 'line', None),
                        column=getattr(node, 'column', None),
                        context={'capabilities': capabilities}
                    ))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors
    
    def _count_agent_capabilities(self, agent_node: ASTNode) -> int:
        """Compte les capacités d'un agent"""
        count = 0
        stack = [agent_node]
        
        while stack:
            node = stack.pop()
            
            if node.node_type in [NodeType.FUNCTION_DEFINITION, NodeType.TASK_DEFINITION]:
                count += 1
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return count
    
    def _validate_workflow_complexity(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la complexité des workflows"""
        errors = []
        
        # Recherche des définitions de workflow
        workflows = self._find_workflows(ast_node)
        
        for workflow in workflows:
            # Compte des étapes
            step_count = self._count_workflow_steps(workflow)
            if step_count > 20:
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.SEMANTIC,
                    severity=ValidationSeverity.WARNING,
                    message=f"Workflow has {step_count} steps - consider splitting into smaller workflows",
                    node_type=workflow.node_type.value,
                    line=getattr(workflow, 'line', None),
                    column=getattr(workflow, 'column', None),
                    context={'step_count': step_count}
                ))
            
            # Vérification de la profondeur
            depth = self._calculate_workflow_depth(workflow)
            if depth > 5:
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.SEMANTIC,
                    severity=ValidationSeverity.WARNING,
                    message=f"Workflow depth is {depth} levels - consider flattening the workflow",
                    node_type=workflow.node_type.value,
                    line=getattr(workflow, 'line', None),
                    column=getattr(workflow, 'column', None),
                    context={'depth': depth}
                ))
        
        return errors
    
    def _find_workflows(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve toutes les définitions de workflow"""
        workflows = []
        stack = [ast_node]
        
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.WORKFLOW_DEFINITION:
                workflows.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return workflows
    
    def _count_workflow_steps(self, workflow_node: ASTNode) -> int:
        """Compte les étapes d'un workflow"""
        count = 0
        stack = [workflow_node]
        
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.WORKFLOW_STEP:
                count += 1
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return count
    
    def _calculate_workflow_depth(self, workflow_node: ASTNode) -> int:
        """Calcule la profondeur d'un workflow"""
        max_depth = 0
        stack = [(workflow_node, 1)]
        
        while stack:
            node, depth = stack.pop()
            max_depth = max(max_depth, depth)
            
            if hasattr(node, 'children'):
                for child in node.children:
                    stack.append((child, depth + 1))
        
        return max_depth
    
    def _validate_data_retention(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les politiques de rétention de données"""
        errors = []
        
        # Cette validation nécessite des règles spécifiques à l'organisation
        
        return errors
    
    def _validate_business_constraints(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les contraintes métier"""
        errors = []
        
        # Exemple: validation des limites métier
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.AGENT_DEFINITION:
                # Vérification du coût estimé
                estimated_cost = getattr(node, 'estimated_cost', 0)
                if estimated_cost > 10000:  # Seuil arbitraire
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SEMANTIC,
                        severity=ValidationSeverity.WARNING,
                        message=f"Agent estimated cost (${estimated_cost}) exceeds business threshold",
                        node_type=node.node_type.value,
                        line=getattr(node, 'line', None),
                        column=getattr(node, 'column', None),
                        context={'estimated_cost': estimated_cost}
                    ))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors
    
    def _validate_data_flow(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la cohérence du flux de données"""
        errors = []
        
        # Analyse des sources et destinations de données
        data_sources = set()
        data_sinks = set()
        data_transforms = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.DATA_SOURCE:
                if hasattr(node, 'name'):
                    data_sources.add(node.name)
            
            elif node.node_type == NodeType.DATA_SINK:
                if hasattr(node, 'name'):
                    data_sinks.add(node.name)
            
            elif node.node_type == NodeType.DATA_TRANSFORM:
                data_transforms.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        # Vérification: toutes les données doivent avoir une source
        for sink in data_sinks:
            if sink not in data_sources and sink not in [t.name for t in data_transforms if hasattr(t, 'name')]:
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.SEMANTIC,
                    severity=ValidationSeverity.ERROR,
                    message=f"Data sink '{sink}' has no corresponding source or transformation",
                    context={'sink': sink}
                ))
        
        return errors


class TypeValidator:
    """Validateur de types"""
    
    def __init__(self):
        """Initialise le validateur de types"""
        self.type_system = self._initialize_type_system()
        self.type_inference = TypeInference()
        
        logger.info("TypeValidator initialisé")
    
    def _initialize_type_system(self) -> Dict[str, Any]:
        """Initialise le système de types"""
        return {
            'primitive_types': {
                'string': {'kind': 'primitive', 'size': 'variable'},
                'number': {'kind': 'primitive', 'subtypes': ['integer', 'float']},
                'integer': {'kind': 'primitive', 'size': 64},
                'float': {'kind': 'primitive', 'size': 64},
                'boolean': {'kind': 'primitive', 'size': 1},
                'any': {'kind': 'wildcard'}
            },
            'composite_types': {
                'array': {'kind': 'composite', 'of': 'any'},
                'object': {'kind': 'composite', 'fields': {}},
                'map': {'kind': 'composite', 'key': 'string', 'value': 'any'}
            },
            'agent_types': {
                'detector': {'inputs': {'data': 'any'}, 'outputs': {'result': 'object'}},
                'analyzer': {'inputs': {'data': 'object'}, 'outputs': {'analysis': 'object'}},
                'remediator': {'inputs': {'issue': 'object'}, 'outputs': {'action': 'object'}}
            }
        }
    
    def validate_types(self, ast_node: ASTNode) -> List[ValidationError]:
        """
        Valide les types dans l'AST.
        
        Args:
            ast_node: Nœud AST racine
            
        Returns:
            Erreurs de type
        """
        errors = []
        
        # Inférence de types
        type_context = self.type_inference.infer_types(ast_node)
        
        # Validation de cohérence des types
        consistency_errors = self._validate_type_consistency(ast_node, type_context)
        errors.extend(consistency_errors)
        
        # Validation des opérations de type
        operation_errors = self._validate_type_operations(ast_node, type_context)
        errors.extend(operation_errors)
        
        # Validation des conversions de type
        conversion_errors = self._validate_type_conversions(ast_node, type_context)
        errors.extend(conversion_errors)
        
        # Validation des limites de type
        limit_errors = self._validate_type_limits(ast_node, type_context)
        errors.extend(limit_errors)
        
        return errors
    
    def _validate_type_consistency(
        self,
        ast_node: ASTNode,
        type_context: Dict[str, str]
    ) -> List[ValidationError]:
        """Valide la cohérence des types"""
        errors = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Validation des déclarations de variables
            if node.node_type == NodeType.VARIABLE_DECLARATION:
                if hasattr(node, 'var_name') and hasattr(node, 'var_type'):
                    var_name = node.var_name
                    declared_type = node.var_type
                    
                    # Vérification que le type déclaré existe
                    if not self._type_exists(declared_type):
                        errors.append(ValidationError(
                            rule_id="TYPE_001",
                            category=ValidationRuleCategory.TYPE,
                            severity=ValidationSeverity.ERROR,
                            message=f"Unknown type '{declared_type}' for variable '{var_name}'",
                            node_type=node.node_type.value,
                            line=getattr(node, 'line', None),
                            column=getattr(node, 'column', None),
                            context={'variable': var_name, 'type': declared_type}
                        ))
                    
                    # Vérification de cohérence avec l'inférence
                    inferred_type = type_context.get(var_name)
                    if inferred_type and declared_type != inferred_type:
                        if not self._are_types_compatible(declared_type, inferred_type):
                            errors.append(ValidationError(
                                rule_id="TYPE_002",
                                category=ValidationRuleCategory.TYPE,
                                severity=ValidationSeverity.WARNING,
                                message=f"Type mismatch for variable '{var_name}': declared as '{declared_type}', inferred as '{inferred_type}'",
                                node_type=node.node_type.value,
                                line=getattr(node, 'line', None),
                                column=getattr(node, 'column', None),
                                context={
                                    'variable': var_name,
                                    'declared_type': declared_type,
                                    'inferred_type': inferred_type
                                }
                            ))
            
            # Validation des retours de fonction
            elif node.node_type == NodeType.FUNCTION_DEFINITION:
                if hasattr(node, 'return_type'):
                    return_type = node.return_type
                    
                    # Analyse des retours dans la fonction
                    return_statements = self._find_return_statements(node)
                    for stmt in return_statements:
                        if hasattr(stmt, 'value_type'):
                            value_type = stmt.value_type
                            if not self._are_types_compatible(value_type, return_type):
                                errors.append(ValidationError(
                                    rule_id="TYPE_003",
                                    category=ValidationRuleCategory.TYPE,
                                    severity=ValidationSeverity.ERROR,
                                    message=f"Return type mismatch: function returns '{value_type}' but declared as '{return_type}'",
                                    node_type=node.node_type.value,
                                    line=getattr(stmt, 'line', None),
                                    column=getattr(stmt, 'column', None),
                                    context={
                                        'function': getattr(node, 'name', 'unknown'),
                                        'return_type': return_type,
                                        'actual_type': value_type
                                    }
                                ))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors
    
    def _type_exists(self, type_name: str) -> bool:
        """Vérifie si un type existe dans le système"""
        # Recherche dans les types primitifs
        if type_name in self.type_system['primitive_types']:
            return True
        
        # Recherche dans les types composites
        if type_name in self.type_system['composite_types']:
            return True
        
        # Recherche dans les types d'agent
        if type_name in self.type_system['agent_types']:
            return True
        
        # Vérification des types génériques (Array<T>, Map<K,V>)
        if self._is_generic_type(type_name):
            return True
        
        return False
    
    def _is_generic_type(self, type_name: str) -> bool:
        """Vérifie si c'est un type générique"""
        generic_patterns = [
            r'^Array<.+>$',
            r'^Map<.+>$',
            r'^Optional<.+>$',
            r'^Result<.+>$'
        ]
        
        for pattern in generic_patterns:
            if re.match(pattern, type_name):
                return True
        
        return False
    
    def _are_types_compatible(self, source_type: str, target_type: str) -> bool:
        """Vérifie la compatibilité entre deux types"""
        # Mêmes types
        if source_type == target_type:
            return True
        
        # Type any accepte tout
        if target_type == 'any':
            return True
        
        # Conversions numériques
        numeric_types = ['integer', 'float', 'number']
        if source_type in numeric_types and target_type in numeric_types:
            return True
        
        # Types génériques
        if self._are_generic_types_compatible(source_type, target_type):
            return True
        
        return False
    
    def _are_generic_types_compatible(self, source_type: str, target_type: str) -> bool:
        """Vérifie la compatibilité des types génériques"""
        # Extraction des paramètres de type
        source_match = re.match(r'^(\w+)<(.+)>$', source_type)
        target_match = re.match(r'^(\w+)<(.+)>$', target_type)
        
        if not source_match or not target_match:
            return False
        
        source_base, source_param = source_match.groups()
        target_base, target_param = target_match.groups()
        
        # Même type de base
        if source_base != target_base:
            return False
        
        # Compatibilité des paramètres
        return self._are_types_compatible(source_param, target_param)
    
    def _find_return_statements(self, function_node: ASTNode) -> List[ASTNode]:
        """Trouve toutes les instructions return dans une fonction"""
        returns = []
        stack = [function_node]
        
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.RETURN_STATEMENT:
                returns.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return returns
    
    def _validate_type_operations(
        self,
        ast_node: ASTNode,
        type_context: Dict[str, str]
    ) -> List[ValidationError]:
        """Valide les opérations sur les types"""
        errors = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Validation des opérations binaires
            if node.node_type == NodeType.BINARY_OPERATION:
                if hasattr(node, 'left_type') and hasattr(node, 'right_type'):
                    left_type = node.left_type
                    right_type = node.right_type
                    operator = getattr(node, 'operator', 'unknown')
                    
                    # Vérification de la compatibilité des opérandes
                    if not self._are_operands_compatible(left_type, right_type, operator):
                        errors.append(ValidationError(
                            rule_id="TYPE_004",
                            category=ValidationRuleCategory.TYPE,
                            severity=ValidationSeverity.ERROR,
                            message=f"Incompatible types for operator '{operator}': {left_type} and {right_type}",
                            node_type=node.node_type.value,
                            line=getattr(node, 'line', None),
                            column=getattr(node, 'column', None),
                            context={
                                'operator': operator,
                                'left_type': left_type,
                                'right_type': right_type
                            }
                        ))
            
            # Validation des appels de fonction
            elif node.node_type == NodeType.FUNCTION_CALL:
                if hasattr(node, 'function_name') and hasattr(node, 'arguments'):
                    func_name = node.function_name
                    args = node.arguments
                    
                    # Récupération de la signature de la fonction
                    func_signature = self._get_function_signature(func_name)
                    if func_signature:
                        # Validation des arguments
                        param_types = func_signature.get('parameters', [])
                        for i, (arg, param_type) in enumerate(zip(args, param_types)):
                            arg_type = getattr(arg, 'type', 'unknown')
                            if not self._are_types_compatible(arg_type, param_type):
                                errors.append(ValidationError(
                                    rule_id="TYPE_005",
                                    category=ValidationRuleCategory.TYPE,
                                    severity=ValidationSeverity.ERROR,
                                    message=f"Argument {i+1} of function '{func_name}' has type '{arg_type}' but expected '{param_type}'",
                                    node_type=node.node_type.value,
                                    line=getattr(arg, 'line', None),
                                    column=getattr(arg, 'column', None),
                                    context={
                                        'function': func_name,
                                        'argument_index': i,
                                        'actual_type': arg_type,
                                        'expected_type': param_type
                                    }
                                ))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors
    
    def _are_operands_compatible(
        self,
        left_type: str,
        right_type: str,
        operator: str
    ) -> bool:
        """Vérifie la compatibilité des opérandes pour un opérateur"""
        # Opérateurs arithmétiques
        arithmetic_ops = ['+', '-', '*', '/', '%']
        if operator in arithmetic_ops:
            numeric_types = ['integer', 'float', 'number']
            return left_type in numeric_types and right_type in numeric_types
        
        # Opérateurs de comparaison
        comparison_ops = ['==', '!=', '<', '>', '<=', '>=']
        if operator in comparison_ops:
            # Les comparaisons nécessitent des types comparables
            return self._are_types_comparable(left_type, right_type)
        
        # Opérateurs logiques
        logical_ops = ['&&', '||']
        if operator in logical_ops:
            return left_type == 'boolean' and right_type == 'boolean'
        
        # Concaténation de chaînes
        if operator == '+':
            return left_type == 'string' and right_type == 'string'
        
        return True  # Par défaut, on accepte
    
    def _are_types_comparable(self, type1: str, type2: str) -> bool:
        """Vérifie si deux types sont comparables"""
        # Mêmes types
        if type1 == type2:
            return True
        
        # Types numériques comparables entre eux
        numeric_types = ['integer', 'float', 'number']
        if type1 in numeric_types and type2 in numeric_types:
            return True
        
        # Chaînes comparables entre elles
        if type1 == 'string' and type2 == 'string':
            return True
        
        return False
    
    def _get_function_signature(self, function_name: str) -> Optional[Dict[str, Any]]:
        """Récupère la signature d'une fonction"""
        # Dans une vraie implémentation, on irait chercher dans le registre
        # Pour l'instant, on retourne des signatures prédéfinies
        
        predefined_signatures = {
            'parse_int': {
                'parameters': ['string'],
                'return_type': 'integer'
            },
            'parse_float': {
                'parameters': ['string'],
                'return_type': 'float'
            },
            'to_string': {
                'parameters': ['any'],
                'return_type': 'string'
            },
            'length': {
                'parameters': ['array'],
                'return_type': 'integer'
            }
        }
        
        return predefined_signatures.get(function_name)
    
    def _validate_type_conversions(
        self,
        ast_node: ASTNode,
        type_context: Dict[str, str]
    ) -> List[ValidationError]:
        """Valide les conversions de type"""
        errors = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.TYPE_CAST:
                if hasattr(node, 'source_type') and hasattr(node, 'target_type'):
                    source_type = node.source_type
                    target_type = node.target_type
                    
                    # Vérification de la validité de la conversion
                    if not self._is_conversion_valid(source_type, target_type):
                        errors.append(ValidationError(
                            rule_id="TYPE_006",
                            category=ValidationRuleCategory.TYPE,
                            severity=ValidationSeverity.ERROR,
                            message=f"Invalid type conversion from '{source_type}' to '{target_type}'",
                            node_type=node.node_type.value,
                            line=getattr(node, 'line', None),
                            column=getattr(node, 'column', None),
                            context={
                                'source_type': source_type,
                                'target_type': target_type
                            }
                        ))
                    
                    # Vérification de la perte de précision
                    if self._is_lossy_conversion(source_type, target_type):
                        errors.append(ValidationError(
                            rule_id="TYPE_007",
                            category=ValidationRuleCategory.TYPE,
                            severity=ValidationSeverity.WARNING,
                            message=f"Lossy conversion from '{source_type}' to '{target_type}'",
                            node_type=node.node_type.value,
                            line=getattr(node, 'line', None),
                            column=getattr(node, 'column', None),
                            context={
                                'source_type': source_type,
                                'target_type': target_type
                            },
                            suggestion=f"Consider using safe conversion or validation"
                        ))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors
    
    def _is_conversion_valid(self, source_type: str, target_type: str) -> bool:
        """Vérifie si une conversion de type est valide"""
        # Conversions autorisées
        valid_conversions = [
            ('integer', 'float'),
            ('float', 'integer'),
            ('string', 'integer'),
            ('string', 'float'),
            ('integer', 'string'),
            ('float', 'string'),
            ('boolean', 'string'),
            ('string', 'boolean')
        ]
        
        # Conversion vers le même type
        if source_type == target_type:
            return True
        
        # Conversion vers 'any'
        if target_type == 'any':
            return True
        
        # Vérification dans la liste des conversions valides
        return (source_type, target_type) in valid_conversions
    
    def _is_lossy_conversion(self, source_type: str, target_type: str) -> bool:
        """Vérifie si une conversion entraîne une perte de précision"""
        lossy_conversions = [
            ('float', 'integer'),  # Perte des décimales
            ('string', 'integer'),  # Échec possible
            ('string', 'float'),   # Échec possible
            ('string', 'boolean')  # Interprétation ambiguë
        ]
        
        return (source_type, target_type) in lossy_conversions
    
    def _validate_type_limits(
        self,
        ast_node: ASTNode,
        type_context: Dict[str, str]
    ) -> List[ValidationError]:
        """Valide les limites de type"""
        errors = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Validation des limites numériques
            if node.node_type == NodeType.NUMBER_LITERAL:
                if hasattr(node, 'value') and hasattr(node, 'type'):
                    value = node.value
                    num_type = node.type
                    
                    # Vérification des limites selon le type
                    if num_type == 'integer':
                        try:
                            int_value = int(value)
                            if not (-2**63 <= int_value <= 2**63 - 1):
                                errors.append(ValidationError(
                                    rule_id="TYPE_008",
                                    category=ValidationRuleCategory.TYPE,
                                    severity=ValidationSeverity.ERROR,
                                    message=f"Integer value {value} out of 64-bit range",
                                    node_type=node.node_type.value,
                                    line=getattr(node, 'line', None),
                                    column=getattr(node, 'column', None),
                                    context={'value': value, 'type': num_type}
                                ))
                        except ValueError:
                            pass
            
            # Validation de la longueur des chaînes
            elif node.node_type == NodeType.STRING_LITERAL:
                if hasattr(node, 'value'):
                    string_value = node.value
                    if len(string_value) > 10000:  # Limite arbitraire
                        errors.append(ValidationError(
                            rule_id="TYPE_009",
                            category=ValidationRuleCategory.TYPE,
                            severity=ValidationSeverity.WARNING,
                            message=f"String length ({len(string_value)}) exceeds recommended limit",
                            node_type=node.node_type.value,
                            line=getattr(node, 'line', None),
                            column=getattr(node, 'column', None),
                            context={'length': len(string_value)}
                        ))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors


class TypeInference:
    """Système d'inférence de types"""
    
    def infer_types(self, ast_node: ASTNode) -> Dict[str, str]:
        """
        Infère les types dans l'AST.
        
        Args:
            ast_node: Nœud AST racine
            
        Returns:
            Contexte de types (nom -> type)
        """
        type_context = {}
        
        # Premier passage: collecte des déclarations
        declarations = self._collect_declarations(ast_node)
        type_context.update(declarations)
        
        # Deuxième passage: inférence à partir des affectations
        assignments = self._infer_from_assignments(ast_node, type_context)
        type_context.update(assignments)
        
        # Troisième passage: propagation des types
        self._propagate_types(ast_node, type_context)
        
        return type_context
    
    def _collect_declarations(self, ast_node: ASTNode) -> Dict[str, str]:
        """Collecte les déclarations de types explicites"""
        declarations = {}
        stack = [ast_node]
        
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.VARIABLE_DECLARATION:
                if hasattr(node, 'var_name') and hasattr(node, 'var_type'):
                    declarations[node.var_name] = node.var_type
            
            elif node.node_type == NodeType.FUNCTION_DEFINITION:
                if hasattr(node, 'name') and hasattr(node, 'return_type'):
                    declarations[node.name] = f"function -> {node.return_type}"
                    
                    # Paramètres de fonction
                    if hasattr(node, 'parameters'):
                        for param in node.parameters:
                            if hasattr(param, 'name') and hasattr(param, 'type'):
                                declarations[param.name] = param.type
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return declarations
    
    def _infer_from_assignments(
        self,
        ast_node: ASTNode,
        type_context: Dict[str, str]
    ) -> Dict[str, str]:
        """Infère les types à partir des affectations"""
        inferred = {}
        stack = [ast_node]
        
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.ASSIGNMENT:
                if hasattr(node, 'target') and hasattr(node, 'value'):
                    target_name = getattr(node.target, 'name', None)
                    value_type = self._infer_expression_type(node.value, type_context)
                    
                    if target_name and value_type:
                        inferred[target_name] = value_type
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return inferred
    
    def _infer_expression_type(
        self,
        expr_node: ASTNode,
        type_context: Dict[str, str]
    ) -> Optional[str]:
        """Infère le type d'une expression"""
        if not expr_node:
            return None
        
        # Littéraux
        if expr_node.node_type == NodeType.NUMBER_LITERAL:
            return 'number'
        
        if expr_node.node_type == NodeType.STRING_LITERAL:
            return 'string'
        
        if expr_node.node_type == NodeType.BOOLEAN_LITERAL:
            return 'boolean'
        
        # Variables
        if expr_node.node_type == NodeType.VARIABLE_REFERENCE:
            if hasattr(expr_node, 'name'):
                return type_context.get(expr_node.name)
        
        # Appels de fonction
        if expr_node.node_type == NodeType.FUNCTION_CALL:
            if hasattr(expr_node, 'function_name'):
                func_name = expr_node.function_name
                # Dans une vraie implémentation, on chercherait la signature
                return 'any'
        
        # Opérations binaires
        if expr_node.node_type == NodeType.BINARY_OPERATION:
            if hasattr(expr_node, 'operator'):
                operator = expr_node.operator
                left_type = self._infer_expression_type(
                    getattr(expr_node, 'left', None), type_context
                )
                right_type = self._infer_expression_type(
                    getattr(expr_node, 'right', None), type_context
                )
                
                # Inférence basée sur l'opérateur
                if operator in ['+', '-', '*', '/', '%']:
                    return 'number'
                elif operator in ['&&', '||']:
                    return 'boolean'
                elif operator in ['==', '!=', '<', '>', '<=', '>=']:
                    return 'boolean'
                elif operator == '+':  # Concaténation de chaînes
                    if left_type == 'string' and right_type == 'string':
                        return 'string'
        
        return 'any'
    
    def _propagate_types(
        self,
        ast_node: ASTNode,
        type_context: Dict[str, str]
    ) -> None:
        """Propage les types dans l'AST"""
        # Cette méthode mettrait à jour les nœuds AST avec les types inférés
        # Pour l'instant, c'est une ébauche
        pass


class SecurityValidator:
    """Validateur de sécurité"""
    
    def __init__(self, security_config: Optional[Dict[str, Any]] = None):
        """
        Initialise le validateur de sécurité.
        
        Args:
            security_config: Configuration de sécurité
        """
        self.security_config = security_config or self._default_security_config()
        self.compliance_checker = SecurityComplianceChecker()
        
        # Politiques de sécurité
        self.security_policies = self._load_security_policies()
        
        logger.info("SecurityValidator initialisé")
    
    def _default_security_config(self) -> Dict[str, Any]:
        """Configuration de sécurité par défaut"""
        return {
            'require_authentication': True,
            'require_authorization': True,
            'data_encryption_required': True,
            'audit_logging_required': True,
            'max_security_level': 5,
            'allowed_data_categories': ['public', 'internal', 'confidential'],
            'forbidden_operations': ['shell_exec', 'file_write', 'network_raw']
        }
    
    def _load_security_policies(self) -> Dict[str, Any]:
        """Charge les politiques de sécurité"""
        return {
            'authentication_required': {
                'description': 'All sensitive operations require authentication',
                'severity': ValidationSeverity.ERROR,
                'validator': self._validate_authentication
            },
            'authorization_level': {
                'description': 'Operations must have appropriate authorization level',
                'severity': ValidationSeverity.ERROR,
                'validator': self._validate_authorization
            },
            'data_privacy': {
                'description': 'Data handling must comply with privacy regulations',
                'severity': ValidationSeverity.ERROR,
                'validator': self._validate_data_privacy
            },
            'secure_communications': {
                'description': 'All communications must be secured',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_secure_comms
            },
            'input_validation': {
                'description': 'All inputs must be validated',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_input_validation
            },
            'forbidden_operations': {
                'description': 'Forbidden operations must not be used',
                'severity': ValidationSeverity.ERROR,
                'validator': self._validate_forbidden_ops
            },
            'security_headers': {
                'description': 'Required security headers must be present',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_security_headers
            }
        }
    
    def validate_security(self, ast_node: ASTNode) -> List[ValidationError]:
        """
        Valide les aspects de sécurité.
        
        Args:
            ast_node: Nœud AST racine
            
        Returns:
            Erreurs de sécurité
        """
        errors = []
        
        # Exécution de toutes les politiques de sécurité
        for policy_id, policy_config in self.security_policies.items():
            try:
                policy_errors = policy_config['validator'](ast_node)
                for error in policy_errors:
                    error.rule_id = policy_id
                    error.category = ValidationRuleCategory.SECURITY
                    error.severity = policy_config['severity']
                errors.extend(policy_errors)
            except Exception as e:
                logger.error(f"Error executing security policy {policy_id}: {str(e)}")
        
        return errors
    
    def _validate_authentication(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les exigences d'authentification"""
        errors = []
        
        if not self.security_config['require_authentication']:
            return errors
        
        # Recherche des opérations sensibles
        sensitive_ops = self._find_sensitive_operations(ast_node)
        
        for op in sensitive_ops:
            # Vérification de la présence d'authentification
            if not self._has_authentication(op):
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.SECURITY,
                    severity=ValidationSeverity.ERROR,
                    message=f"Sensitive operation '{getattr(op, 'name', 'unknown')}' requires authentication",
                    node_type=op.node_type.value,
                    line=getattr(op, 'line', None),
                    column=getattr(op, 'column', None),
                    context={'operation': getattr(op, 'name', 'unknown')}
                ))
        
        return errors
    
    def _find_sensitive_operations(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les opérations sensibles"""
        sensitive_ops = []
        
        sensitive_patterns = [
            NodeType.DATA_ACCESS,
            NodeType.FILE_OPERATION,
            NodeType.NETWORK_OPERATION,
            NodeType.SYSTEM_OPERATION
        ]
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type in sensitive_patterns:
                sensitive_ops.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return sensitive_ops
    
    def _has_authentication(self, operation_node: ASTNode) -> bool:
        """Vérifie si une opération a une authentification"""
        # Recherche des nœuds d'authentification dans le contexte
        stack = [operation_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.AUTHENTICATION:
                return True
            
            # Remonter dans les parents
            if hasattr(node, 'parent'):
                stack.append(node.parent)
        
        return False
    
    def _validate_authorization(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les niveaux d'autorisation"""
        errors = []
        
        if not self.security_config['require_authorization']:
            return errors
        
        # Analyse des niveaux de sécurité requis
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if hasattr(node, 'required_security_level'):
                required_level = node.required_security_level
                max_allowed = self.security_config['max_security_level']
                
                if required_level > max_allowed:
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SECURITY,
                        severity=ValidationSeverity.ERROR,
                        message=f"Operation requires security level {required_level} but maximum allowed is {max_allowed}",
                        node_type=node.node_type.value,
                        line=getattr(node, 'line', None),
                        column=getattr(node, 'column', None),
                        context={
                            'required_level': required_level,
                            'max_allowed': max_allowed
                        }
                    ))
            
            # Vérification des privilèges
            if node.node_type == NodeType.PRIVILEGED_OPERATION:
                if not self._has_sufficient_privileges(node):
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SECURITY,
                        severity=ValidationSeverity.ERROR,
                        message="Insufficient privileges for privileged operation",
                        node_type=node.node_type.value,
                        line=getattr(node, 'line', None),
                        column=getattr(node, 'column', None)
                    ))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors
    
    def _has_sufficient_privileges(self, operation_node: ASTNode) -> bool:
        """Vérifie les privilèges suffisants"""
        # Dans une vraie implémentation, on vérifierait les rôles et permissions
        return True  # Simplifié pour l'exemple
    
    def _validate_data_privacy(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la conformité à la confidentialité des données"""
        errors = []
        
        # Recherche des données sensibles
        sensitive_data = self._find_sensitive_data(ast_node)
        
        for data_node in sensitive_data:
            data_category = getattr(data_node, 'category', 'unknown')
            
            # Vérification de la catégorie autorisée
            if data_category not in self.security_config['allowed_data_categories']:
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.SECURITY,
                    severity=ValidationSeverity.ERROR,
                    message=f"Data category '{data_category}' is not allowed",
                    node_type=data_node.node_type.value,
                    line=getattr(data_node, 'line', None),
                    column=getattr(data_node, 'column', None),
                    context={'category': data_category}
                ))
            
            # Vérification du chiffrement pour les données confidentielles
            if data_category == 'confidential':
                if not self._is_data_encrypted(data_node):
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SECURITY,
                        severity=ValidationSeverity.ERROR,
                        message="Confidential data must be encrypted",
                        node_type=data_node.node_type.value,
                        line=getattr(data_node, 'line', None),
                        column=getattr(data_node, 'column', None)
                    ))
            
            # Vérification GDPR
            if hasattr(data_node, 'contains_pii') and data_node.contains_pii:
                gdp_errors = self.compliance_checker.validate_gdpr_compliance(data_node)
                for gdp_error in gdp_errors:
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SECURITY,
                        severity=ValidationSeverity.ERROR,
                        message=f"GDPR violation: {gdp_error}",
                        node_type=data_node.node_type.value,
                        line=getattr(data_node, 'line', None),
                        column=getattr(data_node, 'column', None)
                    ))
        
        return errors
    
    def _find_sensitive_data(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les données sensibles"""
        sensitive_data = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.DATA_DEFINITION:
                if hasattr(node, 'sensitive') and node.sensitive:
                    sensitive_data.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return sensitive_data
    
    def _is_data_encrypted(self, data_node: ASTNode) -> bool:
        """Vérifie si les données sont chiffrées"""
        # Recherche de l'opération de chiffrement
        stack = [data_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.ENCRYPTION_OPERATION:
                return True
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return False
    
    def _validate_secure_comms(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les communications sécurisées"""
        errors = []
        
        # Recherche des opérations réseau
        network_ops = self._find_network_operations(ast_node)
        
        for op in network_ops:
            # Vérification du protocole sécurisé
            protocol = getattr(op, 'protocol', 'http')
            if protocol.lower() in ['http', 'ftp', 'telnet']:
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.SECURITY,
                    severity=ValidationSeverity.WARNING,
                    message=f"Insecure protocol '{protocol}' used for network operation",
                    node_type=op.node_type.value,
                    line=getattr(op, 'line', None),
                    column=getattr(op, 'column', None),
                    context={'protocol': protocol},
                    suggestion="Use HTTPS, FTPS, or SSH instead"
                ))
        
        return errors
    
    def _find_network_operations(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les opérations réseau"""
        network_ops = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.NETWORK_OPERATION:
                network_ops.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return network_ops
    
    def _validate_input_validation(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la validation des entrées"""
        errors = []
        
        # Recherche des entrées utilisateur
        user_inputs = self._find_user_inputs(ast_node)
        
        for input_node in user_inputs:
            # Vérification de la validation
            if not self._has_input_validation(input_node):
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.SECURITY,
                    severity=ValidationSeverity.WARNING,
                    message="User input lacks validation",
                    node_type=input_node.node_type.value,
                    line=getattr(input_node, 'line', None),
                    column=getattr(input_node, 'column', None)
                ))
        
        return errors
    
    def _find_user_inputs(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les entrées utilisateur"""
        user_inputs = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.USER_INPUT:
                user_inputs.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return user_inputs
    
    def _has_input_validation(self, input_node: ASTNode) -> bool:
        """Vérifie si une entrée a une validation"""
        # Recherche de la validation dans le contexte
        stack = [input_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.VALIDATION:
                return True
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return False
    
    def _validate_forbidden_ops(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide l'absence d'opérations interdites"""
        errors = []
        
        forbidden_ops = self.security_config['forbidden_operations']
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if hasattr(node, 'operation_type'):
                op_type = node.operation_type
                if op_type in forbidden_ops:
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SECURITY,
                        severity=ValidationSeverity.ERROR,
                        message=f"Forbidden operation '{op_type}' detected",
                        node_type=node.node_type.value,
                        line=getattr(node, 'line', None),
                        column=getattr(node, 'column', None),
                        context={'operation': op_type}
                    ))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors
    
    def _validate_security_headers(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les en-têtes de sécurité"""
        errors = []
        
        # Recherche des opérations HTTP
        http_ops = self._find_http_operations(ast_node)
        
        required_headers = [
            'Content-Security-Policy',
            'X-Content-Type-Options',
            'X-Frame-Options',
            'Strict-Transport-Security'
        ]
        
        for op in http_ops:
            headers = getattr(op, 'headers', {})
            
            for required_header in required_headers:
                if required_header not in headers:
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.SECURITY,
                        severity=ValidationSeverity.WARNING,
                        message=f"Missing security header '{required_header}' in HTTP operation",
                        node_type=op.node_type.value,
                        line=getattr(op, 'line', None),
                        column=getattr(op, 'column', None),
                        context={'header': required_header}
                    ))
        
        return errors
    
    def _find_http_operations(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les opérations HTTP"""
        http_ops = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.HTTP_OPERATION:
                http_ops.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return http_ops


class PerformanceValidator:
    """Validateur de performance"""
    
    def __init__(self, performance_config: Optional[Dict[str, Any]] = None):
        """
        Initialise le validateur de performance.
        
        Args:
            performance_config: Configuration de performance
        """
        self.performance_config = performance_config or self._default_performance_config()
        
        # Contraintes de performance
        self.performance_constraints = self._load_performance_constraints()
        
        logger.info("PerformanceValidator initialisé")
    
    def _default_performance_config(self) -> Dict[str, Any]:
        """Configuration de performance par défaut"""
        return {
            'max_response_time_ms': 1000,
            'max_memory_mb': 512,
            'max_cpu_percent': 80,
            'max_network_latency_ms': 100,
            'max_database_query_time_ms': 200,
            'max_concurrent_requests': 100,
            'sla_compliance_required': True
        }
    
    def _load_performance_constraints(self) -> Dict[str, Any]:
        """Charge les contraintes de performance"""
        return {
            'response_time': {
                'description': 'Response time must meet SLA',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_response_time
            },
            'resource_usage': {
                'description': 'Resource usage must be within limits',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_resource_usage
            },
            'database_performance': {
                'description': 'Database operations must be optimized',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_database_performance
            },
            'network_performance': {
                'description': 'Network operations must be efficient',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_network_performance
            },
            'concurrency': {
                'description': 'Concurrent operations must be properly managed',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_concurrency
            },
            'caching': {
                'description': 'Appropriate caching should be used',
                'severity': ValidationSeverity.INFO,
                'validator': self._validate_caching
            }
        }
    
    def validate_performance(self, ast_node: ASTNode) -> List[ValidationError]:
        """
        Valide les contraintes de performance.
        
        Args:
            ast_node: Nœud AST racine
            
        Returns:
            Erreurs de performance
        """
        errors = []
        
        # Exécution de toutes les contraintes de performance
        for constraint_id, constraint_config in self.performance_constraints.items():
            try:
                constraint_errors = constraint_config['validator'](ast_node)
                for error in constraint_errors:
                    error.rule_id = constraint_id
                    error.category =ValidationRuleCategory.PERFORMANCE
                    error.severity = constraint_config['severity']
                errors.extend(constraint_errors)
            except Exception as e:
                logger.error(f"Error executing performance constraint {constraint_id}: {str(e)}")
        
        return errors
    
    def _validate_response_time(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide le temps de réponse"""
        errors = []
        
        max_response_time = self.performance_config['max_response_time_ms']
        
        # Analyse des opérations critiques
        critical_ops = self._find_critical_operations(ast_node)
        
        for op in critical_ops:
            estimated_time = getattr(op, 'estimated_response_time_ms', 0)
            
            if estimated_time > max_response_time:
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.PERFORMANCE,
                    severity=ValidationSeverity.WARNING,
                    message=f"Estimated response time ({estimated_time}ms) exceeds limit ({max_response_time}ms)",
                    node_type=op.node_type.value,
                    line=getattr(op, 'line', None),
                    column=getattr(op, 'column', None),
                    context={
                        'estimated_time': estimated_time,
                        'limit': max_response_time
                    },
                    suggestion="Optimize algorithm or implement caching"
                ))
        
        return errors
    
    def _find_critical_operations(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les opérations critiques pour la performance"""
        critical_ops = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Opérations potentiellement lentes
            if node.node_type in [
                NodeType.DATABASE_QUERY,
                NodeType.NETWORK_CALL,
                NodeType.FILE_OPERATION,
                NodeType.COMPLEX_COMPUTATION
            ]:
                critical_ops.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return critical_ops
    
    def _validate_resource_usage(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide l'utilisation des ressources"""
        errors = []
        
        max_memory = self.performance_config['max_memory_mb']
        max_cpu = self.performance_config['max_cpu_percent']
        
        # Analyse de l'utilisation des ressources
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if hasattr(node, 'estimated_memory_mb'):
                mem_usage = node.estimated_memory_mb
                if mem_usage > max_memory:
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.PERFORMANCE,
                        severity=ValidationSeverity.WARNING,
                        message=f"Estimated memory usage ({mem_usage}MB) exceeds limit ({max_memory}MB)",
                        node_type=node.node_type.value,
                        line=getattr(node, 'line', None),
                        column=getattr(node, 'column', None),
                        context={'memory_usage': mem_usage, 'limit': max_memory}
                    ))
            
            if hasattr(node, 'estimated_cpu_percent'):
                cpu_usage = node.estimated_cpu_percent
                if cpu_usage > max_cpu:
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.PERFORMANCE,
                        severity=ValidationSeverity.WARNING,
                        message=f"Estimated CPU usage ({cpu_usage}%) exceeds limit ({max_cpu}%)",
                        node_type=node.node_type.value,
                        line=getattr(node, 'line', None),
                        column=getattr(node, 'column', None),
                        context={'cpu_usage': cpu_usage, 'limit': max_cpu}
                    ))
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return errors
    
    def _validate_database_performance(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la performance des bases de données"""
        errors = []
        
        max_query_time = self.performance_config['max_database_query_time_ms']
        
        # Recherche des requêtes de base de données
        db_queries = self._find_database_queries(ast_node)
        
        for query in db_queries:
            estimated_time = getattr(query, 'estimated_execution_time_ms', 0)
            
            if estimated_time > max_query_time:
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.PERFORMANCE,
                    severity=ValidationSeverity.WARNING,
                    message=f"Database query estimated time ({estimated_time}ms) exceeds limit ({max_query_time}ms)",
                    node_type=query.node_type.value,
                    line=getattr(query, 'line', None),
                    column=getattr(query, 'column', None),
                    context={'query_time': estimated_time, 'limit': max_query_time},
                    suggestion="Add indexes, optimize query, or implement caching"
                ))
            
            # Vérification des N+1 queries
            if self._is_n_plus_one_query(query):
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.PERFORMANCE,
                    severity=ValidationSeverity.WARNING,
                    message="Potential N+1 query pattern detected",
                    node_type=query.node_type.value,
                    line=getattr(query, 'line', None),
                    column=getattr(query, 'column', None),
                    suggestion="Use eager loading or batch queries"
                ))
        
        return errors
    
    def _find_database_queries(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les requêtes de base de données"""
        db_queries = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.DATABASE_QUERY:
                db_queries.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return db_queries
    
    def _is_n_plus_one_query(self, query_node: ASTNode) -> bool:
        """Détecte les patterns N+1"""
        # Simplifié: détection basée sur la structure de la requête
        query_text = getattr(query_node, 'query', '')
        
        # Recherche de patterns N+1 simples
        n_plus_one_patterns = [
            r'SELECT.*FROM.*WHERE.*id\s*IN\s*\(SELECT',
            r'loop.*query',
            r'foreach.*execute'
        ]
        
        for pattern in n_plus_one_patterns:
            if re.search(pattern, query_text, re.IGNORECASE):
                return True
        
        return False
    
    def _validate_network_performance(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la performance réseau"""
        errors = []
        
        max_latency = self.performance_config['max_network_latency_ms']
        
        # Recherche des appels réseau
        network_calls = self._find_network_calls(ast_node)
        
        for call in network_calls:
            estimated_latency = getattr(call, 'estimated_latency_ms', 0)
            
            if estimated_latency > max_latency:
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.PERFORMANCE,
                    severity=ValidationSeverity.WARNING,
                    message=f"Network call estimated latency ({estimated_latency}ms) exceeds limit ({max_latency}ms)",
                    node_type=call.node_type.value,
                    line=getattr(call, 'line', None),
                    column=getattr(call, 'column', None),
                    context={'latency': estimated_latency, 'limit': max_latency},
                    suggestion="Implement connection pooling or use CDN"
                ))
            
            # Vérification des appels séquentiels
            if self._has_sequential_calls(call):
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.PERFORMANCE,
                    severity=ValidationSeverity.INFO,
                    message="Sequential network calls detected - consider parallelization",
                    node_type=call.node_type.value,
                    line=getattr(call, 'line', None),
                    column=getattr(call, 'column', None)
                ))
        
        return errors
    
    def _find_network_calls(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les appels réseau"""
        network_calls = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.NETWORK_CALL:
                network_calls.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return network_calls
    
    def _has_sequential_calls(self, call_node: ASTNode) -> bool:
        """Détecte les appels réseau séquentiels"""
        # Recherche de multiples appels dans le même contexte
        parent = getattr(call_node, 'parent', None)
        if not parent:
            return False
        
        # Compte des appels réseau dans le même parent
        network_call_count = 0
        if hasattr(parent, 'children'):
            for child in parent.children:
                if child.node_type == NodeType.NETWORK_CALL:
                    network_call_count += 1
        
        return network_call_count > 1
    
    def _validate_concurrency(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la gestion de la concurrence"""
        errors = []
        
        max_concurrent = self.performance_config['max_concurrent_requests']
        
        # Analyse des points de contention
        contention_points = self._find_contention_points(ast_node)
        
        for point in contention_points:
            concurrent_ops = getattr(point, 'concurrent_operations', 0)
            
            if concurrent_ops > max_concurrent:
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.PERFORMANCE,
                    severity=ValidationSeverity.WARNING,
                    message=f"Too many concurrent operations ({concurrent_ops}) on shared resource",
                    node_type=point.node_type.value,
                    line=getattr(point, 'line', None),
                    column=getattr(point, 'column', None),
                    context={'concurrent_ops': concurrent_ops, 'limit': max_concurrent},
                    suggestion="Implement rate limiting or queuing"
                ))
            
            # Vérification des deadlocks potentiels
            if self._has_potential_deadlock(point):
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.PERFORMANCE,
                    severity=ValidationSeverity.WARNING,
                    message="Potential deadlock detected in concurrent operations",
                    node_type=point.node_type.value,
                    line=getattr(point, 'line', None),
                    column=getattr(point, 'column', None),
                    suggestion="Use lock ordering or lock-free algorithms"
                ))
        
        return errors
    
    def _find_contention_points(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les points de contention"""
        contention_points = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Opérations avec potentiel de contention
            if node.node_type in [
                NodeType.LOCK_OPERATION,
                NodeType.SHARED_RESOURCE,
                NodeType.CONCURRENT_ACCESS
            ]:
                contention_points.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return contention_points
    
    def _has_potential_deadlock(self, node: ASTNode) -> bool:
        """Détecte les deadlocks potentiels"""
        # Recherche de patterns de verrouillage circulaire
        lock_operations = self._collect_lock_operations(node)
        
        if len(lock_operations) < 2:
            return False
        
        # Vérification simplifiée des deadlocks
        lock_order = []
        for op in lock_operations:
            lock_order.append(getattr(op, 'resource', 'unknown'))
        
        # Vérification des ordres de verrouillage inconsistants
        return len(set(lock_order)) != len(lock_order)
    
    def _collect_lock_operations(self, node: ASTNode) -> List[ASTNode]:
        """Collecte les opérations de verrouillage"""
        locks = []
        stack = [node]
        
        while stack:
            current = stack.pop()
            
            if current.node_type == NodeType.LOCK_OPERATION:
                locks.append(current)
            
            if hasattr(current, 'children'):
                stack.extend(current.children)
        
        return locks
    
    def _validate_caching(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide l'utilisation du cache"""
        errors = []
        
        # Recherche des opérations coûteuses sans cache
        expensive_ops = self._find_expensive_operations(ast_node)
        
        for op in expensive_ops:
            # Vérification de la présence de cache
            if not self._has_caching(op):
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.PERFORMANCE,
                    severity=ValidationSeverity.INFO,
                    message="Expensive operation without caching detected",
                    node_type=op.node_type.value,
                    line=getattr(op, 'line', None),
                    column=getattr(op, 'column', None),
                    suggestion="Implement caching for better performance"
                ))
        
        return errors
    
    def _find_expensive_operations(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les opérations coûteuses"""
        expensive_ops = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Critères pour les opérations coûteuses
            if hasattr(node, 'complexity'):
                complexity = node.complexity
                if complexity in ['O(n^2)', 'O(2^n)', 'O(n!)']:
                    expensive_ops.append(node)
            
            if hasattr(node, 'frequency'):
                frequency = node.frequency
                if frequency == 'high' and not getattr(node, 'cached', False):
                    expensive_ops.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return expensive_ops
    
    def _has_caching(self, operation_node: ASTNode) -> bool:
        """Vérifie si une opération a un cache"""
        # Recherche de configuration de cache
        stack = [operation_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.CACHE_CONFIG:
                return True
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return False


class CostValidator:
    """Validateur de coûts"""
    
    def __init__(self, cost_config: Optional[Dict[str, Any]] = None):
        """
        Initialise le validateur de coûts.
        
        Args:
            cost_config: Configuration des coûts
        """
        self.cost_config = cost_config or self._default_cost_config()
        self.business_calculator = BusinessValueCalculator()
        
        # Règles d'optimisation des coûts
        self.cost_rules = self._load_cost_rules()
        
        logger.info("CostValidator initialisé")
    
    def _default_cost_config(self) -> Dict[str, Any]:
        """Configuration des coûts par défaut"""
        return {
            'max_monthly_cost_usd': 1000,
            'cost_per_request': 0.01,
            'cost_per_gb_storage': 0.10,
            'cost_per_million_events': 1.00,
            'cost_optimization_required': True,
            'roi_threshold': 3.0  # ROI minimum requis
        }
    
    def _load_cost_rules(self) -> Dict[str, Any]:
        """Charge les règles de coûts"""
        return {
            'monthly_cost_limit': {
                'description': 'Monthly cost must not exceed limit',
                'severity': ValidationSeverity.ERROR,
                'validator': self._validate_monthly_cost
            },
            'resource_efficiency': {
                'description': 'Resources must be used efficiently',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_resource_efficiency
            },
            'storage_cost': {
                'description': 'Storage costs must be optimized',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_storage_cost
            },
            'compute_cost': {
                'description': 'Compute costs must be justified',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_compute_cost
            },
            'data_transfer_cost': {
                'description': 'Data transfer costs must be minimized',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_data_transfer_cost
            },
            'roi_validation': {
                'description': 'ROI must meet minimum threshold',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_roi
            }
        }
    
    def validate_cost(self, ast_node: ASTNode) -> List[ValidationError]:
        """
        Valide les contraintes de coûts.
        
        Args:
            ast_node: Nœud AST racine
            
        Returns:
            Erreurs de coûts
        """
        errors = []
        
        # Exécution de toutes les règles de coûts
        for rule_id, rule_config in self.cost_rules.items():
            try:
                rule_errors = rule_config['validator'](ast_node)
                for error in rule_errors:
                    error.rule_id = rule_id
                    error.category = ValidationRuleCategory.COST
                    error.severity = rule_config['severity']
                errors.extend(rule_errors)
            except Exception as e:
                logger.error(f"Error executing cost rule {rule_id}: {str(e)}")
        
        return errors
    
    def _validate_monthly_cost(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la limite de coût mensuel"""
        errors = []
        
        max_monthly_cost = self.cost_config['max_monthly_cost_usd']
        
        # Calcul du coût estimé
        estimated_cost = self._calculate_estimated_cost(ast_node)
        
        if estimated_cost > max_monthly_cost:
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.COST,
                severity=ValidationSeverity.ERROR,
                message=f"Estimated monthly cost (${estimated_cost:.2f}) exceeds limit (${max_monthly_cost:.2f})",
                context={'estimated_cost': estimated_cost, 'limit': max_monthly_cost},
                suggestion="Optimize resource usage or request budget increase"
            ))
        
        return errors
    
    def _calculate_estimated_cost(self, ast_node: ASTNode) -> float:
        """Calcule le coût mensuel estimé"""
        total_cost = 0.0
        
        # Analyse des ressources utilisées
        resources = self._analyze_resources(ast_node)
        
        for resource in resources:
            resource_type = resource.get('type')
            quantity = resource.get('quantity', 0)
            
            if resource_type == 'compute':
                total_cost += quantity * self.cost_config['cost_per_request']
            elif resource_type == 'storage':
                total_cost += quantity * self.cost_config['cost_per_gb_storage']
            elif resource_type == 'events':
                total_cost += quantity * self.cost_config['cost_per_million_events'] / 1000000
        
        return total_cost
    
    def _analyze_resources(self, ast_node: ASTNode) -> List[Dict[str, Any]]:
        """Analyse l'utilisation des ressources"""
        resources = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Collecte des ressources
            if node.node_type == NodeType.RESOURCE_USAGE:
                resource_info = {
                    'type': getattr(node, 'resource_type', 'unknown'),
                    'quantity': getattr(node, 'quantity', 0),
                    'unit': getattr(node, 'unit', 'unknown')
                }
                resources.append(resource_info)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return resources
    
    def _validate_resource_efficiency(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide l'efficacité des ressources"""
        errors = []
        
        # Recherche des ressources sous-utilisées
        resources = self._find_resources(ast_node)
        
        for resource in resources:
            utilization = getattr(resource, 'utilization_percent', 0)
            
            if utilization < 30:  # Seuil d'utilisation faible
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.COST,
                    severity=ValidationSeverity.WARNING,
                    message=f"Resource utilization low ({utilization}%)",
                    node_type=resource.node_type.value,
                    line=getattr(resource, 'line', None),
                    column=getattr(resource, 'column', None),
                    context={'utilization': utilization},
                    suggestion="Consider downsizing or sharing resources"
                ))
        
        return errors
    
    def _find_resources(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les définitions de ressources"""
        resources = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.RESOURCE_DEFINITION:
                resources.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return resources
    
    def _validate_storage_cost(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les coûts de stockage"""
        errors = []
        
        # Analyse du stockage
        storage_usage = self._analyze_storage_usage(ast_node)
        
        for storage in storage_usage:
            storage_type = storage.get('type')
            size_gb = storage.get('size_gb', 0)
            
            if storage_type == 'cold_storage' and size_gb > 100:
                # Données froides volumineuses
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.COST,
                    severity=ValidationSeverity.WARNING,
                    message=f"Large cold storage volume ({size_gb}GB) detected",
                    context={'size_gb': size_gb, 'type': storage_type},
                    suggestion="Consider data lifecycle management or compression"
                ))
            
            if storage_type == 'redundant' and size_gb > 50:
                # Redondance coûteuse
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.COST,
                    severity=ValidationSeverity.INFO,
                    message=f"Potentially excessive redundant storage ({size_gb}GB)",
                    context={'size_gb': size_gb, 'type': storage_type},
                    suggestion="Review redundancy requirements"
                ))
        
        return errors
    
    def _analyze_storage_usage(self, ast_node: ASTNode) -> List[Dict[str, Any]]:
        """Analyse l'utilisation du stockage"""
        storage_usage = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.STORAGE_USAGE:
                usage_info = {
                    'type': getattr(node, 'storage_type', 'unknown'),
                    'size_gb': getattr(node, 'size_gb', 0),
                    'retention_days': getattr(node, 'retention_days', 0)
                }
                storage_usage.append(usage_info)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return storage_usage
    
    def _validate_compute_cost(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les coûts de calcul"""
        errors = []
        
        # Analyse des instances de calcul
        compute_instances = self._find_compute_instances(ast_node)
        
        for instance in compute_instances:
            instance_type = getattr(instance, 'instance_type', 'unknown')
            running_hours = getattr(instance, 'running_hours_per_day', 0)
            
            if running_hours < 8:
                # Instance utilisée moins de 8h/jour
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.COST,
                    severity=ValidationSeverity.WARNING,
                    message=f"Compute instance '{instance_type}' only used {running_hours}h/day",
                    node_type=instance.node_type.value,
                    line=getattr(instance, 'line', None),
                    column=getattr(instance, 'column', None),
                    context={'instance_type': instance_type, 'hours': running_hours},
                    suggestion="Consider using spot instances or auto-scaling"
                ))
        
        return errors
    
    def _find_compute_instances(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les instances de calcul"""
        instances = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.COMPUTE_INSTANCE:
                instances.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return instances
    
    def _validate_data_transfer_cost(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les coûts de transfert de données"""
        errors = []
        
        # Analyse des transferts de données
        data_transfers = self._find_data_transfers(ast_node)
        
        total_transfer_gb = 0
        for transfer in data_transfers:
            size_gb = getattr(transfer, 'size_gb', 0)
            total_transfer_gb += size_gb
        
        if total_transfer_gb > 100:  # Seuil arbitraire
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.COST,
                severity=ValidationSeverity.WARNING,
                message=f"High data transfer volume ({total_transfer_gb}GB) detected",
                context={'total_transfer_gb': total_transfer_gb},
                suggestion="Consider data locality or compression"
            ))
        
        return errors
    
    def _find_data_transfers(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les transferts de données"""
        transfers = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.DATA_TRANSFER:
                transfers.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return transfers
    
    def _validate_roi(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide le retour sur investissement"""
        errors = []
        
        roi_threshold = self.cost_config['roi_threshold']
        
        # Calcul du ROI
        estimated_roi = self._calculate_estimated_roi(ast_node)
        
        if estimated_roi < roi_threshold:
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.COST,
                severity=ValidationSeverity.WARNING,
                message=f"Estimated ROI ({estimated_roi:.2f}) below threshold ({roi_threshold})",
                context={'estimated_roi': estimated_roi, 'threshold': roi_threshold},
                suggestion="Review business value or optimize costs"
            ))
        
        return errors
    
    def _calculate_estimated_roi(self, ast_node: ASTNode) -> float:
        """Calcule le ROI estimé"""
        # Analyse des bénéfices business
        business_value = self._estimate_business_value(ast_node)
        
        # Analyse des coûts
        estimated_cost = self._calculate_estimated_cost(ast_node)
        
        if estimated_cost <= 0:
            return float('inf')
        
        return business_value / estimated_cost
    
    def _estimate_business_value(self, ast_node: ASTNode) -> float:
        """Estime la valeur business"""
        total_value = 0.0
        
        # Recherche des bénéfices business
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.BUSINESS_VALUE:
                value = getattr(node, 'estimated_value_usd', 0)
                total_value += value
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return total_value


class ComplianceValidator:
    """Validateur de conformité"""
    
    def __init__(self, compliance_config: Optional[Dict[str, Any]] = None):
        """
        Initialise le validateur de conformité.
        
        Args:
            compliance_config: Configuration de conformité
        """
        self.compliance_config = compliance_config or self._default_compliance_config()
        self.compliance_checker = ComplianceChecker()
        
        # Standards de conformité
        self.compliance_standards = self._load_compliance_standards()
        
        logger.info("ComplianceValidator initialisé")
    
    def _default_compliance_config(self) -> Dict[str, Any]:
        """Configuration de conformité par défaut"""
        return {
            'required_standards': ['GDPR', 'SOC2', 'ISO27001', 'HIPAA'],
            'data_retention_days': 90,
            'audit_trail_required': True,
            'access_logging_required': True,
            'data_encryption_at_rest': True,
            'data_encryption_in_transit': True
        }
    
    def _load_compliance_standards(self) -> Dict[str, Dict[str, Any]]:
        """Charge les standards de conformité"""
        return {
            'gdpr': {
                'name': 'GDPR',
                'description': 'General Data Protection Regulation',
                'validator': self._validate_gdpr_compliance
            },
            'soc2': {
                'name': 'SOC2',
                'description': 'Service Organization Control 2',
                'validator': self._validate_soc2_compliance
            },
            'iso27001': {
                'name': 'ISO27001',
                'description': 'Information Security Management',
                'validator': self._validate_iso27001_compliance
            },
            'hipaa': {
                'name': 'HIPAA',
                'description': 'Health Insurance Portability and Accountability Act',
                'validator': self._validate_hipaa_compliance
            },
            'pci_dss': {
                'name': 'PCI DSS',
                'description': 'Payment Card Industry Data Security Standard',
                'validator': self._validate_pci_dss_compliance
            }
        }
    
    def validate_compliance(self, ast_node: ASTNode) -> List[ValidationError]:
        """
        Valide la conformité aux standards.
        
        Args:
            ast_node: Nœud AST racine
            
        Returns:
            Erreurs de conformité
        """
        errors = []
        
        # Validation de tous les standards requis
        for standard_name in self.compliance_config['required_standards']:
            standard_key = standard_name.lower()
            
            if standard_key in self.compliance_standards:
                try:
                    standard_validator = self.compliance_standards[standard_key]['validator']
                    standard_errors = standard_validator(ast_node)
                    
                    for error in standard_errors:
                        error.rule_id = f"COMP_{standard_name.upper()}"
                        error.category = ValidationRuleCategory.COMPLIANCE
                        error.severity = ValidationSeverity.ERROR
                    
                    errors.extend(standard_errors)
                except Exception as e:
                    logger.error(f"Error executing compliance validator for {standard_name}: {str(e)}")
        
        return errors
    
    def _validate_gdpr_compliance(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la conformité GDPR"""
        errors = []
        
        # Recherche des données personnelles
        personal_data = self._find_personal_data(ast_node)
        
        for data_node in personal_data:
            # Vérification des droits GDPR
            gdpr_checks = [
                ('consent_required', 'Data processing requires user consent'),
                ('right_to_be_forgotten', 'Right to be forgotten must be supported'),
                ('data_portability', 'Data portability must be supported'),
                ('privacy_by_design', 'Privacy by design must be implemented')
            ]
            
            for check_name, check_description in gdpr_checks:
                if not self._has_gdpr_feature(data_node, check_name):
                    errors.append(ValidationError(
                        rule_id="",
                        category=ValidationRuleCategory.COMPLIANCE,
                        severity=ValidationSeverity.ERROR,
                        message=f"GDPR violation: {check_description}",
                        node_type=data_node.node_type.value,
                        line=getattr(data_node, 'line', None),
                        column=getattr(data_node, 'column', None),
                        context={'check': check_name}
                    ))
        
        return errors
    
    def _find_personal_data(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les données personnelles"""
        personal_data = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.PERSONAL_DATA:
                personal_data.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return personal_data
    
    def _has_gdpr_feature(self, data_node: ASTNode, feature: str) -> bool:
        """Vérifie si une fonctionnalité GDPR est présente"""
        # Recherche des fonctionnalités GDPR dans le contexte
        stack = [data_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.GDPR_FEATURE:
                feature_name = getattr(node, 'feature_name', '')
                if feature_name == feature:
                    return True
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return False
    
    def _validate_soc2_compliance(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la conformité SOC2"""
        errors = []
        
        # Principes SOC2
        soc2_principles = [
            ('security', 'System must be protected against unauthorized access'),
            ('availability', 'System must be available for operation and use'),
            ('processing_integrity', 'Processing must be complete, accurate, and authorized'),
            ('confidentiality', 'Information must be protected as committed or agreed'),
            ('privacy', 'Personal information must be collected, used, retained, and disclosed appropriately')
        ]
        
        for principle, description in soc2_principles:
            if not self._has_soc2_principle(ast_node, principle):
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.COMPLIANCE,
                    severity=ValidationSeverity.ERROR,
                    message=f"SOC2 violation: {description}",
                    context={'principle': principle}
                ))
        
        return errors
    
    def _has_soc2_principle(self, ast_node: ASTNode, principle: str) -> bool:
        """Vérifie si un principe SOC2 est respecté"""
        # Recherche des contrôles SOC2
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.SOC2_CONTROL:
                control_principle = getattr(node, 'principle', '')
                if control_principle == principle:
                    return True
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return False
    
    def _validate_iso27001_compliance(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la conformité ISO27001"""
        errors = []
        
        # Annexes A de l'ISO27001
        iso_controls = [
            ('A.9', 'Access control'),
            ('A.10', 'Cryptography'),
            ('A.12', 'Operations security'),
            ('A.13', 'Communications security'),
            ('A.14', 'System acquisition, development and maintenance'),
            ('A.16', 'Information security incident management'),
            ('A.17', 'Information security aspects of business continuity management')
        ]
        
        for control_code, control_description in iso_controls:
            if not self._has_iso27001_control(ast_node, control_code):
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.COMPLIANCE,
                    severity=ValidationSeverity.WARNING,
                    message=f"ISO27001 control {control_code} ({control_description}) not implemented",
                    context={'control': control_code}
                ))
        
        return errors
    
    def _has_iso27001_control(self, ast_node: ASTNode, control_code: str) -> bool:
        """Vérifie si un contrôle ISO27001 est présent"""
        # Recherche des contrôles ISO27001
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.ISO27001_CONTROL:
                code = getattr(node, 'control_code', '')
                if code == control_code:
                    return True
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return False
    
    def _validate_hipaa_compliance(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la conformité HIPAA"""
        errors = []
        
        # Règles HIPAA
        hipaa_rules = [
            ('privacy_rule', 'Protected Health Information (PHI) privacy'),
            ('security_rule', 'Electronic PHI (ePHI) security'),
            ('breach_notification', 'Breach notification requirements')
        ]
        
        for rule_name, rule_description in hipaa_rules:
            if not self._has_hipaa_rule(ast_node, rule_name):
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.COMPLIANCE,
                    severity=ValidationSeverity.ERROR,
                    message=f"HIPAA violation: {rule_description} not addressed",
                    context={'rule': rule_name}
                ))
        
        return errors
    
    def _has_hipaa_rule(self, ast_node: ASTNode, rule_name: str) -> bool:
        """Vérifie si une règle HIPAA est respectée"""
        # Recherche des protections HIPAA
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.HIPAA_PROTECTION:
                protection_rule = getattr(node, 'rule_name', '')
                if protection_rule == rule_name:
                    return True
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return False
    
    def _validate_pci_dss_compliance(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la conformité PCI DSS"""
        errors = []
        
        # Exigences PCI DSS
        pci_requirements = [
            ('1', 'Install and maintain a firewall configuration to protect cardholder data'),
            ('2', 'Do not use vendor-supplied defaults for system passwords and other security parameters'),
            ('3', 'Protect stored cardholder data'),
            ('4', 'Encrypt transmission of cardholder data across open, public networks'),
            ('5', 'Use and regularly update anti-virus software or programs'),
            ('6', 'Develop and maintain secure systems and applications'),
            ('7', 'Restrict access to cardholder data by business need-to-know'),
            ('8', 'Assign a unique ID to each person with computer access'),
            ('9', 'Restrict physical access to cardholder data'),
            ('10', 'Track and monitor all access to network resources and cardholder data'),
            ('11', 'Regularly test security systems and processes'),
            ('12', 'Maintain a policy that addresses information security for all personnel')
        ]
        
        for req_number, req_description in pci_requirements:
            if not self._has_pci_requirement(ast_node, req_number):
                errors.append(ValidationError(
                    rule_id="",
                    category=ValidationRuleCategory.COMPLIANCE,
                    severity=ValidationSeverity.ERROR,
                    message=f"PCI DSS requirement {req_number} not met: {req_description}",
                    context={'requirement': req_number}
                ))
        
        return errors
    
    def _has_pci_requirement(self, ast_node: ASTNode, requirement: str) -> bool:
        """Vérifie si une exigence PCI DSS est respectée"""
        # Recherche des contrôles PCI
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.PCI_CONTROL:
                control_requirement = getattr(node, 'requirement', '')
                if control_requirement == requirement:
                    return True
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return False


class BestPracticesValidator:
    """Validateur des bonnes pratiques"""
    
    def __init__(self, best_practices_config: Optional[Dict[str, Any]] = None):
        """
        Initialise le validateur des bonnes pratiques.
        
        Args:
            best_practices_config: Configuration des bonnes pratiques
        """
        self.best_practices_config = best_practices_config or self._default_best_practices_config()
        
        # Catalogue des bonnes pratiques
        self.best_practices = self._load_best_practices()
        
        logger.info("BestPracticesValidator initialisé")
    
    def _default_best_practices_config(self) -> Dict[str, Any]:
        """Configuration des bonnes pratiques par défaut"""
        return {
            'code_quality_required': True,
            'documentation_required': True,
            'testing_required': True,
            'logging_required': True,
            'monitoring_required': True,
            'error_handling_required': True,
            'version_control_required': True,
            'code_review_required': True
        }
    
    def _load_best_practices(self) -> Dict[str, Dict[str, Any]]:
        """Charge les bonnes pratiques"""
        return {
            'code_quality': {
                'description': 'Code quality best practices',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_code_quality
            },
            'documentation': {
                'description': 'Documentation best practices',
                'severity': ValidationSeverity.INFO,
                'validator': self._validate_documentation
            },
            'testing': {
                'description': 'Testing best practices',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_testing
            },
            'logging': {
                'description': 'Logging best practices',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_logging
            },
            'error_handling': {
                'description': 'Error handling best practices',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_error_handling
            },
            'security_practices': {
                'description': 'Security best practices',
                'severity': ValidationSeverity.WARNING,
                'validator': self._validate_security_practices
            },
            'performance_practices': {
                'description': 'Performance best practices',
                'severity': ValidationSeverity.INFO,
                'validator': self._validate_performance_practices
            },
            'maintainability': {
                'description': 'Maintainability best practices',
                'severity': ValidationSeverity.INFO,
                'validator': self._validate_maintainability
            }
        }
    
    def validate_best_practices(self, ast_node: ASTNode) -> List[ValidationError]:
        """
        Valide les bonnes pratiques.
        
        Args:
            ast_node: Nœud AST racine
            
        Returns:
            Erreurs de bonnes pratiques
        """
        errors = []
        
        # Exécution de toutes les validations de bonnes pratiques
        for practice_id, practice_config in self.best_practices.items():
            try:
                practice_errors = practice_config['validator'](ast_node)
                for error in practice_errors:
                    error.rule_id = practice_id
                    error.category = ValidationRuleCategory.BEST_PRACTICE
                    error.severity = practice_config['severity']
                errors.extend(practice_errors)
            except Exception as e:
                logger.error(f"Error executing best practice validator {practice_id}: {str(e)}")
        
        return errors
    
    def _validate_code_quality(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la qualité du code"""
        errors = []
        
        # Recherche des problèmes de qualité de code
        quality_issues = self._find_code_quality_issues(ast_node)
        
        for issue in quality_issues:
            issue_type = getattr(issue, 'issue_type', 'unknown')
            description = getattr(issue, 'description', 'Code quality issue')
            
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.BEST_PRACTICE,
                severity=ValidationSeverity.WARNING,
                message=f"Code quality: {description}",
                node_type=issue.node_type.value,
                line=getattr(issue, 'line', None),
                column=getattr(issue, 'column', None),
                context={'issue_type': issue_type}
            ))
        
        # Validation de la complexité cyclomatique
        complexity = self._calculate_cyclomatic_complexity(ast_node)
        if complexity > 10:
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.BEST_PRACTICE,
                severity=ValidationSeverity.WARNING,
                message=f"High cyclomatic complexity ({complexity}) detected",
                context={'complexity': complexity},
                suggestion="Refactor into smaller functions"
            ))
        
        return errors
    
    def _find_code_quality_issues(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les problèmes de qualité de code"""
        issues = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Détection des mauvaises pratiques
            if node.node_type == NodeType.CODE_SMELL:
                issues.append(node)
            
            # Longues méthodes
            if node.node_type == NodeType.FUNCTION_DEFINITION:
                line_count = self._count_function_lines(node)
                if line_count > 50:
                    node.issue_type = 'long_function'
                    node.description = f'Function too long ({line_count} lines)'
                    issues.append(node)
            
            # Profondeur d'imbrication excessive
            if node.node_type == NodeType.NESTED_BLOCK:
                depth = self._calculate_nesting_depth(node)
                if depth > 5:
                    node.issue_type = 'deep_nesting'
                    node.description = f'Nesting too deep ({depth} levels)'
                    issues.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return issues
    
    def _count_function_lines(self, function_node: ASTNode) -> int:
        """Compte les lignes d'une fonction"""
        line_count = 0
        stack = [function_node]
        
        while stack:
            node = stack.pop()
            
            if hasattr(node, 'line'):
                line_count = max(line_count, node.line)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return line_count
    
    def _calculate_nesting_depth(self, node: ASTNode) -> int:
        """Calcule la profondeur d'imbrication"""
        depth = 0
        current = node
        
        while hasattr(current, 'parent') and current.parent:
            if current.parent.node_type in [NodeType.BLOCK, NodeType.LOOP, NodeType.CONDITIONAL]:
                depth += 1
            current = current.parent
        
        return depth
    
    def _calculate_cyclomatic_complexity(self, ast_node: ASTNode) -> int:
        """Calcule la complexité cyclomatique"""
        complexity = 1  # Base complexity
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Nœuds qui augmentent la complexité
            if node.node_type in [
                NodeType.IF_STATEMENT,
                NodeType.WHILE_LOOP,
                NodeType.FOR_LOOP,
                NodeType.CASE_STATEMENT,
                NodeType.CONDITIONAL_OPERATOR
            ]:
                complexity += 1
            
            # Conditions multiples
            if node.node_type == NodeType.LOGICAL_EXPRESSION:
                # Approximation: chaque opérateur logique ajoute de la complexité
                operators = self._count_logical_operators(node)
                complexity += operators
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return complexity
    
    def _count_logical_operators(self, expression_node: ASTNode) -> int:
        """Compte les opérateurs logiques dans une expression"""
        count = 0
        stack = [expression_node]
        
        while stack:
            node = stack.pop()
            
            if node.node_type == NodeType.BINARY_OPERATION:
                operator = getattr(node, 'operator', '')
                if operator in ['&&', '||']:
                    count += 1
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return count
    
    def _validate_documentation(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la documentation"""
        errors = []
        
        # Recherche des éléments non documentés
        undocumented_items = self._find_undocumented_items(ast_node)
        
        for item in undocumented_items:
            item_type = item.node_type.value
            item_name = getattr(item, 'name', 'unknown')
            
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.BEST_PRACTICE,
                severity=ValidationSeverity.INFO,
                message=f"{item_type} '{item_name}' lacks documentation",
                node_type=item_type,
                line=getattr(item, 'line', None),
                column=getattr(item, 'column', None),
                context={'item_type': item_type, 'item_name': item_name}
            ))
        
        return errors
    
    def _find_undocumented_items(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les éléments non documentés"""
        undocumented = []
        
        # Éléments qui devraient être documentés
        documentable_types = [
            NodeType.AGENT_DEFINITION,
            NodeType.FUNCTION_DEFINITION,
            NodeType.CLASS_DEFINITION,
            NodeType.INTERFACE_DEFINITION,
            NodeType.WORKFLOW_DEFINITION
        ]
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type in documentable_types:
                if not self._has_documentation(node):
                    undocumented.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return undocumented
    
    def _has_documentation(self, node: ASTNode) -> bool:
        """Vérifie si un nœud a une documentation"""
        # Recherche de nœuds de documentation dans le contexte
        stack = [node]
        while stack:
            current = stack.pop()
            
            if current.node_type == NodeType.DOCUMENTATION:
                return True
            
            # Vérifier les frères précédents
            if hasattr(current, 'parent') and current.parent:
                parent = current.parent
                if hasattr(parent, 'children'):
                    for child in parent.children:
                        if child.node_type == NodeType.DOCUMENTATION:
                            return True
            
            if hasattr(current, 'children'):
                stack.extend(current.children)
        
        return False
    
    def _validate_testing(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les pratiques de test"""
        errors = []
        
        # Recherche du code sans tests
        untested_code = self._find_untested_code(ast_node)
        
        for code_node in untested_code:
            code_type = code_node.node_type.value
            code_name = getattr(code_node, 'name', 'unknown')
            
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.BEST_PRACTICE,
                severity=ValidationSeverity.WARNING,
                message=f"{code_type} '{code_name}' has no associated tests",
                node_type=code_type,
                line=getattr(code_node, 'line', None),
                column=getattr(code_node, 'column', None),
                context={'code_type': code_type, 'code_name': code_name}
            ))
        
        return errors
    
    def _find_untested_code(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve le code sans tests"""
        untested = []
        
        # Types de code qui devraient avoir des tests
        testable_types = [
            NodeType.FUNCTION_DEFINITION,
            NodeType.CLASS_DEFINITION,
            NodeType.WORKFLOW_DEFINITION,
            NodeType.AGENT_DEFINITION
        ]
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type in testable_types:
                if not self._has_tests(node):
                    untested.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return untested
    
    def _has_tests(self, node: ASTNode) -> bool:
        """Vérifie si un nœud a des tests associés"""
        # Recherche de nœuds de test dans le contexte
        stack = [node]
        while stack:
            current = stack.pop()
            
            if current.node_type == NodeType.TEST_DEFINITION:
                # Vérifier si le test correspond au nœud
                tested_item = getattr(current, 'tests', None)
                if tested_item == node.name:
                    return True
            
            if hasattr(current, 'children'):
                stack.extend(current.children)
        
        return False
    
    def _validate_logging(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les pratiques de logging"""
        errors = []
        
        # Recherche des opérations importantes sans logging
        unlogged_operations = self._find_unlogged_operations(ast_node)
        
        for op in unlogged_operations:
            op_type = op.node_type.value
            op_name = getattr(op, 'name', 'unknown')
            
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.BEST_PRACTICE,
                severity=ValidationSeverity.WARNING,
                message=f"Important operation '{op_name}' lacks logging",
                node_type=op_type,
                line=getattr(op, 'line', None),
                column=getattr(op, 'column', None),
                context={'operation': op_name}
            ))
        
        return errors
    
    def _find_unlogged_operations(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les opérations importantes sans logging"""
        unlogged = []
        
        # Opérations qui devraient être journalisées
        loggable_operations = [
            NodeType.DATABASE_QUERY,
            NodeType.NETWORK_CALL,
            NodeType.FILE_OPERATION,
            NodeType.ERROR_HANDLER,
            NodeType.AUTHENTICATION,
            NodeType.AUTHORIZATION
        ]
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type in loggable_operations:
                if not self._has_logging(node):
                    unlogged.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return unlogged
    
    def _has_logging(self, node: ASTNode) -> bool:
        """Vérifie si une opération a du logging"""
        # Recherche de nœuds de logging dans le contexte
        stack = [node]
        while stack:
            current = stack.pop()
            
            if current.node_type == NodeType.LOG_STATEMENT:
                return True
            
            if hasattr(current, 'children'):
                stack.extend(current.children)
        
        return False
    
    def _validate_error_handling(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la gestion des erreurs"""
        errors = []
        
        # Recherche des opérations sans gestion d'erreur
        unhandled_operations = self._find_unhandled_operations(ast_node)
        
        for op in unhandled_operations:
            op_type = op.node_type.value
            op_name = getattr(op, 'name', 'unknown')
            
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.BEST_PRACTICE,
                severity=ValidationSeverity.WARNING,
                message=f"Operation '{op_name}' lacks error handling",
                node_type=op_type,
                line=getattr(op, 'line', None),
                column=getattr(op, 'column', None),
                context={'operation': op_name}
            ))
        
        return errors
    
    def _find_unhandled_operations(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les opérations sans gestion d'erreur"""
        unhandled = []
        
        # Opérations qui peuvent échouer
        fallible_operations = [
            NodeType.DATABASE_QUERY,
            NodeType.NETWORK_CALL,
            NodeType.FILE_OPERATION,
            NodeType.EXTERNAL_SERVICE_CALL
        ]
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            if node.node_type in fallible_operations:
                if not self._has_error_handling(node):
                    unhandled.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return unhandled
    
    def _has_error_handling(self, node: ASTNode) -> bool:
        """Vérifie si une opération a une gestion d'erreur"""
        # Recherche de blocs try-catch dans le contexte
        stack = [node]
        while stack:
            current = stack.pop()
            
            if current.node_type == NodeType.TRY_CATCH:
                # Vérifier si ce bloc couvre l'opération
                if self._is_in_try_block(node, current):
                    return True
            
            if hasattr(current, 'parent'):
                stack.append(current.parent)
        
        return False
    
    def _is_in_try_block(self, operation_node: ASTNode, try_catch_node: ASTNode) -> bool:
        """Vérifie si une opération est dans un bloc try"""
        # Parcours de l'arbre pour vérifier la relation parent-enfant
        current = operation_node
        while current:
            if current == try_catch_node:
                return True
            current = getattr(current, 'parent', None)
        
        return False
    
    def _validate_security_practices(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les pratiques de sécurité"""
        errors = []
        
        # Recherche des mauvaises pratiques de sécurité
        security_issues = self._find_security_issues(ast_node)
        
        for issue in security_issues:
            issue_type = getattr(issue, 'issue_type', 'unknown')
            description = getattr(issue, 'description', 'Security issue')
            
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.BEST_PRACTICE,
                severity=ValidationSeverity.WARNING,
                message=f"Security: {description}",
                node_type=issue.node_type.value,
                line=getattr(issue, 'line', None),
                column=getattr(issue, 'column', None),
                context={'issue_type': issue_type}
            ))
        
        return errors
    
    def _find_security_issues(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les problèmes de sécurité"""
        issues = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Détection des mauvaises pratiques de sécurité
            if node.node_type == NodeType.HARDCODED_SECRET:
                node.issue_type = 'hardcoded_secret'
                node.description = 'Hardcoded secret detected'
                issues.append(node)
            
            elif node.node_type == NodeType.INSECURE_ALGORITHM:
                node.issue_type = 'insecure_algorithm'
                node.description = 'Insecure cryptographic algorithm used'
                issues.append(node)
            
            elif node.node_type == NodeType.SQL_INJECTION_RISK:
                node.issue_type = 'sql_injection'
                node.description = 'Potential SQL injection vulnerability'
                issues.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return issues
    
    def _validate_performance_practices(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide les pratiques de performance"""
        errors = []
        
        # Recherche des mauvaises pratiques de performance
        performance_issues = self._find_performance_issues(ast_node)
        
        for issue in performance_issues:
            issue_type = getattr(issue, 'issue_type', 'unknown')
            description = getattr(issue, 'description', 'Performance issue')
            
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.BEST_PRACTICE,
                severity=ValidationSeverity.INFO,
                message=f"Performance: {description}",
                node_type=issue.node_type.value,
                line=getattr(issue, 'line', None),
                column=getattr(issue, 'column', None),
                context={'issue_type': issue_type}
            ))
        
        return errors
    
    def _find_performance_issues(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les problèmes de performance"""
        issues = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Détection des mauvaises pratiques de performance
            if node.node_type == NodeType.INEFFICIENT_ALGORITHM:
                node.issue_type = 'inefficient_algorithm'
                node.description = 'Inefficient algorithm used'
                issues.append(node)
            
            elif node.node_type == NodeType.MEMORY_LEAK_RISK:
                node.issue_type = 'memory_leak_risk'
                node.description = 'Potential memory leak'
                issues.append(node)
            
            elif node.node_type == NodeType.N_PLUS_ONE_QUERY:
                node.issue_type = 'n_plus_one'
                node.description = 'N+1 query pattern detected'
                issues.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return issues
    
    def _validate_maintainability(self, ast_node: ASTNode) -> List[ValidationError]:
        """Valide la maintenabilité"""
        errors = []
        
        # Recherche des problèmes de maintenabilité
        maintainability_issues = self._find_maintainability_issues(ast_node)
        
        for issue in maintainability_issues:
            issue_type = getattr(issue, 'issue_type', 'unknown')
            description = getattr(issue, 'description', 'Maintainability issue')
            
            errors.append(ValidationError(
                rule_id="",
                category=ValidationRuleCategory.BEST_PRACTICE,
                severity=ValidationSeverity.INFO,
                message=f"Maintainability: {description}",
                node_type=issue.node_type.value,
                line=getattr(issue, 'line', None),
                column=getattr(issue, 'column', None),
                context={'issue_type': issue_type}
            ))
        
        return errors
    
    def _find_maintainability_issues(self, ast_node: ASTNode) -> List[ASTNode]:
        """Trouve les problèmes de maintenabilité"""
        issues = []
        
        stack = [ast_node]
        while stack:
            node = stack.pop()
            
            # Détection des problèmes de maintenabilité
            if node.node_type == NodeType.MAGIC_NUMBER:
                node.issue_type = 'magic_number'
                node.description = 'Magic number detected'
                issues.append(node)
            
            elif node.node_type == NodeType.DUPLICATE_CODE:
                node.issue_type = 'duplicate_code'
                node.description = 'Duplicate code detected'
                issues.append(node)
            
            elif node.node_type == NodeType.GOD_OBJECT:
                node.issue_type = 'god_object'
                node.description = 'God object detected (too many responsibilities)'
                issues.append(node)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return issues


class DSLValidator:
    """Validateur DSL principal"""
    
    def __init__(
        self,
        registry_client: Optional[Any] = None,
        security_config: Optional[Dict[str, Any]] = None,
        performance_config: Optional[Dict[str, Any]] = None,
        cost_config: Optional[Dict[str, Any]] = None,
        compliance_config: Optional[Dict[str, Any]] = None,
        best_practices_config: Optional[Dict[str, Any]] = None
    ):
        """
        Initialise le validateur DSL.
        
        Args:
            registry_client: Client pour le registre d'agents
            security_config: Configuration de sécurité
            performance_config: Configuration de performance
            cost_config: Configuration des coûts
            compliance_config: Configuration de conformité
            best_practices_config: Configuration des bonnes pratiques
        """
        # Initialisation des validateurs spécialisés
        self.syntax_validator = SyntaxValidator()
        self.semantic_validator = SemanticValidator(registry_client)
        self.type_validator = TypeValidator()
        self.security_validator = SecurityValidator(security_config)
        self.performance_validator = PerformanceValidator(performance_config)
        self.cost_validator = CostValidator(cost_config)
        self.compliance_validator = ComplianceValidator(compliance_config)
        self.best_practices_validator = BestPracticesValidator(best_practices_config)
        
        # Parser DSL
        self.parser = DSLParser()
        
        logger.info("DSLValidator initialisé avec tous les composants")
    
    def validate(
        self,
        dsl_code: str,
        validation_levels: Optional[List[ValidationLevel]] = None,
        stop_on_error: bool = False
    ) -> ValidationResult:
        """
        Valide le code DSL.
        
        Args:
            dsl_code: Code DSL à valider
            validation_levels: Niveaux de validation à exécuter (None = tous)
            stop_on_error: S'arrêter à la première erreur
            
        Returns:
            Résultat de validation
        """
        import time
        start_time = time.time()
        
        result = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=[],
            infos=[],
            validation_time_ms=0,
            validated_elements=set()
        )
        
        # Niveaux de validation par défaut
        if validation_levels is None:
            validation_levels = list(ValidationLevel)
        
        try:
            # Parsing du code DSL
            ast_tree = self.parser.parse(dsl_code)
            
            if not ast_tree:
                result.add_error(ValidationError(
                    rule_id="PARSER_001",
                    category=ValidationRuleCategory.SYNTAX,
                    severity=ValidationSeverity.ERROR,
                    message="Failed to parse DSL code",
                    line=1,
                    column=1
                ))
                return result
            
            # Exécution des validations selon les niveaux demandés
            for level in validation_levels:
                if stop_on_error and not result.is_valid:
                    break
                
                level_errors = self._execute_validation_level(level, ast_tree, dsl_code)
                self._add_validation_results(result, level_errors)
            
            # Calcul du temps de validation
            result.validation_time_ms = (time.time() - start_time) * 1000
            
            # Ajout des éléments validés
            result.validated_elements.update(self._collect_validated_elements(ast_tree))
            
        except Exception as e:
            logger.error(f"Validation error: {str(e)}")
            result.add_error(ValidationError(
                rule_id="VALIDATOR_001",
                category=ValidationRuleCategory.SYNTAX,
                severity=ValidationSeverity.ERROR,
                message=f"Validation failed: {str(e)}"
            ))
        
        return result
    
    def _execute_validation_level(
        self,
        level: ValidationLevel,
        ast_tree: ASTNode,
        dsl_code: str
    ) -> List[ValidationError]:
        """Exécute une validation à un niveau spécifique"""
        errors = []
        
        try:
            if level == ValidationLevel.SYNTAX:
                errors.extend(self.syntax_validator.validate_syntax(dsl_code))
            
            elif level == ValidationLevel.SEMANTIC:
                errors.extend(self.semantic_validator.validate_semantics(ast_tree))
            
            elif level == ValidationLevel.TYPE:
                errors.extend(self.type_validator.validate_types(ast_tree))
            
            elif level == ValidationLevel.SECURITY:
                errors.extend(self.security_validator.validate_security(ast_tree))
            
            elif level == ValidationLevel.PERFORMANCE:
                errors.extend(self.performance_validator.validate_performance(ast_tree))
            
            elif level == ValidationLevel.COMPLIANCE:
                errors.extend(self.compliance_validator.validate_compliance(ast_tree))
            
            elif level == ValidationLevel.BEST_PRACTICE:
                errors.extend(self.best_practices_validator.validate_best_practices(ast_tree))
        
        except Exception as e:
            logger.error(f"Error in {level.value} validation: {str(e)}")
            errors.append(ValidationError(
                rule_id=f"{level.value.upper()}_ERROR",
                category=getattr(ValidationRuleCategory, level.value.upper()),
                severity=ValidationSeverity.ERROR,
                message=f"{level.value.capitalize()} validation failed: {str(e)}"
            ))
        
        return errors
    
    def _add_validation_results(
        self,
        result: ValidationResult,
        errors: List[ValidationError]
    ) -> None:
        """Ajoute les résultats de validation au résultat global"""
        for error in errors:
            if error.severity == ValidationSeverity.ERROR:
                result.add_error(error)
            elif error.severity == ValidationSeverity.WARNING:
                result.add_warning(error)
            elif error.severity == ValidationSeverity.INFO:
                result.add_info(error)
    
    def _collect_validated_elements(self, ast_tree: ASTNode) -> Set[str]:
        """Collecte les éléments validés"""
        elements = set()
        stack = [ast_tree]
        
        while stack:
            node = stack.pop()
            
            # Ajout des noms d'éléments
            if hasattr(node, 'name') and node.name:
                elements.add(node.name)
            
            if hasattr(node, 'ref_name') and node.ref_name:
                elements.add(node.ref_name)
            
            if hasattr(node, 'children'):
                stack.extend(node.children)
        
        return elements
    
    def validate_json(self, json_data: Dict[str, Any], schema_type: str) -> ValidationResult:
        """
        Valide des données JSON contre un schéma.
        
        Args:
            json_data: Données JSON à valider
            schema_type: Type de schéma à utiliser
            
        Returns:
            Résultat de validation
        """
        import time
        start_time = time.time()
        
        result = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=[],
            infos=[],
            validation_time_ms=0,
            validated_elements=set()
        )
        
        try:
            errors = self.syntax_validator.validate_json_structure(json_data, schema_type)
            self._add_validation_results(result, errors)
            
            result.validation_time_ms = (time.time() - start_time) * 1000
            
        except Exception as e:
            logger.error(f"JSON validation error: {str(e)}")
            result.add_error(ValidationError(
                rule_id="JSON_VALIDATOR_001",
                category=ValidationRuleCategory.SYNTAX,
                severity=ValidationSeverity.ERROR,
                message=f"JSON validation failed: {str(e)}"
            ))
        
        return result
    
    def get_validation_rules(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Récupère toutes les règles de validation.
        
        Returns:
            Règles de validation par catégorie
        """
        rules = {}
        
        # Collecte des règles de tous les validateurs
        validators = [
            ('syntax', self.syntax_validator),
            ('semantic', self.semantic_validator),
            ('type', self.type_validator),
            ('security', self.security_validator),
            ('performance', self.performance_validator),
            ('cost', self.cost_validator),
            ('compliance', self.compliance_validator),
            ('best_practice', self.best_practices_validator)
        ]
        
        for category, validator in validators:
            rules[category] = self._extract_validator_rules(validator)
        
        return rules
    
    def _extract_validator_rules(self, validator: Any) -> List[Dict[str, Any]]:
        """Extrait les règles d'un validateur"""
        rules = []
        
        # Cette méthode devrait extraire les règles de chaque validateur
        # Pour l'instant, retourne une structure vide
        
        return rules
    
    def generate_validation_report(
        self,
        validation_result: ValidationResult,
        format: str = 'json'
    ) -> Union[str, Dict[str, Any]]:
        """
        Génère un rapport de validation.
        
        Args:
            validation_result: Résultat de validation
            format: Format du rapport (json, html, text)
            
        Returns:
            Rapport de validation
        """
        if format == 'json':
            return validation_result.to_dict()
        
        elif format == 'text':
            return self._generate_text_report(validation_result)
        
        elif format == 'html':
            return self._generate_html_report(validation_result)
        
        else:
            raise ValueError(f"Unsupported report format: {format}")
    
    def _generate_text_report(self, result: ValidationResult) -> str:
        """Génère un rapport texte"""
        report = []
        report.append("=" * 80)
        report.append("DSL VALIDATION REPORT")
        report.append("=" * 80)
        report.append(f"\nOverall Status: {'PASS' if result.is_valid else 'FAIL'}")
        report.append(f"Errors: {len(result.errors)}")
        report.append(f"Warnings: {len(result.warnings)}")
        report.append(f"Infos: {len(result.infos)}")
        report.append(f"Validation Time: {result.validation_time_ms:.2f} ms")
        report.append(f"Validated Elements: {len(result.validated_elements)}")
        
        if result.errors:
            report.append("\n" + "=" * 80)
            report.append("ERRORS")
            report.append("=" * 80)
            for error in result.errors:
                report.append(f"\n[{error.rule_id}] {error.message}")
                if error.line:
                    report.append(f"  Location: line {error.line}, column {error.column}")
                if error.suggestion:
                    report.append(f"  Suggestion: {error.suggestion}")
        
        if result.warnings:
            report.append("\n" + "=" * 80)
            report.append("WARNINGS")
            report.append("=" * 80)
            for warning in result.warnings:
                report.append(f"\n[{warning.rule_id}] {warning.message}")
                if warning.line:
                    report.append(f"  Location: line {warning.line}, column {warning.column}")
                if warning.suggestion:
                    report.append(f"  Suggestion: {warning.suggestion}")
        
        return "\n".join(report)
    
    def _generate_html_report(self, result: ValidationResult) -> str:
        """Génère un rapport HTML"""
        # Implémentation simplifiée
        html = f"""
        <html>
        <head>
            <title>DSL Validation Report</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .header {{ background: #f0f0f0; padding: 20px; border-radius: 5px; }}
                .status-pass {{ color: green; font-weight: bold; }}
                .status-fail {{ color: red; font-weight: bold; }}
                .error {{ color: red; }}
                .warning {{ color: orange; }}
                .info {{ color: blue; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #f2f2f2; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>DSL Validation Report</h1>
                <p>Overall Status: 
                    <span class="{'status-pass' if result.is_valid else 'status-fail'}">
                        {'PASS' if result.is_valid else 'FAIL'}
                    </span>
                </p>
                <p>Errors: {len(result.errors)} | Warnings: {len(result.warnings)} | Infos: {len(result.infos)}</p>
                <p>Validation Time: {result.validation_time_ms:.2f} ms</p>
            </div>
        </body>
        </html>
        """
        
        return html


# Exemple d'utilisation
if __name__ == "__main__":
    # Configuration du validateur
    validator = DSLValidator(
        security_config={
            'require_authentication': True,
            'max_security_level': 5
        },
        performance_config={
            'max_response_time_ms': 1000,
            'max_memory_mb': 512
        },
        cost_config={
            'max_monthly_cost_usd': 1000,
            'roi_threshold': 3.0
        }
    )
    
    # Exemple de code DSL
    sample_dsl = """
    agent security:threat_detector:
      version: "1.0.0"
      description: "Detects security threats in real-time"
      
      inputs:
        log_data:
          type: object
          required: true
          description: "Security logs to analyze"
        
        config:
          type: object
          default: {}
          description: "Detection configuration"
      
      outputs:
        threats:
          type: array
          description: "Detected threats"
        
        analysis:
          type: object
          description: "Analysis results"
      
      requires:
        - monitoring:log_analyzer
        - intelligence:threat_feed
    """
    
    # Validation du code DSL
    print("Validating DSL code...")
    result = validator.validate(sample_dsl)
    
    # Génération du rapport
    report = validator.generate_validation_report(result, format='text')
    print(report)
    
    # Affichage des règles disponibles
    rules = validator.get_validation_rules()
    print(f"\nAvailable validation rules: {sum(len(r) for r in rules.values())} rules across {len(rules)} categories")