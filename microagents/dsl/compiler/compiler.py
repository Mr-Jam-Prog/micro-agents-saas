"""
MicroAgents DSL Compiler
Compile le DSL en code Python, configurations, tests et documentation
"""

from __future__ import annotations

import ast as python_ast
import inspect
import json
import re
import sys
import time
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Union, Set
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from collections import defaultdict

import jinja2
from pydantic import BaseModel, ValidationError

from microagents.dsl.language.ast import (
    ASTNode, AgentDefinition, Capability, InputDefinition,
    OutputDefinition, RuleDefinition, BusinessValueDefinition,
    Dependency, Configuration, TestSpec, ComplianceRequirement,
    DocumentationBlock, ImportStatement, VersionConstraint,
    DSLSyntaxError, Position
)
from microagents.dsl.parser.parser import DSLParser, ParseError
from microagents.dsl.transformations.code_generator import CodeGenerator
from microagents.dsl.transformations.optimizer import DSLOptimizer
from microagents.dsl.validator.validator import DSLValidator
from microagents.utils.serialization.serializers import JSONSerializer, YAMLSerializer


class CompilationTarget(Enum):
    """Cibles de compilation supportées"""
    PYTHON_CLASS = "python_class"
    OPENAPI_SPEC = "openapi_spec"
    CONFIGURATION = "configuration"
    DOCUMENTATION = "documentation"
    TEST_SUITE = "test_suite"
    DEPLOYMENT_MANIFEST = "deployment_manifest"
    ALL = "all"


class OptimizationLevel(Enum):
    """Niveaux d'optimisation"""
    NONE = "none"
    BASIC = "basic"      # Élimination code mort, constant folding
    ADVANCED = "advanced"  # Inlining, loop optimization
    AGGRESSIVE = "aggressive"  # Toutes optimisations + profilage


@dataclass
class CompilationResult:
    """Résultat d'une compilation"""
    success: bool
    outputs: Dict[str, str]  # chemin -> contenu
    warnings: List[str]
    errors: List[str]
    metrics: Dict[str, Any]
    generated_files: List[Path]
    compilation_time: float


@dataclass
class TypeInfo:
    """Information de type pour le type checking"""
    name: str
    base_type: str
    constraints: Dict[str, Any] = field(default_factory=dict)
    nullable: bool = False
    default_value: Optional[Any] = None


class DSLCompiler:
    """
    Compilateur DSL pour la transformation en code exécutable
    
    Supporte:
    - Génération de code Python à partir d'AST
    - Optimisations multiples
    - Vérification de types
    - Génération de tests et documentation
    """
    
    def __init__(
        self,
        template_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        optimization_level: OptimizationLevel = OptimizationLevel.BASIC,
        enable_security_scan: bool = True,
        enable_profiling: bool = False
    ):
        """
        Initialise le compilateur DSL
        
        Args:
            template_dir: Répertoire des templates Jinja2
            output_dir: Répertoire de sortie
            optimization_level: Niveau d'optimisation
            enable_security_scan: Active le scan de sécurité
            enable_profiling: Active les hints de profilage
        """
        # Configuration
        self.optimization_level = optimization_level
        self.enable_security_scan = enable_security_scan
        self.enable_profiling = enable_profiling
        
        # Répertoires
        self.template_dir = template_dir or Path(__file__).parent.parent / "templates"
        self.output_dir = output_dir or Path.cwd() / "generated"
        
        # Initialisation des composants
        self.jinja_env = self._init_jinja_environment()
        self.parser = DSLParser()
        self.optimizer = DSLOptimizer(optimization_level)
        self.validator = DSLValidator()
        self.code_generator = CodeGenerator()
        
        # État de compilation
        self.type_registry: Dict[str, TypeInfo] = {}
        self.dependency_graph: Dict[str, List[str]] = {}
        self.compilation_cache: Dict[str, Any] = {}
        
        # Métriques
        self.compilation_metrics: Dict[str, Any] = defaultdict(int)
        
    def _init_jinja_environment(self) -> jinja2.Environment:
        """Initialise l'environnement Jinja2 avec les templates"""
        loader = jinja2.FileSystemLoader([
            self.template_dir,
            Path(__file__).parent.parent / "generator" / "templates"
        ])
        
        env = jinja2.Environment(
            loader=loader,
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            extensions=['jinja2.ext.do']
        )
        
        # Filtres personnalisés
        env.filters['snake_case'] = self._to_snake_case
        env.filters['camel_case'] = self._to_camel_case
        env.filters['type_hint'] = self._python_type_hint
        env.filters['default_value'] = self._format_default_value
        
        # Globals
        env.globals['now'] = datetime.now
        env.globals['compiler_version'] = "1.0.0"
        
        return env
    
    def compile(
        self,
        source: Union[str, Path],
        targets: List[CompilationTarget] = None,
        output_dir: Optional[Path] = None
    ) -> CompilationResult:
        """
        Compile le code DSL source
        
        Args:
            source: Code DSL ou chemin de fichier
            targets: Cibles de compilation
            output_dir: Répertoire de sortie (override)
            
        Returns:
            CompilationResult: Résultat de la compilation
        """
        start_time = time.time()
        
        # Configuration
        if targets is None:
            targets = [CompilationTarget.PYTHON_CLASS]
        
        if output_dir:
            self.output_dir = output_dir
        
        # Crée le répertoire de sortie
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Parse le DSL
        try:
            if isinstance(source, Path):
                with open(source, 'r', encoding='utf-8') as f:
                    dsl_source = f.read()
                source_path = source
            else:
                dsl_source = source
                source_path = None
                
            ast = self.parser.parse(dsl_source, source_path)
            
        except ParseError as e:
            return CompilationResult(
                success=False,
                outputs={},
                warnings=[],
                errors=[str(e)],
                metrics={},
                generated_files=[],
                compilation_time=time.time() - start_time
            )
        
        # Valide l'AST
        validation_result = self.validator.validate(ast)
        if not validation_result.success:
            return CompilationResult(
                success=False,
                outputs={},
                warnings=validation_result.warnings,
                errors=validation_result.errors,
                metrics={},
                generated_files=[],
                compilation_time=time.time() - start_time
            )
        
        # Optimise l'AST
        if self.optimization_level != OptimizationLevel.NONE:
            ast = self.optimizer.optimize(ast)
        
        # Résout les dépendances
        self._resolve_dependencies(ast)
        
        # Type checking et inference
        type_errors = self._perform_type_checking(ast)
        if type_errors:
            return CompilationResult(
                success=False,
                outputs={},
                warnings=[],
                errors=type_errors,
                metrics={},
                generated_files=[],
                compilation_time=time.time() - start_time
            )
        
        # Génération des sorties
        outputs = {}
        generated_files = []
        
        try:
            for target in targets:
                target_outputs = self._compile_target(ast, target)
                outputs.update(target_outputs)
                
                for file_path, content in target_outputs.items():
                    output_path = self.output_dir / file_path
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    
                    generated_files.append(output_path)
                    
                    # Scan de sécurité si activé
                    if self.enable_security_scan:
                        self._security_scan_content(content, output_path)
        
        except Exception as e:
            return CompilationResult(
                success=False,
                outputs={},
                warnings=[],
                errors=[f"Compilation error: {str(e)}"],
                metrics=self.compilation_metrics,
                generated_files=generated_files,
                compilation_time=time.time() - start_time
            )
        
        # Métriques finales
        compilation_time = time.time() - start_time
        self.compilation_metrics.update({
            'total_time': compilation_time,
            'files_generated': len(generated_files),
            'agents_compiled': len([n for n in ast.children if isinstance(n, AgentDefinition)]),
            'optimizations_applied': self.optimizer.metrics
        })
        
        return CompilationResult(
            success=True,
            outputs=outputs,
            warnings=validation_result.warnings,
            errors=[],
            metrics=self.compilation_metrics,
            generated_files=generated_files,
            compilation_time=compilation_time
        )
    
    def _compile_target(self, ast: ASTNode, target: CompilationTarget) -> Dict[str, str]:
        """Compile pour une cible spécifique"""
        outputs = {}
        
        if target == CompilationTarget.PYTHON_CLASS:
            outputs.update(self._generate_python_classes(ast))
        
        elif target == CompilationTarget.OPENAPI_SPEC:
            outputs.update(self._generate_openapi_spec(ast))
        
        elif target == CompilationTarget.CONFIGURATION:
            outputs.update(self._generate_configuration(ast))
        
        elif target == CompilationTarget.DOCUMENTATION:
            outputs.update(self._generate_documentation(ast))
        
        elif target == CompilationTarget.TEST_SUITE:
            outputs.update(self._generate_test_suite(ast))
        
        elif target == CompilationTarget.DEPLOYMENT_MANIFEST:
            outputs.update(self._generate_deployment_manifest(ast))
        
        elif target == CompilationTarget.ALL:
            # Génère tout
            all_targets = [
                CompilationTarget.PYTHON_CLASS,
                CompilationTarget.OPENAPI_SPEC,
                CompilationTarget.CONFIGURATION,
                CompilationTarget.DOCUMENTATION,
                CompilationTarget.TEST_SUITE,
                CompilationTarget.DEPLOYMENT_MANIFEST
            ]
            for t in all_targets:
                outputs.update(self._compile_target(ast, t))
        
        return outputs
    
    def _generate_python_classes(self, ast: ASTNode) -> Dict[str, str]:
        """Génère des classes Python à partir des définitions d'agents"""
        outputs = {}
        
        # Template pour les classes d'agents
        template = self.jinja_env.get_template('python_agent.jinja')
        
        for node in ast.children:
            if isinstance(node, AgentDefinition):
                # Prépare le contexte pour le template
                context = self._prepare_agent_context(node)
                
                # Ajoute des hints de profilage si activé
                if self.enable_profiling:
                    context['profiling_hints'] = self._generate_profiling_hints(node)
                
                # Génère le code
                python_code = template.render(**context)
                
                # Optimisations supplémentaires
                python_code = self._apply_python_optimizations(python_code, node)
                
                # Valide la syntaxe Python
                self._validate_python_syntax(python_code, node.name)
                
                # Nom du fichier
                filename = f"{self._to_snake_case(node.name)}.py"
                outputs[filename] = python_code
                
                self.compilation_metrics['python_classes_generated'] += 1
        
        return outputs
    
    def _prepare_agent_context(self, agent: AgentDefinition) -> Dict[str, Any]:
        """Prépare le contexte pour les templates"""
        # Conversion des types DSL -> Python
        typed_inputs = []
        for inp in agent.inputs:
            python_type = self._dsl_type_to_python(inp.data_type)
            typed_inputs.append({
                'name': inp.name,
                'type': python_type,
                'default': inp.default_value,
                'description': inp.description
            })
        
        typed_outputs = []
        for out in agent.outputs:
            python_type = self._dsl_type_to_python(out.data_type)
            typed_outputs.append({
                'name': out.name,
                'type': python_type,
                'description': out.description
            })
        
        # Compilation des règles en logique Python
        compiled_rules = []
        for rule in agent.rules:
            python_rule = self._compile_rule_to_python(rule, agent)
            compiled_rules.append(python_rule)
        
        # Business value calculator
        business_value_calculator = None
        if agent.business_value:
            business_value_calculator = {
                'name': agent.business_value.calculator,
                'parameters': agent.business_value.parameters
            }
        
        return {
            'agent': agent,
            'agent_name': agent.name,
            'agent_type': agent.agent_type,
            'capabilities': [c.name for c in agent.capabilities],
            'inputs': typed_inputs,
            'outputs': typed_outputs,
            'rules': compiled_rules,
            'business_value': business_value_calculator,
            'dependencies': agent.dependencies,
            'configuration': agent.configuration.settings if agent.configuration else {},
            'compliance': agent.compliance,
            'tests': agent.tests,
            'documentation': agent.documentation.content if agent.documentation else "",
            'generation_time': datetime.now().isoformat(),
            'enable_profiling': self.enable_profiling,
        }
    
    def _dsl_type_to_python(self, dsl_type: str) -> str:
        """Convertit un type DSL en annotation de type Python"""
        type_mapping = {
            'string': 'str',
            'int': 'int',
            'float': 'float',
            'bool': 'bool',
            'datetime': 'datetime',
            'duration': 'timedelta',
            'currency': 'Decimal',
            'percentage': 'float',
            'ratio': 'float',
            'score': 'float',
            'any': 'Any',
            'list': 'List',
            'dict': 'Dict',
            'set': 'Set',
            'tuple': 'Tuple',
            'object': 'Dict[str, Any]',
        }
        
        # Types de liste
        if dsl_type.startswith('list['):
            inner_type = dsl_type[5:-1]
            python_inner = self._dsl_type_to_python(inner_type)
            return f"List[{python_inner}]"
        
        # Types de dictionnaire
        elif dsl_type.startswith('dict['):
            # dict[key_type, value_type]
            inner_types = dsl_type[5:-1].split(',')
            if len(inner_types) == 2:
                key_type, value_type = inner_types
                python_key = self._dsl_type_to_python(key_type.strip())
                python_value = self._dsl_type_to_python(value_type.strip())
                return f"Dict[{python_key}, {python_value}]"
        
        # Type de base
        return type_mapping.get(dsl_type, 'Any')
    
    def _compile_rule_to_python(self, rule: RuleDefinition, agent: AgentDefinition) -> Dict[str, Any]:
        """Compile une règle DSL en logique Python"""
        # Parse la condition
        condition_python = self._compile_condition_to_python(rule.condition, agent)
        
        # Parse l'action
        action_python = self._compile_action_to_python(rule.action, agent)
        
        # Génère le code Python
        rule_code = f"""
    def {self._to_snake_case(rule.name)}(self, context):
        \"\"\"{rule.condition} -> {rule.action}\"\"\"
        # Condition: {rule.condition}
        if {condition_python}:
            # Action: {rule.action}
            {action_python}
            return True
        return False
"""
        
        # Optimise la règle si nécessaire
        if self.optimization_level in [OptimizationLevel.ADVANCED, OptimizationLevel.AGGRESSIVE]:
            rule_code = self._optimize_rule_code(rule_code, agent)
        
        return {
            'name': rule.name,
            'condition': rule.condition,
            'action': rule.action,
            'python_code': rule_code.strip(),
            'compiled_condition': condition_python,
            'compiled_action': action_python
        }
    
    def _compile_condition_to_python(self, condition: str, agent: AgentDefinition) -> str:
        """Compile une condition DSL en expression Python"""
        # Mapping des opérateurs
        operator_mapping = {
            '>': '>',
            '<': '<',
            '>=': '>=',
            '<=': '<=',
            '==': '==',
            '!=': '!=',
            'and': 'and',
            'or': 'or',
            'not': 'not',
            'in': 'in',
        }
        
        # Remplace les noms d'inputs par self.context['input_name']
        python_condition = condition
        
        # Extrait les variables
        import re
        variable_pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b'
        variables = re.findall(variable_pattern, condition)
        
        for var in variables:
            # Vérifie si c'est un input de l'agent
            if any(inp.name == var for inp in agent.inputs):
                python_condition = python_condition.replace(
                    var, f"self.context.get('{var}')"
                )
        
        # Remplace les opérateurs DSL par Python
        for dsl_op, py_op in operator_mapping.items():
            pattern = r'\b' + re.escape(dsl_op) + r'\b'
            python_condition = re.sub(pattern, py_op, python_condition)
        
        return python_condition.strip()
    
    def _compile_action_to_python(self, action: str, agent: AgentDefinition) -> str:
        """Compile une action DSL en code Python"""
        # Détecte le type d'action
        if action.startswith('alert('):
            return self._compile_alert_action(action, agent)
        elif action.startswith('validate('):
            return self._compile_validate_action(action, agent)
        elif action.startswith('recommend('):
            return self._compile_recommend_action(action, agent)
        elif action.startswith('schedule('):
            return self._compile_schedule_action(action, agent)
        elif action.startswith('retry('):
            return self._compile_retry_action(action, agent)
        else:
            # Action générique
            return f"self._execute_action('{action}')"
    
    def _compile_alert_action(self, action: str, agent: AgentDefinition) -> str:
        """Compile une action d'alerte"""
        # Extrait les paramètres: alert(severity: high, message: "Cost anomaly")
        params_match = re.match(r'alert\((.*)\)', action)
        if params_match:
            params_str = params_match.group(1)
            params = self._parse_parameters(params_str)
            
            severity = params.get('severity', 'medium')
            message = params.get('message', 'Alert triggered')
            
            return f"self.trigger_alert(severity='{severity}', message='{message}')"
        
        return "self.trigger_alert()"
    
    def _parse_parameters(self, params_str: str) -> Dict[str, Any]:
        """Parse une chaîne de paramètres key: value"""
        params = {}
        
        # Split par virgule, mais attention aux strings avec virgules
        parts = []
        current = ""
        in_string = False
        
        for char in params_str:
            if char == '"' or char == "'":
                in_string = not in_string
            elif char == ',' and not in_string:
                parts.append(current.strip())
                current = ""
                continue
            current += char
        
        if current:
            parts.append(current.strip())
        
        # Parse chaque paramètre
        for part in parts:
            if ':' in part:
                key, value = part.split(':', 1)
                key = key.strip()
                value = value.strip()
                
                # Nettoie les strings
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                
                params[key] = value
        
        return params
    
    def _optimize_rule_code(self, rule_code: str, agent: AgentDefinition) -> str:
        """Optimise le code généré pour une règle"""
        # Constant folding simple
        rule_code = re.sub(
            r'if True:',
            '# Condition toujours vraie (optimisé)',
            rule_code
        )
        
        rule_code = re.sub(
            r'if False:',
            '# Condition toujours fausse (éliminée)',
            rule_code
        )
        
        # Dead code elimination pour conditions triviales
        if 'if True:' in rule_code:
            # Garde seulement l'action
            lines = rule_code.split('\n')
            optimized_lines = []
            in_action = False
            
            for line in lines:
                if 'if True:' in line:
                    optimized_lines.append('    # Condition optimisée (toujours vraie)')
                    in_action = True
                elif in_action and line.strip() and not line.strip().startswith('#'):
                    optimized_lines.append(line)
                else:
                    optimized_lines.append(line)
            
            rule_code = '\n'.join(optimized_lines)
        
        return rule_code
    
    def _apply_python_optimizations(self, python_code: str, agent: AgentDefinition) -> str:
        """Applique des optimisations au code Python généré"""
        if self.optimization_level == OptimizationLevel.NONE:
            return python_code
        
        # Parse l'AST Python
        try:
            tree = python_ast.parse(python_code)
            
            # Optimisations de base
            if self.optimization_level in [OptimizationLevel.BASIC, OptimizationLevel.ADVANCED, OptimizationLevel.AGGRESSIVE]:
                tree = self._constant_folding(tree)
                tree = self._dead_code_elimination(tree)
            
            # Optimisations avancées
            if self.optimization_level in [OptimizationLevel.ADVANCED, OptimizationLevel.AGGRESSIVE]:
                tree = self._function_inlining(tree, agent)
                tree = self._loop_optimization(tree)
            
            # Optimisations agressives
            if self.optimization_level == OptimizationLevel.AGGRESSIVE:
                tree = self._aggressive_optimizations(tree)
            
            # Régénère le code
            optimized_code = python_ast.unparse(tree)
            
            return optimized_code
            
        except Exception as e:
            # Si l'optimisation échoue, retourne le code original
            warnings.warn(f"Python optimization failed: {e}")
            return python_code
    
    def _constant_folding(self, tree: python_ast.AST) -> python_ast.AST:
        """Constant folding: évalue les expressions constantes"""
        class ConstantFolder(python_ast.NodeTransformer):
            def visit_BinOp(self, node):
                self.generic_visit(node)
                
                # Tente d'évaluer les opérations binaires avec constantes
                if (isinstance(node.left, python_ast.Constant) and 
                    isinstance(node.right, python_ast.Constant)):
                    try:
                        # Évalue l'expression
                        left_val = node.left.value
                        right_val = node.right.value
                        
                        # Opérations supportées
                        if isinstance(node.op, python_ast.Add):
                            result = left_val + right_val
                        elif isinstance(node.op, python_ast.Sub):
                            result = left_val - right_val
                        elif isinstance(node.op, python_ast.Mult):
                            result = left_val * right_val
                        elif isinstance(node.op, python_ast.Div):
                            if right_val != 0:
                                result = left_val / right_val
                            else:
                                return node
                        elif isinstance(node.op, python_ast.FloorDiv):
                            if right_val != 0:
                                result = left_val // right_val
                            else:
                                return node
                        else:
                            return node
                        
                        return python_ast.Constant(value=result)
                    except:
                        pass
                
                return node
        
        folder = ConstantFolder()
        return folder.visit(tree)
    
    def _dead_code_elimination(self, tree: python_ast.AST) -> python_ast.AST:
        """Élimine le code mort"""
        class DeadCodeEliminator(python_ast.NodeTransformer):
            def visit_If(self, node):
                self.generic_visit(node)
                
                # Vérifie si la condition est une constante
                if isinstance(node.test, python_ast.Constant):
                    if node.test.value:  # if True:
                        # Remplace par le corps du if
                        return node.body
                    else:  # if False:
                        # Remplace par le corps du else s'il existe
                        return node.orelse or []
                
                return node
        
        eliminator = DeadCodeEliminator()
        return eliminator.visit(tree)
    
    def _function_inlining(self, tree: python_ast.AST, agent: AgentDefinition) -> python_ast.AST:
        """Inline les petites fonctions"""
        # Pour l'instant, simple passe qui identifie les petites fonctions
        # Une implémentation complète serait plus complexe
        return tree
    
    def _loop_optimization(self, tree: python_ast.AST) -> python_ast.AST:
        """Optimise les boucles"""
        # Pour l'instant, placeholder
        return tree
    
    def _aggressive_optimizations(self, tree: python_ast.AST) -> python_ast.AST:
        """Optimisations agressives"""
        # Pour l'instant, placeholder
        return tree
    
    def _validate_python_syntax(self, python_code: str, agent_name: str):
        """Valide la syntaxe Python du code généré"""
        try:
            python_ast.parse(python_code)
        except SyntaxError as e:
            raise ValueError(f"Invalid Python syntax in generated code for {agent_name}: {e}")
    
    def _generate_openapi_spec(self, ast: ASTNode) -> Dict[str, str]:
        """Génère une spécification OpenAPI"""
        outputs = {}
        
        template = self.jinja_env.get_template('openapi_spec.jinja')
        
        # Collecte toutes les APIs des agents
        endpoints = []
        for node in ast.children:
            if isinstance(node, AgentDefinition):
                endpoint = {
                    'name': node.name,
                    'description': node.documentation.content if node.documentation else "",
                    'inputs': [
                        {
                            'name': inp.name,
                            'type': self._dsl_type_to_openapi(inp.data_type),
                            'required': inp.default_value is None
                        }
                        for inp in node.inputs
                    ],
                    'outputs': [
                        {
                            'name': out.name,
                            'type': self._dsl_type_to_openapi(out.data_type)
                        }
                        for out in node.outputs
                    ]
                }
                endpoints.append(endpoint)
        
        context = {
            'endpoints': endpoints,
            'title': 'MicroAgents API',
            'version': '1.0.0',
            'description': 'Auto-generated OpenAPI specification',
            'generation_time': datetime.now().isoformat()
        }
        
        openapi_spec = template.render(**context)
        
        outputs['openapi.yaml'] = openapi_spec
        self.compilation_metrics['openapi_specs_generated'] += 1
        
        return outputs
    
    def _dsl_type_to_openapi(self, dsl_type: str) -> Dict[str, Any]:
        """Convertit un type DSL en schéma OpenAPI"""
        type_mapping = {
            'string': {'type': 'string'},
            'int': {'type': 'integer', 'format': 'int64'},
            'float': {'type': 'number', 'format': 'float'},
            'bool': {'type': 'boolean'},
            'datetime': {'type': 'string', 'format': 'date-time'},
            'duration': {'type': 'string', 'format': 'duration'},
            'currency': {'type': 'number', 'format': 'decimal'},
            'percentage': {'type': 'number', 'format': 'float', 'minimum': 0, 'maximum': 100},
            'ratio': {'type': 'number', 'format': 'float', 'minimum': 0},
            'score': {'type': 'number', 'format': 'float', 'minimum': 0, 'maximum': 1},
            'any': {'type': 'object'}
        }
        
        # Types de liste
        if dsl_type.startswith('list['):
            inner_type = dsl_type[5:-1]
            inner_schema = self._dsl_type_to_openapi(inner_type)
            return {'type': 'array', 'items': inner_schema}
        
        # Types de dictionnaire
        elif dsl_type.startswith('dict['):
            return {'type': 'object', 'additionalProperties': {'type': 'object'}}
        
        # Type de base
        return type_mapping.get(dsl_type, {'type': 'object'})
    
    def _generate_configuration(self, ast: ASTNode) -> Dict[str, str]:
        """Génère les fichiers de configuration"""
        outputs = {}
        
        template = self.jinja_env.get_template('yaml_agent.jinja')
        
        for node in ast.children:
            if isinstance(node, AgentDefinition):
                context = {
                    'agent': node,
                    'config': node.configuration.settings if node.configuration else {},
                    'compliance': node.compliance,
                    'generation_time': datetime.now().isoformat()
                }
                
                config_yaml = template.render(**context)
                
                filename = f"config/{self._to_snake_case(node.name)}.yaml"
                outputs[filename] = config_yaml
                
                self.compilation_metrics['config_files_generated'] += 1
        
        return outputs
    
    def _generate_documentation(self, ast: ASTNode) -> Dict[str, str]:
        """Génère la documentation"""
        outputs = {}
        
        template = self.jinja_env.get_template('documentation.jinja')
        
        agents = []
        for node in ast.children:
            if isinstance(node, AgentDefinition):
                agents.append(node)
        
        context = {
            'agents': agents,
            'generation_time': datetime.now().isoformat(),
            'total_agents': len(agents)
        }
        
        documentation = template.render(**context)
        
        outputs['DOCUMENTATION.md'] = documentation
        self.compilation_metrics['docs_generated'] += 1
        
        return outputs
    
    def _generate_test_suite(self, ast: ASTNode) -> Dict[str, str]:
        """Génère une suite de tests"""
        outputs = {}
        
        template = self.jinja_env.get_template('test_suite.jinja')
        
        test_cases = []
        for node in ast.children:
            if isinstance(node, AgentDefinition):
                if node.tests:
                    for test in node.tests:
                        test_case = {
                            'agent_name': node.name,
                            'test_name': test.name,
                            'inputs': test.inputs,
                            'expected_outputs': test.expected_outputs
                        }
                        test_cases.append(test_case)
        
        context = {
            'test_cases': test_cases,
            'generation_time': datetime.now().isoformat(),
            'total_tests': len(test_cases)
        }
        
        test_suite = template.render(**context)
        
        outputs['test_agents.py'] = test_suite
        self.compilation_metrics['tests_generated'] += 1
        
        return outputs
    
    def _generate_deployment_manifest(self, ast: ASTNode) -> Dict[str, str]:
        """Génère les manifests de déploiement"""
        outputs = {}
        
        template = self.jinja_env.get_template('deployment.jinja')
        
        for node in ast.children:
            if isinstance(node, AgentDefinition):
                # Détermine les ressources nécessaires
                resources = self._estimate_resources(node)
                
                context = {
                    'agent': node,
                    'resources': resources,
                    'agent_type': node.agent_type,
                    'generation_time': datetime.now().isoformat()
                }
                
                deployment_yaml = template.render(**context)
                
                filename = f"deployment/{self._to_snake_case(node.name)}.yaml"
                outputs[filename] = deployment_yaml
                
                self.compilation_metrics['deployment_manifests_generated'] += 1
        
        return outputs
    
    def _estimate_resources(self, agent: AgentDefinition) -> Dict[str, Any]:
        """Estime les ressources nécessaires pour un agent"""
        # Estimation basée sur le type d'agent et les capacités
        base_resources = {
            'cpu': '100m',
            'memory': '128Mi',
            'replicas': 1
        }
        
        # Ajuste en fonction des capacités
        capabilities = [c.name for c in agent.capabilities]
        
        if 'cost_analysis' in capabilities:
            base_resources['cpu'] = '200m'
            base_resources['memory'] = '256Mi'
        
        if 'anomaly_detection' in capabilities:
            base_resources['cpu'] = '300m'
            base_resources['memory'] = '512Mi'
        
        if 'security_scan' in capabilities:
            base_resources['cpu'] = '500m'
            base_resources['memory'] = '1Gi'
        
        # Ajuste en fonction des inputs/outputs
        total_io = len(agent.inputs) + len(agent.outputs)
        if total_io > 10:
            base_resources['memory'] = '2Gi'
        
        return base_resources
    
    def _resolve_dependencies(self, ast: ASTNode):
        """Résout les dépendances entre agents"""
        # Pour l'instant, simple collecte
        # Une implémentation complète résoudrait le graphe de dépendances
        for node in ast.children:
            if isinstance(node, AgentDefinition):
                self.dependency_graph[node.name] = [
                    dep.name for dep in node.dependencies
                ]
    
    def _perform_type_checking(self, ast: ASTNode) -> List[str]:
        """Effectue le type checking et l'inférence de types"""
        errors = []
        
        for node in ast.children:
            if isinstance(node, AgentDefinition):
                # Vérifie les types des inputs
                for inp in node.inputs:
                    if not self._is_valid_type(inp.data_type):
                        errors.append(
                            f"Invalid type '{inp.data_type}' for input '{inp.name}' "
                            f"in agent '{node.name}'"
                        )
                
                # Vérifie les types des outputs
                for out in node.outputs:
                    if not self._is_valid_type(out.data_type):
                        errors.append(
                            f"Invalid type '{out.data_type}' for output '{out.name}' "
                            f"in agent '{node.name}'"
                        )
                
                # Inférence de types pour les règles
                for rule in node.rules:
                    type_errors = self._check_rule_types(rule, node)
                    errors.extend(type_errors)
        
        return errors
    
    def _is_valid_type(self, type_str: str) -> bool:
        """Vérifie si un type est valide"""
        valid_types = {
            'string', 'int', 'float', 'bool', 'datetime', 'duration',
            'currency', 'percentage', 'ratio', 'score', 'any',
            'list', 'dict', 'set', 'tuple', 'object'
        }
        
        # Types de liste
        if type_str.startswith('list[') and type_str.endswith(']'):
            inner_type = type_str[5:-1]
            return self._is_valid_type(inner_type)
        
        # Types de dictionnaire
        elif type_str.startswith('dict[') and ']' in type_str:
            inner_types = type_str[5:-1].split(',')
            if len(inner_types) == 2:
                key_type, value_type = inner_types
                return (self._is_valid_type(key_type.strip()) and 
                        self._is_valid_type(value_type.strip()))
        
        return type_str in valid_types
    
    def _check_rule_types(self, rule: RuleDefinition, agent: AgentDefinition) -> List[str]:
        """Vérifie les types dans une règle"""
        errors = []
        
        # Pour l'instant, vérifications basiques
        # Une implémentation complète analyserait l'expression
        return errors
    
    def _generate_profiling_hints(self, agent: AgentDefinition) -> Dict[str, Any]:
        """Génère des hints de profilage pour l'agent"""
        hints = {
            'expensive_operations': [],
            'memory_intensive': False,
            'io_intensive': False,
            'cpu_intensive': False,
            'suggested_optimizations': []
        }
        
        # Analyse basée sur les capacités
        capabilities = [c.name for c in agent.capabilities]
        
        if 'anomaly_detection' in capabilities:
            hints['cpu_intensive'] = True
            hints['suggested_optimizations'].append(
                "Consider caching anomaly detection models"
            )
        
        if 'cost_analysis' in capabilities:
            hints['memory_intensive'] = True
            hints['suggested_optimizations'].append(
                "Use streaming processing for large datasets"
            )
        
        # Analyse basée sur les règles
        for rule in agent.rules:
            if '>' in rule.condition or '<' in rule.condition:
                hints['expensive_operations'].append(f"Rule '{rule.name}': comparison operations")
        
        return hints
    
    def _security_scan_content(self, content: str, file_path: Path):
        """Effectue un scan de sécurité basique sur le contenu généré"""
        if not self.enable_security_scan:
            return
        
        security_patterns = {
            'hardcoded_password': r'(?i)password\s*[:=]\s*["\'][^"\']+["\']',
            'hardcoded_secret': r'(?i)(secret|token|key)\s*[:=]\s*["\'][^"\']+["\']',
            'dangerous_eval': r'eval\s*\(',
            'dangerous_exec': r'exec\s*\(',
            'insecure_random': r'random\.\w+\(\)',
        }
        
        for pattern_name, pattern in security_patterns.items():
            matches = re.findall(pattern, content)
            if matches:
                warnings.warn(
                    f"Security warning in {file_path}: "
                    f"Found {len(matches)} {pattern_name} patterns"
                )
    
    # Méthodes utilitaires
    @staticmethod
    def _to_snake_case(name: str) -> str:
        """Convertit CamelCase en snake_case"""
        name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        name = re.sub('([a-z0-9])([A-Z])', r'\1_\2', name)
        return name.lower()
    
    @staticmethod
    def _to_camel_case(name: str) -> str:
        """Convertit snake_case en CamelCase"""
        return ''.join(word.capitalize() for word in name.split('_'))
    
    @staticmethod
    def _python_type_hint(dsl_type: str) -> str:
        """Retourne l'annotation de type Python pour un type DSL"""
        compiler = DSLCompiler()
        return compiler._dsl_type_to_python(dsl_type)
    
    @staticmethod
    def _format_default_value(value: Any) -> str:
        """Formate une valeur par défaut pour Python"""
        if value is None:
            return "None"
        elif isinstance(value, str):
            return f'"{value}"'
        elif isinstance(value, bool):
            return str(value)
        elif isinstance(value, (int, float)):
            return str(value)
        else:
            return repr(value)


# Fonction utilitaire pour compilation rapide
def compile_dsl(
    dsl_source: Union[str, Path],
    output_dir: Optional[Path] = None,
    targets: List[str] = None
) -> CompilationResult:
    """
    Fonction utilitaire pour compiler du DSL
    
    Args:
        dsl_source: Source DSL ou chemin de fichier
        output_dir: Répertoire de sortie
        targets: Liste des cibles (python, openapi, config, etc.)
        
    Returns:
        CompilationResult
    """
    # Convertit les cibles strings en enum
    target_enums = []
    if targets:
        for target in targets:
            try:
                target_enums.append(CompilationTarget(target.lower()))
            except ValueError:
                valid_targets = [t.value for t in CompilationTarget]
                raise ValueError(
                    f"Invalid target '{target}'. "
                    f"Valid targets are: {valid_targets}"
                )
    else:
        target_enums = [CompilationTarget.PYTHON_CLASS]
    
    # Crée et exécute le compilateur
    compiler = DSLCompiler(
        output_dir=output_dir,
        optimization_level=OptimizationLevel.BASIC,
        enable_security_scan=True
    )
    
    return compiler.compile(dsl_source, target_enums, output_dir)


# Exemple d'utilisation
if __name__ == "__main__":
    # Exemple de DSL
    example_dsl = """
agent CostAnomalyDetector:
  type: detector
  capabilities: [cost_analysis, anomaly_detection]
  inputs:
    - monthly_cost: float
    - cost_baseline: float
    - threshold: float = 1.2
  rules:
    - if monthly_cost > cost_baseline * threshold then alert(severity: high)
  outputs:
    - anomaly_score: float
    - confidence: float
  business_value:
    calculator: CostAvoidanceCalculator
    parameters:
      hourly_engineer_cost: 150
      average_resolution_time: 4
"""
    
    # Compilation
    print("🔧 Compilation du DSL...")
    result = compile_dsl(
        example_dsl,
        output_dir=Path("/tmp/generated_agents"),
        targets=["python", "openapi", "documentation"]
    )
    
    if result.success:
        print("✅ Compilation réussie!")
        print(f"📁 Fichiers générés: {len(result.generated_files)}")
        print(f"⏱️  Temps de compilation: {result.compilation_time:.2f}s")
        
        for file_path in result.generated_files[:5]:  # Affiche les 5 premiers
            print(f"  - {file_path}")
        
        if result.warnings:
            print("\n⚠️  Avertissements:")
            for warning in result.warnings:
                print(f"  - {warning}")
        
        # Affiche quelques métriques
        print("\n📊 Métriques:")
        for key, value in result.metrics.items():
            print(f"  {key}: {value}")
    else:
        print("❌ Échec de la compilation:")
        for error in result.errors:
            print(f"  - {error}")